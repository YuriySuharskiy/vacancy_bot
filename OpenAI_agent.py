import os
import time
from openai import OpenAI
from typing import Optional

API_KEY = os.getenv("OPENAI_API_KEY")
if not API_KEY:
    raise SystemExit("OPENAI_API_KEY не встановлений")

client = OpenAI(api_key=API_KEY)

MODEL = "gpt-5-mini"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # множник між спробами
DEFAULT_TIMEOUT = 30

def _call_openai(prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str:
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
        except Exception as exc:
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
— зарплату в повідомленні не вказуй, бо вона вже є в заголовку вакансії.
— бажано не більше ~1000 символів, але не обрізай слово на середині.

ВАЖЛИВО: заверши відповідь повним реченням.
Текст опису:
{text}
"""
    return _call_openai(prompt, timeout=DEFAULT_TIMEOUT)

def create_useful_tips(num_tips: int = 3, locale: str = "uk") -> str:
    lang_note = "українською" if locale.startswith("uk") else "англійською"
    prompt = f"""
Згенеруй {num_tips} коротких корисних порад (Useful tips) для junior розробників.
Кожна порада — 1-2 короткі речення. Використай маркований список (— або •).
Мова: {lang_note}. Поверни лише список порад, без вступу.
"""
    return _call_openai(prompt, timeout=DEFAULT_TIMEOUT)

def format_for_telegram(title: str, company: str = "", salary: str = "", url: str = "", summary: str = "") -> str:
    comp_line = f"🏢 {company}\n" if company else ""
    salary_line = f"💰 {salary}\n\n" if salary else "💰 Зарплата не вказана\n\n"
    message = (
        f"🧑‍💻 <b>{title}</b>\n"
        f"{comp_line}"
        f"{salary_line}"
        f"{summary}\n\n"
        f"🔗 <a href='{url}'>Детальніше</a>"
    )
    return message

def create_vacancy_summary(title: str, company: str = "", salary: str = "", url: str = "") -> str:
    """
    Згенерувати підсумок вакансії. Якщо вдасться — витягнути повний опис з url через desc_parser,
    інакше скористатись заголовком/полями.
    """
    try:
        from desc_parser import get_vacancy_description
    except Exception:
        get_vacancy_description = None

    text = ""
    if get_vacancy_description and url:
        try:
            text = get_vacancy_description(url) or ""
        except Exception:
            text = ""

    if text:
        try:
            summary = summarize_description(text)
        except Exception:
            summary = "Короткий опис недоступний через помилку сервісу."
    else:
        # fallback — мінімальний опис з полів
        parts = [title]
        if company:
            parts.append(f"Компанія: {company}")
        if salary:
            parts.append(f"Зарплата: {salary}")
        summary = " · ".join(parts)

    return format_for_telegram(title, company, salary, url, summary)
