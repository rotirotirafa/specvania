"""T39: offline evidence for the repository's community entry points.

PyYAML is already provided by the locked uvicorn[standard] dependency.
The form checks cover the GitHub schema subset used here, including nested
required flags; parsing alone would not prove that the forms are usable.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_readme import HEADING_PAIRS, headings, section

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/rotirotirafa/specvania"
SPONSOR = "https://github.com/sponsors/rotirotirafa"
FORMS = ("bug", "feature", "room")


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_yaml(relative: str) -> dict[str, Any]:
    document = yaml.safe_load(read(relative))
    assert isinstance(document, dict)
    return document


def form_fields(name: str) -> dict[str, Any]:
    return {
        field["id"]: field
        for field in load_yaml(f".github/ISSUE_TEMPLATE/{name}.yml")["body"]
        if field["type"] != "markdown"
    }


def test_issue_form_inventory_and_unique_names() -> None:
    directory = ROOT / ".github/ISSUE_TEMPLATE"
    assert {path.name for path in directory.iterdir()} == {
        "bug.yml",
        "feature.yml",
        "room.yml",
        "config.yml",
    }
    names = [load_yaml(f".github/ISSUE_TEMPLATE/{name}.yml")["name"] for name in FORMS]
    assert len(set(names)) == len(FORMS)


@pytest.mark.parametrize("name", FORMS)
def test_issue_forms_are_valid_yaml_and_follow_github_schema(name: str) -> None:
    form = load_yaml(f".github/ISSUE_TEMPLATE/{name}.yml")
    assert {"name", "description", "labels", "body"} <= form.keys()
    assert form.keys() <= {"name", "description", "labels", "body", "title"}
    for key in ("name", "description", "title"):
        assert isinstance(form[key], str) and form[key].strip()
    assert isinstance(form["labels"], list) and form["labels"]
    assert all(isinstance(label, str) and label.strip() for label in form["labels"])
    assert isinstance(form["body"], list) and form["body"]
    ids: set[str] = set()
    labels: set[str] = set()
    for field in form["body"]:
        assert isinstance(field, dict)
        assert {"type", "attributes"} <= field.keys()
        assert field.keys() <= {"type", "id", "attributes", "validations"}
        kind = field["type"]
        assert kind in {"markdown", "input", "textarea", "dropdown", "checkboxes"}
        attributes = field["attributes"]
        assert isinstance(attributes, dict)
        if kind == "markdown":
            assert attributes.keys() == {"value"}
            assert isinstance(attributes["value"], str) and attributes["value"].strip()
            assert "validations" not in field
            continue
        assert isinstance(field["id"], str)
        assert re.fullmatch(r"[A-Za-z0-9_-]+", field["id"])
        assert field["id"] not in ids
        ids.add(field["id"])
        assert isinstance(attributes["label"], str) and attributes["label"].strip()
        assert attributes["label"] not in labels
        labels.add(attributes["label"])
        allowed = {"label", "description"}
        if kind in {"input", "textarea"}:
            allowed |= {"placeholder", "value"}
        if kind == "textarea":
            allowed.add("render")
        if kind in {"dropdown", "checkboxes"}:
            allowed.add("options")
        assert attributes.keys() <= allowed
        for key, value in attributes.items():
            if key != "options":
                assert isinstance(value, str) and value.strip()
        if kind in {"dropdown", "checkboxes"}:
            options = attributes["options"]
            assert isinstance(options, list) and options
            if kind == "dropdown":
                assert all(
                    isinstance(option, str) and option.strip() for option in options
                )
                assert len(options) == len(set(options))
            else:
                for option in options:
                    assert option.keys() == {"label", "required"}
                    assert isinstance(option["label"], str) and option["label"].strip()
                    assert option["required"] is True
        if kind != "checkboxes" or "validations" in field:
            assert field["validations"] == {"required": True}
            assert field["validations"]["required"] is True


@pytest.mark.parametrize(
    ("name", "label", "required_fields"),
    [
        (
            "bug",
            "bug",
            (
                ("version", "input"),
                ("os", "input"),
                ("surface", "dropdown"),
                ("steps", "textarea"),
                ("expected", "textarea"),
                ("actual", "textarea"),
                ("doctor", "textarea"),
                ("redaction", "checkboxes"),
            ),
        ),
        (
            "feature",
            "enhancement",
            (
                ("problem", "textarea"),
                ("proposal", "textarea"),
                ("alternatives", "textarea"),
            ),
        ),
        (
            "room",
            "room-proposal",
            (
                ("area", "input"),
                ("title", "input"),
                ("depends_on", "input"),
                ("covers", "input"),
                ("files", "textarea"),
                ("checklist", "textarea"),
            ),
        ),
    ],
)
def test_issue_forms_have_required_fields_labels_and_redaction_reminders(
    name: str, label: str, required_fields: tuple[tuple[str, str], ...]
) -> None:
    form = load_yaml(f".github/ISSUE_TEMPLATE/{name}.yml")
    assert set(form["labels"]) == {label, "needs-triage"}
    fields = form_fields(name)
    for field_id, kind in required_fields:
        field = fields[field_id]
        assert field["type"] == kind
        if kind == "checkboxes":
            assert all(
                option["required"] is True for option in field["attributes"]["options"]
            )
        else:
            assert field["validations"]["required"] is True
    reminder = " ".join(
        field["attributes"]["value"]
        for field in form["body"]
        if field["type"] == "markdown"
    ).lower()
    for term in ("remove", "api key", "tokens", "security policy"):
        assert term in reminder
    assert f"{REPOSITORY}/security/policy" in reminder


def test_bug_form_requests_diagnostics_and_requires_redaction_confirmation() -> None:
    fields = form_fields("bug")
    assert "specvania --version" in fields["version"]["attributes"]["description"]
    assert fields["surface"]["attributes"]["options"] == [
        "Desktop app",
        "CLI",
        "Browser tab",
    ]
    doctor = fields["doctor"]["attributes"]
    assert "specvania doctor" in doctor["label"]
    assert "redact" in doctor["description"].lower()
    assert doctor["render"] == "text"
    confirmation = fields["redaction"]["attributes"]["options"]
    assert len(confirmation) == 1
    assert confirmation[0]["required"] is True
    text = confirmation[0]["label"].lower()
    for term in ("i have removed", "api key", "tokens", "logs", "screenshots"):
        assert term in text


def test_room_proposal_explains_world_fields_and_binary_checklist() -> None:
    fields = form_fields("room")
    for field_id, term in (
        ("area", "A<n>"),
        ("depends_on", "T<nn>"),
        ("covers", "S<n>"),
    ):
        assert term in fields[field_id]["attributes"]["description"]
    for field_id in ("depends_on", "covers"):
        assert "none" in fields[field_id]["attributes"]["description"]
    assert "paths" in fields["files"]["attributes"]["description"]
    checklist = fields["checklist"]["attributes"]
    for term in ("binary", "test", "manual"):
        assert term in checklist["description"]
    assert "- [ ]" in checklist["placeholder"]
    assert "works better" not in checklist["placeholder"]


def test_issue_config_disables_blank_issues_and_links_security_policy() -> None:
    config = load_yaml(".github/ISSUE_TEMPLATE/config.yml")
    assert config.keys() == {"blank_issues_enabled", "contact_links"}
    assert config["blank_issues_enabled"] is False
    assert isinstance(config["contact_links"], list) and config["contact_links"]
    for link in config["contact_links"]:
        assert link.keys() == {"name", "url", "about"}
        assert all(isinstance(value, str) and value.strip() for value in link.values())
    assert any(
        link["url"] == f"{REPOSITORY}/security/policy"
        for link in config["contact_links"]
    )


def test_pr_template_covers_all_gates_applicability_screenshots_and_deviations() -> (
    None
):
    text = read(".github/pull_request_template.md")
    assert "What changed" in text and "Room id" in text
    assert "not applicable" in text and "reason" in text
    gates = dict(re.findall(r"^- \[ \] (G\d+) — (.+)$", text, re.MULTILINE))
    assert set(gates) == {f"G{number}" for number in range(1, 11)}
    for gate, term in (
        ("G1", "ruff check ."),
        ("G2", "ruff format --check ."),
        ("G3", "pyright"),
        ("G4", "socket guard"),
        ("G5", "evidence"),
        ("G6", "screenshot"),
        ("G7", "specvania lint docs/"),
        ("G8", "owner"),
        ("G9", "Postgres"),
        ("G10", "npm --prefix apps/desktop run check"),
    ):
        assert term in gates[gate]
    assert "UI changes" in gates["G6"]
    assert "Spec intent changed?" in text
    for term in (
        ".agent/deviations.md",
        "Approved by: pending",
        "next free DEV number",
    ):
        assert term in text


def test_code_of_conduct_retains_covenant_21_sections_and_private_owner_contact() -> (
    None
):
    text = read("CODE_OF_CONDUCT.md")
    assert text.startswith("# Contributor Covenant Code of Conduct\n")
    assert "version 2.1" in text
    assert (
        "https://www.contributor-covenant.org/version/2/1/code_of_conduct.html" in text
    )
    # All policy sections and all four enforcement levels must retain real text.
    for title in (
        "Our Pledge",
        "Our Standards",
        "Enforcement Responsibilities",
        "Scope",
        "Enforcement",
        "Enforcement Guidelines",
        "Attribution",
    ):
        body = text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]
        assert len(body.split()) >= 20
    for title in (
        "1. Correction",
        "2. Warning",
        "3. Temporary Ban",
        "4. Permanent Ban",
    ):
        body = text.split(f"### {title}\n", 1)[1].split("\n#", 1)[0]
        assert "**Community Impact**:" in body and "**Consequence**:" in body
    assert "[@rotirotirafa](https://github.com/rotirotirafa)" in text
    assert "private contact" in text
    assert f"{REPOSITORY}/security/advisories/new" in text
    assert "Owner email (optional):" in text
    assert "[INSERT CONTACT METHOD]" not in text
    assert not re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)


def test_security_policy_supports_03_and_explains_private_redacted_reports() -> None:
    text = read("SECURITY.md")
    assert "| 0.3.x | Supported |" in text
    assert "| < 0.3 | Not supported |" in text
    assert "GitHub private vulnerability reporting" in text
    assert "Security → Report a vulnerability" in text
    assert f"{REPOSITORY}/security/advisories/new" in text
    prose = " ".join(text.split())
    for term in (
        "specvania --version",
        "operating system",
        "reproduce",
        "impact",
        "redacted",
    ):
        assert term in prose
    assert "Do not include live API keys" in text and "tokens" in text
    assert "Do not open a public issue" in text
    assert "best effort" in text and "7 days" in text and "not a guarantee" in text
    assert "https://github.com/rotirotirafa" in text


def test_funding_names_owner() -> None:
    assert load_yaml(".github/FUNDING.yml") == {"github": "rotirotirafa"}


@pytest.mark.parametrize("name", ["README.md", "README.pt-BR.md"])
def test_readmes_keep_translated_community_sections_and_links(name: str) -> None:
    assert HEADING_PAIRS[-2:] == (
        ("##", "Contributing", "Como contribuir"),
        ("##", "Sponsor", "Apoie o projeto"),
    )
    text = read(name)
    language_column = 1 if name == "README.md" else 2
    assert headings(text) == [
        (pair[0], pair[language_column]) for pair in HEADING_PAIRS
    ]
    contributing = section(text, "Contributing", name)
    for link in ("CONTRIBUTING.md", f"{REPOSITORY}/issues/new/choose"):
        assert f"]({link})" in contributing
    assert SPONSOR in section(text, "Sponsor", name)


@pytest.mark.parametrize("name", ["README.md", "README.pt-BR.md"])
def test_readmes_have_ci_pypi_and_license_badges_at_top(name: str) -> None:
    top = read(name).split("\n## ", 1)[0]
    badges = re.findall(r"\[!\[([^]]+)\]\(([^)]+)\)\]\(([^)]+)\)", top)
    assert len(badges) == 3
    assert {(source, target) for _, source, target in badges} == {
        (
            f"{REPOSITORY}/actions/workflows/ci.yml/badge.svg",
            f"{REPOSITORY}/actions/workflows/ci.yml",
        ),
        (
            "https://img.shields.io/pypi/v/specvania.svg",
            "https://pypi.org/project/specvania/",
        ),
        ("https://img.shields.io/badge/license-MIT-blue.svg", "LICENSE"),
    }
    assert all(label.strip() for label, _, _ in badges)


def test_contributing_explains_setup_room_workflow_safety_and_sponsorship() -> None:
    text = read("CONTRIBUTING.md")
    for term in (
        "Python 3.12",
        "uv sync --locked",
        'export PATH="$PWD/.venv/bin:$PATH"',
        "Node",
        "npm --prefix apps/desktop run check",
        "node --test",
        "docker compose up -d --wait postgres",
        "uv sync --locked --extra postgres",
        "SPECVANIA_TEST_DATABASE_URL=",
        "optional",
        "D1–D35",
        "one authorized room",
        "one commit per room",
        "next free DEV number",
        "Approved by: pending",
        "no network in tests",
        "no real API keys",
        "socket guard",
        "tests/conftest.py",
        "provider/fake.py",
        "FakeClock",
    ):
        assert term in text
    for command in (
        "ruff check .",
        "ruff format --check .",
        "pyright",
        "pytest -q",
        "specvania lint docs/",
    ):
        assert command in text
    for path in (
        "docs/conventions.md",
        "docs/spec.md",
        "docs/tasks.md",
        ".agent/validation-gates.md",
        ".agent/deviations.md",
    ):
        assert f"]({path})" in text
    for number in range(1, 11):
        assert f"| G{number} |" in text
    assert f"{REPOSITORY}/issues/new?template=room.yml" in text
    for field in ("area", "title", "depends_on", "covers", "files", "binary"):
        assert field in text
    assert SPONSOR in text
