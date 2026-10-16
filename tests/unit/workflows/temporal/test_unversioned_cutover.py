"""Outcomes of the one-time unversioned routing cutover against a fake service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from temporalio.api.deployment.v1 import (
    RoutingConfig,
    WorkerDeploymentInfo,
    WorkerDeploymentOptions,
    WorkerDeploymentVersion,
    WorkerDeploymentVersionInfo,
)
from temporalio.api.enums.v1 import TaskQueueType, VersioningBehavior
from temporalio.api.taskqueue.v1 import PollerInfo
from temporalio.api.workflow.v1 import (
    DeploymentVersionTransition,
    VersioningOverride,
    WorkflowExecutionInfo,
    WorkflowExecutionVersioningInfo,
)
from temporalio.api.workflowservice.v1 import (
    DescribeTaskQueueResponse,
    DescribeWorkerDeploymentResponse,
    DescribeWorkerDeploymentVersionResponse,
    DescribeWorkflowExecutionResponse,
)
from temporalio.service import RPCError, RPCStatusCode

from moonmind.workflows.temporal import unversioned_cutover as cutover

DEPLOYMENT = "moonmind-workflow-fleet"
PINNED = VersioningBehavior.VERSIONING_BEHAVIOR_PINNED
AUTO = VersioningBehavior.VERSIONING_BEHAVIOR_AUTO_UPGRADE


def _version(build, deployment=DEPLOYMENT):
    return WorkerDeploymentVersion(deployment_name=deployment, build_id=build)


def _info(
    behavior=None, build=None, override=None, transition=None, deployment=DEPLOYMENT
):
    info = WorkflowExecutionVersioningInfo()
    if behavior is not None:
        info.behavior = behavior
    if build is not None:
        info.deployment_version.CopyFrom(_version(build, deployment))
    if override is not None:
        info.versioning_override.CopyFrom(override)
    if transition is not None:
        info.version_transition.CopyFrom(
            DeploymentVersionTransition(deployment_version=_version(transition))
        )
    return info


class FakeService:
    def __init__(
        self,
        *,
        current="",
        ramping="",
        queues=(),
        ordinary=(),
        conflicts=0,
        version_queues=None,
    ):
        self.current, self.ramping = current, ramping
        self.queues = queues
        # Per-build queues; otherwise every version serves ``queues``.
        self.version_queues = version_queues or {}
        self.ordinary = set(ordinary)
        self.conflicts = conflicts
        self.mutations = []
        self.revision = 0

    def _snapshot(self):
        routing = RoutingConfig()
        if self.current:
            routing.current_deployment_version.CopyFrom(_version(self.current))
        if self.ramping:
            routing.ramping_deployment_version.CopyFrom(_version(self.ramping))
            routing.ramping_version_percentage = 10
        return DescribeWorkerDeploymentResponse(
            conflict_token=str(self.revision).encode(),
            worker_deployment_info=WorkerDeploymentInfo(routing_config=routing),
        )

    async def describe_worker_deployment(self, request):
        if self.current is None:
            raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
        return self._snapshot()

    async def describe_worker_deployment_version(self, request):
        return DescribeWorkerDeploymentVersionResponse(
            worker_deployment_version_info=WorkerDeploymentVersionInfo(
                task_queue_infos=[
                    WorkerDeploymentVersionInfo.VersionTaskQueueInfo(
                        name=name, type=kind
                    )
                    for name, kind in self.version_queues.get(
                        request.version.rpartition(".")[2], self.queues
                    )
                ]
            )
        )

    async def describe_task_queue(self, request):
        recent = datetime.now(timezone.utc) - timedelta(seconds=5)
        pollers = [
            PollerInfo(
                identity="old@host",
                deployment_options=WorkerDeploymentOptions(
                    deployment_name=DEPLOYMENT, build_id=self.current or "old"
                ),
            )
        ]
        if request.task_queue.name in self.ordinary:
            pollers.append(PollerInfo(identity="ordinary@host"))
        for poller in pollers:
            poller.last_access_time.FromDatetime(recent)
        return DescribeTaskQueueResponse(pollers=pollers)

    def _check_token(self, request):
        if self.conflicts:
            self.conflicts -= 1
            self.revision += 1  # A concurrent writer moved routing.
            raise RPCError("conflict", RPCStatusCode.ABORTED, b"")
        assert request.conflict_token == str(self.revision).encode()
        self.revision += 1

    async def set_worker_deployment_ramping_version(self, request):
        self._check_token(request)
        assert not request.version and not request.build_id and request.percentage == 0
        self.mutations.append("clear-ramp")
        self.ramping = ""

    async def set_worker_deployment_current_version(self, request):
        self._check_token(request)
        assert not request.version and not request.build_id
        assert not request.ignore_missing_task_queues and not request.allow_no_pollers
        assert not self.ramping, "a ramp must be cleared before current changes"
        self.mutations.append("unversioned-current")
        self.current = ""


class FakeClient:
    namespace = "default"

    def __init__(self, service, executions=(), *, list_error=None):
        self.workflow_service = service
        self.executions = {item["id"]: item for item in executions}
        self.list_error = list_error
        self.terminated = []

    def list_workflows(self, query):
        assert query == 'ExecutionStatus="Running"'
        client = self

        class Listing:
            def __aiter__(self):
                return self._items()

            async def _items(self):
                if client.list_error is not None:
                    raise client.list_error
                for item in client.executions.values():
                    yield SimpleNamespace(
                        id=item["id"],
                        run_id="run-" + item["id"],
                        workflow_type=item["type"],
                    )

        return Listing()

    def get_workflow_handle(self, workflow_id, *, run_id=None):
        item = self.executions[workflow_id]
        client = self

        class Handle:
            async def describe(self):
                if "error" in item:
                    raise RPCError("unavailable", item["error"], b"")
                return SimpleNamespace(
                    raw_description=DescribeWorkflowExecutionResponse(
                        workflow_execution_info=WorkflowExecutionInfo(
                            versioning_info=item["info"]
                        )
                    )
                )

            async def terminate(self, *, reason):
                client.terminated.append((workflow_id, run_id, reason))

        return Handle()


QUEUES = (
    ("mm.workflow", TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
    ("mm.activity.artifacts", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
)


def test_effective_routing_prefers_overrides_and_reports_transitions():
    assert cutover.classify_execution(
        "MoonMind.UserWorkflow", "user", _info(AUTO, "old", transition="next")
    ) == {
        "classification": "auto_upgrade",
        "deployment": DEPLOYMENT,
        "version": f"{DEPLOYMENT}.old",
        "transitionTo": f"{DEPLOYMENT}.next",
    }
    # Code defaulted to AUTO_UPGRADE, but an explicit pin decides.
    pinned = VersioningOverride()
    pinned.pinned.version.CopyFrom(_version("old"))
    assert (
        cutover.classify_execution(
            "MoonMind.UserWorkflow", "user", _info(AUTO, "old", override=pinned)
        )["classification"]
        == "pinned"
    )
    assert (
        cutover.classify_execution(
            "MoonMind.UserWorkflow",
            "user",
            _info(PINNED, "old", override=VersioningOverride(auto_upgrade=True)),
        )["classification"]
        == "auto_upgrade"
    )
    assert (
        cutover.classify_execution("MoonMind.UserWorkflow", "user", _info())[
            "classification"
        ]
        == "unversioned"
    )


@pytest.mark.parametrize("behavior", [PINNED, AUTO])
def test_only_typed_and_named_release_canaries_are_positively_identified(behavior):
    for workflow_id in ("mm-release-canary-1", "mm-steward-canary-x.y-ordinary-abc"):
        assert (
            cutover.classify_execution(
                "MoonMind.ReleaseCanary", workflow_id, _info(behavior, "old")
            )["classification"]
            == "release_canary"
        )
    # The type alone, or the ID alone, is not identification.
    assert (
        cutover.classify_execution(
            "MoonMind.ReleaseCanary", "user-chosen-id", _info(behavior, "old")
        )["classification"]
        != "release_canary"
    )
    assert (
        cutover.classify_execution(
            "MoonMind.UserWorkflow", "mm-release-canary-1", _info(behavior, "old")
        )["classification"]
        != "release_canary"
    )


@pytest.mark.asyncio
async def test_fresh_installation_is_a_no_op_without_mutation():
    service = FakeService(current=None)
    receipt = await cutover.run_cutover(FakeClient(service), deployment=DEPLOYMENT)
    assert receipt["status"] == "completed" and receipt["outcome"] == "fresh"
    assert service.mutations == []


@pytest.mark.asyncio
async def test_already_unversioned_with_auto_upgrade_work_completes_without_mutation():
    service = FakeService(current="", queues=QUEUES)
    client = FakeClient(
        service,
        [{"id": "user", "type": "MoonMind.UserWorkflow", "info": _info(AUTO, "old")}],
    )
    receipt = await cutover.run_cutover(client, deployment=DEPLOYMENT)
    assert receipt["status"] == "completed"
    assert receipt["outcome"] == "already_unversioned"
    assert receipt["autoUpgradeExecutions"] == 1
    assert service.mutations == []


@pytest.mark.asyncio
async def test_transition_clears_ramp_then_current_after_a_concurrent_writer():
    service = FakeService(
        current="old",
        ramping="newer",
        queues=QUEUES,
        ordinary=[name for name, _ in QUEUES],
        conflicts=1,
    )
    receipt = await cutover.run_cutover(FakeClient(service), deployment=DEPLOYMENT)
    assert receipt["status"] == "completed"
    assert receipt["outcome"] == "transitioned"
    assert receipt["previousRouting"] == {
        "currentVersion": f"{DEPLOYMENT}.old",
        "rampingVersion": f"{DEPLOYMENT}.newer",
    }
    assert service.mutations == ["clear-ramp", "unversioned-current"]
    # A lost acknowledgment reruns: actual routing is reread, nothing repeats.
    again = await cutover.run_cutover(FakeClient(service), deployment=DEPLOYMENT)
    assert again["outcome"] == "already_unversioned"
    assert service.mutations == ["clear-ramp", "unversioned-current"]


@pytest.mark.asyncio
async def test_missing_ordinary_poller_keeps_versioned_routing():
    service = FakeService(current="old", queues=QUEUES, ordinary=["mm.workflow"])
    receipt = await cutover.run_cutover(FakeClient(service), deployment=DEPLOYMENT)
    assert receipt["status"] == "pending"
    assert receipt["outcome"] == "missing_queue"
    assert "mm.activity.artifacts (activity)" in receipt["detail"]
    assert service.mutations == []
    assert service.current == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["", "old"])
async def test_missing_poller_on_a_ramping_only_queue_keeps_the_ramp(current):
    ramp_only = ("mm.activity.ramp", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY)
    service = FakeService(
        current=current,
        ramping="newer",
        queues=QUEUES,
        version_queues={"newer": (*QUEUES, ramp_only)},
        ordinary=[name for name, _ in QUEUES],
    )
    receipt = await cutover.run_cutover(FakeClient(service), deployment=DEPLOYMENT)
    assert receipt["status"] == "pending"
    assert receipt["outcome"] == "missing_queue"
    assert "mm.activity.ramp (activity)" in receipt["detail"]
    assert service.mutations == []
    assert service.ramping == "newer"


@pytest.mark.asyncio
async def test_unavailable_observation_stays_unknown_and_changes_nothing():
    service = FakeService(current="old", queues=QUEUES, ordinary=[n for n, _ in QUEUES])
    client = FakeClient(
        service, list_error=RPCError("down", RPCStatusCode.UNAVAILABLE, b"")
    )
    receipt = await cutover.run_cutover(client, deployment=DEPLOYMENT)
    assert receipt["status"] == "pending"
    assert receipt["outcome"] == "observation_unavailable"
    assert "preservedPinned" not in receipt
    assert service.mutations == []


@pytest.mark.asyncio
async def test_canaries_retire_while_pins_and_unknowns_keep_the_cutover_open():
    service = FakeService(current="old", queues=QUEUES, ordinary=[n for n, _ in QUEUES])
    client = FakeClient(
        service,
        [
            {
                "id": "mm-release-canary-1",
                "type": "MoonMind.ReleaseCanary",
                "info": _info(PINNED, "old"),
            },
            {
                "id": "pinned-user",
                "type": "MoonMind.UserWorkflow",
                "info": _info(PINNED, "old"),
            },
            {
                "id": "auto-user",
                "type": "MoonMind.UserWorkflow",
                "info": _info(AUTO, "old"),
            },
            {"id": "plain", "type": "MoonMind.UserWorkflow", "info": _info()},
            {
                "id": "foreign",
                "type": "MoonMind.UserWorkflow",
                "info": _info(PINNED, "x", deployment="another-deployment"),
            },
            {
                "id": "unreadable",
                "type": "MoonMind.UserWorkflow",
                "error": RPCStatusCode.UNAVAILABLE,
            },
        ],
    )
    receipt = await cutover.run_cutover(client, deployment=DEPLOYMENT)
    assert receipt["status"] == "attention"
    assert receipt["outcome"] == "transitioned"
    assert [item["workflowId"] for item in receipt["retiredCanaries"]] == [
        "mm-release-canary-1"
    ]
    assert [item["workflowId"] for item in receipt["preservedPinned"]] == [
        "pinned-user"
    ]
    assert [item["workflowId"] for item in receipt["unknownExecutions"]] == [
        "unreadable"
    ]
    assert receipt["autoUpgradeExecutions"] == 1
    # Only the positively identified canary is terminated; nothing else is
    # cancelled, terminated or reset.
    assert [item[0] for item in client.terminated] == ["mm-release-canary-1"]
    assert service.mutations == ["unversioned-current"]


def test_controller_receipt_is_bounded_but_keeps_exact_counts():
    pins = [
        {"workflowId": f"pinned-{index}", "runId": "r", "version": "v"}
        for index in range(30)
    ]
    compact = cutover.compact_receipt({"status": "attention", "preservedPinned": pins})
    assert compact["preservedPinned"] == [
        {"workflowId": f"pinned-{i}"} for i in range(8)
    ]
    assert compact["preservedPinnedCount"] == 30
    assert compact["status"] == "attention"
