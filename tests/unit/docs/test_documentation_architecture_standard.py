"""Content contract for the MoonSpec Documentation Architecture Standard (MM-904).

Source: MM-900 (Implement MoonSpec Documentation Architecture Standard).
MM-904 adds the authoring conventions: metadata headers, naming conventions,
and the incremental adoption policy to ``docs/DocumentationArchitecture.md``.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STANDARD_DOC = REPO_ROOT / "docs" / "DocumentationArchitecture.md"


def _read() -> str:
    return STANDARD_DOC.read_text(encoding="utf-8")


def test_standard_doc_exists() -> None:
    assert STANDARD_DOC.exists()


def test_canonical_metadata_header_fields_present() -> None:
    text = _read()
    for field in (
        "Document Class",
        "Status",
        "Updated",
        "Audience",
        "Authority",
        "Owning Surface",
        "Related Docs",
        "Related Implementation",
    ):
        assert f"**{field}:**" in text, f"missing canonical metadata field: {field}"


def test_imperative_plan_header_fields_present() -> None:
    text = _read()
    for field in ("Canonical Target", "Delete/Archive Trigger"):
        assert f"**{field}:**" in text, f"missing imperative-plan field: {field}"
    # The Document Class value uses the canonical MoonSpec Document Model class
    # name; "plan" is reserved for the concrete type/filename/status, not the class.
    assert "Imperative working document" in text


def test_optional_rationale_section_documented() -> None:
    assert "rationale" in _read().lower()


def test_preferred_filename_set_present() -> None:
    text = _read()
    for suffix in (
        "Architecture.md",
        "ModuleArchitecture.md",
        "System.md",
        "Design.md",
        "Contract.md",
        "Plan.md",
    ):
        assert suffix in text, f"missing preferred filename suffix: {suffix}"


def test_module_architecture_is_preferred_filename() -> None:
    text = _read()
    assert "<ModuleName>ModuleArchitecture.md" in text
    assert "preferred" in text.lower()


def test_docs_capitalization_variants_covered() -> None:
    text = _read()
    assert "docs/" in text
    assert "Docs/" in text


def test_filename_alone_does_not_define_authority() -> None:
    text = _read()
    assert "Filename alone does not define authority" in text
    # Authority is established by class, declared Authority, and the precedence
    # ladder (§7) -- not by a separate documentation index that does not exist.
    assert "its declared Authority" in text
    # Forbidden patterns.
    assert "Parallel old/new authorities" in text
    assert "contracts/" in text


def test_incremental_adoption_policy_present() -> None:
    text = _read()
    assert "Incremental Adoption Policy" in text
    assert "new and substantially-edited docs first" in text.lower()
    assert "no retroactive metadata-only churn PR is mandated" in text


def test_downstream_minor_local_adjustment_path_documented() -> None:
    text = _read()
    assert "minor local adjustments" in text.lower()
    assert "preserving the MoonSpec document classes and authority rules" in text


def test_source_traceability_preserved() -> None:
    text = _read()
    assert "MM-900" in text
    assert "MM-904" in text
