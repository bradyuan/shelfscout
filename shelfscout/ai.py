"""AI mode: is each shortlisted product what the user asked for?

The search is the request; optional notes refine it ("raincoat for dogs" + "no plastic"). For each product a
LangGraph ReAct agent (langchain.agents.create_agent) decides relevant / not relevant, calling a RAG tool that
searches that product's evidence (title, Amazon's per-feature summaries and customer quotes) embedded with
sentence-transformers (PyTorch) in Chroma. Output: relevant, one-line reason, and a supporting quote that code
then verifies word for word against the evidence (a made-up quote is dropped and flagged).

Keys come from the user (extension settings), arrive per request, and are never stored.
"""
import asyncio
import hashlib
import json
import re
import time
import urllib.request
from functools import lru_cache

from pydantic import BaseModel, Field

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
PROVIDERS = ("gemini", "openai", "anthropic")
CONCURRENCY = None  # no fixed cap: everything starts at once; the limiter only limits after the provider complains


def is_rate_limited(e: Exception) -> bool:
    s = str(e).lower()
    return "429" in s or "quota" in s or "resource_exhausted" in s or "rate limit" in s or "overloaded" in s


def retry_after(e: Exception, default: float = 10.0) -> float:
    """Seconds the provider asked us to wait ('retry in 17.3s', 'retryDelay': '17s'), else a default."""
    m = re.search(r"retry(?:[ _-]?delay)?['\"]?\s*[:=]?\s*['\"]?(?:in\s+)?(\d+(?:\.\d+)?)\s*s", str(e), re.I)
    return min(float(m.group(1)), 90.0) if m else default


class AdaptiveLimiter:
    """Shared across every call on one key. No limit until the provider complains; then pause everyone for what it
    asked and cap concurrency at half of what was running; each success raises the cap by one (AIMD, like TCP)."""
    def __init__(self, max_limit: int | None = CONCURRENCY):
        self.max = max_limit if max_limit is not None else float("inf")
        self.limit, self.in_flight = self.max, 0
        self.paused_until = 0.0
        self.cond = asyncio.Condition()
        self.backoffs = 0

    async def __aenter__(self):
        async with self.cond:
            while True:
                wait = self.paused_until - asyncio.get_running_loop().time()
                if wait > 0:
                    self.cond.release()
                    try:
                        await asyncio.sleep(wait)
                    finally:
                        await self.cond.acquire()
                    continue
                if self.in_flight < self.limit:
                    self.in_flight += 1
                    return self
                await self.cond.wait()

    async def __aexit__(self, *exc):
        async with self.cond:
            self.in_flight -= 1
            self.cond.notify_all()

    async def success(self):
        async with self.cond:
            self.limit = min(self.max, self.limit + 1)
            self.cond.notify_all()

    async def rate_limited(self, e: Exception):
        import random
        async with self.cond:
            now = asyncio.get_running_loop().time()
            if now >= self.paused_until:  # first complaint of this burst: halve and pause
                base = self.in_flight + 1 if self.limit == float("inf") else self.limit
                self.limit = max(1, int(base) // 2)
                self.backoffs += 1
            self.paused_until = max(self.paused_until, now + retry_after(e) + random.uniform(0, 2))
            self.cond.notify_all()


_limiters: dict = {}


def limiter_for(provider: str, key: str) -> AdaptiveLimiter:
    """One limiter per key, shared by concurrent requests (the provider's limit is per key)."""
    k = provider + ":" + hashlib.sha256(key.encode()).hexdigest()[:16]
    loop = asyncio.get_running_loop()
    if k not in _limiters or _limiters[k][0] is not loop:
        _limiters[k] = (loop, AdaptiveLimiter())
    return _limiters[k][1]


# ---------- the user's key -> a model ----------
def _get(url, headers):
    import ssl
    import certifi
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15, context=ctx) as r:
        return json.load(r)


def _version(name):
    return [int(x) for x in re.findall(r"\d+", name)]


@lru_cache(maxsize=32)
def _models(provider: str, key_hash: str, key: str) -> list[str]:
    if provider == "gemini":
        data = _get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200", {})
        return [m["name"].split("/")[-1] for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]
    if provider == "openai":
        return [m["id"] for m in _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})["data"]]
    if provider == "anthropic":
        return [m["id"] for m in _get("https://api.anthropic.com/v1/models?limit=100",
                                      {"x-api-key": key, "anthropic-version": "2023-06-01"})["data"]]
    raise ValueError(f"unknown provider {provider}")


def pick_model(provider: str, key: str, tier: str = "fast") -> str:
    """Newest fast (or strong) chat model this key can use, from the provider's own model list.
    Prefers stable names; falls back to preview builds when that's all the key has."""
    names = _models(provider, hashlib.sha256(key.encode()).hexdigest(), key)
    family = {("gemini", "fast"): "flash", ("gemini", "strong"): "pro",
              ("openai", "fast"): "mini", ("openai", "strong"): "gpt",
              ("anthropic", "fast"): "haiku", ("anthropic", "strong"): "sonnet"}[(provider, tier)]
    never = re.compile(r"(image|tts|audio|realtime|live|embed|vision|search|transcribe|instruct|computer|"
                       r"codex|oss|thinking|robotics|nano|lite|tuning|learnlm|gemma|aqa)", re.I)
    base = {"gemini": r"^gemini-", "openai": r"^gpt-", "anthropic": r"^claude-"}[provider]
    pool = [n for n in names if re.search(base, n) and family in n.lower() and not never.search(n)]
    if provider == "openai" and tier == "strong":
        pool = [n for n in pool if not re.search(r"mini|nano", n)]
    if not pool:
        raise ValueError(f"no {tier} {provider} chat model available to this key")
    stable = [n for n in pool if not re.search(r"preview|exp|latest|\d{4}-\d{2}-\d{2}|-\d{3}$", n)]
    return max(stable or pool, key=_version)


def chat_model(provider: str, key: str, model: str):
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, api_key=key, temperature=0)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, api_key=key, temperature=0)
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, api_key=key, temperature=0)


def test_key(provider: str, key: str) -> dict:
    model = pick_model(provider, key)
    reply = chat_model(provider, key, model).invoke("Reply with the single word: ok")
    return {"ok": True, "model": model, "reply": str(reply.content)[:40]}


# ---------- RAG over each product's evidence ----------
class _Embed:
    """LangChain Embeddings over a local sentence-transformers model (PyTorch)."""
    @staticmethod
    @lru_cache(maxsize=1)
    def model():
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBED_MODEL)

    def embed_documents(self, texts):
        return self.model().encode(texts, normalize_embeddings=True).tolist()

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def evidence(product: dict) -> list[dict]:
    """Everything known about one product, as short documents."""
    docs = [{"kind": "title", "text": product.get("title") or ""}]
    for b in product.get("badges") or []:
        docs.append({"kind": "badge", "text": b})
    for f in product.get("features") or []:
        neg = round(100 * f["negative"] / f["mentions"]) if f.get("mentions") else 0
        docs.append({"kind": "feature", "text": f"{f['name']}: {f['mentions']} customers mention it, {neg}% negative. {f.get('summary') or ''}".strip()})
        for q in f.get("quotes") or []:
            docs.append({"kind": "quote", "text": q, "feature": f["name"]})
    return [d for d in docs if d["text"]]


def build_store(products: list[dict]):
    from langchain_chroma import Chroma
    import chromadb
    store = Chroma(collection_name="ev_" + hashlib.md5(json.dumps([p["asin"] for p in products]).encode()).hexdigest()[:12],
                   embedding_function=_Embed(), client=chromadb.EphemeralClient(),
                   collection_metadata={"hnsw:space": "cosine"})
    texts, metas = [], []
    for p in products:
        for d in evidence(p):
            texts.append(d["text"])
            metas.append({"asin": p["asin"], "kind": d["kind"]})
    if texts:
        store.add_texts(texts, metadatas=metas)
    return store


# ---------- the agent ----------
class Judgment(BaseModel):
    relevant: bool = Field(description="True only if this product is what the shopper asked for")
    reason: str = Field(description="One short sentence, plain English")
    quote: str | None = Field(default=None, description="Exact text copied from the evidence that supports the decision, or null")


PROMPT = """You check Amazon search results for a shopper.
Shopper searched: "{query}"{notes_line}
Product: "{title}"

Decide if this product is what the shopper asked for. Wrong product type, wrong size/model/variant they
specified, or failing anything in their notes means NOT relevant. Generic products that merely share a word
are NOT relevant (a barbed-wire tattoo is not a teardrop tattoo).
If the title alone doesn't settle it, call search_evidence with the specific thing to check (e.g. "plastic",
"size", "teardrop design"). Base your answer only on the title and evidence. If you quote, copy the text
exactly from the evidence."""


async def judge_one(model, store, product: dict, query: str, notes: str) -> dict:
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    calls = []

    @tool
    def search_evidence(what: str) -> str:
        """Search this product's title, Amazon review-feature summaries and customer quotes for `what`."""
        calls.append(what)
        hits = store.similarity_search(what, k=5, filter={"asin": product["asin"]})
        return "\n".join(f"- [{h.metadata['kind']}] {h.page_content}" for h in hits) or "(no evidence found)"

    agent = create_agent(model, tools=[search_evidence], response_format=Judgment)
    notes_line = f'\nShopper notes: "{notes}"' if notes else ""
    prompt = PROMPT.format(query=query, notes_line=notes_line, title=product.get("title") or product["asin"])
    try:
        out = await agent.ainvoke({"messages": [{"role": "user", "content": prompt}]}, config={"recursion_limit": 16})
        j: Judgment = out["structured_response"]
        fallback = False
    except Exception as e:
        if "recursion" not in str(e).lower() and "structured" not in str(e).lower():
            raise
        # the agent kept calling tools without answering: decide once with the evidence it gathered
        if not calls:
            calls.append(query)
        found = "\n".join(f"- [{h.metadata['kind']}] {h.page_content}" for q in dict.fromkeys(calls)
                          for h in store.similarity_search(q, k=5, filter={"asin": product["asin"]}))
        j = await model.with_structured_output(Judgment).ainvoke(
            prompt + "\n\nEvidence found:\n" + (found or "(none)") + "\n\nDecide now.")
        fallback = True
    text = " ".join(d["text"] for d in evidence(product)).lower()
    quote_ok = bool(j.quote) and re.sub(r"\s+", " ", j.quote.strip().strip('"').lower()) in re.sub(r"\s+", " ", text)
    return {"asin": product["asin"], "title": (product.get("title") or "")[:80], "relevant": j.relevant,
            "reason": j.reason, "quote": j.quote if quote_ok else None,
            "quote_check": "verified" if quote_ok else ("invented" if j.quote else "none"), "searches": calls,
            "fallback": fallback}


async def judge(provider: str, key: str, query: str, notes: str, products: list[dict], model: str | None = None,
                limiter: "AdaptiveLimiter | None" = None, on_done=None) -> dict:
    """on_done() is called as each product finishes, for the panel's progress bar."""
    model = model or pick_model(provider, key)
    llm = chat_model(provider, key, model)
    store = build_store(products)
    lim = limiter or limiter_for(provider, key)

    async def one(p):
        err = None
        for attempt in range(6):
            async with lim:
                try:
                    t0 = time.perf_counter()
                    r = await judge_one(llm, store, p, query, notes)
                    r["ms"] = round((time.perf_counter() - t0) * 1000)
                    await lim.success()
                    return r
                except Exception as e:
                    err = e
                    if not is_rate_limited(e):
                        break
            await lim.rate_limited(err)  # everyone pauses; limit halves; then retry
        try:
            raise err
        except Exception as e:  # one product failing shouldn't sink the rest
                return {"asin": p["asin"], "title": (p.get("title") or "")[:80], "relevant": None,
                        "reason": f"error: {str(e)[:120]}", "quote": None, "quote_check": "none", "searches": []}

    async def tracked(p):
        r = await one(p)
        if on_done:
            on_done()
        return r

    results = await asyncio.gather(*(tracked(p) for p in products))
    return {"model": f"{provider}:{model}", "judgments": results, "backoffs": lim.backoffs, "final_limit": None if lim.limit == float("inf") else lim.limit,
            "evidence_docs": sum(len(evidence(p)) for p in products)}
