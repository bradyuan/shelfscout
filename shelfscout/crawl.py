"""Selenium crawler (public, signed-out data): keyword -> your product + top organic competitors.

    uv run python -m shelfscout.crawl --keyword "wireless earbuds"
    uv run python -m shelfscout.crawl --keyword "wireless earbuds" --offline   # cache only, no Amazon

Collects what Amazon shows signed-out visitors: search rank, title, price, rating, bullets,
star breakdown, Amazon's AI "Customers say" summary and its topic tags. Individual reviews need a
signed-in browser, so the Chrome extension collects those (see extension/).

Rules: visible browser, 6-12 s between page loads, every page cached to data/cache/ so reruns
never hit Amazon twice, stops on CAPTCHA. Selenium fetches/renders; BeautifulSoup parses.
"""
import argparse
import hashlib
import json
import os
import random
import re
import threading
import time

from bs4 import BeautifulSoup

from .trace import DATA, Run, StageFailed

BASE = "https://www.amazon.com"
CACHE = DATA / "cache"
CATALOG = DATA / "catalog.json"  # current product set: what the API + extension read
CATALOG_LOCK = threading.Lock()
SPONSORED = ('.puis-sponsored-label-text, .s-sponsored-label-text, '
             '[data-component-type="sp-sponsored-result"], a[href*="/sspa/"]')


class Blocked(Exception):
    pass


HEADLESS = os.environ.get("SHELFSCOUT_HEADLESS", "1") != "0"  # 0 = show the Chrome window


class Fetcher:
    def __init__(self, offline: bool = False, delay=(6, 12), headless: bool | None = None):
        self.offline, self.delay, self.driver = offline, delay, None
        self.headless = HEADLESS if headless is None else headless
        self.fetched = self.cached = 0
        CACHE.mkdir(parents=True, exist_ok=True)

    def get(self, url: str, wait_for: str | None = None) -> str:
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        path = CACHE / f"{key}.html"
        if path.exists():
            self.cached += 1
            return path.read_text()
        if self.offline:
            raise Blocked(f"offline and not cached: {url}")
        if self.driver is None:
            from selenium import webdriver
            opts = webdriver.ChromeOptions()
            opts.add_argument("--window-size=1280,1000")
            opts.add_argument("--lang=en-US")
            if self.headless:
                opts.add_argument("--headless=new")
                # headless Chrome announces itself in its user agent; use a normal one
                opts.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            self.driver = webdriver.Chrome(options=opts)
        else:
            time.sleep(random.uniform(*self.delay))
        self.driver.get(url)
        time.sleep(2)
        if wait_for:  # lazy sections only load once scrolled into view
            from selenium.common.exceptions import TimeoutException
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            for frac in (0.3, 0.5, 0.7, 0.85):
                self.driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight*{frac})")
                time.sleep(1)
            try:
                WebDriverWait(self.driver, 10).until(lambda d: d.find_elements(By.CSS_SELECTOR, wait_for))
            except TimeoutException:
                pass
        if "/checkpoint/" in self.driver.current_url or "/errors/validateCaptcha" in self.driver.current_url:
            if self.headless:  # nobody can click "Continue shopping" in an invisible window
                raise Blocked("Amazon bot checkpoint (headless); rerun with SHELFSCOUT_HEADLESS=0 to clear it by hand")
            print(f"\n>>> Amazon bot checkpoint on {url}\n>>> Click 'Continue shopping' in the Chrome window (waiting up to 120 s)")
            t = time.time()
            while time.time() - t < 120 and "/checkpoint/" in self.driver.current_url:
                time.sleep(2)
            if "/checkpoint/" in self.driver.current_url:
                raise Blocked("Amazon bot checkpoint not cleared; stopping (nothing cached for this page)")
            if self.driver.current_url.rstrip("/").split("?")[0] != url.split("?")[0]:
                self.driver.get(url)
            time.sleep(3)
            if wait_for:
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight*0.7)")
                time.sleep(3)
        html = self.driver.page_source
        if "/dp/" in url and "productTitle" not in html:
            raise Blocked(f"product page didn't load (got '{self.driver.title[:60]}'); not cached")
        if "validateCaptcha" in html or "Enter the characters you see below" in html:
            raise Blocked("Amazon showed a CAPTCHA; stopping (nothing cached for this page)")
        path.write_text(html)
        (CACHE / f"{key}.url").write_text(self.driver.current_url)
        self.fetched += 1
        return html

    def close(self):
        if self.driver:
            self.driver.quit()


def _txt(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else None


def _num(s, cast=float):
    m = re.search(r"[\d,.]+", s or "")
    return cast(m.group().replace(",", "")) if m else None


# ---------- stage 1: search ----------
def search(fetch: Fetcher, keyword: str, n_competitors: int, asin: str | None):
    soup = BeautifulSoup(fetch.get(f"{BASE}/s?k={keyword.replace(' ', '+')}"), "lxml")
    rows = []
    for pos, card in enumerate(soup.select('div[data-component-type="s-search-result"]'), 1):
        a = card.get("data-asin")
        if not a:
            continue
        sponsored = bool(card.find(string=re.compile(r"^\s*Sponsored\s*$"))) or \
            bool(card.select_one(SPONSORED))
        rows.append(dict(asin=a, position=pos, sponsored=sponsored, title=_txt(card.select_one("h2"))))
    n_sponsored = sum(r["sponsored"] for r in rows)
    picked, seen = [], set()
    for r in rows:
        if not r["sponsored"] and r["asin"] not in seen:
            seen.add(r["asin"])
            picked.append(r)
    ads = {r["asin"] for r in rows if r["sponsored"]}
    out = [{**r, "role": "competitor", "organic_rank": i + 1, "advertised": r["asin"] in ads,
            "sponsored_skipped": n_sponsored}
           for i, r in enumerate(picked[:n_competitors])]
    if asin:  # product focus: this product + the search's competitors
        out = [dict(asin=asin, position=None, sponsored=False, title=None, role="focus")] + \
              [r for r in out if r["asin"] != asin]
    return out


# ---------- stage 2: listings ----------
def parse_listing(soup):
    hist = {}
    for a in soup.select('#histogramTable [aria-label*="percent of reviews"]'):
        m = re.match(r"(\d+) percent of reviews have (\d) star", a.get("aria-label", ""))
        if m:
            hist[f"{m.group(2)}★"] = int(m.group(1))
    summary = None
    h3 = soup.find(lambda t: t.name == "h3" and t.get_text(strip=True) == "Customers say")
    if h3:
        summary = _txt(h3.parent).removeprefix("Customers say").split("AI Generated")[0].strip()
    topics = []
    for el in soup.select('[data-csa-c-slot-id^="cr-product-insights-detail-page_button_"]'):
        m = re.match(r".*_button_\d+_([A-Z]+)_(.+)$", el["data-csa-c-slot-id"])
        if m and (m.group(2), m.group(1).lower()) not in topics:
            topics.append((m.group(2), m.group(1).lower()))
    return dict(
        title=_txt(soup.select_one("#productTitle")),
        price=_txt(soup.select_one(".a-price .a-offscreen")),
        rating=_num(_txt(soup.select_one("#acrPopover .a-icon-alt"))),
        n_ratings=_num(_txt(soup.select_one("#acrCustomerReviewText")), int),
        bullets=[b for b in (_txt(li) for li in soup.select("#feature-bullets li span.a-list-item")) if b],
        star_breakdown=hist,
        amazon_summary=summary,
        topics=[{"topic": t, "sentiment": s} for t, s in topics],
    )


def listings(fetch: Fetcher, products):
    for p in products:
        soup = BeautifulSoup(fetch.get(f"{BASE}/dp/{p['asin']}", wait_for="#histogramTable"), "lxml")
        d = parse_listing(soup)
        yield dict(asin=p["asin"], role=p.get("role", "competitor"), search_position=p.get("position"),
                   organic_rank=p.get("organic_rank"), advertised=p.get("advertised"),
                   **{**d, "title": d["title"] or p.get("title")})


def completeness(rows):
    fields = ["title", "price", "rating", "bullets", "star_breakdown", "amazon_summary"]
    full = sum(all(r.get(f) for f in fields) for r in rows)
    return f"{full}/{len(rows)}"


def enrich(asins: list[str], source_run: str, show: bool = False) -> str:
    """Fill in public listing details (Selenium, signed out) for products the extension sent."""
    fetch = Fetcher(headless=not show)
    run = Run("enrich", notes=f"Selenium public details for {len(asins)} products from snapshot {source_run}")
    try:
        with CATALOG_LOCK:
            cat = json.loads(CATALOG.read_text())
        prods = [p for p in cat["products"] if p["asin"] in asins]
        lst = run.stage("listings", lambda x: listings(fetch, x), prods, one_to_one=True,
                        metric=lambda i, o: ("all fields present", completeness(o)))
        by = {l["asin"]: l for l in lst}
        with CATALOG_LOCK:
            cat = json.loads(CATALOG.read_text())
            if cat.get("run_id") == source_run:  # don't write into a newer snapshot
                for p in cat["products"]:
                    for k, v in by.get(p["asin"], {}).items():
                        if k not in ("role", "search_position") and v not in (None, [], {}):
                            p[k] = v
                    if p["asin"] in by:
                        p["enriched_by"] = run.id
                CATALOG.write_text(json.dumps(cat, indent=2))
        run.finish("ok")
    except (StageFailed, Blocked) as e:
        if run.status == "running":
            run.finish("failed")
        print("ENRICH STOPPED:", e)
    finally:
        fetch.close()
    return run.id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="wireless earbuds")
    ap.add_argument("--asin", help="focus product's ASIN (default: none, niche research mode)")
    ap.add_argument("--competitors", type=int, default=10)
    ap.add_argument("--offline", action="store_true", help="use cached pages only")
    a = ap.parse_args()

    fetch = Fetcher(offline=a.offline)
    run = Run("crawl", notes=f"G1a Selenium public crawl · keyword '{a.keyword}'")
    try:
        prods = run.stage("search", lambda _: search(fetch, a.keyword, a.competitors, a.asin), [],
                          metric=lambda i, o: ("products", len(o)))
        lst = run.stage("listings", lambda x: listings(fetch, x), prods, one_to_one=True,
                        metric=lambda i, o: ("all fields present", completeness(o)))
        with CATALOG_LOCK:
            CATALOG.write_text(json.dumps({"keyword": a.keyword, "mode": "product" if a.asin else "search",
                                           "run_id": run.id, "products": lst}, indent=2))
        run.finish("ok")
    except (StageFailed, Blocked) as e:
        if run.status == "running":
            run.finish("failed")
        print("STOPPED:", e)
    finally:
        fetch.close()
    print(f"pages fetched {fetch.fetched}, from cache {fetch.cached}")
    print(f"run {run.id}: {run.status} -> http://localhost:8100/runs/{run.id}")


if __name__ == "__main__":
    main()
