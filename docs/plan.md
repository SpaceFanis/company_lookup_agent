# Company Lookup Agent — Build Plan

## Context
Practice project before the Gen AI Summit Hackathon (theme: autonomous AI agents). Goal is **understanding**, not just a working app: how an agent loop works, how tools/function-calling work, and how MCP plugs tools into an agent.

The app: paste a company URL → an agent autonomously decides which pages to read and what to search → returns a structured company profile with sources, while the UI shows every step live.

**Key decision (challenged & agreed):** "scrape → summarise" is a pipeline, not an agent. Here the LLM chooses its own tool calls in a loop until it has enough information. We write that loop **by hand (no framework)** so nothing is hidden — that's the knowledge that transfers to ADK / LangGraph / PydanticAI at the hackathon.

## Stack
| Piece | Choice | Why |
|---|---|---|
| LLM | Gemini (free tier, Google AI Studio) via `google-genai` SDK, `gemini-3.5-flash` (2.5 is retired for new keys; 3.7/3.8 Flash returned 503 "high demand" on 2026-11-06) | Free, fast, good function calling |
| Agent loop | Hand-written function-calling loop | Learn the internals |
| Web data | Firecrawl — Python SDK first, then Firecrawl MCP server | Learn tool-calling, then MCP |
| UI | Streamlit with live step log (`st.status`) | Quick, modern-enough, good for demos |
| Env | `uv` + Python 3.12, `pyproject.toml`, `.env` via `python-dotenv` | 3.14 wheels can lag |
| Output schema | Pydantic model | Structured, validated result |

Free-tier limits to respect: Gemini free tier has low requests-per-minute/day caps → cap the loop (max ~8 steps), add retry with backoff on 429. Firecrawl free credits are limited → cap pages scraped (~5), main content only, ~5k chars per page.

## Project layout
```
company_lookup_agent/
  docs/plan.md            # this plan (copied here as step 1)
  pyproject.toml
  .env.example            # GEMINI_API_KEY=, FIRECRAWL_API_KEY=
  .gitignore              # .env, .venv, __pycache__, .cache/
  README.md               # setup + run + "how the agent works"
  src/company_agent/
    config.py             # load env, model name, limits (MAX_STEPS, MAX_PAGES)
    schema.py             # Pydantic CompanyProfile
    tools_sdk.py          # Firecrawl SDK tools: map_site, scrape_page, web_search
    tools_mcp.py          # MCP client: list Firecrawl MCP tools -> Gemini declarations, call_tool
    agent.py              # the agent loop (core learning file)
    prompts.py            # system prompt
  app.py                  # Streamlit UI
  tests/test_agent.py     # loop tests with a fake model + fake tools
```
(The existing `index.html` is an unrelated to-do page; leave it untouched.)

## How the agent works (agent.py)
1. **System prompt** (prompts.py), guidance not hard bans:
   - Research the company at the given URL; prefer the company's own site; cite a source URL for every fact.
   - Prioritise `/about`, `/product`, `/pricing`, `/solutions` pages.
   - Legal pages (terms, privacy, cookies) and old blog posts are low value.
   - Use careers pages only for team size and hiring signals.
   - Use web search for news, funding and competitors.
   - When you have enough, call `submit_company_profile`.
2. **Budget in context:** each turn, the tool-response message carries a short note like `Budget: 5 steps left, 3 page scrapes left`.
3. **Loop** (max `MAX_STEPS`), per model turn:
   - **Multiple calls per turn:** handle *every* `function_call` in the turn, in order, and send *all* `function_response` parts back together in one message. Emit one UI event per call `{step, tool, args, result_preview}`.
   - **Terminal tool:** `submit_company_profile(profile)` — its parameters follow the `CompanyProfile` schema. When called, validate the args with Pydantic:
     - valid → loop ends, return the profile;
     - `ValidationError` → return the error text as that call's `function_response` so the model fixes and resubmits.
   - **No function call in a turn:** send one nudge (`"Call a research tool or submit the profile."`). If the next turn also has none → stop and return a partial result (whatever text/facts exist) with an error note.
   - Step cap reached → stop with a partial result and an error note.
   - No separate "final JSON" request — the terminal tool replaces it.
4. **Guards:** page cap, 429 retry with backoff, tool errors returned to the model as text (so it can recover — a key agent behaviour to observe).
5. **Token usage:** after each model call, read `response.usage_metadata` (prompt, output, total tokens), attach it to the step event and keep running totals.
6. `run_agent(url)` is a **generator** yielding events (`tool_call`, `tool_result`, `nudge`, `usage`, `final`, `error`), so the UI can stream steps.

Tool backend is swappable: `run_agent(url, tools=SDKTools() | MCPTools())` — same loop, different tool source. `submit_company_profile` is always our own local tool.

**Schema note:** Gemini function declarations can struggle with nested/`$ref` schemas. Try `CompanyProfile` as-is first (e.g. via `FunctionDeclaration` built from the Pydantic JSON schema). If Gemini rejects it, flatten it (e.g. `key_people` as a list of `"Name — Role"` strings instead of objects, inline all definitions) and **tell you what was flattened and why**.

### Tools (SDK version)
- `map_site(url)` → list of URLs on the domain (Firecrawl map), trimmed to ~50.
- `scrape_page(url)` → page markdown (Firecrawl scrape, `formats=["markdown"]`, `only_main_content=True`), **truncated to ~5,000 characters** per page.
- `web_search(query)` → top results with title/url/snippet (Firecrawl search) — for news, funding, competitors.
- `submit_company_profile(profile)` → terminal tool (see above).

### CompanyProfile schema
name, one_liner, description, industry, products/services, target customers, business model/pricing, headquarters, founded, team size (estimate), key people, funding/news highlights, competitors, social links, `sources: list[url]`, `confidence_notes`.

## Process
After **each milestone**: stop, explain what was built and why (and what to observe when running it), then wait for your go-ahead before starting the next one.

## Milestones (each one runnable, each teaches one thing)
1. **Setup** — install uv, `uv init` with Python 3.12, deps (`google-genai`, `firecrawl-py`, `streamlit`, `pydantic`, `python-dotenv`, `mcp`; dev: `pytest`), `.env.example`, `.gitignore`, `git init`, copy this plan to `docs/plan.md`. Get keys: Gemini at aistudio.google.com → "Get API key"; Firecrawl at firecrawl.dev → sign up → API key. Install the **Firecrawl Claude Code plugin** (dev-time helper for up-to-date Firecrawl docs/testing; not used by the agent).
2. **Hello Gemini** — one-call script; one function-call round trip with a dummy tool. *Learn: what a function_call / function_response looks like.*
3. **Agent loop + SDK tools (CLI)** — `uv run python -m company_agent.agent https://example.com` prints each step (with token usage) and the final profile. Includes multi-call turns, budget note, nudge logic. *Learn: the ReAct-style loop, stop conditions, error recovery.*
4. **Terminal tool + validation** — `submit_company_profile` with Pydantic validation and retry-on-error; flatten schema if Gemini needs it (report it). *Learn: tool-based structured output and self-correction.*
5. **Streamlit UI** — URL input and a **"Run research" button; the agent runs only inside that button's handler**. Events, profile and token stats are stored in `st.session_state`, so downloads and other widget clicks re-render from state and never re-run the agent. Live `st.status` step log (tool, args, short result), profile as cards, sources list, JSON + Markdown downloads, sidebar with step count, page count and token usage (per step + total). *Learn: streaming agent events to a UI; Streamlit's rerun model.*
6. **(Optional, 2-hour timebox) Swap to MCP** — first check whether `google-genai` accepts an MCP `ClientSession` directly as a tool. If it does, use that instead of hand-converting schemas — but keep automatic function calling **disabled** so our loop (terminal tool, events, budget) still runs the calls. Otherwise: list Firecrawl MCP tools (hosted remote URL, or `npx -y firecrawl-mcp` if Node is installed), convert schemas to `FunctionDeclaration`s, route via `session.call_tool`. UI toggle "Tools: SDK / MCP". If the timebox runs out, skip it and note why. *Learn: MCP = standard tool discovery; the loop doesn't change.*
7. **Stretch (optional, hackathon-relevant)** — a second "critic" pass that checks the profile for unsupported claims and triggers extra research; simple file cache; compare-two-companies mode.

## Verification
- `uv run pytest` — loop tests with a fake Gemini client + fake tools:
  - calls tools and ends when `submit_company_profile` is called with valid args;
  - **two tool calls in one turn** → both executed, both `function_response` parts returned in one message;
  - invalid profile args → validation error returned to model → model resubmits → success;
  - no function call → one nudge; two in a row → partial result with error note;
  - step cap → partial result; tool exception → error text returned to model;
  - token usage totals accumulate from `usage_metadata`.
- CLI run on 2–3 real sites (e.g. a well-known SaaS site, a small startup site): profile fields filled, every fact has a source URL, ≤ MAX_STEPS steps, no 429 crash, per-step tokens printed.
- `uv run streamlit run app.py`, open in the browser pane: agent starts only on "Run research", steps appear live, profile renders, clicking downloads does **not** re-run the agent, sidebar shows steps/pages/tokens, bad URL shows a friendly error.
- If milestone 6 done: same URL with SDK vs MCP toggle → comparable profiles.
