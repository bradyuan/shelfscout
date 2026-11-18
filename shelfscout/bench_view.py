"""Relevance benchmark page: ShelfScout's top picks per frozen search, formula vs AI mode, labeled by an LLM jury.

GET /bench   search | formula top 5 | % relevant | AI mode top 5 | % relevant   (no human labeling)

Picks are recomputed from the frozen cards + feature data on every load, so a ranking change is rescored
against the same jury labels. Labels and AI-mode results come from jury.run (started from the extension's
settings page with the user's own keys).
"""
import html
import json

from . import value
from .bench import BENCH
from .factory import app, page
from .trace import list_runs

TOP_N = 5

CSS = """
main{max-width:1400px}
.big{font-size:30px;font-weight:750;margin:6px 0 2px}.big small{font-size:14px;color:var(--mute);font-weight:500}
td.s{width:170px;font-weight:650}td.s small{display:block;color:var(--mute);font-weight:400;font-size:12px}
td.pct{width:80px;text-align:center;font-size:17px;font-weight:700;white-space:nowrap}td.pct small{display:block;font-size:11px;color:var(--mute);font-weight:400}
.pk{display:grid;grid-template-columns:18px minmax(0,1fr) 112px;gap:6px;align-items:start;padding:3px 0;border-bottom:1px dashed var(--line);font-size:12.5px}
.pk:last-child{border-bottom:0}.rk{color:var(--mute)}
.pk a{text-decoration:none;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
.pk .why{grid-column:2/4;color:var(--mute);font-size:11.5px;margin-top:-2px}
.chip{font-size:11px;font-weight:700;border-radius:999px;padding:1px 8px;text-align:center;white-space:nowrap}
.yes{background:rgba(47,125,79,.15);color:var(--ok)}.no{background:rgba(194,65,12,.13);color:var(--bad)}.unk{color:var(--mute);border:1px solid var(--line)}
.split{font-size:10px;color:var(--mute);display:block;font-weight:500}
th.ai,td.ai{border-left:2px solid var(--line)}
"""


def _read(name, default):
    p = BENCH / name
    return json.loads(p.read_text()) if p.exists() else default


def _snaps():
    return [json.loads(p.read_text()) | {"slug": p.stem} for p in sorted(BENCH.glob("*.json"))
            if p.stem not in ("labels", "ai_latest", "jury_labels")]


def _chip(lab):
    if not lab or lab.get("final") is None:
        return "<span class='chip unk'>not judged</span>"
    split = len(set(lab.get("votes", {}).values())) > 1
    return (f"<span class='chip {'yes' if lab['final'] else 'no'}'>{'Relevant' if lab['final'] else 'Not relevant'}"
            f"{'<span class=split>judges split · tie-broken</span>' if split else ''}</span>")


def _cell(picks, labs, judgments=None):
    rows = []
    for i, p in enumerate(picks, 1):
        j = (judgments or {}).get(p["asin"])
        rows.append(f"<div class=pk><span class=rk>{i}</span>"
                    f"<a href='https://www.amazon.com/dp/{p['asin']}' target=_blank title='{html.escape(p.get('title') or '')}'>"
                    f"{html.escape(p.get('title') or p['asin'])}</a>{_chip(labs.get(p['asin']))}"
                    + (f"<span class=why>AI: {html.escape(j['reason'])}</span>" if j else "") + "</div>")
    return "".join(rows) or "<span class=unk>—</span>"


def _pct(picks, labs):
    judged = [labs[p["asin"]]["final"] for p in picks if labs.get(p["asin"], {}).get("final") is not None]
    if not judged:
        return "—"
    return f"{sum(judged)}/{len(judged)}<small>{round(100 * sum(judged) / len(judged))}%</small>"


@app.get("/bench")
def bench_index():
    ai_latest, jury = _read("ai_latest.json", {}), _read("jury_labels.json", {})
    tot = {"f": [0, 0], "a": [0, 0]}
    rows = []
    for s in _snaps():
        labs = jury.get(s["slug"], {})
        al = ai_latest.get(s["slug"])
        f_picks = value.top_picks(s["cards"], s["features"], TOP_N)
        a_picks = value.top_picks(s["cards"], s["features"], TOP_N, relevance=al["relevance"]) if al else []
        for k, picks in (("f", f_picks), ("a", a_picks)):
            for p in picks:
                v = labs.get(p["asin"], {}).get("final")
                if v is not None:
                    tot[k][0] += v
                    tot[k][1] += 1
        judgments = {j["asin"]: j for j in al["judgments"]} if al else {}
        rows.append(f"<tr><td class=s><a href='{html.escape(s['url'])}' target=_blank>{html.escape(s['query'])}</a>"
                    f"<small>{s['pattern'].replace('_', ' ')}</small></td>"
                    f"<td>{_cell(f_picks, labs)}</td><td class=pct>{_pct(f_picks, labs)}</td>"
                    f"<td class=ai>{_cell(a_picks, labs, judgments) if al else '<span class=unk>not run yet</span>'}</td>"
                    f"<td class=pct>{_pct(a_picks, labs) if al else '—'}</td></tr>")
    pct = lambda t: f"{round(100 * t[0] / t[1])}%" if t[1] else "—"
    last = next((r for r in list_runs() if r["name"] == "bench_ai"), None)
    split = sum(len(set(l.get("votes", {}).values())) > 1 for q in jury.values() for l in q.values())
    labeled = sum(1 for q in jury.values() for l in q.values() if l.get("final") is not None)
    weak = bool(last and "same-provider" in (last.get("notes") or ""))
    meta = (f"last run <a href='/runs/{last['id']}'>{last['id']}</a> · {html.escape(last.get('notes') or '')}"
            + (" · <b class=status-failed>judged by the same provider as AI mode: treat as a rough check</b>" if weak else "") if last
            else "not run yet: open ShelfScout settings → Developer → Run benchmark")
    body = (f"<style>{CSS}</style><h1>Relevance benchmark</h1>"
            f"<div class=sub>Is each of ShelfScout's top {TOP_N} picks what the search asked for? Labels come from an "
            f"LLM jury (a provider never grades itself). Picks are recomputed from frozen Amazon pages.</div>"
            f"<div class=big>{pct(tot['f'])} → {pct(tot['a'])} <small>relevant · formula → AI mode · "
            f"{tot['f'][1]} + {tot['a'][1]} picks judged</small></div>"
            f"<div class=sub>{labeled} picks labeled · judges split on {split} (tie-broken) · {meta}</div>"
            f"<table><tr><th>search</th><th>formula: top {TOP_N}</th><th>relevant</th>"
            f"<th class=ai>AI mode: top {TOP_N}</th><th>relevant</th></tr>{''.join(rows)}</table>")
    return page("Relevance benchmark", body)
