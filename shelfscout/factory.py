"""Factory view: the whole pipeline at a glance, one station per stage.

    uv run uvicorn shelfscout.factory:app --port 8100   ->  http://localhost:8100
"""
import html
import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .trace import list_runs, load_rows

app = FastAPI(title="ShelfScout factory view")

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--bad:#c2410c;--bar:#4f6bed}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecece8;--mute:#9b9b94;--line:#33332f;--ok:#5cc489;--bad:#fb923c;--bar:#8aa0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}a{color:var(--bar)}
.sub{color:var(--mute);margin-bottom:20px}.status-ok{color:var(--ok)}.status-failed{color:var(--bad)}
.line{display:flex;align-items:stretch;gap:0;overflow-x:auto;padding-bottom:8px}
.st{flex:1 0 150px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;text-decoration:none;color:inherit}
.st:hover{border-color:var(--bar)}.st.bad{border-color:var(--bad);box-shadow:0 0 0 1px var(--bad)}
.arrow{align-self:center;color:var(--mute);padding:0 6px}
.nm{font-weight:600}.mv{font-size:22px;font-weight:650;margin:6px 0 0}.ml{color:var(--mute);font-size:12px}
.io{color:var(--mute);font-size:12px;margin-top:8px}.flag{color:var(--bad);font-size:12px;font-weight:600;margin-top:6px}
.bar{display:flex;height:26px;border-radius:6px;overflow:hidden;margin:20px 0 6px;border:1px solid var(--line)}
.seg{background:var(--bar);color:#fff;font-size:11px;display:flex;align-items:center;justify-content:center;white-space:nowrap;overflow:hidden;border-right:1px solid var(--bg)}
.seg:nth-child(even){opacity:.7}.eq{color:var(--mute);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:12px}
td,th{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;font-size:13px}
th{color:var(--mute);font-weight:600}td code{font-size:12px;word-break:break-all}
.runs{margin-top:28px}.runs a{display:inline-block;margin:0 10px 6px 0}
"""


def page(title, body):
    return HTMLResponse(f"<!doctype html><html><head><meta charset=utf-8>"
                        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
                        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
                        f"<body><main>{body}</main></body></html>")


def _run(run_id):
    for r in list_runs():
        if r["id"] == run_id:
            return r
    raise HTTPException(404, "run not found")


@app.get("/")
def index():
    return RedirectResponse("/runs")


@app.get("/runs")
def all_runs(kind: str | None = None):
    """Every run, newest first; filter by kind (analyze, eval, crawl, ...)."""
    runs = list_runs()
    kinds = sorted({r["name"] for r in runs})
    tabs = " · ".join([f"<a href='/runs'>{'<b>all</b>' if not kind else 'all'}</a>"] +
                      [f"<a href='/runs?kind={k}'>{'<b>' + k + '</b>' if k == kind else k}</a>" for k in kinds])
    shown = [r for r in runs if not kind or r["name"] == kind][:200]
    rows = "".join(
        f"<tr><td><a href='/runs/{r['id']}'>{r['id']}</a></td><td>{html.escape(r['name'])}</td>"
        f"<td class=status-{r['status']}>{r['status']}</td><td>{len(r['stages'])}</td>"
        f"<td class=status-failed>{sum(bool(s['flags']) for s in r['stages']) or ''}</td>"
        f"<td>{r.get('total_seconds', 0) * 1000:.0f} ms</td><td>{html.escape((r.get('notes') or '')[:110])}</td></tr>"
        for r in shown)
    body = (f"<h1>Runs</h1><div class=sub>{tabs}</div>"
            f"<table><tr><th>run</th><th>kind</th><th>status</th><th>stages</th><th>flags</th><th>time</th><th>notes</th></tr>"
            f"{rows or '<tr><td>(no runs yet)</td></tr>'}</table>")
    return page("Runs", body)


@app.get("/api/runs/{run_id}")
def run_json(run_id: str):
    """The run's manifest, for the extension's debug panel."""
    return _run(run_id)


@app.get("/runs/{run_id}")
def factory(run_id: str):
    r = _run(run_id)
    stations = []
    for s in r["stages"]:
        m = s["metric"] or {"label": "—", "value": "—"}
        flags = "".join(f"<div class=flag>⚠ {html.escape(f)}</div>" for f in s["flags"])
        stations.append(
            f"<a class='st{' bad' if s['flags'] else ''}' href='/runs/{run_id}/{s['file']}'>"
            f"<div class=nm>{s['idx']}. {html.escape(s['name'])}</div>"
            f"<div class=mv>{html.escape(str(m['value']))}</div><div class=ml>{html.escape(m['label'])}</div>"
            f"<div class=io>{s['n_in']} in → {s['n_out']} out · {s['seconds'] * 1000:.1f} ms</div>{flags}</a>")
    line = "<span class=arrow>→</span>".join(stations)

    stage_total = sum(s["seconds"] for s in r["stages"]) or 1e-9
    segs = "".join(
        f"<div class=seg style='flex:{max(s['seconds'], stage_total * 0.02)}' title='{html.escape(s['name'])}'>"
        f"{html.escape(s['name'])} {s['seconds'] / stage_total:.0%}</div>" for s in r["stages"])
    eq = " + ".join(f"{s['name']} {s['seconds'] * 1000:.1f}" for s in r["stages"])
    other = max(r["total_seconds"] - stage_total, 0)
    eq += f" + overhead {other * 1000:.1f} = <b>{r['total_seconds'] * 1000:.1f} ms total</b>"

    runs = "".join(f"<a href='/runs/{x['id']}'>{x['id']} <span class=status-{x['status']}>{x['status']}</span></a>"
                   for x in list_runs()[:15])
    body = (f"<h1>Factory view · run {html.escape(r['id'])}</h1>"
            f"<div class=sub>{html.escape(r['notes'] or r['name'])} · status "
            f"<b class=status-{r['status']}>{r['status']}</b> · click a station to see its rows</div>"
            f"<div class=line>{line}</div><div class=bar>{segs}</div><div class=eq>{eq}</div>"
            f"<div class=runs><b>Recent runs</b> · <a href='/runs'>all runs</a><br>{runs}</div>")
    return page(f"Run {r['id']}", body)


@app.get("/runs/{run_id}/{file}")
def rows(run_id: str, file: str):
    r = _run(run_id)
    stage = next((s for s in r["stages"] if s["file"] == file), None)
    if not stage:
        raise HTTPException(404, "stage not found")
    data = load_rows(run_id, file)
    cols = list(dict.fromkeys(k for row in data for k in row))

    def cell(v):
        t = v if isinstance(v, str) else json.dumps(v)
        return html.escape(t if len(t) < 300 else t[:300] + "…")

    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body_rows = "".join("<tr>" + "".join(f"<td><code>{cell(row.get(c, ''))}</code></td>" for c in cols) + "</tr>"
                        for row in data)
    err = f"<p class=flag>{html.escape(stage['error'])}</p>" if stage["error"] else ""
    body = (f"<p><a href='/runs/{run_id}'>← back to factory</a></p>"
            f"<h1>{stage['idx']}. {html.escape(stage['name'])}</h1>"
            f"<div class=sub>{stage['n_in']} in → {stage['n_out']} out · {stage['seconds'] * 1000:.1f} ms · "
            f"file <code>data/runs/{run_id}/{file}</code></div>{err}"
            f"<table><tr>{head}</tr>{body_rows or '<tr><td>(no rows)</td></tr>'}</table>")
    return page(f"{stage['name']} rows", body)
