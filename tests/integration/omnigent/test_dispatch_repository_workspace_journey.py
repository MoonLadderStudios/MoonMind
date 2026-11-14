"""Canonical repository requests reach real materialization and durable saves.

Only provider, Docker and Git network transports are controlled. Repository
selection, credential acquisition, source compilation and checkpoint owners run.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent import credential_materializers
from moonmind.omnigent.harness_platform.credential_bindings import (
    repository_bindings_of,
)
from moonmind.omnigent.host_services import docker_backend
from moonmind.omnigent.realizers.turn_delivery import admission_epoch
from moonmind.omnigent.runtime_bindings import RuntimeBindingState, stable_binding_id
from moonmind.omnigent.workspace_sources import compile_workspace_source
from moonmind.workflows.executions.repository_contract import authored_repository_access
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from tests.integration.omnigent import test_dispatch_pool_context_journey as context
from tests.integration.omnigent import test_dispatch_pool_shared_admission as pool
from tests.integration.omnigent import test_generic_native_durable_journey as native

native_journey = pool.native_journey
session_factory = pool.session_factory
context_provider = context.context_provider
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _git(workspace, *args):
    return subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-C",
            str(workspace),
            *args,
        ],
        text=True,
    ).strip()


def _repository_transport(journey, monkeypatch, tmp_path, *, branch="main"):
    remote = tmp_path / "remote"
    subprocess.run(
        ["git", "init", "-q", "-b", "default-branch", str(remote)], check=True
    )
    _git(remote, "config", "user.name", "Repository fixture")
    _git(remote, "config", "user.email", "fixture@example.test")
    for name in ("tracked.txt", "obsolete.txt"):
        (remote / name).write_text("Original repository input.\n")
    (remote / "selected-branch.txt").write_text("Original default branch.\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "Default branch")
    _git(remote, "switch", "-qc", "main")
    (remote / "selected-branch.txt").write_text("Selected main, not remote default.\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "Selected branch")
    if not branch:
        _git(remote, "switch", "default-branch")
    port = journey.realizer._host_runtime._workspace
    original_runner = port._runner
    clones = []
    monkeypatch.setenv("POOL_TEST_REPOSITORY_TOKEN", "synthetic-repository-credential")

    async def daemon(argv, input_bytes=None):
        if list(argv[:2]) == ["docker", "run"] and "clone " in " ".join(argv):
            selected_branch, source, target, name, email = argv[-5:]
            assert source == "https://github.com/MoonLadderStudios/MoonMind.git"
            assert selected_branch == branch
            assert input_bytes == b"synthetic-repository-credential"
            clones.append((selected_branch, source))
            destination = journey.worker_root / target.removeprefix("/work/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "git",
                    "clone",
                    *(["--branch", selected_branch] if selected_branch else []),
                    "--single-branch",
                    str(remote),
                    str(destination),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                _git(destination, "config", "user.name", name)
                _git(destination, "config", "user.email", email)
                _git(destination, "remote", "set-url", "origin", source)
            return result.returncode, result.stdout, result.stderr
        if "/bin/chown" in argv:
            target = journey.worker_root / argv[-1].removeprefix("/work/")
            result = await asyncio.to_thread(
                subprocess.run,
                ["chown", "-R", "--", argv[-2], str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode, result.stdout, result.stderr
        if "-ceu" in argv and "--" in argv and list(argv[:2]) == ["docker", "run"]:
            # Execute the owner's branch script against the actual mounted tree.
            args = argv[argv.index("--") + 1 :]
            if args and args[0] == "/work":
                mount = argv[argv.index("--mount") + 1]
                fields = dict(part.split("=", 1) for part in mount.split(","))
                destination = journey.worker_root / fields["volume-subpath"]
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "/bin/sh",
                        "-ceu",
                        argv[argv.index("-ceu") + 1],
                        "--",
                        str(destination),
                        *args[1:],
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return result.returncode, result.stdout, result.stderr
        return await original_runner(argv, input_bytes=input_bytes)

    restore_git = ManagedCheckpointRestoreService._git

    def checkpoint_git(service, args, *positional, **kwargs):
        if args[0] == "clone":
            assert args == [
                "clone",
                "--no-checkout",
                "https://github.com/MoonLadderStudios/MoonMind.git",
                args[-1],
            ]
            assert kwargs["env"]["GITHUB_TOKEN"] == "synthetic-repository-credential"
            args = [*args[:2], str(remote), args[-1]]
        return restore_git(service, args, *positional, **kwargs)

    monkeypatch.setattr(ManagedCheckpointRestoreService, "_git", checkpoint_git)
    monkeypatch.setattr(port, "_runner", daemon)
    return clones


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "fixed_serialized", [False, True], ids=["pool", "fixed-serialized"]
)
@pytest.mark.parametrize(
    "source_shape",
    ["flat", "nested", "default"],
    ids=["flat-repository", "nested-repository-target", "omitted-source-branch"],
)
async def test_repository_context_survives_source_host_and_worker_cleanup(
    native_journey,
    monkeypatch,
    tmp_path,
    source_shape,
    fixed_serialized,
):
    journey = native_journey
    clones = _repository_transport(
        journey,
        monkeypatch,
        tmp_path,
        branch="" if source_shape == "default" else "main",
    )
    source = {
        "kind": "repository",
        "repository": "MoonLadderStudios/MoonMind",
        "branch": "main",
    }
    if source_shape == "nested":
        source = {
            "kind": "repository",
            "repositoryTarget": {
                "provider": "git",
                "connectionRef": "pool-repository",
                "repository": {"name": "MoonLadderStudios/MoonMind"},
                "branch": {"name": "main"},
            },
        }
    if source_shape == "default":
        source.pop("branch")
        source["connectionRef"] = "pool-repository"
    source["accessMode"] = "explicit"
    execute = pool.execute_selected_pool_source
    selected = {}

    async def first_turn(*args, **kwargs):
        first = await execute(
            *args,
            **kwargs,
            repository_workspace={"workspaceSource": source},
            bind_parent_context=True,
            repository_at_top=source_shape == "flat",
        )
        selected["first"] = first
        if source_shape == "default":
            remote = tmp_path / "remote"
            _git(remote, "switch", "-qc", "new-default")
            (remote / "selected-branch.txt").write_text(
                "Later default must not replace saved work.\n"
            )
            _git(remote, "add", ".")
            _git(remote, "commit", "-qm", "Change remote default")
        bindings = repository_bindings_of(journey.plan.payload.credentialBindings)
        assert bindings and all(
            value.connectionRef == "pool-repository" for value in bindings.values()
        )
        assert all(
            value.repositoryRole != "destination_write" for value in bindings.values()
        )
        return first

    monkeypatch.setattr(pool, "execute_selected_pool_source", first_turn)
    command = journey.docker.command
    launches = []
    saved = {}

    async def observe_launch(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            workspace = Path(native._mounts(list(argv))["/workspaces/run"]["src"])
            assert (workspace / "selected-branch.txt").read_text() == (
                "Original default branch.\n"
                if source_shape == "default"
                else "Selected main, not remote default.\n"
            )
            launches.append(workspace)
            if len(launches) == 1:
                assert _git(workspace, "branch", "--show-current") == (
                    "default-branch" if source_shape == "default" else "main"
                )
                _git(workspace, "switch", "-qc", "agent/candidate")
                (workspace / "unpublished.txt").write_text("Unpublished commit.\n")
                _git(workspace, "add", "unpublished.txt")
                _git(workspace, "commit", "-qm", "Unpublished work")
                saved["head"] = _git(workspace, "rev-parse", "HEAD")
                saved["branch"] = _git(workspace, "branch", "--show-current")
                (workspace / "obsolete.txt").unlink()
                (workspace / "tracked.txt").write_text("Staged work.\n")
                _git(workspace, "add", "tracked.txt", "obsolete.txt")
                (workspace / "tracked.txt").write_text("Unstaged work.\n")
            else:
                assert _git(workspace, "rev-parse", "HEAD") == saved["head"]
                saved["restored_branch"] = _git(workspace, "branch", "--show-current")
                assert _git(workspace, "show", ":tracked.txt") == "Staged work."
                assert (workspace / "tracked.txt").read_text() == "Unstaged work.\n"
                assert not (workspace / "obsolete.txt").exists()
                assert (
                    workspace / "unpublished.txt"
                ).read_text() == "Unpublished commit.\n"
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_launch)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_launch)
    await context.test_pool_context_survives_source_host_and_worker_cleanup(
        journey,
        monkeypatch,
        fixed_serialized=fixed_serialized,
        explicit_scratch=False,
        expected_restore_key="checkpointRef",
    )
    assert len(launches) == 2
    assert authored_repository_access(
        {"workspaceSpec": journey.request.workspace_spec}
    ) == ("pool-repository", False)
    assert compile_workspace_source(journey.request.workspace_spec).kind == "checkpoint"
    assert clones
    if source_shape == "default":
        assert len(clones) == 1
    first = selected["first"]
    assert await journey.gateway.read_bytes(
        first.result.metadata["savedWorkspaceCheckpoint"]["archiveRef"]
    )
    assert not journey.server.host_alive
    assert saved["restored_branch"] == saved["branch"]


@pytest.mark.parametrize("native_journey", ["codex-native-api-key"], indirect=True)
async def test_repository_cancellation_preserves_sibling_resources(
    native_journey,
    monkeypatch,
    tmp_path,
):
    journey = native_journey
    clones = _repository_transport(journey, monkeypatch, tmp_path)
    sibling = journey.worker_root / "sibling-workflow" / "repo"
    sibling.mkdir(parents=True)
    (sibling / "unpublished.txt").write_text("Another active workflow's work.")
    journey.docker.volumes["sibling-provider-config"] = {
        "moonmind.owner": "sibling-workflow"
    }
    monkeypatch.setattr(
        "moonmind.omnigent.activity_ownership.delivery_was_revoked", lambda: False
    )

    async def cancel_turn(admitted):
        execution = asyncio.create_task(
            admitted.realizer.execute(admitted.request, admitted.plan)
        )

        async def cancel_running_turn():
            execution.cancel()

        admitted.server.on_first_message = cancel_running_turn
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, 30)

    await pool.execute_selected_pool_source(
        journey,
        monkeypatch,
        bind_parent_context=True,
        terminal_runner=cancel_turn,
        repository_workspace={
            "workspaceSource": {
                "kind": "repository",
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
            }
        },
    )
    assert (
        clones
        and len(journey.server.session_ids) == 1
        and journey.docker.create_count == 1
    )
    assert not journey.server.host_alive
    assert (
        journey.server.workspace / native.RESULT_PATH
    ).read_bytes() == native.GENERATED
    assert (
        sibling / "unpublished.txt"
    ).read_text() == "Another active workflow's work."
    assert journey.docker.volumes == {
        "journey_oauth": {},
        "sibling-provider-config": {"moonmind.owner": "sibling-workflow"},
    }
    binding = await journey.services.runtime_binding_store.get(
        stable_binding_id(
            execution_plan_ref=journey.plan.planRef,
            idempotency_key=journey.request.idempotency_key,
            admission_epoch=admission_epoch(journey.request),
        )
    )
    assert binding.state is RuntimeBindingState.cleaned
    assert await journey.gateway.read_bytes(
        binding.attestationRefs["cleanupAttestationRef"]
    )


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "authored_capability",
    ["none", "runtime", "workflow"],
    ids=[
        "no-capability",
        "authored-runtime-capability",
        "authored-workflow-capability",
    ],
)
async def test_retained_parent_keeps_repository_on_native_owner(
    native_journey, monkeypatch, authored_capability
):
    await pool.execute_selected_pool_source(
        native_journey,
        monkeypatch,
        repository_workspace={
            "workspaceSource": {
                "kind": "repository",
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "main",
            }
        },
        bind_parent_context=False,
        parent_repository_patch=False,
        runtime_selection={
            "model": native_journey.server.model,
            "parameters": {
                "canonicalRepositoryWorkspaceHandoff": True,
                "metadata": {"moonmind": {"canonicalRepositoryWorkspaceHandoff": True}},
            },
        }
        if authored_capability == "runtime"
        else None,
        submission_parameters={
            "metadata": {"moonmind": {"canonicalRepositoryWorkspaceHandoff": True}},
            "canonicalRepositoryWorkspaceHandoff": True,
        }
        if authored_capability == "workflow"
        else None,
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert native_journey.docker.create_count == 0
    assert not native_journey.server.session_ids


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            {
                "kind": "repository",
                "repository": "/tmp/operator-repository",
                "branch": "main",
            },
            id="local-repository",
        ),
        pytest.param(
            {
                "kind": "repository",
                "repository": "https://gitlab.example.test/owner/project.git",
                "branch": "main",
            },
            id="non-github-repository",
        ),
        pytest.param(
            {"kind": "checkpoint", "checkpointRef": "artifact://retained-checkpoint"},
            id="authored-checkpoint",
        ),
    ],
)
async def test_capable_parent_keeps_other_sources_on_their_native_owner(
    native_journey,
    monkeypatch,
    source,
):
    await pool.execute_selected_pool_source(
        native_journey,
        monkeypatch,
        bind_parent_context=True,
        repository_workspace={"workspaceSource": source},
        repository_at_top=False,
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert native_journey.docker.create_count == 0
    assert not native_journey.server.session_ids


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "target_location",
    ["source", "workspace", "workspace-with-source"],
    ids=[
        "source-repository-target",
        "workspace-repository-target",
        "workspace-pin-with-unpinned-source-target",
    ],
)
async def test_capable_parent_keeps_pinned_repository_revision_on_native_owner(
    native_journey, monkeypatch, target_location
):
    target = {
        "provider": "git",
        "connectionRef": "pool-repository",
        "repository": {"name": "MoonLadderStudios/MoonMind"},
        "branch": {"name": "main"},
        "revision": {"kind": "git_commit", "commitSha": "a" * 40},
    }
    workspace = {"workspaceSource": {"kind": "repository"}}
    if target_location == "source":
        workspace["workspaceSource"]["repositoryTarget"] = target
    else:
        workspace["repositoryTarget"] = target
        if target_location == "workspace-with-source":
            workspace["workspaceSource"]["repositoryTarget"] = {
                key: value for key, value in target.items() if key != "revision"
            }
    await pool.execute_selected_pool_source(
        native_journey,
        monkeypatch,
        bind_parent_context=True,
        repository_workspace=workspace,
        repository_at_top=False,
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert native_journey.docker.create_count == 0
    assert not native_journey.server.session_ids
