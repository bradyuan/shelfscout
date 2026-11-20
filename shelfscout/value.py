"""Best value vs best price vs Amazon's pick, plus insights, from the cards on a search page.
Plain math (the ProScan-style baseline); G2 replaces "value" with an LLM score.

fair rating   = (n * rating + M * page_avg) / (n + M)   -> 4.8 from 12 ratings no longer beats 4.4 from 1.8k
contenders    = products with >= MIN_RATINGS ratings that no other product beats on both price and fair rating
best value    = cheapest contender within TOLERANCE stars of the best fair rating
complaints    = Amazon features (from product pages) with >= MAJOR_MIN_MENTIONS mentions and >= 30% negative;
                products with one can't be best value while a complaint-free contender exists
"""
from statistics import median

M = 200           # ratings of "doubt": a product needs about this many before its own average dominates
TOLERANCE = 0.15  # stars you'd give up to pay less
MIN_RATINGS = 50  # fewer ratings than this can't be best value: too little evidence
DEAL_RATING = 4.3 # fair rating a "good deal" needs
DEAL_DISCOUNT = 0.7  # ... at or below 70% of the median price
MAJOR_MIN_MENTIONS = 10  # an Amazon feature needs this many mentions before its negatives count
MAJOR_NEG_SHARE = 0.30   # ... and at least this share negative to be a "major complaint"
FETCH_N = 10             # products whose feature data the extension reads


# ---------- input hygiene: the extension's parsers can misread a field; one bad product must not break a page ----------

def _num(x, kind=float, lo=None, hi=None):
    """A finite number in [lo, hi] (numeric strings like "$1,299.00" count), else None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, str):
        try:
            x = float(x.replace(",", "").replace("$", "").strip())
        except ValueError:
            return None
    if not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
        return None
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return kind(x)


def sanitize_products(products) -> list[dict]:
    """Coerce every field to its type (or None); drop rows without a usable ASIN."""
    out = []
    for p in products if isinstance(products, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("asin"), str) or not p["asin"].strip():
            continue
        out.append({**{k: v for k, v in p.items() if isinstance(v, (str, int, float, bool)) or v is None},
                    "asin": p["asin"].strip()[:20],
                    "title": p["title"][:500] if isinstance(p.get("title"), str) else "",
                    "price": _num(p.get("price"), float, 0),
                    "rating": _num(p.get("rating"), float, 0, 5),
                    "n_ratings": _num(p.get("n_ratings"), int, 0),
                    "position": _num(p.get("position"), int, 0),
                    "organic_rank": _num(p.get("organic_rank"), int, 0),
                    "sponsored": p.get("sponsored") is True, "advertised": p.get("advertised") is True,
                    "badges": [b for b in p.get("badges") or [] if isinstance(b, str)] if isinstance(p.get("badges"), list) else []})
    return out


def sanitize_features(features) -> dict:
    """{asin: {features: [{name, mentions, positive, negative}], stars: {"1".."5": pct}}} with bad entries dropped."""
    out = {}
    for asin, f in (features.items() if isinstance(features, dict) else []):
        if not isinstance(f, dict):
            continue
        feats = []
        for a in f.get("features") or [] if isinstance(f.get("features"), list) else []:
            if not isinstance(a, dict) or not isinstance(a.get("name"), str):
                continue
            m = _num(a.get("mentions"), int, 0)
            neg, pos = _num(a.get("negative"), int, 0), _num(a.get("positive"), int, 0)
            if m is None or neg is None or neg > m:
                continue
            feats.append({**a, "mentions": m, "negative": neg, "positive": pos if pos is not None and pos <= m else max(0, m - neg)})
        stars = f.get("stars") if isinstance(f.get("stars"), dict) else {}
        stars = {k: v for k, v in ((str(k), _num(v, float, 0, 100)) for k, v in stars.items()) if k in "12345" and len(k) == 1 and v is not None}
        out[asin] = {**f, "features": feats, "stars": stars}
    return out


def clean(products: list[dict]) -> list[dict]:
    """One row per ASIN (ads repeat products), and no $0 / missing prices."""
    seen, out = set(), []
    for p in products:
        if p.get("asin") in seen or not (p.get("price") or 0) > 0:
            continue
        seen.add(p["asin"])
        out.append(p)
    return out


def fair_ratings(products: list[dict]) -> list[dict]:
    rated = [p for p in products if p.get("price") and p.get("rating") and p.get("n_ratings")]
    if not rated:
        return []
    avg = sum(p["rating"] * p["n_ratings"] for p in rated) / sum(p["n_ratings"] for p in rated)
    return [{**p, "fair_rating": round((p["n_ratings"] * p["rating"] + M * avg) / (p["n_ratings"] + M), 3)}
            for p in rated]


def contenders(rated: list[dict]) -> list[dict]:
    def beaten(p):
        return any(q is not p and q["price"] <= p["price"] and q["fair_rating"] >= p["fair_rating"]
                   and (q["price"] < p["price"] or q["fair_rating"] > p["fair_rating"]) for q in rated)
    return sorted((p for p in rated if not beaten(p)), key=lambda p: p["price"])


def complaints(feats: list[dict]) -> list[str]:
    """Amazon features with enough mentions and a high negative share, e.g. 'durability 40% negative (8/20)'."""
    return [f"{a['name']} {round(100 * a['negative'] / a['mentions'])}% negative ({a['negative']}/{a['mentions']})"
            for a in feats or [] if a["mentions"] >= MAJOR_MIN_MENTIONS and a["negative"] / a["mentions"] >= MAJOR_NEG_SHARE]


def with_features(items: list[dict], features: dict) -> list[dict]:
    """Attach Amazon's per-feature counts (fetched by the extension) and what they imply."""
    out = []
    for p in items:
        f = features.get(p["asin"])
        if not f:
            out.append(p)
            continue
        feats = f.get("features") or []
        mentions = sum(a["mentions"] for a in feats)
        stars = f.get("stars") or {}
        out.append({**p, "features": feats, "complaints": complaints(feats),
                    "neg_share": round(sum(a["negative"] for a in feats) / mentions, 3) if mentions else None,
                    "low_star_pct": sum(stars.get(k, 0) for k in ("1", "2", "3")) if stars else None})
    return out


def fetch_targets(products: list[dict], n: int = FETCH_N) -> list[str]:
    """Products worth reading feature data for: the contenders, then the best fair ratings, up to n."""
    rated = [p for p in fair_ratings(clean(products)) if p["n_ratings"] >= MIN_RATINGS]
    ranked = [p["asin"] for p in contenders(rated)] + [p["asin"] for p in sorted(rated, key=lambda p: -p["fair_rating"])]
    return list(dict.fromkeys(ranked))[:n]


AI_SHORTLIST = 100  # products the off-topic filter checks (default: all analyzed)


def ai_shortlist(products: list[dict], n: int = AI_SHORTLIST, feature_n: int = FETCH_N) -> list[str]:
    """What the off-topic filter checks, best candidates first: feature-read targets, then well-reviewed products
    by fair rating, then everything else."""
    items = clean(products)
    rated = fair_ratings(items)
    trusted = [p["asin"] for p in sorted(rated, key=lambda p: -p["fair_rating"]) if p["n_ratings"] >= MIN_RATINGS]
    rest = [p["asin"] for p in sorted(rated, key=lambda p: -p["fair_rating"]) if p["n_ratings"] < MIN_RATINGS]
    ranked = fetch_targets(products, feature_n) + trusted + rest + [p["asin"] for p in items]
    return list(dict.fromkeys(ranked))[:n]


def top_picks(products: list[dict], features: dict | None = None, n: int = 5, relevance: dict | None = None) -> list[dict]:
    """ShelfScout's ranking: best value first, then well-reviewed products without major complaints by fair rating,
    then well-reviewed ones with complaints, then the rest."""
    items = with_features(clean(products), features or {})
    rated = [p for p in fair_ratings(items) if relevance.get(p["asin"]) is True] if relevance else fair_ratings(items)
    best = analyze(products, features, relevance)["best_value"]
    by_fair = sorted(rated, key=lambda p: (-p["fair_rating"], p["price"]))
    tiers = ([p for p in by_fair if p["n_ratings"] >= MIN_RATINGS and not p.get("complaints")],
             [p for p in by_fair if p["n_ratings"] >= MIN_RATINGS and p.get("complaints")],
             [p for p in by_fair if p["n_ratings"] < MIN_RATINGS])
    order = ([best] if best else []) + [p for t in tiers for p in t]
    seen, out = set(), []
    for p in order:
        if p["asin"] not in seen:
            seen.add(p["asin"])
            out.append(p)
    return out[:n]


def analyze(products: list[dict], features: dict | None = None, relevance: dict | None = None,
            feature_n: int = FETCH_N, ai_n: int = AI_SHORTLIST) -> dict:
    """relevance: {asin: True/False} from AI mode; products marked False can't be best value or contenders."""
    items = with_features(clean(products), features or {})
    off = {a for a, v in (relevance or {}).items() if v is False}
    all_rated = fair_ratings(items)
    fair = {r["asin"]: r["fair_rating"] for r in all_rated}
    # with AI mode on, only products it confirmed relevant can be picked
    rated = [p for p in all_rated if relevance.get(p["asin"]) is True] if relevance else all_rated
    trusted = [p for p in rated if p["n_ratings"] >= MIN_RATINGS]
    clean_ones = [p for p in trusted if not p.get("complaints")]
    front = contenders(clean_ones or trusted)
    med = median(p["price"] for p in items) if items else None

    best_price = min(items, key=lambda p: p["price"]) if items else None
    badged = [p for p in items if any(b in " ".join(p.get("badges") or []) for b in ("Overall Pick", "Amazon's Choice"))]
    shown_first = min(items, key=lambda p: p.get("position") or 1e9) if items else None
    amazon = badged[0] if badged else shown_first
    value = None
    if front:
        top = max(p["fair_rating"] for p in front)
        best = max(front, key=lambda p: p["fair_rating"])
        value = min((p for p in front if p["fair_rating"] >= top - TOLERANCE), key=lambda p: p["price"])
        value = {**value, "why": _why(value, best, med) + _feature_note(value, bool(clean_ones))}

    def tag(p, **extra):
        if not p:
            return None
        n = p.get("n_ratings") or 0
        warn = ("not what you searched for" if p["asin"] in off else "few ratings" if n < MIN_RATINGS
                else p["complaints"][0] if p.get("complaints") else "ad" if p.get("sponsored") else None)
        risk = list(p.get("complaints") or []) + (["no ratings"] if not n else [f"only {n} rating{'s' if n != 1 else ''}"] if n < MIN_RATINGS else [])
        return {**p, "fair_rating": fair.get(p["asin"]), "warn": warn, "risk": risk,
                "ad": bool(p.get("sponsored") or p.get("advertised")),
                "unchecked": bool(relevance) and p["asin"] not in relevance, **extra}

    # the rest of the list: matches with no ratings yet, then (after filtering) products the AI didn't check
    unrated = [p for p in items if p["asin"] not in fair and (not relevance or relevance.get(p["asin"]) is True)]
    unchecked = [p for p in items if relevance and p["asin"] not in relevance]

    return {
        "stats": {"items": len(items), "avg_rating": _avg([p.get("rating") for p in items]),
                  "median_price": med, "ads": sum(bool(p.get("sponsored") or p.get("advertised")) for p in items)},
        "best_value": tag(value),
        "best_price": tag(best_price),
        "amazon_pick": tag(amazon, pick_reason="badge" if badged else "shown first"),
        "contenders": front,
        "insights": _insights_with_best(items, rated, med, amazon, relevance, value),
        "ranked": [tag(p) for p in _ranked(rated, value) + unrated + unchecked],   # best value, then by fair rating
        "fetch_targets": fetch_targets(products, feature_n),
        "ai_shortlist": ai_shortlist(products, ai_n, feature_n),
        "features_known": sum(1 for p in items if "features" in p),
        "products": items,
    }


def _ranked(rated, best):
    """ShelfScout's order: best value, then well-reviewed without complaints, then with complaints, then the rest."""
    by_fair = sorted(rated, key=lambda p: (-p["fair_rating"], p["price"]))
    tiers = ([p for p in by_fair if p["n_ratings"] >= MIN_RATINGS and not p.get("complaints")],
             [p for p in by_fair if p["n_ratings"] >= MIN_RATINGS and p.get("complaints")],
             [p for p in by_fair if p["n_ratings"] < MIN_RATINGS])
    order = ([next(p for p in rated if p["asin"] == best["asin"])] if best and any(p["asin"] == best["asin"] for p in rated) else [])
    seen, out = set(), []
    for p in order + [p for t in tiers for p in t]:
        if p["asin"] not in seen:
            seen.add(p["asin"])
            out.append(p)
    return out


def _insights_with_best(items, rated, med, amazon, relevance, best):
    insights._best = best["asin"] if best else None
    try:
        return insights(items, rated, med, amazon, relevance)
    finally:
        insights._best = None


def insights(items, rated, med, amazon, relevance=None) -> list[dict]:
    if not items:
        return []
    judged = relevance or {}
    deals = [p for p in rated if p["n_ratings"] >= MIN_RATINGS and p["fair_rating"] >= DEAL_RATING and p["price"] <= DEAL_DISCOUNT * med]
    risky = [p for p in items if (p.get("rating") or 0) >= 4.5 and (p.get("n_ratings") or 0) < MIN_RATINGS]
    ads = [p for p in items if p.get("sponsored") or p.get("advertised")]
    out = [
        {"priority": "HIGH", "insight": f"{len(deals)} good deals",
         "detail": f"fair rating {DEAL_RATING}+ and at least {round((1 - DEAL_DISCOUNT) * 100)}% below the median price (${med:.2f})",
         "action": "Shortlist these", "asins": [p["asin"] for p in deals]},
        {"priority": "MEDIUM", "insight": f"{len(risky)} risky high ratings",
         "detail": f"4.5★+ but fewer than {MIN_RATINGS} ratings", "action": "Too little evidence; don't trust the stars",
         "asins": [p["asin"] for p in risky]},
        {"priority": "MEDIUM", "insight": f"{len(ads)} of {len(items)} results are ads",
         "detail": "Amazon's pick is " + ("an ad" if amazon and (amazon.get("sponsored") or amazon.get("advertised")) else "not an ad"),
         "action": "Placement is paid; don't trust position", "asins": [p["asin"] for p in ads]},
    ]
    if judged:
        off = [a for a, v in judged.items() if v is False]
        # matches first, in ShelfScout's order: best value, then well-reviewed by fair rating, then the rest
        rank = {p["asin"]: i for i, p in enumerate(sorted(rated, key=lambda p: (p["n_ratings"] < MIN_RATINGS, -p["fair_rating"], p["price"])))}
        best = getattr(insights, "_best", None)
        ok = sorted((a for a, v in judged.items() if v is True), key=lambda a: (a != best, rank.get(a, 1e9)))
        out.insert(0, {"priority": "MEDIUM", "insight": f"{len(off)} of {len(judged)} checked products aren't what you searched for",
                       "detail": "AI read each product's title, review features and quotes", "action": "Left out of best value",
                       "asins": off, "kind": "off_topic"})
        out.insert(0, {"priority": "HIGH", "insight": f"{len(ok)} match your search",
                       "detail": "Ranked by value: best value first, then by fair rating", "action": "These are the real options",
                       "asins": ok, "kind": "matches", "open": True})
    known = [p for p in items if "features" in p]
    if known:
        bad = [p for p in known if p["complaints"]]
        out.insert(1, {"priority": "HIGH" if bad else "LOW", "insight": f"{len(bad)} of {len(known)} top products have a major complaint",
                       "detail": f"an Amazon review feature with {MAJOR_MIN_MENTIONS}+ mentions and {round(MAJOR_NEG_SHARE * 100)}%+ negative",
                       "action": "Skip these unless the complaint doesn't matter to you" if bad else "No red flags in the top products",
                       "asins": [p["asin"] for p in bad], "notes": {p["asin"]: "; ".join(p["complaints"]) for p in bad}})
    plateau = _plateau(rated)
    if plateau:
        out.append({"priority": "LOW", "insight": f"Ratings stop improving above ${plateau:.0f}",
                    "detail": "average fair rating by price band", "action": "Paying more doesn't buy quality here", "asins": []})
    return out


def _plateau(rated):
    """Lowest price band edge above which the average fair rating no longer rises by 0.05★."""
    bands = [(0, 5), (5, 10), (10, 20), (20, 50), (50, 1e9)]
    avgs = [(lo, _avg([p["fair_rating"] for p in rated if lo <= p["price"] < hi])) for lo, hi in bands]
    avgs = [(lo, a) for lo, a in avgs if a is not None]
    for (lo, a), (_, b) in zip(avgs, avgs[1:]):
        if b - a < 0.05:
            return lo + (bands[[x[0] for x in bands].index(lo)][1] - lo)
    return None


def _feature_note(v, any_clean):
    if "features" not in v:
        return ""
    if v.get("complaints"):
        return f" Warning: {v['complaints'][0]} (every well-rated contender had a major complaint)."
    return f" No major complaints across {len(v['features'])} review features."


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _why(v, best, med):
    cheaper = f", ${med - v['price']:.2f} under the median price" if med and v["price"] < med else ""
    if v["asin"] == best["asin"]:
        return f"Best fair rating among well-reviewed products ({v['fair_rating']:.2f}★){cheaper}."
    return (f"{v['fair_rating']:.2f}★ fair rating vs {best['fair_rating']:.2f}★ for the top-rated, "
            f"for ${best['price'] - v['price']:.2f} less{cheaper}.")
