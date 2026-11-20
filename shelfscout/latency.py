"""Latency benchmark (one-off developer run): the off-topic filter one product at a time vs all at once, on the
same frozen products.

    GEMINI_API_KEY=... uv run python -m shelfscout.latency                 # all 30 searches, 10 products each
    OPENAI_API_KEY=... uv run python -m shelfscout.latency --provider openai --per-search 20

The key is read from the environment of your own terminal; it is never stored.

    sequential  limiter capped at 1: exactly one agent in flight, searches one after another
    concurrent  no cap: every product of every search at once; the adaptive limiter backs off on rate limits

A one-product warm-up runs first so the embedding model load doesn't count against either mode. Each mode gets
a fresh limiter (no shared state). Recorded as a "bench_latency" run: wall time, per-product p50/p95,
throughput, speedup, backoffs, errors, fallbacks. Keys come from the extension and live only in memory.
"""
import asyncio
import json
import statistics
import time

from . import ai, value
from .bench import BENCH
from .trace import Run

DEFAULT_SEARCHES = ("teardrop-tattoo", "sugar-free-gummy-bears", "rain-jacket-for-dogs")  # first picks; the rest by name


def _workload(slugs, per_search):
    out = []
    for slug in slugs:
        p = BENCH / f"{slug}.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        by = {c["asin"]: c for c in s["cards"]}
        prods = [{**by[a], "features": s["features"].get(a, {}).get("features") or []}
                 for a in value.ai_shortlist(s["cards"], per_search) if a in by]
        out.append({"slug": slug, "query": s["query"], "products": prods})
    return out


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


async def _mode(name, cfg, model, work):
    lim = ai.AdaptiveLimiter(max_limit=1 if name == "sequential" else None)
    t0 = time.perf_counter()
    if name == "sequential":
        outs = [await ai.judge(cfg["provider"], cfg["key"], w["query"], "", w["products"], model=model, limiter=lim) for w in work]
    else:
        outs = await asyncio.gather(*(ai.judge(cfg["provider"], cfg["key"], w["query"], "", w["products"], model=model, limiter=lim)
                                      for w in work))
    wall = time.perf_counter() - t0
    rows = [{"mode": name, "search": w["query"], "asin": j["asin"], "ms": j.get("ms"), "relevant": j["relevant"],
             "error": j["reason"] if j["reason"].startswith("error:") else None, "fallback": j.get("fallback", False)}
            for w, o in zip(work, outs) for j in o["judgments"]]
    ms = [r["ms"] for r in rows if r["ms"] is not None]
    summary = {"mode": name, "products": len(rows), "wall_s": round(wall, 1),
               "throughput_per_s": round(len(rows) / wall, 2) if wall else None,
               "p50_ms": _pct(ms, .5), "p95_ms": _pct(ms, .95), "mean_ms": round(statistics.mean(ms)) if ms else None,
               "backoffs": lim.backoffs, "errors": sum(bool(r["error"]) for r in rows),
               "fallbacks": sum(r["fallback"] for r in rows)}
    return summary, rows


async def run(cfg: dict, searches: int = 3, per_search: int = 20, holder: list | None = None) -> str:
    model = ai.pick_model(cfg["provider"], cfg["key"])
    slugs = list(DEFAULT_SEARCHES)[:searches]
    if len(slugs) < searches:
        extra = [p.stem for p in sorted(BENCH.glob("*.json")) if p.stem not in ("labels", "ai_latest", "jury_labels")]
        slugs += [s for s in extra if s not in slugs][:searches - len(slugs)]
    work = _workload(slugs, per_search)
    n = sum(len(w["products"]) for w in work)
    r = Run("bench_latency", notes=f"{cfg['provider']}:{model} · {len(work)} searches × up to {per_search} products = {n} checks per mode")
    if holder is not None:
        holder.append(r.id)
    r.stage("workload", lambda _: [{"search": w["query"], "products": len(w["products"])} for w in work], [],
            metric=lambda i, o: ("checks per mode", n))
    warm = work[0]["products"][:1]
    await ai.judge(cfg["provider"], cfg["key"], work[0]["query"], "", warm, model=model, limiter=ai.AdaptiveLimiter())
    r.stage("warmup", lambda _: [{"note": "embedding model loaded, one check done (not timed)"}], [],
            metric=lambda i, o: ("untimed checks", len(warm)))
    seq, seq_rows = await _mode("sequential", cfg, model, work)
    r.stage("sequential", lambda _: seq_rows, [], metric=lambda i, o: ("wall time", f"{seq['wall_s']}s"))
    con, con_rows = await _mode("concurrent", cfg, model, work)
    r.stage("concurrent", lambda _: con_rows, [], metric=lambda i, o: ("wall time", f"{con['wall_s']}s"))
    speed = round(seq["wall_s"] / con["wall_s"], 1) if con["wall_s"] else None
    r.stage("summary", lambda _: [seq, con, {"mode": "speedup", "x": speed}], [],
            metric=lambda i, o: ("speedup", f"{speed}×"))
    r.finish("ok")
    return r.id


if __name__ == "__main__":
    import argparse
    import os
    ap = argparse.ArgumentParser(description="Sequential vs concurrent latency of the off-topic filter")
    ap.add_argument("--provider", default="gemini", choices=["gemini", "openai", "anthropic"])
    ap.add_argument("--searches", type=int, default=30)
    ap.add_argument("--per-search", type=int, default=10)
    a = ap.parse_args()
    env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[a.provider]
    key = os.environ.get(env)
    if not key:
        raise SystemExit(f"Set {env} in this terminal first, e.g.  {env}=... uv run python -m shelfscout.latency")
    rid = asyncio.run(run({"provider": a.provider, "key": key}, a.searches, a.per_search))
    from .trace import load_rows, list_runs
    st = next(s for s in next(r for r in list_runs() if r["id"] == rid)["stages"] if s["name"] == "summary")
    rows = {r["mode"]: r for r in load_rows(rid, st["file"])}
    print(f"\n{'mode':<12}{'checks':>7}{'wall s':>9}{'p50 ms':>9}{'p95 ms':>9}{'per s':>8}{'backoffs':>10}{'errors':>8}")
    for m in ("sequential", "concurrent"):
        r = rows[m]
        print(f"{m:<12}{r['products']:>7}{r['wall_s']:>9}{r['p50_ms']:>9}{r['p95_ms']:>9}{r['throughput_per_s']:>8}{r['backoffs']:>10}{r['errors']:>8}")
    print(f"\nspeedup: {rows['speedup']['x']}x   ·   run {rid}   ·   http://localhost:8100/runs/{rid}")
