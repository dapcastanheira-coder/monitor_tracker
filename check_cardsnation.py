import json
import os
import re
import time
from pathlib import Path
from typing import Dict

import requests

URLS = [
    "https://www.cardsnation.cz/pokemon-tcg--me-2-5-ascended-heroes-elite-trainer-box/",
    # Add 2–4 more here:
    "https://www.pokemall.cz/ascended-heroes/pokemon-tcg-ascended-heroes-elite-trainer-box/",
    "https://www.vesely-drak.cz/produkty/pokemon-elite-trainer-box/17298-pokemon-ascended-heroes-elite-trainer-box-dragonite/",
    "https://www.pokeriders.cz/en/elite-trainer-box--etb/ascended-heroes-elite-trainer-box/?srsltid=AfmBOorfz8MQ4EvuL3ccW-mVUBPNlIqgv25yM8AkhB_NynlEHpIF0I5t",
    "https://www.xzone.cz/karetni-hra-pokemon-tcg-mega-evolution-ascended-heroes-booster-bundle-6-boosteru",
    "https://www.kuma.cz/pokemon-tcg-mega-evolution-ascended-heroes-elite-trainer-box-dragonite/?_fid=mulc",
]

STATE_FILE = Path("state.json")

NOT_AVAILABLE_PATTERNS = [
    r"Položka byla vyprodána",
    r"Dostupnost:\s*Objednáno",
    r"\bHlídat\b",
]

AVAILABLE_PATTERNS = [
    r"Do košíku",
    r"Vložit do košíku",
    r"Přidat do košíku",
    r"Koupit",
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
        data={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
        timeout=20,
    ).raise_for_status()

def load_state() -> Dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: Dict[str, str]) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "AvailabilityMonitor/1.0",
        "Accept-Language": "cs,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text

def main() -> None:
    state = load_state()  # url -> "available"/"not_available"
    changed_to_available = []

    for i, url in enumerate(URLS):
        try:
            html = fetch_html(url)
            now = "available" if is_available(html) else "not_available"
        except Exception as e:
            # Don't flip state on temporary failures
            print(f"ERROR fetching {url}: {e}")
            now = state.get(url, "not_available")

        prev = state.get(url, "unknown")
        state[url] = now

        print(f"{url} => {now} (prev: {prev})")

        if prev != "available" and now == "available":
            changed_to_available.append(url)

        # polite delay between checks (helps avoid being rate-limited)
        if i < len(URLS) - 1:
            time.sleep(3)

    # Notify once per run, listing all URLs that just became available
    if changed_to_available:
        lines = "\n".join(changed_to_available)
        telegram_send(f"✅ AVAILABLE now:\n{lines}")

    save_state(state)

if __name__ == "__main__":
    main()
