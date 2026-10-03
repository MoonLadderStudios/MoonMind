"""Exercise Run's Jules handoff and preserve retained activity arguments."""

import asyncio
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

URL = "https://github.com/org/repo/pull/123"


@workflow.defn(name="RunJulesAuthorityHistory")
class _RunJulesAuthorityHistory:
    @workflow.run
    async def run(self, parameters: dict) -> dict:
        run = MoonMindRunWorkflow()
        run._integration = "jules"
        run._repo = parameters.get("repo")
        try:
            await run._run_integration_stage(parameters=parameters, plan_ref=None)
        except ValueError as exc:
            return {"published": False, "reason": str(exc)}
        return {"published": run._publish_status == "published"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy_patches",
    [
        (),
        ("jules-merge-canonical-authority-v1",),
        ("jules-merge-canonical-authority-v1", "jules-merge-target-authority-v1"),
    ],
    ids=["canonical", "retained-bound", "retained-unbound"],
)
@pytest.mark.parametrize("legacy_aliases", [False, True])
async def test_run_jules_authority_history_replays(
    monkeypatch, legacy_patches, legacy_aliases
):
    calls = []
    emitted = []

    @activity.defn(name="integration.jules.start")
    async def start(payload: dict) -> dict:
        return {"external_id": "ext-1"}

    @activity.defn(name="integration.jules.status")
    async def status(payload: dict) -> dict:
        return {"normalized_status": "completed"}

    @activity.defn(name="integration.jules.fetch_result")
    async def fetch(payload: dict) -> dict:
        return {"url": URL, "summary": "Done"}

    @activity.defn(name="repo.merge_pr")
    async def merge(payload: dict) -> dict:
        calls.append(dict(payload))
        if not payload.get("expected_repository"):
            return {"merged": False, "reasonCode": "merge_authority_required"}
        if not payload.get("expected_head_sha"):
            return {
                "merged": False,
                "reasonCode": "merge_head_resolved",
                "expectedHeadSha": "a" * 40,
            }
        return {"merged": True, "mergeSha": "c" * 40}

    # Keep the fixture on one queue; memo/search-attribute projection is outside
    # this handoff boundary and requires a separately provisioned namespace.
    monkeypatch.setattr(MoonMindRunWorkflow, "_update_search_attributes", lambda self: None)
    monkeypatch.setattr(MoonMindRunWorkflow, "_update_memo", lambda self: None)
    monkeypatch.setattr(
        MoonMindRunWorkflow,
        "_execute_kwargs_for_route",
        lambda self, route: {
            "task_queue": workflow.info().task_queue,
            "start_to_close_timeout": timedelta(seconds=10),
        },
    )
    original_execute = workflow.execute_activity

    async def execute(name, payload, **kwargs):
        if name == "repo.merge_pr":
            emitted.append(dict(payload))
        return await original_execute(name, payload, **kwargs)

    monkeypatch.setattr(workflow, "execute_activity", execute)
    original_patched = workflow.patched
    monkeypatch.setattr(
        workflow,
        "patched",
        lambda patch: False if patch in legacy_patches else original_patched(patch),
    )
    workspace = {"repositoryTarget": {
        "provider": "git",
        "repository": {"name": "org/repo"},
        "branch": {"name": "release"},
    }}
    parameters = {"publishMode": "branch", "workspaceSpec": workspace}
    if legacy_aliases:
        parameters["repo"] = "org/repo"
        workspace["startingBranch"] = "main"
    queue = f"run-jules-authority-{len(legacy_patches)}-{legacy_aliases}"
    async with asyncio.timeout(60):
        async with (
            await WorkflowEnvironment.start_time_skipping(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ) as env,
            Worker(
                env.client,
                task_queue=queue,
                workflows=[_RunJulesAuthorityHistory],
                workflow_runner=UnsandboxedWorkflowRunner(),
                activities=[start, status, fetch, merge],
                graceful_shutdown_timeout=timedelta(seconds=1),
            ),
        ):
            handle = await env.client.start_workflow(
                _RunJulesAuthorityHistory.run,
                parameters,
                id=queue,
                task_queue=queue,
            )
            result = await asyncio.wait_for(handle.result(), 30)
            history = await handle.fetch_history()

    repository = "org/repo" if legacy_aliases or not legacy_patches else ""
    bound = {
        "pr_url": URL,
        "expected_repository": repository,
        "target_branch": "main" if legacy_aliases or legacy_patches else "release",
    }
    expected = [{"pr_url": URL}, bound] if len(legacy_patches) == 2 else [bound]
    if repository:
        expected.append({**bound, "expected_head_sha": "a" * 40})
    elif len(legacy_patches) != 2:
        expected.append(bound)
    assert calls == expected
    assert result["published"] is bool(repository)
    if not repository:
        assert "merge failed" in result["reason"]

    monkeypatch.setattr(workflow, "patched", original_patched)
    emitted.clear()
    await asyncio.wait_for(
        Replayer(
            workflows=[_RunJulesAuthorityHistory],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history),
        30,
    )
    # The SDK's determinism check does not compare every activity argument.
    assert emitted == calls
