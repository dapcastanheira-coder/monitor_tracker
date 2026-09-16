import json
import os
import re
import time
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


# ============================================================
# PRODUCTS TO MONITOR
# ============================================================

URLS = [
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Elite-Trainer-Box-4p278101",

    "https://www.hrananetu.cz/p/pokemon-30th-celebration-elite-trainer-box",

    "https://www.cdmc.cz/elite-trainer-boxy/pokemon-tcg--30th-celebration-elite-trainer-box/",

    "https://www.cdmc.cz/blistery/pokemon-tcg--30th-celebration-2-pack-blister-eevee/",
    "https://cernyrytir.cz/merch/detail/47517394-b620-4713-8389-ce5779d94441",

    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-2-Pack-Blister-4p278095",
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Greninja-ex-Box-4p278100",

    "https://www.hrananetu.cz/p/pokemon-celebration-ditto-premium-collection",

    "https://www.xzone.cz/karetni-hra-pokemon-tcg-30th-celebration-celebration-tin-greninja-ex",
    "https://www.xzone.cz/karetni-hra-pokemon-tcg-30th-celebration-celebration-tin-sylveon-ex",

    "https://www.ihrysko.sk/pokemon-30th-celebration-elite-trainer-box-p122315",
    "https://www.ihrysko.sk/pokemon-30th-celebration-booster-bundle-p122317",

    "https://www.alza.cz/EN/toys/pokemon-tcg-30th-celebration-elite-trainer-box-d13521013.htm",
    "https://www.alza.cz/EN/toys/pokemon-tcg-30th-celebration-ex-tin-d13521014.htm",
    "https://www.alza.cz/EN/toys/pokemon-30th-celebration/18924117.htm",
    
]


# ============================================================
# SMARTY ETB SPECIAL STORE MONITOR
# ============================================================

SMARTY_ETB_URL = (
    "https://www.smarty.cz/Pokemon-TCG-30th-Celebration-Elite-Trainer-Box-4p278101"
)

BRNO_STORES = [
    "Brno - Královo Pole",
    "Brno - Olympia",
    "Brno - Vaňkovka",
]


# ============================================================
# STATE
# ============================================================

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


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=20,
    ).raise_for_status()


# ============================================================
# STATE FUNCTIONS
# ============================================================

def load_state() -> Dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            return {}

    return {}


def save_state(state: Dict[str, str]) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# HELPERS
# ============================================================

def normalize(text: str) -> str:
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


# ============================================================
# NORMAL PRODUCT AVAILABILITY
# ============================================================

def is_available(url: str, html: str) -> bool:

    host = urlparse(url).netloc.lower()

    # --------------------------------------------------------
    # XZONE
    # --------------------------------------------------------

    if "xzone.cz" in host:

        if re.search(
            r"\bOutOfStock\b",
            html,
            re.IGNORECASE
        ):
            return False

        if re.search(
            r"\bInStock\b",
            html,
            re.IGNORECASE
        ):
            return True

        return False


    # --------------------------------------------------------
    # HRANANETU
    # --------------------------------------------------------

    if "hrananetu.cz" in host:

        return bool(
            re.search(
                r"\b\d+\+?\s*ks\s+na\s+skladě\b",
                html,
                re.IGNORECASE,
            )
        )


    # --------------------------------------------------------
    # SMARTY.SK SEARCH PAGE
    # --------------------------------------------------------

    if (
        "smarty.sk" in host
        and "vyhladavanie" in url.lower()
    ):

        match = re.search(
            r"\bSkladom\s+celkom\s+\((\d+)\)",
            html,
            re.IGNORECASE,
        )

        if match:
            return int(match.group(1)) > 0

        return False


    # --------------------------------------------------------
    # VESELY DRAK
    # --------------------------------------------------------

    if "vesely-drak.cz" in host:

        if any(
            re.search(
                p,
                html,
                re.IGNORECASE
            )
            for p in [
                r"Na eshopu nemáme dostupné",
                r"Dočasně nedostupné",
                r"prodej tohoto produktu již skončil",
                r"Položka byla vyprodána",
            ]
        ):
            return False

        return any(
            re.search(
                p,
                html,
                re.IGNORECASE
            )
            for p in [
                r"\bDo\s+košíku\b",
                r"\bVložit\s+do\s+košíku\b",
                r"\bPřidat\s+do\s+košíku\b",
            ]
        )


    # --------------------------------------------------------
    # ALL OTHER SITES
    # --------------------------------------------------------

    if any(
        re.search(
            p,
            html,
            re.IGNORECASE
        )
        for p in AVAILABLE_PATTERNS
    ):
        return True


    if any(
        re.search(
            p,
            html,
            re.IGNORECASE
        )
        for p in NOT_AVAILABLE_PATTERNS
    ):
        return False


    return False


# ============================================================
# FETCH NORMAL PAGE
# ============================================================

def fetch_rendered_html(
    url: str,
    timeout_ms: int = 25000
) -> str:

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            locale="cs-CZ",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome Safari"
            ),
        )

        page = context.new_page()

        try:

            page.goto(
                url,
                wait_until="networkidle",
                timeout=timeout_ms
            )

        except PWTimeoutError:

            # Some sites never become fully idle.
            # We still use whatever loaded.
            pass


        page.wait_for_timeout(1500)

        html = page.content()

        context.close()
        browser.close()

        return html


# ============================================================
# SMARTY STORE ROW
# ============================================================

def get_store_row_text(
    page,
    store_name: str
) -> str:

    locator = page.get_by_text(
        re.compile(
            re.escape(store_name),
            re.IGNORECASE
        )
    ).first


    if not locator.is_visible(
        timeout=2500
    ):
        return ""


    current = locator


    # Walk upwards through the DOM until we find
    # the store container containing availability.
    for _ in range(6):

        try:

            text = normalize(
                current.inner_text(
                    timeout=1000
                )
            )

        except Exception:

            text = ""


        lower = text.lower()


        if (
            store_name.lower() in lower
            and (
                "skladem" in lower
                or "není skladem" in lower
                or "neni skladem" in lower
            )
        ):

            return text


        try:

            current = current.locator("..")

        except Exception:

            break


    return normalize(
        locator.inner_text()
    )


# ============================================================
# PARSE SMARTY STORE STATUS
# ============================================================

def parse_store_status(
    text: str
) -> str:

    text = normalize(text)


    # IMPORTANT:
    # Check "Není skladem" FIRST.

    if re.search(
        r"\bNení\s+skladem\b",
        text,
        re.IGNORECASE
    ):
        return "not_available"


    if re.search(
        r"\bNeni\s+skladem\b",
        text,
        re.IGNORECASE
    ):
        return "not_available"


    if re.search(
        r"\bSkladem\b",
        text,
        re.IGNORECASE
    ):
        return "available"


    return "unknown"


# ============================================================
# CHECK SMARTY BRNO STORES
# ============================================================

def fetch_smarty_brno_stock() -> Dict[str, str]:

    results = {
        store: "unknown"
        for store in BRNO_STORES
    }


    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )


        context = browser.new_context(

            locale="cs-CZ",

            timezone_id="Europe/Prague",

            user_agent=(
                "Mozilla/5.0 "
                "(Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0.0.0 "
                "Safari/537.36"
            ),

            viewport={
                "width": 1440,
                "height": 1000
            },
        )


        page = context.new_page()


        try:

            page.goto(
                SMARTY_ETB_URL,
                wait_until="domcontentloaded",
                timeout=25000
            )

        except PWTimeoutError:

            pass


        # Allow Smarty's JS to finish.
        page.wait_for_timeout(1500)


        # ----------------------------------------------------
        # COOKIE POPUPS
        # ----------------------------------------------------

        for selector in [

            "button:has-text('Souhlasím')",

            "button:has-text('Přijmout')",

            "button:has-text('Akceptovat')",

        ]:

            try:

                btn = page.locator(
                    selector
                ).first


                if btn.is_visible(
                    timeout=700
                ):

                    btn.click(
                        timeout=1500
                    )

                    page.wait_for_timeout(
                        300
                    )

                    break

            except Exception:

                pass


        # ----------------------------------------------------
        # CLICK "DOSTUPNÉ NA PRODEJNĚ"
        # ----------------------------------------------------

        try:

            button = page.get_by_role(
                "button",
                name=re.compile(
                    r"Dostupné na prodejně",
                    re.IGNORECASE
                ),
            ).first


            button.click(
                timeout=10000
            )


        except Exception:

            # Fallback if Smarty renders it
            # as another element type.

            button = page.get_by_text(
                re.compile(
                    r"^Dostupné na prodejně$",
                    re.IGNORECASE
                )
            ).first


            button.click(
                timeout=10000
            )


        # Wait for store popup.
        page.wait_for_timeout(800)


        # ----------------------------------------------------
        # READ THREE BRNO STORES
        # ----------------------------------------------------

        for store in BRNO_STORES:

            try:

                row_text = get_store_row_text(
                    page,
                    store
                )


                results[store] = parse_store_status(
                    row_text
                )


                print(
                    f"    {store}: "
                    f"{results[store]} "
                    f"| {row_text}"
                )


            except Exception as e:

                print(
                    f"    {store}: "
                    f"ERROR reading row: {e}"
                )


        # ----------------------------------------------------
        # FALLBACK BODY-TEXT PARSER
        # ----------------------------------------------------

        body = normalize(
            page.locator(
                "body"
            ).inner_text()
        )


        for store in BRNO_STORES:

            if results[store] != "unknown":
                continue


            idx = body.lower().find(
                store.lower()
            )


            if idx < 0:
                continue


            # Store availability should be close
            # to the store name.

            chunk = body[
                idx:idx + 350
            ]


            status = parse_store_status(
                chunk
            )


            results[store] = status


        context.close()
        browser.close()


    return results


# ============================================================
# HEARTBEAT
# ============================================================

def maybe_send_heartbeat(
    state: Dict[str, str]
) -> None:

    now_ts = int(
        time.time()
    )


    last = int(
        state.get(
            "_last_heartbeat",
            0
        )
    )


    # 30 minutes
    HEARTBEAT_INTERVAL = 30 * 60


    if now_ts - last >= HEARTBEAT_INTERVAL:

        utc_time = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.gmtime()
        )


        available_count = sum(
            1
            for u in URLS
            if state.get(u) == "available"
        )


        brno_available = sum(
            1
            for store in BRNO_STORES
            if state.get(
                f"{SMARTY_ETB_URL}::{store}"
            ) == "available"
        )


        telegram_send(

            f"💓 Heartbeat\n"
            f"🕒 UTC: {utc_time}\n"
            f"🔗 Monitoring: {len(URLS)} products\n"
            f"📦 Available now: {available_count}\n"
            f"🏪 Smarty Brno ETB: "
            f"{brno_available}/3 stores in stock"
        )


        state[
            "_last_heartbeat"
        ] = now_ts


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    state = load_state()

    maybe_send_heartbeat(
        state
    )


    changed_to_available = []


    for i, url in enumerate(URLS):


        # ====================================================
        # SPECIAL SMARTY ETB CHECK
        # ====================================================

        if url == SMARTY_ETB_URL:

            print()
            print("=" * 70)
            print(
                "SMARTY 30th ETB "
                "— CHECKING BRNO STORES"
            )
            print("=" * 70)


            try:

                brno_results = (
                    fetch_smarty_brno_stock()
                )


                any_brno_available = False


                for store in BRNO_STORES:

                    key = (
                        f"{SMARTY_ETB_URL}"
                        f"::{store}"
                    )


                    prev = state.get(
                        key,
                        "unknown"
                    )


                    now = brno_results.get(
                        store,
                        "unknown"
                    )


                    print(
                        f"{store} => {now} "
                        f"(prev: {prev})"
                    )


                    # Don't replace a valid previous
                    # state with unknown after a temporary
                    # website/browser problem.

                    if now == "unknown":

                        if prev in (
                            "available",
                            "not_available"
                        ):

                            now = prev

                        else:

                            now = "unknown"


                    state[key] = now


                    if now == "available":

                        any_brno_available = True


                    # Alert only on transition:
                    # not available -> available

                    if (
                        prev != "available"
                        and now == "available"
                    ):

                        changed_to_available.append(

                            f"🏪 Smarty — {store}\n"
                            f"📦 Pokémon 30th "
                            f"Celebration ETB\n"
                            f"💰 1,999 Kč\n"
                            f"🔗 {SMARTY_ETB_URL}"
                        )


                # The main Smarty URL state now means:
                # "available in at least one BRNO store"

                state[url] = (
                    "available"
                    if any_brno_available
                    else "not_available"
                )


            except Exception as e:

                print(
                    "ERROR checking "
                    f"Smarty Brno stores: {e}"
                )


        # ====================================================
        # ALL OTHER PRODUCTS
        # ====================================================

        else:

            prev = state.get(
                url,
                "unknown"
            )


            try:

                html = fetch_rendered_html(
                    url
                )


                now = (
                    "available"
                    if is_available(
                        url,
                        html
                    )
                    else "not_available"
                )


            except Exception as e:

                print(
                    f"ERROR fetching {url}: {e}"
                )


                now = state.get(
                    url,
                    "not_available"
                )


            state[url] = now


            print(
                f"{url} => {now} "
                f"(prev: {prev})"
            )


            if (
                prev != "available"
                and now == "available"
            ):

                changed_to_available.append(
                    url
                )


        # Small delay between products.

        if i < len(URLS) - 1:

            time.sleep(2)


    # ========================================================
    # SEND ALERT
    # ========================================================

    if changed_to_available:

        telegram_send(

            "🚨 AVAILABLE NOW 🚨\n\n"
            + "\n\n".join(
                changed_to_available
            )
        )


    save_state(
        state
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
