"""Artifact consumers cannot manufacture authority from request strings."""

# Imported pytest fixtures are intentionally reused as test argument names.
# ruff: noqa: F811

from types import SimpleNamespace

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError

from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from tests.unit.omnigent.test_admitted_input_authority import (  # noqa: F401
    _put,
    artifacts,
    auth_mode,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_owner",
    ["workflow:protected-run", "service:temporal-artifacts", "workflow:unrelated-run"],
)
@pytest.mark.parametrize(
    "claimed_scope",
    ["operator", "00000000-0000-0000-0000-000000000000", "workflow:protected-run"],
)
async def test_registered_read_cannot_select_authority_from_payload(
    artifacts, monkeypatch, claimed_scope, artifact_owner  # noqa: F811
):
    artifact_id = await _put(
        artifacts,
        b"protected workflow input",
        owner=artifact_owner,
        workflow_id="protected-run",
        link_type="input.original_task",
    )
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default", workflow_id="unrelated-run", workflow_run_id="run-1"
        ),
    )
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            a for a in catalog.activities if a.activity_type == "artifact.read"
        ),
        fleets=catalog.fleets,
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        (binding,) = build_activity_bindings(
            selected, artifact_activities=TemporalArtifactActivities(service)
        )
        with pytest.raises(ApplicationError) as denied:
            await binding.handler(
                {"artifact_ref": artifact_id, "principal": claimed_scope}
            )
        assert denied.value.non_retryable
        assert denied.value.type == "TemporalArtifactAuthorizationError"


@pytest.mark.asyncio
async def test_registered_link_cannot_mint_authority_from_service_payload(
    artifacts, monkeypatch  # noqa: F811
):
    artifact_id = await _put(
        artifacts,
        b"private",
        owner="workflow:protected-run",
        workflow_id="protected-run",
        link_type="input.original_task",
    )
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="unrelated-run",
            workflow_run_id="run-1",
        ),
    )
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            a for a in catalog.activities if a.activity_type == "artifact.link"
        ),
        fleets=catalog.fleets,
    )
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository)
        (binding,) = build_activity_bindings(
            selected, artifact_activities=TemporalArtifactActivities(service)
        )
        before = await repository.list_links(artifact_id)
        with pytest.raises(ApplicationError) as denied:
            await binding.handler(
                {
                    "artifact_id": artifact_id,
                    "principal": "service:forged",
                    "execution_ref": {
                        "namespace": "default",
                        "workflow_id": "unrelated-run",
                        "run_id": "run-1",
                        "link_type": "input.plan",
                    },
                }
            )
        assert denied.value.type == "TemporalArtifactAuthorizationError"
        assert [link.id for link in await repository.list_links(artifact_id)] == [
            link.id for link in before
        ]


@pytest.mark.asyncio
async def test_explicit_admission_delegates_only_selected_raw_input(
    artifacts,
):  # noqa: F811
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.security.operator_admission import OperatorAdmission

    owner = "00000000-0000-0000-0000-000000004003"
    artifact_id = await _put(
        artifacts, b"retained", owner=owner, workflow_id=None, link_type="input.saved"
    )
    unrelated_id = await _put(
        artifacts, b"unrelated", owner=owner, workflow_id=None, link_type="input.saved"
    )
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository)
        artifact = await repository.get_artifact(artifact_id)
        artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        artifact.operator_recovery_granted = True
        await session.commit()
        identity = (
            artifact.artifact_id,
            artifact.created_by_principal,
            artifact.sha256,
        )
        reader = ArtifactAccessContext.for_execution("publication")
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=artifact_id, access=reader)
        admitted = ArtifactAccessContext.for_operator(
            OperatorAdmission(via="test"), recovery=True
        )
        await service.delegate_read_access(
            artifact_ids=[artifact_id], workflow_id="publication", access=admitted
        )
        assert (await service.read(artifact_id=artifact_id, access=reader))[
            1
        ] == b"retained"
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=unrelated_id, access=reader)
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=artifact_id,
                access=ArtifactAccessContext.for_execution("other"),
            )
        assert (
            artifact.artifact_id,
            artifact.created_by_principal,
            artifact.sha256,
        ) == identity


@pytest.mark.asyncio
@pytest.mark.parametrize("production_proxy", [False, True])
async def test_new_restricted_activity_artifact_keeps_real_producer_access(
    artifacts, monkeypatch, production_proxy  # noqa: F811
):
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(worker_runtime, "get_async_session_context", artifacts)

    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_namespace="default",
            workflow_id="producer",
            workflow_run_id="producer-run",
        ),
    )
    catalog = build_default_activity_catalog()
    selected = TemporalActivityCatalog(
        activities=tuple(
            a
            for a in catalog.activities
            if a.activity_type
            in {
                "artifact.create",
                "artifact.write_complete",
                "artifact.read",
            }
        ),
        fleets=catalog.fleets,
    )
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository)
        handlers = {
            b.activity_type: b.handler
            for b in build_activity_bindings(
                selected,
                artifact_activities=TemporalArtifactActivities(
                    worker_runtime._ArtifactServiceProxy()
                    if production_proxy
                    else service
                ),
            )
        }
        ref, _upload = await handlers["artifact.create"](
            {
                "principal": "workflow:historical-label",
                "content_type": "text/plain",
                "redaction_level": TemporalArtifactRedactionLevel.RESTRICTED,
            }
        )
        await handlers["artifact.write_complete"](
            {
                "artifact_id": ref.artifact_id,
                "principal": "workflow:historical-label",
                "payload": b"producer bytes",
                "content_type": "text/plain",
            }
        )
        assert (
            await handlers["artifact.read"]({"artifact_ref": ref.artifact_id})
            == b"producer bytes"
        )
        artifact = await repository.get_artifact(ref.artifact_id)
        assert artifact.created_by_principal == "workflow:historical-label"
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=ref.artifact_id,
                access=ArtifactAccessContext.for_execution("historical-label"),
            )


@pytest.mark.asyncio
async def test_delegation_does_not_survive_quarantine_or_transfer_operator_role(
    artifacts,
):  # noqa: F811
    from moonmind.security.operator_admission import OperatorAdmission

    artifact_id = await _put(
        artifacts,
        b"scoped",
        owner="operator",
        workflow_id=None,
        link_type="input.saved",
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        admitted = ArtifactAccessContext.for_operator(OperatorAdmission(via="test"))
        reader = ArtifactAccessContext.for_execution("selected", principal="operator")
        assert not reader.is_operator and not reader.operator_recovery
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=artifact_id, access=reader)
        await service.delegate_read_access(
            artifact_ids=[artifact_id], workflow_id="selected", access=admitted
        )
        assert (await service.read(artifact_id=artifact_id, access=reader))[
            1
        ] == b"scoped"
        artifact = await service._repository.get_artifact(artifact_id)
        artifact.metadata_json = {"quarantine": True}
        await session.commit()
        with pytest.raises(TemporalArtifactStateError, match="SAVED_WORK_QUARANTINED"):
            await service.read(artifact_id=artifact_id, access=reader)
        with pytest.raises(TemporalArtifactStateError, match="SAVED_WORK_QUARANTINED"):
            await service.delegate_read_access(
                artifact_ids=[artifact_id], workflow_id="next", access=admitted
            )
        assert not any(
            link.workflow_id == "next"
            for link in await service._repository.list_links(artifact_id)
        )


@pytest.mark.parametrize("invalid", [None, "operator", {"is_operator": True}])
def test_read_context_cannot_be_decoded_from_payload(invalid):
    with pytest.raises(TemporalArtifactAuthorizationError):
        ArtifactAccessContext.require(invalid)


@pytest.mark.asyncio
async def test_signed_fanout_admission_is_parent_scoped_and_never_operator(artifacts):
    from fastapi import HTTPException
    from starlette.requests import Request

    from api_service.api.routers.executions import _execution_request_artifact_access
    from api_service.auth_providers import get_artifact_access
    from moonmind.config.settings import settings
    from moonmind.security.execution_fanout_capabilities import (
        mint_execution_fanout_capability,
    )

    artifact_id = await _put(
        artifacts,
        b"parent input",
        owner="service:planner",
        workflow_id="parent",
        link_type="input.original_task",
    )
    unrelated = await _put(
        artifacts,
        b"foreign",
        owner="service:planner",
        workflow_id="other",
        link_type="input.original_task",
    )
    token = mint_execution_fanout_capability(
        secret=str(settings.security.JWT_SECRET_KEY),
        parent_workflow_id="parent",
        agent_run_id="run",
        step_id="step",
        session_id="session",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=300,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/executions",
            "headers": [
                (b"x-moonmind-execution-fanout", b"v1"),
                (b"authorization", ("Bearer " + token).encode()),
            ],
        }
    )
    access = await _execution_request_artifact_access(request, user=None)
    assert access.execution_principal == "workflow:parent"
    assert not access.is_operator and not access.operator_recovery
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        assert (await service.read(artifact_id=artifact_id, access=access))[
            1
        ] == b"parent input"
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=unrelated, access=access)
    # The existing fan-out entrypoints do not turn into general operator APIs.
    with pytest.raises(HTTPException) as denied:
        await get_artifact_access(request, admission=None)
    assert denied.value.status_code == 401


@pytest.mark.asyncio
async def test_oauth_checkpoint_callback_retains_typed_execution_context(
    artifacts, tmp_path
):
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from tests.unit.omnigent.test_admitted_input_authority import _archive

    payload = _archive({"tracked.txt": b"restored bytes"})
    artifact_id = await _put(
        artifacts,
        payload,
        owner="system:agent_runtime",
        workflow_id="restore-wf",
        link_type="input.checkpoint",
        content_type="application/gzip",
    )
    workspace = tmp_path / "restored"
    workspace.mkdir()
    runtime = object.__new__(OmnigentOAuthHostRuntime)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        await runtime._apply_workspace_checkpoint_restore(
            workspace,
            artifact_ref=f"artifact://{artifact_id}",
            artifact_gateway=service,
            access=ArtifactAccessContext.for_execution("restore-wf"),
        )
    assert (workspace / "tracked.txt").read_bytes() == b"restored bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["mapping", "model"])
async def test_registered_read_accepts_structured_artifact_references(
    artifacts, monkeypatch, representation
):
    from moonmind.schemas.temporal_artifact_models import ArtifactRefModel

    artifact_id = await _put(
        artifacts,
        b"typed reference",
        owner="service:planner",
        workflow_id="reader",
        link_type="input.original_task",
    )
    reference = ArtifactRefModel(artifact_id=artifact_id, encryption="none")
    if representation == "mapping":
        reference = reference.model_dump()
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity, "info", lambda: SimpleNamespace(workflow_id="reader"))
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        assert (
            await TemporalArtifactActivities(service).artifact_read(
                {"artifact_ref": reference}
            )
            == b"typed reference"
        )


@pytest.mark.asyncio
async def test_gateway_requires_verified_context_for_workflow_labels(artifacts):
    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway

    artifact_id = await _put(
        artifacts,
        b"planned",
        owner="service:planner",
        workflow_id="native",
        link_type="input.plan",
    )
    with pytest.raises(TemporalArtifactAuthorizationError):
        await TemporalOmnigentArtifactGateway(
            artifacts, principal="workflow:native"
        ).read_bytes(artifact_id)
    gateway = TemporalOmnigentArtifactGateway(
        artifacts,
        principal="workflow:native",
        access=ArtifactAccessContext.for_execution("native"),
    )
    assert await gateway.read_bytes(artifact_id) == b"planned"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["agent", "session", "forged", "foreign_namespace"])
async def test_step_skill_context_uses_verified_owner_ancestry(
    artifacts, monkeypatch, kind
):
    from unittest.mock import AsyncMock

    from temporalio.api.workflowservice.v1 import (
        DescribeNamespaceResponse,
        DescribeWorkflowExecutionResponse,
    )

    from moonmind.workflows.skills.run_projection import (
        SkillProjectionError,
        step_execution_artifact_access,
    )

    owner = "other" if kind == "forged" else "owner"
    current = "session" if kind == "session" else "agent"
    info = SimpleNamespace(
        namespace="default", workflow_id=current, workflow_run_id=current + "-run"
    )

    def record(workflow_id):
        parent = "agent" if workflow_id == "session" else "owner"
        return DescribeWorkflowExecutionResponse(
            workflow_execution_info={
                "execution": {
                    "workflow_id": workflow_id,
                    "run_id": workflow_id + "-run",
                },
                "type": {
                    "name": (
                        "MoonMind.OmnigentSession"
                        if workflow_id == "session"
                        else "MoonMind.AgentRun"
                    )
                },
                "parent_execution": {"workflow_id": parent, "run_id": parent + "-run"},
                "parent_namespace_id": (
                    "foreign" if kind == "foreign_namespace" else "namespace-default"
                ),
            }
        )

    async def describe(request, **_):
        return record(request.execution.workflow_id)

    server = SimpleNamespace(
        describe_workflow_execution=AsyncMock(side_effect=describe),
        describe_namespace=AsyncMock(
            return_value=DescribeNamespaceResponse(
                namespace_info={"id": "namespace-default"}
            )
        ),
    )
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity, "info", lambda: info)
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(namespace="default", workflow_service=server),
    )
    request = SimpleNamespace(
        step_execution=SimpleNamespace(workflow_id=owner, run_id=owner + "-run"),
        metadata={},
    )
    if kind in {"forged", "foreign_namespace"}:
        with pytest.raises(SkillProjectionError):
            await step_execution_artifact_access(request)
        return
    access = await step_execution_artifact_access(request)
    assert access.execution_principal == "workflow:owner"
    assert not access.is_operator and not access.operator_recovery
    artifact_id = await _put(
        artifacts,
        b"owning workflow Skill",
        owner="service:planner",
        workflow_id="owner",
        link_type="input.skill",
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        assert (await service.read(artifact_id=artifact_id, access=access))[
            1
        ] == b"owning workflow Skill"


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_prefix", ["", "artifact:", "artifact://"])
async def test_readmission_compares_fresh_compiler_snapshots_without_creator_authority(
    artifacts, monkeypatch, ref_prefix
):
    import hashlib
    import json
    from unittest.mock import AsyncMock

    from api_service.services import omnigent_execution_plan_service as plans
    from moonmind.omnigent.harness_platform import stores
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

    body = json.dumps(
        {
            "selection": {
                "connectionId": "selected",
                "repositoryDisplay": "owner/repo",
                "operations": ["read"],
            }
        }
    ).encode()
    old_id = await _put(
        artifacts,
        body,
        owner="service:execution-plan",
        workflow_id="owner",
        link_type="input.repository",
    )
    new_id = await _put(
        artifacts,
        body,
        owner="service:execution-plan",
        workflow_id=None,
        link_type="input.repository",
    )
    digest = "sha256:" + hashlib.sha256(body).hexdigest()

    def payload(artifact_id):
        return SimpleNamespace(
            resolvedTools={
                "repositoryAccess": {
                    "source_read": {
                        "artifactRef": "artifact:" + artifact_id,
                        "snapshotRef": "repository-access-snapshot:" + digest,
                    }
                }
            }
        )

    binding = OmnigentExecutionPlanBinding(
        planRef="omnigent-execution-plan:sha256:" + "1" * 64,
        planDigest="sha256:" + "1" * 64,
        planArtifactRef="art_plan",
        taskInputSnapshotRef="art_task",
        taskInputSnapshotDigest="sha256:" + "2" * 64,
    )
    access = ArtifactAccessContext.for_execution("owner")
    compiled = plans.PersistedOmnigentExecutionPlan(
        artifact_access=access,
        envelope=SimpleNamespace(payload=payload(new_id)),
        binding=binding,
        artifact_refs=(ref_prefix + new_id,),
        resolved_skillset_ref="art_skills",
        resolved_skillset_digest="sha256:" + "3" * 64,
    )
    monkeypatch.setattr(
        stores,
        "SessionExecutionPlanStore",
        lambda _: SimpleNamespace(
            load=AsyncMock(return_value=SimpleNamespace(payload=payload(old_id)))
        ),
    )
    monkeypatch.setattr(
        plans, "frozen_execution_artifact_access", AsyncMock(return_value=access)
    )
    monkeypatch.setattr(
        plans,
        "frozen_task_input_provenance",
        AsyncMock(return_value="service:execution-plan"),
    )
    monkeypatch.setattr(
        plans, "compile_frozen_execution_plan", AsyncMock(return_value=compiled)
    )
    monkeypatch.setattr(plans, "changed_admitted_intent", lambda *_: [])
    db = SimpleNamespace(
        get=AsyncMock(return_value=object()), commit=AsyncMock(), rollback=AsyncMock()
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=new_id, access=access)

        result = await plans.readmit_frozen_execution_plan(
            db_session=db,
            artifact_service=service,
            workflow_id="owner",
            initial_parameters={
                "omnigentExecutionPlan": binding.model_dump(by_alias=True),
                "agentProfileSnapshot": {"providerProfileRef": "selected"},
            },
            subject="test",
            preserve_recorded_intent=True,
        )
        assert result["disposition"] == "admitted", result
        assert (
            await service._repository.get_artifact(new_id)
        ).created_by_principal == "service:execution-plan"
        # The temporary comparison authority never becomes the workflow's grant.
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=new_id, access=access)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["valid", "foreign_job", "forged_owner", "unselected", "raw", "quarantined"]
)
async def test_container_job_reader_keeps_exact_durable_input_admission(
    artifacts, monkeypatch, case
):
    from api_service.db.models import (
        ContainerJobRecord,
        TemporalArtifact,
        TemporalArtifactRedactionLevel,
    )
    from moonmind.schemas.container_job_models import (
        ContainerJobActivityRequest,
        OwnerIdentity,
        container_job_workflow_id,
    )
    from moonmind.workflows.temporal import worker_runtime
    from moonmind.workflows.temporal.worker_runtime import (
        _ArtifactServiceProxy,
        _container_job_artifact_reader,
    )
    from tests.unit.api.test_container_job_input_attachments import _submission

    selected = await _put(
        artifacts,
        b"selected job input",
        owner="service:exporter",
        workflow_id=None,
        link_type="input.saved",
    )
    unrelated = await _put(
        artifacts,
        b"unrelated job input",
        owner="service:exporter",
        workflow_id=None,
        link_type="input.saved",
    )
    submission = _submission({"name": "input", "artifactRef": selected})
    job_id = "container-job:" + "1" * 32
    owner = OwnerIdentity(principalType="service", principalId="owner")
    request = ContainerJobActivityRequest(
        jobId=job_id, owner=owner, ownershipToken=job_id + ":v1", request=submission
    )
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(worker_runtime, "get_async_session_context", artifacts)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=(
                "unrelated"
                if case == "foreign_job"
                else container_job_workflow_id(job_id)
            )
        ),
    )
    async with artifacts() as session:
        session.add(
            ContainerJobRecord(
                job_id=job_id,
                owner_id=owner.principal_id,
                owner_type=owner.principal_type,
                idempotency_key=submission.idempotency_key,
                source_json=submission.source.model_dump(mode="json", by_alias=True),
                request_json=submission.model_dump(mode="json", by_alias=True),
            )
        )
        if case in {"raw", "quarantined"}:
            row = await session.get(TemporalArtifact, selected)
            if case == "raw":
                row.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            else:
                row.metadata_json = {"quarantine": True}
        await session.commit()
        if case == "forged_owner":
            request.owner = OwnerIdentity(principalType="user", principalId="operator")
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        reader = _container_job_artifact_reader(_ArtifactServiceProxy())
        if case != "valid":
            with pytest.raises(
                (TemporalArtifactAuthorizationError, TemporalArtifactStateError)
            ):
                await reader(request, unrelated if case == "unselected" else selected)
        else:
            assert await reader(request, selected) == b"selected job input"
            assert (
                await service._repository.get_artifact(selected)
            ).created_by_principal == "service:exporter"
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=selected,
                    access=ArtifactAccessContext.for_execution("owner"),
                )
