"""The CompanyProfile schema and the terminal tool that submits it.

One Pydantic model does three jobs:
  1. Its JSON schema becomes the parameters of `submit_company_profile`,
     so Gemini knows exactly what to send.
  2. It validates what Gemini actually sent. Errors go back to the model,
     which fixes them and resubmits (self-correction).
  3. It is the app's result type (rendered in the UI, downloaded as JSON/Markdown).
"""

from datetime import date
from typing import Any

from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PLACEHOLDERS = {"", "none", "null", "n/a", "na", "unknown", "-"}


class Person(BaseModel):
    name: str = Field(description="Full name")
    role: str | None = Field(None, description="Title or role, e.g. 'Co-founder & CEO'")


class CompanyProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="Company name")
    website: str = Field(description="Main website URL")
    one_liner: str = Field(description="What the company does, in one sentence")
    description: str = Field(description="2-4 sentence overview")
    industry: str | None = Field(None, description="Industry or category")
    products: list[str] = Field(default_factory=list, description="Main products or services")
    target_customers: list[str] = Field(default_factory=list, description="Who the customers are")
    business_model: str | None = Field(None, description="How it makes money, incl. pricing if known")
    headquarters: str | None = Field(None, description="City, country")
    founded_year: int | None = Field(None, description="Year founded, e.g. 2019")
    team_size: str | None = Field(None, description="Employee estimate, e.g. '50-100', with the basis")
    hiring_signals: str | None = Field(None, description="Hiring activity seen on careers pages")
    key_people: list[Person] = Field(default_factory=list, description="Founders and leaders")
    funding: list[str] = Field(default_factory=list,
                               description="Funding rounds/highlights, e.g. 'Series B, $75M, 2026, led by X'")
    recent_news: list[str] = Field(default_factory=list, description="Notable recent news, each with a date")
    competitors: list[str] = Field(default_factory=list, description="Main competitors")
    social_links: list[str] = Field(default_factory=list, description="LinkedIn, X, GitHub etc. URLs")
    sources: list[str] = Field(description="URLs of every page or search result used. At least one.")
    confidence_notes: str | None = Field(
        None, description="What is uncertain, estimated, or could not be found")

    @field_validator("industry", "business_model", "headquarters", "team_size",
                     "hiring_signals", "confidence_notes", mode="before")
    @classmethod
    def placeholder_to_none(cls, v: Any) -> Any:
        """Models sometimes write the string "None" or "N/A" instead of null."""
        if isinstance(v, str) and v.strip().lower() in PLACEHOLDERS:
            return None
        return v

    @field_validator("name", "website", "one_liner", "description")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("founded_year")
    @classmethod
    def plausible_year(cls, v: int | None) -> int | None:
        if v is not None and not 1800 <= v <= date.today().year:
            raise ValueError(f"must be between 1800 and {date.today().year}")
        return v

    @field_validator("sources")
    @classmethod
    def sources_are_urls(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("cite at least one source URL")
        bad = [s for s in v if not s.startswith(("http://", "https://"))]
        if bad:
            raise ValueError(f"must be full http(s) URLs, got: {bad}")
        return v

    def to_markdown(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {i}" for i in items) if items else "_Unknown_"

        facts = [("Website", self.website), ("Industry", self.industry),
                 ("Headquarters", self.headquarters), ("Founded", self.founded_year),
                 ("Team size", self.team_size), ("Hiring", self.hiring_signals),
                 ("Business model", self.business_model)]
        people = [f"{p.name} — {p.role}" if p.role else p.name for p in self.key_people]
        sections = [
            f"# {self.name}\n\n**{self.one_liner}**\n\n{self.description}",
            "\n".join(f"- **{k}:** {v}" for k, v in facts if v not in (None, "")),
            f"## Products\n{bullets(self.products)}",
            f"## Customers\n{bullets(self.target_customers)}",
            f"## Key people\n{bullets(people)}",
            f"## Funding\n{bullets(self.funding)}",
            f"## Recent news\n{bullets(self.recent_news)}",
            f"## Competitors\n{bullets(self.competitors)}",
            f"## Social\n{bullets(self.social_links)}",
            f"## Sources\n{bullets(self.sources)}",
        ]
        if self.confidence_notes:
            sections.append(f"## Confidence notes\n{self.confidence_notes}")
        return "\n\n".join(sections) + "\n"


class ProfileSubmitter:
    """The terminal tool: `submit_company_profile`, whose parameters ARE the CompanyProfile fields."""

    declaration = types.FunctionDeclaration(
        name="submit_company_profile",
        description=("Finish the research and submit the company profile. Use null or empty "
                     "lists for anything you could not find; never guess."),
        parameters_json_schema=CompanyProfile.model_json_schema(),
    )

    def validate(self, args: dict[str, Any]) -> tuple[CompanyProfile | None, str | None]:
        try:
            return CompanyProfile.model_validate(args), None
        except ValidationError as exc:
            return None, format_errors(exc)


def format_errors(exc: ValidationError) -> str:
    """Short, model-readable error list, e.g. "founded_year: must be between 1800 and 2026"."""
    lines = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"{where}: {err['msg'].removeprefix('Value error, ')}")
    return "; ".join(lines)
