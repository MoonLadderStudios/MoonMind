"""Exercise admitted Codex policy authority across planning and dispatch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.harness_platform.catalog_service import (
    HarnessCatalogSyncResult,
    InMemoryHarnessCatalogRepository,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentPlannedHostResolver,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _PlanStore,
    _configure_ready_host_image_pair,
    _policy_snapshot,
    _protected_support_evidence,
    _snapshot,
)


@pytest_asyncio.fixture
async def admitted_codex_plan(monkeypatch, request):
    policy = getattr(request, "param", "codex-on-demand@16")
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    snapshot = _policy_snapshot(harness="codex-native", policy=policy)
    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service, "_resolve_runtime_policy_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    artifacts = _ArtifactService()
    parameters = {
        "targetRuntime": "omnigent",
        "publishMode": "auto",
        "omnigent": {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": policy,
        },
    }
    admitted = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:pr-resolver-policy-regression",
        agent_profile_snapshot=_snapshot(
            harness="codex-native", policy=policy, provider_id="codex"
        ),
        provider_profile=SimpleNamespace(
            profile_id="codex", runtime_id="codex_cli", provider_id="openai"
        ),
        initial_parameters=parameters,
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=object(),
        host_repository=object(),
        host_runtime=object(),
        run_store=object(),
        execution_runner=AsyncMock(),
        artifact_gateway=artifacts,
        execution_plan=admitted.envelope,
    )
    execution_request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId="workflow-1",
        idempotencyKey="attempt-1",
        parameters=parameters,
        omnigentExecutionPlan=admitted.binding,
    )
    launch = json.loads(
        artifacts.payloads[
            admitted.envelope.payload.effectiveLaunchSnapshotRef.removeprefix(
                "artifact:"
            )
        ]
    )
    return coordinator, execution_request, admitted.envelope, snapshot, launch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "admitted_codex_plan",
    ["codex-on-demand@1", "codex-on-demand@16", "operator-codex@2"],
    indirect=True,
)
async def test_dispatch_accepts_admitted_policy_independent_of_target_default(
    admitted_codex_plan,
):
    coordinator, request, plan, snapshot, launch = admitted_codex_plan
    assert coordinator._require_recorded_plan_request(request) is plan
    coordinator._require_recorded_launch(
        policy_snapshot=snapshot, effective_launch=launch
    )
    assert (
        launch["launchPolicyRef"] == request.parameters["omnigent"]["launchPolicyRef"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("launchPolicyRef", "codex-on-demand@1", "launch policy conflicts"),
        ("executionTargetRef", "omnigent-claude@1", "execution target conflicts"),
        ("executionTargetRef", "unknown-target@1", "execution target conflicts"),
    ],
)
async def test_dispatch_rejects_conflicting_selection_before_effects(
    admitted_codex_plan, field, value, message
):
    coordinator, request, _plan, _snapshot, _launch = admitted_codex_plan
    request.parameters["omnigent"][field] = value
    # execute validates the admitted request before touching the inert ports.
    with pytest.raises(HarnessPlatformError, match=message) as error:
        await coordinator.execute(request)
    assert error.value.code == "OMNIGENT_EXECUTION_PLAN_CONFLICT"


class _PolicySelected(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admitted_codex_plan", "omitted"),
    [
        ("codex-on-demand@16", ("launchPolicyRef",)),
        ("operator-codex@2", ("launchPolicyRef",)),
        ("codex-on-demand@16", ("launchPolicyRef", "executionTargetRef")),
    ],
    indirect=["admitted_codex_plan"],
)
async def test_dispatch_selects_admitted_policy_when_request_omits_it(
    admitted_codex_plan, omitted
):
    coordinator, request, plan, _snapshot, _launch = admitted_codex_plan
    for field in omitted:
        request.parameters["omnigent"].pop(field)
    selected: list[str] = []

    async def resolve_runtime_snapshot(policy_ref: str):
        selected.append(policy_ref)
        raise _PolicySelected

    coordinator._run_store = AsyncMock()
    coordinator._hosts = SimpleNamespace(
        get_binding_for_profile=AsyncMock(return_value=None)
    )
    coordinator._profile_authority = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(runtime_id="codex_cli", launch_ready=True)
        )
    )
    coordinator._policy_authority = SimpleNamespace(
        resolve_runtime_snapshot=resolve_runtime_snapshot
    )

    # Stop at the first policy lookup: selection precedes every lease and host
    # effect, and the admitted policy is the one the plan's snapshots record.
    with pytest.raises(_PolicySelected):
        await coordinator.execute(request)
    assert selected == [plan.payload.launchPolicyRef]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_snapshot", ["policy", "launch"])
async def test_dispatch_still_rejects_changed_admitted_launch(
    admitted_codex_plan, changed_snapshot
):
    coordinator, request, _plan, snapshot, launch = admitted_codex_plan
    coordinator._require_recorded_plan_request(request)
    if changed_snapshot == "policy":
        snapshot["boundaries"]["execution"]["profileRef"] = "omnigent-claude@1"
    else:
        launch["launchPolicyRef"] = "codex-on-demand@1"
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        coordinator._require_recorded_launch(
            policy_snapshot=snapshot, effective_launch=launch
        )


class _ReadableArtifacts(_ArtifactService):
    """Plan artifact store that also serves the plan's durable launch bytes."""

    async def read_bytes(self, ref: str) -> bytes:
        return self.payloads[ref.removeprefix("artifact:")]


async def _admit_codex_plan(monkeypatch, *, snapshot, policy, workflow_id):
    monkeypatch.setattr(service, "DbExecutionPlanStore", _PlanStore)
    monkeypatch.setattr(
        service, "_resolve_runtime_policy_snapshot", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    # Admission persists the exact catalog snapshot the plan pins; launch-time
    # host resolution loads it from the catalog repository.
    catalogs = InMemoryHarnessCatalogRepository()
    created: list = []
    create_snapshot = service.create_catalog_snapshot

    def capture_catalog_snapshot(**kwargs):
        created.append(create_snapshot(**kwargs))
        return created[-1]

    monkeypatch.setattr(service, "create_catalog_snapshot", capture_catalog_snapshot)
    artifacts = _ReadableArtifacts()
    parameters = {
        "targetRuntime": "omnigent",
        "publishMode": "auto",
        "model": "example/model",
        "effort": "high",
        "omnigent": {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": policy,
        },
    }
    admitted = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id=workflow_id,
        agent_profile_snapshot=_snapshot(
            harness="codex-native", policy=policy, provider_id="codex"
        ),
        provider_profile=SimpleNamespace(
            profile_id="codex", runtime_id="codex_cli", provider_id="openai"
        ),
        initial_parameters=parameters,
        authored_request_ref="art_request_1",
        authored_request_digest="sha256:" + "1" * 64,
        task_input_snapshot_ref="art_request_1",
        task_input_snapshot_digest="sha256:" + "1" * 64,
    )
    for catalog in created:
        await catalogs.persist(
            HarnessCatalogSyncResult(snapshot=catalog, trust_records=(), diagnostics={})
        )
    launch = json.loads(
        artifacts.payloads[
            admitted.envelope.payload.effectiveLaunchSnapshotRef.removeprefix(
                "artifact:"
            )
        ]
    )
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=object(),
        host_repository=object(),
        host_runtime=object(),
        run_store=object(),
        execution_runner=AsyncMock(),
        artifact_gateway=artifacts,
        execution_plan=admitted.envelope,
        # The production planned-host resolver and Host Class selector, which
        # read the deployment's installed image evidence from the environment
        # and the bootstrap resolved-images store.
        planned_host_resolver=OmnigentPlannedHostResolver(
            catalog_repository=catalogs, artifact_gateway=artifacts
        ),
    )
    return admitted.envelope, launch, coordinator, admitted.binding


def _dispatch_recompiled_launch(snapshot):
    """Recompile launch authority exactly as the Codex coordinator does."""

    from moonmind.omnigent.profile_bound_execution import (
        _compile_persisted_effective_launch,
        compile_follow_up_retrieval_policy,
    )

    return _compile_persisted_effective_launch(
        snapshot,
        provider_profile_id="codex",
        follow_up_retrieval=compile_follow_up_retrieval_policy(),
    )


@pytest.mark.asyncio
async def test_codex_attempt_after_compatible_image_update_runs_on_installed_host(
    monkeypatch,
):
    """#4627 R4: a fresh Codex attempt follows the newly installed host image.

    A deployment update installs a rebuilt host image from the same repository
    while the policy snapshot (and the predecessor's admitted plan) still pin
    the previous digest. The fresh attempt is admitted on the installed image,
    and the legacy coordinator accepts that plan because recompiling the
    unchanged policy plus the same deterministic reconciliation reproduces the
    recorded launch bytes exactly. The predecessor keeps its actual image.
    """

    old_image = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    installed_image = "ghcr.io/example/omnigent-host@sha256:" + "e" * 64
    policy = "codex-on-demand@16"
    _configure_ready_host_image_pair(monkeypatch)
    snapshot = _policy_snapshot(
        harness="codex-native", policy=policy, host_image_ref=old_image
    )

    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", old_image)
    predecessor, predecessor_launch, _, _ = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:before"
    )
    # The update installs the rebuilt image; the policy is not yet re-versioned.
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", installed_image)
    successor, successor_launch, coordinator, _ = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:after"
    )

    assert predecessor.payload.hostImageRef == old_image
    assert predecessor_launch["hostImageRef"] == old_image
    assert successor.payload.hostImageRef == installed_image
    assert successor_launch["hostImageRef"] == installed_image
    assert successor_launch["boundaries"]["host"]["hostImageRef"] == installed_image
    for field in ("harnessId", "launchPolicyRef", "executionRealizerRef"):
        assert getattr(successor.payload, field) == getattr(predecessor.payload, field)
    assert successor.payload.harnessId == "codex-native"
    assert successor.payload.modelConfig == predecessor.payload.modelConfig
    for field in (
        "harness",
        "executionProfileRef",
        "launchPolicyRef",
        "providerProfileId",
        "repositoryMutation",
        "agentName",
        "hostMode",
    ):
        assert successor_launch.get(field) == predecessor_launch.get(field)

    dispatched = coordinator._require_recorded_launch(
        policy_snapshot=snapshot,
        effective_launch=_dispatch_recompiled_launch(snapshot),
    )
    assert dispatched == successor_launch
    assert dispatched["hostImageRef"] == installed_image


@pytest.mark.asyncio
async def test_codex_drift_reconciliation_rejects_any_other_launch_change(
    monkeypatch,
):
    old_image = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    installed_image = "ghcr.io/example/omnigent-host@sha256:" + "e" * 64
    policy = "codex-on-demand@16"
    _configure_ready_host_image_pair(monkeypatch)
    snapshot = _policy_snapshot(
        harness="codex-native", policy=policy, host_image_ref=old_image
    )
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", installed_image)
    _plan, _launch, coordinator, _ = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:after"
    )

    tampered = _dispatch_recompiled_launch(snapshot)
    tampered["repositoryMutation"] = not tampered["repositoryMutation"]
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        coordinator._require_recorded_launch(
            policy_snapshot=snapshot, effective_launch=tampered
        )
    foreign = _policy_snapshot(
        harness="codex-native",
        policy=policy,
        host_image_ref="ghcr.io/example/other-host@sha256:" + "f" * 64,
    )
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        coordinator._require_recorded_launch(
            policy_snapshot=foreign,
            effective_launch=_dispatch_recompiled_launch(foreign),
        )


@pytest.mark.asyncio
async def test_codex_plan_still_rejects_foreign_repository_host(monkeypatch):
    policy = "codex-on-demand@16"
    _configure_ready_host_image_pair(monkeypatch)
    snapshot = _policy_snapshot(
        harness="codex-native",
        policy=policy,
        host_image_ref="ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/other-host@sha256:" + "e" * 64,
    )
    with pytest.raises(ValueError, match="effective launch host image conflicts"):
        await _admit_codex_plan(
            monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:foreign"
        )


class _LaunchDispatched(Exception):
    pass


def _successor_request(binding, *, reason, explicit_host_class):
    ordinal = 1 if reason == "initial_execution" else 2
    omnigent = {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@16",
    }
    if explicit_host_class:
        omnigent["hostClassRef"] = "omnigent-codex@1"
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "executionProfileRef": "codex",
            "correlationId": "workflow-1",
            "idempotencyKey": f"attempt-{ordinal}",
            "parameters": {
                "targetRuntime": "omnigent",
                "publishMode": "auto",
                "omnigent": omnigent,
            },
            "omnigentExecutionPlan": binding.model_dump(by_alias=True, mode="json"),
            "stepExecution": {
                "workflowId": "mm:interrupted",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": ordinal,
                "stepExecutionId": f"mm:interrupted:run-1:implement:execution:{ordinal}",
                "runtimeContextPolicy": "fresh_agent_run",
                "reason": reason,
            },
        }
    )


async def _dispatched_launch(coordinator, request, snapshot):
    """Run the real coordinator to its recorded dispatch authority.

    The coordinator records the launch it will hand to the host runtime as the
    ``effective_launch_compiled`` lifecycle event, before any lease, host or
    workspace effect. Stop there and return that launch.
    """

    dispatched: list[dict] = []

    async def record_lifecycle_event(_key, *, event_type, metadata=None, **_kwargs):
        if event_type == "effective_launch_compiled":
            dispatched.append(dict((metadata or {})["effectiveLaunch"]))
            raise _LaunchDispatched

    coordinator._run_store = AsyncMock()
    coordinator._run_store.record_lifecycle_event = record_lifecycle_event
    coordinator._hosts = SimpleNamespace(
        get_binding_for_profile=AsyncMock(return_value=None)
    )
    coordinator._profile_authority = SimpleNamespace(
        resolve=AsyncMock(
            return_value=SimpleNamespace(runtime_id="codex_cli", launch_ready=True)
        )
    )

    async def resolve_runtime_snapshot(_policy_ref):
        return json.loads(json.dumps(snapshot))

    coordinator._policy_authority = SimpleNamespace(
        resolve_runtime_snapshot=resolve_runtime_snapshot
    )
    with pytest.raises(_LaunchDispatched):
        await coordinator.execute(request)
    assert len(dispatched) == 1
    return dispatched[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "explicit_host_class", "expected"),
    [
        ("initial_execution", False, "old"),
        ("runtime_recovered", False, "installed"),
        ("runtime_recovered", True, "old"),
    ],
)
async def test_codex_interrupted_successor_dispatches_installed_image(
    monkeypatch, reason, explicit_host_class, expected
):
    """#4627 R4: the default Codex route restarts on the installed host image.

    A workflow's Codex plan was admitted on the pre-update image, which a
    Compose pull leaves cached. After the update, the ``runtime_recovered``
    successor of its interrupted step reuses that admitted plan, and the real
    coordinator dispatches it on the deployment's installed same-repository
    image. Every other launch byte stays the admitted one. The first attempt and
    an explicitly requested Host Class keep the plan's recorded image.
    """

    old_image = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    installed_image = "ghcr.io/example/omnigent-host@sha256:" + "e" * 64
    policy = "codex-on-demand@16"
    _configure_ready_host_image_pair(monkeypatch)
    snapshot = _policy_snapshot(
        harness="codex-native", policy=policy, host_image_ref=old_image
    )
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", old_image)
    plan, admitted_launch, coordinator, binding = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:interrupted"
    )
    assert plan.payload.hostImageRef == old_image
    # The update installs the rebuilt image from the same repository.
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", installed_image)

    request = _successor_request(
        binding, reason=reason, explicit_host_class=explicit_host_class
    )
    launch = await _dispatched_launch(coordinator, request, snapshot)

    image = installed_image if expected == "installed" else old_image
    assert launch["hostImageRef"] == image
    assert launch["boundaries"]["host"]["hostImageRef"] == image
    unchanged = {
        key: value
        for key, value in launch.items()
        if key not in {"hostImageRef", "boundaries", "snapshotRef"}
    }
    assert unchanged == {
        key: value
        for key, value in admitted_launch.items()
        if key not in {"hostImageRef", "boundaries", "snapshotRef"}
    }
    assert {
        key: value for key, value in launch["boundaries"].items() if key != "host"
    } == {
        key: value
        for key, value in admitted_launch["boundaries"].items()
        if key != "host"
    }
    # The admitted plan and its launch record are never rewritten.
    assert plan.payload.hostImageRef == old_image
    assert admitted_launch["hostImageRef"] == old_image


@pytest.mark.asyncio
async def test_codex_interrupted_successor_never_adopts_foreign_or_tampered_launch(
    monkeypatch,
):
    old_image = "ghcr.io/example/omnigent-host@sha256:" + "f" * 64
    policy = "codex-on-demand@16"
    _configure_ready_host_image_pair(monkeypatch)
    snapshot = _policy_snapshot(
        harness="codex-native", policy=policy, host_image_ref=old_image
    )
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", old_image)
    _plan, _launch, coordinator, binding = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:interrupted"
    )
    request = _successor_request(
        binding, reason="runtime_recovered", explicit_host_class=False
    )

    # A different image family installed under the shared ref is not the same
    # runtime: the successor keeps the admitted image.
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/other-host@sha256:" + "e" * 64,
    )
    launch = await _dispatched_launch(coordinator, request, snapshot)
    assert launch["hostImageRef"] == old_image

    # Installed-runtime selection never excuses other launch drift.
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "e" * 64,
    )
    tampered = json.loads(json.dumps(snapshot))
    tampered["policyDigest"] = "sha256:" + "9" * 64
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        await _dispatched_launch(coordinator, request, tampered)
