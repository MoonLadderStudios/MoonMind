from pathlib import Path

from moonmind.services.skill_resolution import (
    extract_required_skill_names_from_skill_markdown,
)

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-health-review" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_document_health_review_skill_exists() -> None:
    assert _SKILL_PATH.is_file()


def test_document_health_review_front_matter() -> None:
    text = _skill_text()

    assert text.startswith("---\n")
    assert "name: document-health-review" in text
    # The description must advertise the disposition vocabulary the skill produces.
    assert "kept, updated, merged, split, moved, archived, or deleted" in text
    assert "required-capabilities:" in text
    assert "- git" in text


def test_document_health_review_defines_all_eight_questions() -> None:
    text = _skill_text()

    assert "## Review Questions" in text
    for question in (
        "Is this document still needed?",
        "unimplemented or out-of-date with the codebase?",
        "align with the strategy defined by the README, constitution",
        "simplified without a loss of key application functionality?",
        "follow engineering best practices?",
        "merged into another document?",
        "split into multiple documents?",
        "right sub-directory",
    ):
        assert question in text, question


def test_document_health_review_is_review_only_by_default() -> None:
    text = _skill_text()

    assert "Review-only by default" in text
    assert "Never commit, push, or open pull requests" in text


def test_document_health_review_declares_disposition_verdicts() -> None:
    text = _skill_text()

    assert "## Verdict" in text
    assert "Keep / Update / Merge / Split / Move / Archive / Delete" in text


def test_document_health_review_split_threshold_rule() -> None:
    text = _skill_text()

    assert "line_count > 2000" in text
    assert "default recommendation = split" in text


def test_document_health_review_lists_non_goals() -> None:
    text = _skill_text()

    assert "## Non-Goals" in text
    # Excluded first-class dimensions that must not become scoring categories.
    for excluded in ("audience", "ownership", "agent-safety", "success criteria"):
        assert excluded in text, excluded


def test_document_health_review_canonical_reference_discovery() -> None:
    text = _skill_text()

    assert "## Canonical Reference Discovery" in text
    assert "README.md" in text
    assert ".specify/memory/constitution.md" in text
    assert "docs/MoonMindArchitecture.md" in text


def test_document_health_review_severity_and_failure_modes() -> None:
    text = _skill_text()

    assert "## Severity" in text
    for level in ("P0", "P1", "P2", "P3"):
        assert level in text, level
    assert "## Failure Modes" in text
    # Code evidence gaps classify as ambiguous, never stale.
    assert "classify the claim as `ambiguous`, not `stale`" in text


def test_document_health_review_declares_document_update_dependency() -> None:
    text = _skill_text()

    required_skills = extract_required_skill_names_from_skill_markdown(
        text,
        skill_name="document-health-review",
    )

    assert required_skills == ("document-update",)
    assert (_SKILLS_DIR / "document-update" / "SKILL.md").is_file()
    # It still inlines the drift-analysis mechanics it relies on.
    assert "drift" in text.lower()
