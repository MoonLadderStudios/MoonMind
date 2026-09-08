"""Caller-backed upstream-duplication audit for the Omnigent boundary.

Source issue: MoonLadderStudios/MoonMind#3954 (parent #3928).

The audit's module/table counts are candidates for investigation, not a
deletion list. A MoonMind workflow binding, immutable profile snapshot,
credential lease, event journal, catalog projection, or artifact manifest is
not redundant simply because Omnigent also has a session, agent, provider, or
event object. This module is the code-owned record of that audit:

* ``OWNERSHIP_TABLE`` holds one row per suspected duplicate with the issue's
  required columns: production entrypoint, state/decision owned, persisted
  consumers, supported upstream API at the pinned commit, proposed surviving
  owner, and the test proving equivalent behavior.
* The fail-closed guards (``check_*``) reject upstream drift, wrong owner,
  stale generation, unknown route, missing evidence, and credential mismatch
  without substituting credentials, profiles, billing-relevant values, source
  authority, or less-constrained execution paths.
* ``evaluate_removal_eligibility`` refuses every table/field removal until a
  verified replacement contract with drained consumers exists. No removal is
  eligible today: the audited upstream commit predates the implementation pin
  and the upstream submodule is not checked out, so no supported replacement
  has been demonstrated.

This module is deliberately pure (stdlib only, plus the one upstream pin
imported from its single authority): decision vocabulary lives here, while
live verification stays in the adapters and conformance suites that own it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT

AUDIT_ISSUE = "MoonLadderStudios/MoonMind#3954"

# The upstream commit the #3954 audit was reviewed against. This is the audit
# baseline, not the implementation pin: the implementation pin is owned by
# ``host_auth_adapter.PINNED_OMNIGENT_COMMIT`` and imported above so there is
# exactly one authority for it.
AUDITED_UPSTREAM_COMMIT = "63bce9852ffa33e33cb0b416bc24a654b1b6f92b"

AUDIT_CONTRACT_VERSION = "moonmind.omnigent-upstream-duplication-audit/v1"


class UpstreamDuplicationAuditError(RuntimeError):
    """Fail-closed rejection raised by the #3954 audit guards."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class UpstreamOwnershipRow:
    """One audited suspected duplicate with its caller-backed disposition."""

    candidate_id: str
    production_entrypoint: str
    state_decision_owned: str
    persisted_consumers: tuple[str, ...]
    upstream_api_at_pinned_commit: str
    surviving_owner: str
    evidence_test: str
    disposition: str
    removal_criteria: str = ""


OWNERSHIP_TABLE: tuple[UpstreamOwnershipRow, ...] = (
    UpstreamOwnershipRow(
        candidate_id="workflow-binding-vs-upstream-session",
        production_entrypoint=(
            "moonmind/omnigent/bridge_store.py::OmnigentBridgeSessionStore + "
            "moonmind/omnigent/bridge_proxy.py::OmnigentBridgeSessionProxy"
        ),
        state_decision_owned=(
            "MoonMind bridge authorization, idempotency-key ownership, "
            "provider-session attachment ordering, session.created journal"
        ),
        persisted_consumers=(
            "omnigent_bridge_session rows keyed by idempotency key; "
            "omnigent_bridge_session_events journal; Temporal supervisor "
            "reattach/retry reads; Workflow Detail projection",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: upstream submodule not checked out and "
            "implementation pin f04b03 differs from the audit baseline, so no "
            "supported upstream replacement for durable binding ownership is "
            "demonstrated"
        ),
        surviving_owner="MoonMind bridge binding (upstream owns only the live runner session)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_bridge_binding_rejects_cross_owner_reuse"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a verified upstream durable-binding contract plus drained "
            "bridge rows, journal, supervisor, and projection consumers"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="immutable-profile-snapshot-vs-launch-args",
        production_entrypoint=(
            "moonmind/omnigent/execution_adapters.py + "
            "moonmind/omnigent/profile_bound_execution.py"
        ),
        state_decision_owned=(
            "Provider Profile selection, policy snapshot, effectiveLaunch "
            "evidence, durable binding before host mutation"
        ),
        persisted_consumers=(
            "bridge authorization refs (profile, lease, binding, host-lease); "
            "effective-launch evidence; retry path reusing durable binding",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: no audited upstream API accepting "
            "MoonMind authorization/policyEvidence in place of caller-supplied "
            "launch args"
        ),
        surviving_owner="MoonMind execution adapters (upstream owns host realization mechanics)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_profile_snapshot_guard_rejects_credential_mismatch"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a supported upstream policy/snapshot API plus migration "
            "of authorization, lease, and retry consumers"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="credential-lease-vs-runner-token",
        production_entrypoint=(
            "moonmind/omnigent/provider_leases.py::"
            "OmnigentProviderLeaseCoordinator + "
            "moonmind/omnigent/host_auth_adapter.py::OmnigentHostAuthAdapter"
        ),
        state_decision_owned=(
            "Purpose-aware Provider Profile lease, exclusive OAuth-home "
            "ownership, credential-generation fencing, release-last ordering"
        ),
        persisted_consumers=(
            "provider-lease rows; credential-generation refs in bridge "
            "authorization; host-lease refs; janitor cleanup evidence",
        ),
        upstream_api_at_pinned_commit=(
            "Pinned runner-tunnel verifier only (host_auth_adapter); it proves "
            "host identity, not profile-lease ownership, capacity, or "
            "release ordering"
        ),
        surviving_owner="MoonMind credential lease (upstream owns only token-bound runner identity proof)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_credential_scope_rejects_mismatch"
        ),
        disposition="preserve",
        removal_criteria=(
            "Never delete enrollment-owned credentials as duplication cleanup; "
            "requires drained leases, generations, and janitor evidence"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="event-journal-vs-provider-stream",
        production_entrypoint=(
            "moonmind/omnigent/bridge_store.py::OmnigentBridgeSessionStore event index + "
            "moonmind/omnigent/execute.py::run_omnigent_execution stream normalization"
        ),
        state_decision_owned=(
            "Durable cursorable event pages, normalized conversation/tool/"
            "lifecycle events, raw bounded event journal as artifact evidence"
        ),
        persisted_consumers=(
            "omnigent_bridge_session_events; raw journal artifacts; terminal "
            "and checkpoint external-state refs; Workflow Detail timeline",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: provider SSE is a live observation without "
            "durable cursors (proxy rejects `after != 0`); no durable journal "
            "replacement demonstrated"
        ),
        surviving_owner="MoonMind event journal (upstream owns live SSE transport)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_missing_terminal_evidence_is_rejected"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a supported durable upstream journal plus migration of "
            "cursor, artifact, checkpoint, and timeline consumers"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="catalog-projection-vs-agent-inventory",
        production_entrypoint=(
            "moonmind/omnigent/harness_platform/catalog.py + "
            "api_service/api/routers/omnigent_catalog.py"
        ),
        state_decision_owned=(
            "Synchronized catalog projection with trust classification, Agent "
            "Profile support evidence, and readiness gating"
        ),
        persisted_consumers=(
            "catalog/projection rows; Agent Profile support evidence; "
            "launch-policy compilation reads",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: the stock agent-inventory route is an observed "
            "inventory without trust, profile, or readiness semantics"
        ),
        surviving_owner="MoonMind catalog projection (upstream owns raw agent inventory observation)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_unknown_catalog_route_is_rejected"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a supported upstream trust/profile/readiness contract "
            "plus drained projection and policy consumers"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="artifact-manifest-vs-session-files",
        production_entrypoint=(
            "moonmind/omnigent/bridge_artifacts.py + "
            "moonmind/omnigent/workspace_publication.py"
        ),
        state_decision_owned=(
            "Terminal AgentRunResult assembly, capture manifests, redacted "
            "diagnostics, ArtifactRef-backed evidence"
        ),
        persisted_consumers=(
            "artifact refs; capture/resource manifests; checkpoint/remediation "
            "reads; post-host-cleanup replay (host-local logs are gone)",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: upstream session/resource files are "
            "provider observations, not ArtifactRef evidence with provenance "
            "and retention"
        ),
        surviving_owner="MoonMind artifact manifest (upstream owns live resource observation)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_missing_terminal_evidence_is_rejected"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a supported upstream artifact/provenance contract plus "
            "migration of manifest, checkpoint, and replay consumers"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="native-ui-facade-vs-upstream-app",
        production_entrypoint=(
            "moonmind/omnigent/native_ui.py + "
            "moonmind/omnigent/workflow_chat_facade.py"
        ),
        state_decision_owned=(
            "Binding-scoped browser boundary: opaque chatBindingId, scoped "
            "API/SSE facade allowlist, bootstrap contract, version gate, "
            "security headers; native UI supplied by upstream"
        ),
        persisted_consumers=(
            "chat binding refs; scoped facade authorization; bootstrap "
            "compatibility evidence; Workflow Chat acceptance manifest",
        ),
        upstream_api_at_pinned_commit=(
            "Native UI verified only against the implementation pin via "
            "SUPPORTED_NATIVE_UI_VERSIONS; unknown/blank/unsupported versions "
            "fail closed, never proxied directly"
        ),
        surviving_owner="MoonMind binding-scoped facade (upstream owns rendering/interaction semantics)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_native_ui_version_gate_rejects_drift"
        ),
        disposition="preserve",
        removal_criteria=(
            "No unrestricted proxy or direct upstream browser path may replace "
            "the facade; requires scoped-route equivalence proof"
        ),
    ),
    UpstreamOwnershipRow(
        candidate_id="wire-transport-vs-host-protocol",
        production_entrypoint=(
            "moonmind/workflows/adapters/omnigent_client.py::"
            "OmnigentHttpClient + moonmind/omnigent/bridge_proxy.py"
        ),
        state_decision_owned=(
            "Binding-scoped facade validation, bounded resource limits, stable "
            "redacted error codes, harvest-before-delete ordering"
        ),
        persisted_consumers=(
            "bridge rows written before first-message post; harvest-completion "
            "markers gating provider-session deletion",
        ),
        upstream_api_at_pinned_commit=(
            "Unverified at 63bce98: raw upstream wire handling without "
            "idempotency ownership, bounds, or harvest ordering is not a "
            "supported replacement"
        ),
        surviving_owner="MoonMind host-protocol facade (upstream owns the stock server/host protocol)",
        evidence_test=(
            "tests/unit/omnigent/test_upstream_duplication_audit_3954.py::"
            "test_unknown_resource_operation_is_rejected"
        ),
        disposition="preserve",
        removal_criteria=(
            "Requires a verified upstream idempotency/bounds/ordering contract "
            "plus drained bridge-row and harvest consumers"
        ),
    ),
)


def get_ownership_row(candidate_id: str) -> UpstreamOwnershipRow:
    """Return the audit row for one suspected duplicate, failing closed."""

    for row in OWNERSHIP_TABLE:
        if row.candidate_id == candidate_id:
            return row
    raise UpstreamDuplicationAuditError(
        f"Unknown duplication candidate: {candidate_id!r}",
        code="omnigent_audit_unknown_candidate",
    )


def check_upstream_pin(
    reported_commit: str | None,
    *,
    supported_commits: frozenset[str] | None = None,
) -> str:
    """Accept only a known-compatible upstream commit; reject drift fail-closed."""

    supported = supported_commits or frozenset({PINNED_OMNIGENT_COMMIT})
    candidate = str(reported_commit or "").strip()
    if not candidate:
        raise UpstreamDuplicationAuditError(
            "Upstream commit is unknown",
            code="omnigent_audit_upstream_drift",
        )
    if candidate not in supported:
        raise UpstreamDuplicationAuditError(
            f"Upstream commit {candidate!r} is not a supported replacement contract",
            code="omnigent_audit_upstream_drift",
        )
    return candidate


def check_audit_baseline_current() -> None:
    """Fail when the audit baseline no longer matches the implementation pin.

    The #3954 review baseline (``AUDITED_UPSTREAM_COMMIT``) differs from the
    implementation pin, so no removal may proceed on audit evidence alone: the
    audit must be re-verified against the current pin first.
    """

    if AUDITED_UPSTREAM_COMMIT != PINNED_OMNIGENT_COMMIT:
        raise UpstreamDuplicationAuditError(
            "Audit baseline 63bce98 differs from the implementation pin; "
            "re-verify the replacement contract before any removal",
            code="omnigent_audit_upstream_drift",
        )


_ALLOWED_FACADE_ROUTES: frozenset[str] = frozenset(
    {
        "changed_files",
        "workspace_files",
        "workspace_file",
        "workspace_diff",
        "session_files",
        "session_file",
    }
)


def check_route_allowed(operation: str) -> str:
    """Accept only binding-scoped facade operations; unknown routes fail closed."""

    candidate = str(operation or "").strip()
    if candidate not in _ALLOWED_FACADE_ROUTES:
        raise UpstreamDuplicationAuditError(
            f"Unknown facade route: {candidate!r}",
            code="omnigent_audit_unknown_route",
        )
    return candidate


def check_owner_match(*, stored_workflow_id: str, calling_workflow_id: str) -> str:
    """Accept only the owning workflow for a durable idempotency key."""

    stored = str(stored_workflow_id or "").strip()
    calling = str(calling_workflow_id or "").strip()
    if not stored or not calling or stored != calling:
        raise UpstreamDuplicationAuditError(
            "Durable binding is owned by a different workflow",
            code="omnigent_audit_wrong_owner",
        )
    return stored


def check_generation_match(*, observed: str, authorized: str) -> str:
    """Accept only the authorized credential/host generation; stale fails closed."""

    seen = str(observed or "").strip()
    want = str(authorized or "").strip()
    if not seen or not want or seen != want:
        raise UpstreamDuplicationAuditError(
            "Stale credential/host generation",
            code="omnigent_audit_stale_generation",
        )
    return seen


def check_evidence_present(refs: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only complete terminal evidence; missing refs fail closed."""

    missing = sorted(
        str(key)
        for key, value in dict(refs or {}).items()
        if str(value or "").strip() == ""
    )
    if missing:
        raise UpstreamDuplicationAuditError(
            f"Missing terminal evidence: {', '.join(missing)}",
            code="omnigent_audit_missing_evidence",
        )
    return dict(refs or {})


def check_credential_scope(
    *,
    presented_profile_ref: str,
    authorized_profile_ref: str,
    generation_matches: bool,
) -> str:
    """Accept only the authorized enrollment-owned credential scope."""

    presented = str(presented_profile_ref or "").strip()
    authorized = str(authorized_profile_ref or "").strip()
    if not presented or not authorized or presented != authorized or not generation_matches:
        raise UpstreamDuplicationAuditError(
            "Credential scope mismatch",
            code="omnigent_audit_credential_mismatch",
        )
    return presented


@dataclass(frozen=True, slots=True)
class RemovalEligibility:
    """Whether one candidate may be removed under the audit."""

    candidate_id: str
    eligible: bool
    blockers: tuple[str, ...] = ()
    contract_version: str = AUDIT_CONTRACT_VERSION


def evaluate_removal_eligibility(candidate_id: str) -> RemovalEligibility:
    """Refuse removal until a verified replacement with drained consumers exists."""

    row = get_ownership_row(candidate_id)
    blockers = [
        "no verified upstream replacement contract at the implementation pin",
        f"persisted consumers not drained: {row.persisted_consumers[0][:80]}...",
        f"removal criteria unmet: {row.removal_criteria[:80]}...",
    ]
    try:
        check_audit_baseline_current()
    except UpstreamDuplicationAuditError as exc:
        blockers.insert(0, str(exc))
    return RemovalEligibility(
        candidate_id=row.candidate_id, eligible=False, blockers=tuple(blockers)
    )


# Production-boundary coverage the audit must preserve (issue acceptance §3):
# each boundary maps to the ownership row and production entrypoint that owns
# it, so coverage is caller-backed rather than aspirational.
PRODUCTION_BOUNDARY_COVERAGE: tuple[tuple[str, str], ...] = (
    ("launch", "wire-transport-vs-host-protocol"),
    ("auth/profile admission", "immutable-profile-snapshot-vs-launch-args"),
    ("session/turn control", "workflow-binding-vs-upstream-session"),
    ("retry/cancel", "workflow-binding-vs-upstream-session"),
    ("terminal evidence", "artifact-manifest-vs-session-files"),
    ("post-host-cleanup reads", "artifact-manifest-vs-session-files"),
    ("publication", "artifact-manifest-vs-session-files"),
    ("cleanup", "credential-lease-vs-runner-token"),
)


@dataclass(frozen=True, slots=True)
class AuditReductionSummary:
    """Behavior-preserving reduction measured after the audit (no quota)."""

    candidates_examined: int = field(default=len(OWNERSHIP_TABLE))
    preserved: int = field(default=len(OWNERSHIP_TABLE))
    removed_tables_or_fields: int = 0
    residual_dependencies: tuple[str, ...] = tuple(
        row.candidate_id for row in OWNERSHIP_TABLE
    )
    contract_version: str = AUDIT_CONTRACT_VERSION


def audit_reduction_summary() -> AuditReductionSummary:
    """Report the measured reduction: behavior preserved, nothing removed."""

    return AuditReductionSummary()


__all__ = [
    "AUDITED_UPSTREAM_COMMIT",
    "AUDIT_CONTRACT_VERSION",
    "AUDIT_ISSUE",
    "OWNERSHIP_TABLE",
    "PRODUCTION_BOUNDARY_COVERAGE",
    "AuditReductionSummary",
    "RemovalEligibility",
    "UpstreamDuplicationAuditError",
    "UpstreamOwnershipRow",
    "audit_reduction_summary",
    "check_audit_baseline_current",
    "check_credential_scope",
    "check_evidence_present",
    "check_generation_match",
    "check_owner_match",
    "check_route_allowed",
    "check_upstream_pin",
    "evaluate_removal_eligibility",
    "get_ownership_row",
]
