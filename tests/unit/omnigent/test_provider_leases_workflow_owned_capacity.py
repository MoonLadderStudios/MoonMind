"""The execution Activity consumes workflow-admitted capacity; it never queues.

Source issues: MoonLadderStudios/MoonMind#3878 (invariants 6, 10, 11, 12) and
MoonLadderStudios/MoonMind#3880 (remaining implementation 1-3, AC2).

Once the AgentRun workflow owns the Provider Profile lease, the Activity's job
changes from *acquire and release* to *inspect and leave alone*. The dangerous
shape is an Activity that reaches for an acquiring client at all: an expired or
missing admission would then wait for, or be granted, capacity nobody admitted,
the ledger would double-count, and two releasers would exist. So the admitted
path here never calls ``acquire_execution_lease``, and every inspection must
positively establish the complete admitted identity — absence is never
acceptance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.provider_leases import OmnigentProviderLeaseCoordinator
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AdmittedProviderCapacity


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def get(self, _model, key):
        return self._rows.get(key)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_factory(rows):
    return lambda: _Session(rows)


def _profiles(*profile_ids: str) -> dict[str, SimpleNamespace]:
    return {
        profile_id: SimpleNamespace(
            enabled=True,
            auth_state="connected",
            runtime_id="opencode",
            capacity_scope_ref=f"provider-profile:{profile_id}",
            credential_generation=3,
        )
        for profile_id in profile_ids
    }


def _plan(*profile_refs: str):
    """A generic-host plan whose credential bindings name the given profiles."""

    model = "opencode-go/model"
    digest = compute_model_config_digest(
        qualifiedId=model,
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "3" * 64,
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": (
                "omnigent-credential-bindings:primary@1#sha256:" + "5" * 64
            ),
            "credentialBindings": {
                f"slot-{index}": {
                    "providerProfileRef": profile_ref,
                    # OpenCode Zen is credentialless: no shared auth home.
                    "materializerRef": "none@1",
                }
                for index, profile_ref in enumerate(profile_refs)
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
    )


def _plan_ref(*profile_refs: str) -> str:
    return _plan(*profile_refs).planRef


def _inspection(
    *,
    profile_ref: str = "opencode-zen-free",
    owner: str = "agent-run-1",
    plan_ref: str | None = None,
    step_execution_id: str = "step-1",
    idempotency_key: str = "idem-1",
    expires_in_seconds: int = 900,
    **overrides,
) -> dict:
    """The manager's ledger view of a live, workflow-owned admitted lease."""

    payload = {
        "active": True,
        "lease_id": owner,
        "profile_id": profile_ref,
        "leaseId": owner,
        "ownerId": owner,
        "ownerIsWorkflow": True,
        "purpose": CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
        "executionPlanRef": plan_ref if plan_ref is not None else _plan_ref(
            profile_ref
        ),
        "stepExecutionId": step_execution_id,
        "idempotencyKey": idempotency_key,
        "credentialGeneration": 3,
        "expiresAt": (
            datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
        ).isoformat(),
    }
    payload.update(overrides)
    return payload


class _LeaseClient:
    def __init__(self, *, already_held: bool = True, inspection=None) -> None:
        self.already_held = already_held
        self.acquired: list[dict] = []
        self.released: list[str] = []
        self.inspected: list[CredentialLease] = []
        self._inspection = inspection

    async def acquire_execution_lease(self, **kwargs):
        self.acquired.append(dict(kwargs))
        return CredentialLease(
            profile_id=kwargs["profile_id"],
            runtime_id=kwargs["runtime_id"],
            lease_id=f"lease-{kwargs['profile_id']}",
            owner_id=kwargs["owner_id"],
            purpose=kwargs["purpose"],
            already_held=self.already_held,
        )

    async def inspect_lease(self, lease):
        self.inspected.append(lease)
        if self._inspection is None:
            return {"active": True}
        if callable(self._inspection):
            return self._inspection(lease)
        return self._inspection

    async def release_lease(self, lease):
        self.released.append(lease.lease_id)


def _admitted(
    *profile_refs: str,
    owner: str = "agent-run-1",
    plan_ref: str | None = None,
    step_execution_id: str | None = "step-1",
    idempotency_key: str | None = "idem-1",
    credential_generation: int | None = 3,
) -> AdmittedProviderCapacity:
    """A real v2 ticket, exactly as the AgentRun workflow builds it."""

    refs = profile_refs or ("opencode-zen-free",)
    return AdmittedProviderCapacity.model_validate(
        {
            "leaseOwnerId": owner,
            "profiles": [
                {
                    "providerProfileRef": ref,
                    "providerRuntimeId": "opencode",
                    "capacityScopeRef": f"provider-profile:{ref}",
                    "credentialGeneration": credential_generation,
                }
                for ref in sorted(refs)
            ],
            "executionPlanRef": (
                plan_ref if plan_ref is not None else _plan_ref(*refs)
            ),
            "stepExecutionId": step_execution_id,
            "idempotencyKey": idempotency_key,
            "admissionEpoch": 1,
        }
    )


def _coordinator(client, *profile_refs: str) -> OmnigentProviderLeaseCoordinator:
    return OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(
            _profiles(*(profile_refs or ("opencode-zen-free",)))
        ),
        lease_client=client,
    )


async def _consume(coordinator, client, *profile_refs: str, admitted=None):
    refs = profile_refs or ("opencode-zen-free",)
    return await coordinator.acquire_all(
        plan=_plan(*refs),
        workflow_id="workflow-1",
        step_execution_id="step-1",
        idempotency_key="idem-1",
        admitted_capacity=admitted if admitted is not None else _admitted(*refs),
    )


@pytest.mark.asyncio
async def test_admitted_capacity_is_consumed_without_any_acquisition():
    """Invariant 6 / #3880: inspection is the whole handoff — nothing is acquired."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)

    acquired = await _consume(coordinator, client)

    assert len(acquired) == 1
    assert acquired[0].owned_by_workflow is True
    assert acquired[0].credential_generation == 3
    assert acquired[0].lease.owner_id == "agent-run-1"
    assert acquired[0].lease.purpose is CredentialLeasePurpose.EXECUTION_OMNIGENT
    # The acquiring client is never reached, so an expired or missing admission
    # can never be silently replaced by a fresh grant or a wait.
    assert client.acquired == []
    assert [lease.lease_id for lease in client.inspected] == ["agent-run-1"]


@pytest.mark.asyncio
async def test_activity_never_releases_a_workflow_owned_lease():
    """Invariant 10: provider capacity is released last, by its owner."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)
    acquired = await _consume(coordinator, client)

    await coordinator.release_all(acquired)

    assert client.released == []


@pytest.mark.asyncio
async def test_activity_owned_lease_is_still_released_by_the_activity():
    """The pre-#3878 shape is unchanged when no capacity is admitted."""

    client = _LeaseClient(already_held=False, inspection={"active": True})
    coordinator = _coordinator(client)
    acquired = await coordinator.acquire_all(
        plan=_plan("opencode-zen-free"),
        workflow_id="workflow-1",
        step_execution_id="step-1",
        idempotency_key="idem-1",
    )

    assert acquired[0].owned_by_workflow is False
    assert client.acquired[0]["owner_is_workflow"] is False
    assert client.acquired[0]["owner_id"] != "workflow-1"

    await coordinator.release_all(acquired)

    assert client.released == ["lease-opencode-zen-free"]


@pytest.mark.parametrize(
    "inspection, why",
    [
        ({}, "an empty payload"),
        ("not-a-mapping", "a malformed payload"),
        ({"lease_id": "agent-run-1"}, "no active flag at all"),
        ({"active": False, "lease_id": "agent-run-1"}, "a revoked lease"),
        ({"active": "yes"}, "a non-boolean active flag"),
    ],
)
@pytest.mark.asyncio
async def test_incomplete_lease_inspection_fails_closed(inspection, why):
    """AC2: absence of evidence is never evidence of an admitted lease."""

    client = _LeaseClient(inspection=inspection)
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(coordinator, client)

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE", why
    # Failing closed must not fall back to acquiring the capacity instead.
    assert client.acquired == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile_id": "some-other-profile"},
        {"ownerId": "agent-run-9"},
        {"ownerIsWorkflow": False},
        {"expiresAt": None},
        {"expiresAt": "not-a-timestamp"},
        {"stepExecutionId": "step-9"},
        {"idempotencyKey": "idem-9"},
        {"executionPlanRef": "omnigent-execution-plan:sha256:" + "f" * 64},
    ],
)
@pytest.mark.asyncio
async def test_a_lease_that_is_not_this_admission_fails_closed(overrides):
    """Revoked, expired, wrong-owner and wrong-plan handles all fail closed."""

    client = _LeaseClient(inspection=_inspection(**overrides))
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(coordinator, client)

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"
    assert client.acquired == []


@pytest.mark.asyncio
async def test_an_expired_admitted_lease_fails_closed():
    """A ticket that outlived its grant must not be consumed."""

    client = _LeaseClient(inspection=_inspection(expires_in_seconds=-1))
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(coordinator, client)

    assert "expired" in str(exc_info.value)
    assert client.acquired == []


@pytest.mark.asyncio
async def test_credentials_rotated_during_the_wait_fail_before_any_side_effect():
    """#3880: the generation is fenced at the handoff, not re-read after it.

    The run was admitted at generation 3 and the manager recorded that. The
    profile has since rotated to 4. Re-reading the generation here would
    silently adopt the rotation; comparing makes it an explicit, typed
    re-admission that the workflow owner performs.
    """

    rows = _profiles("opencode-zen-free")
    rows["opencode-zen-free"].credential_generation = 4
    client = _LeaseClient(inspection=_inspection())
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(rows), lease_client=client
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(coordinator, client)

    assert exc_info.value.code == "OMNIGENT_CREDENTIAL_GENERATION_FENCED"
    assert client.acquired == []


@pytest.mark.asyncio
async def test_a_ticket_that_disagrees_with_its_own_grant_fails_closed():
    """The manager recorded which generation it granted against; they must agree."""

    client = _LeaseClient(inspection=_inspection(credentialGeneration=3))
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(
            coordinator, client, admitted=_admitted(credential_generation=2)
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"
    assert "credential generation" in str(exc_info.value)
    assert client.acquired == []


@pytest.mark.asyncio
async def test_a_ticket_for_another_execution_plan_fails_closed():
    """The admitted capacity is bound to the exact committed plan."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(
            coordinator,
            client,
            admitted=_admitted(
                plan_ref="omnigent-execution-plan:sha256:" + "a" * 64
            ),
        )

    assert exc_info.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"
    assert client.inspected == []


@pytest.mark.asyncio
async def test_admitted_capacity_for_a_different_profile_fails_closed():
    """Invariant 12: the Activity must not bind unadmitted capacity."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("opencode-zen-free"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted("some-other-profile"),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"
    assert client.acquired == []
    assert client.inspected == []


@pytest.mark.asyncio
async def test_multi_profile_plan_rejects_single_admitted_capacity():
    """One admitted profile cannot stand in for a plan that selected several."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client, "profile-a", "profile-b")

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("profile-a", "profile-b"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted("profile-a"),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"


@pytest.mark.asyncio
async def test_admitted_capacity_without_a_lease_owner_fails_closed():
    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(
            coordinator,
            client,
            admitted=SimpleNamespace(lease_owner_id="", profiles=()),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_a_retained_v1_ticket_is_still_consumed_by_inspection():
    """Retained histories replay: the v1 shape bound no plan, step or generation.

    Temporal redelivers the original Activity input on retry, so a ticket
    recorded before #3880 must still work. It carries less authority, so only
    the identities it actually recorded are checked — but it is still consumed
    by inspection, never by acquisition.
    """

    v1 = AdmittedProviderCapacity.model_validate(
        {
            "providerProfileRef": "opencode-zen-free",
            "providerRuntimeId": "opencode",
            "leaseOwnerId": "agent-run-1",
            "capacityScopeRef": "opencode-zen:contributor-free",
        }
    )
    client = _LeaseClient(
        inspection=_inspection(
            executionPlanRef=None, stepExecutionId=None, idempotencyKey=None
        )
    )
    coordinator = _coordinator(client)

    acquired = await _consume(coordinator, client, admitted=v1)

    assert acquired[0].owned_by_workflow is True
    assert acquired[0].capacity_scope_ref == "opencode-zen:contributor-free"
    # The generation was never recorded, so the value observed here is sticky.
    assert acquired[0].credential_generation == 3
    assert client.acquired == []


@pytest.mark.asyncio
async def test_a_disabled_profile_fails_before_any_credential_resolution():
    """Consumption still refuses a profile that is no longer launch ready."""

    rows = _profiles("opencode-zen-free")
    rows["opencode-zen-free"].enabled = False
    client = _LeaseClient(inspection=_inspection())
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(rows), lease_client=client
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await _consume(coordinator, client)

    assert exc_info.value.code == "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"


@pytest.mark.asyncio
async def test_a_retried_activity_attempt_reuses_the_same_stable_lease():
    """Requirement 3: retry reuses the binding; it never creates a second one."""

    client = _LeaseClient(inspection=_inspection())
    coordinator = _coordinator(client)
    admitted = _admitted()

    first = await _consume(coordinator, client, admitted=admitted)
    second = await _consume(coordinator, client, admitted=admitted)

    assert first[0].provider_lease_ref == second[0].provider_lease_ref
    assert first[0].lease.owner_id == second[0].lease.owner_id
    assert client.acquired == []


def _persisted_binding(*, owner_is_workflow: bool) -> dict:
    return {
        "primary-model": {
            "providerProfileRef": "opencode-zen-free",
            "providerLeaseRef": "provider-profile-lease:lease-1",
            "runtimeId": "opencode",
            "leaseOwnerId": "agent-run-1",
            "leasePurpose": CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
            "leaseOwnerIsWorkflow": owner_is_workflow,
        }
    }


@pytest.mark.asyncio
async def test_janitor_recovery_leaves_workflow_owned_capacity_alone():
    """Invariants 10 and 11: reclaiming a live run's lease would double-release."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory({}), lease_client=client
    )

    await coordinator.release_from_binding(
        _persisted_binding(owner_is_workflow=True)
    )

    assert client.released == []


@pytest.mark.asyncio
async def test_janitor_recovery_still_reclaims_activity_owned_capacity():
    """The safety net must keep working for leases no workflow owns."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory({}), lease_client=client
    )

    await coordinator.release_from_binding(
        _persisted_binding(owner_is_workflow=False)
    )

    assert client.released == ["lease-1"]


@pytest.mark.asyncio
async def test_pre_patch_bindings_without_the_flag_stay_reclaimable():
    """A binding persisted before #3878 has no owner flag and is Activity-owned."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory({}), lease_client=client
    )
    binding = _persisted_binding(owner_is_workflow=False)
    del binding["primary-model"]["leaseOwnerIsWorkflow"]

    await coordinator.release_from_binding(binding)

    assert client.released == ["lease-1"]
