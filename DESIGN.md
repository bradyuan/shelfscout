# ShelfScout — design (re-locked 2026-11-18)

Chrome extension for Amazon **shoppers**: on any search page, find the **best value**, not the
cheapest item and not Amazon's pick (which can be sponsored or incentivized). Explains the trade-off
with evidence. ProScan-like page scraping underneath; the AI stack does the "what matters to you" part.

## Two modes
| | Normal (quantitative, every product) | AI enhance (qualitative, shortlist only) |
|---|---|---|
| Input | cards (price, stars, count, ads) + product-page **feature blocks** (Amazon's per-feature mention counts, positive/negative split over all reviews, summary, 3-4 quotes) | the user's sentence ("cheapest that lasts and leaves no mess") + feature summaries, quotes, bullets |
| Output | stats, fair rating, % negative, best value / best price / Amazon's pick, insights, Excel | must-haves / priorities / don't-cares, qualitative tags per shortlisted product, short cited report |
| Stack | math, extension, Selenium (eval fixtures), FastAPI | LangGraph agent, RAG (embeddings + Chroma), OpenAI, evals |
The AI never changes the numbers; it adds tags, dealbreakers and evidence on top.

Feature blocks: signed-out product pages carry `#rh_controls_aspect_N` (e.g. "Fit: 47 mention, 37 positive, 10 negative" + quotes);
21/29 cached product pages had them. Fetched in the user's tab for the top ~10 candidates, cached per ASIN.
No official API returns review text (PA-API: stars + count only).

Profiles (persistent, wipeable): must-haves (dealbreakers), weighted priorities, don't-cares; filled from the user's
sentence, adjusted by 👍/👎 + why; templates (Shopper, Reseller, Gift, Kids) are presets.

AI metrics: win rate vs formula + Amazon's pick (from 👍/👎), grounding rate (cited claims supported), must-have
violation rate (target ~0), plus feature-tagging accuracy on ~50 labeled snippets.

## AI mode (decided 2026-11-18)
One job: **is each shortlisted product what the user asked for?** The search is the request; an optional
notes box refines it ("raincoat for dogs" + "no plastic"). Output per product: relevant / not relevant +
one-line reason + supporting quote. Best value is then picked only from relevant products (same math).
- Engine: backend **LangGraph agent** (tools over the shortlist), **RAG** over Amazon feature summaries,
  quotes and bullets (embeddings + Chroma), LLM via LangChain so any provider works.
- **Bring your own key** (Gemini free tier suggested; OpenAI / Anthropic work too), entered once in the
  extension's settings page, kept in the browser, sent per request, never stored on the server. No key =
  normal mode. Gemini Nano was considered and rejected: it runs in the browser and covers none of the stack.
- Benchmark: the 30 frozen searches; labels from an LLM jury (two models from companies other than the one
  AI mode is tested with, plus a tie-breaker). No human labeling. Score = % of top picks relevant, formula
  vs AI mode.

## Three answers on every search page
| | How it's chosen |
|---|---|
| **Best price** | cheapest product on the page |
| **Amazon's pick** | "Overall Pick" / "Amazon's Choice" badge, else the first result shown (flagged if it's an ad) |
| **Best value** (ShelfScout) | fair rating (small rating counts pulled toward the page average) -> drop anything another product beats on both price and rating -> cheapest product within 0.15★ of the best fair rating |

G2 replaces the fixed rule with your stated needs ("for running, fit matters more than bass").

## Stack (every item does real work)
| Requirement | Choice | Job |
|---|---|---|
| Data (page) | Chrome extension, ProScan-style page-kind + parsers, closed Shadow DOM dock | reads the cards on the search page: price, rating, count, badges, ad flag |
| Data (details) | Selenium, headless (switchable: "Watch the crawler") | bullets, star breakdown, Amazon "Customers say" summary + topics for the contenders |
| RAG | sentence-transformers (PyTorch) embeddings + ChromaDB | match the user's needs to listing text and review topics |
| Agent | LangGraph `create_react_agent` + OpenAI | tools: `get_candidates`, `fetch_details`, `search_evidence`, `compare` |
| Evals | pytest eval suite | constraints met %, citations real %, Brad's blind rating vs best price / Amazon's pick |
| Serving | FastAPI + Docker, deployed to GCP | API for the extension |
| Tracing | factory view (local stage artifacts) + LangSmith for LLM steps | human inspection |

Deliberately not used: Flask, Django, AWS, Pinecone, pgvector, FAISS. Individual review text is not
collected (Amazon hides it signed out and serves ~8 per product signed in).

## Gates — Claude builds between gates; Brad checks 3 things and says go/fix
| Gate | Built | Brad checks |
|---|---|---|
| G0 Observability | factory view | done |
| G1 Data + value math | search snapshot -> fair ratings -> contenders -> 3 picks in the dock; Selenium fills contender details | 1) the 3 picks show on a search page 2) best value is a sensible pick vs the page 3) contender details filled in (factory view) |
| G2 AI | RAG + agent + evals | 1) 5 searches by need find the right products 2) chat answers correct, with real evidence 3) eval numbers hold up |
| G3 Ship | dock UI with picks + chat, Docker, GCP | 1) looks good 2) works on a live page 3) live URL |

Rules: no stage built on an unapproved gate. Pytest = plumbing only; Brad's checks are the bar.
Brad writes as little code as possible.
