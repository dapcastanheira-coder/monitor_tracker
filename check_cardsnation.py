import os
import re
import requests

URL = "https://www.cardsnation.cz/pokemon-tcg--me-2-5-ascended-heroes-elite-trainer-box/"

NOT_AVAILABLE_PATTERNS = [
    r"Položka byla vyprodána",
    r"Dostupnost:\s*Objednáno",
    r"\bHlídat\b",
]
AVAILABLE_PATTERNS = [
    r"Do košíku", r"Vložit do košíku", r"Přidat do košíku", r"Koupit"
]

def is_available(html: str) -> bool:
    has_available = any(re.search(p, html, re.IGNORECASE) for p in AVAILABLE_PATTERNS)
    has_not_available = any(re.search(p, html, re.IGNORECASE) for p in NOT_AVAILABLE_PATTERNS)
    return has_available and not has_not_available

def telegram_send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
    r.raise_for_status()

def main() -> None:
    headers = {
        "User-Agent": "AvailabilityMonitor/1.0",
        "Accept-Language": "cs,en;q=0.8",
    }
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()

    if is_available(resp.text):
        telegram_send(f"✅ AVAILABLE to order now: {URL}")
        print("AVAILABLE")
    else:
        print("NOT AVAILABLE")

if __name__ == "__main__":
    main()
