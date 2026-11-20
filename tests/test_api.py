"""Plumbing only: /api/analyze records a run and returns the three picks."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import shelfscout.trace as t
    monkeypatch.setattr(t, "RUNS", tmp_path / "runs")
    import shelfscout.api as api
    return TestClient(api.app)


CARDS = [
    {"asin": "AD", "position": 1, "sponsored": True, "price": 9.0, "rating": 4.5, "n_ratings": 900, "badges": []},
    {"asin": "CHEAP", "position": 2, "price": 2.0, "rating": 3.0, "n_ratings": 4, "badges": []},
    {"asin": "VALUE", "position": 3, "price": 5.0, "rating": 4.6, "n_ratings": 800, "badges": []},
    {"asin": "PRICEY", "position": 4, "price": 15.0, "rating": 4.7, "n_ratings": 3000, "badges": ["Overall Pick"]},
    {"asin": "VALUE", "position": 5, "price": 5.0, "rating": 4.6, "n_ratings": 800, "badges": []},  # repeat
    {"asin": "FREE", "position": 6, "price": 0.0, "rating": 4.0, "n_ratings": 10, "badges": []},
]


def test_analyze_picks(client):
    r = client.post("/api/analyze", json={"url": "u", "keyword": "k", "products": CARDS}).json()
    assert r["status"] == "ok"
    assert r["stats"]["items"] == 4                      # repeat and $0 dropped
    assert r["best_value"]["asin"] == "VALUE"
    assert r["best_price"]["asin"] == "CHEAP" and r["best_price"]["warn"] == "few ratings"
    assert r["amazon_pick"]["asin"] == "PRICEY" and r["amazon_pick"]["pick_reason"] == "badge"
    assert client.get(f"/runs/{r['run_id']}").status_code == 200


def test_export_xlsx(client):
    import base64, io
    from openpyxl import load_workbook
    r = client.post("/api/analyze", json={"url": "u", "keyword": "k", "products": CARDS}).json()
    x = client.post("/api/export", json={"keyword": "k", "result": r, "products": CARDS}).json()
    wb = load_workbook(io.BytesIO(base64.b64decode(x["xlsx_b64"])))
    assert wb.sheetnames == ["Summary", "Shortlist", "Products"]
    sl = wb["Shortlist"]
    assert sl["B2"].value.startswith("💡") and sl["B2"].hyperlink.target.endswith("/dp/VALUE")
    assert sl["C2"].number_format == '"$"#,##0.00'
    pr = wb["Products"]
    assert pr.max_row == 6 and pr.max_column == 16      # bands + headers + 4 products; every column kept
    assert "Products" in pr.tables and pr["B3"].hyperlink is not None
    assert pr.column_dimensions["O"].outlineLevel == 1   # detail columns are collapsible, not removed


def test_major_complaint_blocks_best_value(client):
    feats = {"VALUE": {"features": [{"name": "durability", "mentions": 40, "positive": 20, "negative": 20}], "stars": {"1": 9}},
             "PRICEY": {"features": [{"name": "quality", "mentions": 50, "positive": 48, "negative": 2}], "stars": {"1": 2}}}
    r = client.post("/api/analyze", json={"url": "u", "keyword": "k", "products": CARDS, "features": feats,
                                          "fetch_log": [{"asin": "VALUE", "status": "fetched"}], "phase": "features"}).json()
    assert r["best_value"]["asin"] != "VALUE"                      # 50% negative durability rules it out
    assert any("major complaint" in i["insight"] and "VALUE" in i["asins"] for i in r["insights"])
    assert r["features_known"] == 2
    assert client.get("/runs").status_code == 200 and client.get(f"/api/runs/{r['run_id']}").json()["name"] == "analyze"


def test_only_untrusted_matches_is_not_a_failure(client):
    # real case: the filter kept 3 necklaces, all under 50 ratings; the run used to fail at "contenders"
    rel = {"AD": False, "CHEAP": True, "VALUE": False, "PRICEY": False}
    r = client.post("/api/analyze", json={"url": "u", "keyword": "k", "products": CARDS, "phase": "ai",
                                          "relevance": rel}).json()
    assert r["status"] == "ok" and r["best_value"] is None
    assert r["ranked"][0]["asin"] == "CHEAP" and r["ranked"][0]["risk"]


def _p(a, price=5.0, rating=4.5, n=500):
    return {"asin": a, "position": 1, "price": price, "rating": rating, "n_ratings": n, "badges": []}


GOOD = [_p("A", 5, 4.6, 800), _p("B", 9, 4.7, 3000), _p("C", 3, 4.0, 200)]
EDGES = {  # name: (request extras, expected best value, expected ranked asins)
    "no products": (dict(products=[]), None, []),
    "all unpriced": (dict(products=[_p("A", 0), _p("B", None)]), None, []),
    "no ratings yet": (dict(products=[_p("A", rating=None, n=None), _p("B", rating=None, n=0)]), None, ["A", "B"]),
    "all under 50 ratings": (dict(products=[_p("A", n=3), _p("B", n=10)]), None, ["A", "B"]),
    "filter: everything off-topic": (dict(products=GOOD, phase="ai", relevance={a: False for a in "ABC"}), None, []),
    "filter: only some checked": (dict(products=GOOD, phase="ai", relevance={"A": True}), "A", ["A", "B", "C"]),
}


@pytest.mark.parametrize("name", EDGES)
def test_edge_cases_never_fail_and_export(client, name):
    extra, best, ranked = EDGES[name]
    r = client.post("/api/analyze", json={"url": "u", "keyword": "k", **extra}).json()
    assert r["status"] == "ok", r.get("error")
    assert (r["best_value"] or {}).get("asin") == best
    assert [p["asin"] for p in r["ranked"]] == ranked
    if name == "no ratings yet":
        assert all(p["risk"] == ["no ratings"] for p in r["ranked"])
    if name == "filter: only some checked":
        assert [p["unchecked"] for p in r["ranked"]] == [False, True, True]
    assert client.post("/api/export", json={"keyword": "k", "result": r, "products": extra["products"]}).status_code == 200
