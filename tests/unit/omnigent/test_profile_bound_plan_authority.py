"""Exercise admitted Codex policy authority across planning and dispatch."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.settings import generic_codex_qualified
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _configure_ready_host_image_pair,
)


async def _admit_codex_plan(
    monkeypatch,
    policy,
    *,
    shared_host="ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    server=None,
):
    """Read bytes admitted by the pre-retirement compiler, without new admission."""
    from pathlib import Path

    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanEnvelope,
    )

    label = "codex-installed-images" if server is not None else policy.replace("@", "-")
    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/omnigent/retained_profile_bound_plans"
            / f"{label}.json"
        ).read_text()
    )
    artifacts = _ArtifactService()
    artifacts.payloads = {
        key: value.encode() for key, value in fixture["artifacts"].items()
    }
    execution_request = AgentExecutionRequest.model_validate(fixture["request"])
    binding = execution_request.omnigent_execution_plan
    plan = OmnigentExecutionPlanEnvelope.model_validate_json(
        artifacts.payloads[binding.plan_artifact_ref]
    )
    snapshot = json.loads(
        artifacts.payloads[plan.payload.policySnapshotRef.removeprefix("artifact:")]
    )
    launch = json.loads(
        artifacts.payloads[
            plan.payload.effectiveLaunchSnapshotRef.removeprefix("artifact:")
        ]
    )
    _configure_ready_host_image_pair(monkeypatch)
    monkeypatch.delenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", raising=False)
    monkeypatch.setenv("OMNIGENT_SHARED_HOST_IMAGE_REF", shared_host)
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", server or launch["serverImageRef"])
    assert generic_codex_qualified() is True
    assert plan.payload.executionRealizerRef == "codex-profile-bound@1"
    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=object(),
        host_repository=object(),
        host_runtime=object(),
        run_store=object(),
        execution_runner=AsyncMock(),
        artifact_gateway=artifacts,
        execution_plan=plan,
    )
    return coordinator, execution_request, plan, snapshot, launch


@pytest_asyncio.fixture
async def admitted_codex_plan(monkeypatch, request):
    return await _admit_codex_plan(
        monkeypatch, getattr(request, "param", "codex-on-demand@16")
    )


@pytest.mark.asyncio
async def test_dispatch_launches_the_images_admission_bound_from_an_older_policy(
    monkeypatch,
):
    """The policy version predates the installed images; dispatch needs no cut.

    Admission bound the installed host and server to the plan. Recompiling the
    unchanged policy snapshot names the policy's images, and binding them to
    the plan's host and the installed server reproduces the admitted launch.
    """
    from moonmind.omnigent.profile_bound_execution import (
        _compile_persisted_effective_launch,
        compile_follow_up_retrieval_policy,
    )

    installed_host = "ghcr.io/example/omnigent-host@sha256:" + "8" * 64
    installed_server = "ghcr.io/example/omnigent-server@sha256:" + "9" * 64
    coordinator, request, plan, snapshot, launch = await _admit_codex_plan(
        monkeypatch,
        "codex-on-demand@16",
        shared_host=installed_host,
        server=installed_server,
    )
    assert plan.payload.hostImageRef == installed_host
    coordinator._require_recorded_plan_request(request)
    recompiled = _compile_persisted_effective_launch(
        snapshot,
        provider_profile_id="codex",
        follow_up_retrieval=compile_follow_up_retrieval_policy(),
    )
    assert recompiled["hostImageRef"] != installed_host

    bound = coordinator._require_recorded_launch(
        policy_snapshot=snapshot, effective_launch=recompiled
    )

    assert bound == launch
    assert bound["hostImageRef"] == installed_host
    assert bound["serverImageRef"] == installed_server

    # A server installed after admission is not the plan's server: the
    # admitted attempt keeps its binding and reports drift instead.
    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF", "ghcr.io/example/omnigent-server@sha256:" + "7" * 64
    )
    with pytest.raises(HarnessPlatformError, match="launch authority has drifted"):
        coordinator._require_recorded_launch(
            policy_snapshot=snapshot, effective_launch=recompiled
        )


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
