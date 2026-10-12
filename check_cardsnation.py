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
PAGE_TIMEOUT_MS = 60000
PAGE_WAIT_MS = 2500
RETRIES = 2

DIRECT_PRODUCTS = [
    {"name": "Portugal | 30th Celebration ETB | Toys R Us", "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Elite-Trainer-Box-(Ingl%C3%AAs)/p/K1108946"},
    {"name": "Portugal | 30th Celebration Booster Bundle | Toys R Us", "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Booster-Bundle-(Ingl%C3%AAs)/p/K1108953"},
    {"name": "Portugal | 30th Celebration Booster Bundle | Centroxogo", "url": "https://www.centroxogo.pt/pokemon-tcg-30th-celebration-booster-bundle-003pc10451101.html"},
    {"name": "Portugal | 30th Celebration UPC | Toys R Us", "url": "https://www.toysrus.pt/Pok%C3%A9mon-30%C2%BA-Anivers%C3%A1rio-Ultra-Premium-Collection-(Ingl%C3%AAs)-V%C3%A1rios-modelos/p/K1108948"},
    {"name": "Portugal | 30th Celebration UPC | Centroxogo", "url": "https://www.centroxogo.pt/pokemon-tcg-30th-celebration-ultra-premium-003pc10448101.html"},
    {"name": "Portugal | 30th Celebration UPC | Geekhaven", "url": "https://geekhaven.pt/products/pokemon-30th-celebration-ultra-premium-collection-night?variant=54637255819587"},
    {"name": "Czechia | Prismatic Evolutions SPC | Alza", "url": "https://www.alza.cz/EN/toys/pokemon-tcg-sv8-5-prismatic-evolutions-super-premium-collection-d12913273.htm"},
]

ALZA_SEARCHES = [
    {"name": "Czechia | Alza | ETB search results", "url": "https://www.alza.cz/search.htm?exps=elite+trainer+box", "keywords": ["elite trainer box"]},
    {"name": "Czechia | Alza | Booster Bundle search results", "url": "https://www.alza.cz/search.htm?exps=booster+bundle", "keywords": ["booster bundle"]},
]

UNAVAILABLE_TERMS = [
    "out of stock", "sold out", "currently unavailable", "temporarily unavailable",
    "notify me when available", "esgotado", "esgotada", "sem stock", "sem estoque",
    "indisponivel", "nao disponivel", "avise-me quando", "avise me quando",
    "vyprodano", "neni skladem", "neni dostupne", "neni dostupny", "nedostupne",
    "nedostupny", "momentálně nedostupné", "momentálně nedostupný",
]
AVAILABLE_TERMS = [
    "add to cart", "add to basket", "buy now", "in stock", "available now",
    "adicionar ao carrinho", "adicionar ao cesto", "comprar", "em stock", "em estoque",
    "disponivel", "skladem", "koupit", "do kosiku", "pridat do kosiku", "vlozit do kosiku",
]
BOT_TERMS = [
    "verify you are human", "checking your browser", "captcha", "access denied",
    "unusual traffic", "robot or human", "security check", "just a moment",
]
ANNIVERSARY_TERMS = ["30th celebration", "30th anniversary", "30th", "celebration", "anniversary", "30. vyroci", "30. vyrocie"]


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().lower()


def telegram_send(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub secret.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": True}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the message.")


def load_state():
    if not STATE_FILE.exists():
        return {"items": {}, "last_heartbeat_hour": ""}
    with STATE_FILE.open("r", encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        raise RuntimeError("state.json must contain a JSON object.")
    state.setdefault("items", {})
    state.setdefault("last_heartbeat_hour", "")
    return state


def save_state(state):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def is_bot_page(text):
    n = normalize(text)
    return any(term in n for term in BOT_TERMS)


def save_diagnostic(name, text):
    DIAGNOSTICS_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:65]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = DIAGNOSTICS_DIR / f"{safe}_{stamp}.txt"
    path.write_text((text or "")[:30000], encoding="utf-8")
    print(f"Saved diagnostic: {path}")


def get_main_text(page):
    for selector in ["main", "#main", "#content", "[role='main']"]:
        try:
            loc = page.locator(selector).first
            if loc.count() and loc.is_visible():
                txt = loc.inner_text(timeout=5000)
                if len(txt.strip()) > 80:
                    return txt
        except Exception:
            pass
    try:
        return page.locator("body").inner_text(timeout=8000)
    except Exception:
        return ""


def detect_stock(text):
    n = normalize(text)
    if not n or is_bot_page(n):
        return "unknown"
    unavailable = any(term in n for term in UNAVAILABLE_TERMS)
    available = any(term in n for term in AVAILABLE_TERMS)
    if unavailable and available:
        return "unknown"
    if unavailable:
        return "unavailable"
    if available:
        return "available"
    return "unknown"


def open_page(page, url, name):
    last_error = ""
    for attempt in range(1, RETRIES + 1):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            page.wait_for_timeout(PAGE_WAIT_MS)
            if response:
                code = response.status
                print(f"HTTP status: {code} (attempt {attempt}/{RETRIES})")
                if code in (403, 451):
                    print(f"Access blocked by the site (HTTP {code}); not retrying or treating as out of stock: {url}")
                    return "blocked", ""
                if code >= 400:
                    last_error = f"HTTP {code}"
                    if attempt < RETRIES:
                        continue
                    return "error", ""
            text = get_main_text(page)
            if is_bot_page(text):
                print("Possible verification/access page detected.")
                save_diagnostic(name, text)
                return "blocked", text
            return "ok", text
        except PlaywrightTimeoutError as exc:
            last_error = f"Navigation timeout: {exc}"
            print(f"Timeout on attempt {attempt}/{RETRIES} for {name}")
            if attempt < RETRIES:
                try:
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            print(f"Page error on attempt {attempt}/{RETRIES}: {last_error}")
            if attempt < RETRIES:
                continue
    print(f"Page check failed after retries: {last_error}")
    return "error", ""


def check_direct_product(page, product):
    name, url = product["name"], product["url"]
    print(f"\nChecking: {name}\nURL: {url}")
    outcome, text = open_page(page, url, name)
    if outcome != "ok":
        return "blocked" if outcome == "blocked" else "unknown"
    status = detect_stock(text)
    if status == "unknown":
        try:
            controls = page.locator("button:visible, [role='button']:visible, input[type='submit']:visible")
            labels = " ".join(controls.all_inner_texts())
            control_status = detect_stock(labels)
            if control_status in ("available", "unavailable"):
                status = control_status
        except Exception:
            pass
    print(f"Status: {status}")
    if status == "unknown":
        save_diagnostic(name, text)
    return status


def matches_alza_product(title, keywords):
    n = normalize(title)
    return "pokemon" in n and any(k in n for k in keywords) and any(t in n for t in ANNIVERSARY_TERMS)


def get_card_text(anchor):
    for selector in [
        "xpath=ancestor::*[contains(@class,'browsingitem')][1]",
        "xpath=ancestor::*[contains(@class,'box')][1]",
        "xpath=ancestor::article[1]",
    ]:
        try:
            card = anchor.locator(selector).first
            if card.count():
                txt = card.inner_text(timeout=2500)
                if len(txt.strip()) > 20:
                    return txt
        except Exception:
            pass
    try:
        return anchor.inner_text(timeout=2000)
    except Exception:
        return ""


def check_alza_search(page, search):
    print(f"\nSearching: {search['name']}\nURL: {search['url']}")
    outcome, text = open_page(page, search["url"], search["name"])
    if outcome != "ok":
        return None
    results = {}
    main = page.locator("main").first
    scope = main if main.count() else page.locator("body")
    anchors = scope.locator("a[href]")
    count = min(anchors.count(), 2000)
    seen = set()
    for i in range(count):
        try:
            anchor = anchors.nth(i)
            href = anchor.get_attribute("href") or ""
            title = (anchor.get_attribute("title") or "").strip() or anchor.inner_text(timeout=1000).strip()
            full_url = urljoin(search["url"], href)
            parsed = urlparse(full_url)
            if not parsed.netloc.endswith("alza.cz") or not re.search(r"-d\d+\.htm$", parsed.path, re.I):
                continue
            if full_url in seen or not matches_alza_product(title, search["keywords"]):
                continue
            seen.add(full_url)
            card_text = get_card_text(anchor)
            status = detect_stock(card_text)
            results[full_url] = {"name": f"Alza | {title[:130]}", "url": full_url, "status": status}
            print(f"Alza result: {title[:90]} -> {status}")
        except Exception as exc:
            print(f"Skipping one Alza result: {type(exc).__name__}: {exc}")
    print(f"Matching Alza products found: {len(results)}")
    return results


def record_observation(state, key, name, url, status):
    # Blocked/unknown observations never overwrite a previously confirmed state.
    if status not in ("available", "unavailable"):
        print(f"State unchanged ({status}): {name}")
        return
    items = state.setdefault("items", {})
    previous = items.get(key, {}).get("status")
    if previous == "unavailable" and status == "available":
        try:
            telegram_send(f"🟢 POKÉMON BACK IN STOCK!\n\n{name}\n\n🔗 {url}")
            print(f"Back-in-stock alert sent: {name}")
        except Exception as exc:
            print(f"Alert failed; previous state retained: {type(exc).__name__}: {exc}")
            return
    items[key] = {"name": name, "url": url, "status": status}


def main():
    print("=" * 60)
    print("Pokémon Stock Monitor")
    print(datetime.now(timezone.utc).isoformat())
    print("=" * 60)
    state = load_state()
    results_summary = []
    blocked = 0
    unknown = 0
    errors = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="pt-PT",
            timezone_id="Europe/Prague",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.set_default_timeout(12000)

        for product in DIRECT_PRODUCTS:
            status = check_direct_product(page, product)
            if status == "blocked":
                blocked += 1
            elif status == "unknown":
                unknown += 1
                errors += 1
            record_observation(state, product["url"], product["name"], product["url"], status)
            results_summary.append((product["name"], status))

        for search in ALZA_SEARCHES:
            alza_results = check_alza_search(page, search)
            if alza_results is None:
                blocked += 1
                continue
            for url, product in alza_results.items():
                status = product["status"]
                if status == "unknown":
                    unknown += 1
                    errors += 1
                record_observation(state, "alza:" + url, product["name"], url, status)
                results_summary.append((product["name"], status))
        browser.close()

    save_state(state)
    current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
    if state.get("last_heartbeat_hour") != current_hour:
        available = sum(s == "available" for _, s in results_summary)
        unavailable = sum(s == "unavailable" for _, s in results_summary)
        heartbeat = (
            "💓 Pokémon monitor heartbeat\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Known in stock: {available}\n"
            f"Known out of stock: {unavailable}\n"
            f"Blocked pages (403/451/verification): {blocked}\n"
            f"Unknown results: {unknown}\n"
            f"Errors/uncertain checks: {errors}\n"
            f"Pages with results: {len(results_summary)}\n\n"
            "Note: blocked/unknown pages are not treated as out of stock."
        )
        telegram_send(heartbeat)
        state["last_heartbeat_hour"] = current_hour
        save_state(state)

    print("=" * 60)
    print(f"Finished. Results: {len(results_summary)}; blocked: {blocked}; unknown: {unknown}; errors: {errors}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"FATAL ERROR: {type(exc).__name__}: {exc}")
        sys.exit(1)
