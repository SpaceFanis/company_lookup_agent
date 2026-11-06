"""Agent loop tests with a scripted fake Gemini client and fake tools (no network)."""

from typing import Any

from google.genai import errors, types

from company_agent.agent import run_agent

URL = "https://acme.test"


# --- Fakes -------------------------------------------------------------------

def call(tool: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=tool, args=args))


def text(t: str) -> types.Part:
    return types.Part(text=t)


def reply(*parts: types.Part, tokens=(10, 5, 2)) -> types.GenerateContentResponse:
    prompt, output, thinking = tokens
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt, candidates_token_count=output,
            thoughts_token_count=thinking, total_token_count=prompt + output + thinking),
    )


VALID_PROFILE = {
    "name": "Acme", "website": URL, "one_liner": "Makes anvils.",
    "description": "Acme makes anvils.", "sources": [URL],
}


def submit(**overrides) -> types.Part:
    return call("submit_company_profile", **{**VALID_PROFILE, **overrides})


class FakeModels:
    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []

    def generate_content(self, model, contents, config):
        # Snapshot the history as sent, since the loop keeps appending to it.
        self.requests.append({"contents": list(contents), "config": config})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.models = FakeModels(script)


class FakeTools:
    declarations = [types.FunctionDeclaration(name=n, description=n) for n in
                    ("map_site", "scrape_page", "web_search")]
    page_tools = {"scrape_page"}

    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, args))
        if name in self.fail:
            raise RuntimeError(f"{name} exploded")
        if name == "scrape_page":
            return {"url": args["url"], "title": "Page", "markdown": "content", "truncated": False}
        return {"ok": True, "tool": name}


def run(script, tools=None, **kw):
    client = FakeClient(script)
    tools = tools or FakeTools()
    events = list(run_agent(URL, tools=tools, client=client, model="fake",
                            sleep=lambda s: None, **kw))
    return events, client, tools


def final_of(events):
    assert events[-1].type == "final"
    return events[-1].data


def function_responses(content: types.Content):
    return [p.function_response for p in content.parts if p.function_response]


# --- Tests -------------------------------------------------------------------

def test_calls_tools_then_finishes_on_submit():
    events, client, tools = run([
        reply(call("map_site", url=URL)),
        reply(submit()),
    ])
    result = final_of(events)
    assert result["error"] is None and not result["partial"]
    assert result["profile"].name == "Acme"
    assert tools.calls == [("map_site", {"url": URL})]


def test_two_tool_calls_in_one_turn_return_together():
    events, client, tools = run([
        reply(call("map_site", url=URL), call("web_search", query="Acme funding")),
        reply(submit()),
    ])
    assert [name for name, _ in tools.calls] == ["map_site", "web_search"]
    # The 2nd request's last message holds BOTH function responses, plus the budget note.
    last_msg = client.models.requests[1]["contents"][-1]
    assert last_msg.role == "user"
    assert [fr.name for fr in function_responses(last_msg)] == ["map_site", "web_search"]
    assert "Budget:" in last_msg.parts[-1].text
    assert final_of(events)["profile"] is not None


def test_budget_note_counts_down():
    events, client, _ = run([
        reply(call("scrape_page", url=f"{URL}/about")),
        reply(submit()),
    ], max_steps=5, max_pages=3)
    first_task = client.models.requests[0]["contents"][0].parts[0].text
    assert "5 steps left, 3 page scrapes left" in first_task
    note = client.models.requests[1]["contents"][-1].parts[-1].text
    assert "4 steps left, 2 page scrapes left" in note


def test_one_nudge_then_continue():
    events, client, _ = run([
        reply(text("Let me think about this.")),
        reply(submit()),
    ])
    assert [e.type for e in events].count("nudge") == 1
    nudge_msg = client.models.requests[1]["contents"][-1]
    assert "submit_company_profile" in nudge_msg.parts[0].text
    assert final_of(events)["profile"] is not None


def test_two_turns_without_tool_calls_stop_with_partial():
    events, _, _ = run([
        reply(text("Acme makes anvils.")),
        reply(text("Still thinking.")),
    ])
    result = final_of(events)
    assert result["partial"] and result["profile"] is None
    assert "twice" in result["error"]
    assert result["last_text"] == "Still thinking."


def test_step_cap_returns_partial():
    events, client, _ = run([reply(call("map_site", url=URL))] * 3, max_steps=3)
    result = final_of(events)
    assert result["partial"]
    assert "Step budget (3)" in result["error"]
    assert len(client.models.requests) == 3


def test_last_step_forces_submit_tool():
    _, client, _ = run([reply(call("map_site", url=URL))] * 2, max_steps=2)
    first, last = (r["config"] for r in client.models.requests)
    assert first.tool_config is None
    fc = last.tool_config.function_calling_config
    assert fc.mode == "ANY" and fc.allowed_function_names == ["submit_company_profile"]


def test_tool_exception_is_returned_to_model():
    events, client, _ = run([
        reply(call("web_search", query="x")),
        reply(submit()),
    ], tools=FakeTools(fail={"web_search"}))
    fr = function_responses(client.models.requests[1]["contents"][-1])[0]
    assert "web_search exploded" in fr.response["error"]
    assert final_of(events)["profile"] is not None


def test_page_budget_blocks_extra_scrapes():
    events, _, tools = run([
        reply(call("scrape_page", url=f"{URL}/a"), call("scrape_page", url=f"{URL}/b")),
        reply(submit()),
    ], max_pages=1)
    assert [a["url"] for n, a in tools.calls] == [f"{URL}/a"]   # 2nd scrape never ran
    results = [e for e in events if e.type == "tool_result"]
    assert "Page budget" in results[1].data["error"]
    assert final_of(events)["pages_used"] == 1


def test_invalid_submit_is_rejected_then_retried():
    events, client, _ = run([
        reply(submit(founded_year=3000, sources=[])),
        reply(submit(founded_year=1949)),
    ])
    error = function_responses(client.models.requests[1]["contents"][-1])[0].response["error"]
    assert "Profile rejected" in error
    assert "founded_year: must be between 1800" in error
    assert "sources: cite at least one source URL" in error
    assert final_of(events)["profile"].founded_year == 1949


def test_submit_with_missing_required_field_is_rejected():
    bad = {k: v for k, v in VALID_PROFILE.items() if k != "one_liner"}
    events, client, _ = run([
        reply(call("submit_company_profile", **bad)),
        reply(submit()),
    ])
    error = function_responses(client.models.requests[1]["contents"][-1])[0].response["error"]
    assert "one_liner: Field required" in error
    assert final_of(events)["profile"] is not None


def test_token_usage_accumulates():
    events, _, _ = run([
        reply(call("map_site", url=URL), tokens=(100, 20, 30)),
        reply(submit(), tokens=(200, 40, 10)),
    ])
    usage_events = [e for e in events if e.type == "usage"]
    assert usage_events[0].data["step"]["total"] == 150
    assert usage_events[1].data["total"] == {"prompt": 300, "output": 60, "thinking": 40, "total": 400}
    assert final_of(events)["usage"]["total"] == 400


def test_rate_limit_is_retried():
    rate_limited = errors.ClientError(429, {"error": {"code": 429, "message": "slow down"}})
    events, _, _ = run([rate_limited, reply(submit())])
    assert [e.type for e in events if e.type == "retry"] == ["retry"]
    assert final_of(events)["profile"] is not None


def test_daily_quota_fails_fast_with_partial():
    daily = errors.ClientError(429, {"error": {"code": 429, "message":
                                               "Generate content requests per day limit exceeded on Free Tier."}})
    events, client, _ = run([reply(call("map_site", url=URL)), daily])
    assert not [e for e in events if e.type == "retry"]
    result = final_of(events)
    assert result["partial"] and "per day" in result["error"]
    assert len(client.models.requests) == 2
