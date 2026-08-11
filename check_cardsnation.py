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
    "https://www.vesely-drak.cz/produkty/pokemon-elite-trainer-box/19132-pokemon-30th-celebration-elite-trainer-box/",
    "https://www.ihrysko.sk/pokemon-30th-celebration-elite-trainer-box-p122315",
    "https://www.cdmc.cz/premiove-kolekce/pokemon-tcg--first-partner-illustration-collection-series-3/",
    "https://www.cdmc.cz/sv10-destined-rivals/",
    "https://www.cdmc.cz/sv8-5-prismatic-evolutions/",
    "https://www.cdmc.cz/elite-trainer-boxy/",
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

# These are pages where one URL contains multiple products.
#
# The NUMBER of available products is NOT hardcoded.
# This only tells the script:
# "inspect individual product cards on this page".
MULTI_PRODUCT_URLS = {
    "https://www.alza.cz/search.htm?exps=Elite+Trainer+box",
    "https://www.smarty.cz/elite-trainer-box-4c14603",
    "https://www.hrananetu.cz/pokemon-trainer-boxy",
    "https://www.alola.cz/elite-trainer-boxy/",
    "https://www.kitstore.cz/elite-trainer-box",
    "https://www.cdmc.cz/me05-pitch-black/",
    "https://www.cdmc.cz/sv10-destined-rivals/",
    "https://www.cdmc.cz/sv8-5-prismatic-evolutions/",
    "https://www.cdmc.cz/elite-trainer-boxy/",
    "https://www.xzone.cz/pokemon-tcg-elite-trainer-boxy?sort=date_desc&s=60&page=1&term=&c=946",
    "https://www.smarty.sk/Vyhladavanie?query=Pok%C3%A9mon%20TCG%3A%2030th%20Celebration",
}


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


def load_state() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(
                STATE_FILE.read_text(encoding="utf-8")
            )
        except Exception:
            return {}

    return {}


def save_state(state: Dict) -> None:
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def is_available(url: str, html: str) -> bool:
    """
    Used mainly for SINGLE-PRODUCT pages.
    """
    host = urlparse(url).netloc.lower()

    # Xzone
    if "xzone.cz" in host:
        if re.search(
            r"\bOutOfStock\b",
            html,
            re.IGNORECASE,
        ):
            return False

        if re.search(
            r"\bInStock\b",
            html,
            re.IGNORECASE,
        ):
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

    # Smarty.sk search
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

    # Vesely Drak
    if "vesely-drak.cz" in host:
        if any(
            re.search(
                p,
                html,
                re.IGNORECASE,
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
                re.IGNORECASE,
            )
            for p in [
                r"\bDo\s+košíku\b",
                r"\bVložit\s+do\s+košíku\b",
                r"\bPřidat\s+do\s+košíku\b",
            ]
        )

    # Generic sites
    if any(
        re.search(
            p,
            html,
            re.IGNORECASE,
        )
        for p in AVAILABLE_PATTERNS
    ):
        return True

    if any(
        re.search(
            p,
            html,
            re.IGNORECASE,
        )
        for p in NOT_AVAILABLE_PATTERNS
    ):
        return False

    return False


def product_card_is_available(
    url: str,
    text: str,
) -> bool:
    """
    Checks ONE product card rather than the entire page.
    """
    host = urlparse(url).netloc.lower()

    # HranaNetu:
    # trust actual quantity such as:
    # 1 ks na skladě
    # 4+ ks na skladě
    if "hrananetu.cz" in host:
        return bool(
            re.search(
                r"\b\d+\+?\s*ks\s+na\s+skladě\b",
                text,
                re.IGNORECASE,
            )
        )

    # Explicit negatives inside THIS product card win.
    if any(
        re.search(
            p,
            text,
            re.IGNORECASE,
        )
        for p in NOT_AVAILABLE_PATTERNS
    ):
        return False

    # Otherwise look for positive signals in THIS card.
    return any(
        re.search(
            p,
            text,
            re.IGNORECASE,
        )
        for p in AVAILABLE_PATTERNS
    )


def extract_available_products(
    page,
    url: str,
) -> Dict[str, str]:
    """
    Returns:

    {
        "PRODUCT_URL": "Product name",
        ...
    }

    Only products which currently look available are returned.
    """

    available = {}

    # Generic ecommerce product-card selectors.
    #
    # Playwright treats this as one combined selector.
    cards = page.locator(
        "[data-product-id], "
        "[data-product], "
        ".product-card, "
        ".product-item, "
        ".product-box, "
        ".product-list-item, "
        ".product, "
        "article"
    )

    try:
        card_count = cards.count()
    except Exception:
        return available

    for i in range(card_count):
        card = cards.nth(i)

        try:
            text = card.inner_text(
                timeout=1000
            ).strip()
        except Exception:
            continue

        if not text:
            continue

        # Is this specific card available?
        if not product_card_is_available(
            url,
            text,
        ):
            continue

        # Find a link identifying this product.
        links = card.locator("a[href]")

        try:
            link_count = links.count()
        except Exception:
            continue

        product_url = None

        for j in range(link_count):
            link = links.nth(j)

            try:
                href = link.get_attribute(
                    "href"
                )
            except Exception:
                continue

            if not href:
                continue

            href = href.strip()

            # Skip useless links.
            if (
                href.startswith("#")
                or href.startswith("javascript:")
                or href.startswith("mailto:")
                or href.startswith("tel:")
            ):
                continue

            product_url = urljoin(
                url,
                href,
            )

            break

        if not product_url:
            continue

        # Try to get a readable product name.
        product_name = ""

        for selector in [
            "h2",
            "h3",
            "h4",
            ".product-name",
            ".product-title",
            "[class*='product-name']",
            "[class*='product-title']",
        ]:
            try:
                candidate = card.locator(
                    selector
                ).first

                if candidate.count():
                    product_name = (
                        candidate.inner_text(
                            timeout=500
                        ).strip()
                    )

                    if product_name:
                        break

            except Exception:
                pass

        # Fallback: first reasonable line of card text.
        if not product_name:
            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            if lines:
                product_name = lines[0][:150]

        if not product_name:
            product_name = product_url

        available[product_url] = product_name

    return available


def fetch_page(
    url: str,
    timeout_ms: int = 25000,
):
    """
    Loads the rendered page and returns:

    html,
    available_products

    available_products is only populated for
    MULTI_PRODUCT_URLS.
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True
        )

        context = browser.new_context(
            locale="cs-CZ",
            user_agent=(
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
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
                timeout=timeout_ms,
            )

        except PWTimeoutError:
            # Some sites never go fully idle.
            pass

        # Give late JS rendering a moment.
        page.wait_for_timeout(1500)

        html = page.content()

        available_products = {}

        if url in MULTI_PRODUCT_URLS:
            available_products = (
                extract_available_products(
                    page,
                    url,
                )
            )

        context.close()
        browser.close()

        return html, available_products


def maybe_send_heartbeat(
    state: Dict,
) -> None:
    now_ts = int(time.time())

    last = int(
        state.get(
            "_last_heartbeat",
            0,
        )
    )

    # 30 minutes for testing.
    # Change this back to:
    # SIX_HOURS = 6 * 60 * 60
    SIX_HOURS = 30 * 60

    if now_ts - last >= SIX_HOURS:
        utc_time = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.gmtime(),
        )

        available_count = 0

        for url in URLS:
            item = state.get(url)

            if isinstance(item, dict):
                # Multi-product page
                if item.get("products"):
                    available_count += 1

                # Single-product page
                elif (
                    item.get("status")
                    == "available"
                ):
                    available_count += 1

        telegram_send(
            f"💓 Heartbeat\n"
            f"🕒 UTC: {utc_time}\n"
            f"🔗 Monitoring: {len(URLS)} pages\n"
            f"📦 Pages with availability: "
            f"{available_count}"
        )

        state["_last_heartbeat"] = now_ts


def main() -> None:
    state = load_state()

    maybe_send_heartbeat(state)

    notifications = []

    for i, url in enumerate(URLS):
        try:
            html, current_products = (
                fetch_page(url)
            )

            #
            # MULTI-PRODUCT PAGE
            #
            if url in MULTI_PRODUCT_URLS:
                previous_data = state.get(
                    url,
                    {},
                )

                if not isinstance(
                    previous_data,
                    dict,
                ):
                    previous_data = {}

                previous_products = set(
                    previous_data.get(
                        "products",
                        [],
                    )
                )

                current_product_urls = set(
                    current_products.keys()
                )

                # Products that are available NOW,
                # but weren't available last run.
                new_products = (
                    current_product_urls
                    - previous_products
                )

                print(
                    f"{url} => "
                    f"{len(current_product_urls)} "
                    f"available products "
                    f"(prev: "
                    f"{len(previous_products)})"
                )

                #
                # Important:
                #
                # Don't notify on very first run,
                # otherwise every already-available
                # product would generate an alert.
                #
                initialized = (
                    previous_data.get(
                        "initialized",
                        False,
                    )
                )

                if (
                    initialized
                    and new_products
                ):
                    lines = [
                        "✅ NEW PRODUCT AVAILABLE"
                    ]

                    for product_url in sorted(
                        new_products
                    ):
                        name = (
                            current_products.get(
                                product_url,
                                product_url,
                            )
                        )

                        lines.append("")
                        lines.append(name)
                        lines.append(product_url)

                    lines.append("")
                    lines.append(
                        f"Page: {url}"
                    )

                    notifications.append(
                        "\n".join(lines)
                    )

                state[url] = {
                    "initialized": True,
                    "products": sorted(
                        current_product_urls
                    ),
                }

            #
            # SINGLE PRODUCT PAGE
            #
            else:
                previous_data = state.get(
                    url,
                    {},
                )

                if not isinstance(
                    previous_data,
                    dict,
                ):
                    previous_data = {}

                prev_status = (
                    previous_data.get(
                        "status",
                        "unknown",
                    )
                )

                now_status = (
                    "available"
                    if is_available(
                        url,
                        html,
                    )
                    else "not_available"
                )

                print(
                    f"{url} => "
                    f"{now_status} "
                    f"(prev: {prev_status})"
                )

                if (
                    prev_status
                    != "available"
                    and now_status
                    == "available"
                    and prev_status
                    != "unknown"
                ):
                    notifications.append(
                        "✅ PRODUCT AVAILABLE\n"
                        f"{url}"
                    )

                state[url] = {
                    "status": now_status,
                }

        except Exception as e:
            print(
                f"ERROR fetching {url}: {e}"
            )

        if i < len(URLS) - 1:
            time.sleep(2)

    #
    # Send notifications
    #
    for message in notifications:
        try:
            telegram_send(message)
        except Exception as e:
            print(
                f"ERROR sending Telegram: {e}"
            )

    save_state(state)


if __name__ == "__main__":
    main()
