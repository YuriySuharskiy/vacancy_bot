import requests
from bs4 import BeautifulSoup

def get_vacancy_description(url: str) -> str:
    response = requests.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Залежить від структури сторінки Work.ua
    # Може знадобитись адаптація — я даю приклад:
    description_block = soup.find("div", {"id": "job-description"})
    if not description_block:
        # fallback — Work.ua часто використовує інший div
        description_block = soup.find("div", class_="card wordwrap")
    
    return description_block.get_text(separator="\n", strip=True) if description_block else ""
