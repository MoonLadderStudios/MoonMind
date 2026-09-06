"""Repository access release gate scaffolding (MoonLadderStudios/MoonMind#4024).

Final integration/release gate for the repository-access and workspace
decoupling overhaul. This module is test infrastructure and contract fixtures,
not another implementation engine: it freezes the reviewed design/plan
revision, maps every stable claim and consumer row to an owner/entrypoint/
support/evidence tuple, defines the bounded risk-based conformance matrix,
records what each run actually executes, and aggregates gate results without
ever turning failures, skips, or missing evidence into success.

Canonical targets:
- ``docs/RepositoryAccessAndWorkspaceDesign.md`` (Status: Proposed)
- ``docs/tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md`` (Status: Proposed)

The gate stays red until the owning implementation issues land
(#2615, #1090, #2619, #4004-#4012, #4014-#4023, #3938, #3940). Required App
acquisition, source variants, saved-work durability, and publication-only
recovery cannot be relabeled out of scope to make the gate green.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

# Reviewed revisions frozen by the #4024 brief. If the design or plan changes,
# review the coverage delta instead of continuing to assert the old claim count.
FROZEN_DESIGN_REVISION = "1dffb29c28de06775ee6028305222c73d911628b"
FROZEN_PLAN_BASELINE = "63bce9852ffa33e33cb0b416bc24a654b1b6f92b"
FROZEN_DESIGN_STATUS = "Proposed"
FROZEN_PLAN_STATUS = "Proposed"

# All 42 stable claims in the epic's design baseline, in document order.
STABLE_CLAIM_IDS: tuple[str, ...] = (
    "DOC-REQ-001",
    "CONTRACT-001",
    "INV-001",
    "DOC-REQ-002",
    "CONTRACT-002",
    "CONTRACT-003",
    "CONTRACT-004",
    "INV-002",
    "CONTRACT-005",
    "CONTRACT-006",
    "CONTRACT-007",
    "QUALITY-001",
    "CONTRACT-008",
    "CONTRACT-009",
    "INV-003",
    "QUALITY-002",
    "CONTRACT-010",
    "INV-004",
    "QUALITY-003",
    "INV-005",
    "CONTRACT-011",
    "INV-006",
    "CONTRACT-012",
    "QUALITY-004",
    "INV-007",
    "QUALITY-005",
    "CONTRACT-013",
    "INV-008",
    "QUALITY-006",
    "CONTRACT-014",
    "QUALITY-007",
    "DOC-REQ-003",
    "INV-009",
    "QUALITY-008",
    "DOC-REQ-004",
    "DOC-REQ-005",
    "TEST-001",
    "TEST-002",
    "TEST-003",
    "TEST-004",
    "NON-GOAL-001",
    "QUALITY-009",
)

# Consumer inventory from the decoupling plan, Section 4. Starting-point
# module lists live in the plan; this table keeps the boundary identities
# stable so coverage can reference them.
CONSUMER_BOUNDARIES: tuple[str, ...] = (
    "authoring/admission",
    "secrets/settings",
    "hosting-api-consumers",
    "managed-launch",
    "omnigent-planning/acquisition",
    "omnigent-host/workspace",
    "publication/recovery",
    "checkpoint/results",
    "bootstrap/ui",
)

# Capabilities that must never be silently relabeled out of scope.
REQUIRED_CAPABILITIES = (
    "app-acquisition",
    "source-variants",
    "saved-work-durability",
    "publication-only-recovery",
)

SupportKind = Literal["supported", "unsupported-design-authorized", "unimplemented"]

ExecutionTier = Literal[
    "helper-in-memory",
    "real-local-process",
    "real-postgres-object-store",
    "workflow-dispatch-replay",
    "packaged-runtime",
    "live-verification",
]


@dataclass(frozen=True)
class ClaimCoverage:
    """One stable claim mapped to its implementation owner and evidence."""

    claim_id: str
    owner: str
    test_entrypoint: str
    support: SupportKind
    evidence_artifact: str
    design_authorization_ref: str = ""


@dataclass(frozen=True)
class ConsumerCoverage:
    """One plan consumer row mapped to its implementation owner and evidence."""

    boundary: str
    owner: str
    test_entrypoint: str
    support: SupportKind
    evidence_artifact: str
    design_authorization_ref: str = ""


@dataclass(frozen=True)
class RiskCombination:
    """One advertised runtime x source x access x output combination."""

    combination_id: str
    runtime: str
    source: str
    access_mode: str
    output_publication: str
    positive_journey: str = ""
    negative_recovery_owner: str = ""
    shared_substrate_justification: str = ""


@dataclass(frozen=True)
class RunProvenance:
    """Record of what a gate run actually executed."""

    source_digest: str
    build_digest: str
    architecture: str
    schema_versions: Mapping[str, str]
    policy_versions: Mapping[str, str]
    db_engine: str
    db_version: str
    artifact_backend: str
    topology: str
    provider_adapter: str
    scenario: str
    substitutions: tuple[str, ...] = ()
    execution_tier: ExecutionTier = "helper-in-memory"


@dataclass(frozen=True)
class GateScenarioResult:
    """One scenario outcome contributing to the aggregate gate report."""

    scenario_id: str
    status: Literal["passed", "failed", "skipped", "missing-artifact", "incomplete"]
    detail: str = ""


@dataclass(frozen=True)
class GateReport:
    """Aggregate gate verdict. Never reports success on partial evidence."""

    verdict: Literal["pass", "blocked", "failed"]
    reasons: tuple[str, ...] = ()
    scenario_count: int = 0


def validate_claim_coverage(
    coverages: Sequence[ClaimCoverage],
    *,
    design_revision: str,
    expected_revision: str = FROZEN_DESIGN_REVISION,
) -> list[str]:
    """Validate that every stable claim has an owner, entrypoint, and evidence."""
    errors: list[str] = []
    if design_revision != expected_revision:
        errors.append(
            f"design revision {design_revision!r} does not match frozen "
            f"{expected_revision!r}: review the coverage delta before asserting "
            f"{len(STABLE_CLAIM_IDS)} claims"
        )
    by_claim = {c.claim_id: c for c in coverages}
    for claim_id in STABLE_CLAIM_IDS:
        coverage = by_claim.get(claim_id)
        if coverage is None:
            errors.append(f"missing coverage for stable claim {claim_id}")
            continue
        if not coverage.owner:
            errors.append(f"{claim_id}: missing implementation owner")
        if not coverage.test_entrypoint:
            errors.append(f"{claim_id}: missing exact test entrypoint")
        if not coverage.evidence_artifact:
            errors.append(f"{claim_id}: missing evidence artifact")
        if coverage.support == "unsupported-design-authorized":
            if not coverage.design_authorization_ref:
                errors.append(
                    f"{claim_id}: unsupported combination needs a "
                    "design authorization reference"
                )
        elif coverage.support == "unimplemented":
            pass
        elif coverage.support != "supported":
            errors.append(f"{claim_id}: unknown support kind {coverage.support!r}")
    for coverage in coverages:
        if coverage.claim_id not in STABLE_CLAIM_IDS:
            errors.append(f"unknown stable claim {coverage.claim_id!r}")
    return errors


def validate_consumer_coverage(
    coverages: Sequence[ConsumerCoverage],
) -> list[str]:
    """Validate that every consumer inventory row has an owner and evidence."""
    errors: list[str] = []
    by_boundary = {c.boundary: c for c in coverages}
    for boundary in CONSUMER_BOUNDARIES:
        coverage = by_boundary.get(boundary)
        if coverage is None:
            errors.append(f"missing coverage for consumer boundary {boundary!r}")
            continue
        if not coverage.owner:
            errors.append(f"{boundary}: missing implementation owner")
        if not coverage.test_entrypoint:
            errors.append(f"{boundary}: missing exact test entrypoint")
        if not coverage.evidence_artifact:
            errors.append(f"{boundary}: missing evidence artifact")
        if coverage.support == "unsupported-design-authorized" and not coverage.design_authorization_ref:
            errors.append(
                f"{boundary}: unsupported combination needs a design "
                "authorization reference"
            )
    for coverage in coverages:
        if coverage.boundary not in CONSUMER_BOUNDARIES:
            errors.append(f"unknown consumer boundary {coverage.boundary!r}")
    return errors


def reject_scope_relabel(
    capability: str,
    support: SupportKind,
) -> str | None:
    """Reject silently relabeling a required capability out of scope."""
    if capability in REQUIRED_CAPABILITIES and support == "unsupported-design-authorized":
        return (
            f"required capability {capability!r} cannot be relabeled "
            "unsupported-design-authorized to make the gate green"
        )
    return None


def validate_risk_matrix(
    combinations: Sequence[RiskCombination],
    *,
    authority_handoffs: Sequence[str],
) -> list[str]:
    """Validate the bounded risk-based matrix.

    Every advertised combination needs a positive production journey and every
    authority handoff needs a named negative/recovery owner. Shared substrate
    tests are allowed only with justification naming the same production
    implementation.
    """
    errors: list[str] = []
    for combo in combinations:
        if not combo.positive_journey:
            errors.append(
                f"{combo.combination_id}: advertised combination needs a "
                "positive production journey"
            )
        if not combo.negative_recovery_owner:
            errors.append(
                f"{combo.combination_id}: advertised combination needs a "
                "named negative/recovery owner"
            )
        if combo.shared_substrate_justification == "":
            continue
        # A justification must name the shared production implementation;
        # a bare "shared" flag is not sufficient.
        if len(combo.shared_substrate_justification.strip()) < 8:
            errors.append(
                f"{combo.combination_id}: shared-substrate justification must "
                "name the same production implementation demonstrably used"
            )
    for handoff in authority_handoffs:
        named = any(
            handoff in (combo.negative_recovery_owner or "")
            for combo in combinations
        )
        if not named:
            errors.append(
                f"authority handoff {handoff!r} has no named "
                "negative/recovery owner"
            )
    return errors


def validate_run_provenance(provenance: RunProvenance) -> list[str]:
    """Validate that a run record separates test doubles from real evidence."""
    errors: list[str] = []
    for field_name in (
        "source_digest",
        "build_digest",
        "architecture",
        "db_engine",
        "db_version",
        "artifact_backend",
        "topology",
        "provider_adapter",
        "scenario",
    ):
        if not getattr(provenance, field_name):
            errors.append(f"run provenance missing {field_name}")
    if not provenance.schema_versions:
        errors.append("run provenance missing schema versions")
    if not provenance.policy_versions:
        errors.append("run provenance missing policy versions")

    tier = provenance.execution_tier
    scenario_lower = provenance.scenario.lower()
    db_lower = provenance.db_engine.lower()
    subs = {s.lower() for s in provenance.substitutions}

    # A PostgreSQL-named run on SQLite proves nothing about PostgreSQL races.
    if "postgres" in scenario_lower and db_lower == "sqlite":
        errors.append(
            "PostgreSQL-named test run on SQLite does not prove PostgreSQL races"
        )
    # Patch-marker mocks are not recorded-history replay.
    if tier == "workflow-dispatch-replay" and (
        "mock" in subs or "patch-marker" in subs or "in-memory" in subs
    ):
        errors.append(
            "patch-marker mocks are not recorded-history replay: "
            "workflow-dispatch-replay requires real dispatch/replay evidence"
        )
    # A browser fixture is not live enrollment.
    if tier == "live-verification" and (
        "browser-fixture" in subs or "catalog-mock" in subs or "profile-seed" in subs
    ):
        errors.append(
            "browser fixture is not live enrollment: live-verification requires "
            "protected live provider evidence"
        )
    # Helper/in-memory results must stay labeled as such.
    if tier == "helper-in-memory" and "postgres" in scenario_lower:
        errors.append(
            "helper/in-memory result must not claim PostgreSQL scenario evidence"
        )
    return errors


def aggregate_gate_report(results: Sequence[GateScenarioResult]) -> GateReport:
    """Aggregate scenario outcomes. Partial evidence never aggregates to pass."""
    if not results:
        return GateReport(
            verdict="blocked",
            reasons=("no gate scenarios executed",),
            scenario_count=0,
        )
    reasons: list[str] = []
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]
    missing = [r for r in results if r.status == "missing-artifact"]
    incomplete = [r for r in results if r.status == "incomplete"]
    for r in failed:
        reasons.append(f"selected failure in {r.scenario_id}: {r.detail or 'failed'}")
    for r in skipped:
        reasons.append(
            f"unexpected skip in {r.scenario_id}: {r.detail or 'skipped'}"
        )
    for r in missing:
        reasons.append(
            f"missing artifact in {r.scenario_id}: {r.detail or 'missing artifact'}"
        )
    for r in incomplete:
        reasons.append(
            f"incomplete scenario in {r.scenario_id}: {r.detail or 'incomplete'}"
        )
    if reasons:
        verdict: Literal["pass", "blocked", "failed"] = (
            "failed" if failed else "blocked"
        )
        return GateReport(
            verdict=verdict, reasons=tuple(reasons), scenario_count=len(results)
        )
    passed = [r for r in results if r.status == "passed"]
    if len(passed) != len(results):
        return GateReport(
            verdict="blocked",
            reasons=(f"unknown scenario status in gate report",),
            scenario_count=len(results),
        )
    return GateReport(verdict="pass", reasons=(), scenario_count=len(results))


def gate_fixture_entrypoint(
    claim_or_boundary: str,
    *,
    kind: Literal["claim", "consumer"] = "claim",
) -> str:
    """Return the canonical gate fixture entrypoint for a claim or consumer."""
    if kind == "consumer":
        return (
            "tests/fixtures/repository_access_gate/coverage_matrix.json"
            f"#consumers/{claim_or_boundary}"
        )
    return (
        "tests/fixtures/repository_access_gate/coverage_matrix.json"
        f"#claims/{claim_or_boundary}"
    )


__all__ = [
    "CONSUMER_BOUNDARIES",
    "FROZEN_DESIGN_REVISION",
    "FROZEN_DESIGN_STATUS",
    "FROZEN_PLAN_BASELINE",
    "FROZEN_PLAN_STATUS",
    "REQUIRED_CAPABILITIES",
    "STABLE_CLAIM_IDS",
    "ClaimCoverage",
    "ConsumerCoverage",
    "GateReport",
    "GateScenarioResult",
    "RiskCombination",
    "RunProvenance",
    "aggregate_gate_report",
    "gate_fixture_entrypoint",
    "reject_scope_relabel",
    "validate_claim_coverage",
    "validate_consumer_coverage",
    "validate_risk_matrix",
    "validate_run_provenance",
]
