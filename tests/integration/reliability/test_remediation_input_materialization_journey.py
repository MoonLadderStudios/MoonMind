"""Replay scheduled remediation evidence through the real artifact/host boundary."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.omnigent.execute import _build_omnigent_first_message, _first_message_text
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjectionError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
)
from moonmind.workflows.skills.approval_policy import parse_step_gate_result
from moonmind.workflows.skills.run_projection import materialize_run_skill_snapshot
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopSpec,
    RemediationLoopState,
)
from moonmind.workflows.temporal.step_evidence import publish_step_evidence
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.container_job import (
    MoonMindContainerJobWorkflow,
)
from tests.integration.reliability.test_container_job_authority_journey import (
    _registered_activities,
    _workflow_input,
)
from tests.integration.reliability.test_container_job_plan_a_qualification_journey import (
    _PlanAWorkflowDaemon,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("existing_workspace", [False, True])
@pytest.mark.parametrize("separate_remaining_work", [False, True])
async def test_recurring_gate_refs_reach_readable_first_message_paths(
    tmp_path,
    existing_workspace,
    separate_remaining_work,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )

            async def publish(payload, owner="workflow-1"):
                artifact, _ = await service.create(
                    principal=f"workflow:{owner}",
                    content_type="application/json",
                    link={
                        "namespace": "moonmind",
                        "workflow_id": owner,
                        "run_id": "run-1",
                        "link_type": "output.primary",
                    },
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal=f"workflow:{owner}",
                    payload=json.dumps(payload).encode(),
                    content_type="application/json",
                )
                return f"artifact://{artifact.artifact_id}"

            brief = await publish({"issue": 4193})
            gate_payload = {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "remainingWork": ["fix"],
            }
            gate = await publish(gate_payload)
            remaining = await publish(["fix"]) if separate_remaining_work else gate
            workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]
            workspace = (
                tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"
            )
            (workspace / ".git" / "info").mkdir(parents=True)
            (workspace / "candidate.txt").write_text("preserved implementation")
            fixture = json.loads(
                (
                    Path(__file__).with_name("replays")
                    / "recurring-remediation-evidence"
                    / "request.json"
                ).read_text()
            )
            replay = dict(fixture["request"])
            replay["inputRefs"] = [brief]
            replay["parameters"] = {
                "gateResultRef": gate,
                "remainingWorkRef": remaining,
            }
            request = AgentExecutionRequest(
                **replay,
                instructionRef="Execute the resolved Skill using the verifier evidence.",
                workspaceSpec={
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": workspace_id,
                        "relativePath": "repo",
                    },
                    "repository": "MoonLadderStudios/MoonMind",
                    "branch": "main",
                },
            )

            async def no_clone(*args, **kwargs):
                raise AssertionError("candidate must remain authoritative")

            materializer = OmnigentWorkspaceMaterializer(
                command_runner=no_clone,
                workspace_root=tmp_path / "workspaces",
                artifact_service=service,
            )
            identity = {"runtime_uid": os.getuid(), "runtime_gid": os.getgid()}
            if existing_workspace:
                await materializer.materialize(
                    request.model_copy(update={"parameters": {}}), **identity
                )
            attachment = await materializer.materialize(request, **identity)
            # Actual generic-host binding must carry the host-owned paths into
            # the message builder. Provider session/model selection is unchanged.
            plan = SimpleNamespace(
                planRef="plan-1",
                payload=SimpleNamespace(
                    agentSource={"upstreamId": "agent-1"},
                    endpointRef="endpoint-1",
                    harnessId="any-qualified-harness",
                    hostClassRef="host-1",
                    launchPolicyRef="launch-1",
                    capturePolicy={},
                    modelConfig=SimpleNamespace(
                        qualifiedId="same-model", effort="xhigh"
                    ),
                ),
            )
            binding = SimpleNamespace(
                bindingId="binding-1",
                providerLeases={},
                hostBindingRef="binding-host",
                hostLeaseRef="lease-1",
            )
            realizer = object.__new__(GenericOmnigentHostRealizer)
            bound = realizer._bind_exact_host(
                request,
                plan,
                {
                    "omnigentHostId": "host-1",
                    "workspacePath": "/workspaces/run",
                    "materializedInputPaths": attachment["materializedInputPaths"],
                },
                binding,
            )
            message = await _build_omnigent_first_message(
                request=bound, prompt={}, artifact_gateway=None
            )
            text = _first_message_text(message)
            paths = attachment["materializedInputPaths"]
            for name, path in paths.items():
                assert f"- {name}: {path}" in text
                assert (workspace / path).is_file()
            assert (
                json.loads((workspace / paths["gateResultPath"]).read_text())
                == gate_payload
            )
            assert json.loads((workspace / paths["remainingWorkPath"]).read_text()) == (
                ["fix"] if separate_remaining_work else gate_payload
            )
            assert (
                workspace / "candidate.txt"
            ).read_text() == "preserved implementation"
            assert (
                bound.parameters["omnigent"]["session"]["modelOverride"] == "same-model"
            )
            # Replayed requests re-admit the same inputs without truncating
            # read-only files, restoring a checkpoint, or replacing the candidate.
            assert (await materializer.materialize(request, **identity))[
                "materializedInputPaths"
            ] == paths
            input_root = workspace / ".moonmind" / "attachments"
            admitted_inputs = {
                path.name: path.read_bytes() for path in input_root.iterdir()
            }
            foreign = await publish({"secret": "foreign"}, owner="other-workflow")
            rejected = request.model_copy(
                update={"parameters": {"gateResultRef": foreign}}
            )
            with pytest.raises(HarnessPlatformError) as failure:
                await materializer.materialize(rejected, **identity)
            assert failure.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
            assert isinstance(failure.value.__cause__, WorkspaceArtifactProjectionError)
            assert isinstance(
                failure.value.__cause__.__cause__, TemporalArtifactAuthorizationError
            )
            assert {
                path.name: path.read_bytes() for path in input_root.iterdir()
            } == admitted_inputs
            assert (
                workspace / "candidate.txt"
            ).read_text() == "preserved implementation"
    finally:
        await engine.dispose()


# MoonLadderStudios/MoonMind#5109 extends the same artifact/materializer journey
# through the job producer and the resolved verifier/publication consumers.
_ROOT = Path(__file__).resolve().parents[3]
_OWNER = Path(__file__).with_name("replays") / "validation-receipt-reuse/owner.py"
_CHECKS = ("prepare", "build", "targeted-test")
_WORKFLOW = "receipt-reuse-workflow"


def _digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _owner(action, repo, **options):
    result = subprocess.run(
        [sys.executable, str(_OWNER), action, str(repo), json.dumps(options)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class _ReceiptDaemon(_PlanAWorkflowDaemon):
    """Control only the Docker transport; execute the fixture workload for real."""

    def __init__(self, repo, options, *, lost_ack=False):
        super().__init__(holder_name=None, ownership_token="unused")
        self.repo, self.options = repo, options
        self.drop_next_start_ack = lost_ack
        self.finish_after_lost_ack = lost_ack
        self.workloads = []
        self.result = None

    async def run(self, raw):
        command = tuple(str(item) for item in raw)
        if command[0] == "start" and self.result is None:
            self.result = subprocess.run(
                [
                    sys.executable,
                    str(_OWNER),
                    "produce",
                    str(self.repo),
                    json.dumps(self.options),
                ],
                capture_output=True,
            )
            self.workloads.extend(
                json.loads(line)["check"] for line in self.result.stdout.splitlines()
            )
        if command[:3] == ("inspect", "--format", "{{json .State}}") and self.result:
            self.commands.append(command)
            return (
                0,
                json.dumps(
                    {"Running": False, "ExitCode": self.result.returncode}
                ).encode(),
                b"",
            )
        if command[0] == "logs" and self.result:
            self.commands.append(command)
            return 0, self.result.stdout, self.result.stderr
        result = await super().run(raw)
        if command[0] == "start":
            self.states[command[1]] = "exited"
        return result


@pytest_asyncio.fixture
async def receipt_journey(tmp_path):
    """Real Git, artifact service, job owner and immutable Skill projection."""
    repo = tmp_path / "producer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Receipt fixture")
    _git(repo, "config", "user.email", "receipt@example.test")
    (repo / ".gitignore").write_text("artifacts/\n.moonmind/\n")
    (repo / "Source").mkdir()
    (repo / "Content").mkdir()
    (repo / "Source/fixture.py").write_text("def value():\n    return 42\n")
    (repo / "Content/fixture.txt").write_text("admitted Demo Content\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "controlled Tactics candidate for MoonMind#5109")
    _git(repo, "switch", "-c", "work/receipt-reuse")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/receipts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "blobs"),
            )
            catalog = PresetCatalogService(session)
            await catalog.sync_seed_templates(
                seed_dir=_ROOT / "api_service/data/presets"
            )
            expanded = await catalog.expand_template(
                slug="issue-implement-work-pr",
                inputs={
                    "issue_provider": "github",
                    "issue_ref": "MoonLadderStudios/MoonMind#5109",
                    "brief_artifact_path": "artifacts/brief.json",
                    "assessment_artifact_path": "artifacts/assessment.json",
                    "pr_artifact_path": "artifacts/pr.json",
                    "verify_artifact_path": "artifacts/verify.json",
                },
                context={},
            )

            async def put(raw, *, owner=_WORKFLOW):
                artifact, _ = await service.create(
                    principal=f"workflow:{owner}",
                    content_type="application/octet-stream",
                    link=ExecutionRef(
                        namespace="default",
                        workflow_id=owner,
                        run_id="run-1",
                        link_type="output.primary",
                    ),
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal=f"workflow:{owner}",
                    payload=raw,
                    content_type="application/octet-stream",
                )
                return "artifact://" + artifact.artifact_id

            async def read(ref):
                _, raw = await service.read(
                    artifact_id=ref.removeprefix("artifact://"),
                    access=ArtifactAccessContext.for_execution(_WORKFLOW),
                )
                return raw

            archive = io.BytesIO()
            skill_root = _ROOT / ".agents/skills/moonspec-verify"
            with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                for relative in (
                    "SKILL.md",
                    "scripts/acceptance.py",
                    "references/acceptance-policy.md",
                ):
                    bundle.add(skill_root / relative, arcname=relative)
            skill_bytes = archive.getvalue()
            skill_ref = await put(skill_bytes)
            resolved = ResolvedSkillSet(
                snapshot_id="receipt-reuse",
                resolved_at=datetime.now(UTC),
                skills=[
                    ResolvedSkillEntry(
                        skill_name="moonspec-verify",
                        format=AgentSkillFormat.BUNDLE,
                        content_ref=skill_ref.removeprefix("artifact://"),
                        content_digest=_digest(skill_bytes),
                        provenance=AgentSkillProvenance(
                            source_kind=AgentSkillSourceKind.DEPLOYMENT
                        ),
                    )
                ],
            )
            projection = await materialize_run_skill_snapshot(
                workspace_path=tmp_path / "skill-workspace",
                run_root=tmp_path / "skill-run",
                runtime_id="omnigent",
                resolved_skillset=resolved,
                artifact_service=service,
                access=ArtifactAccessContext.for_execution(_WORKFLOW),
                project_adapter_aliases=False,
            )
            script = (
                Path(projection["visiblePath"])
                / "moonspec-verify/scripts/acceptance.py"
            )
            portable = ModuleType("resolved_receipt_acceptance")
            exec(compile(script.read_text(), str(script), "exec"), portable.__dict__)
            jobs = []

            async def run_job(
                workspace,
                *,
                checks=_CHECKS,
                lost_ack=False,
                cancel_while_waiting=False,
                **options,
            ):
                daemon = _ReceiptDaemon(
                    workspace, {**options, "checks": list(checks)}, lost_ack=lost_ack
                )
                if cancel_while_waiting:
                    daemon.states["moonmind-container-job-held"] = "running"
                jobs.append(daemon)

                async def publish(_request, _name, raw):
                    return await put(raw)

                async def project(request):
                    daemon.projected.append(request.state or request.terminal_state)

                daemon.projected = []
                backend = DockerContainerJobBackend(
                    workspace_root=workspace.parent,
                    backend_ref="system-proxy",
                    docker_host="tcp://controlled:2375",
                    command_runner=daemon.run,
                    evidence_publisher=publish,
                    projection_writer=project,
                    image_lock_root=tmp_path / "image-locks",
                    workspace_volume_name="agent_workspaces",
                    settings=resolve_container_backend_settings(
                        {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "1"}
                    ),
                )
                runtime = TemporalAgentRuntimeActivities(container_job_backend=backend)
                queue = "receipt-job-" + uuid4().hex
                payload = _workflow_input(
                    "container-job:" + uuid4().hex, "alpine:3.20", workspace
                )
                payload["request"]["source"]["workflowId"] = _WORKFLOW
                payload["request"]["spec"].update(
                    command=["python", "controlled-tactics-validation", *checks],
                    outputs=[
                        {"name": check, "relativePath": f"artifacts/{check}.json"}
                        for check in checks
                    ],
                )
                async with await WorkflowEnvironment.start_time_skipping() as env:
                    async with Worker(
                        env.client,
                        task_queue=settings.temporal.activity_agent_runtime_task_queue,
                        activities=_registered_activities(runtime),
                    ), Worker(
                        env.client,
                        task_queue=queue,
                        workflows=[MoonMindContainerJobWorkflow],
                        workflow_runner=UnsandboxedWorkflowRunner(),
                    ):
                        handle = await env.client.start_workflow(
                            MoonMindContainerJobWorkflow.run,
                            payload,
                            id=queue,
                            task_queue=queue,
                        )
                        if cancel_while_waiting:
                            for _ in range(100):
                                state = await handle.query("status")
                                if state["state"] == "waiting_for_capacity":
                                    break
                                await asyncio.sleep(0.02)
                            else:
                                pytest.fail(
                                    "controlled job did not reach its capacity hold"
                                )
                            await handle.signal("cancel")
                        result = await handle.result()
                assert result["artifactsRef"], result
                outputs = json.loads(await read(result["artifactsRef"]))["artifacts"]
                assert {item["name"] for item in outputs} == set(checks)
                if cancel_while_waiting:
                    assert all(
                        item["collectionStatus"] == "missing" for item in outputs
                    )
                    return result
                for item in outputs:
                    original = (workspace / item["relativePath"]).read_bytes()
                    assert await read(item["artifactRef"]) == original
                    assert item["sha256"] == hashlib.sha256(original).hexdigest()
                return result

            yield SimpleNamespace(
                repo=repo,
                root=tmp_path,
                service=service,
                put=put,
                read=read,
                portable=portable,
                script=script,
                steps=expanded["steps"],
                run_job=run_job,
                jobs=jobs,
            )
    finally:
        await engine.dispose()


def _controller(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _name: True)
    monkeypatch.setattr(
        run_module.workflow, "upsert_memo", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime(2026, 11, 1, tzinfo=UTC)
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id=_WORKFLOW,
            run_id="run-2",
        ),
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/Tactics"
    parent._remediation_loop_spec = RemediationLoopSpec(
        loopId="receipt-reuse",
        remediationTool={"type": "agent_runtime", "name": "omnigent"},
        verificationTool={"type": "agent_runtime", "name": "omnigent"},
        workspacePolicy="continue_from_loop_head",
        budgets={"hardMaxAttempts": 3},
        terminalPolicy={
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        sideEffectPolicy="workflow_owned",
        publicationPolicy="evaluate_after_terminal",
    )
    parent._remediation_loop_state = RemediationLoopState(
        loopId="receipt-reuse",
        phase="verification_pending",
        attemptOrdinal=1,
        consumedBudgets={"attempts": 1, "evidenceRetries": 1, "activityRetries": 1},
    )
    return parent


async def _recover_receipts(journey, monkeypatch):
    repo, portable = journey.repo, journey.portable
    original = {
        f"artifacts/{check}.json": (repo / f"artifacts/{check}.json").read_bytes()
        for check in _CHECKS
    }
    manifest = {
        "schemaVersion": "step-evidence/v1",
        "subject": portable.capture(repo, "MoonLadderStudios/Tactics", "main")[
            "subject"
        ],
        "files": [
            {"path": path, "sha256": _digest(raw)} for path, raw in original.items()
        ],
    }
    (repo / "artifacts/step-evidence.json").write_text(json.dumps(manifest))
    published = await publish_step_evidence(
        workspace=repo,
        service=journey.service,
        principal=f"workflow:{_WORKFLOW}",
        execution_ref=ExecutionRef(
            namespace="default",
            workflow_id=_WORKFLOW,
            run_id="run-1",
            link_type="step.evidence",
        ),
    )
    # Save repository work separately. Receipt files are intentionally absent
    # from this snapshot, so only the durable evidence path can supply them.
    snapshot = io.BytesIO()
    with tarfile.open(fileobj=snapshot, mode="w:gz") as bundle:
        for name in (".git", ".gitignore", "Source", "Content"):
            bundle.add(repo / name, arcname=name)
    checkpoint = await journey.put(snapshot.getvalue())
    with monkeypatch.context() as patch:
        parent = _controller(patch)
        parent._publish_context["acceptedPublishedHead"] = {
            "repository": "MoonLadderStudios/Tactics",
            "branch": "work/receipt-reuse",
            "baseBranch": "main",
            "headSha": manifest["subject"]["revision"],
            "pullRequestUrl": "https://github.com/MoonLadderStudios/Tactics/pull/1",
        }
        parent._remediation_loop_state = parent._remediation_loop_state.model_copy(
            update={"workspace_head_ref": checkpoint}
        )
        parent._record_execution_context(
            node_id="implement", execution_result={"outputs": published}
        )
        carried = parent._build_remediation_loop_continue_as_new_input(
            ordered_nodes=[]
        )["remediation_loop_continuation"]
        restored = _controller(patch)
        restored._remediation_loop_continuation = json.loads(json.dumps(carried))
        restored._restore_remediation_loop_continuation(ordered_nodes=[])
    shutil.rmtree(repo)
    assert not repo.exists()
    request = _receipt_request(restored, monkeypatch, "verify", journey.steps)
    request.workspace_spec = {
        "workspaceSource": {
            "kind": "checkpoint",
            "checkpointRef": checkpoint,
            "checkpointDigest": _digest(snapshot.getvalue()),
        },
    }
    journey.parent, journey.request = restored, request
    journey.original, journey.published = original, published
    journey.carried = carried
    return await _materialize_receipts(journey, request)


def _receipt_request(parent, monkeypatch, phase, steps):
    step = (
        next(
            step
            for step in steps
            if step.get("skill", {}).get("id") == "moonspec-verify"
        )
        if phase == "verify"
        else steps[-1]
    )
    with monkeypatch.context() as patch:
        _controller(patch)  # Supply only Temporal context for the real builder.
        return parent._build_agent_execution_request(
            node_inputs={
                "instructions": step["instructions"],
                "repository": "MoonLadderStudios/Tactics",
                "repositoryOperation": "read" if phase == "verify" else "write",
                "publishMode": "none" if phase == "verify" else "pr",
                "selectedSkill": "moonspec-verify" if phase == "verify" else "auto",
            },
            node_id=phase,
            tool_name="omnigent",
        )


async def _materialize_receipts(journey, request):
    identity = request.step_execution
    owner = identity.workflow_id if identity else request.correlation_id
    attempt = identity.step_execution_id if identity else request.idempotency_key
    workspace_id = hashlib.sha256(f"{owner}:{attempt}".encode()).hexdigest()[:24]
    request.workspace_spec = {
        **request.workspace_spec,
        "workspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
    }
    journey.workspace = (
        journey.root / "workspaces/temporal_sandbox" / workspace_id / "repo"
    )

    async def no_clone(*args, **kwargs):
        raise AssertionError("saved candidate must not reset to the base branch")

    attachment = await OmnigentWorkspaceMaterializer(
        command_runner=no_clone,
        workspace_root=journey.root / "workspaces",
        artifact_service=journey.service,
    ).materialize(request, runtime_uid=os.getuid(), runtime_gid=os.getgid())
    bound = object.__new__(GenericOmnigentHostRealizer)._bind_exact_host(
        request,
        SimpleNamespace(
            planRef="plan",
            payload=SimpleNamespace(
                agentSource={"upstreamId": "agent"},
                endpointRef="endpoint",
                harnessId="controlled-provider",
                hostClassRef="host",
                launchPolicyRef="launch",
                capturePolicy={},
                modelConfig=SimpleNamespace(qualifiedId="controlled", effort="high"),
            ),
        ),
        {
            "omnigentHostId": "host",
            "workspacePath": "/workspaces/run",
            "materializedInputPaths": attachment["materializedInputPaths"],
        },
        SimpleNamespace(
            bindingId="binding",
            providerLeases={},
            hostBindingRef="host",
            hostLeaseRef="lease",
        ),
    )
    message = _first_message_text(
        await _build_omnigent_first_message(
            request=bound, prompt={}, artifact_gateway=None
        )
    )
    paths = attachment["materializedInputPaths"]
    for name, path in paths.items():
        assert f"- {name}: {path}" in message
    # Input names are opaque. Discover the inventory by schema, then follow
    # its exact artifact refs/digests; never guess receipt filenames on disk.
    by_ref = {
        ref: journey.workspace / paths[f"inputPath{index}"]
        for index, ref in enumerate(request.input_refs, start=1)
    }
    journey.inputs_by_ref = by_ref
    inventory = next(
        json.loads(path.read_bytes())
        for path in by_ref.values()
        if json.loads(path.read_bytes()).get("schemaVersion") == "step-evidence/v1"
    )
    receipts = {}
    for item in inventory["files"]:
        raw = by_ref[item["artifactRef"]].read_bytes()
        assert _digest(raw) == item["sha256"]
        receipts[item["path"]] = (item["artifactRef"], by_ref[item["artifactRef"]], raw)
    return receipts


def _current(journey, admitted, receipts):
    portable = journey.portable
    current = portable.capture(journey.workspace, "MoonLadderStudios/Tactics", "main")
    source = b"Prepare admitted Content; build the admitted profile; execute the selected headless tests."
    current["scope"] = portable.scope(
        source, "MoonLadderStudios/MoonMind#5109", list(_CHECKS), complete=True
    )
    current["freshnessPolicy"] = "content"
    current["verificationMap"] = [
        {
            "id": check,
            "sourceRef": current["scope"]["sourceRef"],
            "sourceDigest": current["scope"]["sourceDigest"],
            "sourceLocator": check,
            "classification": "mandatory",
            "requiredBehavior": f"Execute admitted {check}",
            "executableChecks": [f"Tactics-owned {check}"],
            "proofMethods": ["controlled_job", "owner_compatibility"],
            "owner": admitted["owner"],
            "profile": admitted["bindings"]["profile"],
            "samplingPolicy": json.dumps(admitted["scopes"][check], sort_keys=True),
        }
        for check in _CHECKS
    ]
    return portable.bind_evidence(
        current, {ref: raw for ref, _, raw in receipts.values()}
    )


def _judgment(journey, current, receipts):
    """Controlled provider inspects actual reports, never a green aggregate."""
    rows, gaps = [], []
    for plan in current["verificationMap"]:
        check = plan["id"]
        ref, _, raw = receipts[f"artifacts/{check}.json"]
        observed = json.loads(raw)
        assert observed["subject"]["repository"] == current["subject"]["repository"]
        assert observed["scopes"][check] == json.loads(plan["samplingPolicy"])
        assert observed["bindings"]["profile"] == plan["profile"]
        row = {
            "requirementId": check,
            "obligationDigest": journey.portable.obligation_digest(plan),
            "testedSubject": observed["subject"],
        }
        if observed["status"] == "unavailable":
            row.update(
                status="UNVERIFIED",
                unverified={
                    "reason": observed["reason"],
                    "verifiableBy": observed["verifiableBy"],
                },
            )
        elif observed["status"] == "passed":
            assert observed["exitCode"] == 0
            if check == "targeted-test":
                assert observed["selectedTests"] == observed["bindings"]["selection"]
                assert (
                    observed["selectedTestCount"]
                    == len(observed["bindings"]["selection"])
                    > 0
                )
            row.update(
                status="VERIFIED",
                evidenceRefs=[ref],
                proofMethod="controlled_job",
                profile=plan["profile"],
                samplingPolicy=plan["samplingPolicy"],
            )
        else:
            row.update(status="FAILED", evidenceRefs=[ref])
            gaps.append(
                {
                    "requirement": check,
                    "gapType": "test_failure",
                    "remainingWork": observed["reason"],
                    "candidate": current["subject"],
                    "owner": "Tactics",
                    "entrypoint": "Tactics selected validation",
                    "evidenceRefs": [ref],
                    "resumeCheck": "Repair the observed defect and rerun this check.",
                }
            )
        rows.append(row)
    binding = {
        key: copy.deepcopy(current[key])
        for key in (
            "subject",
            "scope",
            "completionTarget",
            "verificationMap",
            "evidenceSet",
            "evidenceSetDigest",
        )
    }
    binding.update(
        schemaVersion="acceptance/v1", freshness={"policy": "content"}, evidence=rows
    )
    return {
        "verdict": "ADDITIONAL_WORK_NEEDED" if gaps else "FULLY_IMPLEMENTED",
        "recommendedNextAction": "reattempt_current_step" if gaps else "advance",
        "recoverableInCurrentRuntime": bool(gaps),
        "remainingWork": gaps,
        "validatedRefs": {"acceptance": binding},
    }


async def _consume(journey, report, current, *, phase, request_missing=True):
    """Controlled provider executes the actual resolved Skill's reuse command."""
    target = journey.workspace / "artifacts"
    target.mkdir(exist_ok=True)
    (target / "judgment.json").write_text(json.dumps(report))
    (target / "current.json").write_text(json.dumps(current))
    result = subprocess.run(
        [
            sys.executable,
            str(journey.script),
            "reuse",
            "--report",
            str(target / "judgment.json"),
            "--current",
            str(target / "current.json"),
        ],
        capture_output=True,
        text=True,
    )
    decision = json.loads(result.stdout)
    requested = []
    if request_missing and not decision["reusable"]:
        retained = {row["requirementId"] for row in decision.get("reusableRows", [])}
        requested = [check for check in _CHECKS if check not in retained]
        await journey.run_job(journey.workspace, checks=requested)
    # Existing artifacts record the source and decision that was actually used.
    ref = await journey.put(
        json.dumps(
            {
                "phase": phase,
                "reuse": decision,
                "evidenceSet": current["evidenceSet"],
                "requestedChecks": requested,
            },
            sort_keys=True,
        ).encode()
    )
    return decision, json.loads(await journey.read(ref))


async def test_original_validation_is_consumed_after_cleanup_without_duplicate_jobs(
    receipt_journey, monkeypatch
):
    journey = receipt_journey
    result = await journey.run_job(journey.repo, lost_ack=True)
    assert result["state"] == "succeeded"
    receipts = await _recover_receipts(journey, monkeypatch)
    admitted = _owner("inspect", journey.workspace)
    current = _current(journey, admitted, receipts)
    report = _judgment(journey, current, receipts)
    assert journey.portable.validate_report(report, current)["valid"]
    for phase in ("verify", "publication"):
        if phase == "publication":
            report_ref = await journey.put(json.dumps(report, sort_keys=True).encode())
            journey.parent._publish_context["moonSpecGate"] = {
                "gateResultRef": report_ref,
                "verdict": report["verdict"],
            }
            request = _receipt_request(
                journey.parent, monkeypatch, phase, journey.steps
            )
            request.workspace_spec = journey.request.workspace_spec
            receipts = await _materialize_receipts(journey, request)
            restored_report = json.loads(journey.inputs_by_ref[report_ref].read_bytes())
            assert restored_report == report
            report = restored_report
            current = _current(journey, _owner("inspect", journey.workspace), receipts)
            assert (
                request.parameters["acceptedPublishedHead"]["headSha"]
                == current["subject"]["revision"]
            )
        decision, persisted = await _consume(journey, report, current, phase=phase)
        assert decision["reusable"]
        assert {row["requirementId"] for row in decision["reusableRows"]} == set(
            _CHECKS
        )
        assert persisted["evidenceSet"] == {
            ref: _digest(raw) for ref, _, raw in receipts.values()
        }
        assert all(
            raw == journey.original[path] for path, (_, _, raw) in receipts.items()
        )
        assert (
            parse_step_gate_result(report, require_acceptance=True).verdict
            == "FULLY_IMPLEMENTED"
        )
    assert [check for job in journey.jobs for check in job.workloads] == list(_CHECKS)
    daemon = journey.jobs[0]
    assert sum(command[0] == "start" for command in daemon.commands) == 1
    assert sum(command[0] == "create" for command in daemon.commands) == 1
    assert daemon.real_starts == 1
    started = next(
        index for index, command in enumerate(daemon.commands) if command[0] == "start"
    )
    reconciled = next(
        index
        for index, command in enumerate(daemon.commands)
        if index > started and command[0] == "ps"
    )
    observed = next(
        index
        for index, command in enumerate(daemon.commands)
        if command[:3] == ("inspect", "--format", "{{json .State}}")
    )
    assert started < reconciled < observed
    assert (
        journey.parent._remediation_loop_state.consumed_budgets.model_dump(
            by_alias=True
        )
        == journey.carried["state"]["consumedBudgets"]
    )
    assert _git(journey.workspace, "status", "--porcelain") == ""


@pytest.mark.parametrize("stop", ["cancel", "hold"])
async def test_stop_boundaries_keep_receipts_candidate_and_original_budgets(
    receipt_journey, monkeypatch, stop
):
    journey = receipt_journey
    await journey.run_job(journey.repo)
    receipts = await _recover_receipts(journey, monkeypatch)
    state = journey.parent._remediation_loop_state.model_dump(by_alias=True)
    head = _git(journey.workspace, "rev-parse", "HEAD")
    if stop == "cancel":
        result = await journey.run_job(
            journey.workspace, checks=["targeted-test"], cancel_while_waiting=True
        )
        assert result["state"] == "canceled"
        assert journey.jobs[-1].workloads == []
        assert all(command[0] != "start" for command in journey.jobs[-1].commands)
    else:
        from moonmind.workflows.temporal.remediation_loop import (
            decide_remediation_continuation,
        )

        decision = decide_remediation_continuation(
            spec=journey.parent._remediation_loop_spec,
            state=journey.parent._remediation_loop_state,
            verdict="ADDITIONAL_WORK_NEEDED",
            gate_result_ref=journey.published["stepEvidenceRefs"][0],
            remaining_work_ref=None,
            recommended_next_action="blocked",
        )
        assert not decision.continue_loop and decision.next_phase.value == "blocked"
        with monkeypatch.context() as patch:
            _controller(patch)
            parent = journey.parent
            parent._paused = True
            waiting, release = asyncio.Event(), asyncio.Event()

            async def wait_condition(predicate):
                assert not predicate()
                waiting.set()
                await release.wait()
                assert predicate()

            patch.setattr(run_module.workflow, "wait_condition", wait_condition)
            pending = asyncio.create_task(parent._wait_if_paused_at_safe_boundary())
            await asyncio.wait_for(waiting.wait(), timeout=2)
            assert not pending.done() and len(journey.jobs) == 1
            parent._paused = False
            release.set()
            await pending
    assert journey.parent._remediation_loop_state.model_dump(by_alias=True) == state
    assert _git(journey.workspace, "rev-parse", "HEAD") == head
    assert journey.parent._publish_context["acceptedPublishedHead"][
        "pullRequestUrl"
    ].endswith("/pull/1")
    assert all(raw == journey.original[path] for path, (_, _, raw) in receipts.items())
    assert [check for job in journey.jobs for check in job.workloads] == list(_CHECKS)


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        ("source", {"build", "targeted-test"}),
        ("Content", {"prepare", "targeted-test"}),
        ("profile", set(_CHECKS)),
        ("selection", {"targeted-test"}),
        ("same-content-commit", set()),
    ],
)
async def test_owner_scopes_invalidate_only_the_missing_workload(
    receipt_journey, monkeypatch, change, missing
):
    journey = receipt_journey
    await journey.run_job(journey.repo)
    receipts = await _recover_receipts(journey, monkeypatch)
    admitted = _owner("inspect", journey.workspace)
    before = _current(journey, admitted, receipts)
    report = _judgment(journey, before, receipts)
    options = {}
    if change in {"source", "Content"}:
        path = "Source/fixture.py" if change == "source" else "Content/fixture.txt"
        with (journey.workspace / path).open("a") as stream:
            stream.write(
                "\n# changed relevant input\n"
                if change == "source"
                else "changed Content\n"
            )
        _git(journey.workspace, "add", path)
        _git(
            journey.workspace,
            "commit",
            "-qm",
            "Change admitted input for MoonMind#5109",
        )
    elif change == "same-content-commit":
        _git(
            journey.workspace,
            "commit",
            "--allow-empty",
            "-qm",
            "Same content for MoonMind#5109",
        )
    else:
        options = {
            change: (
                "Full"
                if change == "profile"
                else ["Tactics.Inventory.Focused", "Tactics.Save.Minimal"]
            )
        }
    # The external owner makes compatibility decisions from actual scoped
    # dependency bytes. No MoonMind path/profile/SHA heuristic substitutes.
    owner_result = _owner(
        "compatibility",
        journey.workspace,
        receipts={
            check: str(receipts[f"artifacts/{check}.json"][1]) for check in _CHECKS
        },
        **options,
    )
    decisions = owner_result["decisions"]
    assert {
        check for check, decision in decisions.items() if not decision["compatible"]
    } == missing
    current = _current(journey, owner_result, receipts)
    reuse, _ = await _consume(
        journey, report, current, phase="changed-input", request_missing=False
    )
    if change == "same-content-commit":
        assert reuse["reusable"]
        assert before["subject"]["revision"] != current["subject"]["revision"]
        assert {row["testedSubject"]["revision"] for row in reuse["reusableRows"]} == {
            before["subject"]["revision"]
        }
    else:
        assert not reuse["reusable"]
        if change in {"profile", "selection"}:
            assert {row["requirementId"] for row in reuse["reusableRows"]} == set(
                _CHECKS
            ) - missing
    original_receipts = dict(receipts)
    if missing:
        result = await journey.run_job(
            journey.workspace, checks=sorted(missing), **options
        )
        outputs = json.loads(await journey.read(result["artifactsRef"]))["artifacts"]
        # Re-enter the production materializer with the new normal job outputs.
        request = journey.request.model_copy(deep=True)
        request.input_refs.extend(item["artifactRef"] for item in outputs)
        request.parameters["stepEvidenceMandatoryRefs"] = [
            item["artifactRef"] for item in outputs
        ]
        await _materialize_receipts(journey, request)
        for item in outputs:
            raw = await journey.read(item["artifactRef"])
            receipts[item["relativePath"]] = (
                item["artifactRef"],
                journey.workspace / item["relativePath"],
                raw,
            )
            assert json.loads(raw)["subject"] == current["subject"]
            assert (
                json.loads(raw)["scopes"][item["name"]]
                == owner_result["scopes"][item["name"]]
            )
    # An owner observation can establish unchanged dependencies on new content;
    # the original report still records the original tested SHA and bytes.
    owner_raw = json.dumps(owner_result, sort_keys=True).encode()
    owner_ref = await journey.put(owner_raw)
    current = journey.portable.bind_evidence(
        current,
        {**{ref: raw for ref, _, raw in receipts.values()}, owner_ref: owner_raw},
    )
    fresh = _judgment(journey, current, receipts)
    for row in fresh["validatedRefs"]["acceptance"]["evidence"]:
        if row["requirementId"] not in missing:
            check = row["requirementId"]
            assert decisions[check]["receiptDigest"] == _digest(
                original_receipts[f"artifacts/{check}.json"][2]
            )
            row.update(
                proofMethod="owner_compatibility",
                evidenceRefs=[row["evidenceRefs"][0], owner_ref],
                testedSubject=owner_result["subject"],
            )
    assert journey.portable.validate_report(fresh, current)["valid"]
    final, persisted = await _consume(journey, fresh, current, phase="publication")
    assert final["reusable"] and persisted["requestedChecks"] == []
    assert [check for job in journey.jobs[1:] for check in job.workloads] == sorted(
        missing
    )
    assert all(
        raw == journey.original[path] for path, (_, _, raw) in original_receipts.items()
    )
    assert journey.parent._publish_context["acceptedPublishedHead"][
        "pullRequestUrl"
    ].endswith("/pull/1")


@pytest.mark.parametrize("damage", ["missing", "corrupt", "foreign", "inaccessible"])
async def test_unusable_original_evidence_is_explicit_and_preserves_accepted_work(
    receipt_journey, monkeypatch, damage
):
    journey = receipt_journey
    await journey.run_job(journey.repo)
    receipts = await _recover_receipts(journey, monkeypatch)
    current = _current(journey, _owner("inspect", journey.workspace), receipts)
    report = _judgment(journey, current, receipts)
    ref, _, raw = receipts["artifacts/targeted-test.json"]
    metadata, *_ = await journey.service.get_metadata(
        artifact_id=ref.removeprefix("artifact://"),
        access=ArtifactAccessContext.for_execution(_WORKFLOW),
    )
    storage = journey.root / "blobs" / metadata.storage_key
    admitted_bytes = {
        path.name: path.read_bytes()
        for path in (journey.workspace / ".moonmind/attachments").iterdir()
    }
    request = journey.request.model_copy(deep=True)
    if damage == "missing":
        storage.unlink()
    elif damage == "corrupt":
        storage.write_bytes(b'{"status":"passed","forged":true}')
    elif damage == "foreign":
        foreign = await journey.put(raw, owner="foreign-workflow")
        request.input_refs = [
            foreign if item == ref else item for item in request.input_refs
        ]
        request.parameters["stepEvidenceBundles"] = [
            [foreign if item == ref else item for item in group]
            for group in request.parameters["stepEvidenceBundles"]
        ]
    else:
        read_chunks = journey.service.read_chunks

        async def deny(*, artifact_id, **kwargs):
            if artifact_id == ref.removeprefix("artifact://"):
                raise TemporalArtifactAuthorizationError(
                    "original report is inaccessible"
                )
            return await read_chunks(artifact_id=artifact_id, **kwargs)

        monkeypatch.setattr(journey.service, "read_chunks", deny)
    with pytest.raises(HarnessPlatformError) as rejected:
        await _materialize_receipts(journey, request)
    assert rejected.value.code in {
        "WORKSPACE_AUTHORITY_MISMATCH",
        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
    }
    assert {
        path.name: path.read_bytes()
        for path in (journey.workspace / ".moonmind/attachments").iterdir()
    } == admitted_bytes
    unavailable = {item: body for item, _, body in receipts.values() if item != ref}
    if damage == "corrupt":
        unavailable[ref] = storage.read_bytes()
    current = journey.portable.bind_evidence(current, unavailable)
    decision, saved = await _consume(
        journey, report, current, phase="publication", request_missing=False
    )
    assert not decision["reusable"] and not decision["completionEligible"]
    assert {row["requirementId"] for row in decision["reusableRows"]} == {
        "prepare",
        "build",
    }
    assert saved["reuse"]["reasons"]
    assert _git(journey.workspace, "status", "--porcelain") == ""
    assert len(journey.jobs) == 1


@pytest.mark.parametrize(
    ("check", "claimed_proof"), [("build", "gameplay"), ("targeted-test", "rendered")]
)
async def test_build_and_headless_receipts_cannot_certify_stronger_proof(
    receipt_journey, monkeypatch, check, claimed_proof
):
    journey = receipt_journey
    await journey.run_job(journey.repo)
    receipts = await _recover_receipts(journey, monkeypatch)
    owner = _owner("inspect", journey.workspace)
    assert owner["capabilities"][claimed_proof] is False
    current = _current(journey, owner, receipts)
    report = _judgment(journey, current, receipts)
    plan = next(row for row in current["verificationMap"] if row["id"] == check)
    plan["proofMethods"] = [claimed_proof]
    plan["topology"] = (
        "gameplay-client" if claimed_proof == "gameplay" else "rendered-client"
    )
    result, _ = await _consume(
        journey, report, current, phase="publication", request_missing=False
    )
    assert not result["reusable"]
    assert check not in {row["requirementId"] for row in result["reusableRows"]}
    assert len(journey.jobs) == 1


@pytest.mark.parametrize("outcome", ["unavailable", "compile-defect", "test-defect"])
async def test_prerequisite_disclosure_and_real_defects_survive_continuation(
    receipt_journey, monkeypatch, outcome
):
    journey = receipt_journey
    if outcome != "unavailable":
        (journey.repo / "Source/fixture.py").write_text(
            "def value(:\n"
            if outcome == "compile-defect"
            else "def value():\n    return -1\n"
        )
        _git(
            journey.repo,
            "commit",
            "-am",
            "Reproduce controlled defect for MoonMind#5109",
        )
    job = await journey.run_job(journey.repo, unavailable=outcome == "unavailable")
    receipts = await _recover_receipts(journey, monkeypatch)
    current = _current(journey, _owner("inspect", journey.workspace), receipts)
    report = _judgment(journey, current, receipts)
    assert journey.portable.validate_report(report, current)["valid"]
    gate = parse_step_gate_result(report, require_acceptance=True)
    from moonmind.workflows.temporal.remediation_loop import (
        decide_remediation_continuation,
    )

    decision = decide_remediation_continuation(
        spec=journey.parent._remediation_loop_spec,
        state=journey.parent._remediation_loop_state,
        verdict=gate.verdict,
        gate_result_ref=await journey.put(json.dumps(report).encode()),
        remaining_work_ref=None,
        recommended_next_action=report["recommendedNextAction"],
    )
    if outcome == "unavailable":
        assert job["state"] == "succeeded"
        assert gate.verdict == "FULLY_IMPLEMENTED"
        row = report["validatedRefs"]["acceptance"]["evidence"][0]
        observed = json.loads(receipts["artifacts/prepare.json"][2])
        assert (
            row["status"] == "UNVERIFIED"
            and row["unverified"]["reason"] == observed["reason"]
        )
        assert row["unverified"]["verifiableBy"] == observed["verifiableBy"]
        assert observed["nextRoute"] and observed["attempts"] == 1
        assert not decision.continue_loop and decision.next_phase.value == "accepted"
        reused, _ = await _consume(journey, report, current, phase="publication")
        assert reused["reusable"]
    else:
        assert job["state"] == "failed"
        assert gate.verdict == "ADDITIONAL_WORK_NEEDED"
        assert report["remainingWork"] and decision.continue_loop
        assert decision.next_phase.value == "remediation_pending"
    assert journey.parent._remediation_loop_state.consumed_budgets.attempts == 1
    assert [check for job in journey.jobs for check in job.workloads] == list(_CHECKS)
