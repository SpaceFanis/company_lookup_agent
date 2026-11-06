"""The agent loop: the core learning file.

Each step:
  1. Send the conversation + tool declarations to Gemini.
  2. If the model asked for tools: run EVERY call in the turn and send all
     function_responses back together (plus a budget note).
  3. If it called the terminal tool `submit_company_profile` with valid args: done.
  4. If it answered with no tool call: nudge once; a second time in a row: stop.
The loop yields Events, so a CLI or UI can show every step as it happens.

Run: uv run python -m company_agent.agent https://example.com
"""

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from google import genai
from google.genai import errors, types

from company_agent import config, prompts
from company_agent.tools_sdk import SDKTools, normalize_url

RETRY_CODES = {429, 500, 503}   # rate limit / transient server errors
RETRY_DELAYS = [2, 5, 10, 20]   # seconds between attempts


# --- Interfaces --------------------------------------------------------------

class ToolBackend(Protocol):
    declarations: list[types.FunctionDeclaration]
    page_tools: set[str]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]: ...


class Submitter(Protocol):
    """The terminal tool: its declaration, plus validation of the model's args."""
    declaration: types.FunctionDeclaration

    def validate(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        """Return (profile, None) if valid, or (None, error message)."""
        ...


class SimpleSubmitter:
    """Milestone 3 terminal tool: a markdown profile + sources.
    Milestone 4 replaces it with the full CompanyProfile schema."""

    declaration = types.FunctionDeclaration(
        name="submit_company_profile",
        description="Finish the research and submit the company profile.",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "profile_markdown": {"type": "string", "description": "The company profile in markdown"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Source URLs used"},
            },
            "required": ["profile_markdown", "sources"],
        },
    )

    def validate(self, args: dict[str, Any]) -> tuple[Any, str | None]:
        text = (args.get("profile_markdown") or "").strip()
        if not text:
            return None, "profile_markdown is empty."
        return {"profile_markdown": text, "sources": list(args.get("sources") or [])}, None


# --- Events and usage --------------------------------------------------------

@dataclass
class Event:
    type: str              # usage | retry | tool_call | tool_result | nudge | final
    step: int
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Usage:
    prompt: int = 0
    output: int = 0
    thinking: int = 0
    total: int = 0

    def add(self, meta: types.GenerateContentResponseUsageMetadata | None) -> "Usage":
        """Add one response's usage to the totals; return that response's usage."""
        step = Usage(
            prompt=(meta.prompt_token_count or 0) if meta else 0,
            output=(meta.candidates_token_count or 0) if meta else 0,
            thinking=(meta.thoughts_token_count or 0) if meta else 0,
            total=(meta.total_token_count or 0) if meta else 0,
        )
        self.prompt += step.prompt
        self.output += step.output
        self.thinking += step.thinking
        self.total += step.total
        return step


# --- The loop ----------------------------------------------------------------

def run_agent(
    url: str,
    tools: ToolBackend | None = None,
    submitter: Submitter | None = None,
    client: Any = None,
    model: str | None = None,
    max_steps: int = config.MAX_STEPS,
    max_pages: int = config.MAX_PAGES,
    sleep=time.sleep,
) -> Iterator[Event]:
    tools = tools or SDKTools()
    submitter = submitter or SimpleSubmitter()
    client = client or genai.Client(api_key=config.GEMINI_API_KEY)
    model = model or config.GEMINI_MODEL
    url = normalize_url(url)
    submit_name = submitter.declaration.name

    base_config = dict(
        system_instruction=prompts.system_prompt(),
        tools=[types.Tool(function_declarations=[*tools.declarations, submitter.declaration])],
        # We run tools ourselves, so the SDK must not do it automatically.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    history: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=prompts.task_message(url, max_steps, max_pages))])
    ]
    usage = Usage()
    pages_used = 0
    nudged = False
    last_text = ""
    visited: list[str] = []

    def final(profile: Any = None, error: str | None = None) -> Event:
        return Event("final", step, {
            "profile": profile, "partial": profile is None, "error": error,
            "last_text": last_text, "visited": visited, "pages_used": pages_used,
            "usage": vars(usage),
        })

    for step in range(1, max_steps + 1):
        last_step = step == max_steps
        gen_config = types.GenerateContentConfig(**base_config)
        if last_step:
            # Last step: only the terminal tool is allowed, so the run can end cleanly.
            gen_config.tool_config = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode="ANY", allowed_function_names=[submit_name]))

        try:
            response = yield from _generate(client, model, history, gen_config, step, sleep)
        except errors.APIError as exc:
            yield final(error=f"Model call failed: {exc}")
            return

        step_usage = usage.add(response.usage_metadata)
        yield Event("usage", step, {"step": vars(step_usage), "total": vars(usage)})

        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.content:
            history.append(candidate.content)   # keep the model's turn exactly as returned
        calls = response.function_calls or []

        # --- No tool call: nudge once, stop on the second time in a row.
        if not calls:
            last_text = _text_of(candidate) or last_text
            if nudged:
                yield final(error="The model stopped calling tools twice in a row.")
                return
            nudged = True
            yield Event("nudge", step, {"model_text": last_text})
            note = prompts.budget_note(max_steps - step, max_pages - pages_used)
            history.append(types.Content(role="user", parts=[types.Part(text=f"{prompts.NUDGE}\n{note}")]))
            continue
        nudged = False

        # --- Run every call in this turn; collect all responses into one message.
        response_parts: list[types.Part] = []
        for call in calls:
            args = dict(call.args or {})
            yield Event("tool_call", step, {"tool": call.name, "args": args})

            if call.name == submit_name:
                profile, error = submitter.validate(args)
                if error is None:
                    yield final(profile=profile)
                    return
                result = {"error": f"Profile rejected: {error} Fix it and call {submit_name} again."}
            elif call.name in tools.page_tools and pages_used >= max_pages:
                result = {"error": "Page budget used up. Use what you have, or web_search, then submit."}
            else:
                try:
                    result = tools.call(call.name, args)
                except Exception as exc:   # tool errors go back to the model so it can recover
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                if call.name in tools.page_tools and "error" not in result:
                    pages_used += 1
                    visited.append(args.get("url", ""))

            yield Event("tool_result", step, {"tool": call.name, "preview": _preview(result),
                                              "error": result.get("error")})
            response_parts.append(types.Part.from_function_response(name=call.name, response=result))

        note = prompts.budget_note(max_steps - step, max_pages - pages_used)
        history.append(types.Content(role="user", parts=[*response_parts, types.Part(text=note)]))

    yield final(error=f"Step budget ({max_steps}) used up before a profile was submitted.")


def _generate(client, model, history, gen_config, step, sleep):
    """Call Gemini, retrying rate limits / overloads with backoff. Yields retry events."""
    for attempt, delay in enumerate([*RETRY_DELAYS, None], start=1):
        try:
            return client.models.generate_content(model=model, contents=history, config=gen_config)
        except errors.APIError as exc:
            if exc.code not in RETRY_CODES or delay is None or _is_daily_quota(exc):
                raise
            yield Event("retry", step, {"code": exc.code, "attempt": attempt, "wait_s": delay})
            sleep(delay)


def _is_daily_quota(exc: errors.APIError) -> bool:
    """A per-day quota won't reset in seconds, so retrying it only wastes time."""
    message = str(exc).lower()
    return exc.code == 429 and ("per day" in message or "perday" in message)


def _text_of(candidate) -> str:
    if not candidate or not candidate.content:
        return ""
    return "".join(p.text for p in candidate.content.parts or [] if p.text and not p.thought).strip()


def _preview(result: dict[str, Any], limit: int = 300) -> str:
    if "error" in result:
        return f"ERROR: {result['error']}"
    if "markdown" in result:
        return f"{result.get('title') or ''} ({len(result['markdown'])} chars): {result['markdown'][:limit]}"
    if "links" in result:
        return f"{result['count']} links: " + ", ".join(l["url"] for l in result["links"][:8])
    if "results" in result:
        return " | ".join(f"{r['title']} <{r['url']}>" for r in result["results"])
    return str(result)[:limit]


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m company_agent.agent <company-url>")
    for ev in run_agent(sys.argv[1]):
        if ev.type == "usage":
            s = ev.data["step"]
            print(f"\n[step {ev.step}] tokens: prompt={s['prompt']} output={s['output']} "
                  f"thinking={s['thinking']} (run total {ev.data['total']['total']})")
        elif ev.type == "retry":
            print(f"  retry: HTTP {ev.data['code']}, waiting {ev.data['wait_s']}s")
        elif ev.type == "tool_call":
            print(f"  -> {ev.data['tool']}({ev.data['args']})")
        elif ev.type == "tool_result":
            print(f"  <- {ev.data['preview'][:200]}")
        elif ev.type == "nudge":
            print(f"  (no tool call; nudging) model said: {ev.data['model_text'][:200]}")
        elif ev.type == "final":
            d = ev.data
            print("\n" + "=" * 60)
            if d["profile"]:
                print(d["profile"]["profile_markdown"])
                print("\nSources:", *d["profile"]["sources"], sep="\n  ")
            else:
                print("PARTIAL RESULT -", d["error"])
                print(d["last_text"] or "(no text)")
                print("Pages read:", d["visited"])
            print(f"\nPages scraped: {d['pages_used']}  Tokens: {d['usage']}")


if __name__ == "__main__":
    main()
