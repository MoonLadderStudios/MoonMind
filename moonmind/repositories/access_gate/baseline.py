"""Frozen baseline for the issue #4024 release gate.

Freezes the reviewed design/plan revision and maps every stable claim in
``docs/RepositoryAccessAndWorkspaceDesign.md`` plus every consumer row in
the decoupling plan to an implementation owner, exact test entrypoint,
support combination, and evidence artifact.

A design-authorized unsupported combination (``unsupported_by_design``) is
distinct from an unimplemented required capability
(``unimplemented_required``): only the former may be green without an
implementation. Required App acquisition, source variants, saved-work
durability, and publication-only recovery stay ``unimplemented_required``
until their owning slices land; they are never relabeled out of scope to
make the gate green.
"""

from __future__ import annotations

from dataclasses import dataclass


# Design revision reviewed 2026-09-05 per the issue brief.
FROZEN_DESIGN_REVISION = "1dffb29c28de06775ee6028305222c73d911628b"
# Plan's own inspected baseline (plan Section header); kept separate so a
# plan-only refresh does not masquerade as a design change.
FROZEN_PLAN_BASELINE = "63bce9852ffa33e33cb0b416bc24a654b1b6f92b"
# Repository HEAD the NOT_IMPLEMENTED assessment ran against.
ASSESSED_HEAD = "7643ae6ab"

GATE_TEST_ENTRYPOINT = (
    "tests/unit/repositories/test_access_gate.py"
)


@dataclass(frozen=True)
class StableClaim:
    """One stable design claim with its gate mapping."""

    id: str
    owner: str
    test_entrypoint: str
    support_combination: str
    evidence_artifact: str
    status: str


class ClaimStatus:
    """Terminal mapping states for a stable claim."""

    # Covered by this gate's own required-shard regressions.
    GATE_COVERED = "gate_covered"
    # Required capability whose owning slice has not landed yet. Gate stays red.
    UNIMPLEMENTED_REQUIRED = "unimplemented_required"
    # Design-authorized unsupported combination (NON-GOAL scope). May be green.
    UNSUPPORTED_BY_DESIGN = "unsupported_by_design"


# All 42 stable claims in the epic's design baseline:
# CONTRACT-001..014 (14) + INV-001..009 (9) + QUALITY-001..009 (9) +
# DOC-REQ-001..005 (5) + TEST-001..004 (4) + NON-GOAL-001 (1).
CLAIM_IDS_42 = (
    "CONTRACT-001",
    "CONTRACT-002",
    "CONTRACT-003",
    "CONTRACT-004",
    "CONTRACT-005",
    "CONTRACT-006",
    "CONTRACT-007",
    "CONTRACT-008",
    "CONTRACT-009",
    "CONTRACT-010",
    "CONTRACT-011",
    "CONTRACT-012",
    "CONTRACT-013",
    "CONTRACT-014",
    "INV-001",
    "INV-002",
    "INV-003",
    "INV-004",
    "INV-005",
    "INV-006",
    "INV-007",
    "INV-008",
    "INV-009",
    "QUALITY-001",
    "QUALITY-002",
    "QUALITY-003",
    "QUALITY-004",
    "QUALITY-005",
    "QUALITY-006",
    "QUALITY-007",
    "QUALITY-008",
    "QUALITY-009",
    "DOC-REQ-001",
    "DOC-REQ-002",
    "DOC-REQ-003",
    "DOC-REQ-004",
    "DOC-REQ-005",
    "TEST-001",
    "TEST-002",
    "TEST-003",
    "TEST-004",
    "NON-GOAL-001",
)

# Owner slice per claim family. Slices 1-7 are the owning implementation
# issues; slice 0 is contract reconciliation/inventory.
_CLAIM_OWNERS = {
    "CONTRACT-001": "slice-0",
    "CONTRACT-002": "slice-2/slice-3",
    "CONTRACT-003": "slice-2",
    "CONTRACT-004": "slice-2",
    "CONTRACT-005": "slice-1",
    "CONTRACT-006": "slice-7",
    "CONTRACT-007": "slice-1/slice-2",
    "CONTRACT-008": "slice-2",
    "CONTRACT-009": "slice-1/slice-2",
    "CONTRACT-010": "slice-2",
    "CONTRACT-011": "slice-3",
    "CONTRACT-012": "slice-4",
    "CONTRACT-013": "slice-5",
    "CONTRACT-014": "slice-0/slice-6",
    "INV-001": "slice-2",
    "INV-002": "slice-5",
    "INV-003": "slice-2/slice-7",
    "INV-004": "slice-2",
    "INV-005": "slice-2/slice-3",
    "INV-006": "slice-3/slice-4",
    "INV-007": "slice-4",
    "INV-008": "slice-5",
    "INV-009": "slice-6",
    "QUALITY-001": "slice-1",
    "QUALITY-002": "slice-2",
    "QUALITY-003": "slice-2",
    "QUALITY-004": "slice-4",
    "QUALITY-005": "slice-4",
    "QUALITY-006": "slice-5",
    "QUALITY-007": "slice-0",
    "QUALITY-008": "slice-6",
    "QUALITY-009": "slice-0",
    "DOC-REQ-001": "slice-3/slice-6",
    "DOC-REQ-002": "slice-3",
    "DOC-REQ-003": "slice-6",
    "DOC-REQ-004": "slice-6",
    "DOC-REQ-005": "slice-4/slice-6",
    "TEST-001": "gate",
    "TEST-002": "gate",
    "TEST-003": "gate",
    "TEST-004": "gate",
    "NON-GOAL-001": "design",
}

# The four TEST obligations are owned by this gate's own required-shard
# regressions; every sibling-dependent claim stays unimplemented until its
# owning slice lands. NON-GOAL-001 is design-authorized unsupported scope.
_GATE_COVERED = frozenset({"TEST-001", "TEST-002", "TEST-003", "TEST-004"})


def _status_for(claim_id: str) -> str:
    if claim_id in _GATE_COVERED:
        return ClaimStatus.GATE_COVERED
    if claim_id == "NON-GOAL-001":
        return ClaimStatus.UNSUPPORTED_BY_DESIGN
    return ClaimStatus.UNIMPLEMENTED_REQUIRED


def _entrypoint_for(claim_id: str, owner: str) -> str:
    if claim_id in _GATE_COVERED:
        return GATE_TEST_ENTRYPOINT
    return f"pending:{owner}"


def frozen_claims() -> tuple[StableClaim, ...]:
    """Return the frozen 42-claim coverage map."""
    return tuple(
        StableClaim(
            id=claim_id,
            owner=_CLAIM_OWNERS[claim_id],
            test_entrypoint=_entrypoint_for(claim_id, _CLAIM_OWNERS[claim_id]),
            support_combination="admissible runtime x source x access x output/publication",
            evidence_artifact=f"gate/{claim_id.lower()}.json",
            status=_status_for(claim_id),
        )
        for claim_id in CLAIM_IDS_42
    )


@dataclass(frozen=True)
class ConsumerRow:
    """One plan Section 4 consumer-inventory row with its gate mapping."""

    boundary: str
    owner: str
    test_entrypoint: str
    status: str


# Complete consumer inventory from plan Section 4 ("Required consumer
# cutover"). No row may call singleton discovery once its slice lands;
# every row is unimplemented until then.
CONSUMER_ROWS: tuple[ConsumerRow, ...] = (
    ConsumerRow("Authoring/admission", "slice-2/slice-3", "pending:slice-2/slice-3",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Secrets/settings", "slice-1", "pending:slice-1",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Hosting API consumers", "slice-2", "pending:slice-2",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Managed launch", "slice-2", "pending:slice-2",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Omnigent planning/acquisition", "slice-2", "pending:slice-2",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Omnigent host/workspace", "slice-2/slice-3", "pending:slice-2/slice-3",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Publication/recovery", "slice-4/slice-5", "pending:slice-4/slice-5",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Checkpoint/results", "slice-4", "pending:slice-4",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
    ConsumerRow("Bootstrap/UI", "slice-6", "pending:slice-6",
                ClaimStatus.UNIMPLEMENTED_REQUIRED),
)


@dataclass(frozen=True)
class CoverageDelta:
    """Result of comparing a candidate design revision against the freeze."""

    frozen_revision: str
    candidate_revision: str
    requires_review: bool


def coverage_delta(candidate_revision: str) -> CoverageDelta:
    """Report whether a candidate revision needs a coverage-delta review.

    If the design changes, the old 42-claim count must not keep being
    asserted: any revision other than the frozen one requires review.
    """
    return CoverageDelta(
        frozen_revision=FROZEN_DESIGN_REVISION,
        candidate_revision=candidate_revision,
        requires_review=candidate_revision != FROZEN_DESIGN_REVISION,
    )
