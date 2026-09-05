"""The execution Activity confirms workflow-admitted capacity; it never queues.

Source issue: MoonLadderStudios/MoonMind#3878 (invariants 6, 10, 11, 12).

Once the AgentRun workflow owns the Provider Profile lease, the Activity's job
changes from *acquire and release* to *confirm and leave alone*. Two failure
modes are worth failing loudly on: an Activity that accepts a fresh grant while
believing it is confirming one (the ledger then double-counts), and an Activity
or janitor that releases capacity a live workflow still owns.
"""

from __future__ import annotations

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


class _LeaseClient:
    def __init__(self, *, already_held: bool = True) -> None:
        self.already_held = already_held
        self.acquired: list[dict] = []
        self.released: list[str] = []
        self.released_fences: list[int | None] = []

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
        return {"active": True}

    async def release_lease(self, lease):
        self.released.append(lease.lease_id)
        self.released_fences.append(lease.fencing_generation)


def _admitted(profile_ref: str = "opencode-zen-free", owner: str = "agent-run-1"):
    return SimpleNamespace(provider_profile_ref=profile_ref, lease_owner_id=owner)


@pytest.mark.asyncio
async def test_workflow_admitted_capacity_is_confirmed_under_the_workflow_owner():
    """The manager already tracks the workflow as owner; reuse that identity."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )

    acquired = await coordinator.acquire_all(
        plan=_plan("opencode-zen-free"),
        workflow_id="workflow-1",
        step_execution_id="step-1",
        idempotency_key="idem-1",
        admitted_capacity=_admitted(),
    )

    assert len(acquired) == 1
    assert acquired[0].owned_by_workflow is True
    assert client.acquired[0]["owner_id"] == "agent-run-1"
    assert client.acquired[0]["owner_is_workflow"] is True
    assert client.acquired[0]["metadata"]["workflowId"] == "agent-run-1"
    assert acquired[0].lease.purpose is CredentialLeasePurpose.EXECUTION_OMNIGENT


@pytest.mark.asyncio
async def test_activity_never_releases_a_workflow_owned_lease():
    """Invariant 10: provider capacity is released last, by its owner."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )
    acquired = await coordinator.acquire_all(
        plan=_plan("opencode-zen-free"),
        workflow_id="workflow-1",
        step_execution_id="step-1",
        idempotency_key="idem-1",
        admitted_capacity=_admitted(),
    )

    await coordinator.release_all(acquired)

    assert client.released == []


@pytest.mark.asyncio
async def test_activity_owned_lease_is_still_released_by_the_activity():
    """The pre-#3878 shape is unchanged when no capacity is admitted."""

    client = _LeaseClient(already_held=False)
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )
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


@pytest.mark.asyncio
async def test_a_fresh_grant_where_a_held_lease_was_promised_fails_closed():
    """Accepting it would double-count the ledger and leave two releasers."""

    client = _LeaseClient(already_held=False)
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("opencode-zen-free"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted(),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_admitted_capacity_for_a_different_profile_fails_closed():
    """Invariant 12: the Activity must not acquire unadmitted capacity."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("opencode-zen-free"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted(profile_ref="some-other-profile"),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"
    assert client.acquired == []


@pytest.mark.asyncio
async def test_multi_profile_plan_rejects_single_admitted_capacity():
    """One admitted profile cannot stand in for a plan that selected several."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("profile-a", "profile-b")),
        lease_client=client,
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("profile-a", "profile-b"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted(profile_ref="profile-a"),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"


@pytest.mark.asyncio
async def test_admitted_capacity_without_a_lease_owner_fails_closed():
    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("opencode-zen-free")),
        lease_client=client,
    )

    with pytest.raises(HarnessPlatformError) as exc_info:
        await coordinator.acquire_all(
            plan=_plan("opencode-zen-free"),
            workflow_id="workflow-1",
            step_execution_id="step-1",
            idempotency_key="idem-1",
            admitted_capacity=_admitted(owner=""),
        )

    assert exc_info.value.code == "OMNIGENT_PROVIDER_LEASE_UNAVAILABLE"


def _persisted_binding(
    *, owner_is_workflow: bool, fencing_generation: int | None = None
) -> dict:
    binding = {
        "providerProfileRef": "opencode-zen-free",
        "providerLeaseRef": "provider-profile-lease:lease-1",
        "runtimeId": "opencode",
        "leaseOwnerId": "agent-run-1",
        "leasePurpose": CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
        "leaseOwnerIsWorkflow": owner_is_workflow,
    }
    if fencing_generation is not None:
        binding["leaseFencingGeneration"] = fencing_generation
    return {"primary-model": binding}


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
async def test_janitor_recovery_releases_the_exact_grant_the_binding_owns():
    """MoonLadderStudios/MoonMind#3879: the persisted handle carries its fence."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory({}), lease_client=client
    )

    await coordinator.release_from_binding(
        _persisted_binding(owner_is_workflow=False, fencing_generation=7)
    )

    assert client.released == ["lease-1"]
    # Quoting the generation is what stops recovery from freeing whatever this
    # deterministic owner ID happens to hold by the time the janitor runs.
    assert client.released_fences == [7]


@pytest.mark.asyncio
async def test_a_binding_without_a_fence_still_frees_capacity():
    """A handle persisted before fenced grants must stay reclaimable."""

    client = _LeaseClient()
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory({}), lease_client=client
    )

    await coordinator.release_from_binding(
        _persisted_binding(owner_is_workflow=False)
    )

    assert client.released == ["lease-1"]
    assert client.released_fences == [None]


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
