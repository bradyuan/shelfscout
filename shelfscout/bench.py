"""Relevance-hell benchmark: freeze real Amazon searches so relevance can be tested deterministically.

    uv run python -m shelfscout.bench            # runs every query not yet frozen
    uv run python -m shelfscout.bench --redo     # re-freeze everything

For each query: open the search page in headless Chrome, run the extension's own parsers on it (same code
the user's tab runs), read feature data for the top FEATURE_N candidates in-page, and save
data/bench/<slug>.json = {query, pattern, key_terms, url, cards, features, result}. Each freeze is a stage row
in one "bench" run in the factory view. `suspect` is only a triage hint (key terms missing from the best-value
title); Brad's labels are the answer key.
"""
import argparse
import json
import random
import re
import time
from pathlib import Path

from . import value
from .trace import DATA, Run

BENCH = DATA / "bench"
EXT = Path(__file__).resolve().parent.parent / "extension"
FEATURE_N = 5
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# (query, pattern, key terms the right product must match; "|" = alternatives)
QUERIES = [
    ("teardrop tattoo", "specific", ["teardrop|tear drop"]),
    ("small cross temporary tattoo", "specific", ["cross"]),
    ("8 inch cast iron skillet", "specific", ["8 inch|8-inch|8\"|8 in", "cast iron"]),
    ("10 ft usb c to lightning cable", "specific", ["10 ft|10ft|10 feet|10-foot", "lightning", "usb c|usb-c|type c|type-c"]),
    ("queen linen duvet cover", "specific", ["queen", "linen"]),
    ("glass meal prep containers 3 compartment", "specific", ["glass", "3 compartment|3-compartment|3 comp"]),
    ("iphone 15 pro case", "compatibility", ["15 pro"]),
    ("levoit core 300 replacement filter", "compatibility", ["core 300|core300"]),
    ("ryobi 18v battery", "compatibility", ["ryobi", "18v|18 v|18-volt|18 volt"]),
    ("kindle paperwhite 11th generation case", "compatibility", ["paperwhite", "11th"]),
    ("dyson v8 battery", "compatibility", ["v8"]),
    ("airpods pro 2 ear tips", "compatibility", ["airpods pro", "tips"]),
    ("samsung tv replacement remote", "compatibility", ["samsung", "remote"]),
    ("fragrance free shampoo", "without", ["fragrance free|fragrance-free|unscented"]),
    ("sugar free gummy bears", "without", ["sugar free|sugar-free"]),
    ("dairy free protein powder", "without", ["dairy free|dairy-free|vegan|plant"]),
    ("caffeine free tea bags", "without", ["caffeine free|caffeine-free|decaf|herbal"]),
    ("wireless earbuds without stem", "without", ["earbud"]),
    ("gluten free pasta", "without", ["gluten free|gluten-free"]),
    ("left handed scissors", "size_fit", ["left"]),
    ("womens running shoes wide", "size_fit", ["wide"]),
    ("single aa battery charger", "size_fit", ["aa"]),
    ("king size pillow 1 pack", "size_fit", ["king"]),
    ("toddler toothbrush", "size_fit", ["toddler|kids|baby"]),
    ("mouse pad for kids", "ambiguous", ["mouse pad|mousepad"]),
    ("apple slicer", "ambiguous", ["slicer|corer"]),
    ("fake tattoo", "ambiguous", ["tattoo"]),
    ("mini fridge for skincare", "ambiguous", ["fridge"]),
    ("phone stand for desk", "ambiguous", ["stand"]),
    ("rain jacket for dogs", "ambiguous", ["dog"]),
]

PARSERS = (EXT / "page-kind.js").read_text() + ";" + (EXT / "parsers.js").read_text() + ";window.SSP=SSParsers;"
READ = """const cb=arguments[1];fetch('/dp/'+arguments[0],{credentials:'include'})
  .then(async r=>{const d=new DOMParser().parseFromString(await r.text(),'text/html');
    cb({blocked:SSP.blockKind(d,r.url),f:{features:SSP.features(d),stars:SSP.starBreakdown(d)}})})
  .catch(e=>cb({err:String(e)}))"""


def slug(q):
    return re.sub(r"\W+", "-", q.lower()).strip("-")


def matches(title, terms):
    t = (title or "").lower()
    return all(any(alt in t for alt in term.split("|")) for term in terms)


def driver():
    from selenium import webdriver
    o = webdriver.ChromeOptions()
    if not __import__("os").environ.get("BENCH_HEADED"):  # BENCH_HEADED=1 shows a browser window instead of headless
        o.add_argument("--headless=new")
    o.add_argument("--window-size=1280,1000")
    o.add_argument("--lang=en-US")
    o.add_argument(f"--user-agent={UA}")
    d = webdriver.Chrome(options=o)
    d.set_script_timeout(60)
    return d


def freeze(d, query, pattern, terms):
    d.get("https://www.amazon.com/s?k=" + query.replace(" ", "+"))
    time.sleep(3)
    if "/checkpoint/" in d.current_url or "validateCaptcha" in d.page_source[:20000]:
        raise RuntimeError("Amazon bot check on search page")
    d.execute_script(PARSERS)
    cards = d.execute_script("return SSP.searchCards(document)")
    if not cards:
        raise RuntimeError("no products parsed")
    quick = value.analyze(cards)
    features, log = {}, []
    for i, asin in enumerate(quick["fetch_targets"][:FEATURE_N]):
        if i:
            time.sleep(random.uniform(2, 4))
        t = time.time()
        r = d.execute_async_script(READ, asin)
        ms = int((time.time() - t) * 1000)
        if r.get("blocked") or r.get("err"):
            log.append({"asin": asin, "status": "failed", "reason": r.get("blocked") or r.get("err"), "ms": ms})
            break
        features[asin] = r["f"]
        log.append({"asin": asin, "status": "fetched", "n_features": len(r["f"]["features"]), "ms": ms})
    result = value.analyze(cards, features)
    snap = {"query": query, "pattern": pattern, "key_terms": terms, "url": d.current_url,
            "frozen_at": time.strftime("%Y-%m-%d %H:%M"), "cards": cards, "features": features,
            "fetch_log": log, "result": {k: result[k] for k in ("best_value", "best_price", "amazon_pick", "stats")}}
    (BENCH / f"{slug(query)}.json").write_text(json.dumps(snap, indent=1))
    return snap


def summary(snap):
    bv = snap["result"]["best_value"] or {}
    cards = snap["cards"]
    on = [c for c in cards if matches(c.get("title"), snap["key_terms"])]
    return {"query": snap["query"], "pattern": snap["pattern"], "items": len(cards),
            "title_matches": f"{len(on)}/{len(cards)}",
            "best_value": (bv.get("title") or "")[:70], "best_value_price": bv.get("price"),
            "suspect": bool(bv) and not matches(bv.get("title"), snap["key_terms"]),
            "features_read": len(snap["features"])}


def main(redo=False, only=None):
    BENCH.mkdir(parents=True, exist_ok=True)
    todo = [q for q in QUERIES if (only and slug(q[0]) in only) or
            (not only and (redo or not (BENCH / f"{slug(q[0])}.json").exists()))]
    run = Run("bench", notes=f"Relevance-hell benchmark: freezing {len(todo)} of {len(QUERIES)} searches")
    done, stop = [], None
    for i, (q, pattern, terms) in enumerate(todo):
        if i:
            time.sleep(random.uniform(12, 20))  # Amazon throttled ~120 loads in one session at 6-10 s
        d = driver()  # fresh browser per search: a long-lived headless session died mid-run twice
        try:
            snap = freeze(d, q, pattern, terms)
            failed = sum(l["status"] == "failed" for l in snap["fetch_log"])
            print(f"[{i + 1}/{len(todo)}] {q}: {len(snap['cards'])} cards, {len(snap['features'])} feature reads"
                  + (f", {failed} failed" if failed else ""), flush=True)
            done.append(q)
            if failed:
                time.sleep(60)  # back off when product pages start failing
        except Exception as e:  # stop on bot checks; keep what's frozen
            stop = f"{q}: {str(e).splitlines()[0][:160]}"
            print("STOP", stop, flush=True)
            break
        finally:
            d.quit()
    snaps = [json.loads(p.read_text()) for p in sorted(BENCH.glob("*.json")) if p.stem not in ("labels", "ai_latest", "jury_labels")]
    run.stage("frozen_searches", lambda _: [summary(s) for s in snaps], [],
              metric=lambda i, o: ("best value matches search terms", f"{sum(not r['suspect'] for r in o)}/{len(o)}"))
    run.finish("ok" if not stop else "failed")
    print(f"frozen {len(done)} this run, {len(snaps)} total. stop: {stop}. run {run.id}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--only", nargs="*", help="slugs to (re)freeze, e.g. gluten-free-pasta")
    a = ap.parse_args()
    main(a.redo, set(a.only) if a.only else None)
