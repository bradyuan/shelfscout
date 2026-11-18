"""Excel export: a Shortlist sheet first (picks + good deals, with links and why), then every product
(clickable links, highlighted rows, filters), then the insights."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

GREEN, LIGHT_GREEN, YELLOW, GRAY = "C6EFCE", "E2F5E7", "FFF2CC", "EDEDED"
HEAD = PatternFill("solid", fgColor="1F6F4A")
LINK = Font(color="0563C1", underline="single")
URL = "https://www.amazon.com/dp/{}"


def _fill(c):
    return PatternFill("solid", fgColor=c)


def _sheet(ws, header, rows, widths, fills=None, links=None):
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEAD
    for i, r in enumerate(rows):
        ws.append(r)
        row = ws.max_row
        if fills and fills[i]:
            for c in ws[row]:
                c.fill = _fill(fills[i])
        if links and links[i]:
            cell = ws.cell(row, header.index("Link") + 1)
            cell.hyperlink, cell.font = links[i], LINK
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def build(result: dict, products: list[dict], keyword: str | None) -> bytes:
    fair = {p["asin"]: p.get("fair_rating") for p in result.get("contenders", [])}
    for k in ("best_value", "best_price", "amazon_pick"):
        if result.get(k):
            fair.setdefault(result[k]["asin"], result[k].get("fair_rating"))
    deals = next((set(i["asins"]) for i in result.get("insights", []) if "good deal" in i["insight"]), set())
    risky = next((set(i["asins"]) for i in result.get("insights", []) if "risky" in i["insight"]), set())
    complained = next((set(i["asins"]) for i in result.get("insights", []) if "major complaint" in i["insight"]), set())
    picks = {}
    for k, label in (("best_value", "Best value"), ("best_price", "Best price"), ("amazon_pick", "Amazon's pick")):
        if result.get(k):
            picks.setdefault(result[k]["asin"], []).append(label)

    def tag(a):
        t = list(picks.get(a, []))
        if a in deals:
            t.append("Good deal")
        if a in risky:
            t.append("Risky: few ratings")
        if a in complained:
            t.append("Major complaint")
        return ", ".join(t)

    wb = Workbook()
    ws = wb.active
    ws.title = "Shortlist"
    order = [result[k] for k in ("best_value", "best_price", "amazon_pick") if result.get(k)]
    seen = {p["asin"] for p in order}
    order += [p for p in products if p["asin"] in deals and p["asin"] not in seen]
    rows, fills, links = [], [], []
    for p in order:
        why = p.get("why") or ("Fair rating 4.3+ and 30%+ below the median price" if p["asin"] in deals else
                               "Cheapest on the page" if "Best price" in picks.get(p["asin"], []) else
                               "Badge or top placement on Amazon")
        rows.append([tag(p["asin"]), p.get("title"), p.get("price"), p.get("rating"), p.get("n_ratings"),
                     fair.get(p["asin"]), why, "Open on Amazon"])
        fills.append(GREEN if "Best value" in picks.get(p["asin"], []) else YELLOW if p["asin"] in risky or p.get("warn") else LIGHT_GREEN)
        links.append(URL.format(p["asin"]))
    _sheet(ws, ["Pick", "Product", "Price ($)", "Rating", "Ratings", "Fair rating", "Why", "Link"],
           rows, [22, 60, 10, 8, 10, 11, 50, 16], fills, links)
    for r in ws.iter_rows(min_row=2):
        r[1].alignment = r[6].alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.create_sheet("Products")
    rows, fills, links = [], [], []
    for p in products:
        a = p["asin"]
        rows.append([tag(a), p.get("title"), a, p.get("price"), p.get("rating"), p.get("n_ratings"), fair.get(a),
                     p.get("organic_rank"), "yes" if p.get("sponsored") or p.get("advertised") else "",
                     p.get("bought_past_month"), "; ".join(p.get("complaints") or []),
                     None if p.get("neg_share") is None else round(p["neg_share"] * 100),
                     p.get("low_star_pct"), "Open"])
        fills.append(GREEN if "Best value" in picks.get(a, []) else LIGHT_GREEN if a in deals else
                     YELLOW if a in risky else GRAY if p.get("sponsored") else None)
        links.append(URL.format(a))
    _sheet(ws, ["Pick", "Product", "ASIN", "Price ($)", "Rating", "Ratings", "Fair rating", "Organic rank", "Ad",
                "Bought/month", "Major complaints", "Feature mentions % negative", "1-3★ %", "Link"],
           rows, [22, 60, 13, 10, 8, 10, 11, 12, 6, 13, 36, 14, 8, 8], fills, links)

    ws = wb.create_sheet("Insights")
    rows = [[i["priority"], i["insight"], i["detail"], i["action"], len(i.get("asins", []))] for i in result.get("insights", [])]
    s = result.get("stats", {})
    _sheet(ws, ["Priority", "Insight", "Detail", "Action", "Products"], rows, [10, 34, 55, 45, 10],
           [{"HIGH": GREEN, "MEDIUM": YELLOW}.get(r[0], GRAY) for r in rows])
    ws.append([])
    ws.append(["Search", keyword or ""])
    ws.append(["Items", s.get("items"), "Avg rating", s.get("avg_rating"), "Median price", s.get("median_price")])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
