"""Milestone 2: Hello Gemini.

Shows the two building blocks of every agent, step by step:
  Part 1: a plain call (prompt in, text out).
  Part 2: one function-calling round trip with a dummy tool.
          1. We describe a tool to the model (name, description, JSON-schema params).
          2. The model answers with a function_call instead of text.
          3. WE run the tool (the model never executes code) and send back a function_response.
          4. The model uses the result to write its final answer.
An agent is just Part 2 wrapped in a loop.

Run: uv run python examples/hello_gemini.py
"""

import json

from google import genai
from google.genai import types

from company_agent import config

client = genai.Client(api_key=config.GEMINI_API_KEY)


def show_usage(label: str, response: types.GenerateContentResponse) -> None:
    u = response.usage_metadata
    print(f"  [tokens {label}] prompt={u.prompt_token_count} "
          f"output={u.candidates_token_count} thinking={u.thoughts_token_count or 0} "
          f"total={u.total_token_count}")


# --- Part 1: plain call ----------------------------------------------------
print("=== Part 1: plain call ===")
response = client.models.generate_content(
    model=config.GEMINI_MODEL,
    contents="In one sentence: what is an AI agent?",
)
print(response.text.strip())
show_usage("part 1", response)


# --- Part 2: one function-calling round trip ------------------------------
print("\n=== Part 2: function calling ===")


def get_employee_count(company: str) -> dict:
    """Dummy tool. A real one would call an API; this returns fake data."""
    fake_db = {"acme": 120, "globex": 4300}
    count = fake_db.get(company.lower())
    if count is None:
        return {"error": f"No data for {company!r}"}
    return {"company": company, "employees": count}


# 1. Describe the tool. This JSON schema is all the model ever sees of it.
employee_tool = types.FunctionDeclaration(
    name="get_employee_count",
    description="Look up how many employees a company has.",
    parameters_json_schema={
        "type": "object",
        "properties": {"company": {"type": "string", "description": "Company name"}},
        "required": ["company"],
    },
)

config_with_tools = types.GenerateContentConfig(
    tools=[types.Tool(function_declarations=[employee_tool])],
    # The SDK can run Python tools for you automatically. We turn that off so
    # every step is visible - the agent loop will do this by hand too.
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)

# The conversation is a list of Content objects. We keep appending to it.
history: list[types.Content] = [
    types.Content(role="user", parts=[types.Part(text="How many employees do Acme and Globex have combined?")])
]

# 2. First call: the model should reply with function_call(s), not text.
response = client.models.generate_content(
    model=config.GEMINI_MODEL, contents=history, config=config_with_tools
)
show_usage("turn 1", response)

calls = response.function_calls or []
print(f"Model asked for {len(calls)} tool call(s):")
for call in calls:
    print(f"  function_call -> {call.name}({json.dumps(call.args)})")

# Keep the model's own turn in the history, exactly as it was returned.
history.append(response.candidates[0].content)

# 3. Run every requested tool and send ALL results back in ONE message.
response_parts = []
for call in calls:
    result = get_employee_count(**call.args)
    print(f"  function_response <- {call.name}: {result}")
    response_parts.append(types.Part.from_function_response(name=call.name, response=result))
history.append(types.Content(role="user", parts=response_parts))

# 4. Second call: the model now has the data and answers in text.
response = client.models.generate_content(
    model=config.GEMINI_MODEL, contents=history, config=config_with_tools
)
show_usage("turn 2", response)
print("Final answer:", response.text.strip())
