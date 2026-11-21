"""Plumbing only: the agent calls the RAG tool, returns a structured judgment, and code verifies quotes."""
import asyncio

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from shelfscout import ai


class FakeToolModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kw):
        return self


PRODUCT = {"asin": "P1", "title": "Transparent Dog Rain Poncho for Small Dogs",
           "features": [{"name": "material", "mentions": 20, "positive": 8, "negative": 12,
                         "summary": "Customers say it is thin plastic.", "quotes": ["it's a thin plastic poncho, tore in a week"]}]}


def run(final_args):
    msgs = iter([
        AIMessage(content="", tool_calls=[{"name": "search_evidence", "args": {"what": "plastic material"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[{"name": "Judgment", "args": final_args, "id": "c2"}]),
    ])
    store = ai.build_store([PRODUCT])
    return asyncio.run(ai.judge_one(FakeToolModel(messages=msgs), store, PRODUCT, "raincoat for dogs", "no plastic"))


def test_agent_uses_rag_and_verifies_quote():
    r = run({"relevant": False, "reason": "It is plastic.", "quote": "it's a thin plastic poncho, tore in a week"})
    assert r["relevant"] is False and r["searches"] == ["plastic material"] and r["quote_check"] == "verified"


def test_invented_quote_is_dropped():
    r = run({"relevant": False, "reason": "It is plastic.", "quote": "made of cheap vinyl"})
    assert r["quote"] is None and r["quote_check"] == "invented"


class LoopingModel(FakeToolModel):
    """Keeps calling the tool forever; structured fallback returns a verdict."""
    def with_structured_output(self, schema, **kw):
        class R:
            async def ainvoke(self, _):
                return schema(relevant=False, reason="fallback decided", quote=None)
        return R()


def test_agent_that_never_answers_falls_back():
    from itertools import repeat
    msgs = repeat(AIMessage(content="", tool_calls=[{"name": "search_evidence", "args": {"what": "plastic"}, "id": "c"}]))
    store = ai.build_store([PRODUCT])
    r = asyncio.run(ai.judge_one(LoopingModel(messages=msgs), store, PRODUCT, "raincoat for dogs", ""))
    assert r["fallback"] is True and r["relevant"] is False and r["reason"] == "fallback decided"


def test_limiter_backs_off_and_recovers():
    class RL(Exception):
        pass

    async def main():
        lim = ai.AdaptiveLimiter()                 # no cap until the first complaint
        assert lim.limit == float("inf")
        lim.in_flight = 99                         # 99 running + the one that just failed
        e = RL("429 RESOURCE_EXHAUSTED. Please retry in 0.2s.")
        assert ai.is_rate_limited(e) and abs(ai.retry_after(e) - 0.2) < 1e-9
        await lim.rate_limited(e)
        assert lim.limit == 50 and lim.backoffs == 1
        lim.in_flight = 0                          # those finished
        await lim.rate_limited(e)          # same burst: no second halving
        assert lim.limit == 50
        loop = asyncio.get_running_loop(); t = loop.time()
        async with lim:                    # waits out the pause
            pass
        assert loop.time() - t >= 0.15
        await lim.success()
        assert lim.limit == 51
    asyncio.run(main())


def test_progress_counts_every_finished_check(monkeypatch):
    async def fake_one(llm, store, p, query, notes):
        await asyncio.sleep(0.01)
        return {"asin": p["asin"], "relevant": True, "reason": "ok", "quote": None, "quote_check": "none", "searches": []}
    monkeypatch.setattr(ai, "pick_model", lambda *a, **k: "fake")
    monkeypatch.setattr(ai, "chat_model", lambda *a, **k: None)
    monkeypatch.setattr(ai, "build_store", lambda products: None)
    monkeypatch.setattr(ai, "judge_one", fake_one)
    seen = []
    out = asyncio.run(ai.judge("gemini", "k", "q", "", [{"asin": f"A{i}"} for i in range(7)],
                               limiter=ai.AdaptiveLimiter(), on_done=lambda: seen.append(1)))
    assert len(seen) == 7 and len(out["judgments"]) == 7
