"""SDKTools shaping/truncation tests with a fake Firecrawl client (no network)."""

from types import SimpleNamespace as NS

from company_agent import config
from company_agent.tools_sdk import SDKTools, normalize_url


class FakeFirecrawl:
    def scrape(self, url, **kw):
        return NS(markdown="x" * (config.MAX_PAGE_CHARS + 100), metadata=NS(title="About"))

    def search(self, query, **kw):
        return NS(web=[NS(title="Big", url="https://big.test", description="y" * 30_000)])

    def map(self, url, **kw):
        return NS(links=[NS(url="https://a.test/about", title="About"), NS(url="https://a.test/x", title=None)])


tools = SDKTools(client=FakeFirecrawl())


def test_scrape_is_truncated():
    r = tools.call("scrape_page", {"url": "a.test/about"})
    assert len(r["markdown"]) == config.MAX_PAGE_CHARS and r["truncated"]


def test_search_snippets_are_truncated():
    r = tools.call("web_search", {"query": "q"})
    assert len(r["results"][0]["snippet"]) == config.MAX_SNIPPET_CHARS


def test_map_links_shape():
    r = tools.call("map_site", {"url": "a.test"})
    assert r["links"] == [{"url": "https://a.test/about", "title": "About"}, {"url": "https://a.test/x"}]


def test_unknown_tool_and_url_normalizing():
    assert "error" in tools.call("nope", {})
    assert normalize_url(" a.test ") == "https://a.test"
