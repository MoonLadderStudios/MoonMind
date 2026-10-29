"""Branch retention delegates reads from its durable launch and real Activity."""

from __future__ import annotations

import dataclasses
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.testing import ActivityEnvironment

from api_service.db.models import (
    Base,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactPin,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    build_branch_turn_execution_identity,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workflows import checkpoint_branch_turn as turns
from tests.artifact_access import access_for_test

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def retention(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branch.db")
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")

    def artifacts(session):
        return TemporalArtifactService(TemporalArtifactRepository(session), store=store)

    monkeypatch.setattr(turns, "async_session_maker", sessions)
    monkeypatch.setattr(turns, "get_checkpoint_branch_artifact_service", artifacts)
    identity = build_branch_turn_execution_identity(
        branch_id="branch", branch_turn_id="turn", logical_step_id="step", ordinal=1
    )
    async with sessions() as session:
        session.add(TemporalExecutionCanonicalRecord(
            workflow_id="source", run_id="source-run", namespace="default",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW, entry="api",
        ))
        await session.commit()
        service = CheckpointBranchService(session)
        await service.create_branch_graph({
            "branchId": "branch", "branchTurnId": "turn",
            "label": "Scoped branch retention",
            "source": {
                "workflowId": "source", "runId": "source-run",
                "logicalStepId": "step", "sourceExecutionOrdinal": 1,
                "checkpointBoundary": "after_execution",
                "checkpointRef": "artifact://source/checkpoint",
            },
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "runtimeContextPolicy": "fresh_agent_run",
            "instructionRef": "artifact://source/instructions",
            "instructionDigest": "sha256:" + "1" * 64,
            "idempotencyKey": "create-turn",
        })
        await service.claim_turn_execution(
            workflow_id="source", branch_id="branch", branch_turn_id="turn",
            context_bundle_ref="artifact://launch/context",
            step_execution_manifest_ref="artifact://launch/manifest",
            diagnostics_ref="artifact://launch/diagnostics",
            agent_request_ref="artifact://launch/request",
            launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                workflow_id="source", branch_id="branch", branch_turn_id="turn"
            ),
            created_step_execution_id=identity.step_execution_id,
            runtime_agent_run_id=identity.agent_run_workflow_id,
            execution_workflow_id=identity.workflow_id,
        )
        await session.commit()

    producer = str(uuid4())

    async def seed(workflow_id, *, restricted=False, quarantined=False):
        async with sessions() as session:
            service = artifacts(session)
            artifact, _ = await service.create(
                principal=producer, content_type="text/plain",
                link={
                    "namespace": "default", "workflow_id": workflow_id,
                    "run_id": "producer-run", "link_type": "output.test",
                },
                redaction_level=(TemporalArtifactRedactionLevel.RESTRICTED
                                 if restricted else TemporalArtifactRedactionLevel.NONE),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id, principal=producer,
                payload=b"retained evidence", content_type="text/plain",
            )
            if quarantined:
                artifact.metadata_json = {**(artifact.metadata_json or {}), "quarantine": True}
                await session.commit()
            return f"artifact://{artifact.artifact_id}"

    environment = ActivityEnvironment()
    environment.info = dataclasses.replace(
        environment.info, workflow_id=identity.workflow_id,
        workflow_type=turns.WORKFLOW_NAME, workflow_run_id="actual-temporal-run",
    )
    payload = {
        "workflowId": "source", "branchId": "branch", "branchTurnId": "turn",
        "principal": producer, "sourceNamespace": "default", "sourceRunId": "source-run",
        "outcome": "failed", "agentResult": {"summary": "Retain failed turn evidence."},
    }
    yield sessions, artifacts, seed, environment, payload, identity, producer
    await engine.dispose()


async def _assert_not_retained(sessions, ref):
    artifact_id = ref.removeprefix("artifact://")
    async with sessions() as session:
        assert not (await session.scalars(select(TemporalArtifactPin).where(
            TemporalArtifactPin.artifact_id == artifact_id
        ))).all()
        links = (await session.scalars(select(TemporalArtifactLink).where(
            TemporalArtifactLink.artifact_id == artifact_id
        ))).all()
        assert all(link.link_type != turns._RETENTION_LINK_TYPE for link in links)


async def test_retention_reads_only_recorded_source_turn_and_child_scopes(retention):
    sessions, artifacts, seed, environment, payload, identity, producer = retention
    refs = [await seed(workflow_id) for workflow_id in (
        "source", identity.workflow_id, identity.agent_run_workflow_id
    )]
    payload["agentResult"]["outputRefs"] = refs
    result = await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
    assert await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload) == result
    async with sessions() as session:
        for ref in [*refs, result["agentResultRef"], result["diagnosticsRef"]]:
            artifact, links, pinned, _ = await artifacts(session).get_metadata(
                artifact_id=ref.removeprefix("artifact://"),
                access=access_for_test(
                    "service:checkpoint-branch-turn",
                    admitted_principal="workflow:source",
                ),
            )
            assert pinned
            assert artifact.created_by_principal == producer
            assert any(link.link_type == turns._RETENTION_LINK_TYPE for link in links)
        _, data = await artifacts(session).read(
            artifact_id=result["agentResultRef"].removeprefix("artifact://"),
            access=access_for_test(
                "service:checkpoint-branch-turn", admitted_principal="workflow:source"
            ),
        )
        assert json.loads(data)["outputRefs"] == refs


@pytest.mark.parametrize(
    "kind,auth_mode",
    [
        (kind, mode)
        for kind in ("foreign", "restricted", "quarantined")
        for mode in ("accounts", "oidc", "header", "disabled")
        if kind != "foreign" or mode != "disabled"
    ],
)
async def test_retention_rejects_foreign_and_unsafe_refs_before_link_or_pin(
    retention, kind, auth_mode, monkeypatch
):
    monkeypatch.setenv("AUTH_PROVIDER", str(auth_mode or ""))
    sessions, _, seed, environment, payload, identity, _ = retention
    ref = await seed(
        "foreign" if kind == "foreign" else identity.workflow_id,
        restricted=kind == "restricted", quarantined=kind == "quarantined",
    )
    payload["agentResult"]["outputRefs"] = [ref]
    # Untrusted result metadata must not add an arbitrary source/child scope.
    payload["agentResult"]["metadata"] = {"workflowId": "foreign", "agentRunWorkflowId": "foreign"}
    with pytest.raises(turns.CheckpointBranchRetainedEvidenceError):
        await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
    await _assert_not_retained(sessions, ref)


@pytest.mark.parametrize("changed", ["workflow_id", "workflow_type", "namespace", "workflowId", "branchId"])
async def test_retention_rejects_forged_activity_or_graph_authority(retention, changed):
    sessions, _, seed, environment, payload, identity, _ = retention
    ref = await seed(identity.workflow_id)
    payload["agentResult"]["outputRefs"] = [ref]
    if changed in {"workflowId", "branchId"}:
        payload[changed] = "foreign"
    else:
        environment.info = dataclasses.replace(environment.info, **{changed: "foreign"})
    with pytest.raises(ValueError, match="not found|does not match launch claim"):
        await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
    await _assert_not_retained(sessions, ref)


async def test_rejection_evidence_uses_same_recorded_scope_and_keeps_producer(retention):
    sessions, artifacts, _, environment, payload, _, producer = retention
    payload["terminalPayloadDigest"] = "sha256:" + "2" * 64
    result = await environment.run(turns.persist_checkpoint_branch_turn_terminal_rejection, payload)
    assert await environment.run(turns.persist_checkpoint_branch_turn_terminal_rejection, payload) == result
    async with sessions() as session:
        for key in ("agentResultRef", "diagnosticsRef"):
            artifact, _, pinned, _ = await artifacts(session).get_metadata(
                artifact_id=result[key].removeprefix("artifact://"),
                access=access_for_test(
                    "service:checkpoint-branch-turn",
                    admitted_principal="workflow:source",
                ),
            )
            assert pinned
            assert artifact.created_by_principal == producer


async def test_rejection_refuses_foreign_activity_before_artifact_creation(retention):
    sessions, _, _, environment, payload, _, _ = retention
    payload["terminalPayloadDigest"] = "sha256:" + "2" * 64
    environment.info = dataclasses.replace(environment.info, workflow_id="foreign")
    with pytest.raises(ValueError, match="does not match launch claim"):
        await environment.run(turns.persist_checkpoint_branch_turn_terminal_rejection, payload)
    async with sessions() as session:
        assert not (await session.scalars(select(TemporalArtifact))).all()


async def test_existing_foreign_turn_cannot_borrow_the_current_activity(retention):
    sessions, _, seed, environment, payload, _, _ = retention
    foreign = build_branch_turn_execution_identity(
        branch_id="other-branch", branch_turn_id="other-turn", logical_step_id="step", ordinal=1
    )
    async with sessions() as session:
        session.add(TemporalExecutionCanonicalRecord(
            workflow_id="other-source", run_id="other-run", namespace="default",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW, entry="api",
        ))
        await session.commit()
        service = CheckpointBranchService(session)
        await service.create_branch_graph({
            "branchId": "other-branch", "branchTurnId": "other-turn", "label": "Other branch",
            "source": {
                "workflowId": "other-source", "runId": "other-run",
                "logicalStepId": "step", "sourceExecutionOrdinal": 1,
                "checkpointBoundary": "after_execution",
                "checkpointRef": "artifact://other/checkpoint",
            },
            "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
            "runtimeContextPolicy": "fresh_agent_run",
            "instructionRef": "artifact://other/instructions",
            "instructionDigest": "sha256:" + "1" * 64,
            "idempotencyKey": "other-create-turn",
        })
        await service.claim_turn_execution(
            workflow_id="other-source", branch_id="other-branch", branch_turn_id="other-turn",
            context_bundle_ref="artifact://other/context",
            step_execution_manifest_ref="artifact://other/manifest",
            diagnostics_ref="artifact://other/diagnostics",
            agent_request_ref="artifact://other/request",
            launch_idempotency_key=build_branch_turn_launch_idempotency_key(
                workflow_id="other-source", branch_id="other-branch", branch_turn_id="other-turn"
            ),
            created_step_execution_id=foreign.step_execution_id,
            runtime_agent_run_id=foreign.agent_run_workflow_id,
            execution_workflow_id=foreign.workflow_id,
        )
        await session.commit()
    ref = await seed("other-source")
    payload.update(workflowId="other-source", branchId="other-branch", branchTurnId="other-turn")
    payload["agentResult"]["outputRefs"] = [ref]
    with pytest.raises(ValueError, match="does not match launch claim"):
        await environment.run(turns.persist_checkpoint_branch_turn_terminal, payload)
    await _assert_not_retained(sessions, ref)


@pytest.mark.parametrize("missing", ["workflow_id", "workflow_type", "namespace"])
@pytest.mark.parametrize("rejection", [False, True], ids=["terminal", "rejection"])
async def test_retention_requires_actual_activity_identity_before_effects(
    retention, missing, rejection
):
    sessions, _, _, environment, payload, _, _ = retention
    environment.info = dataclasses.replace(environment.info, **{missing: None})
    payload["terminalPayloadDigest"] = "sha256:" + "2" * 64
    persist = (
        turns.persist_checkpoint_branch_turn_terminal_rejection
        if rejection
        else turns.persist_checkpoint_branch_turn_terminal
    )
    with pytest.raises(ValueError, match="does not match launch claim"):
        await environment.run(persist, payload)
    async with sessions() as session:
        assert not (await session.scalars(select(TemporalArtifact))).all()
