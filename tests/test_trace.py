"""Plumbing only: the recorder writes what it claims to. Not evidence the app works."""
import json

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    import shelfscout.trace as t
    monkeypatch.setattr(t, "RUNS", tmp_path / "runs")
    yield


def test_healthy_run_writes_manifest_and_rows():
    from shelfscout import dummy_pipeline
    run = dummy_pipeline.main()
    mf = json.loads((run.dir / "manifest.json").read_text())
    assert mf["status"] == "ok"
    assert [s["name"] for s in mf["stages"]] == ["crawl", "chunk", "embed", "retrieve", "answer"]
    assert all(not s["flags"] for s in mf["stages"])
    assert (run.dir / "02_chunk.jsonl").read_text().count("\n") == 8


def test_broken_stage_is_flagged_and_stops_run():
    from shelfscout import dummy_pipeline
    run = dummy_pipeline.main(broken="chunk")
    mf = json.loads((run.dir / "manifest.json").read_text())
    assert mf["status"] == "failed"
    assert mf["stages"][-1]["name"] == "chunk"
    assert "empty output" in mf["stages"][-1]["flags"]
