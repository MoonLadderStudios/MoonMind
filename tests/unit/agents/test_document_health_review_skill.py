from pathlib import Path

from moonmind.services.skill_resolution import (
    _load_skill_frontmatter,
    extract_required_capabilities_from_skill_markdown,
)

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-health-review" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_document_health_review_skill_exists() -> None:
    assert _SKILL_PATH.is_file()


def test_document_health_review_front_matter() -> None:
    text = _skill_text()

    meta = _load_skill_frontmatter(_SKILL_PATH.parent)
    assert meta["name"] == "document-health-review"
    assert isinstance(meta["description"], str) and meta["description"].strip()
    assert extract_required_capabilities_from_skill_markdown(
        text, skill_name="document-health-review"
    ) == ("git",)
