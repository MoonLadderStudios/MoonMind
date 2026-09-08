"""Structured examples and identifiers in the documentation standard.

Source: MM-900 (Implement MoonSpec Documentation Architecture Standard).
MM-904 adds the authoring conventions: metadata headers, naming conventions,
and the incremental adoption policy to ``docs/DocumentationArchitecture.md``.
"""

from pathlib import Path

from tools.check_documentation_architecture import CANONICAL_CLAIM_PREFIXES

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
    assert "docs/<Module>/<ModuleName>ModuleArchitecture.md" in text


def test_system_and_design_filename_examples_present() -> None:
    text = _read()
    assert "<SystemName>System.md" in text
    assert "<FeatureName>Design.md" in text


def test_docs_capitalization_variants_covered() -> None:
    text = _read()
    assert "docs/" in text
    assert "Docs/" in text


def test_global_contract_directory_policy_is_documented() -> None:
    # Retain this policy token: the advisory checker does not enforce the
    # standard's prohibition on a competing global contracts directory.
    assert "contracts/" in _read()


def test_stable_canonical_claim_id_families_present() -> None:
    text = _read()
    for prefix in CANONICAL_CLAIM_PREFIXES:
        assert prefix in text
    assert "PREFIX-NNN" in text


def test_claim_ids_are_distinguished_from_design_req_traceability() -> None:
    text = _read()
    assert "DESIGN-REQ-*" in text
    assert "MM-927" in text
    assert "MM-929" in text


def test_source_traceability_preserved() -> None:
    text = _read()
    assert "MM-900" in text
    assert "MM-904" in text


def test_explanatory_rewording_preserves_structured_examples(monkeypatch) -> None:
    text = _read()
    rewritten = text.replace(
        "durable system or capability description", "long-lived capability overview"
    ).replace(
        "no retroactive metadata-only churn PR is mandated",
        "existing documents need no metadata-only update",
    )
    assert rewritten != text
    monkeypatch.setattr(f"{__name__}._read", lambda: rewritten)
    test_system_and_design_filename_examples_present()
    test_canonical_metadata_header_fields_present()
    test_stable_canonical_claim_id_families_present()
