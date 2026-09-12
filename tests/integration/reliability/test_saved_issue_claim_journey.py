"""Default saved preset across SQL, GitHub HTTP, artifacts and worker replacement."""

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db import models
from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import (
    ToolRegistrySnapshot,
    compute_registry_digest,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSkillActivities,
    _bind_activity_handler,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.integration.reliability.test_release_routing_journey import connect
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class SavedClaimJourney:
    def __init__(self):
        self.claimed = None
        self.resume_requested = False

    @workflow.query
    def claim(self):
        return self.claimed

    @workflow.signal
    def resume(self):
        self.resume_requested = True

    @workflow.run
    async def run(self, payload: dict):
        identity = workflow.info()
        context = {
            "namespace": identity.namespace,
            "workflow_id": identity.workflow_id,
            "run_id": identity.run_id,
        }

        async def execute(step, previous=None):
            tool = step["tool"]
            result = await workflow.execute_activity(
                "mm.tool.execute",
                {
                    "registry_snapshot_ref": payload["registry"].removeprefix(
                        "artifact:"
                    ),
                    "principal": payload["principal"],
                    "invocation_payload": {
                        "id": step["id"],
                        "tool": {"type": "skill", "name": tool["id"]},
                        "inputs": {
                            **tool["inputs"],
                            **({"previousOutputs": previous} if previous else {}),
                        },
                    },
                    "context": {**context, "node_id": step["id"]},
                    "idempotency_key": identity.workflow_id + ":" + step["id"],
                },
                task_queue=payload["activityQueue"],
                start_to_close_timeout=timedelta(seconds=30),
            )
            if result["status"] != "COMPLETED":
                raise ApplicationError(json.dumps(result), non_retryable=True)
            return result["outputs"]

        trusted = MoonMindRunWorkflow()
        self.claimed = await execute(payload["steps"][0])
        trusted._record_trusted_issue_context(self.claimed)
        await workflow.wait_condition(lambda: self.resume_requested)
        # Provider assessment is a controlled input, persisted through the real
        # artifact service; it is not evidence about a model's judgment.
        previous = trusted._merge_trusted_issue_context(
            {"assessmentArtifactRef": payload["assessment"].removeprefix("artifact:")}
        )
        for step in payload["steps"][2:4]:
            output = await execute(step, previous)
            trusted._record_assessment_context(output)
            previous = trusted._merge_trusted_issue_context(output)
        return previous


@pytest.mark.parametrize("journey", ["postgres"], indirect=True)
@pytest.mark.parametrize("inputs", [{}, {"repository": "", "issue_search": ""}])
async def test_saved_default_claim_survives_lost_ack_and_worker_replacement(
    journey, inputs, tmp_path, monkeypatch
):
    state, service, _claims = journey
    state["lost_ack"] = True
    # Only credential acquisition is replaced. GitHub requests, pagination,
    # mutations and acknowledgment loss cross a real HTTP socket.
    monkeypatch.setattr(
        GitHubService, "resolve_github_token", service.resolve_github_token
    )

    async def readiness(_self, **kwargs):
        return await service.check_issue_label_readiness(**kwargs)

    monkeypatch.setattr(GitHubService, "check_issue_label_readiness", readiness)
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "blobs")),
    )
    required = {
        models.Preset.__table__,
        models.PresetFavorite.__table__,
        models.PresetRecent.__table__,
        models.TemporalArtifact.__table__,
        models.TemporalArtifactLink.__table__,
        models.TemporalArtifactPin.__table__,
        models.TemporalArtifactUseClaim.__table__,
        models.TemporalArtifactDeletionIntent.__table__,
    }
    pending = list(required)
    while pending:
        for foreign_key in pending.pop().foreign_keys:
            if foreign_key.column.table not in required:
                required.add(foreign_key.column.table)
                pending.append(foreign_key.column.table)
    async with isolated_postgres(
        [table for table in Base.metadata.sorted_tables if table in required]
    ) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            seed = Path(__file__).resolve().parents[3] / "api_service/data/presets"
            await catalog.sync_seed_templates(seed_dir=seed)
            expanded = await catalog.expand_template(
                slug="github-issue-search-and-implement",
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"repository": "example/repo"},
            )
        principal = "service:omnigent-generic-host"
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="saved-preset",
            idempotencyKey="registry",
        )
        gateway = TemporalOmnigentArtifactGateway(sessions, principal=principal)
        definitions = tuple(
            parse_tool_definition(_default_registry_skill_payload(name=name))
            for name in (
                "github.load_issue_preset_brief",
                "github.check_issue_blockers",
                "github.update_issue_status",
            )
        )
        registry = ToolRegistrySnapshot(
            compute_registry_digest(skills=definitions), "", definitions
        )
        registry_ref = await gateway.write_json(
            request=request,
            name="registry",
            payload=registry.to_payload(),
            link_type="input",
        )
        assessment = await gateway.write_json(
            request=request,
            name="assessment",
            payload={"verdict": "NOT_IMPLEMENTED"},
            link_type="evidence",
        )
        client = await connect()
        # The claim store resolves ancestry through its production client.
        # Bind it to this journey's isolated server, including on hosted CI
        # where the deployment-only hostname "temporal" does not resolve.
        monkeypatch.setattr(
            settings.temporal, "address", os.environ["MOONMIND_TEST_TEMPORAL_ADDRESS"]
        )
        queue = "saved-preset-" + uuid4().hex

        def bound(session):
            dispatcher = ToolActivityDispatcher()
            register_story_output_tool_handlers(dispatcher)
            instance = TemporalSkillActivities(
                dispatcher=dispatcher,
                artifact_service=TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ),
            )
            return _bind_activity_handler(
                instance,
                func=TemporalSkillActivities.mm_tool_execute,
                activity_type="mm.tool.execute",
            )

        async with Worker(
            client,
            task_queue=queue,
            workflows=[SavedClaimJourney],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            async with sessions() as first_session, Worker(
                client, task_queue=queue + "-tools", activities=[bound(first_session)]
            ):
                handle = await client.start_workflow(
                    SavedClaimJourney.run,
                    {
                        "steps": expanded["steps"],
                        "registry": registry_ref,
                        "assessment": assessment,
                        "principal": principal,
                        "activityQueue": queue + "-tools",
                    },
                    id=queue,
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=90),
                )
                for _ in range(120):
                    claimed = await handle.query(SavedClaimJourney.claim)
                    if claimed:
                        break
                    if (await handle.describe()).status.name == "FAILED":
                        await handle.result()
                    await asyncio.sleep(0.1)
                assert claimed and claimed["attemptId"]
                assert state["posts"] == 1
            # No in-memory tool, artifact session or dispatcher survives.
            async with sessions() as replacement_session, Worker(
                client,
                task_queue=queue + "-tools",
                activities=[bound(replacement_session)],
            ):
                await handle.signal(SavedClaimJourney.resume)
                result = await handle.result()
            assert result["attemptId"] == claimed["attemptId"]
            assert result["issue"]["number"] == 3970
            assert state["posts"] == 1
            assert state["labels"] == ["status: in-progress"]
            brief = json.loads(await gateway.read_text(claimed["briefArtifactRef"]))
            assert brief["attemptId"] == claimed["attemptId"]
