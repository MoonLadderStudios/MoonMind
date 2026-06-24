"""Coverage for the MM-906 viewpoint templates and their reference from the standard.

Traceability: MM-906 (templates) / source issue MM-900 (DocumentationArchitecture standard).
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
STANDARD = REPO_ROOT / "docs" / "DocumentationArchitecture.md"
VIEWPOINTS_DIR = REPO_ROOT / "docs" / "_viewpoints"

CANONICAL_TEMPLATES = {
    "SystemArchitectureView.template.md": "System Architecture View",
    "ModuleArchitectureView.template.md": "Module Architecture View",
    "SystemFeatureDesignView.template.md": "System / Feature Design View",
    "ModuleContractSpecification.template.md": "Module Contract Specification",
}
PLAN_TEMPLATE = "MigrationImplementationPlan.template.md"
ALL_TEMPLATES = tuple(CANONICAL_TEMPLATES) + (PLAN_TEMPLATE,)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ALL_TEMPLATES)
def test_template_exists(filename: str) -> None:
    assert (VIEWPOINTS_DIR / filename).is_file()


@pytest.mark.parametrize(("filename", "viewpoint"), list(CANONICAL_TEMPLATES.items()))
def test_canonical_template_embeds_canonical_header(filename: str, viewpoint: str) -> None:
    text = _read(VIEWPOINTS_DIR / filename)
    assert "**Document Class:** Canonical declarative" in text
    assert f"**Viewpoint:** {viewpoint}" in text
    # Canonical viewpoints must not carry the imperative-plan markers.
    assert "Imperative working document" not in text


def test_plan_template_embeds_imperative_plan_header() -> None:
    text = _read(VIEWPOINTS_DIR / PLAN_TEMPLATE)
    assert "**Document Class:** Imperative working document" in text
    assert "**Location policy:**" in text
    assert "**Tracks:**" in text
    assert "Canonical declarative" not in text


@pytest.mark.parametrize("filename", ALL_TEMPLATES)
def test_standard_references_each_template(filename: str) -> None:
    standard_text = _read(STANDARD)
    assert f"./_viewpoints/{filename}" in standard_text


def test_standard_defines_both_headers_and_traceability() -> None:
    text = _read(STANDARD)
    assert "Canonical metadata header" in text
    assert "Imperative-plan header" in text
    # Source-issue traceability is preserved in the standard.
    assert "MM-900" in text
    assert "MM-906" in text
