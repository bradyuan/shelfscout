"""Plumbing only: latency.run times sequential vs concurrent with a fake 50 ms agent."""
import asyncio

from shelfscout import ai, latency
from pathlib import Path


def test_latency_speedup(tmp_path, monkeypatch):
    import shelfscout.trace as t
    monkeypatch.setattr(t, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(latency, "BENCH", Path(__file__).resolve().parent / "fixtures" / "bench")
    monkeypatch.setattr(ai, "pick_model", lambda p, k, tier="fast": "fake")
    monkeypatch.setattr(ai, "chat_model", lambda p, k, m: None)
    monkeypatch.setattr(ai, "build_store", lambda products: None)

    async def fake_one(llm, store, p, query, notes):
        await asyncio.sleep(0.05)
        return {"asin": p["asin"], "title": "", "relevant": True, "reason": "ok", "quote": None,
                "quote_check": "none", "searches": [], "fallback": False}
    monkeypatch.setattr(ai, "judge_one", fake_one)
    run_id = asyncio.run(latency.run({"provider": "gemini", "key": "k"}, searches=2, per_search=10))
    rows = {r["mode"]: r for r in t.load_rows(run_id, "05_summary.jsonl")}
    assert rows["sequential"]["products"] == rows["concurrent"]["products"] == 20
    assert rows["sequential"]["wall_s"] >= 0.9 and rows["speedup"]["x"] >= 5   # 20 × 50 ms vs ~50 ms
