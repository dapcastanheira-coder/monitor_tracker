
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


STATE_FILE = Path("state.json")
DIAGNOSTICS_DIR = Path("diagnostics")
PAGE_TIMEOUT_MS = 45000
PAGE_WAIT_MS = 2500

DIRECT_PRODUCTS = [
    {
        "name": "Portugal | 30th Celebration ETB | Toys R Us",
        "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Elite-Trainer-Box-(Ingl%C3%AAs)/p/K1108946",
    },
    {
        "name": "Portugal | 30th Celebration Booster Bundle | Toys R Us",
        "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Booster-Bundle-(Ingl%C3%AAs)/p/K1108953",
    },
    {
        "name": "Portugal | 30th Celebration Booster Bundle | Centroxogo",
        "url": "https://www.centroxogo.pt/pokemon-tcg-30th-celebration-booster-bundle-003pc10451101.html",
    },
    {
        "name": "Portugal | 30th Celebration UPC | Toys R Us",
        "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Ultra-Premium-Collection-(Ingl%C3%AAs)-V%C3%A1rios-modelos/p/K1108948",
    },
    {
        "name": "Portugal | 30th Celebration UPC | Centroxogo",
        "url": "https://www.centroxogo.pt/pokemon-tcg-30th-celebration-ultra-premium-003pc10448101.html",
    },
    {
        "name": "Portugal | 30th Celebration UPC | Geekhaven",
        "url": "https://geekhaven.pt/products/pokemon-30th-celebration-ultra-premium-collection-night?variant=54637255819587",
    },
    {
        "name": "Czechia | Prismatic Evolutions SPC | Alza",
        "url": "https://www.alza.cz/EN/toys/pokemon-tcg-sv8-5-prismatic-evolutions-super-premium-collection-d12913273.htm",
    },
]

ALZA_SEARCHES = [
    {
        "name": "Czechia | Alza | ETB search results",
        "url": "https://www.alza.cz/search.htm?exps=elite+trainer+box",
        "keywords": ["elite trainer box"],
    },
    {
        "name": "Czechia | Alza | Booster Bundle search results",
        "url": "https://www.alza.cz/search.htm?exps=booster+bundle",
        "keywords": ["booster bundle"],
    },
]

UNAVAILABLE_TERMS = [
    "out of stock",
    "sold out",
    "currently unavailable",
    "temporarily unavailable",
    "notify me when available",
    "esgotado",
    "esgotada",
    "sem stock",
    "sem estoque",
    "indisponivel",
    "nao disponivel",
    "avise-me quando",
    "avise me quando",
    "vyprodano",
    "neni skladem",
    "neni dostupne",
    "neni dostupny",
    "nedostupne",
    "nedostupny",
    "momentálně nedostupné",
    "momentálně nedostupný",
]

AVAILABLE_TERMS = [
    "add to cart",
    "add to basket",
    "buy now",
    "in stock",
    "available now",
    "adicionar ao carrinho",
    "adicionar ao cesto",
    "comprar",
    "em stock",
    "em estoque",
    "disponivel",
    "skladem",
    "koupit",
    "do kosiku",
    "pridat do kosiku",
    "vlozit do kosiku",
]

BOT_TERMS = [
    "verify you are human",
    "checking your browser",
    "captcha",
    "access denied",
    "unusual traffic",
    "robot or human",
    "security check",
    "just a moment",
]

ANNIVERSARY_TERMS = [
    "30th celebration",
    "30th anniversary",
    "30th",
    "celebration",
    "anniversary",
    "30. vyroci",
    "30. vyrocie",
]


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(
        character for character in value
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", value).strip().lower()


def telegram_send(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret."
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message.")


def load_state():
    if not STATE_FILE.exists():
        return {"items": {}, "last_heartbeat_hour": ""}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state, dict):
            raise ValueError("State must be a JSON object.")

        state.setdefault("items", {})
        state.setdefault("last_heartbeat_hour", "")
        return state

    except Exception as exc:
        raise RuntimeError(
            "state.json is invalid or unreadable. Back it up before repairing it."
        ) from exc


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(STATE_FILE)


def is_bot_page(text):
    normalized = normalize(text)
    return any(term in normalized for term in BOT_TERMS)


def save_diagnostic(name, text):
    DIAGNOSTICS_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:65]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = DIAGNOSTICS_DIR / f"{safe_name}_{timestamp}.txt"
    path.write_text((text or "")[:30000], encoding="utf-8")
    print(f"Saved diagnostic: {path}")


def get_main_text(page):
    for selector in ["main", "#main", "#content", "[role='main']"]:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                text = locator.inner_text(timeout=4000)
                if len(text.strip()) > 80:
                    return text
        except Exception:
            pass

    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def detect_stock(text):
    """
    Conservative multilingual stock detection.

    Returns available, unavailable or unknown.
    Unknown observations never overwrite a known state.
    """
    text = normalize(text)

    if not text or is_bot_page(text):
        return "unknown"

    unavailable = any(term in text for term in UNAVAILABLE_TERMS)
    available = any(term in text for term in AVAILABLE_TERMS)

    if unavailable and available:
        return "unknown"

    if unavailable:
        return "unavailable"

    if available:
        return "available"

    return "unknown"


def open_page(page, url):
    response = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT_MS,
    )
    page.wait_for_timeout(PAGE_WAIT_MS)

    if response and response.status >= 400:
        print(f"HTTP error {response.status}: {url}")
        return None

    return get_main_text(page)


def check_direct_product(page, product):
    name = product["name"]
    url = product["url"]

    print(f"\nChecking: {name}")
    print(f"URL: {url}")

    try:
        text = open_page(page, url)

        if text is None:
            return "unknown"

        if is_bot_page(text):
            print("Blocked or verification page detected.")
            save_diagnostic(name, text)
            return "unknown"

        status = detect_stock(text)

        # For direct product pages, inspect visible purchase controls
        # if the page text did not provide a clear answer.
        if status == "unknown":
            try:
                controls = page.locator(
                    "button:visible, [role='button']:visible, "
                    "input[type='submit']:visible"
                )
                labels = " ".join(controls.all_inner_texts())
                status_from_controls = detect_stock(labels)

                if status_from_controls in ("available", "unavailable"):
                    status = status_from_controls
            except Exception:
                pass

        print(f"Status: {status}")

        if status == "unknown":
            save_diagnostic(name, text)

        return status

    except PlaywrightTimeoutError:
        print(f"Timeout while checking {name}")
        return "unknown"
    except Exception as exc:
        print(f"Error checking {name}: {type(exc).__name__}: {exc}")
        return "unknown"


def matches_alza_product(title, keywords):
    title = normalize(title)

    if "pokemon" not in title:
        return False

    if not any(keyword in title for keyword in keywords):
        return False

    # Exclude ordinary products unrelated to the anniversary release.
    return any(term in title for term in ANNIVERSARY_TERMS)


def get_card_text(anchor):
    selectors = [
        "xpath=ancestor::*[contains(@class,'browsingitem')][1]",
        "xpath=ancestor::*[contains(@class,'box')][1]",
        "xpath=ancestor::article[1]",
    ]

    for selector in selectors:
        try:
            card = anchor.locator(selector).first
            if card.count():
                text = card.inner_text(timeout=2000)
                if len(text.strip()) > 20:
                    return text
        except Exception:
            pass

    try:
        return anchor.inner_text(timeout=1500)
    except Exception:
        return ""


def check_alza_search(page, search):
    print(f"\nSearching: {search['name']}")
    results = {}

    try:
        text = open_page(page, search["url"])

        if text is None:
            return None

        if is_bot_page(text):
            print("Alza returned a verification page.")
            save_diagnostic(search["name"], text)
            return None

        main = page.locator("main").first
        scope = main if main.count() else page.locator("body")
        anchors = scope.locator("a[href]")
        count = min(anchors.count(), 2000)
        seen_urls = set()

        for index in range(count):
            try:
                anchor = anchors.nth(index)
                href = anchor.get_attribute("href") or ""
                title = (anchor.get_attribute("title") or "").strip()

                if not title:
                    title = anchor.inner_text(timeout=1000).strip()

                full_url = urljoin(search["url"], href)
                parsed = urlparse(full_url)

                if not parsed.netloc.endswith("alza.cz"):
                    continue

                if not re.search(r"-d\d+\.htm$", parsed.path, re.I):
                    continue

                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                if not matches_alza_product(title, search["keywords"]):
                    continue

                card_text = get_card_text(anchor)
                status = detect_stock(card_text)

                results[full_url] = {
                    "name": f"Alza | {title[:130]}",
                    "url": full_url,
                    "status": status,
                }

                print(f"Result: {title[:90]} -> {status}")

            except Exception as exc:
                print(f"Skipping one search result: {type(exc).__name__}")

        print(f"Matching Alza products found: {len(results)}")
        return results

    except PlaywrightTimeoutError:
        print("Alza search timed out.")
        return None
    except Exception as exc:
        print(f"Alza search error: {type(exc).__name__}: {exc}")
        return None


def record_observation(state, key, name, url, status):
    if status not in ("available", "unavailable"):
        print(f"Keeping previous state; result is uncertain: {name}")
        return

    items = state.setdefault("items", {})
    previous_item = items.get(key, {})
    previous = previous_item.get("status")

    # Only alert on a confirmed out-of-stock -> in-stock transition.
    if previous == "unavailable" and status == "available":
        message = (
            f"🟢 POKÉMON BACK IN STOCK!\n\n"
            f"{name}\n\n"
            f"🔗 {url}"
        )

        try:
            telegram_send(message)
            print(f"Back-in-stock alert sent: {name}")
        except Exception as exc:
            # Do not update state on failed alert delivery. This permits
            # the next run to retry the alert.
            print(
                f"Alert failed; state was not updated: "
                f"{type(exc).__name__}: {exc}"
            )
            return

    items[key] = {
        "name": name,
        "url": url,
        "status": status,
    }


def main():
    print("=" * 60)
    print("Pokémon Stock Monitor")
    print(datetime.now(timezone.utc).isoformat())
    print("=" * 60)

    state = load_state()
    summary = []
    errors = 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            locale="pt-PT",
            timezone_id="Europe/Prague",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.set_default_timeout(10000)

        for product in DIRECT_PRODUCTS:
            status = check_direct_product(page, product)

            if status == "unknown":
                errors += 1

            record_observation(
                state,
                product["url"],
                product["name"],
                product["url"],
                status,
            )
            summary.append((product["name"], status))

        for search in ALZA_SEARCHES:
            results = check_alza_search(page, search)

            if results is None:
                errors += 1
                continue

            for url, product in results.items():
                status = product["status"]

                if status == "unknown":
                    errors += 1

                record_observation(
                    state,
                    "alza:" + url,
                    product["name"],
                    url,
                    status,
                )
                summary.append((product["name"], status))

        browser.close()

    # Save all observations before sending the heartbeat.
    save_state(state)

    current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

    if state.get("last_heartbeat_hour") != current_hour:
        in_stock = sum(status == "available" for _, status in summary)
        out_of_stock = sum(status == "unavailable" for _, status in summary)
        unknown = sum(status == "unknown" for _, status in summary)

        heartbeat = (
            "💓 Pokémon monitor heartbeat\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Known in stock: {in_stock}\n"
            f"Known out of stock: {out_of_stock}\n"
            f"Unknown results: {unknown}\n"
            f"Errors/uncertain checks: {errors}\n"
            f"Pages with results: {len(summary)}"
        )

        telegram_send(heartbeat)
        state["last_heartbeat_hour"] = current_hour
        save_state(state)

    print("=" * 60)
    print(f"Finished. Results: {len(summary)}; uncertain/errors: {errors}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL ERROR: {type(exc).__name__}: {exc}")
        sys.exit(1)
