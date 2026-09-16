import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


# ============================================================
# PRODUCTS TO MONITOR
# ============================================================

URLS = [
    # Smarty
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Elite-Trainer-Box-4p278101",
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-2-Pack-Blister-4p278095",
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Greninja-ex-Box-4p278100",

    # Hrananetu
    "https://www.hrananetu.cz/p/pokemon-30th-celebration-elite-trainer-box",
    "https://www.hrananetu.cz/p/pokemon-celebration-ditto-premium-collection",

    # CDMC
    "https://www.cdmc.cz/elite-trainer-boxy/pokemon-tcg--30th-celebration-elite-trainer-box/",
    "https://www.cdmc.cz/blistery/pokemon-tcg--30th-celebration-2-pack-blister-eevee/",

    # Černý rytíř
    "https://cernyrytir.cz/merch/detail/47517394-b620-4713-8389-ce5779d94441",

    # Alza
    "https://www.alza.cz/hracky/pokemon-tcg-30th-celebration-elite-trainer-box-d13521013.htm",
    "https://www.alza.cz/hracky/pokemon-tcg-30th-celebration-2-pack-blister-d13521015.htm",

    # Rohlík
    "https://www.rohlik.cz/1483651-pokemon-tcg-30th-celebration-elite-trainer-box",
    "https://www.rohlik.cz/1483649-pokemon-tcg-30th-celebration-sylveon-ex-box",

    # Planeta her
    # Add the verified URL here once confirmed.
]


# ============================================================
# STATE
# ============================================================

STATE_FILE = Path("state.json")


# ============================================================
# STOCK WORDS
# ============================================================

AVAILABLE_PATTERNS = [
    r"\bDo\s+košíku\b",
    r"\bVložit\s+do\s+košíku\b",
    r"\bPřidat\s+do\s+košíku\b",
    r"\bDo\s+košíka\b",
    r"\bVložiť\s+do\s+košíka\b",
    r"\bAdd\s+to\s+cart\b",
    r"\bInStock\b",
    r"\bSkladem\b",
    r"\bSkladom\b",
    r"\bskladě\b",
    r"\bskladom\b",
]

NOT_AVAILABLE_PATTERNS = [
    r"\bNení\s+skladem\b",
    r"\bNeni\s+skladem\b",
    r"\bNie\s+je\s+skladom\b",
    r"\bVyprodáno\b",
    r"\bVyprodany\b",
    r"\bPoložka byla vyprodána\b",
    r"\bThe item has been sold out\b",
    r"\bOutOfStock\b",
    r"\bOut of stock\b",
    r"\bSold out\b",
    r"\bPřipravujeme\b",
    r"\bna dotaz\b",
    r"\bHlídat produkt\b",
    r"\bNení dostupné\b",
    r"\bnení dostupné\b",
    r"\bNelze zakoupit\b",
    r"\bnelze zakoupit\b",
]


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    response.raise_for_status()


# ============================================================
# STATE
# ============================================================

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            return {}

    return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# HELPERS
# ============================================================

def normalize(text):
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def product_name(url):
    host = urlparse(url).netloc.lower()

    if "smarty.cz" in host:
        return "Smarty"

    if "hrananetu.cz" in host:
        return "Hrananetu"

    if "cdmc.cz" in host:
        return "CDMC"

    if "cernyrytir.cz" in host:
        return "Černý rytíř"

    if "alza.cz" in host:
        return "Alza"

    if "rohlik.cz" in host:
        return "Rohlík"

    if "planetaher.cz" in host:
        return "Planeta her"

    return host


# ============================================================
# STOCK CHECK
# ============================================================

def is_available(url, html):

    host = urlparse(url).netloc.lower()

    # --------------------------------------------------------
    # First: explicit OUT OF STOCK
    # --------------------------------------------------------

    for pattern in NOT_AVAILABLE_PATTERNS:
        if re.search(pattern, html, re.IGNORECASE):
            return False

    # --------------------------------------------------------
    # Then: explicit IN STOCK
    # --------------------------------------------------------

    for pattern in AVAILABLE_PATTERNS:
        if re.search(pattern, html, re.IGNORECASE):
            return True

    # --------------------------------------------------------
    # Hrananetu
    # --------------------------------------------------------

    if "hrananetu.cz" in host:
        if re.search(
            r"\b\d+\+?\s*ks\s+na\s+skladě\b",
            html,
            re.IGNORECASE,
        ):
            return True

    return False


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(page, url):

    print(f"Checking: {url}")

    try:
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

    except PWTimeoutError:
        print("  Page timeout - using loaded page")

    except Exception as e:
        print(f"  ERROR loading page: {e}")
        return None

    # Give JavaScript a moment to render stock information.
    page.wait_for_timeout(2000)

    return page.content()


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    newly_available = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            locale="cs-CZ",
            timezone_id="Europe/Prague",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 "
                "Safari/537.36"
            ),
            viewport={
                "width": 1440,
                "height": 1000,
            },
        )

        page = context.new_page()

        for url in URLS:

            previous = state.get(url, "unknown")

            try:

                html = fetch_page(
                    page,
                    url,
                )

                if html is None:
                    print("  Could not check")
                    continue

                now = (
                    "available"
                    if is_available(url, html)
                    else "not_available"
                )

                print(
                    f"  {product_name(url)} => {now}"
                    f"  (previous: {previous})"
                )

                # ------------------------------------------------
                # ONLY ALERT ON:
                #
                # not available -> available
                #
                # ------------------------------------------------

                if (
                    previous != "available"
                    and now == "available"
                ):

                    newly_available.append(
                        (
                            f"🚨 POKÉMON BACK IN STOCK 🚨\n\n"
                            f"🏪 {product_name(url)}\n"
                            f"🔗 {url}"
                        )
                    )

                state[url] = now

            except Exception as e:

                print(
                    f"  ERROR checking {url}: {e}"
                )

                # Keep previous state if checking failed.
                continue

            # Small delay between websites.
            time.sleep(1)

        context.close()
        browser.close()

    # ========================================================
    # SEND TELEGRAM ONLY IF SOMETHING BECAME AVAILABLE
    # ========================================================

    if newly_available:

        message = (
            "🚨 POKÉMON RESTOCK ALERT 🚨\n\n"
            + "\n\n".join(newly_available)
        )

        print("\nSENDING TELEGRAM ALERT...")
        print(message)

        telegram_send(message)

    else:

        print("\nNo new stock. No notification sent.")

    save_state(state)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
