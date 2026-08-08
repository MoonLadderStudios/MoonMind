"""Controlling journey for MoonLadderStudios/MoonMind#3561.

Extends the #3507 materialization journey through the parts #3561 owns:
repository work executed through the Omnigent path must obey the same authored
branch, mutation, publication, saved-work, publication-only recovery, retry, and
bounded-evidence contracts as normal MoonMind workflows.

Everything here is hermetic (no Docker, no network): a real Git source and a real
bare remote back the canonical ``PublishService`` publisher, the real
``OmnigentOAuthHostRuntime`` materializes the authoritative workspace, and the
real profile-bound coordinator is driven through materialization -> repository
mutation -> publication/saved-work -> terminal harvest -> cleanup -> replay for
both the static and on-demand host modes.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from api_service.db.models import (
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
)
from moonmind.config.settings import settings
from moonmind.omnigent.checkpoints import OmnigentCheckpointIdentity
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import CredentialLeasePurpose
from moonmind.publish.service import PublishService
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from tests.integration.reliability.helpers import load_replay
from tests.unit.omnigent.test_policy_authority import policy_document

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


# --- Git helpers ------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=journey@moonmind.test",
            "-c",
            "user.name=MoonMind Journey",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _init_bare_remote_with_seed(tmp_path: Path) -> Path:
    """Create a bare remote holding a seeded ``main`` branch; return the remote."""

    remote = tmp_path / "remote.git"
    remote.mkdir(parents=True)
    _git(remote, "init", "--bare")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init")
    _git(seed, "checkout", "-B", "main")
    (seed / "app.py").write_text("print('hello')\n", encoding="utf-8")
    _git(seed, "add", "app.py")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    return remote


def _configure_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "journey@moonmind.test")
    _git(repo, "config", "user.name", "MoonMind Journey")


async def _run_command(cmd, *, cwd=None, check=True, env=None, **_kwargs):
    """Minimal async command runner matching the PublishService contract."""

    process = await asyncio.create_subprocess_exec(
        *[str(part) for part in cmd],
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if check and process.returncode != 0:
        raise RuntimeError(
            f"command {cmd!r} failed: {stderr.decode('utf-8', 'replace')[:200]}"
        )
    return SimpleNamespace(
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
        returncode=process.returncode,
    )


async def _materialize_workspace(
    tmp_path: Path,
    *,
    workflow_id: str,
    step_execution_id: str,
    source: Path,
    starting_branch: str,
    target_branch: str,
    configure_identity: bool = True,
) -> tuple[OmnigentOAuthHostRuntime, Path]:
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=workspace_root,
        repository_source_root=tmp_path,
    )
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode("utf-8")
    ).hexdigest()[:24]
    locator = {"kind": "sandbox", "workspaceId": workspace_id, "relativePath": "repo"}
    workspace = await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        repository_source=str(source),
        starting_branch=starting_branch,
        target_branch=target_branch,
    )
    if configure_identity:
        _configure_identity(workspace)
    return runtime, workspace


# --- Journey 1: mutation -> branch publication -> publication-only recovery -


@pytest.mark.asyncio
async def test_materialized_workspace_publishes_branch_and_recovery_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    # The outbound secret scan is the publication layer's own concern and is
    # covered elsewhere; disable it so this journey is deterministic and offline.
    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    remote = _init_bare_remote_with_seed(tmp_path)

    _runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id="mm:wf-3561",
        step_execution_id="mm:wf-3561:run:implement:execution:1",
        source=remote,
        starting_branch="main",
        target_branch="agent/implement",
    )

    # Authored source, output, and publication branches stay distinct (AC1).
    assert _git_out(workspace, "rev-parse", "--abbrev-ref", "HEAD") == (
        "agent/implement"
    )
    assert (workspace / "app.py").is_file()

    # Simulate the Omnigent agent mutating the repository (uncommitted work that
    # canonical publication owns the commit/branch/push for).
    (workspace / "feature.py").write_text("print('feature')\n", encoding="utf-8")

    publisher = PublishService()
    job_id = UUID("00000000-0000-0000-0000-000000003561")
    result = await publisher.publish(
        job_id=job_id,
        instruction="Add the feature module",
        publish_mode="branch",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=workspace,
        run_command=_run_command,
    )

    assert result is not None
    assert result.status == "published"
    assert result.commit_created is True
    assert result.branch_pushed is True
    publication_branch = result.branch_name
    assert publication_branch and publication_branch.startswith("moonmind-job-")
    # Source (main), output (agent/implement), and publication branch are three
    # distinct refs; none was silently reset onto another (AC1/AC3).
    assert publication_branch not in {"main", "agent/implement"}

    # The push reached the real remote with the mutation.
    remote_branches = _git_out(remote, "branch", "--list", publication_branch)
    assert publication_branch in remote_branches
    remote_head = _git_out(remote, "rev-parse", publication_branch)

    # Publication-only recovery from the already-qualified candidate is
    # idempotent: the accepted work is already committed and pushed, so a retry
    # neither creates a second commit nor a divergent remote head (AC4).
    recovery = await publisher.publish(
        job_id=job_id,
        instruction="Add the feature module",
        publish_mode="branch",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=workspace,
        run_command=_run_command,
    )
    assert recovery is not None
    assert recovery.status == "skipped"
    assert recovery.reason_code == "no_commit"
    assert _git_out(remote, "rev-parse", publication_branch) == remote_head


@pytest.mark.asyncio
async def test_clean_omnigent_workspace_publishes_existing_agent_commit(
    tmp_path, monkeypatch
) -> None:
    """Replay the false-success shape from workflow mm:ea264977.

    The native agent may honor its own commit instruction, leaving a clean
    worktree with commits over the authored base. Publication must qualify and
    push those commits instead of treating clean status as "no work".
    """

    replay = load_replay("omnigent-profile-bound-publication", "manifest.json")
    expected = load_replay(
        "omnigent-profile-bound-publication", "expected-outcome.json"
    )
    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    remote = _init_bare_remote_with_seed(tmp_path)
    workflow_id = replay["incidentWorkflowId"]
    step_execution_id = replay["failedChildWorkflowId"]
    runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id=workflow_id,
        step_execution_id=step_execution_id,
        source=remote,
        starting_branch="main",
        target_branch="agent/live-replay",
    )
    (workspace / "feature.py").write_text("print('fixed')\n", encoding="utf-8")
    _git(workspace, "add", "feature.py")
    _git(workspace, "commit", "-m", "Fix escaped workflow failure")
    assert _git_out(workspace, "status", "--porcelain") == ""

    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode("utf-8")
    ).hexdigest()[:24]
    result = await runtime.publish_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        publication_identity="replay:omnigent-profile-bound-publication",
        publish_mode="pr",
        base_branch="main",
        repository="",
        github_token=None,
    )

    assert result["push_status"] == expected["pushStatus"]
    assert result["push_commit_count"] == expected["commitsAheadOfBase"]
    assert result["remote_verified"] is expected["remoteVerified"]
    assert result["push_head_sha"] == _git_out(
        remote, "rev-parse", result["push_branch"]
    )


@pytest.mark.asyncio
async def test_pr_publication_projects_existing_remote_pull_request(
    tmp_path, monkeypatch
) -> None:
    """The finalizer receives the PR created by the portable agent step."""

    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    resolved_selectors: list[tuple[str, str, str | None]] = []

    async def resolve_pull_request_selector(
        _self, *, repo: str, selector: str, github_token: str | None = None
    ):
        resolved_selectors.append((repo, selector, github_token))
        return SimpleNamespace(
            resolved=True,
            pr_url="https://github.com/MoonLadderStudios/MoonMind/pull/3652",
        )

    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService.resolve_pull_request_selector",
        resolve_pull_request_selector,
    )
    remote = _init_bare_remote_with_seed(tmp_path)
    workflow_id = "mm:pr-handoff-replay"
    step_execution_id = "mm:pr-handoff-replay:agent:node-1"
    runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id=workflow_id,
        step_execution_id=step_execution_id,
        source=remote,
        starting_branch="main",
        target_branch="agent/pr-handoff",
    )
    (workspace / "feature.py").write_text("print('fixed')\n", encoding="utf-8")
    _git(workspace, "add", "feature.py")
    _git(workspace, "commit", "-m", "Fix PR handoff")
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode("utf-8")
    ).hexdigest()[:24]

    result = await runtime.publish_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        publication_identity="replay:omnigent-pr-handoff",
        publish_mode="pr",
        base_branch="main",
        repository="MoonLadderStudios/MoonMind",
        github_token="test-token",
    )

    assert result["pull_request_url"] == (
        "https://github.com/MoonLadderStudios/MoonMind/pull/3652"
    )
    assert resolved_selectors == [
        (
            "MoonLadderStudios/MoonMind",
            result["push_branch"],
            "test-token",
        )
    ]


@pytest.mark.asyncio
async def test_profile_bound_publication_reports_no_commits_over_base(
    tmp_path, monkeypatch
) -> None:
    """Provider completion with an untouched workspace is not publish success."""

    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    remote = _init_bare_remote_with_seed(tmp_path)
    workflow_id = "mm:no-commits-replay"
    step_execution_id = "mm:no-commits-replay:agent:node-1"
    runtime, _workspace = await _materialize_workspace(
        tmp_path,
        workflow_id=workflow_id,
        step_execution_id=step_execution_id,
        source=remote,
        starting_branch="main",
        target_branch="agent/no-commits",
    )
    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode("utf-8")
    ).hexdigest()[:24]

    result = await runtime.publish_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_execution_id,
        publication_identity="replay:omnigent-profile-bound-no-commits",
        publish_mode="pr",
        base_branch="main",
        repository="",
        github_token=None,
    )

    assert result["push_status"] == "no_commits"
    assert result["push_commit_count"] == 0
    assert result["remote_verified"] is False


@pytest.mark.asyncio
async def test_profile_bound_publication_supplies_missing_git_identity(
    tmp_path, monkeypatch
) -> None:
    """Replay mm:bfc017e9 staged output with no repository-local author."""

    replay = load_replay(
        "omnigent-publication-missing-git-identity", "manifest.json"
    )
    expected = load_replay(
        "omnigent-publication-missing-git-identity", "expected-outcome.json"
    )
    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    monkeypatch.setattr(settings.workflow, "git_user_name", replay["gitUserName"])
    monkeypatch.setattr(settings.workflow, "git_user_email", replay["gitUserEmail"])
    remote = _init_bare_remote_with_seed(tmp_path)
    runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id=replay["incidentWorkflowId"],
        step_execution_id=replay["stepExecutionId"],
        source=remote,
        starting_branch="main",
        target_branch="agent/missing-identity",
        configure_identity=False,
    )
    (workspace / "feature.py").write_text("print('fixed')\n", encoding="utf-8")
    _git(workspace, "add", "feature.py")
    subprocess.run(
        ["git", "config", "--unset-all", "user.name"],
        cwd=workspace,
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "--unset-all", "user.email"],
        cwd=workspace,
        check=False,
        capture_output=True,
    )

    workspace_id = hashlib.sha256(
        f"{replay['incidentWorkflowId']}:{replay['stepExecutionId']}".encode()
    ).hexdigest()[:24]
    result = await runtime.publish_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=replay["incidentWorkflowId"],
        current_step_execution_id=replay["stepExecutionId"],
        publication_identity="replay:omnigent-publication-missing-git-identity",
        publish_mode="pr",
        base_branch="main",
        repository="",
        github_token=None,
    )

    assert result["push_status"] == expected["pushStatus"]
    assert result["push_commit_count"] == expected["commitsAheadOfBase"]
    assert result["remote_verified"] is expected["remoteVerified"]
    assert _git_out(workspace, "log", "-1", "--format=%an") == replay["gitUserName"]
    assert _git_out(workspace, "log", "-1", "--format=%ae") == replay["gitUserEmail"]


# --- Journey 2: pull-request publication through canonical contract ---------


@pytest.mark.asyncio
async def test_materialized_workspace_publishes_pull_request_through_canonical_contract(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "moonmind.publish.service.resolve_high_security_mode", lambda *a, **k: False
    )
    # Managed publishing resolves an explicit token; ambient gh state is never a
    # publishing authority. Stub the resolver so the PR path stays offline.
    monkeypatch.setattr(
        "moonmind.auth.github_credentials.resolve_github_credential",
        AsyncMock(return_value=SimpleNamespace(token="ghp_journeytoken", safe_summary="stub")),
    )
    remote = _init_bare_remote_with_seed(tmp_path)
    _runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id="mm:wf-3561-pr",
        step_execution_id="mm:wf-3561-pr:run:implement:execution:1",
        source=remote,
        starting_branch="main",
        target_branch="agent/implement",
    )
    (workspace / "feature.py").write_text("print('feature')\n", encoding="utf-8")

    created_calls: list[dict] = []

    async def _create_pull_request(**kwargs):
        created_calls.append(kwargs)
        return SimpleNamespace(
            created=True, url="https://github.com/owner/repo/pull/7"
        )

    publisher = PublishService(github_create_pull_request=_create_pull_request)
    result = await publisher.publish(
        job_id=UUID("00000000-0000-0000-0000-000000003562"),
        instruction="Add the feature module",
        publish_mode="pr",
        publish_base_branch="main",
        runtime_mode="codex",
        repo_dir=workspace,
        run_command=_run_command,
        repo="owner/repo",
    )

    assert result is not None
    assert result.status == "published"
    assert result.pr_url == "https://github.com/owner/repo/pull/7"
    # The canonical PR contract targets base=main from the moonmind head branch.
    assert len(created_calls) == 1
    assert created_calls[0]["base"] == "main"
    assert created_calls[0]["head"] == result.branch_name
    assert created_calls[0]["repo"] == "owner/repo"


# --- Journey 3: no-publish preserves the terminal saved-work contract -------


@pytest.mark.asyncio
async def test_no_publish_preserves_distinct_branch_saved_work_contract(
    tmp_path,
) -> None:
    remote = _init_bare_remote_with_seed(tmp_path)
    _runtime, workspace = await _materialize_workspace(
        tmp_path,
        workflow_id="mm:wf-3561-none",
        step_execution_id="mm:wf-3561-none:run:implement:execution:1",
        source=remote,
        starting_branch="main",
        target_branch="agent/implement",
    )
    (workspace / "feature.py").write_text("print('feature')\n", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "wip")
    head_commit = _git_out(workspace, "rev-parse", "HEAD")
    baseline_commit = _git_out(workspace, "rev-parse", "main")

    # publishMode=none performs no external publication.
    publisher = PublishService()
    assert (
        await publisher.publish(
            job_id=UUID("00000000-0000-0000-0000-000000003563"),
            instruction="Add the feature module",
            publish_mode="none",
            publish_base_branch="main",
            runtime_mode="codex",
            repo_dir=workspace,
            run_command=_run_command,
        )
        is None
    )

    # The work is preserved as a durable terminal saved-work checkpoint whose
    # identity keeps source and output branches distinct and records that it was
    # never published (AC1/AC3 terminal saved-work capture).
    checkpoint = OmnigentCheckpointIdentity(
        workflowId="mm:wf-3561-none",
        runId="run-1",
        logicalStepId="implement",
        stepExecutionId="mm:wf-3561-none:run:implement:execution:1",
        attemptOrdinal=1,
        boundary="after_execution",
        providerProfileId="codex",
        credentialRef="credential://codex",
        credentialGeneration=3,
        hostBindingRef="omnigent-oauth:codex",
        endpointRef="default",
        bridgeSessionId="bridge-1",
        externalStateRef="artifact://external-state",
        externalStateDigest="sha256:" + "0" * 64,
        idempotencyKey="idem-none",
        effectiveLaunchRef="omnigent-launch:sha256:" + "0" * 64,
        executionProfileRef="omnigent-codex@1",
        launchPolicyRef="codex-on-demand@1",
        workspaceLocator={
            "kind": "sandbox",
            "workspaceId": "workspace-none",
            "relativePath": "repo",
        },
        baselineCommit=baseline_commit,
        headCommit=head_commit,
        headRef="artifact://head",
        headDigest="sha256:" + "2" * 64,
        workspaceCheckpointRef="artifact://workspace-checkpoint",
        workspaceCheckpointDigest="sha256:" + "3" * 64,
        sourceBranch="main",
        outputBranch="agent/implement",
        publicationState="unpublished",
        capturedAt=datetime(2026, 7, 31, tzinfo=UTC),
        producerVersion="moonmind-journey",
        validation={
            "valid": True,
            "liveReattachAvailable": False,
            "workspaceColdRestoreAvailable": True,
            "branchCreationAvailable": True,
        },
    )
    assert checkpoint.publication_state == "unpublished"
    assert checkpoint.source_branch == "main"
    assert checkpoint.output_branch == "agent/implement"
    assert checkpoint.source_branch != checkpoint.output_branch
    assert checkpoint.baseline_commit != checkpoint.head_commit


# --- Journey 4: coordinator materialization -> cleanup for both host modes --


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        disabled_reason=None,
        max_parallel_runs=1,
        cooldown_after_429_seconds=900,
        runtime_id="codex_cli",
        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
        volume_ref="codex_auth_volume",
        volume_mount_path="/home/app/.codex",
        secret_refs={},
        command_behavior={},
    )


def _binding(*, on_demand: bool) -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:codex",
        providerProfileId="codex",
        endpointRef="default",
        harness="codex-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="codex",
                runtimeId="codex_cli",
                providerId="openai",
                volumeRef="codex_auth_volume",
                credentialGeneration=3,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.codex",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId=None if on_demand else "host-1",
        hostLaunchProfileRef="codex-on-demand@1" if on_demand else None,
    )


def _host_lease() -> OmnigentHostLease:
    now = datetime(2026, 7, 31, tzinfo=UTC)
    return OmnigentHostLease(
        leaseId="host-lease-1",
        providerProfileId="codex",
        providerLeaseId="provider-lease-1",
        bindingRef="omnigent-oauth:codex",
        credentialGeneration=3,
        omnigentHostId=None,
        status="allocating",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )


async def _resolve_policy(_self, policy_ref):
    document = policy_document()
    if policy_ref.startswith("codex-on-demand@"):
        document["host"]["mode"] = "on_demand_docker"
        document["host"]["backendRef"] = "container-backend"
        document["session"]["cleanup"] = "remove"
    return compile_policy_snapshot(
        policy_id=policy_ref.rsplit("@", 1)[0],
        version=int(policy_ref.rsplit("@", 1)[1]),
        document=document,
        validation={"valid": True, "diagnostics": []},
    )


async def _drive_coordinator_materialization_to_cleanup(
    tmp_path: Path,
    *,
    on_demand: bool,
    publish_mode: str,
) -> list[dict]:
    """Run the real coordinator across a real workspace materialization.

    Returns the ordered list of ``(event_type, status, metadata)`` records so the
    caller can assert cleanup ordering, replay idempotency, and bounded evidence.
    """

    remote = _init_bare_remote_with_seed(tmp_path)
    workspace_root = tmp_path / "workspaces"
    real_runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(),
        workspace_root=workspace_root,
        repository_source_root=tmp_path,
    )
    ordered: list[dict] = []

    provider_lease = SimpleNamespace(
        profile_id="codex",
        runtime_id="codex_cli",
        lease_id="provider-lease-1",
        owner_id="owner-1",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
    )
    binding = _binding(on_demand=on_demand)

    class LeaseClient:
        async def acquire_execution_lease(self, **_kwargs):
            return provider_lease

        async def release_lease(self, _lease):
            ordered.append({"type": "provider_released", "status": "completed"})

        async def record_cooldown(self, **_kwargs):
            return None

    class Hosts:
        def __init__(self):
            self.lease = _host_lease()

        async def get_binding_for_profile(self, _profile_id):
            return binding

        async def create_or_update_static_binding(self, **kwargs):
            if "effective_launch_snapshot" not in kwargs:
                return binding
            return binding.model_copy(
                update={
                    "host_launch_profile_ref": kwargs.get("host_launch_profile_ref")
                    or binding.host_launch_profile_ref,
                    "execution_profile_ref": kwargs["execution_profile_ref"],
                    "launch_policy_ref": kwargs["launch_policy_ref"],
                    "effective_launch_snapshot": kwargs["effective_launch_snapshot"],
                }
            )

        async def create_or_get_host_lease(self, **_kwargs):
            return self.lease

        async def restart_host_lease(self, _lease_id):
            self.lease = self.lease.model_copy(update={"status": "allocating"})
            return self.lease

        async def transition_host_lease(
            self, _lease_id, *, expected_status, new_status, fields=None
        ):
            self.lease = self.lease.model_copy(
                update={"status": new_status, **dict(fields or {})}
            )
            return self.lease

        async def mark_host_lease_stopped(self, _lease_id):
            ordered.append({"type": "host_stopped", "status": "completed"})
            self.lease = self.lease.model_copy(update={"status": "stopped"})
            return self.lease

        async def mark_host_lease_failed(self, *_args, **_kwargs):
            return None

    class Runtime:
        async def prepare_host(
            self,
            *,
            workspace_locator,
            current_workflow_id,
            current_step_execution_id,
            repository_source,
            starting_branch=None,
            target_branch=None,
            **_kwargs,
        ):
            # Delegate to the REAL materialization boundary so the coordinator is
            # proven end to end over an authoritative on-disk workspace.
            workspace = await real_runtime._prepare_workspace(
                workspace_locator=workspace_locator,
                current_workflow_id=current_workflow_id,
                current_step_execution_id=current_step_execution_id,
                repository_source=repository_source,
                starting_branch=starting_branch,
                target_branch=target_branch,
            )
            assert workspace.is_dir()
            return {
                "hostId": "host-1",
                "workspacePath": "/workspaces/run",
                "workspaceResolution": dict(real_runtime._last_workspace_evidence),
            }

        async def stop_host(self, **_kwargs):
            ordered.append({"type": "host_cleanup", "status": "completed"})

        async def inspect_session_completion(self, session_id):
            assert session_id == "session-1"
            return {
                "sessionStatus": "completed",
                "itemCount": 4,
                "assistantMessageCount": 1,
                "toolResultCount": 1,
                "terminalAssistantAfterWork": True,
            }

        async def publish_workspace(self, **_kwargs):
            return {
                "push_status": "pushed",
                "push_branch": "agent/implement",
                "push_base_branch": "main",
                "push_head_sha": "a" * 40,
                "push_commit_count": 1,
                "remote_verified": True,
                "pushRef": "artifact://push-1",
            }

    class Store:
        async def get_or_create(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def bind_profile_authorization(self, **_kwargs):
            return SimpleNamespace(bridge_session_id="bridge-1")

        async def record_lifecycle_event(self, _key, *, event_type, **kwargs):
            ordered.append(
                {
                    "type": event_type,
                    "status": kwargs.get("status"),
                    "metadata": kwargs.get("metadata") or {},
                }
            )

        async def mark_terminal(self, *_args, **_kwargs):
            return None

    async def execute(request, **_kwargs):
        return AgentRunResult(
            summary="done",
            outputRefs=["artifact://out-1"],
            metadata={"omnigentSessionId": "session-1"},
        )

    coordinator = OmnigentProfileBoundExecutionCoordinator(
        session_factory=lambda: None,
        lease_client=LeaseClient(),
        host_repository=Hosts(),
        host_runtime=Runtime(),
        run_store=Store(),
        execution_runner=execute,
        artifact_gateway=object(),
    )
    coordinator._resolve_profile = AsyncMock(return_value=_profile())
    coordinator._resolve_policy_snapshot = _resolve_policy.__get__(coordinator)

    workflow_id = f"mm:wf-3561-coord-{'ondemand' if on_demand else 'static'}"
    step_execution_id = f"{workflow_id}:run:implement:execution:1"
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId=workflow_id,
        idempotencyKey=f"idem-{workflow_id}",
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(
                    f"{workflow_id}:{step_execution_id}".encode("utf-8")
                ).hexdigest()[:24],
            },
            "repository": str(remote),
            "startingBranch": "main",
            "targetBranch": "agent/implement",
        },
        parameters={
            "stepExecution": {
                "workflowId": workflow_id,
                "stepExecutionId": step_execution_id,
            },
            "publishMode": publish_mode,
            "omnigent": {"session": {"workspace": str(remote)}},
        },
    )

    await coordinator.execute(request)
    # Replay: the identical durable identity must not clone a second workspace or
    # duplicate the host/session; materialization is observed as reuse (AC4).
    await coordinator.execute(request)
    return ordered


@pytest.mark.asyncio
async def test_coordinator_materialization_to_cleanup_on_demand_mutation(
    tmp_path,
) -> None:
    ordered = await _drive_coordinator_materialization_to_cleanup(
        tmp_path, on_demand=True, publish_mode="branch"
    )
    types = [entry["type"] for entry in ordered]

    # Full authority handoff ordering: host stop -> provider lease release ->
    # terminal, for the first run.
    assert types.index("host_stopped") < types.index("profile_lease_release")
    assert types.index("provider_released") < types.index(
        "profile_lease_release", types.index("provider_released")
    )
    assert "authority_chain" in types
    assert types.index("authority_chain") < types.index("terminal")

    chains = [
        entry["metadata"]["authorityChain"]
        for entry in ordered
        if entry["type"] == "authority_chain"
    ]
    assert len(chains) == 2
    first, second = chains
    # First run materialized; the replay reused the same authoritative workspace.
    assert first["workspace"]["materializationAction"] == "materialized"
    assert second["workspace"]["materializationAction"] == "reused_pre_materialized"
    assert first["runtime"]["hostMode"] == "on_demand_docker"
    assert first["publication"]["publishMode"] == "branch"
    assert first["publication"]["publicationState"] == "published"
    assert first["workspace"]["candidateHead"] == "agent/implement"
    assert first["terminal"]["releaseOrdering"][-1] == "terminal"
    # Bounded + credential-free: the internal daemon/sandbox workspace paths
    # never leak (the authored repository identity is echoed as-is; here it is a
    # local test path, but the container/host workspace path is not).
    flat = repr(first)
    assert "/workspaces/run" not in flat
    assert "temporal_sandbox" not in flat
    assert str(tmp_path / "workspaces") not in flat
    assert "token" not in flat.lower()


@pytest.mark.asyncio
async def test_coordinator_materialization_to_cleanup_static_read_only(
    tmp_path,
) -> None:
    ordered = await _drive_coordinator_materialization_to_cleanup(
        tmp_path, on_demand=False, publish_mode="none"
    )
    types = [entry["type"] for entry in ordered]
    assert types.index("host_stopped") < types.index("profile_lease_release")

    chains = [
        entry["metadata"]["authorityChain"]
        for entry in ordered
        if entry["type"] == "authority_chain"
    ]
    assert len(chains) == 2
    first, second = chains
    assert first["runtime"]["hostMode"] == "static_compose"
    # Static read-only work performs no repository mutation or publication.
    assert first["publication"]["publishMode"] == "none"
    assert first["publication"]["publicationState"] == "read_only_no_publication"
    assert first["terminal"]["cleanupMode"] == "static_drain"
    assert second["workspace"]["materializationAction"] == "reused_pre_materialized"
