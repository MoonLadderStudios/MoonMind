"""Optional installed-Docker qualification of the selected read-only seed reader."""

import hashlib
import json
import shutil
import subprocess
import uuid

import pytest

from moonmind.omnigent.codex_file_seeds import (
    CodexFileSeedUnavailable,
    capture_codex_file_seeds,
)
from moonmind.omnigent.credential_materializers import LocalDockerCommandBackend

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]
IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"


async def test_actual_docker_reader_captures_only_selected_nonsecret_files():
    if shutil.which("docker") is None:
        pytest.skip("installed Docker unavailable")
    available = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, timeout=10
    )
    if available.returncode:
        pytest.skip(
            "installed MoonMind host image unavailable; no image pull attempted"
        )
    backend = LocalDockerCommandBackend()
    prefix = "moonmind-test-codex-seeds-" + uuid.uuid4().hex
    volume = prefix + "-volume"
    seed_name = prefix + "-writer"
    created = False
    try:
        code, _, _ = await backend.run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "com.docker.compose.project=moonmind-test-codex-seeds",
                volume,
            ]
        )
        assert code == 0
        created = True
        script = """import pathlib, os
root=pathlib.Path('/selected')
(root/'AGENTS.md').write_bytes(b'Literal {{unbound}} seed\\r\\n')
(root/'auth.json').write_text('{"password":"synthetic-not-selected"}')
(root/'rules').mkdir()
(root/'rules/preferences.json').write_text('{"seed":true}')
(root/'rules/sibling.md').symlink_to(root/'auth.json')
(root/'rules/secret.md').write_text('ghp_synthetic_seed_must_be_refused')
for path in [root,*root.rglob('*')]:
 if not path.is_symlink(): os.chown(path,1000,1000)
"""
        code, _, _ = await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                seed_name,
                "--user",
                "0:0",
                "--network",
                "none",
                "--read-only",
                "--mount",
                f"type=volume,source={volume},target=/selected",
                "--entrypoint",
                "python3",
                IMAGE,
                "-c",
                script,
            ]
        )
        assert code == 0
        seeds = await capture_codex_file_seeds(
            backend,
            volume_ref=volume,
            writer_image_ref=IMAGE,
            paths=["AGENTS.md", "rules/preferences.json", "rules/missing.json"],
        )
        assert seeds == {
            "version": 1,
            "files": [
                {
                    "path": "AGENTS.md",
                    "present": True,
                    "content": "Literal {{unbound}} seed\r\n",
                },
                {
                    "path": "rules/preferences.json",
                    "present": True,
                    "content": '{"seed":true}',
                },
                {"path": "rules/missing.json", "present": False},
            ],
        }
        assert "synthetic-not-selected" not in json.dumps(seeds)
        with pytest.raises(CodexFileSeedUnavailable):
            await capture_codex_file_seeds(
                backend,
                volume_ref=volume,
                writer_image_ref=IMAGE,
                paths=["rules/sibling.md"],
            )
        with pytest.raises(CodexFileSeedUnavailable):
            await capture_codex_file_seeds(
                backend,
                volume_ref=volume,
                writer_image_ref=IMAGE,
                paths=["rules/secret.md"],
            )
        code, stdout, _ = await backend.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                seed_name,
                "--network",
                "none",
                "--read-only",
                "--mount",
                f"type=volume,source={volume},target=/selected,readonly",
                "--entrypoint",
                "python3",
                IMAGE,
                "-c",
                "import hashlib,pathlib;print(hashlib.sha256((pathlib.Path('/selected')/'auth.json').read_bytes()).hexdigest())",
            ]
        )
        assert code == 0 and stdout.decode().strip() == hashlib.sha256(b'{"password":"synthetic-not-selected"}').hexdigest()
    finally:
        await backend.run(["docker", "rm", "--force", seed_name])
        if created:
            code, _, _ = await backend.run(["docker", "volume", "rm", volume])
            assert code == 0
