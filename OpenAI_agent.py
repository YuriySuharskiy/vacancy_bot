import os
import time
from openai import OpenAI
from desc_parser import get_vacancy_description

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise SystemExit("OPENAI_API_KEY не встановлений")

client = OpenAI(api_key=API_KEY)

MODEL = "gpt-5-mini"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # множник між спробами

# Ліміти (налаштуйте при потребі)
MAX_INPUT_CHARS = 2000         # скоротили, щоб залишалось місця для відповіді
MAX_OUTPUT_TOKENS = 1000        # більше токенів для повної відповіді


def _call_openai(prompt: str, timeout: int = 15, max_output_tokens: int = MAX_OUTPUT_TOKENS) -> str:
    """Виклик OpenAI з ретраєм і обмеженням вихідних токенів."""
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.responses.create(
                model=MODEL,
                input=prompt,
                max_output_tokens=max_output_tokens,
                timeout=timeout
            )
            # Сумісна витяжка тексту з різних версій відповіді
            if hasattr(resp, "output_text") and resp.output_text:
                return resp.output_text.strip()
            # інша структура відповіді
            out = ""
            for item in getattr(resp, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        out += c.get("text", "")
                    elif isinstance(c, str):
                        out += c
            return out.strip()
        except Exception as exc:
            # ловимо будь-які помилки клієнта/мережі; виконуємо ретрай
            if attempt == MAX_RETRIES:
                raise
            time.sleep(delay)
            delay *= RETRY_BACKOFF

def summarize_description(text: str) -> str:
    if not text:
        return ""
    prompt = f"""
Стисни опис вакансії до короткого повідомлення для Telegram-каналу.
Формат:
— короткий вступ з назвою посади;
— ключові вимоги;
— що пропонують;
— не більше 700 символів та не менше 400 символів;
- не прописуй зарплату в описі.

ВАЖЛИВО: заверши відповідь повним реченням, не обривай слово на середині.

Текст опису:
{text}
"""
    # трохи більший timeout на запит
    return _call_openai(prompt, max_output_tokens=MAX_OUTPUT_TOKENS, timeout=30)

def format_for_telegram(title: str, company: str = "", salary: str = "", url: str = "", summary: str = "") -> str:
    """
    Версія повідомлення з підтримкою компанії.
    company опціонально, щоб зберегти сумісність.
    """
    comp_line = f"🏢 {company}\n" if company else ""
    message = (
        f"🧑‍💻 <b>{title}</b>\n"
        f"{comp_line}"
        f"💰 {salary if salary else 'Зарплата не вказана'}\n\n"
        f"{summary}\n\n"
        f"🔗 <a href='{url}'>Детальніше</a>"
    )
    return message

def create_vacancy_summary(title: str, company: str = "", salary: str = "", url: str = "") -> str:
    """
    Генерує summary та повертає оформлене повідомлення. company опціонально.
    """
    text = get_vacancy_description(url)
    if not text:
        return format_for_telegram(title, company, salary, url, "Опис вакансії недоступний.")
    try:
        summary = summarize_description(text)
    except Exception:
        summary = "Короткий опис недоступний через помилку сервісу."
    return format_for_telegram(title, company, salary, url, summary)
