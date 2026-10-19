"""Remediation consumers use admitted execution scope, under every auth mode.

Real SQLite artifact metadata and local blobs exercise authorization; no live
Temporal server, PostgreSQL, Docker, or external provider is involved.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base, TemporalArtifactRedactionLevel
from moonmind.config.settings import settings
from moonmind.omnigent.remediation_workspace import (
    ArtifactRemediationHeadLoader,
    RemediationWorkspaceBinding,
    RemediationWorkspaceError,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
    omnigent_execute_activity,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from tests.unit.omnigent.test_remediation_workspace import _binding, _head


@pytest_asyncio.fixture(params=("accounts", "oidc", "header", "disabled"))
async def artifacts(request, tmp_path, monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", request.param)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/remediation.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _publish(sessions, *, consumer, workflow_id="workflow-1", protection=None):
    owner = (
        "service:remediation-lifecycle"
        if consumer == "head"
        else "service:remediation-context"
    )
    payload = json.dumps(
        asdict(_head()) if consumer == "head" else {"remainingWork": ["retry"]}
    ).encode()
    async with sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        artifact, _ = await service.create(
            principal=owner,
            content_type="application/json",
            link=ExecutionRef(
                namespace="default",
                workflow_id=workflow_id,
                run_id="run-1",
                link_type="evidence.remediation",
            ),
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id, principal=owner, payload=payload
        )
        if protection == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        elif protection == "quarantined":
            artifact.metadata_json = {"quarantine": True}
        await session.commit()
        return artifact.artifact_id, payload


def _request(artifact_id, *, with_step=True):
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "managed",
            "agentId": "codex",
            "correlationId": "untrusted-correlation",
            "idempotencyKey": "turn-1",
            "parameters": {
                "workflowId": "unrelated",
                "principal": "operator",
                "gateResultRef": f"artifact://{artifact_id}",
                "remainingWorkRef": f"artifact://{artifact_id}",
            },
            **(
                {
                    "stepExecution": {
                        "workflowId": "workflow-1",
                        "runId": "run-1",
                        "logicalStepId": "remediate",
                        "executionOrdinal": 2,
                        "stepExecutionId": "workflow-1:run-1:remediate:execution:2",
                        "runtimeContextPolicy": "fresh_agent_run",
                    }
                }
                if with_step
                else {}
            ),
        }
    )


async def _consume(sessions, artifact_id, *, consumer, tmp_path, monkeypatch):
    service = _OnDemandTemporalArtifactService(sessions)
    if consumer == "head":
        return await ArtifactRemediationHeadLoader(
            service, workflow_id="workflow-1"
        ).load(f"artifact://{artifact_id}")
    managed_root = tmp_path / "agent_jobs"
    workspace = managed_root / "run-1" / "repo"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    return await TemporalAgentRuntimeActivities(
        artifact_service=service
    ).agent_runtime_prepare_turn_instructions(
        {
            "request": _request(artifact_id).model_dump(by_alias=True),
            "workspacePath": str(workspace),
            "metadataOnly": True,
            "skipSkillMaterialization": True,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ("head", "evidence"))
async def test_remediation_consumers_read_own_workflow_on_retry(
    artifacts, tmp_path, monkeypatch, consumer
):
    artifact_id, payload = await _publish(artifacts, consumer=consumer)
    for _ in range(2):
        result = await _consume(
            artifacts,
            artifact_id,
            consumer=consumer,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
        if consumer == "head":
            assert result == json.loads(payload)
        else:
            files = list(
                (tmp_path / "agent_jobs/run-1/artifacts/remediation-inputs").glob(
                    "*.json"
                )
            )
            assert len(files) == 2
            assert all(path.read_bytes() == payload for path in files)


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ("head", "evidence"))
@pytest.mark.parametrize("denial", ("unrelated", "restricted", "quarantined"))
async def test_remediation_consumers_cannot_expand_scope_or_raw_policy(
    artifacts, tmp_path, monkeypatch, consumer, denial
):
    artifact_id, _ = await _publish(
        artifacts,
        consumer=consumer,
        workflow_id="unrelated" if denial == "unrelated" else "workflow-1",
        protection=None if denial == "unrelated" else denial,
    )
    with pytest.raises(
        (RemediationWorkspaceError, TemporalActivityRuntimeError)
    ) as failure:
        await _consume(
            artifacts,
            artifact_id,
            consumer=consumer,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
        )
    assert isinstance(
        failure.value.__cause__,
        TemporalArtifactStateError
        if denial == "quarantined"
        else TemporalArtifactAuthorizationError,
    )
    assert not list((tmp_path / "agent_jobs").rglob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ("head", "evidence"))
async def test_remediation_consumers_require_admitted_step_scope(
    tmp_path, monkeypatch, consumer
):
    class UnreadableService:
        async def read(self, **kwargs):
            pytest.fail("missing execution authority must fail before reading")

    if consumer == "head":
        with pytest.raises(RemediationWorkspaceError, match="authority"):
            await ArtifactRemediationHeadLoader(
                UnreadableService(), workflow_id=None
            ).load("artifact://art_missing")
    else:
        with pytest.raises(TemporalActivityRuntimeError, match="Step Execution"):
            await TemporalAgentRuntimeActivities(
                artifact_service=UnreadableService()
            )._materialize_remediation_evidence_for_turn(
                request=_request("art_missing", with_step=False),
                workspace_path=str(tmp_path),
            )


@pytest.mark.asyncio
async def test_production_omnigent_activity_binds_head_to_admitted_step(
    artifacts, monkeypatch
):
    import api_service.db.base as db_base
    from moonmind.omnigent import profile_bound_execution

    artifact_id, _ = await _publish(artifacts, consumer="head")
    binding = _binding(step="workflow-1:run-1:remediate:execution:2").model_copy(
        update={"head_authority_ref": f"artifact://{artifact_id}"}
    )
    monkeypatch.setattr(db_base, "async_session_maker", artifacts)

    class Coordinator:
        def __init__(self, *, workspace_owner, **kwargs):
            self.owner = workspace_owner

        async def execute(self, request):
            head = await self.owner._authoritative_head(
                RemediationWorkspaceBinding.model_validate(
                    request.remediation_workspace
                )
            )
            assert head == _head()
            return AgentRunResult(summary="head admitted")

    monkeypatch.setattr(
        profile_bound_execution, "OmnigentProfileBoundExecutionCoordinator", Coordinator
    )
    request = _request(artifact_id).model_copy(
        update={
            "agent_kind": "external",
            "agent_id": "omnigent",
            "execution_profile_ref": "codex",
            "remediation_workspace": binding.model_dump(by_alias=True),
        }
    )
    result = await ActivityEnvironment().run(omnigent_execute_activity, request)
    assert result.summary == "head admitted"


@pytest.mark.asyncio
async def test_scoped_head_read_preserves_owner_and_version_fences(artifacts, tmp_path):
    from moonmind.omnigent.remediation_workspace import SandboxRemediationWorkspaceOwner

    artifact_id, _ = await _publish(artifacts, consumer="head")
    owner = SandboxRemediationWorkspaceOwner(
        tmp_path / "workspaces",
        head_loader=ArtifactRemediationHeadLoader(
            _OnDemandTemporalArtifactService(artifacts), workflow_id="workflow-1"
        ),
    )
    binding = _binding().model_copy(
        update={"head_authority_ref": f"artifact://{artifact_id}"}
    )
    for changes, code in (
        ({"expected_head_version": 3}, "REMEDIATION_LOOP_HEAD_MISMATCH"),
        ({"workflow_id": "unrelated"}, "REMEDIATION_WORKSPACE_OWNER_MISMATCH"),
    ):
        with pytest.raises(RemediationWorkspaceError, match=code):
            await owner.admit_and_resolve(
                binding=binding.model_copy(update=changes),
                workflow_id="workflow-1",
                step_execution_id="step-2",
            )
    assert not list((tmp_path / "workspaces").rglob("repo"))
