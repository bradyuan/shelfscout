"""ShelfScout backend: REST API for the extension + the factory view, one app.

    uv run uvicorn shelfscout.api:app --port 8100

POST /api/analyze   cards on the user's Amazon search page (+ per-product feature data once the extension
                    has read it) -> stats, best value / best price / Amazon's pick, insights, fetch_targets.
                    Every call is a run in the factory view.
POST /api/export    the same result + products -> Excel (Shortlist, Products, Insights), base64
POST /api/ai/test   {provider, key} -> picks a model the key can use and checks it answers
POST /api/ai/judge  {provider, key, query, notes, products} -> relevant / not + reason + verified quote per
                    product (LangGraph agent + RAG). Keys arrive per request and are never stored or logged.
"""
import base64

from pydantic import BaseModel, Field

import asyncio

from . import ai, bench_view, export, value  # noqa: F401  (bench_view registers /bench routes)
from .factory import app  # factory view routes live on the same app
from .trace import Run, StageFailed


class Page(BaseModel):
    url: str
    keyword: str | None = None
    products: list[dict]
    features: dict = Field(default_factory=dict)    # asin -> {features: [...], stars: {...}}
    fetch_log: list[dict] = Field(default_factory=list)  # the extension's page reads: asin, status, ms
    phase: str = "cards"                           # "cards" (instant), "features" (after page reads), "ai"
    relevance: dict = Field(default_factory=dict)   # asin -> True/False from AI mode
    feature_n: int = 10                            # limits (extension settings): product pages read for features
    ai_n: int = 100                                #   ... and products the off-topic filter checks
    judgments: list[dict] = Field(default_factory=list)


@app.post("/api/analyze")
def analyze(page: Page):
    run = Run("analyze", notes=f'{page.phase} · search "{page.keyword}" · {page.url}')
    try:
        items = run.stage("page_products", lambda _: page.products, [], allow_empty=True,
                          metric=lambda i, o: ("products on page", len(o)))
        clean = run.stage("cleaned", value.clean, items, allow_empty=True,
                          metric=lambda i, o: ("unique, priced", f"{len(o)}/{len(i)}"))
        if page.phase == "features":
            run.stage("feature_reads", lambda _: page.fetch_log, [],
                      metric=lambda i, o: ("read / cached / failed",
                                           "/".join(str(sum(r.get("status") == s for r in o)) for s in ("fetched", "cached", "failed"))))
            run.stage("features", lambda c: [
                {"asin": p["asin"], "title": (p.get("title") or "")[:60], "features": len(p["features"]),
                 "complaints": p["complaints"], "neg_share": p["neg_share"], "low_star_pct": p["low_star_pct"]}
                for p in value.with_features(c, page.features) if "features" in p], clean,
                metric=lambda i, o: ("major complaints", f"{sum(bool(r['complaints']) for r in o)} of {len(o)}"))
        rated = run.stage("fair_ratings", value.fair_ratings, clean, allow_empty=True,
                          metric=lambda i, o: ("rated", f"{len(o)}/{len(i)}"))
        result = value.analyze(page.products, page.features, page.relevance, page.feature_n, page.ai_n)
        why = {j["asin"]: j.get("reason") for j in page.judgments}
        for i in result["insights"]:
            if i.get("kind") in ("matches", "off_topic"):
                i["notes"] = {a: why.get(a) for a in i["asins"] if why.get(a)}
        # empty is a real answer (e.g. every match has under 50 ratings), not a failure
        run.stage("contenders", lambda _: result["contenders"], rated, allow_empty=True,
                  metric=lambda i, o: ("not beaten on price + rating", len(o)))
        run.stage("picks", lambda _: [{"pick": k, **{f: result[k].get(f) for f in ("asin", "title", "price", "rating", "n_ratings",
                                                                                  "fair_rating", "warn", "why", "complaints")}}
                                      for k in ("best_value", "best_price", "amazon_pick") if result[k]], [], allow_empty=True,
                  metric=lambda i, o: ("best value", f"${result['best_value']['price']:.2f}" if result["best_value"] else "none"))
        run.finish("ok")
    except StageFailed as e:
        return {"run_id": run.id, "status": "failed", "error": str(e)}
    return {"run_id": run.id, "status": "ok", **result}


class ExportReq(BaseModel):
    keyword: str | None = None
    result: dict
    products: list[dict]


@app.post("/api/export")
def export_xlsx(req: ExportReq):
    items = req.result.get("products") or value.clean(req.products)
    return {"xlsx_b64": base64.b64encode(export.build(req.result, items, req.keyword)).decode()}


class KeyReq(BaseModel):
    provider: str
    key: str


@app.post("/api/ai/test")
def ai_test(k: KeyReq):
    """Check a user's key. Recorded as a run (provider + outcome only, never the key)."""
    run = Run("key_test", notes=f"{k.provider} key test")
    try:
        out = ai.test_key(k.provider, k.key.strip())
    except Exception as e:
        out = {"ok": False, "error": _plain_error(e), "raw": str(e).splitlines()[0][:200].replace(k.key.strip(), "[key]")}
    run.stage("result", lambda _: [{"provider": k.provider, **{x: out.get(x) for x in ("ok", "model", "error", "raw")}}], [],
              metric=lambda i, o: ("result", out.get("model") or "failed"))
    run.finish("ok" if out.get("ok") else "failed")
    out.pop("raw", None)
    return out


class JudgeReq(BaseModel):
    provider: str
    key: str
    query: str
    notes: str = ""
    products: list[dict]


@app.post("/api/ai/judge")
async def ai_judge(r: JudgeReq):
    run = Run("ai", notes=f'AI mode ({r.provider}) · "{r.query}"' + (f' + "{r.notes}"' if r.notes else ""))
    try:
        run.stage("shortlist", lambda _: [{"asin": p["asin"], "title": (p.get("title") or "")[:80],
                                           "features": len(p.get("features") or [])} for p in r.products], [],
                  metric=lambda i, o: ("products to check", len(o)))
        out = await ai.judge(r.provider, r.key.strip(), r.query, r.notes, r.products)
        run.stage("rate_limits", lambda _: [{"backoffs": out.get("backoffs", 0), "final_limit": out.get("final_limit")}], [],
                  metric=lambda i, o: ("backoffs · final concurrency", f"{out.get('backoffs', 0)} · {out.get('final_limit') or 'no limit'}"))
        run.stage("evidence", lambda _: [{"asin": p["asin"], "docs": len(ai.evidence(p))} for p in r.products], r.products,
                  metric=lambda i, o: ("RAG documents", out["evidence_docs"]), one_to_one=True)
        js = run.stage("judgments", lambda _: out["judgments"], r.products,
                       metric=lambda i, o: ("relevant", f"{sum(j['relevant'] is True for j in o)}/{len(o)}"), one_to_one=True)
        run.stage("quote_check", lambda x: [{"asin": j["asin"], "quote_check": j["quote_check"], "quote": j["quote"]} for j in x], js,
                  metric=lambda i, o: ("verified / invented", f"{sum(j['quote_check'] == 'verified' for j in o)}/"
                                                             f"{sum(j['quote_check'] == 'invented' for j in o)}"), one_to_one=True)
        run.finish("ok")
    except StageFailed as e:
        return {"run_id": run.id, "status": "failed", "error": str(e)}
    except Exception as e:
        run.finish("failed")
        return {"run_id": run.id, "status": "failed", "error": _plain_error(e)}
    return {"run_id": run.id, "status": "ok", "model": out["model"], "judgments": out["judgments"]}


def _plain_error(e: Exception) -> str:
    s = str(e)
    if "401" in s or "403" in s or "HTTP Error 400" in s or "API key not valid" in s or "invalid x-api-key" in s or "Incorrect API key" in s:
        return "That key didn't work. Check you copied all of it, and that it's for the provider you picked."
    if "429" in s or "quota" in s.lower() or "rate" in s.lower():
        return "This key hit its rate limit or quota. Wait a minute and try again."
    return s.splitlines()[0][:200]
