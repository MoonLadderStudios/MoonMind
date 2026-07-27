import hashlib
import json
from datetime import UTC, datetime

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


class _AuthorizedArtifacts:
    def __init__(self) -> None:
        self.reads: list[tuple[str, str]] = []

    async def read(
        self, *, artifact_id: str, principal: str, allow_restricted_raw: bool
    ):
        self.reads.append((artifact_id, principal))
        return {}, b'{"restored":true}\n'


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.integration_ci
async def test_external_restore_and_managed_reattach_share_authoritative_workspace(
    tmp_path,
) -> None:
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    artifacts = _AuthorizedArtifacts()
    run_store = ManagedRunStore(tmp_path / "managed_runs")
    runtime = OmnigentOAuthHostRuntime(
        client=None,
        workspace_root=tmp_path,
        managed_run_store=run_store,
    )

    restored_workspace = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "external_state",
            "artifactRef": "artifact://checkpoint-1",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        artifact_gateway=artifacts,
    )
    evidence = json.loads(
        (restored_workspace.parent / ".moonmind-workspace.json").read_text(
            encoding="utf-8"
        )
    )
    restored_input = (
        restored_workspace / evidence["materializedInputRefs"][0]["localPath"]
    )
    assert restored_input.read_bytes() == b'{"restored":true}\n'

    run_store.save(
        ManagedRunRecord(
            runId="run-1",
            workflowId=workflow_id,
            agentId="omnigent",
            runtimeId="omnigent",
            status="running",
            startedAt=datetime.now(UTC),
            workspacePath=str(restored_workspace),
        )
    )
    reattached_workspace = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "managed_runtime",
            "runtimeId": "omnigent",
            "agentRunId": "run-1",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
    )

    assert reattached_workspace == restored_workspace
    assert artifacts.reads == [("checkpoint-1", f"workflow:{workflow_id}")]
    expected_workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_id}".encode("utf-8")
    ).hexdigest()[:24]
    assert restored_workspace.parent.name == expected_workspace_id
