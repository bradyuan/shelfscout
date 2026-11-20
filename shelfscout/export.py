"""Excel export, premium layout, no data dropped.

Summary   KPI tiles, best value with its reason, insights (wrapped)
Shortlist what to buy: best value + top options (or the AI-filtered matches), with why
Products  every product, ranked, every column: an Excel Table (banded, filterable), column bands, collapsible
          detail groups, data bars / color scales, consistent number formats, frozen title column
"""
import io
import math
from datetime import date

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

FONT = "Aptos"
GREEN, DARK, LIGHT, RED_BG, RED, INK, MUTE, LINE = "1F6F4A", "17402C", "E6F4EC", "FBE3E0", "B42318", "1D2329", "6B7178", "E2E5E8"
URL = "https://www.amazon.com/dp/{}"
WRAP = Alignment(wrap_text=True, vertical="top")
TOP = Alignment(vertical="top")
NUM = Alignment(horizontal="right", vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
THIN = Border(bottom=Side(style="thin", color=LINE))
FMT = {"price": '"$"#,##0.00', "rating": "0.0", "fair": "0.00", "count": "#,##0", "pct": '0"%"'}


def F(**kw):
    return Font(name=FONT, **kw)


def fill(c):
    return PatternFill("solid", fgColor=c)


def _height(texts_widths, base=16):
    """Row height so wrapped text isn't cut off (openpyxl can't auto-fit)."""
    lines = max([math.ceil(len(str(t or "")) / max(w * 1.15, 1)) for t, w in texts_widths] + [1])
    return max(base, 15 * lines + 2)


def _notes(result):
    out = {}
    for i in result.get("insights", []):
        if i.get("kind") in ("matches", "off_topic"):
            mark = "✓ " if i["kind"] == "matches" else "✗ "
            out.update({a: mark + r for a, r in (i.get("notes") or {}).items()})
    return out


def _sheet_basics(ws):
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 110


def build(result: dict, products: list[dict], keyword: str | None) -> bytes:
    by = {p["asin"]: p for p in products}
    ranked = [{**by.get(p["asin"], {}), **p} for p in (result.get("ranked") or products)]
    ranked += [p for p in products if p["asin"] not in {x["asin"] for x in ranked}]  # nothing dropped
    notes = _notes(result)
    filtered = bool(notes)
    bv = result.get("best_value")
    s = result.get("stats", {})
    risky = [p for p in ranked if p.get("risk")]
    wb = Workbook()

    # ---------- Summary ----------
    ws = wb.active
    ws.title = "Summary"
    _sheet_basics(ws)
    ws.column_dimensions["A"].width = 2
    for col in range(2, 8):
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws["B2"] = "ShelfScout"
    ws["B2"].font = F(bold=True, size=11, color=GREEN)
    ws["B3"] = keyword or "search"
    ws["B3"].font = F(bold=True, size=22, color=INK)
    ws["B4"] = f"{date.today():%B %d, %Y} · {s.get('items', len(products))} products" + (" · filtered to what you searched for" if filtered else "")
    ws["B4"].font = F(color=MUTE)
    tiles = [("Items", s.get("items"), FMT["count"], False), ("Avg rating", s.get("avg_rating"), FMT["rating"], False),
             ("Median price", s.get("median_price"), FMT["price"], False), ("High risk", len(risky), FMT["count"], True),
             ("Ads", s.get("ads"), FMT["count"], False), ("Best value", bv["price"] if bv else None, FMT["price"], False)]
    for i, (label, val, fmt, bad) in enumerate(tiles, 2):
        v, lab = ws.cell(6, i, val if val is not None else "—"), ws.cell(7, i, label.upper())
        v.font, v.number_format = F(bold=True, size=24, color=RED if bad else GREEN), fmt
        lab.font = F(size=9, bold=True, color=MUTE)
        for c in (v, lab):
            c.fill, c.alignment = fill(RED_BG if bad else LIGHT), CENTER
            c.border = Border(left=Side(style="thick", color="FFFFFF"), right=Side(style="thick", color="FFFFFF"))
    ws.row_dimensions[6].height, ws.row_dimensions[7].height = 44, 20

    r = 9
    ws.cell(r, 2, "BEST VALUE").font = F(bold=True, size=9, color=GREEN)
    if bv:
        for rr in (r + 1, r + 2, r + 3):
            ws.merge_cells(start_row=rr, start_column=2, end_row=rr, end_column=7)
        t = ws.cell(r + 1, 2, bv.get("title") or bv["asin"])
        t.hyperlink, t.font, t.alignment = URL.format(bv["asin"]), F(bold=True, size=13, color="0563C1", underline="single"), WRAP
        ws.row_dimensions[r + 1].height = _height([(bv.get("title"), 105)], 20)
        line = f"${bv['price']:.2f}  ·  {bv.get('rating')}★  ·  {bv.get('n_ratings'):,} ratings" + (f"  ·  {notes[bv['asin']]}" if bv["asin"] in notes else "")
        ws.cell(r + 2, 2, line).font = F(color=INK)
        w = ws.cell(r + 3, 2, bv.get("why") or "")
        w.font, w.alignment = F(color=MUTE), WRAP
        ws.row_dimensions[r + 3].height = _height([(bv.get("why"), 105)])
    else:
        ws.cell(r + 1, 2, "No product here has enough ratings to trust.").font = F(color=MUTE)
    r += 5
    ws.cell(r, 2, "INSIGHTS").font = F(bold=True, size=9, color=GREEN)
    r += 1
    for i in result.get("insights", []):
        tag = ws.cell(r, 2, i["priority"])
        tag.font, tag.alignment = F(bold=True, size=9, color="FFFFFF"), CENTER
        tag.fill = fill({"HIGH": GREEN, "MEDIUM": "A16207"}.get(i["priority"], "8A9097"))
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=7)
        c = ws.cell(r, 3, f"{i['insight']}: {i['detail']}. {i['action']}.")
        c.font, c.alignment = F(color=INK), WRAP
        for col in range(2, 8):
            ws.cell(r, col).border = THIN
        ws.row_dimensions[r].height = _height([(c.value, 88)], 20)
        r += 1

    # ---------- Shortlist ----------
    ws = wb.create_sheet("Shortlist")
    _sheet_basics(ws)
    cols = [("#", 5), ("Product", 60), ("Price", 11), ("Rating", 9), ("Ratings", 11), ("Why it's here", 52)]
    for i, (name, width) in enumerate(cols, 1):
        c = ws.cell(1, i, name)
        c.font, c.fill, c.alignment = F(bold=True, color="FFFFFF"), fill(GREEN), Alignment(vertical="center", horizontal="right" if i in (3, 4, 5) else "left")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 26
    pool = [p for p in ranked if not p.get("risk") and (not filtered or notes.get(p["asin"], "").startswith("✓"))] or ranked
    short = ([bv] if bv else []) + [p for p in pool if not bv or p["asin"] != bv["asin"]][:9]
    for n, p in enumerate(short, 1):
        row = n + 1
        is_bv = bool(bv) and p["asin"] == bv["asin"]
        why = (p.get("why") if is_bv else None) or notes.get(p["asin"]) or "High fair rating, no major complaints"
        ws.cell(row, 1, n).alignment = TOP
        t = ws.cell(row, 2, ("💡 " if is_bv else "") + (p.get("title") or p["asin"]))
        t.hyperlink, t.font, t.alignment = URL.format(p["asin"]), F(color="0563C1", underline="single", bold=is_bv), WRAP
        for col, key, fmt in ((3, "price", FMT["price"]), (4, "rating", FMT["rating"]), (5, "n_ratings", FMT["count"])):
            c = ws.cell(row, col, p.get(key))
            c.number_format, c.alignment, c.font = fmt, NUM, F(color=INK)
        ws.cell(row, 6, why).alignment = WRAP
        ws.cell(row, 6).font = F(color=MUTE)
        for col in range(1, 7):
            ws.cell(row, col).border = THIN
            if is_bv:
                ws.cell(row, col).fill = fill(LIGHT)
        ws.row_dimensions[row].height = _height([(p.get("title"), 60), (why, 52)], 20)
    ws.freeze_panes = "C2"

    # ---------- Products: every column ----------
    ws = wb.create_sheet("Products")
    _sheet_basics(ws)
    cols = [  # (band, header, width, key, format, align)
        ("Product", "#", 5, "_rank", None, "r"), ("Product", "Product", 54, "title", None, "w"),
        ("Product", "Pick", 16, "_pick", None, "w"), ("Price & rating", "Price", 10, "price", FMT["price"], "r"),
        ("Price & rating", "Rating", 8, "rating", FMT["rating"], "r"), ("Price & rating", "Ratings", 10, "n_ratings", FMT["count"], "r"),
        ("Price & rating", "Fair rating", 10, "fair_rating", FMT["fair"], "r"), ("Reviews", "Risk", 34, "_risk", None, "w"),
        ("Reviews", "Major complaints", 30, "_complaints", None, "w"), ("Reviews", "% negative (features)", 12, "_neg", FMT["pct"], "r"),
        ("Reviews", "1-3★ %", 9, "low_star_pct", FMT["pct"], "r"), ("Placement", "Ad", 6, "_ad", None, "c"),
        ("Placement", "Organic rank", 10, "organic_rank", FMT["count"], "r"), ("Placement", "Bought / month", 12, "bought_past_month", None, "r"),
        ("Placement", "ASIN", 13, "asin", None, "l"), ("Placement", "Link", 40, "_url", None, "l"),
    ]
    if filtered:
        cols.insert(3, ("Product", "AI: matches your search?", 32, "_ai", None, "w"))
    detail = {"_complaints", "_neg", "low_star_pct", "organic_rank", "bought_past_month", "asin", "_url"}  # collapsible
    picks = {}
    for k, label in (("best_value", "💡 Best value"), ("best_price", "Best price"), ("amazon_pick", "Amazon's pick")):
        if result.get(k):
            picks.setdefault(result[k]["asin"], []).append(label)

    # row 1: column bands; row 2: headers
    band_colors = {"Product": DARK, "Price & rating": "245A3E", "Reviews": "6B2C25", "Placement": "3A4048"}
    start = 1
    for i in range(1, len(cols) + 2):
        if i == len(cols) + 1 or cols[i - 1][0] != cols[start - 1][0]:
            ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=i - 1)
            c = ws.cell(1, start, cols[start - 1][0].upper())
            c.font, c.fill, c.alignment = F(bold=True, size=9, color="FFFFFF"), fill(band_colors[cols[start - 1][0]]), Alignment(horizontal="center", vertical="center")
            for j in range(start, i):
                ws.cell(1, j).fill = fill(band_colors[cols[start - 1][0]])
            start = i
    ws.row_dimensions[1].height = 18
    for i, (_, name, width, key, _, align) in enumerate(cols, 1):
        ws.cell(2, i, name)
        ws.column_dimensions[get_column_letter(i)].width = width
        if key in detail:
            ws.column_dimensions[get_column_letter(i)].outlineLevel = 1
    ws.row_dimensions[2].height = 30

    for n, p in enumerate(ranked, 1):
        row = n + 2
        feats = p.get("features") or []
        vals = {"_rank": n, "_pick": ", ".join(picks.get(p["asin"], [])), "_risk": ("HIGH RISK · " + " · ".join(p["risk"])) if p.get("risk") else "",
                "_complaints": "; ".join(p.get("complaints") or []),
                "_neg": round(100 * p["neg_share"]) if p.get("neg_share") is not None else None,
                "_ad": "ad" if p.get("ad") or p.get("sponsored") or p.get("advertised") else "", "_url": URL.format(p["asin"]),
                "_ai": notes.get(p["asin"], "")}
        for i, (_, name, width, key, fmt, align) in enumerate(cols, 1):
            c = ws.cell(row, i, vals[key] if key in vals else p.get(key))
            c.font = F(color=INK, size=10)
            c.alignment = {"r": NUM, "w": WRAP, "c": Alignment(horizontal="center", vertical="top"), "l": TOP}[align]
            if fmt:
                c.number_format = fmt
            if key == "title":
                c.hyperlink, c.font = URL.format(p["asin"]), F(color="0563C1", underline="single", size=10, bold=bool(bv) and p["asin"] == bv["asin"])
            if key == "_risk" and c.value:
                c.font = F(color=RED, size=10, bold=True)
        ws.row_dimensions[row].height = _height([(p.get("title"), 54), (vals["_risk"], 34), (vals["_complaints"], 30), (vals["_ai"], 32)])

    last = max(len(ranked), 1) + 2   # an Excel table needs at least one (blank) data row
    ref = f"A2:{get_column_letter(len(cols))}{last}"
    tbl = Table(displayName="Products", ref=ref)
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleMedium7", showRowStripes=True)
    ws.add_table(tbl)
    col_of = {key: get_column_letter(i) for i, (_, _, _, key, _, _) in enumerate(cols, 1)}
    ws.conditional_formatting.add(f"{col_of['n_ratings']}3:{col_of['n_ratings']}{last}",
                                  DataBarRule(start_type="min", end_type="max", color="7FC79E", showValue=True))
    ws.conditional_formatting.add(f"{col_of['fair_rating']}3:{col_of['fair_rating']}{last}",
                                  ColorScaleRule(start_type="min", start_color="FFFFFF", end_type="max", end_color="7FC79E"))
    for key in ("_neg", "low_star_pct"):
        ws.conditional_formatting.add(f"{col_of[key]}3:{col_of[key]}{last}",
                                      ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF", end_type="num", end_value=50, end_color="F4A99A"))
    ws.sheet_properties.outlinePr.summaryRight = False
    ws.freeze_panes = "C3"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
