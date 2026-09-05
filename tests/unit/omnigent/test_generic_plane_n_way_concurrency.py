"""N concurrent OpenCode Zen executions on the generic Omnigent plane.

Source issue: MoonLadderStudios/MoonMind#3878 (AC3, invariants 2, 7, 10, 12).

This is the acceptance journey the program was missing: ``N`` real
``GenericOmnigentHostRealizer.execute`` calls running at once against one shared
machine ledger, for the first required supported combination —

    OpenCode Zen Contributor Free + opencode-native + none@1
    + generic-omnigent-host@1 + on-demand run-dedicated hosts

It proves the property that makes concurrency safe rather than merely possible:
every admitted run receives its *own* runtime binding, host, container, state
volume, workspace, session, and cleanup authority, and every one of those is
cleaned up. A bug that shares any of them between two runs shows up here as a
duplicate identity, which is exactly how such a bug would manifest in
production.

The realizer is real. Only the substrate below it (Docker, Omnigent endpoint,
credential materialization, control-plane persistence) is in-memory, so the
lifecycle ordering and authority handoffs under test are the production ones.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.credential_materializers import CredentialRuntimeHandle
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import (
    HostClass,
    get_launch_policy,
)
from moonmind.omnigent.host_capacity import (
    LIMITING_LAYER_HOST_CAPACITY,
    GenericHostCapacityAdmission,
)
from moonmind.omnigent.host_runtime import PreparedHostInputs
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.host_leases import InMemoryOmnigentHostLeaseRepository
from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
)

#: The deployment-selectable ceilings the generic plane must stay correct for.
#: MoonLadderStudios/MoonMind#3880 AC7 requires the chosen 8 and 16 execution
#: rows: nothing between the deployment setting and the ledger may impose a
#: lower, undocumented cap.
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]

ZEN_PROFILE = "opencode-zen-free"
ZEN_MODEL = "opencode/muse-spark-1.2-contributor-free"


def _zen_plan(run: str):
    """An immutable plan for the required combination, distinct per run."""

    digest = compute_model_config_digest(
        qualifiedId=ZEN_MODEL,
        effort=None,
        routeRef="opencode",
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
                "primary-model": {
                    "providerProfileRef": ZEN_PROFILE,
                    # Credentialless: no shared mutable authentication home.
                    "materializerRef": "none@1",
                }
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": ZEN_MODEL,
                "effort": None,
                "routeRef": "opencode",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": f"artifact:skills:{run}",
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


_HOST_CLASS = HostClass.model_validate(
    {
        "hostClassId": "omnigent-opencode",
        "version": 1,
        "imageRef": "ghcr.io/example/opencode@sha256:" + "f" * 64,
        "omnigentVersion": "0.11.0",
        "omnigentBuildDigest": "sha256:" + "1" * 64,
        "architectures": ["linux/amd64"],
        "declaredHarnessImplementations": [
            {
                "harnessId": "opencode-native",
                "implementationRef": "omnigent-harness-implementation:sha256:"
                + "3" * 64,
                "runtimeDependencies": [{"name": "opencode", "version": "1.18.11"}],
            }
        ],
        "integrationModes": ["native-server"],
        "materializerRefs": ["none@1"],
        "features": {
            "workspaceBind": True,
            "restrictedEgress": True,
            "mountedSkills": True,
        },
        "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
    }
)


class _Machine:
    """One shared, observable substrate for every concurrent run."""

    def __init__(self) -> None:
        self.allocated_hosts: set[str] = set()
        self.peak_hosts = 0
        self.launched: list[dict[str, str]] = []
        self.sessions: list[str] = []
        self.workspaces: list[str] = []
        self.cleanups: list[str] = []
        self.credentials_cleaned: list[str] = []
        self.provider_releases: list[str] = []
        self.runtime_bindings: list[str] = []
        self.admitted_capacity: list[Any] = []

    def allocate(self, run: str) -> None:
        self.allocated_hosts.add(run)
        self.peak_hosts = max(self.peak_hosts, len(self.allocated_hosts))

    def free(self, run: str) -> None:
        self.allocated_hosts.discard(run)


class _LedgerAdmission(GenericHostCapacityAdmission):
    """Aggregate admission over the shared machine instead of a database."""

    def __init__(self, machine: _Machine, *, host_capacity: int) -> None:
        super().__init__(
            session_factory=None,
            host_capacity=host_capacity,
            cold_launch_burst=1024,
            cold_launch_window_seconds=30,
        )
        self._machine = machine

    async def observe(self, *, now=None) -> tuple[int, int]:
        return len(self._machine.allocated_hosts), 0


def _request(run: str, *, workflow_owned: bool) -> AgentExecutionRequest:
    admitted = (
        {
            "leaseOwnerId": f"agent-run-{run}",
            "profiles": [
                {
                    "providerProfileRef": ZEN_PROFILE,
                    "providerRuntimeId": "opencode",
                    "capacityScopeRef": "opencode-zen:contributor-free",
                    "credentialGeneration": 4,
                }
            ],
            "executionPlanRef": _zen_plan(run).planRef,
            "agentRunWorkflowId": f"agent-run-{run}",
            "stepExecutionId": f"step-{run}",
            "idempotencyKey": f"idem-{run}",
            "admissionEpoch": 1,
        }
        if workflow_owned
        else None
    )
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": ZEN_PROFILE,
            "correlationId": f"workflow-{run}",
            "idempotencyKey": f"idem-{run}",
            "parameters": {"publishMode": "none"},
            "admittedProviderCapacity": admitted,
        }
    )


def _build_realizer(
    run: str,
    machine: _Machine,
    *,
    admission: _LedgerAdmission | None,
) -> GenericOmnigentHostRealizer:
    """Build the run's own service graph, exactly as production does per run."""

    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref=ZEN_PROFILE,
        capacity_scope_ref="opencode-zen:contributor-free",
        provider_lease_ref=f"provider-profile-lease:lease-{run}",
        credential_generation=4,
        lease=CredentialLease(
            profile_id=ZEN_PROFILE,
            runtime_id="opencode",
            lease_id=f"lease-{run}",
            owner_id=f"agent-run-{run}",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
        owned_by_workflow=True,
    )

    class Leases:
        async def acquire_all(self, **kwargs):
            # Record what the realizer forwarded so a dropped hand-off of the
            # workflow-owned capacity is visible rather than silently ignored.
            machine.admitted_capacity.append(kwargs.get("admitted_capacity"))
            return (acquired,)

        async def release_all(self, leases):
            for item in leases:
                if not item.owned_by_workflow:
                    machine.provider_releases.append(run)

    handle = CredentialRuntimeHandle.model_validate(
        {
            "credentialRuntimeRef": f"credential-runtime:sha256:{run:>064}".replace(
                " ", "0"
            ),
            "providerProfileRef": ZEN_PROFILE,
            "providerLeaseRef": f"provider-profile-lease:lease-{run}",
            "credentialGeneration": 4,
            "materializerRef": "none@1",
            "attachments": [],
            "cleanupRef": f"credential-cleanup:{run}",
            "attestationRef": f"artifact://credential-attestation/{run}",
        }
    )

    class Credentials:
        async def materialize_all(self, **_kwargs):
            return (handle,)

        async def load_cleanup_handles(self, *_args):
            return (handle,)

        async def cleanup_all(self, handles):
            for item in handles:
                machine.credentials_cleaned.append(item.cleanupRef)
            return ()

    class HostRuntime:
        async def prepare(self, **kwargs):
            await kwargs["authority_sink"](
                {"kind": "skills", "cleanupRef": f"skill-cleanup:{run}"}
            )
            return PreparedHostInputs(
                workspace_attachment={
                    "kind": "bind",
                    "sourceRef": f"/work/agent_jobs/{run}/repo",
                    "targetPath": "/workspaces/run",
                    "accessMode": "read-write",
                },
                skill_attachment={
                    "kind": "bind",
                    "sourceRef": f"/tmp/skills/{run}",
                    "targetPath": "/opt/moonmind-skills",
                    "accessMode": "read-only",
                    "deliveryRef": f"skill-delivery:{run}",
                },
                tool_attachments=(),
                egress_attestation={
                    "networkRef": f"egress-{run}",
                    "attestationRef": f"artifact://egress/{run}",
                },
            )

        async def realize(self, **_kwargs):
            machine.allocate(run)
            # A cold launch is not instantaneous; overlap is what makes this a
            # concurrency test rather than N sequential runs.
            await asyncio.sleep(0.01)
            record = {
                "omnigentHostId": f"host-{run}",
                "hostId": f"host-{run}",
                "containerName": f"mm-host-{run}",
                "stateVolumeRef": f"mm-state-{run}",
                "hostClassRef": "omnigent-opencode@1",
                "launchPolicyRef": "omnigent-on-demand@1",
                "workspacePath": f"/work/agent_jobs/{run}/repo",
                "hostHarnessAttestationRef": f"artifact://host/{run}",
                "modelOptionAttestationRef": f"artifact://models/{run}",
                "hostCleanupRef": f"host-cleanup:{run}",
            }
            machine.launched.append(record)
            return record

        async def cleanup(self, **_kwargs):
            machine.free(run)
            machine.cleanups.append(f"host-cleanup:{run}")
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
            machine.cleanups.append(f"inputs-cleanup:{run}")

    async def resolve_host(_plan):
        return _HOST_CLASS, get_launch_policy("omnigent-on-demand@1")

    async def session_driver(request, *, session_authority_sink):
        session_id = f"session-{run}"
        await session_authority_sink.session_created(session_id)
        omnigent = request.parameters["omnigent"]
        # Each run drives its own host through its own session.
        assert omnigent["session"]["hostId"] == f"host-{run}"
        machine.sessions.append(session_id)
        # Read the workspace and runtime-binding identity the realizer actually
        # produced, not a value this harness supplied.
        machine.workspaces.append(omnigent["session"]["workspace"])
        machine.runtime_bindings.append(
            omnigent["session"]["labels"]["moonmind.runtime_binding_id"]
        )
        await asyncio.sleep(0.01)
        return AgentRunResult(
            summary=f"done-{run}", metadata={"omnigentSessionId": session_id}
        )

    class SessionCleanup:
        async def drain(self, session_id):
            machine.cleanups.append(f"session-drain:{session_id}")
            return {"sessionId": session_id, "stopped": True}

    class WorkspacePublisher:
        async def publish_request_workspace(self, **_kwargs):
            return {"push_status": "skipped"}

    class TurnCommands:
        async def claim(self, **_kwargs):
            return SimpleNamespace(
                owns_delivery=True,
                session_id=f"oms_generic_{run}",
                fencing_generation=1,
            )

        async def attach_provider_session(self, **_kwargs):
            return None

        async def settle(self, **_kwargs):
            return None

    return GenericOmnigentHostRealizer(
        runtime_binding_store=InMemoryStableRuntimeBindingStore(),
        provider_lease_coordinator=Leases(),
        credential_provisioning_service=Credentials(),
        host_lease_repository=InMemoryOmnigentHostLeaseRepository(),
        host_runtime=HostRuntime(),
        planned_host_resolver=resolve_host,
        session_driver=session_driver,
        session_cleanup_service=SessionCleanup(),
        workspace_publisher=WorkspacePublisher(),
        turn_command_service=TurnCommands(),
        host_capacity_admission=admission,
        deployment_validator=lambda _payload: None,
        heartbeat_interval_seconds=0.005,
        heartbeat_ttl_seconds=60,
    )


async def _run_n(
    concurrency: int, *, host_capacity: int | None = None
) -> tuple[_Machine, list[Any]]:
    machine = _Machine()
    capacity = host_capacity if host_capacity is not None else concurrency
    runs = [str(index) for index in range(concurrency)]
    results = await asyncio.gather(
        *(
            _build_realizer(
                run, machine, admission=_LedgerAdmission(machine, host_capacity=capacity)
            ).execute(_request(run, workflow_owned=True), _zen_plan(run))
            for run in runs
        ),
        return_exceptions=True,
    )
    return machine, results


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_n_concurrent_zen_runs_all_complete(concurrency: int) -> None:
    machine, results = await _run_n(concurrency)

    assert [getattr(item, "summary", item) for item in results] == [
        f"done-{index}" for index in range(concurrency)
    ]
    # Real overlap, not N sequential executions.
    assert machine.peak_hosts == concurrency


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_every_run_receives_distinct_execution_authority(
    concurrency: int,
) -> None:
    """Invariant 2: no host, container, volume, workspace, or session is shared."""

    machine, _ = await _run_n(concurrency)

    for key in (
        "omnigentHostId",
        "containerName",
        "stateVolumeRef",
        "workspacePath",
        "hostCleanupRef",
    ):
        values = [record[key] for record in machine.launched]
        assert len(set(values)) == concurrency, f"{key} was shared between runs"

    assert len(set(machine.sessions)) == concurrency
    assert len(set(machine.workspaces)) == concurrency
    assert len(set(machine.runtime_bindings)) == concurrency


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_every_run_cleans_up_its_own_authority(concurrency: int) -> None:
    """A leaked host at concurrency N is N times the leak, and blocks admission."""

    machine, _ = await _run_n(concurrency)

    for index in range(concurrency):
        assert f"host-cleanup:{index}" in machine.cleanups
        assert f"inputs-cleanup:{index}" in machine.cleanups
        assert f"session-drain:session-{index}" in machine.cleanups
        assert f"credential-cleanup:{index}" in machine.credentials_cleaned
    assert machine.allocated_hosts == set()


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_the_activity_never_releases_workflow_owned_capacity(
    concurrency: int,
) -> None:
    """Invariant 10: capacity is released last, by the workflow that admitted it."""

    machine, _ = await _run_n(concurrency)

    assert machine.provider_releases == []


@pytest.mark.asyncio
async def test_aggregate_host_capacity_refuses_the_run_it_cannot_carry() -> None:
    """Invariant 7: a provider ceiling above machine capacity is still bounded."""

    machine, results = await _run_n(4, host_capacity=2)

    refusals = [item for item in results if isinstance(item, BaseException)]
    assert refusals, "an oversubscribed machine must refuse at least one run"
    for failure in refusals:
        assert isinstance(failure, HarnessPlatformError)
        assert failure.code == "OMNIGENT_HOST_CAPACITY_UNAVAILABLE"
        assert LIMITING_LAYER_HOST_CAPACITY in str(failure)
    # The machine is never oversubscribed, and refusal still cleans up.
    assert machine.peak_hosts <= 2
    assert machine.allocated_hosts == set()


@pytest.mark.asyncio
async def test_capacity_refusal_does_not_reroute_to_another_host_class() -> None:
    """Invariant 12: no fallback realizer, host class, or model on refusal."""

    machine, results = await _run_n(4, host_capacity=1)

    completed = [item for item in results if not isinstance(item, BaseException)]
    for record in machine.launched:
        assert record["hostClassRef"] == "omnigent-opencode@1"
        assert record["launchPolicyRef"] == "omnigent-on-demand@1"
    assert len(completed) + len(machine.launched) >= 1


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_each_run_forwards_its_own_admitted_capacity(concurrency: int) -> None:
    """Invariant 6: the Activity confirms the workflow's lease, not a fresh one."""

    machine, _ = await _run_n(concurrency)

    owners = [item.lease_owner_id for item in machine.admitted_capacity]
    assert sorted(owners) == sorted(
        f"agent-run-{index}" for index in range(concurrency)
    )
    assert all(
        item.profile_refs == (ZEN_PROFILE,) for item in machine.admitted_capacity
    )
    # Each ticket binds its own plan and request identity, so no run can consume
    # another's grant (MoonLadderStudios/MoonMind#3880 requirement 1).
    assert len({item.execution_plan_ref for item in machine.admitted_capacity}) == (
        concurrency
    )
    assert all(
        item.profiles[0].credential_generation == 4
        for item in machine.admitted_capacity
    )


@pytest.mark.asyncio
async def test_a_run_without_admitted_capacity_keeps_the_pre_patch_shape() -> None:
    """Omitting the field must not be read as an empty or forged authority."""

    machine = _Machine()
    realizer = _build_realizer("solo", machine, admission=None)

    result = await realizer.execute(
        _request("solo", workflow_owned=False), _zen_plan("solo")
    )

    assert result.summary == "done-solo"
    assert machine.admitted_capacity == [None]
