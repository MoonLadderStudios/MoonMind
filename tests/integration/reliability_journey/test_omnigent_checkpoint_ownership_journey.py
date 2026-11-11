"""Replay the restored verifier directory that blocked mm:f23c3090 publication."""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _mutation_state(path):
    # Verification may update atime; only writes change this state.
    info = path.stat()
    return (
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_identity", [False, True])
async def test_runtime_can_publish_beside_restored_verifier(
    tmp_path, explicit_identity
):
    if os.geteuid() != 0:
        pytest.skip("Run through the Compose integration runner for UID handoff")
    # The Compose test runner is root; the real selected host agent is UID 1000.
    workspace_id = hashlib.sha256(b"workflow:publication").hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    for parent in (workspace, *workspace.parents):
        if parent == tmp_path.parent.parent.parent:
            break
        parent.chmod(0o755)
    os.chown(workspace, 1000, 1000)
    data = b'{"verdict":"FULLY_IMPLEMENTED"}'
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
        member = tarfile.TarInfo("artifacts/verify.json")
        member.size = len(data)
        bundle.addfile(member, io.BytesIO(data))
        script = b"#!/bin/sh\nprintf 'restored executable works\\n'\n"
        member = tarfile.TarInfo("tools/verify.sh")
        member.mode = 0o755
        member.size = len(script)
        bundle.addfile(member, io.BytesIO(script))

    class Artifacts:
        async def get_metadata(self, **_kwargs):
            return SimpleNamespace(size_bytes=len(archive.getvalue())), []

        async def read_chunks(self, **_kwargs):
            return SimpleNamespace(), iter((archive.getvalue(),))

    request = SimpleNamespace(
        correlation_id="workflow",
        idempotency_key="publication",
        step_execution=None,
        input_refs=[],
        parameters={},
        workspace_spec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            "workspaceCheckpointRestoreRef": "artifact://verifier-checkpoint",
        },
    )
    await OmnigentWorkspaceMaterializer(
        command_runner=None,
        workspace_root=tmp_path,
        artifact_service=Artifacts(),
    ).materialize(
        request,
        **({"runtime_uid": 1000, "runtime_gid": 1000} if explicit_identity else {})
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('artifacts/pr.json').write_text('{}')",
        ],
        cwd=workspace,
        user=1000,
        group=1000,
        check=True,
        capture_output=True,
    )
    assert (workspace / "artifacts/pr.json").stat().st_uid == 1000
    assert (workspace / "artifacts/verify.json").read_bytes() == data
    # Execute from the selected workspace, without traversing pytest's
    # root-owned temporary ancestors after the subprocess drops to UID 1000.
    executed = subprocess.run(
        ["./tools/verify.sh"],
        cwd=workspace,
        user=1000,
        group=1000,
        check=True,
        capture_output=True,
        text=True,
    )
    assert executed.stdout == "restored executable works\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_saved_input", [False, True])
@pytest.mark.parametrize("distinct_runtime", [False, True])
async def test_projected_inputs_are_traversable_with_private_parent_directories(
    tmp_path, monkeypatch, reuse_saved_input, distinct_runtime
):
    import json
    import stat

    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector
    from tests.unit.omnigent.test_workspace_restore_convergence import (
        FakeArtifactService,
    )

    if distinct_runtime and os.geteuid() != 0:
        pytest.skip("Compose integration runner supplies root-to-UID1000 handoff")
    uid, gid = (1000, 1000) if distinct_runtime else (os.getuid(), os.getgid())
    workspace = tmp_path / "repo"
    workspace.mkdir(mode=0o700)
    # Model the canonical baseline handoff, which precedes input projection.
    os.chown(workspace, uid, gid)
    owner = WorkspaceArtifactProjector(
        FakeArtifactService({"restore": b"restore", "saved": b"saved", "new": b"new"})
    )
    options = dict(workflow_id="workflow-1", runtime_uid=uid, runtime_gid=gid)
    projection = await owner.prepare_input_projection(
        restore_refs=("artifact://restore",),
        attachment_refs=("artifact://saved", "artifact://new"),
        **options,
    )
    baseline_paths = set()
    baseline = {}
    if reuse_saved_input:
        item = projection["entries"][1]
        saved = workspace / item["path"]
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b"saved")
        saved.chmod(0o400)
        for path, mode in ((saved.parent.parent, 0o750), (saved.parent, 0o710)):
            path.chmod(mode)
            os.chown(path, uid, gid)
        os.chown(saved, uid, gid)
        baseline_paths.add(item["path"])
        baseline = {
            path: path.stat() for path in (saved, saved.parent, saved.parent.parent)
        }
    previous_umask = os.umask(0o077)
    try:
        link = os.link

        def interrupt_after_first_input(*args, **kwargs):
            link(*args, **kwargs)
            raise RuntimeError("projection interrupted after promotion")

        with monkeypatch.context() as interrupted:
            interrupted.setattr(os, "link", interrupt_after_first_input)
            with pytest.raises(RuntimeError, match="projection interrupted"):
                await owner.project_bound_inputs(
                    workspace,
                    projection=projection,
                    baseline_paths=baseline_paths,
                    **options,
                )
        promoted = workspace / projection["entries"][0]["path"]
        promoted_inode = promoted.stat().st_ino
        await owner.project_bound_inputs(
            workspace, projection=projection, baseline_paths=baseline_paths, **options
        )
        assert promoted.stat().st_ino == promoted_inode
    finally:
        os.umask(previous_umask)
    if reuse_saved_input:
        before, after = baseline[saved], saved.stat()
        assert (after.st_ino, after.st_mode, after.st_mtime_ns, after.st_ctime_ns) == (
            before.st_ino,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    expected = {
        item["path"]: item["ref"].removeprefix("artifact://")
        for item in projection["entries"]
    }
    # Relative opens from the selected workspace exercise every private parent,
    # without relying on access through pytest's unrelated private ancestors.
    child_options = (
        dict(user=uid, group=gid, extra_groups=[]) if distinct_runtime else {}
    )
    completed = subprocess.run(
        [
            os.path.abspath(sys.executable),
            "-I",
            "-c",
            "import json, os, pathlib, sys; "
            "assert os.geteuid() == int(sys.argv[1]); "
            "expected = json.loads(sys.argv[2]); "
            "actual = {path: pathlib.Path(path).read_text() for path in expected}; "
            "assert actual == expected",
            str(uid),
            json.dumps(expected),
        ],
        cwd=workspace,
        check=True,
        capture_output=True,
        timeout=10,
        **child_options,
    )
    assert completed.returncode == 0
    for directory in (
        workspace / ".moonmind",
        workspace / ".moonmind/restore",
        workspace / ".moonmind/attachments",
    ):
        info = directory.stat()
        assert (info.st_uid, info.st_gid) == (uid, gid)
        expected_mode = (
            stat.S_IMODE(baseline[directory].st_mode)
            if directory in baseline
            else 0o700
        )
        assert stat.S_IMODE(info.st_mode) == expected_mode
    snapshot = {path: _mutation_state(path) for path in workspace.rglob("*")}
    await owner.project_bound_inputs(
        workspace, projection=projection, baseline_paths=baseline_paths, **options
    )
    assert {path: _mutation_state(path) for path in workspace.rglob("*")} == snapshot
    await owner.project_bound_inputs(
        workspace,
        projection=projection,
        baseline_paths=baseline_paths,
        verify_only=True,
        **options,
    )
    assert {path: _mutation_state(path) for path in workspace.rglob("*")} == snapshot
