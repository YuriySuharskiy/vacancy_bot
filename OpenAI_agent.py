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
DEFAULT_TIMEOUT = 30

def _call_openai(prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Виклик OpenAI з ретраєм. Без програмних обмежень на розмір prompt/response."""
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.responses.create(
                model=MODEL,
                input=prompt,
                timeout=timeout
            )
            if hasattr(resp, "output_text") and resp.output_text:
                return resp.output_text.strip()
            out = ""
            for item in getattr(resp, "output", []) or []:
                for c in getattr(item, "content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        out += c.get("text", "")
                    elif isinstance(c, str):
                        out += c
            return out.strip()
        except Exception:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(delay)
            delay *= RETRY_BACKOFF

def summarize_description(text: str) -> str:
    """Генерує стислий опис вакансії — без програмного скорочення вхідного тексту або обмеження вихідних токенів."""
    if not text:
        return ""
    prompt = f"""
Стисни опис вакансії до короткого повідомлення для Telegram-каналу.
Формат:
— короткий вступ з назвою посади;
— ключові вимоги;
— що пропонують;
— зарплату в повідомленні не вказуй, бо вона вже є в заголовку вакансії.
— бажано не більше ~1000 символів, але не обрізай слово на середині.

ВАЖЛИВО: заверши відповідь повним реченням.
Текст опису:
{text}
"""
    return _call_openai(prompt, timeout=DEFAULT_TIMEOUT)

def format_for_telegram(title: str, company: str = "", salary: str = "", url: str = "", summary: str = "") -> str:
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
    text = get_vacancy_description(url)
    if not text:
        return format_for_telegram(title, company, salary, url, "Опис вакансії недоступний.")
    try:
        summary = summarize_description(text)
    except Exception:
        summary = "Короткий опис недоступний через помилку сервісу."
    return format_for_telegram(title, company, salary, url, summary)
