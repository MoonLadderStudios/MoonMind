"""Temporal regression tests for OAuth session workflow."""

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from moonmind.workflows.temporal.workflows.oauth_session import (
    MoonMindOAuthSessionWorkflow,
    WORKFLOW_TASK_QUEUE,
    ACTIVITY_TASK_QUEUE,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

@activity.defn(name="oauth_session.ensure_volume")
async def mock_ensure_volume(request: dict) -> dict:
    return {"volume_ref": request.get("volume_ref"), "status": "ok"}

@activity.defn(name="oauth_session.start_auth_runner")
async def mock_start_auth_runner(request: dict) -> dict:
    return {
        "container_name": "mocked_container",
        "terminal_session_id": "ts_123",
        "terminal_bridge_id": "br_123"
    }

@activity.defn(name="oauth_session.update_terminal_session")
async def mock_update_terminal_session(request: dict) -> dict:
    return {}

@activity.defn(name="oauth_session.update_status")
async def mock_update_status(request: dict) -> dict:
    return {}

@activity.defn(name="oauth_session.verify_cli_fingerprint")
async def mock_verify_cli_fingerprint(request: dict) -> dict:
    return {"verified": True, "fingerprint_verified": True}

@activity.defn(name="oauth_session.register_profile")
async def mock_register_profile(request: dict) -> dict:
    return {"profile_id": "prof_123"}

@activity.defn(name="oauth_session.stop_auth_runner")
async def mock_stop_auth_runner(request: dict) -> dict:
    return {"stopped": True}


async def test_oauth_session_workflow_success() -> None:
    """Test full successful OAuth session workflow lifecycle."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        # We need two workers since the workflow calls activities on ACTIVITY_TASK_QUEUE
        # while itself running on WORKFLOW_TASK_QUEUE (or another workflow queue)
        
        async with Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                mock_ensure_volume,
                mock_start_auth_runner,
                mock_update_terminal_session,
                mock_update_status,
                mock_verify_cli_fingerprint,
                mock_register_profile,
                mock_stop_auth_runner,
            ],
        ), Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[MoonMindOAuthSessionWorkflow],
        ):
            handle = await env.client.start_workflow(
                MoonMindOAuthSessionWorkflow.run,
                {
                    "session_id": "sess_default",
                    "runtime_id": "codex_cli",
                    "volume_ref": "vol_123",
                    "volume_mount_path": "/mnt/auth",
                },
                id="oauth-session:sess_default",
                task_queue=WORKFLOW_TASK_QUEUE,
            )

            # Workflow awaits user action via finalize signal
            await handle.signal(MoonMindOAuthSessionWorkflow.finalize)

            result = await handle.result()
            
            assert result["session_id"] == "sess_default"
            assert result["status"] == "succeeded"
            
async def test_oauth_session_workflow_cancel() -> None:
    """Test OAuth session workflow cancellation."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=ACTIVITY_TASK_QUEUE,
            activities=[
                mock_ensure_volume,
                mock_start_auth_runner,
                mock_update_terminal_session,
                mock_update_status,
                mock_verify_cli_fingerprint,
                mock_register_profile,
                mock_stop_auth_runner,
            ],
        ), Worker(
            env.client,
            task_queue=WORKFLOW_TASK_QUEUE,
            workflows=[MoonMindOAuthSessionWorkflow],
        ):
            handle = await env.client.start_workflow(
                MoonMindOAuthSessionWorkflow.run,
                {
                    "session_id": "sess_default",
                    "runtime_id": "codex_cli",
                    "volume_ref": "vol_123",
                    "volume_mount_path": "/mnt/auth",
                },
                id="oauth-session:sess_default_cancel",
                task_queue=WORKFLOW_TASK_QUEUE,
            )

            await handle.signal(MoonMindOAuthSessionWorkflow.cancel)

            result = await handle.result()
            
            assert result["session_id"] == "sess_default"
            assert result["status"] == "cancelled"

