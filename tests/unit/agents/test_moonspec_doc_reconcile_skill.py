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
    assert "## Authority-Scope Resolution" in text
    assert "docs/DocumentationArchitecture.md" in text
    assert "module-owned contract policy" in text
    assert "The owning document may be different from the original source document" in text
    assert "Escalate instead of guessing when ownership is ambiguous" in text
    assert "no_update_required" in text
    assert "noUpdateRequired" in text
    assert "stop with `NO_UPDATE_REQUIRED`" not in text
    assert "report `NO_UPDATE_REQUIRED`" not in text
    assert "immediate `NO_UPDATE_REQUIRED`" not in text
    assert '"updated": [' in text
    assert '"escalated": [' in text
    assert "Every updated/noUpdateRequired/escalated item must include a reason" in text
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


def test_moonspec_orchestrate_skill_honors_source_design_path_for_doc_reconcile() -> None:
    text = (_SKILLS_DIR / "moonspec-orchestrate" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "at least one canonical source candidate exists" in text
    assert "the orchestration step provides a source design path under `docs/`" in text
    assert "Skip this stage with outcome `no_update_required`" in text
    assert "when `spec.md` records a canonical source document" not in text


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
