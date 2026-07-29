"""Owner-boundary workspace materialization for the normal Omnigent path.

Covers github issue MoonLadderStudios/MoonMind#3507: the normal Workflow path
must materialize the authored repository/branch/attachment/input/checkpoint state
into one authorized workspace behind the ``WorkspaceLocator`` boundary, carrying
only durable refs (never absolute worker/daemon paths, volume names, bind
sources, Docker socket authority, or credential bodies).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.workspace_materialization import (
    WorkspaceMaterializationError,
    WorkspaceMaterializationSpec,
    materialize_workspace,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "HOME": str(cwd),
            "PATH": __import__("os").environ.get("PATH", ""),
        },
    )


def _make_origin(root: Path) -> Path:
    """Create a local origin repository with a feature branch and content."""

    origin = root / "origin"
    origin.mkdir(parents=True)
    _git(origin, "init", "-b", "main")
    (origin / "README.md").write_text("main\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "initial")
    _git(origin, "checkout", "-b", "feature/authored")
    (origin / "feature.txt").write_text("authored\n", encoding="utf-8")
    _git(origin, "add", "feature.txt")
    _git(origin, "commit", "-m", "feature")
    _git(origin, "checkout", "main")
    return origin


# --------------------------------------------------------------------------- #
# Spec validation — the payload can never carry host authority.
# --------------------------------------------------------------------------- #


def test_spec_rejects_absolute_repository_path() -> None:
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(repository="/work/agent_jobs/repo")


def test_spec_rejects_embedded_credentials_in_repository() -> None:
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(
            repository="https://x-access-token:secret@github.com/o/r.git"
        )


def test_spec_rejects_volume_and_socket_input_refs() -> None:
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(
            repository="o/r", inputRefs=["type=volume,src=foo,dst=/x"]
        )
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(
            repository="o/r", externalStateRef="unix:///var/run/docker.sock"
        )


def test_spec_rejects_attachment_destination_traversal() -> None:
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(
            repository="o/r",
            attachments=[{"artifactRef": "artifact://a", "destination": "../escape"}],
        )


def test_spec_normalizes_and_validates_commit() -> None:
    spec = WorkspaceMaterializationSpec(repository="o/r", commit="ABCDEF0")
    assert spec.commit == "abcdef0"
    with pytest.raises(ValueError):
        WorkspaceMaterializationSpec(repository="o/r", commit="not-a-sha")


def test_spec_digest_is_stable_across_field_order() -> None:
    a = WorkspaceMaterializationSpec(
        repository="o/r", startingBranch="main", targetBranch="out"
    )
    b = WorkspaceMaterializationSpec(
        repository="o/r", targetBranch="out", startingBranch="main"
    )
    assert a.digest() == b.digest()


def test_from_request_payload_returns_none_without_repository() -> None:
    assert (
        WorkspaceMaterializationSpec.from_request_payload(
            workspace_spec={}, parameters={}, input_refs=[]
        )
        is None
    )


def test_from_request_payload_compiles_authored_intent() -> None:
    spec = WorkspaceMaterializationSpec.from_request_payload(
        workspace_spec={
            "startingBranch": "feature/authored",
            "targetBranch": "out/1",
            "attachments": [
                {"artifactRef": "artifact://a1", "destination": "docs/a.md"}
            ],
        },
        parameters={"repository": "o/r", "publishMode": "pr"},
        input_refs=["artifact://in1"],
    )
    assert spec is not None
    assert spec.repository == "o/r"
    assert spec.starting_branch == "feature/authored"
    assert spec.target_branch == "out/1"
    assert spec.repository_mutation is True
    assert spec.publish_mode == "pr"
    assert spec.input_refs == ("artifact://in1",)
    assert spec.attachments[0].destination == "docs/a.md"


# --------------------------------------------------------------------------- #
# materialize_workspace — real clone/checkout at the owner boundary.
# --------------------------------------------------------------------------- #


class _RecordingRunner:
    """Wraps the real runtime command runner and records argv/env for auditing."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict]] = []

    async def __call__(self, *args, env=None, check=True):
        self.calls.append((args, dict(env or {})))
        return await OmnigentOAuthHostRuntime._run(*args, env=env, check=check)


@pytest.mark.asyncio
async def test_materialize_clones_and_checks_out_authored_branch(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"
    runner = _RecordingRunner()

    spec = WorkspaceMaterializationSpec(
        repository=origin.as_uri(),
        startingBranch="feature/authored",
        targetBranch="out/1",
    )

    evidence = await materialize_workspace(
        spec=spec, owned_root=owned_root, repo_path=repo_path, run_command=runner
    )

    assert (repo_path / "feature.txt").read_text(encoding="utf-8") == "authored\n"
    assert evidence["materialized"] is True
    assert evidence["reused"] is False
    assert evidence["cloned"] is True
    assert evidence["checkedOutRef"] == "feature/authored"
    assert evidence["startingBranch"] == "feature/authored"
    assert evidence["targetBranch"] == "out/1"
    assert len(evidence["sourceCommit"]) == 40
    # Bounded evidence must not leak absolute daemon paths or credentials.
    assert str(owned_root) not in str(evidence)


@pytest.mark.asyncio
async def test_materialize_is_idempotent_on_retry(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"
    spec = WorkspaceMaterializationSpec(
        repository=origin.as_uri(), startingBranch="main"
    )

    first_runner = _RecordingRunner()
    await materialize_workspace(
        spec=spec, owned_root=owned_root, repo_path=repo_path, run_command=first_runner
    )
    assert any("clone" in call[0] for call in first_runner.calls)

    # A retry with the same intent must not clone/checkout again.
    retry_runner = _RecordingRunner()
    evidence = await materialize_workspace(
        spec=spec, owned_root=owned_root, repo_path=repo_path, run_command=retry_runner
    )
    assert retry_runner.calls == []
    assert evidence["reused"] is True


@pytest.mark.asyncio
async def test_materialize_rejects_conflicting_intent(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"
    await materialize_workspace(
        spec=WorkspaceMaterializationSpec(
            repository=origin.as_uri(), startingBranch="main"
        ),
        owned_root=owned_root,
        repo_path=repo_path,
        run_command=_RecordingRunner(),
    )

    with pytest.raises(WorkspaceMaterializationError) as exc:
        await materialize_workspace(
            spec=WorkspaceMaterializationSpec(
                repository=origin.as_uri(), startingBranch="feature/authored"
            ),
            owned_root=owned_root,
            repo_path=repo_path,
            run_command=_RecordingRunner(),
        )
    assert exc.value.code == "WORKSPACE_MATERIALIZATION_SPEC_CONFLICT"


@pytest.mark.asyncio
async def test_materialize_token_never_appears_in_argv(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"
    runner = _RecordingRunner()

    await materialize_workspace(
        spec=WorkspaceMaterializationSpec(
            repository=origin.as_uri(), startingBranch="main"
        ),
        owned_root=owned_root,
        repo_path=repo_path,
        run_command=runner,
        github_token="ghp_supersecrettoken",
    )

    for args, env in runner.calls:
        assert "ghp_supersecrettoken" not in " ".join(str(a) for a in args)
    # The token is only reachable through the inline credential helper env var.
    clone_env = next(env for args, env in runner.calls if "clone" in args)
    assert clone_env.get("MOONMIND_MATERIALIZE_TOKEN") == "ghp_supersecrettoken"
    # It must not be persisted into the checkout's git config.
    config = (repo_path / ".git" / "config").read_text(encoding="utf-8")
    assert "ghp_supersecrettoken" not in config


@pytest.mark.asyncio
async def test_materialize_writes_attachments_and_inputs(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"

    class _Reader:
        async def read_bytes(self, artifact_ref: str) -> bytes:
            return f"payload:{artifact_ref}".encode("utf-8")

    spec = WorkspaceMaterializationSpec(
        repository=origin.as_uri(),
        startingBranch="main",
        attachments=[{"artifactRef": "artifact://a1", "destination": "docs/a.md"}],
        inputRefs=["artifact://in1"],
    )
    evidence = await materialize_workspace(
        spec=spec,
        owned_root=owned_root,
        repo_path=repo_path,
        run_command=_RecordingRunner(),
        artifact_reader=_Reader(),
    )

    assert (repo_path / "docs" / "a.md").read_bytes() == b"payload:artifact://a1"
    assert evidence["attachmentCount"] == 1
    assert evidence["inputRefCount"] == 1
    assert (owned_root / "inputs").is_dir()


@pytest.mark.asyncio
async def test_materialize_restores_external_state_through_boundary(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    repo_path = owned_root / "repo"

    seen: dict = {}

    class _Restore:
        async def restore(self, *, ref, destination, idempotency_key):
            seen["ref"] = ref
            seen["destination"] = destination
            return {"restoreEvidenceRef": "artifact://restore-evidence"}

    spec = WorkspaceMaterializationSpec(
        repository=origin.as_uri(),
        startingBranch="main",
        checkpointRef="artifact://checkpoint-1",
    )
    evidence = await materialize_workspace(
        spec=spec,
        owned_root=owned_root,
        repo_path=repo_path,
        run_command=_RecordingRunner(),
        restore_boundary=_Restore(),
    )
    assert seen["ref"] == "artifact://checkpoint-1"
    assert seen["destination"] == owned_root
    assert evidence["restoreEvidenceRef"] == "artifact://restore-evidence"


@pytest.mark.asyncio
async def test_materialize_requires_reader_for_declared_attachments(tmp_path) -> None:
    origin = _make_origin(tmp_path)
    owned_root = tmp_path / "owned"
    with pytest.raises(WorkspaceMaterializationError) as exc:
        await materialize_workspace(
            spec=WorkspaceMaterializationSpec(
                repository=origin.as_uri(),
                startingBranch="main",
                attachments=[
                    {"artifactRef": "artifact://a1", "destination": "a.md"}
                ],
            ),
            owned_root=owned_root,
            repo_path=owned_root / "repo",
            run_command=_RecordingRunner(),
            artifact_reader=None,
        )
    assert exc.value.code == "WORKSPACE_MATERIALIZATION_ARTIFACTS_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# Controlling worker-boundary journey — the runtime materializes only after
# ownership/containment resolution succeeds, and surfaces bounded evidence.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_prepare_workspace_materializes_after_ownership_resolution(
    tmp_path,
) -> None:
    origin = _make_origin(tmp_path)
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )
    workspace_id = hashlib.sha256(b"workflow-1:step-1").hexdigest()[:24]

    materialized, evidence = await runtime._prepare_workspace(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        materialization={
            "repository": origin.as_uri(),
            "startingBranch": "feature/authored",
        },
    )

    expected = workspace_root / "temporal_sandbox" / workspace_id / "repo"
    assert materialized == expected
    assert (expected / "feature.txt").exists()
    assert evidence is not None
    assert evidence["materialized"] is True
    assert evidence["checkedOutRef"] == "feature/authored"
    # Owner record is written before materialization.
    assert SandboxWorkspaceRecordStore(workspace_root).load(workspace_id) == (
        SandboxWorkspaceRecord(workspace_id, "workflow-1", "step-1", "repo")
    )


@pytest.mark.asyncio
async def test_prepare_workspace_rejects_foreign_locator_before_materializing(
    tmp_path,
) -> None:
    origin = _make_origin(tmp_path)
    workspace_root = tmp_path / "workspaces"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=workspace_root
    )
    # An identity that does not match the current execution must be rejected
    # before any clone runs.
    foreign_id = hashlib.sha256(b"other:step").hexdigest()[:24]

    with pytest.raises(Exception):
        await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": foreign_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            materialization={"repository": origin.as_uri(), "startingBranch": "main"},
        )
    assert not (workspace_root / "temporal_sandbox" / foreign_id).exists()
