"""Retained and new account-selection Activity commands replay unchanged."""

from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_STEP_PROFILE_SELECTION_INHERITANCE_PATCH,
    MoonMindRunWorkflow,
)


@activity.defn
async def capture_account_requests(requests: list[dict]) -> list[dict]:
    return requests


class _LegacyAccountSelection(MoonMindRunWorkflow):
    @staticmethod
    def _workflow_patch_enabled(patch_id: str) -> bool:
        if patch_id == RUN_STEP_PROFILE_SELECTION_INHERITANCE_PATCH:
            return False
        return MoonMindRunWorkflow._workflow_patch_enabled(patch_id)


def _account_requests(run: MoonMindRunWorkflow) -> list[dict]:
    return [
        run._build_agent_execution_request(
            node_inputs={
                "instructions": "Use admitted account intent.",
                "runtime": {"mode": "codex_cli", **runtime},
            },
            node_id=f"step-{index}",
            tool_name="codex_cli",
            workflow_parameters={
                "profileId": "saved-account",
                "workflow": {"runtime": {"profileId": "saved-account", "modelTier": 1}},
            },
        ).model_dump(mode="json", by_alias=True)
        for index, runtime in enumerate(
            [
                {"profileSelector": {"tagsAll": ["chosen"]}},
                {},
                {
                    "profileId": "recorded-account",
                    "profileSelector": {"tagsAll": ["chosen"]},
                },
                {"providerProfileRef": "chosen-account"},
                {
                    "providerProfileRef": "chosen-account",
                    "executionProfileRef": "recorded-account",
                },
                {"execution_profile_ref": "chosen-account"},
            ]
        )
    ]


@workflow.defn(name="AccountSelectionReplayFixture")
class _LegacyAccountSelectionWorkflow:
    @workflow.run
    async def run(self) -> list[dict]:
        return await workflow.execute_activity(
            capture_account_requests,
            _account_requests(_LegacyAccountSelection()),
            start_to_close_timeout=timedelta(seconds=10),
        )


@workflow.defn(name="AccountSelectionReplayFixture")
class _CurrentAccountSelectionWorkflow:
    @workflow.run
    async def run(self) -> list[dict]:
        return await workflow.execute_activity(
            capture_account_requests,
            _account_requests(MoonMindRunWorkflow()),
            start_to_close_timeout=timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_account_selection_old_and_new_activity_payloads_replay():
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for name, implementation, expected in (
            (
                "legacy",
                _LegacyAccountSelectionWorkflow,
                [
                    "saved-account",
                    "saved-account",
                    "recorded-account",
                    "saved-account",
                    "recorded-account",
                    "saved-account",
                ],
            ),
            (
                "current",
                _CurrentAccountSelectionWorkflow,
                [
                    None,
                    "saved-account",
                    "recorded-account",
                    "chosen-account",
                    "chosen-account",
                    "chosen-account",
                ],
            ),
        ):
            queue = f"account-selection-{name}"
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[capture_account_requests],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await environment.client.start_workflow(
                    implementation.run,
                    id=queue,
                    task_queue=queue,
                )
                requests = await handle.result()
                assert [
                    request["executionProfileRef"] for request in requests
                ] == expected
                histories.append(await handle.fetch_history())
    replayer = Replayer(
        workflows=[_CurrentAccountSelectionWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
