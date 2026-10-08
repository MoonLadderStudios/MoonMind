"""Exercise admitted Codex policy authority across planning and dispatch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
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
    artifacts = _ArtifactService()
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
    )
    return admitted.envelope, launch, coordinator


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
    predecessor, predecessor_launch, _ = await _admit_codex_plan(
        monkeypatch, snapshot=snapshot, policy=policy, workflow_id="mm:before"
    )
    # The update installs the rebuilt image; the policy is not yet re-versioned.
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", installed_image)
    successor, successor_launch, coordinator = await _admit_codex_plan(
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
    _plan, _launch, coordinator = await _admit_codex_plan(
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
