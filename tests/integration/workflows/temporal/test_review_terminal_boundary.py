"""Real reviewer, UserWorkflow, durable artifacts/checkpoints and terminal owner."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import (
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from moonmind.config.settings import AppSettings, OpenAISettings, settings
from moonmind.schemas.managed_session_models import CodexManagedSessionWorkflowInput
from moonmind.workflows.temporal.activities.reviewer import ConfiguredStepReviewer
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_TASK_QUEUE,
    ARTIFACTS_TASK_QUEUE,
    LLM_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalReviewActivities,
    TemporalSandboxActivities,
    _bind_activity_handler,
)
from moonmind.workflows.temporal.client import (
    MOONMIND_TEMPORAL_DATA_CONVERTER,
    TemporalClientAdapter,
)
from moonmind.workflows.temporal.workflows.agent_session import (
    MoonMindAgentSessionWorkflow,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_artifact_workers import artifact_workers
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize(
    "review_settings",
    [
        {},
        {
            "reviewer_model": "default",
            "max_review_attempts": 2,
            "review_timeout_seconds": 120,
        },
    ],
)
@pytest.mark.parametrize("cleanup_failure", [False, True])
@pytest.mark.parametrize("outcome", ["approved", "unavailable", "timeout", "malformed"])
async def test_review_survives_through_terminal_persistence(
    tmp_path, monkeypatch, review_settings, outcome, cleanup_failure
):
    owner = str(uuid4())
    workspace = tmp_path / "temporal_sandbox" / "worktree"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(settings.workflow, "workspace_root", str(tmp_path))
    session_ready = asyncio.Event()
    cleanup_calls = []
    executed = []
    requests = []
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def provider():
        requests.append(outcome)
        if outcome == "timeout":
            await asyncio.sleep(2)
        content = (
            "not-json"
            if outcome == "malformed"
            else json.dumps({"verdict": "PASS", "confidence": 0.9})
        )
        return {"choices": [{"message": {"content": content}}]}

    config = AppSettings(
        default_chat_provider="openai",
        openai=OpenAISettings(
            openai_api_key=None if outcome == "unavailable" else "hermetic",
            openai_enabled=True,
        ),
    )
    reviewer = TemporalReviewActivities(
        reviewer=ConfiguredStepReviewer(config, transport=ASGITransport(app))
    )
    review_handler = _bind_activity_handler(
        reviewer, func=TemporalReviewActivities.step_review, activity_type="step.review"
    )
    async with artifact_workers(tmp_path, monkeypatch) as owners:
        registry = await owners.put(
            {
                "skills": [
                    {
                        "name": "fixture.execute",
                        "description": "Hermetic work",
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
        policy = {"enabled": True, **review_settings}
        if outcome == "timeout":
            policy["review_timeout_seconds"] = 1
        plan = await owners.put(
            {
                "plan_version": "1.0",
                "metadata": {
                    "title": "Review lifecycle",
                    "created_at": "2026-09-05T00:00:00Z",
                    "registry_snapshot": {
                        "digest": "reg:sha256:"
                        + hashlib.sha256(
                            await owners.read(registry, principal=owner)
                        ).hexdigest(),
                        "artifact_ref": registry,
                    },
                },
                "policy": {"failure_mode": "FAIL_FAST", "approval_policy": policy},
                "nodes": [
                    {
                        "id": "work",
                        "tool": {"type": "skill", "name": "fixture.execute"},
                        "inputs": {},
                        "options": {},
                    }
                ],
                "edges": [],
            },
            principal=owner,
        )
        output = None

        @activity.defn(name="mm.skill.execute")
        async def work(payload: dict) -> dict:
            nonlocal output
            # Only the business workload is a fixture. Review and all persistence
            # use the registered production implementations and real artifact IDs.
            await session_ready.wait()
            executed.append(activity.info().activity_id)
            (workspace / "accepted.txt").write_text("Accepted implementation evidence")
            output = await owners.put(
                {"summary": "Accepted implementation evidence"}, principal=owner
            )
            return {
                "status": "COMPLETED",
                "outputs": {
                    "summary": "Accepted implementation evidence",
                    "output_summary_ref": output,
                    "workspacePath": str(workspace),
                },
                "output_artifacts": [output],
            }

        activity_names = [
            "artifact.create",
            "artifact.read",
            "artifact.write_complete",
            "resilience.compile_policy",
            "provider_profile.list",
            "step_checkpoint.create",
            "execution.record_terminal_state",
        ]
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            await register_deployment_search_attributes(env)
            queue = f"review-terminal-{uuid4()}"
            runtime = TemporalAgentRuntimeActivities(
                client_adapter=TemporalClientAdapter(env.client)
            )
            bound_snapshot = _bind_activity_handler(
                runtime,
                func=TemporalAgentRuntimeActivities.agent_runtime_load_session_snapshot,
                activity_type="agent_runtime.load_session_snapshot",
            )

            @activity.defn(name="agent_runtime.load_session_snapshot")
            async def load_snapshot(payload: dict):
                snapshot = await bound_snapshot(payload)
                cleanup_calls.append(snapshot)
                if cleanup_failure:
                    # Inject a transport loss after the real Activity queried the
                    # real session owner. No workflow decision/result is replaced.
                    raise ApplicationError(
                        "injected cleanup response loss", non_retryable=True
                    )
                return snapshot

            async with (
                Worker(
                    env.client,
                    task_queue=queue,
                    workflows=[MoonMindUserWorkflow, MoonMindAgentSessionWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(
                    env.client,
                    task_queue=ARTIFACTS_TASK_QUEUE,
                    activities=[owners.bind(name) for name in activity_names],
                ),
                Worker(
                    env.client, task_queue=LLM_TASK_QUEUE, activities=[review_handler]
                ),
                Worker(
                    env.client,
                    task_queue=SANDBOX_TASK_QUEUE,
                    activities=[
                        work,
                        owners.bind(
                            "workspace.capture_checkpoint", TemporalSandboxActivities
                        ),
                    ],
                ),
                Worker(
                    env.client,
                    task_queue=AGENT_RUNTIME_TASK_QUEUE,
                    activities=[load_snapshot],
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflow_type": "MoonMind.UserWorkflow",
                        "plan_artifact_ref": plan,
                        "initial_parameters": {
                            "publishMode": "none" if outcome == "approved" else "pr"
                        },
                    },
                    id=f"review-{uuid4()}",
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
                desc = await handle.describe()
                async with owners.sessions() as session:
                    session.add(
                        TemporalExecutionCanonicalRecord(
                            workflow_id=desc.id,
                            run_id=desc.run_id,
                            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                            owner_type=TemporalExecutionOwnerType.USER,
                            owner_id=owner,
                            entry="user_workflow",
                        )
                    )
                    await session.commit()
                session_handle = await env.client.start_workflow(
                    MoonMindAgentSessionWorkflow.run,
                    CodexManagedSessionWorkflowInput(
                        agentRunId=desc.id, runtimeId="codex_cli"
                    ),
                    id=f"{desc.id}:session:codex_cli",
                    task_queue=queue,
                )
                session_snapshot = await session_handle.query("get_status")
                await handle.signal(
                    "managed_session_bound", {"binding": session_snapshot["binding"]}
                )
                session_ready.set()
                if outcome == "approved":
                    result = await handle.result()
                    assert result["status"] == "success"
                else:
                    with pytest.raises(
                        WorkflowFailureError, match="Workflow execution failed"
                    ):
                        await handle.result()
                assert (await session_handle.result())["status"] == "terminated"
                assert len(cleanup_calls) == 1
                ledger = await handle.query("get_step_ledger")
                history = await handle.fetch_history()
                assert len(executed) == 1
                row = ledger["steps"][0]
                assert row["checks"][0]["gateVerdict"] == (
                    "FULLY_IMPLEMENTED" if outcome == "approved" else "NO_DETERMINATION"
                )
                assert row["artifacts"]["outputSummary"] == output
                manifest = json.loads(
                    await owners.read(
                        row["artifacts"]["stepExecutionManifestRef"], principal=owner
                    )
                )
                assert (
                    manifest["checks"][0]["gateVerdict"]
                    == row["checks"][0]["gateVerdict"]
                )
                checkpoint_ref = row["refs"]["checkpointRefsByBoundary"]["after_gate"]
                checkpoint = json.loads(
                    await owners.read(checkpoint_ref, principal=owner)
                )
                assert checkpoint["boundary"] == "after_gate"
                assert checkpoint["source"]["runId"] == desc.run_id
                archive = await owners.read(checkpoint["workspace"]["archiveRef"])
                with tarfile.open(fileobj=io.BytesIO(archive)) as captured:
                    member = next(
                        name
                        for name in captured.getnames()
                        if name.endswith("accepted.txt")
                    )
                    assert (
                        captured.extractfile(member).read()
                        == b"Accepted implementation evidence"
                    )
                assert (
                    workspace / "accepted.txt"
                ).read_text() == "Accepted implementation evidence"
                assert (
                    await owners.read(output, principal=owner)
                    == b'{"summary": "Accepted implementation evidence"}'
                )
                async with owners.sessions() as session:
                    record = await session.get(
                        TemporalExecutionCanonicalRecord, desc.id
                    )
                    assert record.close_status == (
                        "completed" if outcome == "approved" else "failed"
                    )
                    assert (
                        record.finish_summary_json["publish"]["status"] != "published"
                    )
                    finish = record.finish_summary_json
                    if outcome != "approved":
                        assert "NO_DETERMINATION" in finish["finishOutcome"]["reason"]
                        assert finish["publish"]["mode"] == "pr"
                    assert "injected cleanup response loss" not in str(
                        finish["finishOutcome"]
                    )
                await Replayer(
                    workflows=[MoonMindUserWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                ).replay_workflow(history)
