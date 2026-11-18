"""Relevance benchmark: AI mode on the frozen searches, labeled by an LLM jury. No human labels.

For each frozen search: run AI mode (ai.judge) on its shortlist, take the top picks with and without AI mode,
and have every judge (the user's other providers; a provider never grades itself) label each pick relevant or
not in one batched call. If two judges disagree, a stronger model from the first judge's provider breaks the
tie. Everything is cached under data/bench/, so reruns cost nothing. Keys live only in memory for the run.

Outputs read by the bench page:
  data/bench/ai_latest.json      {slug: {model, relevance, judgments}}
  data/bench/jury_labels.json    {slug: {asin: {final, votes: {model: bool}, tiebreak}}}
"""
import asyncio
import json

from pydantic import BaseModel, Field

from . import ai, value
from .bench import BENCH
from .trace import Run

AI_LATEST = BENCH / "ai_latest.json"
JURY = BENCH / "jury_labels.json"
CACHE = BENCH / "cache"
TOP_N = 5
BENCH_SHORTLIST = 10  # AI mode checks this many per search in the benchmark (free-tier rate limits)


class Verdict(BaseModel):
    n: int = Field(description="the product number from the list")
    relevant: bool
    reason: str = Field(description="a few words")


class Verdicts(BaseModel):
    verdicts: list[Verdict]


JURY_PROMPT = """You are judging Amazon search results. A shopper searched for: "{query}"
For each product below, say whether it is what the shopper asked for. Wrong product type, or a wrong size,
model, variant or attribute the search specifies, means NOT relevant. Sharing a word is not enough
(a barbed-wire tattoo is not a teardrop tattoo; regular pasta is not gluten-free pasta).

{items}"""


def _read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _cached(name):
    p = CACHE / f"{name}.json"
    return p, _read(p, None)


def _item_text(i, p, features):
    f = features.get(p["asin"], {}).get("features") or []
    feats = "; ".join(x["name"] for x in f)[:200]
    return f"{i}. {p.get('title') or p['asin']}" + (f" (review topics: {feats})" if feats else "")


async def _jury_call(provider, key, model, query, products, features):
    llm = ai.chat_model(provider, key, model).with_structured_output(Verdicts)
    items = "\n".join(_item_text(i, p, features) for i, p in enumerate(products, 1))
    lim = ai.limiter_for(provider, key)
    for attempt in range(6):
        async with lim:
            try:
                out: Verdicts = await llm.ainvoke(JURY_PROMPT.format(query=query, items=items))
                await lim.success()
                return {products[v.n - 1]["asin"]: v.relevant for v in out.verdicts if 1 <= v.n <= len(products)}
            except Exception as e:
                if not ai.is_rate_limited(e) or attempt == 5:
                    raise
                err = e
        await lim.rate_limited(err)


async def run(ai_cfg: dict, judges: list[dict], run_id_holder: list | None = None) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    judges = [j for j in judges if j["provider"] != ai_cfg["provider"]]
    ai_model = ai.pick_model(ai_cfg["provider"], ai_cfg["key"])
    if judges:  # independent jury: other companies only
        jm = [(j["provider"], j["key"], ai.pick_model(j["provider"], j["key"])) for j in judges]
        tb = (jm[0][0], jm[0][1], ai.pick_model(jm[0][0], jm[0][1], "strong")) if len(jm) >= 2 else None
        kind = "independent jury"
    else:  # only one provider: its stronger model judges (same family, weaker test)
        jm = [(ai_cfg["provider"], ai_cfg["key"], ai.pick_model(ai_cfg["provider"], ai_cfg["key"], "strong"))]
        tb = None
        kind = "same-provider jury (weaker test)"
    r = Run("bench_ai", notes=f"AI mode {ai_cfg['provider']}:{ai_model} · {kind}: " + ", ".join(f"{p}:{m}" for p, _, m in jm)
            + (f" · tiebreak {tb[2]}" if tb else ""))
    if run_id_holder is not None:
        run_id_holder.append(r.id)
    snaps = [json.loads(p.read_text()) | {"slug": p.stem} for p in sorted(BENCH.glob("*.json"))
             if p.stem not in ("labels", "ai_latest", "jury_labels")]
    ai_latest, jury = _read(AI_LATEST, {}), _read(JURY, {})

    async def ai_mode(s):
        path, hit = _cached(f"ai_{ai_cfg['provider']}_{ai_model}_{s['slug']}")
        if hit:
            return hit
        by = {c["asin"]: c for c in s["cards"]}
        prods = [{**by[a], "features": s["features"].get(a, {}).get("features") or []}
                 for a in value.ai_shortlist(s["cards"], BENCH_SHORTLIST) if a in by]
        out = await ai.judge(ai_cfg["provider"], ai_cfg["key"], s["query"], "", prods, model=ai_model)
        if not any(j["reason"].startswith("error:") for j in out["judgments"]):
            path.write_text(json.dumps(out))
        return out

    ai_rows = []
    outs = await asyncio.gather(*(ai_mode(s) for s in snaps))  # all searches at once
    for s, out in zip(snaps, outs):
        rel = {j["asin"]: j["relevant"] for j in out["judgments"] if j["relevant"] is not None}
        ai_latest[s["slug"]] = {"model": out["model"], "relevance": rel, "judgments": out["judgments"]}
        ai_rows.append({"search": s["query"], "checked": len(out["judgments"]), "relevant": sum(rel.values()),
                        "errors": sum(j["reason"].startswith("error:") for j in out["judgments"]),
                        "invented_quotes": sum(j["quote_check"] == "invented" for j in out["judgments"])})
    AI_LATEST.write_text(json.dumps(ai_latest, indent=1))
    r.stage("ai_mode", lambda _: ai_rows, snaps, metric=lambda i, o: ("products judged relevant",
            f"{sum(x['relevant'] for x in o)}/{sum(x['checked'] for x in o)}"), one_to_one=True)

    async def label_search(s):
        rel = ai_latest[s["slug"]]["relevance"]
        picks = {p["asin"]: p for p in value.top_picks(s["cards"], s["features"], TOP_N)}
        picks.update({p["asin"]: p for p in value.top_picks(s["cards"], s["features"], TOP_N, relevance=rel)})
        prods = list(picks.values())
        labels = jury.setdefault(s["slug"], {})
        async def one_judge(prov, key, model):
            path, hit = _cached(f"jury_{prov}_{model}_{s['slug']}")
            hit = hit or {}
            todo = [p for p in prods if p["asin"] not in hit]
            if todo:
                hit.update(await _jury_call(prov, key, model, s["query"], todo, s["features"]))
                path.write_text(json.dumps(hit))
            return prov, model, hit

        for prov, model, hit in await asyncio.gather(*(one_judge(*j) for j in jm)):
            for a in picks:
                if a in hit:
                    labels.setdefault(a, {"votes": {}})["votes"][f"{prov}:{model}"] = hit[a]
        split = [p for p in prods if len(set(labels.get(p["asin"], {}).get("votes", {}).values())) > 1]
        if split and tb:
            path, hit = _cached(f"tiebreak_{tb[0]}_{tb[2]}_{s['slug']}")
            hit = hit or {}
            todo = [p for p in split if p["asin"] not in hit]
            if todo:
                hit.update(await _jury_call(tb[0], tb[1], tb[2], s["query"], todo, s["features"]))
                path.write_text(json.dumps(hit))
            for p in split:
                if p["asin"] in hit:
                    labels[p["asin"]]["tiebreak"] = {f"{tb[0]}:{tb[2]}": hit[p["asin"]]}
        for a, lab in labels.items():
            votes = list(lab["votes"].values())
            if len(set(votes)) == 1:
                lab["final"] = votes[0]
            elif lab.get("tiebreak"):
                lab["final"] = list(lab["tiebreak"].values())[0]
            else:
                lab["final"] = None
        return {"search": s["query"], "picks_labeled": len(picks), "split": len(split)}

    jury_rows = await asyncio.gather(*(label_search(s) for s in snaps))
    JURY.write_text(json.dumps(jury, indent=1))
    r.stage("jury", lambda _: jury_rows, snaps, metric=lambda i, o: ("judges split on",
            f"{sum(x['split'] for x in o)} of {sum(x['picks_labeled'] for x in o)} picks"), one_to_one=True)
    sc = scores()
    r.stage("scores", lambda _: [sc], [], metric=lambda i, o: ("relevant: formula → AI",
            f"{sc['formula_pct']}% → {sc['ai_pct']}%"))
    r.finish("ok")
    return r.id


def scores() -> dict:
    """% of top picks the jury calls relevant, formula vs AI mode."""
    ai_latest, jury = _read(AI_LATEST, {}), _read(JURY, {})
    tot = {"formula": [0, 0], "ai": [0, 0]}
    for p in sorted(BENCH.glob("*.json")):
        if p.stem in ("labels", "ai_latest", "jury_labels"):
            continue
        s = json.loads(p.read_text())
        labs = jury.get(p.stem, {})
        rel = (ai_latest.get(p.stem) or {}).get("relevance")
        for name, picks in (("formula", value.top_picks(s["cards"], s["features"], TOP_N)),
                            ("ai", value.top_picks(s["cards"], s["features"], TOP_N, relevance=rel) if rel else [])):
            for x in picks:
                f = labs.get(x["asin"], {}).get("final")
                if f is not None:
                    tot[name][0] += f
                    tot[name][1] += 1
    pct = lambda a: round(100 * a[0] / a[1]) if a[1] else None
    return {"formula_pct": pct(tot["formula"]), "ai_pct": pct(tot["ai"]),
            "formula_n": tot["formula"][1], "ai_n": tot["ai"][1]}
