"""Scheduled source -> public continuation -> recorded plan -> readable evidence.

The scheduler and create service use real Temporal; source/relationship/artifact
owners use PostgreSQL. No provider task or repository completion is simulated.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from temporalio.testing import ActivityEnvironment
from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest

from api_service.api.routers import executions as ex
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalArtifactLink,
)
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activity_runtime import (
    TemporalPlanActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactActivities,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.integration.reliability.test_resolver_verification_capability_journey import (
    resolver_test_client,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]
REPLAY = json.loads(
    (
        Path(__file__).with_name("replays") / "scheduled-recovery" / "manifest.json"
    ).read_text()
)


@pytest.mark.parametrize("authored", [False, True])
@pytest.mark.parametrize("lost_ack", [False, True])
async def test_schedule_continuation_preserves_intent_and_materializes_evidence(
    tmp_path, monkeypatch, authored, lost_ack
):
    client = await resolver_test_client()
    from temporalio.service import RPCError, RPCStatusCode

    for name in ("mm_target_runtime", "mm_target_skill", "mm_title", "mm_updated_at"):
        try:
            await client.operator_service.add_search_attributes(
                AddSearchAttributesRequest(
                    namespace=client.namespace,
                    search_attributes={
                        name: (
                            IndexedValueType.INDEXED_VALUE_TYPE_DATETIME
                            if name == "mm_updated_at"
                            else IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST
                        )
                    },
                )
            )
        except RPCError as exc:
            if exc.status != RPCStatusCode.ALREADY_EXISTS:
                raise
    owner = str(uuid4())
    user = SimpleNamespace(id=owner)
    queue = "continuation-qualification-" + uuid4().hex
    adapter = TemporalClientAdapter(client)
    source_id = "mm:" + uuid4().hex
    schedule_id = "recovery-" + uuid4().hex
    destination = None
    schedule = None
    async with isolated_postgres([]) as sessions:
        async with sessions() as session:
            connection = await session.connection()
            await connection.run_sync(Base.metadata.create_all)
            await session.commit()
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            monkeypatch.setattr(
                ex, "get_temporal_artifact_service", lambda _: artifacts
            )

            async def publish(body):
                artifact, _ = await artifacts.create(
                    principal=owner, content_type="application/json"
                )
                await artifacts.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal=owner,
                    payload=json.dumps(body).encode(),
                    content_type="application/json",
                )
                return artifact.artifact_id

            original = REPLAY["originalInput"]
            old_input = await publish(original)
            evidence_body = {"remainingWork": ["verify saved candidate"]}
            evidence_ref = await publish(evidence_body)
            await session.commit()
            initial = {
                **original,
                "targetRuntime": "omnigent",
                "model": "opencode-go/exact-model",
                "effort": "xhigh",
            }
            start = {
                "workflow_type": "MoonMind.UserWorkflow",
                "owner_user_id": owner,
                "initial_parameters": initial,
                "input_artifact_ref": old_input,
            }
            try:
                schedule = await client.create_schedule(
                    schedule_id,
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            "MoonMind.UserWorkflow",
                            start,
                            id=source_id,
                            task_queue=queue,
                            typed_search_attributes=TypedSearchAttributes(
                                [
                                    SearchAttributePair(
                                        SearchAttributeKey.for_keyword("mm_owner_id"),
                                        owner,
                                    ),
                                    SearchAttributePair(
                                        SearchAttributeKey.for_keyword("mm_owner_type"),
                                        "user",
                                    ),
                                ]
                            ),
                        ),
                        spec=ScheduleSpec(),
                        state=ScheduleState(paused=True),
                    ),
                )
                await schedule.trigger()
                for _ in range(100):
                    actions = (await schedule.describe()).info.recent_actions
                    if actions:
                        break
                    await asyncio.sleep(0.05)
                assert actions
                source_id = actions[-1].action.workflow_id
                handle = client.get_workflow_handle(source_id)
                await handle.terminate(
                    "Qualification: terminal scheduled source without an API source row"
                )
                source_run = (await handle.describe()).run_id
                service = TemporalExecutionService(session, client_adapter=adapter)
                assert (
                    await session.get(TemporalExecutionCanonicalRecord, source_id)
                    is None
                )
                # Source-less scheduled recovery reaches the ordinary evidence
                # gate; no source-row backfill or fabricated checkpoint grants
                # eligibility to a run with no saved checkpoint.
                with pytest.raises(HTTPException) as unavailable:
                    await ex.recover_execution_from_failed_step(
                        source_id,
                        ex.RecoverFromFailedStepRequest(idempotencyKey="failed-step"),
                        service, session, user, None,
                    )
                assert unavailable.value.status_code == 409
                assert unavailable.value.detail["reason"] == "recovery_manifest_missing"
                create = service.create_execution
                creates = []

                async def create_with_lost_ack(**kwargs):
                    creates.append(kwargs)
                    record = await create(**kwargs)
                    if lost_ack and len(creates) == 1:
                        raise TemporalExecutionValidationError(
                            "lost create acknowledgement"
                        )
                    return record

                monkeypatch.setattr(service, "create_execution", create_with_lost_ack)

                async def captured(**kwargs):
                    assert kwargs == {"workflow_id": source_id, "run_id": source_run}
                    # External capture discovery is the service seam; artifact
                    # ownership, copying, linking, and delivery below are real.
                    return ex._SourceCapturedEvidence(
                        True,
                        {"finalSnapshotRef": evidence_ref},
                        [],
                        None,
                        evidence_ref,
                        None,
                        "saved candidate",
                        None,
                        None,
                        None,
                    )

                monkeypatch.setattr(ex, "_resolve_source_captured_evidence", captured)
                steps = REPLAY["continuationSteps"]
                payload = ex.ContinueInNewWorkflowRequest(
                    idempotencyKey="same-request",
                    selectedSourceArtifactRefs=[evidence_ref],
                    **(
                        {
                            "instructions": "Bounded continuation",
                            "initialParameters": {
                                "workflow": {
                                    "instructions": "Bounded continuation",
                                    "runtime": {"mode": "omnigent"},
                                    "steps": steps,
                                }
                            },
                        }
                        if authored
                        else {}
                    ),
                )
                if lost_ack:
                    with pytest.raises(
                        HTTPException, match="lost create acknowledgement"
                    ):
                        await ex.continue_in_new_workflow(
                            source_id, payload, service, session, user, None
                        )
                result = await ex.continue_in_new_workflow(
                    source_id, payload, service, session, user, None
                )
                destination = result.destination_workflow_id
                duplicate = await ex.continue_in_new_workflow(
                    source_id, payload, service, session, user, None
                )
                assert (
                    duplicate.destination_workflow_id == destination
                    and not duplicate.created
                )
                assert (
                    await session.get(TemporalExecutionCanonicalRecord, source_id)
                    is None
                )
                recorded = await adapter.read_workflow_start_input(
                    destination,
                    run_id=(
                        await client.get_workflow_handle(destination).describe()
                    ).run_id,
                )
                assert recorded["input_artifact_ref"] is None
                params = recorded["initial_parameters"]
                assert (
                    params["model"] == initial["model"] and params["effort"] == "xhigh"
                )
                assert params["continuationSource"]["sourceRunId"] == source_run
                assert "task" not in params
                expected_instruction = (
                    "Bounded continuation" if authored else "Retained source intent"
                )
                assert params["workflow"]["instructions"] == expected_instruction

                planner = TemporalPlanActivities(
                    artifact_service=artifacts, planner=_build_runtime_planner()
                )
                generated = await planner.plan_generate(
                    principal=owner,
                    inputs_ref=recorded["input_artifact_ref"],
                    parameters=params,
                )
                _, raw_plan = await artifacts.read(
                    artifact_id=generated.plan_ref.artifact_id, principal=owner
                )
                plan = json.loads(raw_plan)
                assert len(plan["nodes"]) == (2 if authored else 1)
                node = plan["nodes"][0]
                assert (
                    "Repair only the saved candidate"
                    if authored
                    else expected_instruction
                ) in json.dumps(node)
                run = (await client.get_workflow_handle(destination).describe()).run_id
                # Lose API-side linking, as when Temporal dispatch wins the race
                # or the submitting process dies after start acknowledgement.
                await session.execute(
                    delete(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id == destination,
                        TemporalArtifactLink.link_type == "input.attachment",
                    )
                )
                await session.commit()
                catalog = build_default_activity_catalog()
                selected = TemporalActivityCatalog(
                    activities=tuple(
                        a
                        for a in catalog.activities
                        if a.activity_type == "artifact.link"
                    ),
                    fleets=catalog.fleets,
                )
                (binding,) = build_activity_bindings(
                    selected, artifact_activities=TemporalArtifactActivities(artifacts)
                )

                async def dispatch(name, payload, **kwargs):
                    assert name == "artifact.link"
                    return await ActivityEnvironment().run(
                        binding.handler, json.loads(json.dumps(payload))
                    )

                with patch(
                    "moonmind.workflows.temporal.workflows.run.workflow.info",
                    return_value=SimpleNamespace(
                        workflow_id=destination, run_id=run, namespace=client.namespace
                    ),
                ), patch(
                    "moonmind.workflows.temporal.workflows.run.workflow.patched",
                    return_value=True,
                ):
                    workflow = MoonMindRunWorkflow()
                    workflow._owner_id = owner
                    with patch(
                        "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                        side_effect=dispatch,
                    ):
                        await workflow._run_planning_stage(
                            parameters=params,
                            input_ref=None,
                            plan_ref=generated.plan_ref.artifact_id,
                        )
                    request = workflow._build_agent_execution_request(
                        node_inputs=node["inputs"],
                        node_id=node["id"],
                        tool_name="omnigent",
                        workflow_parameters=params,
                    )
                request = AgentExecutionRequest.model_validate_json(
                    request.model_dump_json(by_alias=True)
                )
                copied = params["workflow"]["inputAttachments"][0]["artifactId"]
                assert "artifact://" + copied in request.input_refs
                # New hosts receive actual evidence bytes; existing candidate
                # files remain in place when input admission repeats.
                workspace_id = hashlib.sha256(
                    f"{destination}:{request.step_execution.step_execution_id}".encode()
                ).hexdigest()[:24]
                root = tmp_path / "workspaces"
                workspace = root / "temporal_sandbox" / workspace_id / "repo"
                (workspace / ".git" / "info").mkdir(parents=True)
                (workspace / "candidate.txt").write_text("saved implementation")
                request = request.model_copy(
                    update={
                        "workspace_spec": {
                            "workspaceLocator": {
                                "kind": "sandbox",
                                "workspaceId": workspace_id,
                                "relativePath": "repo",
                            },
                            "repository": "MoonLadderStudios/MoonMind",
                        }
                    }
                )

                async def no_clone(*args, **kwargs):
                    raise AssertionError("saved candidate must not be replaced")

                materializer = OmnigentWorkspaceMaterializer(
                    command_runner=no_clone,
                    workspace_root=root,
                    artifact_service=artifacts,
                )
                for _ in range(2):
                    await materializer.materialize(
                        request, runtime_uid=os.getuid(), runtime_gid=os.getgid()
                    )
                assert any(
                    p.is_file() and p.read_bytes() == json.dumps(evidence_body).encode()
                    for p in workspace.rglob("*")
                )
                assert (
                    workspace / "candidate.txt"
                ).read_text() == "saved implementation"
            finally:
                if schedule:
                    await schedule.delete()
                if destination:
                    await client.get_workflow_handle(destination).terminate(
                        "Qualification complete"
                    )
