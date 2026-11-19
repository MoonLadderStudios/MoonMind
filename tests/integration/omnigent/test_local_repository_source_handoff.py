"""Supported explicit local clone input backed by a real prior shared workspace.

Docker/provider and Temporal transport are controlled by the existing journey;
workspace ownership, Git preparation and artifact persistence are production owners.
"""

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.realizers.turn_delivery import admission_epoch
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.integration.omnigent import test_generic_native_durable_journey as native
from tests.unit.workflows.temporal.workflows.test_run_failed_step_repair import (
    _configure_workflow_runtime,
)

session_factory = native.session_factory
native_journey = native.native_journey
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture(autouse=True)
def three_sequential_hosts(monkeypatch):
    # This journey owns three sequential, cleaned attempts inside one window.
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST", "3")


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize("admission", ["direct", "lease"])
async def test_authored_local_source_clones_real_prior_shared_workspace(
    native_journey, monkeypatch, admission
):
    journey = native_journey
    commits = []

    async def commit_source_turn():
        source = journey.server.workspace
        git(source, "init", "-b", "authored-base")
        git(source, "config", "user.name", "Test operator")
        git(source, "config", "user.email", "operator@example.invalid")
        (source / "tracked.txt").write_text("first\n")
        git(source, "add", "tracked.txt")
        git(source, "commit", "-m", "first")
        commits.append(git(source, "rev-parse", "HEAD"))
        (source / "tracked.txt").write_text("committed second\n")
        git(source, "commit", "-am", "second")
        commits.append(git(source, "rev-parse", "HEAD"))
        git(source, "branch", "other-native-branch")
        (source / "tracked.txt").write_text("dirty excluded\n")
        (source / "untracked.txt").write_text("untracked excluded\n")

    journey.server.on_first_message = commit_source_turn
    result = await journey.realizer.execute(journey.request, journey.plan)
    assert result.failure_class is None, result
    source = journey.server.workspace
    assert source.is_dir()
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
            admission_epoch=admission_epoch(journey.request),
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    host_lease = await journey.services.host_lease_repository.get(binding.hostLeaseRef)
    assert host_lease.status == "cleaned"
    assert not journey.server.host_alive
    locator = journey.request.workspace_spec["workspaceLocator"]
    record = SandboxWorkspaceRecordStore(journey.worker_root).load(
        locator["workspaceId"]
    )
    assert record is not None
    first = journey.request.step_execution
    assert first.workflow_id.startswith("mm:")
    assert record.workflow_id == first.workflow_id
    assert record.step_execution_id == first.step_execution_id
    assert (
        source
        == journey.worker_root
        / "temporal_sandbox"
        / record.workspace_id
        / record.relative_path
    )

    # Run the real manifest writer with only its Temporal activity transport
    # replaced by the existing durable artifact gateway.
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id=first.workflow_id,
            run_id=first.run_id,
            task_queue="mm.workflow",
            search_attributes={},
        ),
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    parent = run_module.MoonMindUserWorkflow()

    async def write_artifact(**kwargs):
        return await journey.gateway.write_bytes(
            request=journey.request,
            name=kwargs["name"],
            payload=json.dumps(kwargs["payload"]).encode(),
            content_type=kwargs["content_type"],
            link_type="output",
        )

    monkeypatch.setattr(parent, "_write_json_artifact", write_artifact)
    now = datetime.now(UTC)
    parent._step_ledger_rows = run_module.build_initial_step_rows(
        ordered_nodes=[{"id": first.logical_step_id, "title": "Produce source"}],
        dependency_map={first.logical_step_id: []},
        updated_at=now,
    )
    parent._mark_step_terminal(
        first.logical_step_id, status="completed", updated_at=now
    )
    manifest = run_module.StepExecutionManifestModel(
        workflowId=first.workflow_id,
        runId=first.run_id,
        logicalStepId=first.logical_step_id,
        executionOrdinal=first.execution_ordinal,
        stepExecutionId=first.step_execution_id,
        reason="initial_execution",
        status="succeeded",
        startedAt=now,
        updatedAt=now,
    )
    manifest_ref = await parent._write_step_execution_manifest(
        first.logical_step_id,
        attempt=1,
        manifest=manifest,
        step_execution_id=first.step_execution_id,
        idempotency_key=journey.request.idempotency_key,
        updated_at=now,
    )
    assert parent._step_ledger_rows[0]["status"] == "completed"
    assert (
        parent._step_ledger_rows[0]["refs"]["latestStepExecutionManifestRef"]
        == manifest_ref
    )
    saved_manifest = json.loads(await journey.gateway.read_bytes(manifest_ref))
    assert saved_manifest["runId"] == first.run_id
    assert saved_manifest["status"] == "succeeded"

    # Literal operator-authored source is an existing canonical input. No new
    # result-output field or fabricated owner record grants its identity.
    request = parent._build_agent_execution_request(
        node_id="clone-next",
        tool_name="codex_cli",
        resolved_skillset_ref=journey.request.resolved_skillset_ref,
        node_inputs={
            "instructions": "Inspect committed history",
            "runtime": {"mode": "codex_cli", "model": journey.server.model},
            "repository": str(source),
            "workspaceSpec": {"workspaceSource": {"kind": "repository"}},
            "startingBranch": "authored-base",
            "publishMode": "none",
        },
        workflow_parameters={},
    )
    request = parent._bind_shared_runtime_context(request)
    assert request.canonical_repository_workspace_handoff
    assert request.workspace_spec["workspaceSource"]["kind"] == "repository"
    assert request.step_execution.workflow_id == first.workflow_id
    assert request.step_execution.run_id == first.run_id
    from copy import deepcopy

    from api_service.api.routers import executions
    from api_service.services.native_pool_execution_plan import (
        _validate_local_source_original_input,
    )

    authored_task = {
        "instructions": "Inspect committed history",
        "runtime": {"mode": "codex_cli", "model": journey.server.model},
        "steps": [
            {
                "id": "clone-next",
                "instructions": "Inspect committed history",
                "type": "skill",
                "skill": {"id": native._DURABLE_SKILL_NAME},
                "runtime": {
                    "mode": "codex_cli",
                    "workspaceSpec": deepcopy(request.workspace_spec),
                },
            }
        ],
    }
    snapshot = executions._build_original_workflow_input_snapshot_payload(
        source_kind="create",
        payload={"workflow": authored_task},
        task_payload=authored_task,
    )
    snapshot_ref = await journey.gateway.write_bytes(
        request=journey.request,
        name="original-local-task.json",
        payload=json.dumps(snapshot).encode(),
        content_type=executions._TASK_INPUT_SNAPSHOT_CONTENT_TYPE,
        link_type="output",
    )
    snapshot = json.loads(await journey.gateway.read_bytes(snapshot_ref))
    assert _validate_local_source_original_input(request, snapshot)
    for changed in ("path", "branch", "pin"):
        tampered = request.model_copy(deep=True)
        if changed == "path":
            tampered.workspace_spec["repository"] = str(source / "another")
        elif changed == "branch":
            tampered.workspace_spec["startingBranch"] = "another"
        else:
            tampered.workspace_spec["workspaceSource"]["repositoryTarget"] = {
                "provider": "git",
                "repository": {"name": str(source)},
                "branch": {"name": "authored-base"},
                "revision": {"kind": "git_commit", "commitSha": "0" * 40},
            }
        with pytest.raises(ValueError, match="frozen|repository_intent_conflict"):
            _validate_local_source_original_input(tampered, snapshot)
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.runtime.local_repository_authority import (
        capture_initial_source,
        resolve_source_authority,
    )
    from tests.artifact_access import access_for_test

    ledger = {
        "workflowId": first.workflow_id,
        "runId": first.run_id,
        "steps": parent._step_ledger_rows,
    }
    client = SimpleNamespace(
        get_workflow_handle=lambda workflow_id, *, run_id: SimpleNamespace(
            query=AsyncMock(return_value=ledger)
        )
    )
    from moonmind.workflows.temporal.runtime.initial_repository_source import (
        source_intent,
    )

    intent = source_intent(request)
    assert intent is not None and intent["sourcePath"] == str(source)
    async with journey.session_factory() as session:
        authority = await resolve_source_authority(
            request=request,
            intent=intent,
            workspace_root=journey.worker_root,
            temporal_client=client,
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session)
            ),
            session=session,
            access=access_for_test(
                "system", admitted_principal=f"workflow:{first.workflow_id}"
            ),
        )
    assert authority is not None
    assert authority["manifestRef"] == manifest_ref
    assert authority["runtimeBindingRef"] == binding.bindingId

    async def resolve_again(candidate=intent):
        async with journey.session_factory() as session:
            return await resolve_source_authority(
                request=request,
                intent=candidate,
                workspace_root=journey.worker_root,
                temporal_client=client,
                artifact_service=TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ),
                session=session,
                access=access_for_test(
                    "system", admitted_principal=f"workflow:{first.workflow_id}"
                ),
            )

    assert await resolve_again({**intent, "sourcePath": str(source / "child")}) is None
    ledger["runId"] = "another-run"
    assert await resolve_again() is None
    ledger["runId"] = first.run_id
    ledger["steps"][0]["status"] = "executing"
    assert await resolve_again() is None
    ledger["steps"][0]["status"] = "completed"
    from api_service.db.models import OmnigentHostLeaseRecordV2

    async with journey.session_factory() as session:
        host = await session.get(OmnigentHostLeaseRecordV2, binding.hostLeaseRef)
        host.status = "running"
        await session.commit()
    assert await resolve_again() is None
    async with journey.session_factory() as session:
        host = await session.get(OmnigentHostLeaseRecordV2, binding.hostLeaseRef)
        host.status = "cleaned"
        await session.commit()

    async def revalidate():
        assert await resolve_again() == authority

    async with journey.session_factory() as session:
        captured = await capture_initial_source(
            request=request,
            intent=intent,
            authority=authority,
            workspace_root=journey.worker_root,
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session)
            ),
            access=access_for_test("system"),
            revalidate_authority=revalidate,
        )
    async with journey.session_factory() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        artifact, body = await service.read(
            artifact_id=captured["artifactRef"].removeprefix("artifact:"),
            access=access_for_test("system"),
        )
        assert artifact.dependency_closure_sha256
        descriptor = json.loads(body)
    assert descriptor["workspace"]["baseCommit"] == commits[-1]
    assert (
        descriptor["gitRefs"]["refs/remotes/origin/authored-base"]["commit"]
        == commits[-1]
    )
    launcher = ManagedRuntimeLauncher(
        ManagedRunStore(journey.worker_root / "managed_runs")
    )
    destination = Path(
        await launcher._prepare_workspace_path(
            run_id="next-native-attempt",
            request=request,
            workspace_path=None,
        )
    )
    assert destination != source
    # AgentRun's managed launch calls this preparation owner with the authored
    # branch: it uses --single-branch, unlike the unrelated sandbox checkout API.
    native_refs = set(
        git(destination, "for-each-ref", "--format=%(refname)").splitlines()
    )
    assert "refs/remotes/origin/other-native-branch" not in native_refs
    assert set(descriptor["gitRefs"]) == native_refs
    assert (destination / "tracked.txt").read_text() == "committed second\n"
    assert not (destination / "untracked.txt").exists()
    assert git(destination, "branch", "--show-current") == "authored-base"
    assert git(destination, "rev-parse", "HEAD") == commits[-1]
    assert git(destination, "rev-parse", "refs/heads/authored-base") == commits[-1]
    assert (
        git(destination, "rev-parse", "refs/remotes/origin/authored-base")
        == commits[-1]
    )
    origin_head = await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-C",
            str(destination),
            "symbolic-ref",
            "-q",
            "refs/remotes/origin/HEAD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # Git versions differ in whether a selected clone creates origin/HEAD.
    # Compare the actual worker toolchain, not an assumed default.
    if origin_head.returncode == 0:
        assert (
            descriptor["gitRefs"]["refs/remotes/origin/HEAD"]["symbolicTarget"]
            == origin_head.stdout.strip()
        )
    else:
        assert origin_head.returncode == 1
        assert "refs/remotes/origin/HEAD" not in descriptor["gitRefs"]
    # Baseline native clone records its source as origin. This is observed
    # native behavior, not publication or reusable local-path authority for a
    # shared restore: that consumer must not copy this configuration.
    assert git(destination, "remote", "get-url", "origin") == str(source)
    assert git(destination, "rev-list", "HEAD").splitlines() == list(reversed(commits))
    assert (source / "tracked.txt").read_text() == "dirty excluded\n"

    lease_result = None
    if admission == "lease":
        lease_result = await _exercise_lease_handoff(
            journey,
            request,
            authored_task,
            ledger,
            monkeypatch,
        )
        captured = lease_result[0]

    # Compile a real second shared execution, then prove that its materializer
    # consumes only the durable receipt after the original source disappears.
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        ProviderProfileSlotLease,
    )
    from api_service.services import omnigent_execution_plan_service as plan_writer
    from moonmind.workflows.temporal.runtime.initial_repository_source import (
        read_initial_source,
    )
    from tests.unit.services.test_omnigent_execution_plan_service import _snapshot

    if lease_result is None:
        async with journey.session_factory() as session:
            artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
            execution_access = access_for_test(f"workflow:{first.workflow_id}")
            assert (
                await read_initial_source(artifacts, captured, access=execution_access)
                == descriptor
            )
            profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
            selected = _snapshot(
                harness="codex-native",
                policy="codex-on-demand@1",
                provider_id=native.PROFILE,
            )
            selected["document"]["skills"] = [native._DURABLE_SKILL_NAME]
            parameters = {
                "targetRuntime": "omnigent",
                "publishMode": "none",
                "model": journey.server.model,
                "workspaceSpec": deepcopy(request.workspace_spec),
                "repository": str(source),
                "startingBranch": "authored-base",
                "workflow": {
                    "instructions": "Inspect committed history",
                    "skill": {"id": native._DURABLE_SKILL_NAME},
                },
            }
            input_ref, input_digest = await plan_writer.persist_json_artifact(
                artifact_service=artifacts,
                principal="system",
                artifact_class="omnigent.task_input_snapshot",
                payload=parameters,
            )
            compiled = await plan_writer.compile_and_persist_execution_plan(
                session_factory=journey.session_factory,
                artifact_service=artifacts,
                principal="system",
                workflow_id=first.workflow_id,
                agent_profile_snapshot=selected,
                provider_profile=profile,
                initial_parameters=parameters,
                authored_request_ref=input_ref,
                authored_request_digest=input_digest,
                task_input_snapshot_ref=input_ref,
                task_input_snapshot_digest=input_digest,
                access=access_for_test(
                    "system", admitted_principal=f"workflow:{first.workflow_id}"
                ),
                _initial_repository_source_snapshot=captured,
            )
    else:
        captured, shared, plan, _retry_export = lease_result
        compiled = SimpleNamespace(
            envelope=plan,
            binding=shared.omnigent_execution_plan,
            resolved_skillset_ref=shared.resolved_skillset_ref,
        )
    next_plan = compiled.envelope
    from api_service.services.native_pool_execution_plan import _shared_request

    next_request = (
        shared
        if lease_result is not None
        else _shared_request(
            request.model_copy(update={"execution_profile_ref": native.PROFILE}),
            snapshot=selected,
            binding=compiled.binding,
            plan=next_plan,
            skill_ref=compiled.resolved_skillset_ref,
        )
    )
    next_request.admitted_provider_capacity = journey.request.admitted_provider_capacity
    next_request.admitted_provider_capacity = (
        next_request.admitted_provider_capacity.model_copy(
            update={
                "lease_owner_id": "workflow-second-grant",
                "execution_plan_ref": next_plan.planRef,
                "step_execution_id": next_request.step_execution.step_execution_id,
                "idempotency_key": next_request.idempotency_key,
            }
        )
    )
    journey.manager.request, journey.manager.plan = next_request, next_plan
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.workflow_id = lease.owner_id = lease.lease_id = "workflow-second-grant"
        lease.execution_plan_ref = next_plan.planRef
        lease.step_execution_id = next_request.step_execution.step_execution_id
        lease.idempotency_key = next_request.idempotency_key
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(
        request=next_request, plan=next_plan
    )
    import shutil

    shutil.rmtree(source)
    if lease_result is not None:
        assert await lease_result[3]() == captured
    observed = {}
    changed_head = []

    async def inspect_restored_turn():
        assert "ADDED_AFTER_PREPARE" not in journey.docker.launch_env
        restored = journey.server.workspace
        observed["commits"] = git(restored, "rev-list", "HEAD").splitlines()
        observed["branch"] = git(restored, "branch", "--show-current")
        observed["tracking"] = git(
            restored, "rev-parse", "refs/remotes/origin/authored-base"
        )
        assert git(restored, "rev-parse", "@{upstream}") == commits[-1]
        assert str(source) not in git(restored, "config", "--local", "--list")
        observed["tracked"] = (restored / "tracked.txt").read_text()
        observed["untracked"] = (restored / "untracked.txt").exists()
        observed["origin"] = (
            await asyncio.to_thread(
                subprocess.run,
                [
                    "git",
                    "-C",
                    str(restored),
                    "config",
                    "--get-all",
                    "remote.origin.url",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        ).returncode
        git(restored, "config", "user.name", "Test operator")
        git(restored, "config", "user.email", "operator@example.invalid")
        git(restored, "checkout", "--orphan", "continued-work")
        (restored / "tracked.txt").write_text("continued committed work\n")
        git(restored, "add", "tracked.txt")
        git(restored, "commit", "-m", "Continued work on independent history")
        changed_head.append(git(restored, "rev-parse", "HEAD"))

    journey.server.on_first_message = inspect_restored_turn
    restored_result = await journey.realizer.execute(next_request, next_plan)
    assert restored_result.failure_class is None, restored_result
    assert observed == {
        "commits": list(reversed(commits)),
        "branch": "authored-base",
        "tracking": commits[-1],
        "tracked": "committed second\n",
        "untracked": False,
        "origin": 1,
    }
    saved = restored_result.metadata["savedWorkspaceCheckpoint"]
    saved_manifest = json.loads(await journey.gateway.read_bytes(saved["manifestRef"]))
    assert saved_manifest["gitHistory"]["requiresSourceObjects"] is False
    assert not saved_manifest["gitHistory"].get("shallowCommits")
    assert saved_manifest["baseCommit"] == changed_head[0]
    predecessor_workspace = journey.server.workspace
    shutil.rmtree(predecessor_workspace)
    from moonmind.omnigent.workspace_sources import replace_repository_workspace_source
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.temporal.step_executions import step_execution_id

    successor_payload = next_request.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    ordinal = next_request.step_execution.execution_ordinal + 1
    successor_id = step_execution_id(
        workflow_id=first.workflow_id,
        run_id=first.run_id,
        logical_step_id=next_request.step_execution.logical_step_id,
        execution_ordinal=ordinal,
    )
    successor_payload["stepExecution"].update(
        executionOrdinal=ordinal, stepExecutionId=successor_id
    )
    successor_payload["idempotencyKey"] = (
        next_request.idempotency_key + ":cold-successor"
    )
    successor_payload["workspaceSpec"] = replace_repository_workspace_source(
        next_request.workspace_spec, saved["checkpointRef"], allow_pinned=True
    )
    successor_payload["workspaceSpec"].pop("workspaceLocator", None)
    successor_payload["admittedProviderCapacity"].update(
        stepExecutionId=successor_id,
        idempotencyKey=successor_payload["idempotencyKey"],
        agentRunRunId="cold-successor-child",
    )
    successor = _shared_request(
        AgentExecutionRequest.model_validate(successor_payload),
        snapshot=json.loads(
            await journey.gateway.read_admitted_input(
                next_plan.payload.agentProfileSnapshotRef, request=next_request
            )
        ),
        binding=compiled.binding,
        plan=next_plan,
        skill_ref=compiled.resolved_skillset_ref,
    )
    journey.manager.request = successor
    async with journey.session_factory() as session:
        lease = await session.get(ProviderProfileSlotLease, journey.provider_lease_id)
        lease.step_execution_id = successor_id
        lease.idempotency_key = successor.idempotency_key
        await session.commit()
    await journey.gateway.admit_execution_plan_inputs(request=successor, plan=next_plan)
    cold = {}

    async def inspect_cold_turn():
        assert "ADDED_AFTER_PREPARE" not in journey.docker.launch_env
        restored = journey.server.workspace
        cold["head"] = git(restored, "rev-parse", "HEAD")
        cold["branch"] = git(restored, "branch", "--show-current")
        cold["tracking"] = git(
            restored, "rev-parse", "refs/remotes/origin/authored-base"
        )
        cold["original_history"] = git(
            restored, "rev-list", "refs/remotes/origin/authored-base"
        ).splitlines()
        cold["bytes"] = (restored / "tracked.txt").read_text()
        assert str(source) not in git(restored, "config", "--local", "--list")

    journey.server.on_first_message = inspect_cold_turn
    successor_result = await journey.realizer.execute(successor, next_plan)
    assert successor_result.failure_class is None, successor_result
    assert cold == {
        "head": changed_head[0],
        "branch": "continued-work",
        "tracking": commits[-1],
        "original_history": list(reversed(commits)),
        "bytes": "continued committed work\n",
    }


async def _exercise_lease_handoff(journey, request, task, ledger, monkeypatch):
    """Use the existing canonical execution and lease writers, not invented rows."""
    from contextlib import asynccontextmanager
    from copy import deepcopy
    from datetime import timedelta

    from sqlalchemy import func, select

    from api_service.api.routers import executions
    from api_service.db.models import (
        ManagedAgentProviderProfile,
        OmnigentExecutionPlanRecord,
        ProviderProfileSlotLease,
    )
    from api_service.services import native_pool_execution_plan as admission
    from moonmind.config.settings import settings
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.security.operator_admission import OperatorAdmission
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactActivities,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from moonmind.workflows.temporal.service import TemporalExecutionService

    identity = request.step_execution
    owner, owner_run = "workflow-second-grant", "next-agent-run"
    description = SimpleNamespace(
        raw_description=SimpleNamespace(
            workflow_execution_info=SimpleNamespace(
                execution=SimpleNamespace(workflow_id=owner, run_id=owner_run),
                status=1,
                type=SimpleNamespace(name="MoonMind.AgentRun"),
                parent_execution=SimpleNamespace(
                    workflow_id=identity.workflow_id, run_id=identity.run_id
                ),
            )
        )
    )
    transport = SimpleNamespace(
        start_workflow=AsyncMock(return_value=SimpleNamespace(run_id=identity.run_id)),
        describe_workflow=AsyncMock(return_value=description),
        namespace_retention_seconds=AsyncMock(return_value=86400),
        get_workflow_handle=lambda workflow_id, *, run_id: SimpleNamespace(
            query=AsyncMock(return_value=ledger)
        ),
    )
    parameters = {
        "workflow": deepcopy(task),
        "targetRuntime": "codex_cli",
        "publishMode": "none",
        "profileSelector": {"tagsAll": ["selected-pool"]},
        "resolvedSkillsetRef": journey.request.resolved_skillset_ref,
    }
    async with journey.session_factory() as session:
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.tags = ["selected-pool"]
        await session.commit()
        from api_service.services.omnigent_agent_bootstrap_service import (
            reconcile_bootstrap_agent_profile,
        )
        from moonmind.omnigent.settings import resolved_server_url
        from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

        inventory = await OmnigentHttpClient(
            base_url=resolved_server_url()
        ).list_agents()
        await reconcile_bootstrap_agent_profile(session, inventory=inventory)
        service = TemporalExecutionService(session, client_adapter=transport)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        monkeypatch.setattr(
            service,
            "_sync_projection_best_effort",
            AsyncMock(side_effect=lambda record: record),
        )
        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            title="Local source handoff",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=parameters,
            idempotency_key="local-source-workflow",
            _workflow_id=identity.workflow_id,
            _run_id=identity.run_id,
        )
        await executions._persist_original_workflow_input_snapshot(
            session=session,
            record=record,
            user=OperatorAdmission(via="loopback"),
            payload=parameters,
            task_payload=task,
            source_kind="create",
        )
        await session.commit()

    @asynccontextmanager
    async def database():
        async with journey.session_factory() as session:
            yield session

    monkeypatch.setattr("api_service.db.base.get_async_session_context", database)
    monkeypatch.setattr(settings.workflow, "workspace_root", journey.worker_root)
    activities = TemporalArtifactActivities(None)
    released = await activities.provider_profile_sync_slot_leases(
        runtime_id="codex_cli",
        action="release_one",
        leases=[
            {
                "lease_id": journey.request.admitted_provider_capacity.lease_owner_id,
                "fencing_generation": 7,
            }
        ],
    )
    assert released["released"]
    granted = await activities.provider_profile_sync_slot_leases(
        runtime_id="codex_cli",
        action="grant",
        leases=[
            {
                "lease_id": owner,
                "workflow_id": owner,
                "owner_id": owner,
                "profile_id": native.PROFILE,
                "owner_kind": "workflow",
                "ownerIsWorkflow": True,
                "purpose": "execution_direct",
                "compatibility_class": "shared_execution",
                "fencing_generation": 7,
                "scope_generation": 3,
                "capacity_scope_ref": f"provider-profile:{native.PROFILE}",
                "credential_generation": 3,
                "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "safe_metadata": {"runId": owner_run},
            }
        ],
    )
    assert granted.get("granted") is True, granted
    selected = request.model_copy(update={"execution_profile_ref": native.PROFILE})
    payload = {
        "owner_id": owner,
        "profile_id": native.PROFILE,
        "runId": owner_run,
        "fencing_generation": 7,
        "originalProfileRef": None,
        "localSourceHandoff": True,
        "request": selected.model_dump(mode="json", by_alias=True),
    }
    async with journey.session_factory() as session:
        before = await session.scalar(
            select(func.count(OmnigentExecutionPlanRecord.plan_ref))
        )
        first = await admission.prepare_selected_pool_plan(
            session, runtime_id="codex_cli", payload=payload, client=transport
        )
        assert first["disposition"] == "source_export_required", first
        assert (
            await session.scalar(
                select(func.count(OmnigentExecutionPlanRecord.plan_ref))
            )
            == before
        )
        lease = (
            await session.scalars(
                select(ProviderProfileSlotLease).where(
                    ProviderProfileSlotLease.lease_id == owner
                )
            )
        ).one()
        assert lease.execution_plan_ref is None
        assert lease.safe_metadata_json["initialRepositorySource"]["candidate"]
        journey.provider_lease_id = lease.id
        profile = await session.get(ManagedAgentProviderProfile, native.PROFILE)
        profile.default_model = "edited-after-source-grant"
        profile.env_template = {"ADDED_AFTER_PREPARE": "must-not-reach-this-attempt"}
        await session.commit()
    async with journey.session_factory() as session:
        captured = await admission.export_selected_pool_source(
            session_factory=journey.session_factory,
            runtime_id="codex_cli",
            payload=payload,
            workspace_root=journey.worker_root,
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session)
            ),
            client=transport,
        )
    async with journey.session_factory() as session:
        final = await admission.prepare_selected_pool_plan(
            session, runtime_id="codex_cli", payload=payload, client=transport
        )
        assert final["disposition"] == "shared", final
        await session.commit()
        repeated = await admission.prepare_selected_pool_plan(
            session, runtime_id="codex_cli", payload=payload, client=transport
        )
        assert repeated == final
        shared = AgentExecutionRequest.model_validate(final["request"])
        plan = await SessionExecutionPlanStore(session).load(
            shared.omnigent_execution_plan.plan_ref
        )
        assert plan.payload.modelConfig.qualifiedId != "edited-after-source-grant"
        assert (
            await session.scalar(
                select(func.count(OmnigentExecutionPlanRecord.plan_ref))
            )
            == before + 1
        )

    async def retry_export():
        async with journey.session_factory() as session:
            return await admission.export_selected_pool_source(
                session_factory=journey.session_factory,
                runtime_id="codex_cli",
                payload=payload,
                workspace_root=journey.worker_root,
                artifact_service=TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ),
                client=transport,
            )

    return captured, shared, plan, retry_export
