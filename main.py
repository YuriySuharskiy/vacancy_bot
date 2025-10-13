import os
from dotenv import load_dotenv
load_dotenv()  # читає .env з поточної робочої директорії
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from parser_work_ua import fetch_and_store, delete_old_jobs, get_unposted_jobs, mark_jobs_posted, init_db, set_meta, get_meta, save_job_summary
from OpenAI_agent import create_vacancy_summary, summarize_description, format_for_telegram
from desc_parser import get_vacancy_description

BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
CHAT_ID = os.getenv('TG_CHAT_ID')  # channel id like '@yourchannel' or numeric id
if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("Set TG_BOT_TOKEN and TG_CHAT_ID environment variables")

TELEGRAM_SEND_URL = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'

KYIV = ZoneInfo('Europe/Kyiv')
# production windows (08:00-10:59 and 18:00-20:59)
POST_WINDOW_1 = (8, 11)   # 8:00 <= hour < 11:00
POST_WINDOW_2 = (18, 21)  # 18:00 <= hour < 21:00
CHECK_INTERVAL = 60  # seconds

# If you want to test immediately, set TG_TEST_MODE=1 in env — then in_allowed_window() returns True
TEST_MODE = os.getenv('TG_TEST_MODE', '0') == '1'

def in_allowed_window(dt_kyiv: datetime) -> bool:
    if TEST_MODE:
        return True
    h = dt_kyiv.hour
    return (POST_WINDOW_1[0] <= h < POST_WINDOW_1[1]) or (POST_WINDOW_2[0] <= h < POST_WINDOW_2[1])

def send_to_telegram(text: str) -> bool:
    payload = {
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True
    }
    resp = requests.post(TELEGRAM_SEND_URL, data=payload, timeout=15)
    try:
        return resp.status_code == 200 and resp.json().get('ok', False)
    except Exception:
        return False

def parse_iso_to_dt(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

def main_loop():
    init_db()
    while True:
        try:
            new = fetch_and_store()
            delete_old_jobs(days=30)

            unposted = get_unposted_jobs()
            if not unposted:
                time.sleep(CHECK_INTERVAL)
                continue

            # --- постинг однієї вакансії згідно правил (тест дозволяє ігнорувати час) ---
            last_post_iso = get_meta('last_post_time')
            last_post_dt = parse_iso_to_dt(last_post_iso) if last_post_iso else None
            now_utc = datetime.utcnow()

            candidate = None
            if last_post_dt is None:
                if unposted:
                    candidate = unposted[0]
            else:
                newer = []
                for job in unposted:
                    job_dt = parse_iso_to_dt(job.get('inserted_at'))
                    if job_dt and job_dt > last_post_dt:
                        newer.append((job_dt, job))
                if newer:
                    newer.sort(key=lambda x: x[0])
                    candidate = newer[0][1]
                else:
                    if (now_utc - last_post_dt) >= timedelta(hours=1) and unposted:
                        candidate = unposted[0]

            if not candidate:
                time.sleep(CHECK_INTERVAL)
                continue

            now_kyiv = datetime.now(KYIV)
            if not in_allowed_window(now_kyiv):
                time.sleep(CHECK_INTERVAL)
                continue

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
            ok = False
            try:
                ok = send_to_telegram(message)
            except Exception:
                ok = False

            if ok:
                mark_jobs_posted([candidate['id']])
                set_meta('last_post_time', datetime.utcnow().isoformat())

        except Exception:
            time.sleep(10)
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    main_loop()
