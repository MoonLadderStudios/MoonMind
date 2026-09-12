"""Content contract tests for the MM-888 document-health-remediate skill."""

from pathlib import Path

from moonmind.services.skill_resolution import (
    _load_skill_frontmatter,
    extract_required_capabilities_from_skill_markdown,
)

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-health-remediate" / "SKILL.md"


def _read_skill() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_skill_file_exists() -> None:
    assert _SKILL_PATH.is_file()


def test_front_matter_defines_name_description_and_git_capability() -> None:
    meta = _load_skill_frontmatter(_SKILL_PATH.parent)

    assert meta["name"] == "document-health-remediate"
    assert isinstance(meta["description"], str) and meta["description"].strip()
    assert extract_required_capabilities_from_skill_markdown(
        _read_skill(), skill_name="document-health-remediate"
    ) == ("git",)
