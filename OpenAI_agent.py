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

def _call_openai(prompt: str, timeout: int = DEFAULT_TIMEOUT, temperature: float = 0.7, top_p: float = 0.9, max_output_tokens: int = 300) -> str:
    delay = 1.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.responses.create(
                model=MODEL,
                input=prompt,
                temperature=temperature,
                top_p=top_p,
                max_output_tokens=max_output_tokens,
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
    """
    Згенерувати короткі корисні tips для junior.
    Інструкції: кожна порада має бути унікальною, охоплювати різну тему
    (наприклад: інструменти, soft-skill, інтерв'ю, навчальні ресурси, практика).
    Не повторювати однакові фрази/словосполучення. Повернути маркований список.
    """
    lang_note = "українською" if locale.startswith("uk") else "англійською"
    prompt = f"""
Твоя задача — згенерувати {num_tips} коротких корисних порад для junior розробників.
УМОВИ:
- Мова: {lang_note}.
- Кожна порада — 1–2 короткі речення (макс. 140 символів).
- Кожна порада має торкатися ОКРЕМОЇ теми: (1) інструменти/workflow, (2) навчальні ресурси/практика, (3) soft skills/співпраця, (4) підготовка до інтерв'ю, (5) продуктивність/звички — підбирай по темах залежно від кількості порад.
- Уникай повторів: не вживай однакові слова/фрази на початку кожної поради.
- Потрібен маркований список (— або •), без зайвих заголовків або пояснень.
Почни відповідь лише зі списку порад (без вступних фраз).
Приклад формату:
— ...
— ...
"""
    # трохи вища temperature + широкий top_p для більшої варіативності
    raw = _call_openai(prompt, timeout=DEFAULT_TIMEOUT, temperature=0.85, top_p=0.95, max_output_tokens=256)

    # просте постоброблення: видалити надлишкові порожні рядки, гарантувати потрібну кількість елементів
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    # зберігаємо тільки марковані рядки або перші num_tips рядків як fallback
    tips = [l for l in lines if l.startswith(("—", "-", "•"))]
    if len(tips) < num_tips:
        # fallback: взяти перші непорожні рядки і маркувати їх
        nonblank = [l for l in lines if not l.startswith("Почни")][:num_tips]
        tips = [f"— {l.lstrip('—-• ')}" for l in nonblank][:num_tips]
    # якщо модель все ще повторює — спроба генерації ще раз з іншою temperature (простий retry)
    if len(set(tips)) < len(tips):
        alt = _call_openai(prompt, timeout=DEFAULT_TIMEOUT, temperature=0.95, top_p=1.0, max_output_tokens=256)
        alt_lines = [l.strip() for l in alt.splitlines() if l.strip()]
        alt_tips = [l for l in alt_lines if l.startswith(("—", "-", "•"))]
        for t in alt_tips:
            if t not in tips and len(tips) < num_tips:
                tips.append(t)
    # trim to requested count
    tips = tips[:num_tips]
    return "\n".join(tips)

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
