"""Issue #4024 release-gate infrastructure.

Bounded gate-owned test infrastructure for the repository-access overhaul.
This package freezes the reviewed design revision, maps every stable claim
and consumer-inventory row to an owner plus exact test entrypoint, carries
the bounded risk-based conformance matrix, and records what each run
actually executes.

It does not claim credential, capture, publication, or clean-install
conformance (gates 5-8 in the issue): those rows stay explicitly
``unimplemented_required`` with their owning slice until the sibling
implementation issues land. The gate therefore stays red until the
program is truly complete.
"""

from moonmind.repositories.access_gate.baseline import (
    ASSESSED_HEAD,
    CLAIM_IDS_42,
    CONSUMER_ROWS,
    FROZEN_DESIGN_REVISION,
    FROZEN_PLAN_BASELINE,
    ClaimStatus,
    CoverageDelta,
    StableClaim,
    coverage_delta,
    frozen_claims,
)
from moonmind.repositories.access_gate.risk_matrix import (
    AdvertisedCombination,
    advertised_combinations,
    negative_owner_for,
    positive_owner_for,
)
from moonmind.repositories.access_gate.run_record import (
    ExecutionClass,
    GateAggregate,
    RunRecord,
    aggregate_gate_result,
)

__all__ = [
    "ASSESSED_HEAD",
    "CLAIM_IDS_42",
    "CONSUMER_ROWS",
    "FROZEN_DESIGN_REVISION",
    "FROZEN_PLAN_BASELINE",
    "AdvertisedCombination",
    "ClaimStatus",
    "CoverageDelta",
    "ExecutionClass",
    "GateAggregate",
    "RunRecord",
    "StableClaim",
    "advertised_combinations",
    "aggregate_gate_result",
    "coverage_delta",
    "frozen_claims",
    "negative_owner_for",
    "positive_owner_for",
]
