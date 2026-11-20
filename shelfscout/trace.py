"""Stage recorder: every pipeline stage saves its output rows and one manifest entry.

data/runs/<run_id>/
  manifest.json        run status + one entry per stage (counts, seconds, metric, flags)
  01_<stage>.jsonl     that stage's output, one JSON row per line
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

DATA = Path(os.environ.get("SHELFSCOUT_DATA", Path(__file__).resolve().parent.parent / "data"))
RUNS = DATA / "runs"

Metric = Callable[[list, list], tuple[str, Any]]


class StageFailed(Exception):
    pass


class Run:
    def __init__(self, name: str, notes: str = ""):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.name, self.notes = name, notes
        self.dir = RUNS / self.id
        self.dir.mkdir(parents=True)
        self.stages: list[dict] = []
        self.status = "running"
        self._t0 = time.perf_counter()
        self._save()

    def stage(self, name: str, fn: Callable[[list], Iterable], items: list,
              metric: Metric | None = None, one_to_one: bool = False, allow_empty: bool = False) -> list:
        """Run fn(items), save its output rows, record counts/time/metric/flags."""
        idx = len(self.stages) + 1
        file = f"{idx:02d}_{name}.jsonl"
        t = time.perf_counter()
        out, err = [], None
        try:
            out = list(fn(items))
        except Exception as e:  # recorded, then re-raised as StageFailed
            err = f"{type(e).__name__}: {e}"
        seconds = time.perf_counter() - t

        with open(self.dir / file, "w") as f:
            for row in out:
                f.write(json.dumps(row, default=str) + "\n")

        flags = []
        if err:
            flags.append("error")
        elif not out:
            flags.append("empty" if allow_empty else "empty output")
        elif one_to_one and len(out) < len(items):
            flags.append(f"lost {len(items) - len(out)} items")

        m = None
        if metric and out and not err:
            label, value = metric(items, out)
            m = {"label": label, "value": value}

        self.stages.append(dict(idx=idx, name=name, file=file, n_in=len(items), n_out=len(out),
                                seconds=round(seconds, 4), metric=m, flags=flags, error=err))
        if err or "empty output" in flags:
            self.finish("failed")
            raise StageFailed(f"stage {name}: {err or 'empty output'}")
        self._save()
        return out

    def finish(self, status: str = "ok") -> None:
        self.status = status
        self._save()

    def _save(self) -> None:
        manifest = dict(id=self.id, name=self.name, notes=self.notes, status=self.status,
                        total_seconds=round(time.perf_counter() - self._t0, 4), stages=self.stages)
        (self.dir / "manifest.json").write_text(json.dumps(manifest, indent=2))


def list_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        mf = d / "manifest.json"
        if mf.exists():
            out.append(json.loads(mf.read_text()))
    return out


def load_rows(run_id: str, file: str, limit: int = 200) -> list[dict]:
    path = (RUNS / run_id / file).resolve()
    if RUNS.resolve() not in path.parents:
        raise FileNotFoundError(file)
    rows = []
    with open(path) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break
            rows.append(json.loads(line))
    return rows
