"""Content contract tests for the MM-888 document-health-remediate skill."""

from pathlib import Path

import yaml

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-health-remediate" / "SKILL.md"


def _read_skill() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _front_matter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must begin with YAML front matter"
    _, raw, _ = text.split("---\n", 2)
    return yaml.safe_load(raw)


def test_skill_file_exists() -> None:
    assert _SKILL_PATH.is_file()


def test_front_matter_defines_name_description_and_git_capability() -> None:
    meta = _front_matter(_read_skill())

    assert meta["name"] == "document-health-remediate"
    assert "document-health-review" in meta["description"]
    # The description enumerates the remediation actions.
    for action in ("updating", "merging", "splitting", "moving", "archiving", "deleting"):
        assert action in meta["description"]
    assert meta["metadata"]["required-capabilities"] == ["git"]


def test_skill_is_report_driven_but_evidence_validated() -> None:
    text = _read_skill()

    assert "report-driven but evidence-validated" in text
    assert "action ledger" in text
    # Re-check against current checkout, preserve before destructive, summarize.
    assert "current checkout" in text
    assert "Preserve" in text or "preserve" in text
    assert "before destructive" in text


def test_inputs_section_documents_required_and_optional_inputs() -> None:
    text = _read_skill()

    assert "## Inputs" in text
    assert "document-health-review` report" in text
    assert "file path or pasted content" in text
    # Optional inputs.
    assert "Scope limit" in text
    assert "Allowed actions" in text
    assert "Disallowed actions" in text
    assert "destructive actions are allowed" in text
    assert "archive directory" in text
    assert "Validation commands" in text
    # Example invocations.
    assert "Use document-health-remediate on reports/docs-health.md." in text


def test_supported_action_types_and_safety_rules_documented() -> None:
    text = _read_skill()

    assert "## Supported Actions" in text
    for heading in (
        "### update",
        "### merge",
        "### split",
        "### move",
        "### archive",
        "### delete",
        "### reference repair",
    ):
        assert heading in text

    # Merge preservation check, split >2000-line rule, conservative delete.
    assert "preservation check" in text
    assert "line_count > 2000" in text
    assert "most conservative action" in text


def test_remediation_handles_docs_architecture_defects_and_broad_work() -> None:
    text = _read_skill()

    for expected in (
        "missing metadata",
        "unclear authority",
        "missing embedded rationale",
        "duplicate contract",
        "imperative leakage",
        "unverifiable canonical claims",
    ):
        assert expected in text
    assert "docs/tmp/" in text
    assert "improvement plan" in text


def test_high_level_workflow_has_thirteen_safe_ordered_steps() -> None:
    text = _read_skill()

    assert "## Workflow" in text
    for step in range(1, 14):
        assert f"{step}. " in text
    assert "13. Produce final remediation summary." in text
    assert "Do not delete, archive, or move files before preserving content" in text


def test_detailed_workflow_includes_phase_zero_preflight() -> None:
    text = _read_skill()

    assert "### Phase 0: Preflight" in text
    assert "working tree" in text
    assert "`docs/`" in text
    assert "`Docs/`" in text
    assert "both, if present" in text
    assert "documentation conventions" in text
