"""Research tools backed by the Firecrawl Python SDK.

A "tool backend" is anything with:
  - `declarations`: the FunctionDeclarations the model sees
  - `call(name, args) -> dict`: runs a tool and returns a JSON-able result
The agent loop only talks to that interface, so the MCP backend (milestone 6)
can replace this one without changing the loop.
"""

from typing import Any

from firecrawl import Firecrawl
from google.genai import types

from company_agent import config

MAX_MAP_LINKS = 50
MAX_SEARCH_RESULTS = 5

DECLARATIONS = [
    types.FunctionDeclaration(
        name="map_site",
        description=(
            "List the URLs found on a website (titles included when known). "
            "Cheap. Use it first to decide which pages are worth scraping."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Site URL, e.g. https://example.com"}},
            "required": ["url"],
        },
    ),
    types.FunctionDeclaration(
        name="scrape_page",
        description=(
            "Read one web page and return its main content as markdown "
            f"(truncated to about {config.MAX_PAGE_CHARS} characters). Counts against the page budget."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full page URL"}},
            "required": ["url"],
        },
    ),
    types.FunctionDeclaration(
        name="web_search",
        description=(
            "Search the web. Returns titles, URLs and snippets. Use for news, "
            "funding, competitors and facts the company's own site doesn't state."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query"}},
            "required": ["query"],
        },
    ),
]

# Tools that consume the page budget (the agent loop enforces the cap).
PAGE_TOOLS = {"scrape_page"}


class SDKTools:
    """Firecrawl SDK implementation of the tool backend."""

    declarations = DECLARATIONS
    page_tools = PAGE_TOOLS

    def __init__(self, client: Firecrawl | None = None) -> None:
        self.fc = client or Firecrawl(api_key=config.FIRECRAWL_API_KEY)

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool {name!r}"}
        return handler(**args)

    def _tool_map_site(self, url: str) -> dict[str, Any]:
        result = self.fc.map(normalize_url(url), limit=MAX_MAP_LINKS)
        links = [
            {"url": link.url, "title": link.title} if link.title else {"url": link.url}
            for link in (result.links or [])
        ]
        return {"count": len(links), "links": links}

    def _tool_scrape_page(self, url: str) -> dict[str, Any]:
        doc = self.fc.scrape(normalize_url(url), formats=["markdown"], only_main_content=True)
        markdown = doc.markdown or ""
        title = doc.metadata.title if doc.metadata else None
        return {
            "url": url,
            "title": title,
            "markdown": markdown[: config.MAX_PAGE_CHARS],
            "truncated": len(markdown) > config.MAX_PAGE_CHARS,
        }

    def _tool_web_search(self, query: str) -> dict[str, Any]:
        result = self.fc.search(query, limit=MAX_SEARCH_RESULTS)
        hits = [
            {"title": getattr(r, "title", None), "url": getattr(r, "url", None),
             "snippet": (getattr(r, "description", None) or "")[: config.MAX_SNIPPET_CHARS]}
            for r in (result.web or [])
        ]
        return {"query": query, "results": hits}


def normalize_url(url: str) -> str:
    url = url.strip()
    return url if url.startswith(("http://", "https://")) else f"https://{url}"
