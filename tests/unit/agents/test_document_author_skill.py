"""Content contract tests for the MM-931 document-author skill."""

from pathlib import Path

import yaml

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"
_SKILL_PATH = _SKILLS_DIR / "document-author" / "SKILL.md"


def _read_skill() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


def _front_matter(text: str) -> dict:
    assert text.startswith("---\n"), "SKILL.md must begin with YAML front matter"
    _, raw, _ = text.split("---\n", 2)
    return yaml.safe_load(raw)


def test_document_author_skill_exists_with_front_matter() -> None:
    meta = _front_matter(_read_skill())

    assert meta["name"] == "document-author"
    assert "location, filename, viewpoint template, metadata header" in meta["description"]
    assert meta["metadata"]["required-capabilities"] == ["git"]


def test_document_author_chooses_docs_architecture_fields() -> None:
    text = _read_skill()

    for expected in (
        "document class",
        "location",
        "filename",
        "viewpoint template",
        "metadata header",
        "stable claims",
        "embedded rationale",
    ):
        assert expected in text


def test_document_author_routes_broad_work_to_docs_tmp() -> None:
    text = _read_skill()

    assert "If the request is broad" in text
    assert "docs/tmp/" in text
    assert "improvement plan" in text


def test_document_author_never_creates_docs_native_spec() -> None:
    text = _read_skill()

    assert "Do not create `spec.md`" in text
    assert "or writes under `specs/`" in text
    assert "Confirmation that no docs-native `spec.md` was created" in text


def test_document_author_preserves_mm_931_and_mm_927_traceability() -> None:
    text = _read_skill()

    assert "MM-931" in text
    assert "MM-927" in text
