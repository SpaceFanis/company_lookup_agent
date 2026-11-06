"""Settings loaded from .env, plus the agent's budget limits."""

import os

from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

MAX_STEPS = 8            # max model turns per research run
MAX_PAGES = 5            # max page scrapes per run (Firecrawl credits)
MAX_PAGE_CHARS = 5_000   # truncate each scraped page to keep context small


def missing_keys() -> list[str]:
    """Names of required keys that are not set."""
    return [name for name, value in
            [("GEMINI_API_KEY", GEMINI_API_KEY), ("FIRECRAWL_API_KEY", FIRECRAWL_API_KEY)]
            if not value]


if __name__ == "__main__":
    missing = missing_keys()
    print("Model:", GEMINI_MODEL)
    print("Missing keys:", ", ".join(missing) if missing else "none - ready")
