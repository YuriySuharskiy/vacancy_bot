# перевірка актуальності вакансії перед її обробкою, якщо вакансія не актуальна
# видаляти її з бд і брати наступну. додати логування для перевірки надсилання опису в гпт і в тг

from dotenv import load_dotenv
load_dotenv()  # читає .env з поточної робочої директорії

import os
import logging
import time
import requests
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from parser_work_ua import (
    fetch_and_store, delete_old_jobs, get_unposted_jobs, mark_jobs_posted,
    init_db, set_meta, get_meta, save_job_summary, DB_PATH, HEADERS
)
from OpenAI_agent import create_vacancy_summary, summarize_description, format_for_telegram, create_useful_tips
from desc_parser import get_vacancy_description

BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
CHAT_ID = os.getenv('TG_CHAT_ID')  # channel id like '@yourchannel' or numeric id
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Set TG_BOT_TOKEN and TG_CHAT_ID environment variables")

TELEGRAM_SEND_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
TELEGRAM_SEND_PHOTO_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto'
# локальний файл зображення для поста (vacancy.jpg у корені проєкту)
PHOTO_PATH = os.path.join(os.path.dirname(__file__), 'vacancy.jpg')
TIP_PHOTO_PATH = os.path.join(os.path.dirname(__file__), 'tips.jpg')

KYIV = ZoneInfo('Europe/Kyiv')
# Нове вікно постингу: щогодини з 10:00 до 20:00 (10:00 <= hour < 20:00)
POST_WINDOW = (10, 20)
CHECK_INTERVAL = 60  # seconds

# Тестовий режим керується змінною середовища TG_TEST_MODE (1 = тест)
TEST_MODE = os.getenv('TG_TEST_MODE', '0') == '1'
# cooldown: 30s в тесті, 1 година в продакшні
COOLDOWN = timedelta(seconds=30) if TEST_MODE else timedelta(hours=1)

def in_allowed_window(dt_kyiv: datetime) -> bool:
    if TEST_MODE:
        return True
    h = dt_kyiv.hour
    return POST_WINDOW[0] <= h < POST_WINDOW[1]

def send_to_telegram(text: str, photo_path: str | None = None) -> bool:
    """
    Надіслати повідомлення або фото з caption.
    Якщо photo_path заданий і файл існує — викликає sendPhoto, інакше sendMessage.
    """
    try:
        if photo_path and os.path.exists(photo_path):
            with open(photo_path, 'rb') as f:
                files = {'photo': f}
                data = {
                    'chat_id': CHAT_ID,
                    'caption': text,
                    'parse_mode': 'HTML',
                    'disable_web_page_preview': True
                }
                resp = requests.post(TELEGRAM_SEND_PHOTO_URL, data=data, files=files, timeout=30)
                return resp.status_code == 200 and resp.json().get('ok', False)
        else:
            payload = {
                'chat_id': CHAT_ID,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            resp = requests.post(TELEGRAM_SEND_URL, data=payload, timeout=15)
            return resp.status_code == 200 and resp.json().get('ok', False)
    except Exception as e:
        logging.getLogger(__name__).warning("send_to_telegram error: %s", e)
        return False

def parse_iso_to_dt(s: str):
    if not s:
        return None
    try:
        # підтримуємо формати з 'Z' та з часовою зоною
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception:
            return None
    # якщо наївний — трактуємо як UTC
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    # повертаємо у UTC як єдиний reference
    return dt.astimezone(timezone.utc)

def is_vacancy_active(url: str):
    """Повертає (active:bool, reason:str, status:int, snippet:str)."""
    print(f"[check-start] requesting: {url}", flush=True)
    try:
        # спроба HEAD, при потребі — GET
        try:
            resp = requests.head(url, headers=HEADERS, timeout=6, allow_redirects=True)
            status = resp.status_code
            if status == 200:
                print(f"[check-head] {url} -> status={status}", flush=True)
                return True, "ok_head", status, ""
            if status in (405, 501):
                raise Exception("HEAD fallback")
        except Exception:
            resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            status = resp.status_code
            snippet = (resp.text or "")[:600].lower()
            print(f"[check-get] {url} -> status={status}", flush=True)

        if status in (404, 410):
            return False, f"status_{status}", status, snippet if 'snippet' in locals() else ""

        inactive_phrases = (
            "вакансія неактуальна", "вакансію закрито", "вакансію видалено",
            "вакансія закрита", "this vacancy is no longer available",
            "job not found", "объявление удалено", "оголошення видалено",
            "сторінку не знайдено", "такої сторінки не існує"
        )
        if 'snippet' in locals():
            for p in inactive_phrases:
                if p in snippet:
                    return False, f"phrase:{p}", status, snippet

        if status == 200:
            return True, "ok", status, snippet if 'snippet' in locals() else ""

        return False, f"status_{status}", status, snippet if 'snippet' in locals() else ""
    except Exception as e:
        print(f"[check-error] request failed for {url}: {e}", flush=True)
        return False, f"request_error:{e}", None, ""

def delete_job(job_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
        conn.commit()
        conn.close()
        print(f"[db] deleted job id={job_id}", flush=True)
    except Exception as e:
        print("[db] delete_job error:", e, flush=True)

def main_loop():
    init_db()
    # логування: INFO для продакшну, DEBUG для локальної налагодки
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)
    logger.info("Starting main_loop, TEST_MODE = %s", TEST_MODE)

    # -- quick test send for Useful tips when TEST_MODE=1 --
    if TEST_MODE:
        TIP_META_KEY = 'last_tip_sent_test'
        sent_marker = get_meta(TIP_META_KEY)
        today_tag = f"TEST:{datetime.now(KYIV).date().isoformat()}"
        if sent_marker != today_tag:
            try:
                tips_text = create_useful_tips(num_tips=3, locale="uk")
                tips_message = f"<b>Корисні поради</b>\n\n{tips_text}\n\n#junior #tips"
                ok_tip = send_to_telegram(tips_message, photo_path=TIP_PHOTO_PATH)
                logger.info("Test tip send result: %s", ok_tip)
                if ok_tip:
                    set_meta(TIP_META_KEY, today_tag)
            except Exception as e:
                logger.warning("Failed to generate/send test tips: %s", e)
    # -- end test send --

    while True:
        try:
            new = fetch_and_store()
            logger.info("fetch_and_store -> new inserted: %d", len(new))
            delete_old_jobs(days=30)

            unposted = get_unposted_jobs()
            logger.info("unposted count: %d", len(unposted))
            if not unposted:
                time.sleep(CHECK_INTERVAL)
                continue

            # --- нова логіка: перевіряємо послідовно лише перші записи і видаляємо недоступні ---
            last_post_iso = get_meta('last_post_time')
            logger.debug("last_post_time (meta): %s", last_post_iso)
            last_post_dt = parse_iso_to_dt(last_post_iso) if last_post_iso else None
            now_utc = datetime.now(timezone.utc)

            # перевіряємо чи зараз дозволений інтервал за Києвом
            now_kyiv = datetime.now(KYIV)
            if not in_allowed_window(now_kyiv):
                logger.info("Outside posting window, skipping post")
                time.sleep(CHECK_INTERVAL)
                continue

            # --- Scheduled tips: 10:30 and 18:30 Kyiv ---
            TIP_SCHEDULE = [(10, 30), (18, 30)]
            TIP_META_KEY = 'last_tip_sent'  # зберігаємо маркер останнього відправленого слоту
            if (now_kyiv.hour, now_kyiv.minute) in TIP_SCHEDULE:
                slot_tag = f"{now_kyiv.date().isoformat()}T{now_kyiv.hour:02d}:{now_kyiv.minute:02d}"
                last_tip = get_meta(TIP_META_KEY)
                if last_tip != slot_tag:
                    logger.info("Tip slot matched %s, generating tips...", slot_tag)
                    try:
                        tips_text = create_useful_tips(num_tips=3, locale="uk")
                        tips_message = f"<b>Корисні поради</b>\n\n{tips_text}\n\n#junior #tips"
                        ok_tip = send_to_telegram(tips_message, photo_path=TIP_PHOTO_PATH)
                        logger.info("Tip send result: %s", ok_tip)
                        if ok_tip:
                            set_meta(TIP_META_KEY, slot_tag)
                    except Exception as e:
                        logger.warning("Failed to generate/send tips: %s", e)
                        # не блокуємо подальшу логіку вакансій

            # end scheduled tips

            # перевірка кулдауну (TEST_MODE впливає на COOLDOWN)
            if last_post_dt is not None and (now_utc - last_post_dt) < COOLDOWN:
                logger.info("Cooldown not passed, skipping post")
                time.sleep(CHECK_INTERVAL)
                continue

            # Ідемо FIFO по unposted: перевіряємо першу; якщо недоступна — видаляємо і йдемо далі
            candidate = None
            for job in unposted:
                jid = job.get('id')
                link = job.get('link')
                logger.debug("[check-candidate] id=%s title=%r link=%s", jid, job.get('title'), link)
                if not link:
                    logger.info("[delete] job id=%s missing link", jid)
                    delete_job(jid)
                    continue

                active, reason, status, snippet = is_vacancy_active(link)
                logger.debug("[result] job id=%s active=%s reason=%s status=%s", jid, active, reason, status)
                if not active:
                    logger.info("[delete] job id=%s deleted (reason=%s)", jid, reason)
                    delete_job(jid)
                    continue

                # знайшли доступну вакансію — беремо її як кандидата та ламаємо цикл
                candidate = job
                break

            if not candidate:
                logger.debug("No available candidate found (all checked entries were deleted or unavailable)")
                time.sleep(CHECK_INTERVAL)
                continue

            logger.info("Selected candidate id=%s title=%r", candidate['id'], candidate['title'])

            summary = candidate.get('summary') or ''
            if not summary:
                try:
                    desc = get_vacancy_description(candidate['link'])
                    if desc:
                        summary = summarize_description(desc)
                    else:
                        summary = "Опис вакансії недоступний."
                except Exception:
                    summary = "Короткий опис недоступний через помилку сервісу."
                try:
                    save_job_summary(candidate['id'], summary)
                except Exception:
                    pass

            # ensure we include company in the telegram message (company may be empty)
            message = format_for_telegram(
                candidate['title'],
                candidate.get('company', ''),
                candidate.get('salary', ''),
                candidate.get('link', ''),
                summary
            )
            logger.info("Preparing to send message to Telegram (summary length=%d)", len(summary or ""))
            ok = False
            try:
                ok = send_to_telegram(message, photo_path=PHOTO_PATH)
                logger.info("send_to_telegram returned: %s", ok)
            except Exception as e:
                logger.warning("send_to_telegram raised: %s", e)
                ok = False

            if ok:
                mark_jobs_posted([candidate['id']])
                set_meta('last_post_time', datetime.now(timezone.utc).isoformat())
                logger.info("Marked posted and updated last_post_time")

        except Exception as e:
            logger.exception("main loop exception:")
            time.sleep(10)
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main_loop()
