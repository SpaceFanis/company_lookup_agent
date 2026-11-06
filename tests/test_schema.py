"""CompanyProfile validation, error formatting, markdown and declaration tests."""

from company_agent.schema import CompanyProfile, ProfileSubmitter

BASE = {"name": "Acme", "website": "https://acme.test", "one_liner": "Anvils.",
        "description": "Makes anvils.", "sources": ["https://acme.test/about"]}

submitter = ProfileSubmitter()


def test_valid_minimal_profile():
    profile, error = submitter.validate(BASE)
    assert error is None and profile.name == "Acme" and profile.key_people == []


def test_nested_people_and_extra_fields():
    profile, error = submitter.validate({**BASE, "key_people": [{"name": "Ann", "role": "CEO"}],
                                         "made_up_field": 1})
    assert error is None and profile.key_people[0].role == "CEO"


def test_errors_are_short_and_name_fields():
    _, error = submitter.validate({**BASE, "name": "  ", "sources": ["acme.test"], "founded_year": 1500})
    assert "name: must not be empty" in error
    assert "sources: must be full http(s) URLs" in error
    assert "founded_year: must be between 1800" in error
    assert "Value error" not in error


def test_markdown_rendering():
    profile = CompanyProfile.model_validate({**BASE, "founded_year": 1949, "competitors": ["Globex"],
                                             "key_people": [{"name": "Ann", "role": "CEO"}]})
    md = profile.to_markdown()
    assert md.startswith("# Acme")
    assert "- **Founded:** 1949" in md
    assert "- Ann — CEO" in md
    assert "## Funding\n_Unknown_" in md


def test_declaration_uses_the_pydantic_schema():
    params = ProfileSubmitter.declaration.parameters_json_schema
    assert ProfileSubmitter.declaration.name == "submit_company_profile"
    assert set(params["required"]) == {"name", "website", "one_liner", "description", "sources"}
    assert "Person" in params["$defs"]


def test_placeholder_strings_become_none():
    profile, _ = submitter.validate({**BASE, "confidence_notes": "None", "industry": " N/A ",
                                     "headquarters": "Paris"})
    assert profile.confidence_notes is None and profile.industry is None
    assert profile.headquarters == "Paris"
