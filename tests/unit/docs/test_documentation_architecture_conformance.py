"""MM-909 self-conformance guardrails for the MoonSpec Documentation Architecture Standard.

These tests are the durable, repeatable form of the self-conformance review
recorded in ``docs/tmp/MoonSpecDocsArchitectureConformanceReview.md``. They
assert that the docs introduced by the standard (source design MM-900, authored
by MM-902) keep honoring the issue's non-goals: the standard stays declarative
desired state, introduces no ADR/decision-log authority, does not force every
module to be a DDD bounded context, does not treat ``specs/`` as durable
documentation, and the self-conformance evidence stays a non-canonical
``docs/tmp/`` working document with preserved MM-900/MM-909 traceability.

The checks are read-only and advisory in spirit (no hard-blocking validation is
introduced by this issue); they only protect the conditions the guardrail story
owns from silent regression.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

STANDARD_DOC = REPO_ROOT / "docs" / "DocumentationArchitecture.md"
DOCUMENT_MODEL_DOC = REPO_ROOT / "docs" / "Workflows" / "MoonSpecDocumentModel.md"
CONFORMANCE_REVIEW = (
    REPO_ROOT / "docs" / "tmp" / "MoonSpecDocsArchitectureConformanceReview.md"
)
MIGRATION_PLAN = REPO_ROOT / "docs" / "tmp" / "MoonSpecDocsFirstAlignmentPlan.md"
STALE_MIGRATION_PLAN = (
    REPO_ROOT / "docs" / "tmp" / "DocumentationArchitectureMigrationPlan.md"
)

FORBIDDEN_DECISION_DIRS = (
    REPO_ROOT / "docs" / "adr",
    REPO_ROOT / "docs" / "ADR",
    REPO_ROOT / "docs" / "decisions",
    REPO_ROOT / "docs" / "Decisions",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_standard_is_declarative_desired_state_not_a_tracker() -> None:
    text = _read(STANDARD_DOC)

    # Declarative status, not a migration/checklist/status framing.
    assert "**Status:** Current standard and target direction" in text
    # The standard explicitly declares itself a desired-state strategy, not a plan.
    assert "not a migration plan or a checklist" in text
    # No checklist/status checkbox framing leaks into the canonical standard.
    assert "- [ ]" not in text
    assert "- [x]" not in text
    # The five declarative viewpoints remain the backbone.
    for viewpoint in (
        "System Architecture View",
        "Module Architecture View",
        "System / Feature Design View",
        "Module Contract Specification",
        "Cross-Cutting Concept View",
    ):
        assert viewpoint in text, viewpoint


def test_standard_introduces_no_adr_or_decision_log_authority() -> None:
    for directory in FORBIDDEN_DECISION_DIRS:
        assert not directory.exists(), directory

    text = _read(STANDARD_DOC)
    lowered = text.lower()
    # The standard must not establish ADRs / decision logs / decisions dirs.
    assert "decision log" not in lowered
    assert "decisions/" not in lowered
    # "ADR" must not be introduced as a document type (guards the non-goal).
    assert "adr" not in lowered.replace("standard", "")


def test_standard_does_not_force_bounded_contexts() -> None:
    text = _read(STANDARD_DOC)
    # Architectural boundary / ownership surface is the default, not Bounded Context.
    assert "architectural boundary" in text
    assert "ownership surface" in text
    # Bounded Context is gated as a subtype behind boundary tests.
    assert "subtype" in text.lower()
    assert (
        "the directory is an architectural boundary or ownership surface — not a Bounded Context"
        in text
    )


def test_standard_does_not_treat_specs_as_durable_docs() -> None:
    text = _read(STANDARD_DOC)
    lowered = text.lower()
    # specs/ must never be promoted to durable/canonical documentation here.
    assert "specs/ as durable" not in lowered
    assert "specs/ is the source of truth" not in lowered


def test_standard_defers_to_document_model_no_second_authority() -> None:
    assert DOCUMENT_MODEL_DOC.exists()
    text = _read(STANDARD_DOC)
    # The standard extends, not replaces, the Document Model — no second authority.
    assert "extends, and does not replace" in text
    assert "MoonSpecDocumentModel.md" in text
    assert "additive refinements" in text


def test_conformance_review_is_non_canonical_working_doc_with_traceability() -> None:
    if not CONFORMANCE_REVIEW.exists():
        pytest.skip(
            "docs/tmp self-conformance evidence is disposable; the durable "
            "guardrails on the canonical standard cover the regression surface "
            "once this working doc is archived/removed after MM-900 completes."
        )
    text = _read(CONFORMANCE_REVIEW)
    # Self-labels as time-bound docs/tmp evidence, not a canonical view.
    assert "docs/tmp/" in text
    assert "not** a canonical" in text or "not a canonical" in text
    # MM-900 source traceability and MM-909 ownership are preserved.
    assert "MM-909" in text
    assert "MM-900" in text
    assert "MM-902" in text
    assert "DESIGN-REQ-019" in text
    assert "DESIGN-REQ-020" in text
    # Records the validation outcome and the authority-conflict result.
    assert "check_terminology.sh" in text
    assert "verify_workflow_terminology.py" in text
    assert "Unresolved documentation-authority conflicts" in text


def test_migration_plan_records_authority_conflict_outcome() -> None:
    if not MIGRATION_PLAN.exists():
        pytest.skip(
            "docs/tmp migration/alignment plan is disposable working evidence; "
            "it is not a permanent test fixture and may be archived/removed once "
            "MM-900 completes without failing the required unit suite."
        )
    text = _read(MIGRATION_PLAN)
    assert "MM-909 self-conformance and authority-conflict record" in text
    assert "MoonSpecDocsArchitectureConformanceReview.md" in text
    # The recorded outcome: no unresolved authority conflict.
    assert "Unresolved documentation-authority conflicts:" in text


def test_stale_documentation_migration_plan_is_not_active_or_contradictory() -> None:
    if not STALE_MIGRATION_PLAN.exists():
        pytest.skip(
            "Historical docs/tmp migration plan may be deleted once the "
            "Documentation Architecture cleanup is fully closed."
        )
    text = _read(STALE_MIGRATION_PLAN)
    assert "MM-928" in text
    assert "**Status:** Superseded / closed" in text
    assert "no longer records active authority conflicts" in text
    assert "## 7. Historical documentation-authority conflicts (closed)" in text
    assert "## 7. Unresolved documentation-authority conflicts" not in text
    assert "Draft / active" not in text
