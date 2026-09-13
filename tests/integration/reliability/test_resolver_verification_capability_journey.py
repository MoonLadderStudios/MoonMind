"""Real merge-workflow child delivery and replay preserve verification authority.

GitHub readiness, artifact storage and the resolver's repository work are
controlled service boundaries. No merge or successful repository work is
fabricated: the child deliberately stops after the capability handoff.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.api.operatorservice.v1 import (
    AddSearchAttributesRequest,
    ListSearchAttributesRequest,
)
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows import merge_automation as module
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from tests.integration.reliability.test_release_routing_journey import connect
from tests.unit.workflows.temporal.workflows.test_merge_automation_temporal import (
    _payload,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn(name="MoonMind.UserWorkflow")
class CaptureResolverRequest:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            "qualification.resolver_capability",
            payload,
            task_queue=workflow.info().task_queue,
            start_to_close_timeout=timedelta(seconds=30),
        )


async def resolver_test_client():
    """Connect and prepare only the isolated merge-workflow search attributes."""
    connected = await connect()
    client = Client(
        connected.service_client,
        namespace=connected.namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    required_attributes = {
        "mm_state",
        "mm_entry",
        "mm_owner_type",
        "mm_owner_id",
        "mm_repo",
    }
    for attempt in range(30):
        try:
            attributes = await client.operator_service.list_search_attributes(
                ListSearchAttributesRequest(namespace=client.namespace)
            )
            missing = required_attributes - set(attributes.custom_attributes)
            if not missing:
                break
            try:
                await client.operator_service.add_search_attributes(
                    AddSearchAttributesRequest(
                        namespace=client.namespace,
                        search_attributes={
                            name: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD
                            for name in missing
                        },
                    )
                )
            except RPCError as exc:
                if exc.status != RPCStatusCode.ALREADY_EXISTS:
                    raise
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND or attempt == 29:
                raise
        await asyncio.sleep(1)
    else:
        pytest.fail("isolated Temporal search attributes did not become available")
    return client


@pytest.mark.parametrize("legacy", [False, True])
async def test_merge_workflow_delivers_verification_capability_and_replays(
    legacy, monkeypatch
):
    client = await resolver_test_client()
    queue = "resolver-capability-" + uuid4().hex
    monkeypatch.setattr(module, "INTEGRATIONS_TASK_QUEUE", queue)
    monkeypatch.setattr(module, "ARTIFACTS_TASK_QUEUE", queue)
    monkeypatch.setattr(
        module.settings,
        "temporal",
        module.settings.temporal.model_copy(
            update={"user_workflow_v2_task_queue": queue}
        ),
    )
    original_patched = module.workflow.patched
    if legacy:
        monkeypatch.setattr(
            module.workflow,
            "patched",
            lambda name: (
                False
                if name
                == module.MERGE_AUTOMATION_RESOLVER_VERIFICATION_CAPABILITY_PATCH
                else original_patched(name)
            ),
        )
    observed = []

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def readiness(_payload: dict) -> dict:
        return {
            "headSha": "abc123",
            "pullRequestOpen": True,
            "checksComplete": True,
            "checksPassing": False,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "policyAllowed": True,
        }

    @activity.defn(name="artifact.create")
    async def create(_payload: dict) -> list:
        return [{"artifact_id": "art-qualification-" + uuid4().hex}, {}]

    @activity.defn(name="artifact.write_complete")
    async def write(_payload: dict) -> dict:
        return {}

    @activity.defn(name="qualification.resolver_capability")
    async def capture(payload: dict) -> dict:
        observed.append(payload)
        return {
            "status": "success",
            "mergeAutomationDisposition": "failed",
            "reason": "Qualification deliberately stops before repository mutation.",
        }

    payload = _payload()
    payload.pop("jiraIssueKey", None)
    payload["resolverTemplate"] = {
        "targetRuntime": "codex_cli",
        "requiredCapabilities": ["git", "gh"],
    }
    async with Worker(
        client,
        task_queue=queue,
        workflows=[MoonMindMergeAutomationWorkflow, CaptureResolverRequest],
        activities=[readiness, create, write, capture],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ):
        for attempt in range(30):
            try:
                handle = await client.start_workflow(
                    MoonMindMergeAutomationWorkflow.run,
                    payload,
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=45),
                )
                break
            except RPCError as exc:
                if exc.status != RPCStatusCode.NOT_FOUND or attempt == 29:
                    raise
                await asyncio.sleep(1)
        result = await asyncio.wait_for(handle.result(), timeout=50)
        history = await handle.fetch_history()
    assert result["status"] == "failed", "the qualification never performs a merge"
    assert len(observed) == 1
    assert observed[0]["initial_parameters"]["requiredCapabilities"] == (
        ["git", "gh"] if legacy else ["git", "gh", "docker"]
    )
    # Replaying old histories must preserve their recorded child authority;
    # replaying new histories must retain the new patch marker and capability.
    monkeypatch.setattr(module.workflow, "patched", original_patched)
    await Replayer(
        workflows=[MoonMindMergeAutomationWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    ).replay_workflow(history)
