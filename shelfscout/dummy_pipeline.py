"""G0 dummy pipeline: same stage shape as the real one, fake internals.

Exists only to prove the observability layer. Each stage gets replaced at its gate:
crawl -> Selenium (G1), chunk/embed -> LangChain + PyTorch + Chroma (G2),
retrieve -> retriever + reranker (G4), answer -> ReAct agent on OpenAI (G5).

    uv run python -m shelfscout.dummy_pipeline              # healthy run
    uv run python -m shelfscout.dummy_pipeline --break chunk # chunk returns nothing -> flagged
"""
import argparse
import hashlib
import math

from .trace import Run, StageFailed

LISTINGS = [
    {"asin": "B0DUMMY001", "title": "Wireless Earbuds A", "reviews": [
        "Battery died after two hours, very disappointed.",
        "Great sound for the price.",
        "Left earbud stopped charging in a month."]},
    {"asin": "B0DUMMY002", "title": "Wireless Earbuds B", "reviews": [
        "Battery life is amazing, lasts all day.",
        "Case feels cheap and the hinge broke.",
        "Pairing is quick and reliable."]},
    {"asin": "B0DUMMY003", "title": "Wireless Earbuds C", "reviews": [
        "Fits well, but battery drains fast on calls.",
        "Noise cancelling barely works."]},
]
QUERY = "What do customers complain about with battery life?"
DIM = 64


def crawl(_):
    return LISTINGS


def chunk(listings):
    for l in listings:
        for i, text in enumerate(l["reviews"]):
            yield {"chunk_id": f"{l['asin']}-r{i}", "asin": l["asin"], "text": text}


def _vec(text):
    v = [0.0] * DIM
    for w in text.lower().replace(",", " ").replace(".", " ").split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1
    n = math.sqrt(sum(x * x for x in v)) or 1
    return [x / n for x in v]


def embed(chunks):
    for c in chunks:
        yield {**c, "vector": [round(x, 3) for x in _vec(c["text"])]}


def retrieve(embedded, k=3):
    q = _vec(QUERY)
    scored = [{"chunk_id": e["chunk_id"], "text": e["text"],
               "score": round(sum(a * b for a, b in zip(q, e["vector"])), 3)} for e in embedded]
    return sorted(scored, key=lambda r: -r["score"])[:k]


def answer(hits):
    cites = ", ".join(h["chunk_id"] for h in hits)
    return [{"question": QUERY, "answer": f"(dummy) Top matching reviews: {cites}",
             "citations": [h["chunk_id"] for h in hits]}]


def main(broken=None):
    stages = {"crawl": crawl, "chunk": chunk, "embed": embed, "retrieve": retrieve, "answer": answer}
    if broken:
        stages[broken] = lambda _: []
    run = Run("dummy", notes=f"G0 dummy pipeline{' (broken: ' + broken + ')' if broken else ''}")
    try:
        x = run.stage("crawl", stages["crawl"], [],
                      metric=lambda i, o: ("reviews", sum(len(l["reviews"]) for l in o)))
        x = run.stage("chunk", stages["chunk"], x, metric=lambda i, o: ("chunks", len(o)))
        x = run.stage("embed", stages["embed"], x, one_to_one=True,
                      metric=lambda i, o: ("dims", len(o[0]["vector"])))
        x = run.stage("retrieve", stages["retrieve"], x,
                      metric=lambda i, o: ("top score", o[0]["score"]))
        run.stage("answer", stages["answer"], x,
                  metric=lambda i, o: ("citations", len(o[0]["citations"])))
        run.finish("ok")
    except StageFailed as e:
        print("FAILED:", e)
    print(f"run {run.id}: {run.status} -> data/runs/{run.id}/")
    return run


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--break", dest="broken", choices=["crawl", "chunk", "embed", "retrieve", "answer"])
    main(p.parse_args().broken)
