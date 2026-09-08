"""Content contract for the MoonSpec Documentation Architecture Standard (MM-904).

Source: MM-900 (Implement MoonSpec Documentation Architecture Standard).
MM-904 adds the authoring conventions: metadata headers, naming conventions,
and the incremental adoption policy to ``docs/DocumentationArchitecture.md``.
"""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _semantic_docs_3964 import assert_semantic_present

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
    assert "docs/<Module>/<ModuleName>ModuleArchitecture.md" in text
    assert "An `Architecture.md` or `Overview.md` inside the module's doc directory" not in text


def test_system_filename_is_durable_and_design_filename_is_transitional() -> None:
    text = _read()
    assert "<SystemName>System.md" in text
    assert_semantic_present(
        text,
        ("durable system or capability description",),
        context="system filename durability",
    )
    assert "<FeatureName>Design.md" in text
    assert_semantic_present(
        text, ("transitional design",), context="design filename transience"
    )
    # Settled designs are promoted or superseded (#3964: exact sentence
    # loosened to the lifecycle contract).
    assert_semantic_present(
        text,
        ("promote or supersede", "settled desired state"),
        context="design promotion lifecycle",
    )


def test_docs_capitalization_variants_covered() -> None:
    text = _read()
    assert "docs/" in text
    assert "Docs/" in text


def test_filename_alone_does_not_define_authority() -> None:
    text = _read()
    # Authority ladder heading (#3964: exact heading loosened; class +
    # declared-authority + precedence ladder is the contract).
    assert_semantic_present(
        text,
        ("filename alone does not define authority",),
        context="filename authority rule",
    )
    # Authority is established by class, declared Authority, and the precedence
    # ladder (§7) -- not by a separate documentation index that does not exist.
    assert "its declared Authority" in text
    # Forbidden patterns.
    assert "Parallel old/new authorities" in text
    assert "contracts/" in text


def test_incremental_adoption_policy_present() -> None:
    text = _read()
    assert "Incremental Adoption Policy" in text
    assert_semantic_present(
        text,
        ("new and substantially-edited docs first",),
        context="incremental adoption scope",
    )
    assert_semantic_present(
        text,
        ("no retroactive metadata-only churn",),
        context="incremental adoption churn guard",
    )


def test_stable_canonical_claim_id_families_present() -> None:
    text = _read()
    assert "Stable Canonical Claim IDs" in text
    for prefix in ("DOC-REQ", "CONTRACT", "INV", "NON-GOAL", "QUALITY", "TEST"):
        assert prefix in text
    assert "PREFIX-NNN" in text


def test_claim_ids_are_distinguished_from_design_req_traceability() -> None:
    text = _read()
    assert "DESIGN-REQ-*" in text
    assert "not stable canonical anchors" in text
    assert "MM-927" in text
    assert "MM-929" in text


def test_claim_id_validation_is_advisory_and_incremental() -> None:
    text = _read()
    assert "malformed IDs" in text
    assert "reused for multiple canonical claims" in text
    assert "Adoption is incremental" in text


def test_downstream_minor_local_adjustment_path_documented() -> None:
    text = _read()
    assert_semantic_present(
        text, ("minor local adjustments",), context="downstream adjustment path"
    )
    assert_semantic_present(
        text,
        ("preserving", "document classes", "authority rules"),
        context="downstream adjustment constraints",
    )


def test_source_traceability_preserved() -> None:
    text = _read()
    assert "MM-900" in text
    assert "MM-904" in text
