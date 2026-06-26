from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parents[3] / ".agents" / "skills"


def test_moonspec_doc_reconcile_skill_defines_gate_and_output_contract() -> None:
    text = (_SKILLS_DIR / "moonspec-doc-reconcile" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "name: moonspec-doc-reconcile" in text
    assert "FULLY_IMPLEMENTED" in text
    assert "## Update Gate" in text
    assert "definitely requires" in text
    assert "**Function**" in text
    assert "**Consistency**" in text
    assert "**Best practices**" in text
    assert "no_update_required" in text
    assert "escalated" in text
    assert "docs/Workflows/MoonSpecDocumentModel.md" in text
    assert "Never commit, push, or create pull requests" in text


def test_moonspec_implement_skill_defines_discovery_ledger() -> None:
    text = (_SKILLS_DIR / "moonspec-implement" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "## Discovery Ledger" in text
    assert "artifacts/doc-discoveries/<feature>.json" in text
    assert '"severity": "definite | possible"' in text
    assert "moonspec-doc-reconcile" in text


def test_moonspec_verify_skill_reports_source_document_drift() -> None:
    text = (_SKILLS_DIR / "moonspec-verify" / "SKILL.md").read_text(encoding="utf-8")

    assert "## Source Document Drift" in text
    assert "moonspec-doc-reconcile" in text
    assert "does not block `FULLY_IMPLEMENTED`" in text


def test_moonspec_breakdown_skill_classifies_input_documents() -> None:
    text = (_SKILLS_DIR / "moonspec-breakdown" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "## Input Classification" in text
    assert "canonical-declarative" in text
    assert "imperative-override" in text
    assert "sourceDocumentClass" in text
    assert "docs/Workflows/MoonSpecDocumentModel.md" in text


def test_moonspec_breakdown_skill_requires_stable_canonical_claim_ids() -> None:
    text = (_SKILLS_DIR / "moonspec-breakdown" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "stable canonical claim IDs" in text
    assert "sourceReference.claimIds" in text
    assert "path-anchored IDs" in text
    assert "do not fabricate canonical claim IDs" in text
    assert (
        "coverage matrix from every selected canonical claim ID and every"
        in text
    )


def test_moonspec_specify_skill_marks_specs_derived_from_claims() -> None:
    text = (_SKILLS_DIR / "moonspec-specify" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`**Derived From Canonical Claims**`" in text
    assert "sourceReference" in text
    assert "claimIds" in text
