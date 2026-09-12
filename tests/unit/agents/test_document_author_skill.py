"""Content contract tests for the document-author skill."""

from pathlib import Path

from moonmind.services.skill_resolution import (
    _load_skill_frontmatter,
    extract_required_capabilities_from_skill_markdown,
)

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-author" / "SKILL.md"


def _read_skill() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def test_document_author_skill_exists_with_front_matter() -> None:
    meta = _load_skill_frontmatter(_SKILL_PATH.parent)

    assert meta["name"] == "document-author"
    assert isinstance(meta["description"], str) and meta["description"].strip()
    assert extract_required_capabilities_from_skill_markdown(
        _read_skill(), skill_name="document-author"
    ) == ("git",)


def test_document_author_never_creates_docs_native_spec() -> None:
    assert "Do not create `spec.md`" in _read_skill()
