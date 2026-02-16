import os
import re
import requests

# 👇 ADD ALL PRODUCT URLS HERE
URLS = [
    "https://www.cardsnation.cz/pokemon-tcg--me-2-5-ascended-heroes-elite-trainer-box/",
    # Add more below:
    # "https://www.cardsnation.cz/another-product/",
]

NOT_AVAILABLE_PATTERNS = [
    r"Položka byla vyprodána",
    r"Dostupnost:\s*Objednáno",
    r"\bHlídat\b",
]

AVAILABLE_PATTERNS = [
    r"Do košíku",
    r"Vložit do košíku",
    r"Přidat do košíku",
    r"Koupit"
]

def is_available(html: str) -> bool:
    has_available = any(re.search(p, html, re.IGNORECASE) for p in AVAILABLE_PATTERNS)
    has_not_available = any(re.search(p, html, re.IGNORECASE) for p in NOT_AVAILABLE_PATTERNS)
    return has_available and not has_not_available

def telegram_send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout=20,
    ).raise_for_status()

def check_product(url: str):
    headers = {
        "User-Agent": "AvailabilityMonitor/1.0",
        "Accept-Language": "cs,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    if is_available(resp.text):
        telegram_send(f"✅ AVAILABLE:\n{url}")
        print(f"AVAILABLE: {url}")
    else:
        print(f"NOT AVAILABLE: {url}")

def main():
    for url in URLS:
        check_product(url)

if __name__ == "__main__":
    main()
