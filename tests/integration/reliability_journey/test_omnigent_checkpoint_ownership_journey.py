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
