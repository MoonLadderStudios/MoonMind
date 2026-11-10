"""Branch producers must deliver saved Git work through recorded-plan dispatch."""

import dataclasses
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base
from api_service.services.checkpoint_branch_service import CheckpointBranchService
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
)
from moonmind.config.settings import settings
from moonmind.omnigent.control_plane.records import ControlPlaneOutcome
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
from moonmind.schemas.temporal_models import (
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
)
from moonmind.workflows.temporal.activities import omnigent_activities
from moonmind.workflows.temporal.activity_runtime import (
    TemporalCheckpointActivities,
    TemporalSandboxActivities,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.unit.omnigent.test_checkpoint_branch_turn_execution import (
    _authority_graph,
    _Session,
    _valid_source_checkpoint,
)
from tests.unit.omnigent.test_generic_platform_production_services import _exact_plan


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "workflow",
        "run",
        "step",
        "digest",
        "plan",
        "model_override",
        "source_param_mismatch",
    ],
)
async def test_branch_producer_dispatch_restores_saved_git_work(
    tmp_path, monkeypatch, fault
):
    model_override = fault == "model_override"
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    workspaces = tmp_path / "workspaces"
    identity = StepExecutionIdentityModel(
        workflowId="source-workflow",
        runId="source-run",
        logicalStepId="implement",
        executionOrdinal=1,
    )
    source_step = "source-workflow:source-run:implement:execution:1"
    source_id = hashlib.sha256(f"source-workflow:{source_step}".encode()).hexdigest()[
        :24
    ]
    source = workspaces / "temporal_sandbox" / source_id / "repo"
    source.mkdir(parents=True)
    git(source, "init", "-q", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "Test")
    (source / ".gitignore").write_text(".moonmind/\n")
    for name in ("staged.txt", "unstaged.txt", "deleted.txt", "committed.txt"):
        (source / name).write_text("base\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "base")
    baseline = git(source, "rev-parse", "HEAD")
    origin = tmp_path / "published.git"
    git(tmp_path, "clone", "--bare", "--quiet", str(source), str(origin))
    (source / "committed.txt").write_text("unpublished commit\n")
    git(source, "commit", "-qam", "unpublished work")
    head = git(source, "rev-parse", "HEAD")
    (source / "staged.txt").write_text("staged work\n")
    git(source, "add", "staged.txt")
    (source / "unstaged.txt").write_text("unstaged work\n")
    (source / "deleted.txt").unlink()
    expected_status = git(source, "status", "--porcelain")
    activity = ActivityEnvironment()
    activity.info = dataclasses.replace(
        activity.info, workflow_id="source-workflow", workflow_run_id="source-run"
    )
    try:
        async with sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )() as session:
            artifacts = TemporalArtifactService(TemporalArtifactRepository(session))

            async def write(payload, content_type="application/json"):
                record, _ = await artifacts.create(
                    principal="00000000-0000-0000-0000-000000000000",
                    content_type=content_type,
                    size_bytes=len(payload),
                    metadata_json={},
                    link=ExecutionRef(
                        namespace="default",
                        workflow_id="source-workflow",
                        run_id="source-run",
                        link_type="input.execution_plan",
                    ),
                )
                await artifacts.write_complete(
                    artifact_id=record.artifact_id,
                    principal="00000000-0000-0000-0000-000000000000",
                    payload=payload,
                    content_type=content_type,
                )
                return "artifact://" + record.artifact_id

            instruction = await write(b'"Continue the checkpoint branch"')
            captured = await activity.run(
                TemporalSandboxActivities(
                    workspace_root=workspaces, artifact_service=artifacts
                ).workspace_capture_checkpoint,
                {
                    "identity": identity.model_dump(by_alias=True),
                    "boundary": "before_publication",
                    "kind": "worktree_archive",
                    "workspacePath": str(source),
                    "artifactNamespace": "checkpoint",
                    "idempotencyKey": "capture",
                    "includeUntracked": True,
                },
            )
            _, manifest_bytes = await artifacts.read(
                artifact_id=captured["workspace"]["manifestRef"].removeprefix("artifact:").lstrip("/"),
                access=access_for_test("operator"),
            )
            assert json.loads(manifest_bytes)["git"]["branch"] == "main"
            saved = await activity.run(
                TemporalCheckpointActivities(
                    artifact_service=artifacts
                ).step_checkpoint_create,
                {
                    "identity": identity.model_dump(by_alias=True),
                    "boundary": "before_publication",
                    "taskInputSnapshotRef": instruction,
                    "workspace": captured["workspace"],
                    "createdAt": datetime.now(UTC).isoformat(),
                    "planRef": await write(b'{"nodes": []}'),
                    "idempotencyKey": "save",
                },
            )
            source.rename(tmp_path / "retired-source")
            saved_ref = "artifact://" + saved["checkpointRef"].removeprefix(
                "artifact:"
            ).lstrip("/")
            _, saved_bytes = await artifacts.read(
                artifact_id=saved_ref.removeprefix("artifact://"),
                access=access_for_test("operator"),
            )
            plan_payload = _exact_plan("gpt-5.4").payload.model_dump(
                mode="json", by_alias=True
            )
            plan_payload["resolvedSkills"] = {}
            plan_payload["model"]["effort"] = "high"
            from moonmind.omnigent.harness_platform.execution_plan import (
                compute_model_config_digest,
            )

            model = plan_payload["model"]
            model["modelConfigDigest"] = compute_model_config_digest(
                qualifiedId=model["qualifiedId"],
                effort="high",
                routeRef=model["routeRef"],
                normalizedOptions=model["normalizedOptions"],
            )
            plan_payload["authority"] = {
                key: "fixture"
                for key in (
                    "authoredRequestRef",
                    "authoredRequestDigest",
                    "repositoryIntentRef",
                    "continuationPolicyRef",
                    "remediationPolicyRef",
                    "checkpointPolicyRef",
                    "publicationPolicyRef",
                    "timingPolicyRef",
                    "failurePolicyRef",
                )
            }
            plan_payload["authority"].update(
                taskInputSnapshotRef=instruction,
                taskInputSnapshotDigest="sha256:"
                + hashlib.sha256(b'"Continue the checkpoint branch"').hexdigest(),
            )
            if model_override:
                # Runtime model overrides already derive an immutable plan in
                # dispatch. Keep the checkpoint's recorded source plan intact.
                from moonmind.omnigent.harness_platform.support import (
                    compute_support_combination_key,
                )

                support = {
                    "omnigentServerBuildRef": "server@1",
                    "omnigentHostBuildRef": "host@1",
                    "harnessImplementationRef": plan_payload[
                        "harnessImplementationRef"
                    ],
                    "vendorRuntimeRefs": [],
                    "agentSourceRef": "agent@1",
                    "materializerRefs": ["opencode-auth-json@1"],
                    "providerCompatibilityClass": "opencode-go",
                    "hostClassRef": plan_payload["hostClassRef"],
                    "architecture": "linux/amd64",
                    "launchPolicyRef": plan_payload["launchPolicyRef"],
                    "modelConfigDigest": model["modelConfigDigest"],
                    "executionRealizerRef": plan_payload["executionRealizerRef"],
                    "requiredCapabilitiesDigest": "sha256:" + "a" * 64,
                }
                plan_payload["supportIdentity"] = support
                plan_payload["supportCombinationKey"] = compute_support_combination_key(
                    support
                )
            plan = create_execution_plan_envelope(plan_payload)
            source_checkpoint = StepExecutionCheckpointModel.model_validate_json(
                _valid_source_checkpoint()[0]
            )
            checkpoint = source_checkpoint.omnigent.model_copy(
                update={
                    "baseline_commit": baseline,
                    "head_commit": head,
                    "workspace_checkpoint_ref": saved_ref,
                    "workspace_checkpoint_digest": "sha256:"
                    + hashlib.sha256(saved_bytes).hexdigest(),
                    "head_ref": await write(json.dumps({"head": head}).encode()),
                    "head_digest": "sha256:"
                    + hashlib.sha256(json.dumps({"head": head}).encode()).hexdigest(),
                    "external_state_ref": await write(b"{}"),
                    "external_state_digest": "sha256:"
                    + hashlib.sha256(b"{}").hexdigest(),
                    "execution_plan_ref": plan.planRef,
                    "runtime_binding_ref": "omnigent-runtime-binding:sha256:"
                    + "a" * 64,
                    "runtime_binding_revision": 1,
                    "runtime_binding_fencing_generation": 1,
                    "step_execution_id": source_step,
                    "boundary": "before_publication",
                }
            )
            source_checkpoint = source_checkpoint.model_copy(
                update={"omnigent": checkpoint, "boundary": "before_publication"}
            )
            branch, turn, binding, source_record, _, profile, policy = (
                _authority_graph()
            )
            if model_override:
                branch.diagnostics["runtimeSelection"]["model"] = "gpt-6.1-sol"
            binding.base_commit = baseline
            binding.repository = "acme/repo"
            turn.instruction_ref = instruction
            turn.source_checkpoint_ref = saved_ref
            turn.source_checkpoint_digest = (
                "sha256:" + hashlib.sha256(saved_bytes).hexdigest()
            )
            command = SimpleNamespace(
                claim_with_repositories=AsyncMock(
                    return_value=SimpleNamespace(outcome=ControlPlaneOutcome.APPLIED)
                ),
                settle=AsyncMock(),
            )
            owner = CheckpointBranchTurnExecutionOwner(
                _Session(),
                principal="service:test",
                client=SimpleNamespace(),
                artifact_service=artifacts,
                turn_command_service=command,
                access=access_for_test("operator"),
            )
            owner._load_graph_authority = AsyncMock(
                return_value=(branch, turn, binding, source_record)
            )
            owner._validate_source_authority = AsyncMock(
                return_value=(source_checkpoint, profile, policy)
            )
            owner._validated_remediation_context_ref = AsyncMock(return_value=None)
            written = {}

            async def write_producer_artifact(*, payload, content_type, kind, **kwargs):
                written[kind] = payload
                return await write(payload, content_type)

            owner._write_artifact = write_producer_artifact

            async def start_with_admitted_restore(**kwargs):
                admitted_request = AgentExecutionRequest.model_validate_json(
                    written["runtime.branch_turn.agent_request.json"]
                )
                await owner._admit_restore_inputs(
                    checkpoint=kwargs["checkpoint"],
                    destination_workflow_id=admitted_request.step_execution.workflow_id,
                    namespace="default",
                )

            owner._start_claimed_turn = start_with_admitted_restore
            monkeypatch.setattr(
                CheckpointBranchService,
                "claim_turn_execution",
                AsyncMock(return_value=turn),
            )
            await owner.launch(
                workflow_id="source-workflow",
                branch_id="branch-1",
                branch_turn_id="turn-1",
                intent={"idempotencyKey": "operator-launch"},
            )
            request = AgentExecutionRequest.model_validate_json(
                written["runtime.branch_turn.agent_request.json"]
            )
            assert request.parameters["executionPlanRef"] == plan.planRef
            assert request.step_execution.workflow_id != checkpoint.workflow_id
            assert request.idempotency_key != checkpoint.idempotency_key
            destination = (
                workspaces
                / "temporal_sandbox"
                / request.workspace_spec["workspaceLocator"]["workspaceId"]
                / "repo"
            )
            # The launch helper admits the real saved-work dependency closure.
            # Start-workflow and plan admission are controlled persistence ports
            # here; delegate the new request's authored attachments explicitly.
            await artifacts.delegate_read_access(
                artifact_ids=[
                    ref.removeprefix("artifact://") for ref in request.input_refs
                ],
                workflow_id=request.step_execution.workflow_id,
                namespace="default",
                access=access_for_test("operator"),
            )
            materializer = OmnigentWorkspaceMaterializer(
                command_runner=None,
                workspace_root=workspaces,
                artifact_service=artifacts,
            )

            # Controlled repository transport: a normal clone yields only the
            # published baseline. Capture, source selection and restore remain real.
            async def clone_baseline(candidate, **kwargs):
                candidate.parent.mkdir(parents=True, exist_ok=True)
                git(tmp_path, "clone", "--quiet", str(origin), str(candidate))
                git(candidate, "reset", "--hard", baseline)

            from moonmind.omnigent.host_services import workspace as workspace_module

            ownership_handoffs = []
            branch_preparations = []
            real_chown_tree = workspace_module.chown_tree

            def observed_chown(path, uid, gid):
                real_chown_tree(path, uid, gid)
                ownership_handoffs.append((path, uid, gid))

            monkeypatch.setattr(workspace_module, "chown_tree", observed_chown)

            async def target_branch(candidate, **kwargs):
                assert ownership_handoffs[-1] == (
                    candidate,
                    kwargs["runtime_uid"],
                    kwargs["runtime_gid"],
                )
                git_owner = (candidate / ".git").stat()
                assert (git_owner.st_uid, git_owner.st_gid) == (
                    kwargs["runtime_uid"],
                    kwargs["runtime_gid"],
                )
                branch_preparations.append(candidate)
                if (
                    git(candidate, "branch", "--show-current")
                    != kwargs["target_branch"]
                ):
                    git(candidate, "checkout", "-qb", kwargs["target_branch"])

            monkeypatch.setattr(
                materializer, "_prepare_sandbox_workspace", clone_baseline
            )
            monkeypatch.setattr(materializer, "_prepare_target_branch", target_branch)
            gateway = SimpleNamespace(admit_execution_plan_inputs=AsyncMock())
            monkeypatch.setattr(
                "moonmind.omnigent.bridge_artifacts.TemporalOmnigentArtifactGateway",
                lambda *args: gateway,
            )

            async def persist_derived(derived):
                assert derived.planRef != plan.planRef
                assert derived.payload.modelConfig.qualifiedId == "gpt-6.1-sol"
                return derived

            plan_store = SimpleNamespace(
                load=AsyncMock(return_value=plan),
                persist=AsyncMock(side_effect=persist_derived),
            )
            monkeypatch.setattr(
                "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
                lambda *args: plan_store,
            )
            monkeypatch.setattr(
                omnigent_activities,
                "_resolve_live_recovery_authority",
                AsyncMock(return_value={"current_credential_generation": 1}),
            )
            received = []

            async def execute(delivered, recorded_plan):
                received.append(delivered)
                if model_override:
                    assert (
                        delivered.step_execution.runtime_selection["executionPlanRef"]
                        == plan.planRef
                    )
                    assert plan.payload.modelConfig.qualifiedId == "gpt-5.4"
                    assert recorded_plan.planRef != plan.planRef
                    assert (
                        recorded_plan.payload.modelConfig.qualifiedId == "gpt-6.1-sol"
                    )
                    assert (
                        delivered.checkpoint_recovery["omnigentCheckpoint"][
                            "executionPlanRef"
                        ]
                        == plan.planRef
                    )
                else:
                    assert recorded_plan.planRef == plan.planRef
                if fault and not model_override:
                    # Even after dispatch admission, the workspace owner must
                    # not restore a different source descriptor across workflows.
                    changed = delivered.model_copy(deep=True)
                    field, value = {
                        "workflow": ("workflowId", "unrelated-workflow"),
                        "run": ("runId", "unrelated-run"),
                        "step": ("stepExecutionId", "unrelated-step"),
                        "digest": ("workspaceCheckpointDigest", "sha256:" + "0" * 64),
                        "plan": (
                            "executionPlanRef",
                            "omnigent-execution-plan:sha256:" + "0" * 64,
                        ),
                    }[fault]
                    changed.checkpoint_recovery["omnigentCheckpoint"][field] = value
                    with pytest.raises(CheckpointRestoreError) as rejected:
                        await materializer.materialize(
                            changed,
                            plan=recorded_plan,
                            runtime_uid=os.getuid(),
                            runtime_gid=os.getgid(),
                        )
                    assert rejected.value.code == "CHECKPOINT_SOURCE_IDENTITY_MISMATCH"
                    return AgentRunResult(summary="cross-workflow restore rejected")
                await materializer.materialize(
                    delivered,
                    plan=recorded_plan,
                    runtime_uid=os.getuid(),
                    runtime_gid=os.getgid(),
                )
                return AgentRunResult(summary="fresh host completed")

            registry = SimpleNamespace(
                require=lambda ref: SimpleNamespace(execute=execute)
            )
            monkeypatch.setattr(
                "moonmind.omnigent.realizers.registry.get_default_registry",
                lambda: registry,
            )
            if fault == "source_param_mismatch":
                request.parameters["executionPlanRef"] = (
                    "omnigent-execution-plan:sha256:" + "0" * 64
                )
                with pytest.raises(
                    ValueError, match="checkpoint execution plan does not match"
                ):
                    await omnigent_activities._omnigent_execute_activity(request)
                assert not received
                plan_store.load.assert_not_awaited()
                plan_store.persist.assert_not_awaited()
                assert not destination.exists()
                return
            result = await omnigent_activities._omnigent_execute_activity(request)
            assert result.failure_class is None, result.metadata.get(
                "dispatchError", result.summary
            )
            assert len(received) == 1
            if fault and not model_override:
                assert not destination.exists()
                return
            if model_override:
                plan_store.persist.assert_awaited_once()
                assert request.parameters["executionPlanRef"] == plan.planRef
                assert (
                    request.checkpoint_recovery["omnigentCheckpoint"][
                        "executionPlanRef"
                    ]
                    == plan.planRef
                )
            assert git(destination, "rev-parse", "HEAD") == head
            assert (destination / "committed.txt").read_text() == "unpublished commit\n"
            assert (destination / "staged.txt").read_text() == "staged work\n"
            assert (destination / "unstaged.txt").read_text() == "unstaged work\n"
            assert not (destination / "deleted.txt").exists()
            assert git(destination, "status", "--porcelain") == expected_status
            assert (
                git(destination, "branch", "--show-current")
                == request.workspace_spec["targetBranch"]
            )
            assert branch_preparations == [destination]
            redelivery = await omnigent_activities._omnigent_execute_activity(request)
            assert redelivery.failure_class is None, redelivery
            assert branch_preparations == [destination]
            assert git(destination, "rev-parse", "HEAD") == head
            assert git(destination, "status", "--porcelain") == expected_status
    finally:
        await engine.dispose()
