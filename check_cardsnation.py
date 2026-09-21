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
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Greninja-ex-Box-4p278100",

    # Hrananetu
    "https://www.hrananetu.cz/p/pokemon-30th-celebration-elite-trainer-box",

    # CDMC
    "https://www.cdmc.cz/elite-trainer-boxy/pokemon-tcg--30th-celebration-elite-trainer-box/",

    # Černý rytíř
    "https://cernyrytir.cz/merch/detail/47517394-b620-4713-8389-ce5779d94441",
    "https://cernyrytir.cz/merch/detail/1c1a80b4-2f16-4e22-89bb-517d847ab016",

    # Alza
    "https://www.alza.cz/hracky/pokemon-tcg-30th-celebration-elite-trainer-box-d13521013.htm",

    # Rohlík
    "https://www.rohlik.cz/1483651-pokemon-tcg-30th-celebration-elite-trainer-box",
    "https://www.rohlik.cz/1483649-pokemon-tcg-30th-celebration-sylveon-ex-box",
]


# ============================================================
# STATE
# ============================================================

STATE_FILE = Path("state.json")


# ============================================================
# GENERIC STOCK WORDS
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
# HEARTBEAT
# ============================================================

def send_heartbeat(state):

    current_hour = time.strftime("%Y-%m-%d %H")

    # Only send one heartbeat per hour
    if state.get("_last_heartbeat_hour") == current_hour:
        return

    message = (
        "🟢 POKÉMON MONITOR HEARTBEAT\n\n"
        f"Checked products: {len(URLS)}\n"
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "Monitor is running."
    )

    try:

        telegram_send(message)

        state["_last_heartbeat_hour"] = current_hour

        print("Heartbeat sent.")

    except Exception as e:

        print(f"Heartbeat failed: {e}")


# ============================================================
# STATE
# ============================================================

def load_state():

    if STATE_FILE.exists():

        try:

            return json.loads(
                STATE_FILE.read_text(
                    encoding="utf-8"
                )
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

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


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
# CDMC DETECTOR
# ============================================================

def is_cdmc_available(page):

    print("  CDMC detector: checking purchase button...")

    # --------------------------------------------------------
    # PRIMARY SIGNAL
    #
    # Look for the ACTUAL visible purchase button.
    # --------------------------------------------------------

    try:

        buttons = page.locator(
            "button, a, input[type='submit']"
        ).filter(
            has_text=re.compile(
                r"PŘIDAT\s+DO\s+KOŠÍKU",
                re.IGNORECASE
            )
        )

        count = buttons.count()

        for i in range(count):

            element = buttons.nth(i)

            try:

                if not element.is_visible():
                    continue

                # If it is a button, make sure it is enabled.
                tag = element.evaluate(
                    "(el) => el.tagName.toLowerCase()"
                )

                if tag == "button":

                    if not element.is_enabled():
                        continue

                print(
                    "  CDMC detector: "
                    "VISIBLE + ENABLED 'PŘIDAT DO KOŠÍKU'"
                )

                return True

            except Exception:
                continue

    except Exception as e:

        print(
            f"  CDMC button check failed: {e}"
        )

    # --------------------------------------------------------
    # SECONDARY SIGNAL
    #
    # Look at VISIBLE body text only.
    #
    # This catches:
    # Skladem (1 ks)
    # Skladem (2 ks)
    # Skladem (6 ks)
    # Skladem (>15 ks)
    # --------------------------------------------------------

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

        if re.search(
            r"\bSkladem\s*\(\s*(?:\d+|>\s*\d+)\s*ks\s*\)",
            body_text,
            re.IGNORECASE,
        ):

            print(
                "  CDMC detector: "
                "VISIBLE 'Skladem (X ks)'"
            )

            return True

    except Exception as e:

        print(
            f"  CDMC body check failed: {e}"
        )

    print(
        "  CDMC detector: NOT AVAILABLE"
    )

    return False


# ============================================================
# SMARTY DETECTOR
# ============================================================

def is_smarty_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Smarty body check failed: {e}"
        )

        return False

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # "Dostupné na prodejně" means store availability.
    # It is NOT treated as online stock here.
    # --------------------------------------------------------

    if re.search(
        r"\bDostupné\s+na\s+prodejně\b",
        body_text,
        re.IGNORECASE,
    ):

        print(
            "  Smarty: 'Dostupné na prodejně' "
            "detected (NOT online stock)"
        )

    # --------------------------------------------------------
    # Explicit unavailable states
    # --------------------------------------------------------

    for pattern in NOT_AVAILABLE_PATTERNS:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Smarty: unavailable pattern: {pattern}"
            )

            return False

    # --------------------------------------------------------
    # Online stock
    # --------------------------------------------------------

    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        # If only store availability is shown,
        # don't call it online stock.
        if re.search(
            r"\bDostupné\s+na\s+prodejně\b",
            body_text,
            re.IGNORECASE,
        ) and not re.search(
            r"\bSkladem\s+(?:celkem\s+)?(?:>|≥)?\s*\d+",
            body_text,
            re.IGNORECASE,
        ):

            print(
                "  Smarty: only store stock"
            )

            return False

        print(
            "  Smarty: online stock detected"
        )

        return True

    return False


# ============================================================
# HRANANETU DETECTOR
# ============================================================

def is_hrananetu_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Hrananetu body check failed: {e}"
        )

        return False

    # Explicit unavailable
    if re.search(
        r"\bNení\s+skladem\b",
        body_text,
        re.IGNORECASE,
    ):

        return False

    # Specific stock format
    if re.search(
        r"\b\d+\+?\s*ks\s+na\s+skladě\b",
        body_text,
        re.IGNORECASE,
    ):

        return True

    # Generic stock
    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        return True

    return False


# ============================================================
# ROHLÍK DETECTOR
# ============================================================

def is_rohlik_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Rohlík body check failed: {e}"
        )

        return False

    # Strong unavailable signals
    if re.search(
        r"\bVyprodáno\b",
        body_text,
        re.IGNORECASE,
    ):

        return False

    if re.search(
        r"\bNení\s+dostupné\b",
        body_text,
        re.IGNORECASE,
    ):

        return False

    if re.search(
        r"\bNelze\s+zakoupit\b",
        body_text,
        re.IGNORECASE,
    ):

        return False

    # Rohlík purchase controls
    purchase_patterns = [
        r"Do\s+košíku",
        r"Přidat\s+do\s+košíku",
        r"Koupit",
        r"Objednat",
    ]

    for pattern in purchase_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return True

    return False


# ============================================================
# ALZA DETECTOR
# ============================================================

def is_alza_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Alza body check failed: {e}"
        )

        return False

    # Strong unavailable states
    unavailable = [
        r"Není skladem",
        r"Neni skladem",
        r"Vyprodáno",
        r"Vyprodano",
        r"Momentálně nedostupné",
        r"Momentálně vyprodáno",
        r"Nelze objednat",
    ]

    for pattern in unavailable:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return False

    # Actual purchase signals
    purchase_patterns = [
        r"Do košíku",
        r"Přidat do košíku",
        r"Koupit",
    ]

    for pattern in purchase_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return True

    # Skladem is also valid
    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        return True

    return False


# ============================================================
# ČERNÝ RYTÍŘ DETECTOR
# ============================================================

def is_cernyrytir_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Černý rytíř body check failed: {e}"
        )

        return False

    # Explicit unavailable
    unavailable_patterns = [
        r"Není skladem",
        r"Neni skladem",
        r"Vyprodáno",
        r"Vyprodano",
        r"Momentálně nedostupné",
        r"Momentálně vyprodáno",
        r"Nelze zakoupit",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return False

    # Purchase signals
    purchase_patterns = [
        r"Do košíku",
        r"Přidat do košíku",
        r"Koupit",
        r"Objednat",
    ]

    for pattern in purchase_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return True

    # Stock text
    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        return True

    return False


# ============================================================
# GENERIC DETECTOR
# ============================================================

def is_generic_available(page):

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Generic body check failed: {e}"
        )

        return False

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We now inspect VISIBLE BODY TEXT.
    # We no longer inspect page.content().
    #
    # This prevents hidden HTML from falsely saying
    # "Není skladem".
    # --------------------------------------------------------

    for pattern in NOT_AVAILABLE_PATTERNS:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return False

    for pattern in AVAILABLE_PATTERNS:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return True

    return False


# ============================================================
# MASTER STOCK CHECK
# ============================================================

def check_availability(page, url):

    host = urlparse(url).netloc.lower()

    # CDMC
    if "cdmc.cz" in host:

        return is_cdmc_available(page)

    # Smarty
    if "smarty.cz" in host:

        return is_smarty_available(page)

    # Hrananetu
    if "hrananetu.cz" in host:

        return is_hrananetu_available(page)

    # Rohlík
    if "rohlik.cz" in host:

        return is_rohlik_available(page)

    # Alza
    if "alza.cz" in host:

        return is_alza_available(page)

    # Černý rytíř
    if "cernyrytir.cz" in host:

        return is_cernyrytir_available(page)

    # Everything else
    return is_generic_available(page)


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

        print(
            "  Page timeout - using loaded page"
        )

    except Exception as e:

        print(
            f"  ERROR loading page: {e}"
        )

        return False

    # Allow JavaScript to render.
    page.wait_for_timeout(2000)

    return True


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    send_heartbeat(state)

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

            previous = state.get(
                url,
                "unknown"
            )

            try:

                loaded = fetch_page(
                    page,
                    url
                )

                if not loaded:

                    print(
                        "  Could not check"
                    )

                    continue

                # --------------------------------------------
                # STORE-SPECIFIC DETECTION
                # --------------------------------------------

                available = check_availability(
                    page,
                    url
                )

                now = (
                    "available"
                    if available
                    else "not_available"
                )

                print(
                    f"  {product_name(url)} => "
                    f"{now} "
                    f"(previous: {previous})"
                )

                # --------------------------------------------
                # ALERT ONLY ON:
                #
                # not_available -> available
                #
                # --------------------------------------------

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

                    print(
                        "  🚨 NEW STOCK DETECTED!"
                    )

                state[url] = now

            except Exception as e:

                print(
                    f"  ERROR checking {url}: {e}"
                )

                # Keep previous state if check failed.
                continue

            # Small delay between websites.
            time.sleep(1)

        context.close()
        browser.close()

    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    if newly_available:

        message = (
            "🚨 POKÉMON RESTOCK ALERT 🚨\n\n"
            + "\n\n".join(newly_available)
        )

        print(
            "\nSENDING TELEGRAM ALERT..."
        )

        print(message)

        try:

            telegram_send(message)

            print(
                "Telegram alert sent."
            )

        except Exception as e:

            print(
                f"Telegram failed: {e}"
            )

    else:

        print(
            "\nNo new stock. "
            "No notification sent."
        )

    save_state(state)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
