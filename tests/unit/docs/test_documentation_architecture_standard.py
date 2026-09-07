"""Content contract for the MoonSpec Documentation Architecture Standard (MM-904).

Source: MM-900 (Implement MoonSpec Documentation Architecture Standard).
MM-904 adds the authoring conventions: metadata headers, naming conventions,
and the incremental adoption policy to ``docs/DocumentationArchitecture.md``.
"""

import re
from pathlib import Path

from tools.check_documentation_architecture import (
    CANONICAL_CLAIM_PREFIXES,
    metadata_fields,
)
from tools.check_documentation_architecture import (
    main as check_architecture,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
STANDARD_DOC = REPO_ROOT / "docs" / "DocumentationArchitecture.md"


def _read() -> str:
    return STANDARD_DOC.read_text(encoding="utf-8")


def test_standard_doc_exists() -> None:
    assert STANDARD_DOC.exists()


def test_canonical_metadata_header_fields_present() -> None:
    headers = [
        metadata_fields(block)
        for block in re.findall(r"```markdown\n(.*?)\n```", _read(), re.DOTALL)
    ]
    header = next(
        header
        for header in headers
        if header.get("document class") == "Canonical declarative"
    )
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
        assert header[field.lower()], f"missing canonical metadata field: {field}"


def test_imperative_plan_header_fields_present() -> None:
    headers = [
        metadata_fields(block)
        for block in re.findall(r"```markdown\n(.*?)\n```", _read(), re.DOTALL)
    ]
    header = next(
        header
        for header in headers
        if header.get("document class") == "Imperative working document"
    )
    for field in ("Canonical Target", "Delete/Archive Trigger"):
        assert header[field.lower()], f"missing imperative-plan field: {field}"
    # The Document Class value uses the canonical MoonSpec Document Model class
    # name; "plan" is reserved for the concrete type/filename/status, not the class.
    assert header["document class"] == "Imperative working document"


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


def test_system_filename_is_durable_and_design_filename_is_transitional() -> None:
    text = _read()
    assert "<SystemName>System.md" in text
    assert "durable system or capability description" in text
    assert "<FeatureName>Design.md" in text
    assert "transitional design" in text
    assert (
        "promote or supersede it when the design becomes settled desired state" in text
    )


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


def test_stable_canonical_claim_id_families_present() -> None:
    text = _read()
    for prefix in CANONICAL_CLAIM_PREFIXES:
        assert prefix in text
    assert "PREFIX-NNN" in text


def test_claim_ids_are_distinguished_from_design_req_traceability() -> None:
    text = _read()
    assert "DESIGN-REQ-*" in text
    assert "not stable canonical anchors" in text
    assert "MM-927" in text
    assert "MM-929" in text


def test_claim_id_validation_is_advisory_by_default(
    tmp_path, monkeypatch, capsys
) -> None:
    # Exercise the existing operator entrypoint instead of a sentence claiming
    # it is advisory. The separate mutation suite pins detection itself.
    from tools import check_documentation_architecture as checker

    root = tmp_path / "docs"
    root.mkdir()
    (root / "Example.md").write_text("# Example\n### INV-bad Claim\n", encoding="utf-8")
    monkeypatch.setattr(checker, "all_doc_paths", lambda: ["docs/Example.md"])
    load_docs = checker.load_docs
    monkeypatch.setattr(
        checker, "load_docs", lambda paths: load_docs(paths, root=tmp_path)
    )
    assert check_architecture(["--scope", "all", "--format", "json"]) == 0
    assert "malformed-claim-id" in capsys.readouterr().out


def test_downstream_minor_local_adjustment_path_documented() -> None:
    text = _read()
    assert "preserving the MoonSpec document classes and authority rules" in text


def test_source_traceability_preserved() -> None:
    text = _read()
    assert "MM-900" in text
    assert "MM-904" in text
