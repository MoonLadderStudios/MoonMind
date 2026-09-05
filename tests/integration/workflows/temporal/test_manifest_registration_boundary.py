"""One registered ManifestIngest type executes both persisted entry contracts."""

import hashlib
import json
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_TASK_QUEUE
from moonmind.workflows.temporal.activity_runtime import TemporalManifestActivities
from moonmind.workflows.temporal.workflow_registry import (
    workflow_fleet_workflow_classes,
)
from moonmind.workflows.temporal.workflows.manifest_ingest import (
    MoonMindManifestIngestWorkflow,
)
from tests.helpers.temporal_artifact_workers import artifact_workers
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
MANIFEST = b"""version: v0
metadata:
  name: boundary
embeddings:
  provider: openai
vectorStore:
  type: qdrant
dataSources:
  - id: local-source
    type: GithubRepositoryReader
  - id: second-source
    type: GithubRepositoryReader
"""


@pytest.mark.parametrize("action", [None, "run"])
async def test_canonical_manifest_compiles_and_persists_real_artifacts(
    tmp_path, monkeypatch, action
):
    registered = [
        cls
        for cls in workflow_fleet_workflow_classes()
        if workflow._Definition.must_from_class(cls).name == "MoonMind.ManifestIngest"
    ]
    assert registered == [MoonMindManifestIngestWorkflow]
    async with artifact_workers(tmp_path, monkeypatch) as owners:
        manifest = await owners.put(MANIFEST)
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await register_deployment_search_attributes(env)
            queue = f"manifest-{uuid4()}"
            async with (
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=registered,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[
                        owners.bind("manifest.compile", TemporalManifestActivities),
                        owners.bind(
                            "manifest.write_summary", TemporalManifestActivities
                        ),
                        owners.bind("artifact.read"),
                    ],
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindManifestIngestWorkflow.run,
                    {
                        "manifest_ref": manifest,
                        **({"action": action} if action else {}),
                    },
                    id=str(uuid4()),
                    task_queue=queue,
                )
                result = await handle.result()
                assert result["status"] == "success"
                plan = json.loads(await owners.read(result["plan_ref"]["artifact_id"]))
                summary = json.loads(
                    await owners.read(result["summary_ref"]["artifact_id"])
                )
                assert plan["nodes"]
                assert summary["workflowId"] == handle.id
                history = await handle.fetch_history()
                await Replayer(
                    workflows=registered, workflow_runner=UnsandboxedWorkflowRunner()
                ).replay_workflow(history)


@pytest.mark.parametrize(
    "control", ["complete", "cancel_and_retry", "parent_cancel", "update"]
)
async def test_manifest_controls_real_user_children(tmp_path, monkeypatch, control):
    import asyncio

    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.common import (
        SearchAttributeKey,
        SearchAttributePair,
        TypedSearchAttributes,
    )

    from moonmind.workflows.temporal.activity_catalog import SANDBOX_TASK_QUEUE
    from moonmind.workflows.temporal.client import MOONMIND_TEMPORAL_DATA_CONVERTER
    from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

    ready = asyncio.Event()
    release = asyncio.Event()
    children = []
    owner = str(uuid4())
    async with artifact_workers(tmp_path, monkeypatch) as owners:
        manifest_ref = await owners.put(MANIFEST, principal=owner)
        from moonmind.workflows.temporal.manifest_ingest import compile_manifest_plan

        compiled = compile_manifest_plan(
            manifest_ref=manifest_ref,
            manifest_payload=MANIFEST,
            action="run",
            options={},
            requested_by={"type": "user", "id": owner},
            execution_policy={},
        )
        node_ids = [node.node_id for node in compiled.nodes]
        registry_ref = await owners.put(
            {
                "tools": [
                    {
                        "name": "fixture.work",
                        "description": "Bounded hermetic workload",
                        "inputs": {"schema": {"type": "object"}},
                        "outputs": {"schema": {"type": "object"}},
                        "executor": {
                            "activity_type": "mm.skill.execute",
                            "selector": {"mode": "by_capability"},
                        },
                        "requirements": {"capabilities": ["sandbox"]},
                        "policies": {
                            "timeouts": {
                                "start_to_close_seconds": 30,
                                "schedule_to_close_seconds": 60,
                            },
                            "retries": {"max_attempts": 1},
                        },
                    }
                ]
            },
            principal=owner,
        )
        plan_ref = await owners.put(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Child workload",
                    "created_at": "2026-09-05T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:"
                        + hashlib.sha256(
                            await owners.read(registry_ref, principal=owner)
                        ).hexdigest(),
                        "artifact_ref": registry_ref,
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST"},
                "nodes": [
                    {
                        "id": "work",
                        "tool": {"type": "skill", "name": "fixture.work"},
                        "inputs": {},
                        "options": {},
                    }
                ],
                "edges": [],
            },
            principal=owner,
        )
        output_ref = await owners.put({"summary": "completed child"}, principal=owner)

        @activity.defn(name="mm.skill.execute")
        async def work(payload: dict) -> dict:
            children.append(
                (activity.info().workflow_id, activity.info().workflow_run_id)
            )
            if len(children) >= 2:
                ready.set()
            await release.wait()
            return {
                "status": "COMPLETED",
                "outputs": {
                    "summary": "completed child",
                    "output_summary_ref": output_ref,
                },
            }

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            await register_deployment_search_attributes(env)
            queue = f"manifest-controls-{uuid4()}"
            async with (
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindManifestIngestWorkflow, MoonMindUserWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[
                        *(
                            owners.bind(name)
                            for name in [
                                "artifact.read",
                                "artifact.create",
                                "artifact.write_complete",
                                "provider_profile.list",
                                "resilience.compile_policy",
                                "execution.record_terminal_state",
                            ]
                        ),
                        owners.bind(
                            "manifest.write_summary", TemporalManifestActivities
                        ),
                        owners.bind("manifest.compile", TemporalManifestActivities),
                    ],
                ),
                Worker(env.client, task_queue=SANDBOX_TASK_QUEUE, activities=[work]),
            ):
                handle = await env.client.start_workflow(
                    MoonMindManifestIngestWorkflow.run,
                    {
                        "manifestArtifactRef": manifest_ref,
                        "planArtifactRef": plan_ref,
                        "executionPolicy": {
                            "failurePolicy": "best_effort",
                            "maxConcurrency": 2,
                        },
                        "manifestNodes": [
                            {"nodeId": node, "state": "pending", "dependencies": []}
                            for node in node_ids
                        ],
                    },
                    id=str(uuid4()),
                    task_queue=queue,
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_id"), owner
                            ),
                        ]
                    ),
                )
                await asyncio.wait_for(ready.wait(), 15)
                assert (
                    await handle.execute_update("SetConcurrency", {"maxConcurrency": 1})
                )["accepted"]
                assert not (
                    await handle.execute_update("SetConcurrency", {"maxConcurrency": 0})
                )["accepted"]
                assert (await handle.execute_update("Pause"))["accepted"]
                if control == "cancel_and_retry":
                    canceled = await handle.execute_update(
                        "CancelNodes", {"nodeIds": [node_ids[0]]}
                    )
                    assert canceled["result"]["acceptedNodeIds"] == [node_ids[0]]
                    first = next(
                        identity
                        for identity in children
                        if identity[0].endswith(":" + node_ids[0])
                    )
                    with pytest.raises(WorkflowFailureError):
                        await env.client.get_workflow_handle(
                            first[0], run_id=first[1]
                        ).result()
                    await handle.execute_update(
                        "RetryNodes", {"nodeIds": [node_ids[0]]}
                    )
                if control == "update":
                    response = await handle.execute_update(
                        "UpdateManifest",
                        {
                            "newManifestArtifactRef": manifest_ref,
                            "mode": "REPLACE_FUTURE",
                        },
                    )
                    assert response["applied"] == "next_safe_point"
                if control == "parent_cancel":
                    await handle.cancel()
                    with pytest.raises(WorkflowFailureError):
                        await handle.result()
                    for child_id, run_id in children:
                        with pytest.raises(WorkflowFailureError):
                            await env.client.get_workflow_handle(
                                child_id, run_id=run_id
                            ).result()
                else:
                    await handle.execute_update("Resume")
                    release.set()
                    result = await handle.result()
                    assert result["status"] == "completed"
                    summary = json.loads(
                        await owners.read(result["summaryRef"], principal=owner)
                    )
                    assert summary["workflowId"] == handle.id
                    if control == "cancel_and_retry":
                        assert len(children) == 3
                        assert len({run_id for _id, run_id in children}) == 3
                history = await handle.fetch_history()
                if control == "update":
                    assert len(children) == 2
                    compiles = [
                        event.event_id
                        for event in history.events
                        if event.HasField("activity_task_scheduled_event_attributes")
                        and event.activity_task_scheduled_event_attributes.activity_type.name
                        == "manifest.compile"
                    ]
                    completions = [
                        event.event_id
                        for event in history.events
                        if event.HasField(
                            "child_workflow_execution_completed_event_attributes"
                        )
                    ]
                    assert compiles and min(compiles) > max(completions)
                child_commands = [
                    event.start_child_workflow_execution_initiated_event_attributes
                    for event in history.events
                    if event.HasField(
                        "start_child_workflow_execution_initiated_event_attributes"
                    )
                ]
                assert child_commands
                assert all(
                    command.parent_close_policy
                    == workflow.ParentClosePolicy.REQUEST_CANCEL
                    for command in child_commands
                )  # REQUEST_CANCEL
                await Replayer(
                    workflows=[MoonMindManifestIngestWorkflow, MoonMindUserWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(history)
