"""Acceptance survives the production publisher, Temporal serialization, and gate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog, build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities, build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.integration.reliability.test_release_routing_journey import connect

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@workflow.defn
class AcceptanceProjectionJourney:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        published = await workflow.execute_activity(
            "agent_runtime.publish_artifacts", payload,
            start_to_close_timeout=timedelta(minutes=1),
        )
        result = AgentRunResult.model_validate(published)
        parent = MoonMindRunWorkflow()
        parent._assessment_context = {"issueRef": "example/repo#1"}
        return parent._moonspec_verify_gate_result(result.metadata).to_payload()


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("near_limit", [False, True])
async def test_published_acceptance_remains_valid_and_replayable(tmp_path, monkeypatch, expired, near_limit):
    fixture = Path(__file__).resolve().parents[2] / "fixtures/reliability/acceptance-evidence-projection.json"
    payload = json.loads(fixture.read_text())["gatePayload"]
    acceptance = payload["validatedRefs"]["acceptance"]
    acceptance["evidence"][0]["evidenceRefs"] = ["large evidence " * 4000]
    if expired:
        acceptance["freshness"]["validUntil"] = "2020-01-01T00:00:00Z"
    workspace = tmp_path / "repo"
    report = workspace / "artifacts/verify.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps(payload))
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(ManagedRunRecord(
        runId="verify-run", agentId="codex_cli", runtimeId="codex_cli", status="completed",
        startedAt=datetime.now(timezone.utc), workspacePath=str(workspace),
    ))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service, run_store=run_store)
            monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock(return_value={"status": "skipped"}))
            catalog = build_default_activity_catalog()
            selected = TemporalActivityCatalog(
                activities=tuple(a for a in catalog.activities if a.activity_type == "agent_runtime.publish_artifacts"),
                fleets=catalog.fleets,
            )
            binding, = build_activity_bindings(selected, agent_runtime_activities=activities)
            connected = await connect()
            client = Client(
                connected.service_client, namespace=connected.namespace,
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            )
            queue = "acceptance-projection-" + uuid4().hex
            async with Worker(
                client, task_queue=queue, workflows=[AcceptanceProjectionJourney],
                activities=[binding.handler], workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                metadata = {"agentRunId": "verify-run", "verify_artifact_path": "artifacts/verify.json", "acceptanceContract": "acceptance/v1"}
                if near_limit:
                    metadata.update(diagnostic1="p" * 7800, diagnostic2="q" * 7600)
                handle = await client.start_workflow(
                    AcceptanceProjectionJourney.run,
                    {"metadata": metadata},
                    id=queue, task_queue=queue, execution_timeout=timedelta(minutes=2),
                )
                result = await handle.result()
                history = await handle.fetch_history()
            assert result["verdict"] == ("NO_DETERMINATION" if expired else "FULLY_IMPLEMENTED")
            assert result["invalid"] is expired
            await Replayer(
                workflows=[AcceptanceProjectionJourney], workflow_runner=UnsandboxedWorkflowRunner(),
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
            ).replay_workflow(history)
            if not expired:
                evidence = result["validatedRefs"]["acceptance"]
                artifact_ref = evidence["evidence"][0]["evidenceRefs"][0]
                _, path = await service.read_path(artifact_id=artifact_ref, principal="system:agent_runtime")
                assert json.loads(path.read_text())["validatedRefs"]["acceptance"] == acceptance
    finally:
        await engine.dispose()
