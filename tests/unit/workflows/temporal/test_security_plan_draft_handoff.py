"""The planner's draft reaches the deterministic security.plan step (#5431).

The planning Skill writes its draft in the workspace; the agent runtime
publishes those exact bytes, links them to the parent run and returns their
ref, which the next step receives as ``previousOutputs.securityPlanDraftRef``.
The planner reads its context from the context step's trusted outputs.
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_acceptance_boundary_regressions import _request
from tests.unit.workflows.temporal.test_agent_result_artifact_handoff import (
    execution_context,
)
from tests.unit.workflows.temporal.test_remediation_context import temporal_db

DRAFT_PATH = "artifacts/security-plan/draft.json"


async def publish(tmp_path, monkeypatch, *, draft: bytes | None):
    execution_context(monkeypatch)
    workspace = tmp_path / "workspace"
    (workspace / "artifacts/security-plan").mkdir(parents=True)
    if draft is not None:
        (workspace / DRAFT_PATH).write_bytes(draft)
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="plan-run",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=_request(securityPlanDraftPath=DRAFT_PATH),
        result=AgentRunResult(
            summary="Drafted",
            metadata={"agentRunId": "plan-run"},
        ),
        include_parent_execution=True,
    )
    assert result.metadata["securityPlanDraftPath"] == DRAFT_PATH
    return result, run_store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "draft",
    [
        json.dumps({"schemaVersion": "security-plan/v1"}).encode(),
        # Invalid drafts are kept unchanged for diagnosis, not repaired here.
        b"not json {",
    ],
)
async def test_published_draft_bytes_reach_the_next_step_and_parent(
    tmp_path, monkeypatch, draft
):
    result, run_store = await publish(tmp_path, monkeypatch, draft=draft)
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await publisher.agent_runtime_publish_artifacts(result)
        ref = published.metadata["securityPlanDraftRef"]
        outputs = run_module.MoonMindRunWorkflow()._map_agent_run_result(published)[
            "outputs"
        ]
        assert outputs["securityPlanDraftRef"] == ref
        _, body = await service.read(
            artifact_id=ref, access=access_for_test("workflow:parent")
        )
        assert body == draft
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=ref, access=access_for_test("workflow:unrelated")
            )


@pytest.mark.asyncio
async def test_missing_draft_publishes_no_ref_and_keeps_the_result(
    tmp_path, monkeypatch
):
    result, run_store = await publish(tmp_path, monkeypatch, draft=None)
    async with temporal_db(tmp_path) as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        publisher = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=run_store
        )
        monkeypatch.setattr(
            publisher,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        published = await publisher.agent_runtime_publish_artifacts(result)
    assert "securityPlanDraftRef" not in published.metadata
    assert published.summary == "Drafted"


def test_planner_reads_the_context_step_outputs_as_trusted_context():
    context = {
        "schemaVersion": "security-plan-context/v1",
        "subjectType": "application",
        "standards": [
            {
                "packId": "owasp-asvs-l1",
                "requirements": [{"id": f"V{i}"} for i in range(400)],
            }
        ],
    }
    instruction = run_module.MoonMindRunWorkflow._trusted_previous_outputs_instruction(
        {
            "trustedSource": "moonmind.security.plan_context",
            "planningContext": context,
            "planningContextRef": "art_context",
            "summary": "Planning context",
            "unrelated": "dropped",
        }
    )
    assert instruction is not None
    assert "deterministic security.plan context step" in instruction
    assert "cannot change the subject, standards" in instruction
    assert "issue tool path" not in instruction
    payload = json.loads(instruction.split("```json\n", 1)[1].split("\n```", 1)[0])
    # Every requirement reaches the planner, even past the compact budget.
    assert payload["planningContext"] == context
    assert payload["planningContextRef"] == "art_context"
    assert "unrelated" not in payload
