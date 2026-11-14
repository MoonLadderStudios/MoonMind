"""Canonical repository requests reach real materialization and durable saves.

Only provider, Docker and Git network transports are controlled. Repository
selection, credential acquisition, source compilation and checkpoint owners run.
"""

import asyncio
import fcntl
import os
import stat
import tempfile
from contextlib import contextmanager
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


@pytest.fixture(autouse=True)
def _mount_fixture_scope(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.getbasetemp().resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    boundary = base
    # Include pytest's private per-user and xdist parents, never /tmp itself.
    if temporary_root in base.parents:
        while boundary.parent != temporary_root:
            boundary = boundary.parent
    monkeypatch.setitem(globals(), "_FIXTURE_TEMP_BOUNDARY", boundary)


@contextmanager
def _fixture_mount_traversal(destination, boundary):
    destination, boundary = Path(destination), Path(boundary)
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("fixture mount path must be absolute without parent traversal")
    relative = destination.relative_to(boundary)
    if not relative.parts:
        raise ValueError("fixture mount must be below its explicit temporary boundary")
    boundary_parts = boundary.relative_to(_FIXTURE_TEMP_BOUNDARY).parts
    if ".." in boundary.parts:
        raise ValueError("fixture boundary must not contain parent traversal")
    current = _FIXTURE_TEMP_BOUNDARY
    for part in boundary_parts:
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("fixture boundary ancestry must be owned non-symlink directories")
        current = current / part
    boundary_info = boundary.lstat()
    if not stat.S_ISDIR(boundary_info.st_mode) or boundary_info.st_uid != os.geteuid():
        raise ValueError("fixture boundary must be an owned non-symlink directory")
    # xdist workers can share pytest's private per-user ancestors. Serialize
    # temporary traversal so one command cannot restore another's access early.
    lock = os.open(
        _FIXTURE_TEMP_BOUNDARY / ".repository-mount-traversal.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
    )
    fcntl.flock(lock, fcntl.LOCK_EX)
    changed = []
    try:
        current = boundary
        for part in (*relative.parts[:-1], None):
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                raise ValueError("fixture mount ancestors must be owned non-symlink directories")
            mode = stat.S_IMODE(info.st_mode)
            if mode & 0o111 != 0o111:
                current.chmod(mode | 0o111)
                changed.append((current, mode))
            if part is not None:
                current = current / part
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("fixture mount target must be a non-symlink directory")
        yield
    finally:
        try:
            for path, mode in reversed(changed):
                path.chmod(mode)
        finally:
            os.close(lock)


async def _run_mounted_repository_script(argv, destination, args, *, fixture_boundary=None):
    uid, gid = map(int, argv[argv.index("--user") + 1].split(":"))
    # This short local Git operation is synchronous and bounded: no coroutine
    # suspension can expose widened modes to cancellation/fixture teardown.
    with _fixture_mount_traversal(destination, fixture_boundary or _FIXTURE_TEMP_BOUNDARY):
        result = subprocess.run(
            ["/bin/sh", "-ceu", argv[argv.index("-ceu") + 1], "--", ".", *args[1:]],
            cwd=destination,
            user=uid,
            group=gid,
            **({"extra_groups": []} if os.geteuid() == 0 else {}),
            capture_output=True, text=True, check=False, timeout=30,
        )
    if result.returncode:
        ownership = []
        for label, path in [
            ("candidate", destination), ("git", destination / ".git"),
            *((f"parent-{index}", path) for index, path in enumerate(list(destination.parents)[:3])),
        ]:
            try:
                info = path.stat()
                ownership.append((label, info.st_uid, info.st_gid, oct(info.st_mode & 0o777)))
            except OSError as exc:
                ownership.append((label, type(exc).__name__))
        print({
            "fixture_mounted_git_failure": True,
            "worker_uid": os.geteuid(), "requested_uid": uid, "requested_gid": gid,
            "cwd_is_mounted_subpath": True, "returncode": result.returncode,
            "stderr": result.stderr[:2048], "ownership": ownership,
        })
    return result


def _repository_transport(journey, monkeypatch, tmp_path, *, branch="main", interrupt_before_pin=False):
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

    interrupted = False

    async def daemon(argv, input_bytes=None):
        nonlocal interrupted
        if list(argv[:2]) == ["docker", "run"] and "clone " in " ".join(argv):
            args = list(argv[argv.index("--") + 1 :])
            selected_branch, source, target = args[:3]
            assert source == "https://github.com/MoonLadderStudios/MoonMind.git"
            assert selected_branch == branch
            assert input_bytes == b"synthetic-repository-credential"
            clones.append((selected_branch, source))
            destination = journey.worker_root / target.removeprefix("/work/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if interrupt_before_pin and not interrupted and len(args) == 6:
                interrupted = True
                subprocess.run(
                    ["git", "clone", "--branch", selected_branch, str(remote), str(destination)],
                    check=True, capture_output=True,
                )
                assert _git(destination, "rev-parse", "HEAD") != args[5]
                raise asyncio.CancelledError("interrupted after clone before pin checkout")
            # Run the production clone/fetch/checkout script; substitute only
            # the network transport and mounted path with the local fixture.
            args[1:3] = [str(remote), str(destination)]
            result = await asyncio.to_thread(
                subprocess.run,
                ["/bin/sh", "-ceu", argv[argv.index("-ceu") + 1], "--", *args],
                input=input_bytes.decode(), capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
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
            # Execute branch preparation and pin verification against the real tree.
            args = argv[argv.index("--") + 1 :]
            if args and args[0] == "/work":
                mount = argv[argv.index("--mount") + 1]
                fields = dict(part.split("=", 1) for part in mount.split(",") if "=" in part)
                destination = journey.worker_root / fields["volume-subpath"]
                result = await _run_mounted_repository_script(argv, destination, args)
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


@pytest.mark.skipif(os.geteuid() != 0, reason="requires root worker to exercise runtime UID transition")
async def test_root_repository_transport_reproduces_and_repairs_uid_mismatch(tmp_path):
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

    repository = tmp_path / "private-worker-parent" / "repository"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked.txt").write_text("Pinned input.\n")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "-c", "user.name=Fixture", "-c",
         "user.email=fixture@example.test", "commit", "-qm", "Pinned input"],
        check=True,
    )
    pin = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True,
    ).strip()
    subprocess.run(["chown", "-R", "1000:1000", str(repository)], check=True)
    assert repository.stat().st_uid == 1000
    observed = []

    async def daemon(argv, input_bytes=None):
        assert argv[argv.index("--user") + 1] == "1000:1000"
        args = argv[argv.index("--") + 1 :]
        script = argv[argv.index("-ceu") + 1]
        # Reproduce the previous transport: root runs the runtime-owned
        # checkout's actual production verifier without honoring --user.
        previous = subprocess.run(
            ["/bin/sh", "-ceu", script, "--", str(repository), *args[1:]],
            capture_output=True, text=True, check=False,
        )
        assert previous.returncode != 0
        assert "dubious ownership" in previous.stderr
        identity_argv = list(argv)
        identity_argv[identity_argv.index("-ceu") + 1] = "id -u"
        identity = await _run_mounted_repository_script(identity_argv, repository, args)
        assert identity.returncode == 0 and identity.stdout.strip() == "1000"
        corrected = await _run_mounted_repository_script(argv, repository, args)
        assert corrected.returncode == 0, corrected.stderr
        observed.append(corrected.returncode)
        return corrected.returncode, corrected.stdout, corrected.stderr

    materializer = OmnigentWorkspaceMaterializer(
        command_runner=daemon, workspace_root=tmp_path, workspace_volume="test-volume",
    )
    await materializer._verify_initial_repository_pin(
        repository, pin, runtime_uid=1000, runtime_gid=1000,
    )
    assert observed == [0]


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize(
    "fixed_serialized", [False, True], ids=["pool", "fixed-serialized"]
)
@pytest.mark.parametrize(
    "source_shape",
    ["flat", "nested", "default", "pinned"],
    ids=["flat-repository", "nested-repository-target", "omitted-source-branch", "typed-pinned-repository"],
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
    pin = _git(tmp_path / "remote", "rev-parse", "main~1") if source_shape == "pinned" else None
    execute = pool.execute_selected_pool_source
    selected = {}

    async def first_turn(*args, **kwargs):
        first = await execute(
            *args,
            **kwargs,
            repository_workspace={"workspaceSource": source},
            bind_parent_context=True,
            repository_at_top=source_shape in {"flat", "pinned"},
            repository_revision={"kind": "git_commit", "commitSha": pin} if pin else None,
            pinned_repository_patch=bool(pin),
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
                if source_shape in {"default", "pinned"}
                else "Selected main, not remote default.\n"
            )
            launches.append(workspace)
            if len(launches) == 1:
                assert _git(workspace, "branch", "--show-current") == (
                    "" if pin else "default-branch" if source_shape == "default" else "main"
                )
                if pin:
                    assert _git(workspace, "rev-parse", "HEAD") == pin
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
    compiled = compile_workspace_source(journey.request.workspace_spec)
    assert compiled.kind == "checkpoint"
    if pin:
        assert compiled.repository_commit_sha == pin
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


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize("work_branch", [None, "main"], ids=["detached", "same-source-work-branch"])
async def test_typed_pinned_repository_reaches_shared_materialization(
    native_journey, monkeypatch, tmp_path, work_branch
):
    journey = native_journey
    _repository_transport(journey, monkeypatch, tmp_path)
    pinned = _git(tmp_path / "remote", "rev-parse", "main~1")
    command = journey.docker.command
    observed = []

    async def observe_pin(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            workspace = Path(native._mounts(list(argv))["/workspaces/run"]["src"])
            observed.append(_git(workspace, "rev-parse", "HEAD"))
            assert _git(workspace, "branch", "--show-current") == (work_branch or "")
            assert (workspace / "selected-branch.txt").read_text() == "Original default branch.\n"
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_pin)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_pin)
    await pool.execute_selected_pool_source(
        journey, monkeypatch, bind_parent_context=True,
        repository_workspace={
            "workspaceSource": {"kind": "repository"},
            **({"targetBranch": work_branch} if work_branch else {}),
        },
        repository_revision={"kind": "git_commit", "commitSha": pinned},
        pinned_repository_patch=True,
        runtime_selection={
            "model": journey.server.model,
            "parameters": {
                "workBranch": work_branch,
                "workBranchId": f"refs/heads/{work_branch}",
                "workBranchOrigin": "review_mapping",
            },
        } if work_branch else None,
    )
    assert journey.docker.create_count
    assert observed == [pinned]


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_pinned_repository_does_not_gain_publication_authority(
    native_journey, monkeypatch
):
    await pool.execute_selected_pool_source(
        native_journey, monkeypatch, scenario="publish", bind_parent_context=True,
        repository_workspace={"workspaceSource": {"kind": "repository"}},
        repository_revision={"kind": "git_commit", "commitSha": "a" * 40},
        pinned_repository_patch=True,
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert not native_journey.docker.create_count


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_unavailable_pinned_repository_never_starts_harness(
    native_journey, monkeypatch, tmp_path
):
    journey = native_journey
    _repository_transport(journey, monkeypatch, tmp_path)

    async def fail_without_harness(admitted):
        from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

        with pytest.raises(HarnessPlatformError, match="HEAD does not match the admitted commit"):
            await admitted.realizer.execute(admitted.request, admitted.plan)
        assert not admitted.docker.create_count
        assert not admitted.server.session_ids

    await pool.execute_selected_pool_source(
        journey, monkeypatch, bind_parent_context=True,
        repository_workspace={"workspaceSource": {"kind": "repository"}},
        repository_revision={"kind": "git_commit", "commitSha": "a" * 40},
        pinned_repository_patch=True, terminal_runner=fail_without_harness,
    )


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_pinned_repository_retries_interrupted_initial_clone(
    native_journey, monkeypatch, tmp_path
):
    journey = native_journey
    clones = _repository_transport(journey, monkeypatch, tmp_path, interrupt_before_pin=True)
    pin = _git(tmp_path / "remote", "rev-parse", "main~1")
    command = journey.docker.command
    observed = []

    async def observe_pin(argv, **kwargs):
        if list(argv[:2]) == ["docker", "create"]:
            workspace = Path(native._mounts(list(argv))["/workspaces/run"]["src"])
            observed.append(_git(workspace, "rev-parse", "HEAD"))
        return await command(argv, **kwargs)

    monkeypatch.setattr(docker_backend, "run_runtime_command", observe_pin)
    monkeypatch.setattr(credential_materializers, "run_runtime_command", observe_pin)

    async def retry_initialization(admitted):
        with pytest.raises(asyncio.CancelledError):
            await admitted.realizer._host_runtime._workspace.materialize(
                admitted.request, plan=admitted.plan,
            )
        assert not admitted.docker.create_count
        assert not list(admitted.worker_root.rglob(".moonmind-import-*"))
        return await native.assert_native_turn_saved_and_cleaned(admitted)

    await pool.execute_selected_pool_source(
        journey, monkeypatch, bind_parent_context=True,
        repository_workspace={"workspaceSource": {"kind": "repository"}},
        repository_revision={"kind": "git_commit", "commitSha": pin},
        pinned_repository_patch=True, terminal_runner=retry_initialization,
    )
    assert len(clones) == 2
    assert observed == [pin]


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
@pytest.mark.parametrize("variant", ["abbreviated", "authored-capability-spoof"])
async def test_typed_pin_requires_full_revision_and_controller_capability(
    native_journey, monkeypatch, variant
):
    abbreviated = variant == "abbreviated"
    await pool.execute_selected_pool_source(
        native_journey, monkeypatch, bind_parent_context=True,
        repository_workspace={"workspaceSource": {"kind": "repository"}},
        repository_revision={
            "kind": "git_commit", "commitSha": "a" * (12 if abbreviated else 40),
        },
        pinned_repository_patch=abbreviated,
        submission_parameters={
            "metadata": {"moonmind": {"pinnedRepositoryWorkspaceHandoff": True}},
        } if not abbreviated else None,
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert native_journey.docker.create_count == 0
    assert not native_journey.server.session_ids


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_pinned_checkpoint_successor_cannot_gain_publication_authority(
    native_journey, monkeypatch
):
    await pool.execute_selected_pool_source(
        native_journey, monkeypatch, scenario="publish", bind_parent_context=True,
        repository_workspace={"workspaceSource": {"kind": "repository"}},
        repository_revision={"kind": "git_commit", "commitSha": "a" * 40},
        pinned_repository_patch=True,
        controller_checkpoint_ref="artifact://controller-saved-pinned-work",
        expected_native_reason="workspace_requires_its_recorded_native_owner",
    )
    assert native_journey.docker.create_count == 0
    assert not native_journey.server.session_ids


@pytest.mark.parametrize("worker_uid", [0, 1000], ids=["root-worker", "runtime-worker"])
async def test_repository_transport_honors_runtime_user_and_subpath(
    monkeypatch, tmp_path, worker_uid
):
    mounted = tmp_path / "private-worker-parent" / "mounted-repository"
    argv = [
        "docker", "run", "--user", "1001:1002", "-ceu",
        'git -C "$1" rev-parse HEAD', "--", "/work", "expected-pin",
    ]
    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, "verified", "")

    from contextlib import nullcontext
    monkeypatch.setitem(globals(), "_fixture_mount_traversal", lambda *args: nullcontext())
    monkeypatch.setattr(os, "geteuid", lambda: worker_uid)
    monkeypatch.setattr(subprocess, "run", run)
    await _run_mounted_repository_script(argv, mounted, ["/work", "expected-pin"])
    assert captured["command"][-2:] == [".", "expected-pin"]
    assert captured["cwd"] == mounted
    assert captured["user"] == 1001
    assert captured["group"] == 1002
    assert captured.get("extra_groups") == ([] if worker_uid == 0 else None)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses local ancestor traversal denial; root UID proof covers CI")
async def test_private_fixture_ancestor_mount_traversal_restored(tmp_path):
    private = tmp_path / "private-staging"
    repository = private / "repository"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "tracked").write_text("fixture")
    _git(repository, "add", ".")
    _git(repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture")
    pin = _git(repository, "rev-parse", "HEAD")
    script = 'env -i PATH="$PATH" HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$1" rev-parse HEAD'
    argv = ["docker", "run", "--user", f"{os.geteuid()}:{os.getegid()}", "-ceu", script, "--", "/work"]
    previous_cwd = Path.cwd()
    os.chdir(repository)
    try:
        private.chmod(0)
        previous = subprocess.run(["/bin/sh", "-ceu", script, "--", "."], capture_output=True, text=True)
        assert previous.returncode != 0 and "Permission denied" in previous.stderr
    finally:
        private.chmod(0o700)
        os.chdir(previous_cwd)
    private.chmod(0)
    try:
        corrected = await _run_mounted_repository_script(argv, repository, ["/work"], fixture_boundary=tmp_path)
        assert corrected.returncode == 0, corrected.stderr
        assert corrected.stdout.strip() == pin
        assert stat.S_IMODE(private.stat().st_mode) == 0
        failure = list(argv)
        failure[failure.index("-ceu") + 1] = "exit 17"
        result = await _run_mounted_repository_script(failure, repository, ["/work"], fixture_boundary=tmp_path)
        assert result.returncode == 17
        assert stat.S_IMODE(private.stat().st_mode) == 0
    finally:
        private.chmod(0o700)


@pytest.mark.parametrize("failure", [OSError("controlled fixture subprocess failure"), subprocess.TimeoutExpired("fixture-git", 30)], ids=["exception", "timeout"])
async def test_fixture_mount_restores_ancestors_on_subprocess_exception(monkeypatch, tmp_path, failure):
    private = tmp_path / "private"
    repository = private / "repository"
    repository.mkdir(parents=True)
    before = stat.S_IMODE(private.stat().st_mode)
    private.chmod(0o700)

    def fail(*args, **kwargs):
        assert stat.S_IMODE(private.stat().st_mode) == 0o711
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    argv = ["docker", "run", "--user", f"{os.geteuid()}:{os.getegid()}", "-ceu", "true", "--", "/work"]
    try:
        with pytest.raises(type(failure)):
            await _run_mounted_repository_script(argv, repository, ["/work"], fixture_boundary=tmp_path)
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
    finally:
        private.chmod(before)


async def test_fixture_mount_rejects_symlink_and_out_of_scope(tmp_path):
    boundary = tmp_path / "scope"
    boundary.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = boundary / "alias"
    link.symlink_to(outside, target_is_directory=True)
    for target in (outside, link, link / "nested"):
        with pytest.raises(ValueError):
            with _fixture_mount_traversal(target, boundary):
                pytest.fail("unsafe fixture target was accepted")
        assert stat.S_IMODE(boundary.stat().st_mode) == 0o700


async def test_fixture_mount_cancellation_waits_for_exact_mode_restoration(monkeypatch, tmp_path):
    private = tmp_path / "private"
    repository = private / "repository"
    repository.mkdir(parents=True)
    private.chmod(0o700)

    def run(command, **kwargs):
        assert stat.S_IMODE(private.stat().st_mode) == 0o711
        task = asyncio.current_task()
        task.cancel()
        task.cancel()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    argv = ["docker", "run", "--user", f"{os.geteuid()}:{os.getegid()}", "-ceu", "true", "--", "/work"]
    task = asyncio.create_task(_run_mounted_repository_script(argv, repository, ["/work"], fixture_boundary=tmp_path))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


async def test_fixture_mount_rejects_symlink_in_explicit_boundary(monkeypatch, tmp_path):
    scope = tmp_path / "declared-scope"
    scope.mkdir()
    outside = tmp_path / "outside"
    boundary = outside / "nested-scope"
    repository = boundary / "repository"
    repository.mkdir(parents=True)
    boundary.chmod(0o700)
    (scope / "alias").symlink_to(outside, target_is_directory=True)
    monkeypatch.setitem(globals(), "_FIXTURE_TEMP_BOUNDARY", scope)
    with pytest.raises(ValueError, match="non-symlink"):
        with _fixture_mount_traversal(scope / "alias" / "nested-scope" / "repository", scope / "alias" / "nested-scope"):
            pytest.fail("intermediate boundary symlink escaped fixture scope")
    assert stat.S_IMODE(boundary.stat().st_mode) == 0o700
