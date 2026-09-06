"""N concurrent OpenCode Zen executions on the generic Omnigent plane.

Source issues: MoonLadderStudios/MoonMind#3878 (AC3, invariants 2, 7, 10, 12)
and MoonLadderStudios/MoonMind#3885 (hermetic layer of the layered N-way
qualification program).

Overlap here is *observed*, not assumed. Every admitted execution holds a
controlled barrier inside its own live session until the effective limit has
been reached, and the peak is swept from the per-execution start/end windows
the substrate actually recorded. N executions that merely ran concurrently
under ``asyncio.gather`` would sweep to a peak of 1 and fail; only genuinely
simultaneous, useful execution sweeps to N.

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
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.concurrency_qualification import (
    DEFAULT_REPEATED_WAVE_THRESHOLDS,
    CleanupScanEntry,
    CleanupScanReport,
    ExecutionOverlapSample,
    ObservedOverlapEvidence,
    RepeatedWaveReport,
    WaveObservation,
)

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


class _ConcurrencyGate:
    """A controlled hold proving executions were simultaneously live.

    Each admitted execution parks here in the middle of its own session and is
    released only once ``expected`` executions are parked at the same time. A
    substrate that serialized the runs never reaches the release, so the wait
    times out and :attr:`satisfied` stays ``False`` — which is the assertion
    that separates real overlap from N sequential results.
    """

    def __init__(self, expected: int, *, timeout: float = 10.0) -> None:
        self._expected = max(1, expected)
        self._timeout = timeout
        self._arrived = 0
        self._released = asyncio.Event()
        self.satisfied = False
        self.peak_parked = 0

    async def hold(self) -> None:
        self._arrived += 1
        self.peak_parked = max(self.peak_parked, self._arrived)
        if self._arrived >= self._expected:
            self.satisfied = True
            self._released.set()
        try:
            await asyncio.wait_for(self._released.wait(), timeout=self._timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # Release the stragglers so the test reports the missing overlap
            # rather than hanging for every remaining execution's full budget.
            self._released.set()


@dataclass(frozen=True)
class _Faults:
    """Injected authority-handoff failures for specific runs.

    ``lost_ack`` runs perform the side effect and *then* raise, which is the
    ambiguous case the program has to survive: the caller cannot tell whether
    the mutation happened, so cleanup must still reclaim it and no second
    mutation may be authorized.
    """

    #: run id -> handoff point that fails for that run.
    points: dict[str, str] = field(default_factory=dict)
    lost_ack: bool = False

    def trips(self, run: str, point: str) -> bool:
        return self.points.get(run) == point


_NO_FAULTS = _Faults()


class _InjectedHandoffFailure(RuntimeError):
    """A deliberate failure at one named authority handoff."""


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
        self.first_messages: list[str] = []
        self.lease_mutations = 0
        self.registration_requests = 0
        self._origin = time.monotonic()
        #: run id -> (started_at, ended_at) seconds since this machine started.
        self.windows: dict[str, list[float]] = {}

    def _now(self) -> float:
        return time.monotonic() - self._origin

    def allocate(self, run: str) -> None:
        self.allocated_hosts.add(run)
        self.peak_hosts = max(self.peak_hosts, len(self.allocated_hosts))
        self.windows.setdefault(run, [self._now(), self._now()])[0] = self._now()

    def free(self, run: str) -> None:
        self.allocated_hosts.discard(run)
        window = self.windows.get(run)
        if window is not None:
            window[1] = self._now()

    def overlap_samples(self) -> tuple[ExecutionOverlapSample, ...]:
        """Return the observed execution windows, oldest first."""

        return tuple(
            ExecutionOverlapSample(
                execution_ref=f"run-{run}",
                started_at=window[0],
                ended_at=max(window[0], window[1]),
            )
            for run, window in sorted(self.windows.items())
        )

    def cleanup_scan(self) -> CleanupScanReport:
        """Return the honest post-run teardown scan for this machine.

        A host still allocated after every execution settled is an unresolved
        entry, so :attr:`CleanupScanReport.zero_leak` reports ``False`` instead
        of an empty-looking clean sweep.
        """

        from datetime import UTC, datetime

        entries = [
            CleanupScanEntry(
                resource_ref=f"host-{run}", kind="container", resolved=False
            )
            for run in sorted(self.allocated_hosts)
        ]
        entries.extend(
            CleanupScanEntry(
                resource_ref=record["containerName"],
                kind="container",
                resolved=record["hostCleanupRef"] in self.cleanups,
            )
            for record in self.launched
        )
        return CleanupScanReport(scanned_at=datetime.now(UTC), entries=tuple(entries))


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
    gate: _ConcurrencyGate | None = None,
    faults: _Faults = _NO_FAULTS,
) -> GenericOmnigentHostRealizer:
    """Build the run's own service graph, exactly as production does per run."""

    def _trip(point: str) -> None:
        """Raise at ``point`` when this run is the injected failure."""

        if faults.trips(run, point):
            raise _InjectedHandoffFailure(f"{point} failed for run {run}")

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
            machine.lease_mutations += 1
            _trip("provider_lease_confirmation")
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
            _trip("credential_preparation")
            return (handle,)

        async def load_cleanup_handles(self, *_args):
            return (handle,)

        async def cleanup_all(self, handles):
            for item in handles:
                machine.credentials_cleaned.append(item.cleanupRef)
            _trip("credential_cleanup")
            return ()

    class HostRuntime:
        async def prepare(self, **kwargs):
            await kwargs["authority_sink"](
                {"kind": "skills", "cleanupRef": f"skill-cleanup:{run}"}
            )
            _trip("workspace_preparation")
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

        async def realize(self, **kwargs):
            machine.allocate(run)
            machine.registration_requests += 1
            # Production records launch authority the moment the container
            # exists, before registration can fail, so an ambiguous launch is
            # still reclaimable. Modelling that ordering is what makes the
            # lost-acknowledgment case meaningful here.
            await kwargs["authority_sink"](
                {
                    "kind": "host",
                    "containerName": f"mm-host-{run}",
                    "stateVolumeRef": f"mm-state-{run}",
                    "hostCleanupRef": f"host-cleanup:{run}",
                }
            )
            # A cold launch is not instantaneous; overlap is what makes this a
            # concurrency test rather than N sequential runs.
            await asyncio.sleep(0.01)
            if not faults.lost_ack:
                # Ordinary failure: the container exists but registration never
                # produced a record, so cleanup has only the allocation to
                # reclaim.
                _trip("host_registration")
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
            if faults.lost_ack:
                # Lost acknowledgment: the host is registered and recorded, but
                # the caller never learns it. Cleanup must reclaim the same
                # host rather than authorize a second launch.
                _trip("host_registration")
            return record

        async def cleanup(self, **_kwargs):
            # A teardown that failed did not remove the container, so the
            # machine must still count it. Freeing first would manufacture the
            # clean scan this program exists to catch.
            _trip("host_teardown")
            machine.free(run)
            machine.cleanups.append(f"host-cleanup:{run}")
            return {"containerRemoved": True}

        async def cleanup_prepared(self, _prepared):
            machine.cleanups.append(f"inputs-cleanup:{run}")

    async def resolve_host(_plan):
        return _HOST_CLASS, get_launch_policy("omnigent-on-demand@1")

    async def session_driver(request, *, session_authority_sink):
        session_id = f"session-{run}"
        _trip("session_creation")
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
        # The first message is delivered exactly once per workflow, inside the
        # live session, and the run then holds the barrier so this session is
        # provably open while every other admitted session is open too.
        _trip("first_message")
        machine.first_messages.append(session_id)
        if gate is not None:
            await gate.hold()
        else:
            await asyncio.sleep(0.01)
        _trip("harvest")
        return AgentRunResult(
            summary=f"done-{run}", metadata={"omnigentSessionId": session_id}
        )

    class SessionCleanup:
        async def drain(self, session_id):
            machine.cleanups.append(f"session-drain:{session_id}")
            _trip("drain")
            return {"sessionId": session_id, "stopped": True}

    class WorkspacePublisher:
        async def publish_request_workspace(self, **_kwargs):
            return {"push_status": "skipped"}

    class TurnCommands:
        async def claim(self, **_kwargs):
            machine.lease_mutations += 1
            _trip("turn_claim")
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


@dataclass
class _Wave:
    """One executed concurrency wave and the evidence it produced."""

    machine: _Machine
    results: list[Any]
    gate: _ConcurrencyGate
    requested_level: int
    effective_limit: int

    def overlap(self, *, durable_waiters: int | None = None) -> ObservedOverlapEvidence:
        """Return the observed overlap evidence this wave actually produced.

        Constructing the evidence is itself an assertion: the model refuses a
        peak that does not match the effective limit, refuses samples that were
        not barrier-synchronized, and refuses work above the limit that was not
        observed waiting.
        """

        waiters = (
            durable_waiters
            if durable_waiters is not None
            else max(0, self.requested_level - self.effective_limit)
        )
        return ObservedOverlapEvidence(
            requested_level=self.requested_level,
            effective_limit=self.effective_limit,
            barrier_synchronized=self.gate.satisfied,
            samples=self.machine.overlap_samples(),
            durable_waiters=waiters,
        )

    @property
    def completed(self) -> list[Any]:
        return [item for item in self.results if not isinstance(item, BaseException)]

    @property
    def failures(self) -> list[BaseException]:
        return [item for item in self.results if isinstance(item, BaseException)]


#: Handoffs that trip only after a run has already parked on the barrier, so a
#: fault there does not reduce the number of executions expected to overlap.
_POST_PARK_HANDOFFS = frozenset(
    {"harvest", "drain", "host_teardown", "credential_cleanup"}
)


async def _run_wave(
    concurrency: int,
    *,
    host_capacity: int | None = None,
    faults: _Faults = _NO_FAULTS,
    machine: _Machine | None = None,
    offset: int = 0,
    expected_overlap: int | None = None,
) -> _Wave:
    """Execute one wave of ``concurrency`` runs against one shared machine.

    Every run that reaches a live session parks on a shared barrier sized to
    the effective limit, so the executions are provably simultaneous rather
    than merely submitted together. Runs the machine refuses never reach the
    barrier, which is why the barrier is sized to the limit and not to the
    submission count.

    ``offset`` gives a later wave distinct run identities, and
    ``expected_overlap`` states the limit when the machine already carries work
    from an earlier wave.
    """

    machine = machine if machine is not None else _Machine()
    capacity = host_capacity if host_capacity is not None else concurrency
    effective_limit = (
        expected_overlap
        if expected_overlap is not None
        else min(concurrency, capacity)
    )
    runs = [str(offset + index) for index in range(concurrency)]
    # A run whose session never opens cannot park, so the barrier expects only
    # the runs that are admitted *and* not failed before their first message.
    expected_parked = effective_limit - sum(
        1
        for run in runs[:effective_limit]
        for point in (faults.points.get(run),)
        if point is not None and point not in _POST_PARK_HANDOFFS
    )
    gate = _ConcurrencyGate(max(1, expected_parked))
    results = await asyncio.gather(
        *(
            _build_realizer(
                run,
                machine,
                admission=_LedgerAdmission(machine, host_capacity=capacity),
                gate=gate,
                faults=faults,
            ).execute(_request(run, workflow_owned=True), _zen_plan(run))
            for run in runs
        ),
        return_exceptions=True,
    )
    return _Wave(
        machine=machine,
        results=list(results),
        gate=gate,
        requested_level=concurrency,
        effective_limit=effective_limit,
    )


async def _run_n(
    concurrency: int, *, host_capacity: int | None = None
) -> tuple[_Machine, list[Any]]:
    wave = await _run_wave(concurrency, host_capacity=host_capacity)
    return wave.machine, wave.results


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


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#3885 — observed overlap, injected authority-handoff
# failures, bounded repeated waves, and honest cleanup reporting.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("concurrency", CONCURRENCY_LEVELS)
@pytest.mark.asyncio
async def test_observed_overlap_proves_simultaneous_useful_execution(
    concurrency: int,
) -> None:
    """The peak is swept from observed windows, not asserted by the test."""

    wave = await _run_wave(concurrency)

    assert wave.gate.satisfied, "executions never held the barrier together"
    overlap = wave.overlap()
    assert overlap.observed_peak == concurrency
    assert overlap.durable_waiters == 0
    # Each workflow delivered its first message exactly once.
    assert sorted(wave.machine.first_messages) == sorted(
        f"session-{index}" for index in range(concurrency)
    )


@pytest.mark.asyncio
async def test_sequential_execution_cannot_be_filed_as_overlap_evidence() -> None:
    """An expected invariant violation must actually fail the suite.

    This is the broken-allocator case: N runs that completed one after another
    look identical to N successes at the summary level, so the evidence
    contract has to reject them explicitly.
    """

    sequential = tuple(
        ExecutionOverlapSample(
            execution_ref=f"run-{index}",
            started_at=float(index),
            ended_at=float(index) + 0.5,
        )
        for index in range(4)
    )
    with pytest.raises(ValueError, match="observed peak concurrency"):
        ObservedOverlapEvidence(
            requested_level=4,
            effective_limit=4,
            barrier_synchronized=True,
            samples=sequential,
        )


@pytest.mark.asyncio
async def test_a_lower_effective_limit_observes_the_limit_and_its_waiters() -> None:
    """Under a lower limit the peak is the limit, and the rest wait."""

    wave = await _run_wave(4, host_capacity=2)

    overlap = wave.overlap()
    assert overlap.observed_peak == 2
    assert overlap.durable_waiters == 2
    refusals = wave.failures
    assert len(refusals) == 2
    for failure in refusals:
        assert isinstance(failure, HarnessPlatformError)
        assert failure.code == "OMNIGENT_HOST_CAPACITY_UNAVAILABLE"


#: Every authority handoff the concurrent journey crosses, in order. A failure
#: at any one of them must stay confined to its own run.
AUTHORITY_HANDOFFS = [
    "turn_claim",
    "provider_lease_confirmation",
    "credential_preparation",
    "workspace_preparation",
    "host_registration",
    "session_creation",
    "first_message",
    "harvest",
]


@pytest.mark.parametrize("handoff", AUTHORITY_HANDOFFS)
@pytest.mark.asyncio
async def test_a_failed_handoff_is_confined_to_its_own_run(handoff: str) -> None:
    """Invariant: one run's failed handoff never touches another run.

    At concurrency 4 a shared identity, a shared cleanup claim, or a released
    neighbour lease shows up here as a missing completion or a leaked host.
    """

    wave = await _run_wave(4, faults=_Faults(points={"0": handoff}))

    assert len(wave.failures) == 1
    assert sorted(item.summary for item in wave.completed) == [
        "done-1",
        "done-2",
        "done-3",
    ]
    # The three healthy runs still overlapped; the failure did not serialize
    # the machine.
    assert wave.gate.satisfied
    # Nothing leaked, and the failed run never released a neighbour's capacity.
    assert wave.machine.allocated_hosts == set()
    assert wave.machine.provider_releases == []
    assert wave.machine.cleanup_scan().zero_leak
    # No run consumed another run's first message.
    assert len(wave.machine.first_messages) == len(set(wave.machine.first_messages))
    # A run that never got past its first message never posted one.
    delivered_zero = "session-0" in wave.machine.first_messages
    assert delivered_zero == (handoff == "harvest")


@pytest.mark.asyncio
async def test_a_lost_registration_acknowledgment_reclaims_the_same_host() -> None:
    """Ambiguous registration does not authorize a second launch.

    The container was created and recorded; only the acknowledgment was lost.
    Cleanup must reclaim that exact host, and no run may be launched twice.
    """

    wave = await _run_wave(
        4, faults=_Faults(points={"0": "host_registration"}, lost_ack=True)
    )

    assert len(wave.failures) == 1
    launched_for_run_zero = [
        record for record in wave.machine.launched if record["hostId"] == "host-0"
    ]
    assert len(launched_for_run_zero) == 1, "an ambiguous ack authorized a relaunch"
    assert wave.machine.allocated_hosts == set()
    assert wave.machine.cleanup_scan().zero_leak
    assert wave.machine.provider_releases == []


#: Teardown steps the host reclaim depends on. ``drain`` runs before the host
#: is removed, so a failure at either point strands the container for the
#: janitor rather than reclaiming it inline.
BLOCKING_TEARDOWN_STEPS = ["drain", "host_teardown"]


@pytest.mark.parametrize("step", BLOCKING_TEARDOWN_STEPS)
@pytest.mark.asyncio
async def test_an_unresolved_teardown_is_never_a_zero_leak_pass(step: str) -> None:
    """A container the teardown could not remove stays visibly unresolved.

    This is the false-clean-scan invariant: the run reached its terminal
    result, so a scan that only counted completions would report zero leaks
    while one container is still consuming the machine. The scan reports the
    stranded host instead, which is what makes the leak actionable.
    """

    wave = await _run_wave(4, faults=_Faults(points={"0": step}))

    assert wave.gate.satisfied
    assert wave.machine.allocated_hosts == {"0"}
    scan = wave.machine.cleanup_scan()
    assert not scan.zero_leak
    assert "host-0" in {entry.resource_ref for entry in scan.unresolved}
    # One run's stuck teardown never releases a neighbour's capacity, and the
    # three healthy runs still reclaimed their own hosts.
    assert wave.machine.provider_releases == []
    for index in (1, 2, 3):
        assert f"host-cleanup:{index}" in wave.machine.cleanups


@pytest.mark.asyncio
async def test_a_credential_teardown_failure_still_reclaims_the_host() -> None:
    """Credential teardown runs after the host reclaim and cannot strand it."""

    wave = await _run_wave(4, faults=_Faults(points={"0": "credential_cleanup"}))

    assert wave.gate.satisfied
    assert wave.machine.allocated_hosts == set()
    assert wave.machine.cleanup_scan().zero_leak
    assert wave.machine.provider_releases == []


@pytest.mark.asyncio
async def test_repeated_waves_show_bounded_growth() -> None:
    """Repeated waves must not grow per-lease work, requests, or residue."""

    level = 4
    observations: list[WaveObservation] = []
    for index in range(3):
        started = time.monotonic()
        wave = await _run_wave(level)
        control_seconds = time.monotonic() - started
        overlap = wave.overlap()
        observations.append(
            WaveObservation(
                wave_index=index,
                observed_peak=overlap.observed_peak,
                wait_seconds=0.0,
                launch_seconds=0.0,
                registration_seconds=0.0,
                # Provider latency is excluded by construction: this substrate
                # has no provider round-trip, so the budget measures control.
                control_seconds=control_seconds,
                cleanup_seconds=0.0,
                lease_mutations=wave.machine.lease_mutations,
                registration_requests=wave.machine.registration_requests,
                transport_pool_peak=wave.machine.peak_hosts,
                residual_resources=len(wave.machine.allocated_hosts),
            )
        )

    report = RepeatedWaveReport(
        level=level,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=tuple(observations),
    )
    assert report.bounded, report.violations


@pytest.mark.asyncio
async def test_a_growing_leak_fails_the_repeated_wave_report() -> None:
    """The bounded-growth check must actually catch a wave-over-wave leak."""

    def _observation(index: int, *, residual: int, mutations: int) -> WaveObservation:
        return WaveObservation(
            wave_index=index,
            observed_peak=4,
            wait_seconds=0.0,
            launch_seconds=0.0,
            registration_seconds=0.0,
            control_seconds=1.0,
            cleanup_seconds=0.0,
            lease_mutations=mutations,
            registration_requests=4,
            transport_pool_peak=4,
            residual_resources=residual,
        )

    report = RepeatedWaveReport(
        level=4,
        thresholds=DEFAULT_REPEATED_WAVE_THRESHOLDS,
        waves=(
            _observation(0, residual=0, mutations=8),
            _observation(1, residual=1, mutations=12),
        ),
    )
    assert not report.bounded
    violations = " ".join(report.violations)
    assert "residual resources" in violations
    assert "database mutations grew" in violations


@pytest.mark.asyncio
async def test_capacity_is_not_reused_before_teardown_is_proven() -> None:
    """A stranded host keeps consuming the slot it was never released from.

    This is the premature-release invariant. The first wave's run 0 reached a
    terminal result but its container was never removed, so the machine is
    still carrying it. A second wave must be admitted against the real
    occupancy, not against the count of finished workflows — otherwise the
    machine is oversubscribed by exactly the number of unresolved teardowns.
    """

    machine = _Machine()
    first = await _run_wave(
        4, machine=machine, faults=_Faults(points={"0": "host_teardown"})
    )
    assert first.machine.allocated_hosts == {"0"}
    assert not machine.cleanup_scan().zero_leak

    # Four more submissions against a machine of four, one slot of which is
    # still held by the stranded host: exactly three may run.
    second = await _run_wave(
        4, host_capacity=4, machine=machine, offset=10, expected_overlap=3
    )

    assert len(second.completed) == 3
    refusals = second.failures
    assert len(refusals) == 1
    assert isinstance(refusals[0], HarnessPlatformError)
    assert refusals[0].code == "OMNIGENT_HOST_CAPACITY_UNAVAILABLE"
    # The machine never exceeded its four slots, and the stranded host is still
    # counted rather than quietly reclaimed by the second wave.
    assert machine.peak_hosts == 4
    assert machine.allocated_hosts == {"0"}
    assert second.gate.satisfied
    assert second.overlap().observed_peak == 3
