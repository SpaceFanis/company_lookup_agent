# Company Lookup Agent

Paste a company URL; an agent decides which pages to read and what to search, then returns a structured company profile with sources. Built to learn how agents work (hand-written loop, Gemini function calling, Firecrawl, MCP, Streamlit). Full plan: [docs/plan.md](docs/plan.md).

## Setup
1. Install [uv](https://docs.astral.sh/uv/) (`brew install uv`).
2. `uv sync`
3. `cp .env.example .env`, then add your keys:
   - `GEMINI_API_KEY` from https://aistudio.google.com ("Get API key", free tier)
   - `FIRECRAWL_API_KEY` from https://firecrawl.dev (free plan)
4. Check: `uv run python -m company_agent.config` should print `Missing keys: none - ready`.

## Run
- Hello Gemini (one function-call round trip): `uv run python examples/hello_gemini.py`
- Research agent (CLI): `uv run python -m company_agent.agent https://example.com`
- Web UI: `uv run streamlit run app.py`, then open http://localhost:8501
- Tests (no network): `uv run pytest`

## Free-tier limits
- Gemini free tier allows about **20 requests per day per model**. One research run uses up to 8 (`MAX_STEPS`), so you get roughly 3 runs a day. When the daily quota runs out, the agent stops at once with a partial result.
- Firecrawl: 1 credit per map or scrape, about 2 per search. One run uses roughly 7–12.
