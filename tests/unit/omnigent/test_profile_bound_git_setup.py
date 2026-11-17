"""Git setup for the Omnigent host GitHub projection used by PR resolver #2767."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.unit.omnigent.test_gh_config_migration_suppression import (
    _static_host_github_block,
)


@pytest.mark.parametrize("restart", [False, True])
def test_profile_bound_github_projection_authenticates_git_without_setup(
    tmp_path: Path, restart: bool
) -> None:
    """The resolver must fetch/push with its projected gh account immediately."""
    gh = shutil.which("gh")
    if gh is None:
        pytest.skip("requires GitHub CLI for its offline Git credential protocol")
    home = tmp_path / "home"
    config_home = home / ".cache/moonmind-xdg"
    gh_dir = config_home / "gh"
    gh_dir.mkdir(parents=True)
    # No real provider credential or network call is needed by this protocol.
    token = "fixture_github_token"
    if restart:
        (gh_dir / "hosts.yml").write_text(
            f"github.com:\n    oauth_token: {token}\n    git_protocol: https\n"
        )
    block = (
        _static_host_github_block()
        .replace("/home/app", str(home))
        .replace("/opt/moonmind-tools/bin/gh", gh)
    )
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_PROMPT_DISABLED": "1",
        **({} if restart else {"GH_TOKEN": token}),
    }
    staged = subprocess.run(
        ["/bin/sh", "-c", block],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert staged.returncode == 0, staged.stderr
    environment.pop("GH_TOKEN", None)
    credential = subprocess.run(
        ["git", "credential", "fill"],
        env=environment,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
    )
    assert credential.returncode == 0, credential.stderr
    assert "username=" in credential.stdout
    assert f"password={token}" in credential.stdout
    # Runtime setup stays outside the checkout; no token reaches Git config.
    git_config = config_home / "git/config"
    assert token not in git_config.read_text()
    assert stat.S_IMODE(git_config.stat().st_mode) == 0o600
    # The same setup is safe to repeat without accumulating helpers.
    subprocess.run(["/bin/sh", "-c", block], env=environment, check=True)
    helpers = subprocess.run(
        ["git", "config", "--get-all", "credential.https://github.com.helper"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert helpers.stdout.splitlines() == ["", f"!{gh} auth git-credential"]
