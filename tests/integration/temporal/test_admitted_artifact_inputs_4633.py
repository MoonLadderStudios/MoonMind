"""#4633: actual producers, durable admission and runtime input readers."""

from __future__ import annotations

import hashlib
import json
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base, models
from api_service.services import omnigent_execution_plan_service as writer
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import (
    OmnigentArtifactError,
    TemporalOmnigentArtifactGateway,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import ResolvedSkillSet
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactAuthorizationError,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _compile_opencode_plan,
    _ready_opencode_image_pair,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def digest(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


@pytest_asyncio.fixture
async def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    monkeypatch.setattr(settings.temporal, "namespace", "input-tests")
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    monkeypatch.setattr(writer, "_try_load_real_harness_config", AsyncMock(return_value=None))
    monkeypatch.setattr(writer, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified"))
    store = LocalTemporalArtifactStore(tmp_path / "blobs")
    monkeypatch.setattr(TemporalArtifactService, "_build_store_from_settings", staticmethod(lambda: store))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inputs.db")
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", sessions)
    artifacts = _OnDemandTemporalArtifactService(sessions)
    source = tmp_path / "sources" / "selected"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: selected\ndescription: exact admitted skill\n---\nUse helpers.\n")
    (source / "helper.py").write_text("print('admitted helper')\n")
    selected = ResolvedSkillSet.model_validate({
        "snapshot_id": "selected-snapshot",
        "resolved_at": datetime.now(UTC),
        "skills": [{
            "skill_name": "selected",
            "provenance": {"source_kind": "deployment", "source_path": str(source)},
        }],
    })
    monkeypatch.setattr(writer.AgentSkillResolver, "resolve", AsyncMock(return_value=selected))

    async def compile(workflow_id="wf-inputs", snapshot=None):
        task = snapshot or {"draft": {"workflow": {"instructions": "Use only admitted inputs."}}}
        task_ref, task_digest = await writer.persist_json_artifact(
            artifact_service=artifacts, principal="operator", artifact_class="workflow.task_input_snapshot", payload=task,
        )
        return await _compile_opencode_plan(
            monkeypatch, artifacts=artifacts, plan_store=DbExecutionPlanStore(sessions),
            session_factory=sessions, launch_policy_ref="omnigent-on-demand@1",
            workflow_id=workflow_id, task_input_snapshot_ref=task_ref,
            task_input_snapshot_digest=task_digest,
        )

    def request(compiled, workflow_id="wf-inputs", run_id="run-inputs"):
        return AgentExecutionRequest(
            agentKind="external", agentId="omnigent", executionProfileRef="provider-opencode-native",
            correlationId=f"{workflow_id}:child", idempotencyKey=f"{workflow_id}:{run_id}:read",
            resolvedSkillsetRef=compiled.resolved_skillset_ref,
            omnigentExecutionPlan=compiled.binding,
            parameters={"executionPlanRef": compiled.binding.plan_ref},
            stepExecution={"workflowId": workflow_id, "runId": run_id,
                "logicalStepId": "read", "executionOrdinal": 1,
                "stepExecutionId": f"{workflow_id}:{run_id}:read:execution:1",
                "runtimeContextPolicy": "fresh_agent_run", "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True)},
        )

    try:
        yield SimpleNamespace(sessions=sessions, artifacts=artifacts, store=store,
            gateway=TemporalOmnigentArtifactGateway(sessions), compile=compile,
            request=request, source=source, selected=selected)
    finally:
        await engine.dispose()


async def test_oidc_admitted_plan_service_bundle_is_materialized(inputs, tmp_path):
    compiled = await inputs.compile()
    request = inputs.request(compiled)
    await inputs.gateway.admit_execution_plan_inputs(request=request, plan=compiled.envelope)
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace(), network="test-network", workspace_root=tmp_path / "runs")
    # Exercise the actual existing durable host reader before changing it.
    projection = await runtime._prepare_skill_projection(
        workspace_key=request.idempotency_key,
        resolved_skillset_ref=request.resolved_skillset_ref,
        artifact_gateway=inputs.gateway.for_request(request),
    )
    assert (projection / "selected" / "SKILL.md").read_bytes() == (inputs.source / "SKILL.md").read_bytes()
    assert (projection / "selected" / "helper.py").read_bytes() == (inputs.source / "helper.py").read_bytes()


async def test_unscoped_gateway_cannot_read_operator_inputs_in_disabled_mode(inputs, monkeypatch):
    compiled = await inputs.compile()
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    with pytest.raises(OmnigentArtifactError, match="admitted"):
        await inputs.gateway.read_bytes(compiled.resolved_skillset_ref)


@pytest.mark.parametrize("auth_mode", [None, "disabled"])
async def test_real_compose_default_keeps_runtime_inputs_scoped(inputs, tmp_path, monkeypatch, auth_mode):
    import subprocess
    repo_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment.pop("AUTH_PROVIDER", None)
    if auth_mode is not None:
        environment["AUTH_PROVIDER"] = auth_mode
    rendered = subprocess.run(["docker", "compose", "-p", "moonmind-test-4633",
        "-f", str(repo_root / "docker-compose.yaml"), "--project-directory", str(tmp_path),
        "config", "--format", "json"], env=environment, capture_output=True, text=True, check=True)
    configuration = json.loads(rendered.stdout)
    mode = configuration["services"]["api"]["environment"]["AUTH_PROVIDER"]
    assert mode == "disabled"
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", mode)
    compiled = await inputs.compile()
    reader, _ = await admitted_reader(inputs, compiled)
    assert json.loads(await reader.read_bytes(compiled.resolved_skillset_ref))["skills"]
    with pytest.raises(OmnigentArtifactError, match="admitted"):
        await inputs.gateway.read_bytes(compiled.resolved_skillset_ref)


async def write_input(inputs, body=b"admitted input", **kwargs):
    row, _ = await inputs.artifacts.create(principal="operator", content_type="application/octet-stream", **kwargs)
    await inputs.artifacts.write_complete(artifact_id=row.artifact_id, principal="operator", payload=body)
    return row.artifact_id


async def admitted_reader(inputs, compiled, **kwargs):
    request = inputs.request(compiled, **kwargs)
    await inputs.gateway.admit_execution_plan_inputs(request=request, plan=compiled.envelope)
    return inputs.gateway.for_request(request), request


@pytest.mark.parametrize("mode", ["accounts", "oidc", "header", "disabled"])
async def test_admitted_inputs_work_across_supported_modes_without_owner_impersonation(inputs, mode, monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", mode)
    compiled = await inputs.compile()
    reader, request = await admitted_reader(inputs, compiled)
    from moonmind.omnigent.host_services.skills import OmnigentSkillDeliveryService
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(inputs.source.parent))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    service = OmnigentSkillDeliveryService(workspace_root=inputs.source.parent,
        workspace_volume="test-workspaces", artifact_gateway=inputs.gateway)
    attachment = await service.materialize(compiled.envelope.payload.resolvedSkills,
        owner_ref=request.idempotency_key, request=request)
    projected = Path(attachment["cleanupSourceRef"]) / "runtime" / "skills_active" / "selected-snapshot" / "selected"
    assert (projected / "helper.py").read_bytes() == (inputs.source / "helper.py").read_bytes()
    other = await write_input(inputs, b"same operator, other execution")
    with pytest.raises(OmnigentArtifactError, match="not admitted"):
        await reader.read_bytes(other)


async def test_restore_and_attachment_metadata_and_streams_share_admission(inputs, tmp_path):
    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector
    restore = await write_input(inputs, b"restore bytes")
    attachment = await write_input(inputs, b"attachment bytes")
    compiled = await inputs.compile(snapshot={
        "draft": {"workflow": {"workspace": {"restoreInputRefs": [f"artifact://{restore}"]}}},
        "attachmentRefs": [{"artifactId": attachment, "digest": digest(b"attachment bytes")}],
    })
    reader, request = await admitted_reader(inputs, compiled)
    workspace = tmp_path / "projected"
    workspace.mkdir()
    projected = await WorkspaceArtifactProjector(reader).project(workspace,
        restore_refs=(f"artifact://{restore}",), attachment_refs=(f"artifact://{attachment}",),
        workflow_id=request.step_execution.workflow_id, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    assert (workspace / projected["restoreInputs"][0]["path"]).read_bytes() == b"restore bytes"
    assert (workspace / projected["attachments"][0]["path"]).read_bytes() == b"attachment bytes"
    async with inputs.sessions() as session:
        repo = TemporalArtifactRepository(session)
        for ref in (restore, attachment):
            links = await repo.list_links(ref)
            assert any(link.namespace == "input-tests" and link.workflow_id == "wf-inputs" for link in links)
            assert (await repo.get_artifact(ref)).created_by_principal == "operator"


async def test_workspace_source_digest_does_not_replace_other_input_digests(inputs):
    base_ref = await write_input(inputs, b"base archive")
    restore_ref = await write_input(inputs, b"distinct restore bytes")
    compiled = await inputs.compile(snapshot={"draft": {"workspaceSpec": {
        "workspaceSource": {"artifactRef": f"artifact://{base_ref}",
            "artifactDigest": digest(b"base archive")},
        "restoreInputRefs": [f"artifact://{restore_ref}"],
    }}})
    reader, _ = await admitted_reader(inputs, compiled)
    assert await reader.read_bytes(restore_ref) == b"distinct restore bytes"


async def test_workspace_materializer_restores_real_admitted_inputs_before_mount(inputs, tmp_path):
    import io
    import tarfile
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        body = b"saved implementation\n"
        member = tarfile.TarInfo("candidate.txt")
        member.size = len(body)
        bundle.addfile(member, io.BytesIO(body))
    checkpoint = await write_input(inputs, archive.getvalue())
    restore = await write_input(inputs, b"current restore")
    attachment = await write_input(inputs, b"current assessment")
    step_id = "wf-inputs:run-inputs:read:execution:1"
    workspace_id = hashlib.sha256(f"wf-inputs:{step_id}".encode()).hexdigest()[:24]
    spec = {"workspaceLocator": {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"},
        "repository": "MoonLadderStudios/MoonMind", "branch": "main",
        "workspaceCheckpointRestoreRef": f"artifact://{checkpoint}", "restoreInputRefs": [f"artifact://{restore}"]}
    compiled = await inputs.compile(snapshot={"draft": {"workspaceSpec": spec},
        "attachmentRefs": [{"artifactId": attachment, "digest": digest(b"current assessment")}]})
    _, request = await admitted_reader(inputs, compiled)
    request.workspace_spec = spec
    request.input_refs = [f"artifact://{attachment}"]
    root = tmp_path / "runs"
    workspace = root / "temporal_sandbox" / workspace_id / "repo"
    (workspace / ".git" / "info").mkdir(parents=True)
    (workspace / ".moonmind" / "attachments").mkdir(parents=True)
    (workspace / ".moonmind" / "attachments" / "stale").write_bytes(b"old input")

    async def fail_runner(*_args, **_kwargs):
        raise AssertionError("authoritative checkout must not be cloned")

    await OmnigentWorkspaceMaterializer(command_runner=fail_runner, workspace_root=root,
        artifact_service=inputs.artifacts).materialize(request,
            runtime_uid=os.getuid(), runtime_gid=os.getgid())
    assert (workspace / "candidate.txt").read_bytes() == b"saved implementation\n"
    for directory, ref, body in (("restore", restore, b"current restore"), ("attachments", attachment, b"current assessment")):
        path = workspace / ".moonmind" / directory / hashlib.sha256(f"artifact://{ref}".encode()).hexdigest()[:24]
        assert path.read_bytes() == body
        assert path.stat().st_mode & 0o777 == 0o400
    assert not (workspace / ".moonmind" / "attachments" / "stale").exists()


@pytest.mark.parametrize("producer", ["current", "sibling", "unknown", "forged_skill"])
async def test_workflow_prepared_attachments_require_exact_native_lineage(inputs, producer):
    compiled = await inputs.compile()
    request = inputs.request(compiled)
    link = None if producer == "unknown" else ExecutionRef(
        namespace="input-tests", workflow_id="sibling" if producer == "sibling" else "wf-inputs",
        run_id="run-inputs", link_type="output.context", created_by_activity_type="workflow.prepare_inputs")
    ref = await write_input(inputs, b"prepared context", link=link)
    request.input_refs = [f"artifact://{ref}"]
    request.step_execution.prepared_input_refs = [ref]
    if producer == "forged_skill":
        request.resolved_skillset_ref = ref
    if producer == "current":
        await inputs.gateway.admit_execution_plan_inputs(request=request, plan=compiled.envelope)
        assert await inputs.gateway.for_request(request).read_bytes(ref) == b"prepared context"
    else:
        with pytest.raises(OmnigentArtifactError, match="Skill" if producer == "forged_skill" else "lineage"):
            await inputs.gateway.admit_execution_plan_inputs(request=request, plan=compiled.envelope)
        async with inputs.sessions() as session:
            assert not any(link.created_by_activity_type == "omnigent.admit_execution_inputs"
                for link in await TemporalArtifactRepository(session).list_links(ref))


async def test_concurrent_readers_and_restart_keep_exact_durable_scope(inputs):
    first = await inputs.compile("first")
    second = await inputs.compile("second")
    reader_a, request_a = await admitted_reader(inputs, first, workflow_id="first")
    reader_b, request_b = await admitted_reader(inputs, second, workflow_id="second")
    refs = [first.resolved_skillset_ref, second.resolved_skillset_ref]
    payloads = await asyncio.gather(reader_a.read_bytes(refs[0]), reader_b.read_bytes(refs[1]))
    assert all(json.loads(body)["skills"][0]["content_ref"] for body in payloads)
    request_a.resolved_skillset_ref = refs[1]
    assert await reader_a.read_bytes(refs[0]) == payloads[0]
    for reader, other in ((reader_a, refs[1]), (reader_b, refs[0])):
        for method in ("read", "get_metadata", "read_chunks"):
            kwargs = {"chunk_size": 16} if method == "read_chunks" else {}
            with pytest.raises(OmnigentArtifactError, match="not admitted"):
                await getattr(reader, method)(artifact_id=other, principal="operator", **kwargs)
    # Reconstruct both service and reader from the same durable database and
    # blob store, with no cookies, cached grants, or copied input bytes.
    replacement = TemporalOmnigentArtifactGateway(inputs.sessions).for_request(inputs.request(first, workflow_id="first"))
    assert await replacement.read_bytes(refs[0]) == payloads[0]
    with pytest.raises(OmnigentArtifactError, match="not admitted"):
        await replacement.read_bytes(refs[1])


@pytest.mark.parametrize("change", ["namespace", "run", "workflow", "skill_ref", "plan_digest", "plan_pointer", "machine"])
async def test_substituted_identity_or_authority_is_denied(inputs, change, monkeypatch):
    compiled = await inputs.compile()
    reader, request = await admitted_reader(inputs, compiled)
    if change == "namespace":
        monkeypatch.setattr(settings.temporal, "namespace", "other-namespace")
    elif change in {"run", "workflow"}:
        field = change + "_id"
        request.step_execution = request.step_execution.model_copy(update={field: "sibling"})
    elif change == "skill_ref":
        request.resolved_skillset_ref = await write_input(inputs)
    elif change in {"plan_digest", "plan_pointer"}:
        field = "plan_digest" if change == "plan_digest" else "plan_artifact_ref"
        value = digest(b"forged") if change == "plan_digest" else await write_input(inputs)
        request.omnigent_execution_plan = request.omnigent_execution_plan.model_copy(update={field: value})
    gateway = inputs.gateway if change != "machine" else TemporalOmnigentArtifactGateway(inputs.sessions, principal="service:forged")
    with pytest.raises(OmnigentArtifactError):
        await gateway.for_request(request).read_bytes(compiled.resolved_skillset_ref)


async def test_revoked_admission_and_missing_scope_fail_in_disabled_mode(inputs, monkeypatch):
    from sqlalchemy import delete
    compiled = await inputs.compile()
    reader, request = await admitted_reader(inputs, compiled)
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    async with inputs.sessions() as session:
        await session.execute(delete(models.TemporalArtifactLink).where(
            models.TemporalArtifactLink.artifact_id == compiled.resolved_skillset_ref,
            models.TemporalArtifactLink.link_type == "input.execution_plan"))
        await session.commit()
    with pytest.raises(OmnigentArtifactError, match="authority"):
        await reader.read_bytes(compiled.resolved_skillset_ref)
    request.step_execution = None
    with pytest.raises(OmnigentArtifactError, match="Step Execution"):
        await inputs.gateway.for_request(request).read_bytes(compiled.resolved_skillset_ref)


@pytest.mark.parametrize("producer_workflow", ["parent", "child"])
async def test_actual_linked_skill_producer_and_managed_launcher(inputs, producer_workflow, tmp_path, monkeypatch):
    from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities
    from moonmind.schemas.agent_skill_models import SkillSelector
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
    from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
    monkeypatch.setattr("moonmind.workflows.agent_skills.agent_skills_activities.activity.info", lambda: SimpleNamespace(
        workflow_id=producer_workflow, workflow_run_id="run-inputs", namespace="input-tests", activity_id="resolve"))
    resolved = await AgentSkillsActivities(artifact_service=inputs.artifacts).resolve_skills(SkillSelector())
    request = AgentExecutionRequest(agentKind="managed", agentId="managed", correlationId=producer_workflow,
        idempotencyKey=producer_workflow, resolvedSkillsetRef=resolved.manifest_ref,
        parameters={"selectedSkill": "selected"}, stepExecution={"workflowId": producer_workflow,
            "runId": "run-inputs", "logicalStepId": "read", "executionOrdinal": 1,
            "stepExecutionId": f"{producer_workflow}:run-inputs:read:execution:1", "runtimeContextPolicy": "fresh_agent_run",
            "resolvedSkillsetRef": resolved.manifest_ref})
    if producer_workflow == "parent":
        async with inputs.sessions() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            for principal in ("service:omnigent-generic-host", "agent_runtime"):
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(artifact_id=resolved.manifest_ref, principal=principal)
        # A distinct child may read the parent's selected bundle only through
        # its own verified immutable plan; its workflow id alone is insufficient.
        from moonmind.omnigent.harness_platform.execution_plan import create_execution_plan_envelope
        from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
        compiled = await inputs.compile("admitted-child")
        payload = compiled.envelope.payload.model_dump(by_alias=True, mode="json")
        async with inputs.sessions() as session:
            manifest = await TemporalArtifactRepository(session).get_artifact(resolved.manifest_ref)
            payload["resolvedSkills"].update({"resolvedSkillSetRef": resolved.manifest_ref,
                "resolvedSkillSetDigest": "sha256:" + manifest.sha256})
        plan = create_execution_plan_envelope(payload)
        await DbExecutionPlanStore(inputs.sessions).persist(plan)
        plan_artifact, _ = await writer.persist_json_artifact(artifact_service=inputs.artifacts,
            principal="operator", artifact_class="omnigent.execution_plan", payload=plan.model_dump(by_alias=True, mode="json"))
        binding = OmnigentExecutionPlanBinding.model_validate({
            **compiled.binding.model_dump(by_alias=True), "planRef": plan.planRef,
            "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1], "planArtifactRef": plan_artifact})
        child = inputs.request(SimpleNamespace(binding=binding, resolved_skillset_ref=resolved.manifest_ref), "admitted-child")
        await inputs.gateway.admit_execution_plan_inputs(request=child, plan=plan)
        assert json.loads(await inputs.gateway.for_request(child).read_bytes(resolved.manifest_ref))["skills"][0]["content_ref"]
    workspace = tmp_path / "managed" / "repo"
    workspace.mkdir(parents=True)
    launcher = ManagedRuntimeLauncher.__new__(ManagedRuntimeLauncher)
    launcher._artifact_service = inputs.artifacts
    result = await launcher._project_run_skill_snapshot(request=request,
        profile=SimpleNamespace(runtime_id="claude_code"), resolved_workspace_path=str(workspace))
    assert (Path(result["visiblePath"]) / "selected" / "helper.py").read_bytes() == (inputs.source / "helper.py").read_bytes()
    # The surviving runtime publication produces a reference wrapper, not a
    # second Skill manifest. Its child link must never imply a wildcard grant.
    activities = TemporalAgentRuntimeActivities(artifact_service=inputs.artifacts)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(workspace.parent.parent))
    direct_projection = await activities._materialize_selected_agent_skill_for_turn(request=request, workspace_path=str(workspace))
    assert (Path(direct_projection["visiblePath"]) / "selected" / "helper.py").read_bytes() == (inputs.source / "helper.py").read_bytes()
    activities.execution_notify_completion = AsyncMock()
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    published = await activities.agent_runtime_publish_artifacts(AgentRunResult(summary="done", metadata={"resolvedSkillsetRef": resolved.manifest_ref}))
    wrapper_ref = published.metadata["inputSkillSnapshotRef"]
    async with inputs.sessions() as session:
        repo = TemporalArtifactRepository(session)
        wrapper = await repo.get_artifact(wrapper_ref)
        assert wrapper.created_by_principal == "system:agent_runtime"
        assert any(link.workflow_id == producer_workflow for link in await repo.list_links(wrapper_ref))
        assert json.loads(inputs.store.read_bytes(wrapper.storage_key)) == {"resolvedSkillsetRef": resolved.manifest_ref}
    # The runtime copy remains a wrapper, with the original manifest and exact
    # helper bytes readable through a freshly reconstructed admitted reader.
    replacement = TemporalOmnigentArtifactGateway(inputs.sessions).for_request(request)
    assert json.loads(await replacement.read_bytes(resolved.manifest_ref))["skills"][0]["content_ref"]
    with pytest.raises(OmnigentArtifactError, match="not admitted"):
        await replacement.read_bytes(wrapper_ref)


@pytest.mark.parametrize("state", ["restricted", "preview_only", "quarantined", "expired", "incomplete", "corrupt", "oversize"])
async def test_workspace_preserves_raw_lifetime_integrity_and_size_policy(inputs, state, tmp_path, monkeypatch):
    from datetime import timedelta
    from moonmind.core.artifacts import TemporalArtifactRedactionLevel, TemporalArtifactStatus
    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector, WorkspaceArtifactProjectionError
    attachment = await write_input(inputs, b"safe bytes")
    compiled = await inputs.compile(snapshot={"attachmentRefs": [{"artifactId": attachment, "digest": digest(b"safe bytes")}]})
    reader, request = await admitted_reader(inputs, compiled)
    async with inputs.sessions() as session:
        row = await TemporalArtifactRepository(session).get_artifact(attachment)
        if state in {"restricted", "preview_only"}:
            row.redaction_level = TemporalArtifactRedactionLevel(state)
        elif state == "quarantined":
            row.metadata_json = {"quarantine": True}
        elif state == "expired":
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif state == "incomplete":
            row.status = TemporalArtifactStatus.PENDING_UPLOAD
        elif state == "corrupt":
            inputs.store.read_path(row.storage_key).write_bytes(b"corruption")
        await session.commit()
    if state == "oversize":
        monkeypatch.setattr("moonmind.omnigent.workspace_artifacts.MAX_INPUT_BYTES", 2)
    workspace = tmp_path / "policy"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError):
        await WorkspaceArtifactProjector(reader).project_attachments(workspace, refs=(f"artifact://{attachment}",),
            workflow_id=request.step_execution.workflow_id, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    assert not any(path.is_file() for path in workspace.glob(".moonmind/attachments/*"))
    if state in {"restricted", "preview_only"}:
        _row, _links, _pinned, policy = await reader.get_metadata(artifact_id=attachment, principal="operator")
        assert policy.default_read_ref is not None
        with pytest.raises((OmnigentArtifactError, TemporalArtifactAuthorizationError)):
            await reader.read_bytes(attachment)


async def test_failed_manifest_provenance_never_leaves_a_read_grant(inputs):
    from moonmind.omnigent.harness_platform.execution_plan import create_execution_plan_envelope
    compiled = await inputs.compile()
    payload = compiled.envelope.payload.model_dump(by_alias=True, mode="json")
    payload["resolvedSkills"]["resolvedSkillSetDigest"] = digest(b"forged manifest")
    plan = await DbExecutionPlanStore(inputs.sessions).persist(create_execution_plan_envelope(payload))
    plan_ref, _ = await writer.persist_json_artifact(artifact_service=inputs.artifacts,
        principal="operator", artifact_class="omnigent.execution_plan", payload=plan.model_dump(by_alias=True, mode="json"))
    binding = compiled.binding.model_copy(update={"plan_ref": plan.planRef,
        "plan_digest": "sha256:" + plan.planRef.rsplit(":", 1)[-1], "plan_artifact_ref": plan_ref})
    request = inputs.request(compiled).model_copy(update={"omnigent_execution_plan": binding,
        "parameters": {"executionPlanRef": plan.planRef}})
    request.step_execution = request.step_execution.model_copy(update={"omnigent_execution_plan": binding})
    with pytest.raises(OmnigentArtifactError, match="provenance"):
        await inputs.gateway.admit_execution_plan_inputs(request=request, plan=plan)
    with pytest.raises(OmnigentArtifactError):
        await inputs.gateway.for_request(request).read_bytes(compiled.resolved_skillset_ref)
