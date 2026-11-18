"""Plumbing only: jury.run with faked LLMs: AI mode results, jury votes, tie-break, scores, caching."""
import asyncio
import json
import shutil
from pathlib import Path

from shelfscout import ai, jury

REAL = Path(__file__).resolve().parent / "fixtures" / "bench"  # two frozen searches


def test_jury_run(tmp_path, monkeypatch):
    for slug in ("teardrop-tattoo", "gluten-free-pasta"):
        shutil.copy(REAL / f"{slug}.json", tmp_path / f"{slug}.json")
    for name, val in (("BENCH", tmp_path), ("AI_LATEST", tmp_path / "ai_latest.json"),
                      ("JURY", tmp_path / "jury_labels.json"), ("CACHE", tmp_path / "cache")):
        monkeypatch.setattr(jury, name, val)
    import shelfscout.trace as t
    monkeypatch.setattr(t, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(ai, "pick_model", lambda p, k, tier="fast": f"{p}-{tier}")
    term = {"teardrop tattoo": ("teardrop", "tear drop"), "gluten free pasta": ("gluten free", "gluten-free")}

    async def fake_judge(provider, key, query, notes, products, model=None):
        return {"model": f"{provider}:{model}", "evidence_docs": 0, "judgments": [
            {"asin": p["asin"], "title": p["title"], "relevant": any(w in p["title"].lower() for w in term[query]),
             "reason": "fake", "quote": None, "quote_check": "none", "searches": []} for p in products]}
    monkeypatch.setattr(ai, "judge", fake_judge)
    calls = []

    async def fake_call(provider, key, model, query, products, features):
        calls.append(model)
        # judge A is strict, judge B accepts everything -> splits on off-topic picks; tie-break is strict
        return {p["asin"]: (True if model == "anthropic-fast" else any(w in p["title"].lower() for w in term[query]))
                for p in products}
    monkeypatch.setattr(jury, "_jury_call", fake_call)

    cfg = {"provider": "gemini", "key": "k"}
    judges = [{"provider": "openai", "key": "k"}, {"provider": "anthropic", "key": "k"}]
    asyncio.run(jury.run(cfg, judges))
    s = jury.scores()
    assert s["ai_pct"] > s["formula_pct"]                       # AI mode filters off-topic picks
    assert "openai-strong" in calls                              # splits went to the tie-breaker
    labels = json.loads((tmp_path / "jury_labels.json").read_text())
    assert all(l["final"] is not None for q in labels.values() for l in q.values())
    n = len(calls)
    asyncio.run(jury.run(cfg, judges))
    assert len(calls) == n                                       # second run is fully cached
