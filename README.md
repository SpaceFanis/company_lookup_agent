# Company Lookup Agent

Paste a company URL; an agent decides which pages to read and what to search, then returns a structured company profile with sources. Built to learn how agents work (hand-written loop, Gemini function calling, Firecrawl, MCP, Streamlit). Full plan: [docs/plan.md](docs/plan.md).

## Setup
1. Install [uv](https://docs.astral.sh/uv/) (`brew install uv`).
2. `uv sync`
3. `cp .env.example .env`, then add your keys:
   - `GEMINI_API_KEY` from https://aistudio.google.com ("Get API key", free tier)
   - `FIRECRAWL_API_KEY` from https://firecrawl.dev (free plan)
4. Check: `uv run python -m company_agent.config` should print `Missing keys: none - ready`.
