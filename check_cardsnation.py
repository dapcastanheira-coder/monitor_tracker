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

    # Alza
    "https://www.alza.cz/hracky/pokemon-tcg-30th-celebration-elite-trainer-box-d13521013.htm",

    # Rohlík
    "https://www.rohlik.cz/1483651-pokemon-tcg-30th-celebration-elite-trainer-box",
    "https://www.rohlik.cz/1483649-pokemon-tcg-30th-celebration-sylveon-ex-box",

    # Centroxogo - Portugal
    "https://www.centroxogo.pt/pokemon-tcg-30th-celebration-elite-trainer-box-003pc10447101.html",
    "https://www.centroxogo.pt/brinquedos-personagem/pokemon/cartas-tcg-pokemon/pokemon-tcg-30th-celebration-booster-bundle-003pc10451101.html",

    
    "https://www.elcorteingles.pt/brinquedos/A202042813-30-caixa-elite-trainer-comemoracao-do-30-aniversario-do-tcg-ingles-pokemon-bandai",
    "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Booster-Bundle-%28Ingl%C3%AAs%29/p/K1108953",
    
]


# ============================================================
# STATE
# ============================================================

STATE_FILE = Path("state.json")


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

    if "cernyrytir.cz" in host:
        return "Černý rytíř"

    if "alza.cz" in host:
        return "Alza"

    if "rohlik.cz" in host:
        return "Rohlík"

    if "centroxogo.pt" in host:
        return "Centroxogo 🇵🇹"

    return host


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

    # Store availability is NOT online stock
    if re.search(
        r"\bDostupné\s+na\s+prodejně\b",
        body_text,
        re.IGNORECASE,
    ):

        print(
            "  Smarty: store availability detected"
        )

    # Explicit unavailable states
    unavailable_patterns = [
        r"\bNení\s+skladem\b",
        r"\bNeni\s+skladem\b",
        r"\bVyprodáno\b",
        r"\bVyprodano\b",
        r"\bMomentálně\s+nedostupné\b",
        r"\bNelze\s+zakoupit\b",
        r"\bHlídat\s+produkt\b",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Smarty: unavailable: {pattern}"
            )

            return False

    # Online stock
    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        # If only store availability is shown,
        # don't treat it as online stock.
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

    unavailable_patterns = [
        r"Není skladem",
        r"Neni skladem",
        r"Vyprodáno",
        r"Vyprodano",
        r"Momentálně nedostupné",
        r"Momentálně vyprodáno",
        r"Nelze objednat",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Alza: unavailable: {pattern}"
            )

            return False

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

            print(
                f"  Alza: purchase signal: {pattern}"
            )

            return True

    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        print(
            "  Alza: Skladem detected"
        )

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

    unavailable_patterns = [
        r"\bVyprodáno\b",
        r"\bVyprodano\b",
        r"\bNení\s+dostupné\b",
        r"\bNeni\s+dostupne\b",
        r"\bNelze\s+zakoupit\b",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Rohlík: unavailable: {pattern}"
            )

            return False

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

            print(
                f"  Rohlík: purchase signal: {pattern}"
            )

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

            print(
                f"  Černý rytíř: unavailable: {pattern}"
            )

            return False

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

            print(
                f"  Černý rytíř: purchase signal: {pattern}"
            )

            return True

    if re.search(
        r"\bSkladem\b",
        body_text,
        re.IGNORECASE,
    ):

        print(
            "  Černý rytíř: Skladem detected"
        )

        return True

    return False


# ============================================================
# CENTROXOGO DETECTOR - PORTUGAL
# ============================================================

def is_centroxogo_available(page):

    print(
        "  Centroxogo: checking Portuguese stock..."
    )

    try:

        body_text = normalize(
            page.locator("body").inner_text()
        )

    except Exception as e:

        print(
            f"  Centroxogo body check failed: {e}"
        )

        return False

    # --------------------------------------------------------
    # UNAVAILABLE PORTUGUESE SIGNALS
    # --------------------------------------------------------

    unavailable_patterns = [
        r"\bEsgotado\b",
        r"\bEsgotada\b",
        r"\bSem\s+stock\b",
        r"\bSem\s+estoque\b",
        r"\bIndisponível\b",
        r"\bIndisponivel\b",
        r"\bNão\s+disponível\b",
        r"\bNao\s+disponivel\b",
        r"\bTemporariamente\s+indisponível\b",
        r"\bTemporariamente\s+indisponivel\b",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Centroxogo: unavailable: {pattern}"
            )

            return False

    # --------------------------------------------------------
    # PURCHASE BUTTONS / ACTIONS
    # --------------------------------------------------------

    purchase_patterns = [
        r"\bAdicionar\s+ao\s+carrinho\b",
        r"\bAdiciona\s+ao\s+carrinho\b",
        r"\bComprar\b",
        r"\bEncomendar\b",
        r"\bAdicionar\b",
    ]

    for pattern in purchase_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Centroxogo: purchase signal: {pattern}"
            )

            return True

    # --------------------------------------------------------
    # STOCK WORDS
    # --------------------------------------------------------

    stock_patterns = [
        r"\bEm\s+stock\b",
        r"\bEm\s+estoque\b",
        r"\bDisponível\b",
        r"\bDisponivel\b",
    ]

    for pattern in stock_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            print(
                f"  Centroxogo: stock signal: {pattern}"
            )

            return True

    print(
        "  Centroxogo: NOT AVAILABLE"
    )

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

    unavailable_patterns = [
        r"Není skladem",
        r"Neni skladem",
        r"Vyprodáno",
        r"Vyprodano",
        r"Out of stock",
        r"Sold out",
        r"Esgotado",
        r"Sem stock",
        r"Indisponível",
    ]

    for pattern in unavailable_patterns:

        if re.search(
            pattern,
            body_text,
            re.IGNORECASE,
        ):

            return False

    available_patterns = [
        r"Do košíku",
        r"Přidat do košíku",
        r"Koupit",
        r"Add to cart",
        r"Skladem",
        r"Em stock",
        r"Disponível",
        r"Disponivel",
    ]

    for pattern in available_patterns:

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

    if "smarty.cz" in host:
        return is_smarty_available(page)

    if "alza.cz" in host:
        return is_alza_available(page)

    if "rohlik.cz" in host:
        return is_rohlik_available(page)

    if "cernyrytir.cz" in host:
        return is_cernyrytir_available(page)

    if "centroxogo.pt" in host:
        return is_centroxogo_available(page)

    return is_generic_available(page)


# ============================================================
# FETCH PAGE
# ============================================================

def fetch_page(page, url):

    print(
        f"\nChecking: {url}"
    )

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

    # Allow JavaScript to render
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

            locale="pt-PT",

            timezone_id="Europe/Prague",

            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
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

                # ------------------------------------------------
                # ALERT ONLY WHEN:
                #
                # unavailable/unknown -> available
                # ------------------------------------------------

                if (
                    previous != "available"
                    and now == "available"
                ):

                    newly_available.append(
                        (
                            "🚨 POKÉMON BACK IN STOCK 🚨\n\n"
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

                # Keep previous state if check failed
                continue

            # Small delay between websites
            time.sleep(1)

        context.close()
        browser.close()

    # ========================================================
    # SEND TELEGRAM ALERT
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
