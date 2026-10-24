"""Runtime readers carry the admitted execution's input authority (#4633).

Uses the real artifact service (SQLite metadata plus local blobs) and
synthetic identities. Retired AUTH_PROVIDER values never relax machine
execution delegation or the immutable plan input closure.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
)
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import (
    OmnigentArtifactError,
    TemporalOmnigentArtifactGateway,
    link_verified_execution_plan_inputs,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.skills import OmnigentSkillDeliveryService
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.workspace_artifacts import (
    WorkspaceArtifactProjectionError,
    WorkspaceArtifactProjector,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentExecutionPlanBinding,
)
from moonmind.workflows.skills.run_projection import (
    SkillProjectionError,
    load_resolved_skillset,
    materialize_run_skill_snapshot,
    step_execution_read_principal,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import _codex_plan_inputs

AUTHENTICATED_MODES = ("accounts", "oidc", "header")
AUTH_MODES = (*AUTHENTICATED_MODES, "disabled")
SKILL_NAME = "admitted-skill"


@pytest.fixture
def auth_mode():
    return "oidc"


@pytest_asyncio.fixture
async def artifacts(tmp_path, monkeypatch, auth_mode):
    monkeypatch.setenv("AUTH_PROVIDER", str(auth_mode or ""))
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _put(
    sessions,
    payload: bytes,
    *,
    owner: str,
    workflow_id: str | None,
    link_type: str,
    content_type: str = "application/octet-stream",
    metadata: dict | None = None,
) -> str:
    async with sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        artifact, _ = await service.create(
            principal=owner,
            content_type=content_type,
            size_bytes=len(payload),
            metadata_json=metadata,
            link=(
                ExecutionRef(
                    namespace="default",
                    workflow_id=workflow_id,
                    run_id=f"{workflow_id}-run",
                    link_type=link_type,
                )
                if workflow_id
                else None
            ),
        )
        await service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=owner,
            payload=payload,
            content_type=content_type,
        )
    return artifact.artifact_id


async def _protect(sessions, artifact_id: str, protection: str) -> None:
    async with sessions() as session:
        repository = TemporalArtifactRepository(session)
        artifact = await repository.get_artifact(artifact_id)
        if protection == "restricted":
            artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        else:
            artifact.metadata_json = {"quarantine": True}
        await session.commit()


def _skill_body(tag: str) -> bytes:
    return f"---\nname: {SKILL_NAME}\ndescription: {tag}\n---\nUse {tag}.\n".encode()


async def _skill_set(sessions, *, tag: str, owner: str, workflow_id: str | None):
    """Persist a Skill body plus manifest like ``agent_skill.resolve`` does."""

    body = _skill_body(tag)
    body_id = await _put(
        sessions,
        body,
        owner=owner,
        workflow_id=workflow_id,
        link_type="input.agent_skill_body",
        content_type="text/markdown",
    )
    manifest = json.dumps(
        {
            "snapshot_id": f"skillset_{tag}",
            "resolved_at": datetime.now(tz=UTC).isoformat(),
            "skills": [
                {
                    "skill_name": SKILL_NAME,
                    "content_ref": body_id,
                    "content_digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                    "format": "markdown",
                    "provenance": {"source_kind": "built_in"},
                }
            ],
        },
        sort_keys=True,
    ).encode()
    manifest_id = await _put(
        sessions,
        manifest,
        owner=owner,
        workflow_id=workflow_id,
        link_type="input.skill_snapshot",
        content_type="application/json",
    )
    canonical = json.dumps(
        json.loads(manifest), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return SimpleNamespace(
        manifest_id=manifest_id,
        manifest_digest="sha256:" + hashlib.sha256(canonical).hexdigest(),
        body_id=body_id,
        body=body,
    )


async def _admitted_plan(
    sessions, skills, *, plan_owner: str = "operator", manifest_digest: str | None = None
):
    """Compile, persist and publish a verified plan admitting ``skills``."""

    inputs = _codex_plan_inputs()
    if manifest_digest is None:
        canonical = json.dumps(
            json.loads(await _read_as_owner(sessions, skills.manifest_id)),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        manifest_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    inputs["resolved_skills"] = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": f"artifact:{skills.manifest_id}",
            "resolvedSkillSetDigest": manifest_digest,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    plan = await DbExecutionPlanStore(sessions).persist(
        compile_execution_plan(**inputs)
    )
    plan_bytes = json.dumps(
        plan.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    plan_artifact = await _put(
        sessions,
        plan_bytes,
        owner=plan_owner,
        workflow_id=None,
        link_type="",
        content_type="application/json",
        metadata={"artifact_class": "omnigent.execution_plan"},
    )
    task_input = b'{"task":"synthetic"}'
    task_input_id = await _put(
        sessions, task_input, owner=plan_owner, workflow_id=None, link_type=""
    )
    binding = OmnigentExecutionPlanBinding(
        planRef=plan.planRef,
        planDigest="sha256:" + plan.planRef.rsplit(":", 1)[-1],
        planArtifactRef=f"artifact:{plan_artifact}",
        taskInputSnapshotRef=f"artifact:{task_input_id}",
        taskInputSnapshotDigest="sha256:" + hashlib.sha256(task_input).hexdigest(),
    )
    return plan, binding


async def _read_as_owner(sessions, artifact_id: str) -> bytes:
    async with sessions() as session:
        artifact = await TemporalArtifactRepository(session).get_artifact(artifact_id)
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        _artifact, payload = await service.read(
            artifact_id=artifact_id, principal=artifact.created_by_principal
        )
    return payload


def _request(plan, binding, *, workflow_id: str, skills) -> AgentExecutionRequest:
    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": workflow_id,
            "idempotencyKey": f"{workflow_id}:step",
            "executionProfileRef": "codex_openai_oauth",
            "resolvedSkillsetRef": skills.manifest_id,
            "omnigentExecutionPlan": binding,
            "parameters": {"executionPlanRef": plan.planRef},
            "stepExecution": {
                "schemaVersion": "v1",
                "workflowId": workflow_id,
                "runId": f"{workflow_id}-run",
                "logicalStepId": "step",
                "executionOrdinal": 1,
                "stepExecutionId": f"{workflow_id}:{workflow_id}-run:step:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )


async def _admit(sessions, plan, binding, *, workflow_id: str) -> None:
    await link_verified_execution_plan_inputs(
        session_factory=sessions,
        plan=plan,
        binding=binding,
        workflow_id=workflow_id,
        run_id=f"{workflow_id}-run",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", [None, "changed-content", "stored-bytes"])
async def test_legacy_manifest_canonical_digest_protects_the_admitted_closure(
    artifacts, corruption
):
    skills = await _skill_set(
        artifacts, tag="legacy-spaced", owner="operator", workflow_id=None
    )
    original = await _read_as_owner(artifacts, skills.manifest_id)
    assert "sha256:" + hashlib.sha256(original).hexdigest() != skills.manifest_digest
    if corruption == "changed-content":
        manifest = json.loads(original)
        manifest["snapshot_id"] = "substituted-snapshot"
        skills.manifest_id = await _put(
            artifacts,
            json.dumps(manifest, sort_keys=True).encode(),
            owner="operator",
            workflow_id=None,
            link_type="",
            content_type="application/json",
        )
    plan, binding = await _admitted_plan(
        artifacts, skills, manifest_digest=skills.manifest_digest
    )
    if corruption == "stored-bytes":
        async with artifacts() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            artifact = await service._repository.get_artifact(skills.manifest_id)
            # Valid JSON with unchanged meaning is still a corrupt stored blob
            # when it differs from the bytes recorded by its actual producer.
            service._store.read_path(artifact.storage_key).write_bytes(original + b"\n")
    if corruption:
        with pytest.raises(OmnigentArtifactError, match="manifest .*mismatch"):
            await _admit(artifacts, plan, binding, workflow_id="run-wf")
        async with artifacts() as session:
            assert not (
                await session.scalars(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id == "run-wf"
                    )
                )
            ).all()
    else:
        await _admit(artifacts, plan, binding, workflow_id="run-wf")
        request = _request(plan, binding, workflow_id="run-wf", skills=skills)
        assert (
            await TemporalOmnigentArtifactGateway(artifacts).read_admitted_input(
                skills.manifest_id, request=request
            )
        ) == original
    async with artifacts() as session:
        with pytest.raises(TemporalArtifactAuthorizationError):
            await TemporalArtifactService(TemporalArtifactRepository(session)).read(
                artifact_id=skills.manifest_id, principal="workflow:unrelated"
            )


# Producer shapes from the original report. ``workflow_id`` is the execution
# the producer linked its artifacts to; ``run-wf`` is the admitted Step.
SHAPES = {
    # agent_skill.resolve in the Run that owns the Step.
    "resolver-linked": {"owner": "agent_workflow", "workflow_id": "run-wf"},
    # A runtime copy linked to another (child/parent) execution.
    "other-execution-linked": {"owner": "agent_workflow", "workflow_id": "child-wf"},
    # A plan-service/operator snapshot with no execution link.
    "unlinked-operator": {"owner": "operator", "workflow_id": None},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
async def test_bare_runtime_readers_are_denied_without_admitted_scope(
    artifacts, auth_mode
):
    """Historical reproduction: bare service/runtime readers of a Skill set."""

    skills = await _skill_set(
        artifacts, tag="bare", owner="agent_workflow", workflow_id="run-wf"
    )
    gateway = TemporalOmnigentArtifactGateway(artifacts)
    with pytest.raises(TemporalArtifactAuthorizationError):
        await gateway.read_bytes(skills.manifest_id)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(SkillProjectionError):
            await load_resolved_skillset(service, skills.manifest_id)
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=skills.body_id,
                principal="service:omnigent_workspace_restore",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_admitted_skill_closure_reaches_every_runtime_consumer(
    artifacts, tmp_path, monkeypatch, auth_mode, shape
):
    skills = await _skill_set(artifacts, tag=shape, **SHAPES[shape])
    plan, binding = await _admitted_plan(artifacts, skills)
    request = _request(plan, binding, workflow_id="run-wf", skills=skills)
    gateway = TemporalOmnigentArtifactGateway(artifacts)

    # Planning reads the selected manifest before any plan or admission exists.
    pre_plan = request.model_copy(
        update={"omnigent_execution_plan": None, "parameters": {}}
    )
    assert await gateway.read_requested_skill_snapshot(
        skills.manifest_id, request=pre_plan
    ) == await _read_as_owner(artifacts, skills.manifest_id)

    await _admit(artifacts, plan, binding, workflow_id="run-wf")
    assert await gateway.read_admitted_input(skills.body_id, request=request) == (
        skills.body
    )

    # Generic host delivery (shared by the generic realizer).
    workspace_root = tmp_path / "worker"
    workspace_root.mkdir()
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("WORKFLOW_DOCKER_DAEMON_MODE", "remote")
    delivery = OmnigentSkillDeliveryService(
        workspace_root=workspace_root,
        workspace_volume="agent-workspaces",
        artifact_gateway=gateway,
    )
    await delivery.materialize(
        plan.payload.resolvedSkills, owner_ref="run-wf:step", request=request
    )
    delivered = list(
        workspace_root.glob(f".skill-projections/*/**/{SKILL_NAME}/SKILL.md")
    )
    assert [path.read_bytes() for path in delivered] == [skills.body]

    # Codex host preparation through the realizer's gateway and through the
    # direct coordinator's raw artifact service.
    host = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), network="test-network", workspace_root=tmp_path
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        for key, reader in (("gateway", gateway), ("service", service)):
            projection = await host._prepare_skill_projection(
                workspace_key=f"run-wf:{key}",
                resolved_skillset_ref=skills.manifest_id,
                artifact_gateway=reader,
                request=request,
            )
            assert (projection / SKILL_NAME / "SKILL.md").read_bytes() == skills.body

    # Managed launcher: artifact service plus the Step Execution scope.
    admitted_principal = step_execution_read_principal(request)
    assert admitted_principal == "workflow:run-wf"
    managed_root = tmp_path / "managed"
    (managed_root / "repo").mkdir(parents=True)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        resolved = await load_resolved_skillset(
            service, skills.manifest_id, admitted_principal=admitted_principal
        )
        metadata = await materialize_run_skill_snapshot(
            workspace_path=managed_root / "repo",
            run_root=managed_root,
            runtime_id="claude_code",
            resolved_skillset=resolved,
            artifact_service=service,
            admitted_principal=admitted_principal,
        )
    visible = Path(metadata["visiblePath"])
    assert (visible / SKILL_NAME / "SKILL.md").read_bytes() == skills.body


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
async def test_unadmitted_execution_cannot_read_another_executions_inputs(
    artifacts, auth_mode
):
    """Immutable closures and execution scope survive retired login selectors."""

    gateway = TemporalOmnigentArtifactGateway(artifacts)
    runs = {}
    for name in ("run-a", "run-b"):
        skills = await _skill_set(
            artifacts, tag=name, owner="operator", workflow_id=None
        )
        plan, binding = await _admitted_plan(artifacts, skills)
        await _admit(artifacts, plan, binding, workflow_id=name)
        runs[name] = (skills, _request(plan, binding, workflow_id=name, skills=skills))

    skills_a, request_a = runs["run-a"]
    skills_b, request_b = runs["run-b"]
    # Sibling ref substitution is outside the admitted closure.
    with pytest.raises(OmnigentArtifactError, match="not an admitted input"):
        await gateway.read_admitted_input(skills_b.body_id, request=request_a)
    with pytest.raises(OmnigentArtifactError, match="not the request's"):
        await gateway.read_requested_skill_snapshot(
            skills_b.manifest_id, request=request_a
        )
    # Artifact reads also require this run's delegation.
    forged = request_a.model_copy(
        update={
            "omnigent_execution_plan": request_b.omnigent_execution_plan,
            "parameters": dict(request_b.parameters),
        }
    )
    with pytest.raises(TemporalArtifactAuthorizationError):
        await gateway.read_admitted_input(skills_b.body_id, request=forged)
    # Concurrent use of one gateway keeps each run's delegation separate.
    results = await asyncio.gather(
        *(
            gateway.read_admitted_input(skills.body_id, request=request)
            for skills, request in (runs["run-a"], runs["run-b"]) * 4
        )
    )
    assert results == [skills_a.body, skills_b.body] * 4


@pytest.mark.asyncio
async def test_admitted_reader_rejects_missing_or_conflicting_authority(artifacts):
    skills = await _skill_set(
        artifacts, tag="authority", owner="agent_workflow", workflow_id="run-wf"
    )
    stray = await _put(
        artifacts,
        b"linked but not admitted",
        owner="agent_workflow",
        workflow_id="run-wf",
        link_type="input.agent_skill_body",
    )
    plan, binding = await _admitted_plan(artifacts, skills)
    request = _request(plan, binding, workflow_id="run-wf", skills=skills)
    gateway = TemporalOmnigentArtifactGateway(artifacts)

    # Linkage alone is not admission: the ref must be in the plan closure.
    with pytest.raises(OmnigentArtifactError, match="not an admitted input"):
        await gateway.read_admitted_input(stray, request=request)
    with pytest.raises(OmnigentArtifactError, match="Step Execution authority"):
        await gateway.read_admitted_input(
            skills.body_id, request=request.model_copy(update={"step_execution": None})
        )
    tampered = binding.model_copy(update={"plan_digest": "sha256:" + "0" * 64})
    with pytest.raises(OmnigentArtifactError, match="binding conflicts"):
        await gateway.read_admitted_input(
            skills.body_id,
            request=request.model_copy(update={"omnigent_execution_plan": tampered}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
async def test_admission_does_not_release_protected_raw_bytes(artifacts, protection, auth_mode):
    skills = await _skill_set(
        artifacts, tag=protection, owner="agent_workflow", workflow_id="run-wf"
    )
    await _protect(artifacts, skills.body_id, protection)
    plan, binding = await _admitted_plan(artifacts, skills)
    await _admit(artifacts, plan, binding, workflow_id="run-wf")
    request = _request(plan, binding, workflow_id="run-wf", skills=skills)
    with pytest.raises(Exception) as failure:
        await TemporalOmnigentArtifactGateway(artifacts).read_admitted_input(
            skills.body_id, request=request
        )
    assert type(failure.value).__name__ in {
        "TemporalArtifactAuthorizationError",
        "TemporalArtifactStateError",
    }


@pytest.mark.asyncio
async def test_admitted_inputs_survive_worker_replacement(artifacts, tmp_path):
    """A fresh gateway and database session re-read without new grants."""

    skills = await _skill_set(
        artifacts, tag="restart", owner="operator", workflow_id=None
    )
    plan, binding = await _admitted_plan(artifacts, skills)
    await _admit(artifacts, plan, binding, workflow_id="run-wf")
    request = _request(plan, binding, workflow_id="run-wf", skills=skills)
    replacement = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        sessions = sessionmaker(
            replacement, class_=AsyncSession, expire_on_commit=False
        )
        gateway = TemporalOmnigentArtifactGateway(sessions)
        assert (
            await gateway.read_admitted_input(skills.body_id, request=request)
            == skills.body
        )
    finally:
        await replacement.dispose()


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
async def test_restore_inputs_and_checkpoint_use_the_admitted_execution(
    artifacts, tmp_path, auth_mode
):
    restore = b"restore evidence\n"
    restore_id = await _put(
        artifacts,
        restore,
        owner="system:agent_runtime",
        workflow_id="run-wf",
        link_type="input.restore",
    )
    checkpoint = _archive({"tracked.txt": b"saved work\n"})
    checkpoint_id = await _put(
        artifacts,
        checkpoint,
        owner="system:agent_runtime",
        workflow_id="run-wf",
        link_type="evidence.recovery",
        content_type="application/gzip",
    )
    projector = WorkspaceArtifactProjector(TemporalOmnigentArtifactGateway(artifacts))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence = await projector.project(
        workspace,
        checkpoint_ref=f"artifact://{checkpoint_id}",
        checkpoint_digest="sha256:" + hashlib.sha256(checkpoint).hexdigest(),
        restore_refs=(f"artifact://{restore_id}",),
        workflow_id="run-wf",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
        strict_admission=True,
    )
    assert (workspace / "tracked.txt").read_bytes() == b"saved work\n"
    (restored,) = evidence["restoreInputs"]
    assert (workspace / restored["path"]).read_bytes() == restore

    # The Codex host's own restore path carries the same execution scope.
    codex_workspace = tmp_path / "codex-workspace"
    codex_workspace.mkdir()
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), network="test-network", workspace_root=tmp_path
    )
    (codex_evidence,) = await runtime._materialize_restore_inputs(
        codex_workspace,
        restore_input_refs=(f"artifact://{restore_id}",),
        artifact_gateway=TemporalOmnigentArtifactGateway(artifacts),
        workflow_id="run-wf",
    )
    assert codex_evidence["bytes"] == len(restore)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTHENTICATED_MODES)
async def test_restore_inputs_of_another_execution_stay_denied(
    artifacts, tmp_path, auth_mode
):
    restore_id = await _put(
        artifacts,
        b"sibling evidence\n",
        owner="system:agent_runtime",
        workflow_id="sibling-wf",
        link_type="input.restore",
    )
    projector = WorkspaceArtifactProjector(TemporalOmnigentArtifactGateway(artifacts))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(WorkspaceArtifactProjectionError) as failure:
        await projector.project(
            workspace,
            restore_refs=(f"artifact://{restore_id}",),
            workflow_id="run-wf",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
        )
    assert failure.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), network="test-network", workspace_root=tmp_path
    )
    with pytest.raises(TemporalArtifactAuthorizationError):
        await runtime._materialize_restore_inputs(
            workspace,
            restore_input_refs=(f"artifact://{restore_id}",),
            artifact_gateway=TemporalOmnigentArtifactGateway(artifacts),
            workflow_id="run-wf",
        )


async def _admitted_launch(sessions, *, workflow_id: str):
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )

    skills = await _skill_set(
        sessions, tag=workflow_id, owner="agent_workflow", workflow_id=workflow_id
    )
    base, binding = await _admitted_plan(sessions, skills, plan_owner="system")
    launch = {
        "launchPolicyRef": base.payload.launchPolicyRef,
        "hostImageRef": "example/native-host@sha256:" + "a" * 64,
        "harness": base.payload.harnessId,
        "runtimeUid": 1000,
    }
    launch["snapshotRef"] = "omnigent-launch:sha256:" + hashlib.sha256(
        json.dumps(launch, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    body = json.dumps(launch, sort_keys=True, separators=(",", ":")).encode()
    launch_id = await _put(
        sessions, body, owner="system", workflow_id=None,
        link_type="input.effective_launch_snapshot", content_type="application/json",
    )
    payload = base.payload.model_dump(mode="json", by_alias=True)
    payload.update(
        hostImageRef=launch["hostImageRef"],
        omnigentHostBuildDigest="sha256:" + "b" * 64,
        hostArchitecture="linux/amd64",
        policySnapshotDigest="sha256:" + "c" * 64,
        effectiveLaunchSnapshotRef=f"artifact:{launch_id}",
        effectiveLaunchSnapshotDigest="sha256:" + hashlib.sha256(body).hexdigest(),
    )
    plan = await DbExecutionPlanStore(sessions).persist(
        create_execution_plan_envelope(payload)
    )
    plan_id = await _put(
        sessions,
        json.dumps(plan.model_dump(mode="json", by_alias=True), sort_keys=True,
                   separators=(",", ":"), ensure_ascii=False).encode(),
        owner="system", workflow_id=None, link_type="", content_type="application/json",
        metadata={"artifact_class": "omnigent.execution_plan"},
    )
    binding = binding.model_copy(update={
        "plan_ref": plan.planRef,
        "plan_digest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
        "plan_artifact_ref": f"artifact:{plan_id}",
    })
    request = _request(plan, binding, workflow_id=workflow_id, skills=skills)
    return plan, request, launch, launch_id


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
@pytest.mark.parametrize("binding_shape", ["typed", "retained-plan-ref"])
async def test_planned_host_launch_requires_its_admitted_execution(
    artifacts, auth_mode, binding_shape
):
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentPlannedHostResolver,
    )

    plan, request, launch, launch_id = await _admitted_launch(
        artifacts, workflow_id="launch-a"
    )
    gateway = TemporalOmnigentArtifactGateway(artifacts)
    resolver = OmnigentPlannedHostResolver(
        catalog_repository=SimpleNamespace(), artifact_gateway=gateway
    )
    # Reads require delegation before the exact launch is read.
    with pytest.raises(TemporalArtifactAuthorizationError):
        await resolver._load_exact_launch(plan, request=request)
    await gateway.admit_execution_plan_inputs(request=request, plan=plan)
    if binding_shape == "retained-plan-ref":
        request = request.model_copy(update={"omnigent_execution_plan": None})
    assert await resolver._load_exact_launch(plan, request=request) == launch
    with pytest.raises(OmnigentArtifactError, match="Step Execution authority"):
        await resolver._load_exact_launch(
            plan, request=request.model_copy(update={"step_execution": None})
        )
    with pytest.raises(TemporalArtifactAuthorizationError):
        await gateway.read_bytes(launch_id)

    other_plan, other_request, _launch, _launch_id = await _admitted_launch(
        artifacts, workflow_id="launch-b"
    )
    await gateway.admit_execution_plan_inputs(request=other_request, plan=other_plan)
    # A substituted launch is outside the request's immutable plan closure.
    with pytest.raises(OmnigentArtifactError, match="not an admitted input"):
        await resolver._load_exact_launch(other_plan, request=request)
    # Artifact reads reject another workflow's delegation.
    forged = request.model_copy(update={
        "omnigent_execution_plan": other_request.omnigent_execution_plan,
        "parameters": dict(other_request.parameters),
    })
    with pytest.raises(TemporalArtifactAuthorizationError):
        await resolver._load_exact_launch(other_plan, request=forged)
    async with artifacts() as session:
        row = await TemporalArtifactRepository(session).get_artifact(launch_id)
        assert row.created_by_principal == "system"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", AUTH_MODES)
@pytest.mark.parametrize("protection", ["restricted", "quarantined"])
async def test_planned_host_launch_preserves_raw_protection(
    artifacts, auth_mode, protection
):
    from moonmind.omnigent.harness_platform.planning_service import (
        OmnigentPlannedHostResolver,
    )

    plan, request, _launch, launch_id = await _admitted_launch(
        artifacts, workflow_id="protected-launch"
    )
    gateway = TemporalOmnigentArtifactGateway(artifacts)
    await gateway.admit_execution_plan_inputs(request=request, plan=plan)
    await _protect(artifacts, launch_id, protection)
    resolver = OmnigentPlannedHostResolver(
        catalog_repository=SimpleNamespace(), artifact_gateway=gateway
    )
    with pytest.raises(Exception) as failure:
        await resolver._load_exact_launch(plan, request=request)
    assert type(failure.value).__name__ in {
        "TemporalArtifactAuthorizationError", "TemporalArtifactStateError"
    }
