"""Epic-level docs contract for MoonLadderStudios/MoonMind#3930.

Pins the umbrella journey, authorization boundary, terminal evidence, recovery,
local-first, traceability, child-ownership, and #3971-exclusion statements
without duplicating the functional suites owned by child issues #3967-#3970.
"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
ADOPTION_DOC = REPO_ROOT / "docs" / "SoftwareEngineeringAdoptionPath.md"
README_DOC = REPO_ROOT / "README.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_adoption_doc_exists_and_is_canonical() -> None:
    assert ADOPTION_DOC.exists()
    text = _read(ADOPTION_DOC)
    assert "**Document Class:** Canonical declarative" in text
    assert "System / Feature Design View" in text
    assert "MoonLadderStudios/MoonMind#3930" in text
    # Canonical docs stay declarative: no migration checklists.
    assert "- [ ]" not in text
    assert "- [x]" not in text


def test_journey_defines_entrypoint_boundary_evidence_and_recovery() -> None:
    text = _read(ADOPTION_DOC)
    assert "POST /api/executions" in text
    assert "webhook signature" in text.lower()
    assert "terminal evidence" in text.lower()
    assert "recovery" in text.lower()
    assert "publication-only recovery" in text.lower()
    # Public content or signature alone authorizes nothing.
    assert "cannot authorize model spend or repository mutation" in text


def test_local_first_preserved_with_honest_limitations() -> None:
    text = _read(ADOPTION_DOC)
    assert "repository-independent work is a complete product path" in text.lower()
    assert "planned but not yet implemented or qualified" in text


def test_claims_trace_to_code_boundaries_not_counts() -> None:
    text = _read(ADOPTION_DOC)
    for boundary in (
        "SecretsSystem",
        "Provider Profile",
        "RepositoryConnection",
        "artifact",
        "publication",
    ):
        assert boundary.lower() in text.lower(), boundary
    assert "not by source counts" in text.lower()


def test_child_ownership_agrees_and_3971_stays_excluded() -> None:
    text = _read(ADOPTION_DOC)
    for child in ("#3967", "#3968", "#3969", "#3970"):
        assert child in text, child
    assert "#3971" in text
    assert "not planned" in text.lower()
    assert "excluded from completion criteria" in text.lower()
    # #3971 must read as excluded, never as required or completed work.
    assert "not relabeled as implemented" in text.lower()
    assert "not recreated" in text.lower() or "is not recreated" in text.lower()


def test_readme_preserves_pillars_and_links_adoption_path() -> None:
    text = _read(README_DOC)
    for pillar in ("Security", "Resilience", "Observability"):
        assert pillar in text, pillar
    assert "Where this is headed" in text
    assert "docs/SoftwareEngineeringAdoptionPath.md" in text
    assert "MoonLadderStudios/MoonMind#3930" in text
    assert "planned direction, not current enforcement" in text
