"""Branch preparation uses the admitted subtree with its real runtime UID."""

import shutil
import subprocess
import uuid

import pytest

from moonmind.omnigent.host_services.workspace import (
    build_daemon_workspace_branch_argv,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_private_parent_allows_scoped_branch_preparation_and_retained_edits():
    docker = shutil.which("docker")
    if not docker or subprocess.run(
        [docker, "info", "--format", "{{.ServerVersion}}"],
        capture_output=True, timeout=30,
    ).returncode:
        pytest.skip("Docker daemon is unavailable")
    image = "alpine/git:v2.43.0"
    if subprocess.run(
        [docker, "image", "inspect", image], capture_output=True, timeout=30,
    ).returncode:
        pytest.skip("The workspace Git helper image is unavailable locally")

    def run(argv):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return result.stdout

    volume = "moonmind-test-private-branch-" + uuid.uuid4().hex[:12]
    run([docker, "volume", "create", volume])
    try:
        setup = """set -eu
umask 077
mkdir -p /fixture/temporal_sandbox/attempt/repo /fixture/temporal_sandbox/attempt/sibling
git init -q -b main /fixture/temporal_sandbox/attempt/repo
printf 'admitted input\\n' > /fixture/temporal_sandbox/attempt/repo/README.md
git -C /fixture/temporal_sandbox/attempt/repo add README.md
git -C /fixture/temporal_sandbox/attempt/repo -c user.name=Fixture -c user.email=fixture@example.test commit -qm Baseline
printf 'retained sibling\\n' > /fixture/temporal_sandbox/attempt/sibling/retained.txt
chown -R 1000:1000 /fixture/temporal_sandbox/attempt/repo
"""
        run([
            docker, "run", "--rm", "--network", "none", "--read-only",
            "--user", "0:0", "-v", volume + ":/fixture",
            "--entrypoint", "/bin/sh", image, "-ceu", setup,
        ])

        def prepare(initialize):
            return build_daemon_workspace_branch_argv(
                volume=volume, target_in_volume="temporal_sandbox/attempt/repo",
                target_branch="feature/selected", runtime_uid=1000,
                runtime_gid=1000, image=image, allow_initialization=initialize,
            )

        run(prepare(True))
        inspect_and_edit = prepare(True)
        inspect_and_edit[inspect_and_edit.index("-ceu") + 1] = """set -eu
[ "$(git -C /work symbolic-ref --short HEAD)" = feature/selected ]
[ ! -e /work/../sibling ]
[ ! -e /work/temporal_sandbox ]
git -C /work switch -qc agent/candidate
printf 'retained edits\\n' >> /work/README.md
"""
        run(inspect_and_edit)
        run(prepare(False))

        verify = """set -eu
stat -c '%u:%g %a' /fixture/temporal_sandbox/attempt
cat /fixture/temporal_sandbox/attempt/repo/.git/HEAD
cat /fixture/temporal_sandbox/attempt/repo/README.md
cat /fixture/temporal_sandbox/attempt/sibling/retained.txt
"""
        observed = run([
            docker, "run", "--rm", "--network", "none", "--read-only",
            "--user", "0:0", "--mount",
            "type=volume,src=" + volume + ",dst=/fixture,readonly",
            "--entrypoint", "/bin/sh", image, "-ceu", verify,
        ])
        assert observed.splitlines() == [
            "0:0 700", "ref: refs/heads/agent/candidate", "admitted input",
            "retained edits", "retained sibling",
        ]
    finally:
        run([docker, "volume", "rm", volume])
