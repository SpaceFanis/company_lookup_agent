"""Streamlit UI for the company research agent.

Streamlit re-runs this whole script on every widget interaction. So:
  - the agent runs ONLY inside the "Run research" button handler;
  - everything it produces (events, profile, token stats) goes into st.session_state;
  - the page is always drawn from session_state, so clicking a download button
    (or anything else) just redraws the stored result, never re-runs the agent.

Run: uv run streamlit run app.py
"""

import re

import streamlit as st

from company_agent import config
from company_agent.agent import Event, run_agent
from company_agent.schema import CompanyProfile

st.set_page_config(page_title="Company Lookup Agent", page_icon="🔎", layout="wide")

MODELS = list(dict.fromkeys([config.GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.5-flash-lite"]))
URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[a-z]{2,}(/\S*)?$", re.IGNORECASE)


# --- State -------------------------------------------------------------------

def fresh_run_state() -> dict:
    return {"url": None, "model": None, "events": [], "result": None, "steps": 0, "pages": 0,
            "usage_total": {"prompt": 0, "output": 0, "thinking": 0, "total": 0},
            "usage_steps": []}


if "run" not in st.session_state:
    st.session_state.run = fresh_run_state()


def record(run: dict, ev: Event) -> None:
    """Fold one agent event into the stored run state."""
    run["events"].append(ev)
    run["steps"] = max(run["steps"], ev.step)
    if ev.type == "usage":
        run["usage_total"] = ev.data["total"]
        run["usage_steps"].append({"step": ev.step, **ev.data["step"]})
    elif ev.type == "tool_result" and ev.data["tool"] == "scrape_page" and not ev.data["error"]:
        run["pages"] += 1
    elif ev.type == "final":
        run["result"] = ev.data
        run["pages"] = ev.data["pages_used"]


# --- Rendering helpers ---------------------------------------------------------

def render_stats(run: dict, model: str) -> None:
    st.markdown("### Run stats")
    c1, c2 = st.columns(2)
    c1.metric("Steps", f"{run['steps']} / {config.MAX_STEPS}")
    c2.metric("Pages", f"{run['pages']} / {config.MAX_PAGES}")
    total = run["usage_total"]
    st.metric("Tokens (total)", f"{total['total']:,}")
    st.caption(f"prompt {total['prompt']:,} · output {total['output']:,} · thinking {total['thinking']:,}")
    if run["usage_steps"]:
        st.markdown("**Tokens per step**")
        st.dataframe(run["usage_steps"], hide_index=True, width="stretch",
                     column_config={"step": st.column_config.NumberColumn("Step", width="small")})
    st.caption(f"Model used: `{run['model'] or model}`")


def render_event(ev: Event) -> None:
    d = ev.data
    if ev.type == "tool_call":
        args = ", ".join(f"{k}={v!r}" for k, v in d["args"].items())
        if d["tool"] == "submit_company_profile":
            st.markdown(f"**Step {ev.step}** · 📝 `submit_company_profile(...)`")
        else:
            st.markdown(f"**Step {ev.step}** · 🔧 `{d['tool']}({args})`")
    elif ev.type == "tool_result":
        if d["error"]:
            st.markdown(f"&nbsp;&nbsp;&nbsp;⚠️ {d['preview'][:300]}")
        else:
            st.caption(f"↳ {d['preview'][:300]}")
    elif ev.type == "retry":
        st.caption(f"⏳ Gemini returned HTTP {d['code']}; retrying in {d['wait_s']}s")
    elif ev.type == "nudge":
        st.caption(f"💬 No tool call; nudged the model. It said: {d['model_text'][:200]}")


def bullet_card(title: str, items: list[str]) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.markdown("\n".join(f"- {i}" for i in items) if items else "_Unknown_")


def render_profile(p: CompanyProfile) -> None:
    st.header(p.name)
    st.markdown(f"**{p.one_liner}**")
    st.write(p.description)

    facts = [("Industry", p.industry), ("Headquarters", p.headquarters),
             ("Founded", p.founded_year), ("Team size", p.team_size)]
    for col, (label, value) in zip(st.columns(4), facts):
        with col, st.container(border=True):
            st.caption(label)
            st.markdown(f"**{value if value not in (None, '') else 'Unknown'}**")

    if p.business_model or p.hiring_signals:
        with st.container(border=True):
            if p.business_model:
                st.markdown(f"**Business model:** {p.business_model}")
            if p.hiring_signals:
                st.markdown(f"**Hiring:** {p.hiring_signals}")

    left, right = st.columns(2)
    with left:
        bullet_card("Products", p.products)
        bullet_card("Key people", [f"{x.name} — {x.role}" if x.role else x.name for x in p.key_people])
        bullet_card("Competitors", p.competitors)
    with right:
        bullet_card("Customers", p.target_customers)
        bullet_card("Funding", p.funding)
        bullet_card("Recent news", p.recent_news)

    bullet_card("Links", p.social_links)
    if p.confidence_notes:
        st.info(f"**Confidence notes:** {p.confidence_notes}")

    st.subheader("Sources")
    st.markdown("\n".join(f"{i}. {u}" for i, u in enumerate(p.sources, 1)))

    slug = re.sub(r"[^a-z0-9]+", "-", p.name.lower()).strip("-") or "company"
    d1, d2, _ = st.columns([1, 1, 4])
    # on_click="ignore": downloading doesn't even trigger a rerun.
    d1.download_button("Download JSON", p.model_dump_json(indent=2), f"{slug}.json",
                       "application/json", on_click="ignore", icon="⬇️")
    d2.download_button("Download Markdown", p.to_markdown(), f"{slug}.md",
                       "text/markdown", on_click="ignore", icon="⬇️")


def render_partial(result: dict) -> None:
    st.warning(f"**No complete profile.** {result['error']}")
    if result["last_text"]:
        with st.container(border=True):
            st.markdown("**What the model said last**")
            st.write(result["last_text"])
    if result["visited"]:
        st.markdown("**Pages read:**\n" + "\n".join(f"- {u}" for u in result["visited"]))


# --- Page ----------------------------------------------------------------------

with st.sidebar:
    st.title("🔎 Company Lookup")
    model = st.selectbox("Gemini model", MODELS,
                         help="Each model has its own free daily quota (about 20 requests). "
                              "One run uses up to 8.")
    stats_box = st.empty()   # filled live during a run, from state otherwise

st.title("Company research agent")
st.caption("Paste a company website. The agent decides which pages to read and what to search, "
           "then submits a structured profile with sources.")

missing = config.missing_keys()
if missing:
    st.error(f"Missing API keys in `.env`: {', '.join(missing)}. See README for setup.")

with st.form("research"):
    col_url, col_btn = st.columns([3, 1], vertical_alignment="bottom")
    url = col_url.text_input("Company URL", placeholder="https://example.com")
    run_clicked = col_btn.form_submit_button("Run research", type="primary", disabled=bool(missing),
                                             width="stretch")

if run_clicked:
    url = url.strip()
    if not URL_RE.match(url):
        st.error("That doesn't look like a website address. Try something like `https://example.com`.")
    else:
        run = st.session_state.run = fresh_run_state()
        run["url"] = url
        run["model"] = model
        with st.status(f"Researching {url}…", expanded=True) as status:
            try:
                for ev in run_agent(url, model=model):
                    record(run, ev)
                    render_event(ev)
                    with stats_box.container():
                        render_stats(run, model)
            except Exception as exc:   # e.g. invalid Firecrawl key, network down
                run["result"] = {"profile": None, "partial": True, "visited": [], "last_text": "",
                                 "error": f"{type(exc).__name__}: {exc}", "pages_used": run["pages"]}
            ok = bool(run["result"] and run["result"]["profile"])
            status.update(label="Research complete" if ok else "Research stopped early",
                          state="complete" if ok else "error", expanded=False)
        st.rerun()   # redraw everything from session_state

# Everything below is drawn from session_state, on every rerun.
run = st.session_state.run
with stats_box.container():
    render_stats(run, model)

if run["url"]:
    with st.expander(f"Agent steps for {run['url']} ({len(run['events'])} events)", expanded=False):
        for ev in run["events"]:
            render_event(ev)

result = run["result"]
if result:
    if result["profile"]:
        render_profile(result["profile"])
    else:
        render_partial(result)
elif not run_clicked:
    st.info("Enter a URL and click **Run research**. Each run uses up to "
            f"{config.MAX_STEPS} Gemini requests and about 10 Firecrawl credits.")
