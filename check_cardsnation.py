import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

URLS = [
    "https://www.alza.cz/search.htm?exps=Elite+Trainer+box",
    "https://www.cdmc.cz/me05-pitch-black/",
    "https://www.smarty.cz/Pokemon-TCG-SV10-Destined-Rivals-Booster-4p225223",
    "https://www.smarty.cz/elite-trainer-box-4c14603",
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Elite-Trainer-Box-4p278101",
    "https://www.hrananetu.cz/pokemon-trainer-boxy",
    "https://www.vesely-drak.cz/produkty/pokemon-elite-trainer-box/19132-pokemon-30th-celebration-elite-trainer-box/",
    "https://www.ihrysko.sk/pokemon-30th-celebration-elite-trainer-box-p122315",
    "https://www.alola.cz/elite-trainer-boxy/",
    "https://www.kitstore.cz/elite-trainer-box",
    "https://www.cdmc.cz/premiove-kolekce/pokemon-tcg--first-partner-illustration-collection-series-3/",
    "https://www.cdmc.cz/sv10-destined-rivals/",
    "https://www.cdmc.cz/sv8-5-prismatic-evolutions/",
    "https://www.cdmc.cz/elite-trainer-boxy/",
    "https://www.smarty.sk/pokemon-tcg-30th-celebration-elite-trainer-box-4p278101",
    "https://www.xzone.cz/pokemon-tcg-elite-trainer-boxy?sort=date_desc&s=60&page=1&term=&c=946",
    "https://www.smarty.sk/Vyhladavanie?query=Pok%C3%A9mon%20TCG%3A%2030th%20Celebration",
]

STATE_FILE = Path("state.json")

NOT_AVAILABLE_PATTERNS = [
    r"Položka byla vyprodána",
    r"The item has been sold out",
    r"Dostupnost:\s*na dotaz",
    r"Na eshopu nemáme dostupné",
    r"Hlídat produkt",
    r"\bNení\s+skladem\b",
    r"\bPřipravujeme\b",
    r"\bVyprodáno\b",
    r"\bOutOfStock\b",
    r"Produkt aktuálně nelze zakoupit",
    r"\bnelze\s+zakoupit\b",
    r"\bOčakávame\b",
    r"sledovať\s+dostupnosť",
    
]

AVAILABLE_PATTERNS = [
    r"\bDo\s+košíku\b",
    r"\bVložit\s+do\s+košíku\b",
    r"\bPřidat\s+do\s+košíku\b",
    r"\bInStock\b",
    r"\bAdd\s+to\s+cart\b",
    r"(?<!Není\s)\bSkladem\b",
    r"\bVložiť\s+do\s+košíka\b",
    r"(?<!nie je\s)\bskladom\b",
]

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

def is_available(url: str, html: str) -> bool:
    host = urlparse(url).netloc.lower()

    # Xzone
    if "xzone.cz" in host:
        if re.search(r"\bOutOfStock\b", html, re.IGNORECASE):
            return False
        if re.search(r"\bInStock\b", html, re.IGNORECASE):
            return True
        return False

    # HranaNetu
    if "hrananetu.cz" in host:
        return bool(
            re.search(
                r"\b\d+\+?\s*ks\s+na\s+skladě\b",
                html,
                re.IGNORECASE,
            )
        )

    # Smarty.sk search page
    if "smarty.sk" in host and "vyhladavanie" in url.lower():
        match = re.search(
            r"\bSkladom\s+celkom\s+\((\d+)\)",
            html,
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1)) > 0

        return False

    # Vesely Drak
    if "vesely-drak.cz" in host:
        if any(
            re.search(p, html, re.IGNORECASE)
            for p in [
                r"Na eshopu nemáme dostupné",
                r"Dočasně nedostupné",
                r"prodej tohoto produktu již skončil",
                r"Položka byla vyprodána",
            ]
        ):
            return False

        return any(
            re.search(p, html, re.IGNORECASE)
            for p in [
                r"\bDo\s+košíku\b",
                r"\bVložit\s+do\s+košíku\b",
                r"\bPřidat\s+do\s+košíku\b",
            ]
        )

    # All other sites
    if any(re.search(p, html, re.IGNORECASE) for p in AVAILABLE_PATTERNS):
        return True

    if any(re.search(p, html, re.IGNORECASE) for p in NOT_AVAILABLE_PATTERNS):
        return False

    return False

def fetch_rendered_html(url: str, timeout_ms: int = 25000) -> str:
    """
    Loads the page in a real headless browser (JS included) and returns the final HTML snapshot.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="cs-CZ",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari"
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        except PWTimeoutError:
            # Some sites never go fully idle; still grab what we have
            pass

        # Small extra wait for late JS UI updates
        page.wait_for_timeout(1500)
        html = page.content()

        context.close()
        browser.close()
        return html

def maybe_send_heartbeat(state: Dict[str, str]) -> None:
    now_ts = int(time.time())
    last = int(state.get("_last_heartbeat", 0))
    #SIX_HOURS = 6 * 60 * 60
    SIX_HOURS = 30 * 60

    if now_ts - last >= SIX_HOURS:
        utc_time = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        available_count = sum(1 for u in URLS if state.get(u) == "available")
        telegram_send(
            f"💓 Heartbeat\n"
            f"🕒 UTC: {utc_time}\n"
            f"🔗 Monitoring: {len(URLS)} products\n"
            f"📦 Available now: {available_count}"
        )
        state["_last_heartbeat"] = now_ts

def main() -> None:
    state = load_state()
    maybe_send_heartbeat(state)

    changed_to_available = []

    for i, url in enumerate(URLS):
        prev = state.get(url, "unknown")

        try:
            html = fetch_rendered_html(url)
            now = "available" if is_available(url, html) else "not_available"
        except Exception as e:
            print(f"ERROR fetching {url}: {e}")
            now = state.get(url, "not_available")

        state[url] = now
        print(f"{url} => {now} (prev: {prev})")

        if prev != "available" and now == "available":
            changed_to_available.append(url)

        if i < len(URLS) - 1:
            time.sleep(2)

    if changed_to_available:
        telegram_send("✅ AVAILABLE now:\n" + "\n".join(changed_to_available))

    save_state(state)

if __name__ == "__main__":
    main()
