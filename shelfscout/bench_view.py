"""Benchmark page: latency of the off-topic filter, one product at a time vs all at once.

GET /bench   latest bench_latency run (started from the extension's settings page with the user's own key)
"""
import html

from .factory import app, page
from .trace import list_runs

CSS = """
main{max-width:1100px}
.big{font-size:30px;font-weight:750;margin:6px 0 2px}.big small{font-size:14px;color:var(--mute);font-weight:500}
.unk{color:var(--mute)}
"""


def _latency_html():
    """Latest sequential-vs-concurrent result, read from the bench_latency run's summary stage."""
    from .trace import load_rows
    run = next((r for r in list_runs() if r["name"] == "bench_latency"), None)
    if not run:
        return ("<h1>Latency benchmark</h1><div class=sub>Not run yet: ShelfScout settings → Developer → Run latency "
                "benchmark.</div>")
    st = next((s for s in run["stages"] if s["name"] == "summary"), None)
    if not st:
        done = [s["name"] for s in run["stages"]]
        return (f"<h1>Latency benchmark</h1><div class=sub>Running ({', '.join(done)} done) · "
                f"<a href='/runs/{run['id']}'>{run['id']}</a> · refresh to update</div>")
    rows = {r["mode"]: r for r in load_rows(run["id"], st["file"])}
    seq, con, sp = rows.get("sequential", {}), rows.get("concurrent", {}), rows.get("speedup", {}).get("x")
    mx = max(seq.get("wall_s") or 0, con.get("wall_s") or 0) or 1
    def bar(r, color):
        return (f"<div style='display:flex;align-items:center;gap:10px;margin:4px 0'><span style='width:90px'>{r.get('mode')}</span>"
                f"<span style='height:18px;width:{max(2, 600 * (r.get('wall_s') or 0) / mx):.0f}px;background:{color};border-radius:4px'></span>"
                f"<b>{r.get('wall_s')}s</b><span class=unk>p50 {r.get('p50_ms')} ms · p95 {r.get('p95_ms')} ms · "
                f"{r.get('throughput_per_s')}/s · backoffs {r.get('backoffs')} · errors {r.get('errors')} · fallbacks {r.get('fallbacks')}</span></div>")
    return (f"<h1>Latency benchmark</h1><div class=sub>{html.escape(run.get('notes') or '')} · <a href='/runs/{run['id']}'>{run['id']}</a></div>"
            f"<div class=big>{sp}× faster <small>all at once vs one at a time</small></div>"
            f"{bar(seq, '#c2410c')}{bar(con, '#2f7d4f')}<div style='height:22px'></div>")


@app.get("/bench")
def bench_index():
    return page("Latency benchmark", f"<style>{CSS}</style>{_latency_html()}")
