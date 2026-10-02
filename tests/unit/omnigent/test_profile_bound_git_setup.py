"""Git setup for the profile-bound Omnigent path used by PR resolver #2767."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

from moonmind.config.settings import settings
from tests.unit.omnigent.test_gh_config_migration_suppression import (
    _static_host_github_block,
)
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _FakeArtifactService,
    _init_source_repo,
    _runtime_for,
    _sandbox_id,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("preparation", ["fresh", "retry", "existing", "checkpoint"])
@pytest.mark.parametrize("configured", [False, True])
async def test_profile_bound_workspace_can_commit_merge_and_push(
    tmp_path, monkeypatch, preparation, configured
) -> None:
    """Resolver #2767 stopped before repair because this lane had no identity."""
    from moonmind.omnigent.git_identity import (
        DEFAULT_GIT_USER_EMAIL,
        DEFAULT_GIT_USER_NAME,
    )

    name = "Deployment Operator" if configured else DEFAULT_GIT_USER_NAME
    email = "operator@example.test" if configured else DEFAULT_GIT_USER_EMAIL
    monkeypatch.setattr(
        settings.workflow, "git_user_name", name if configured else None
    )
    monkeypatch.setattr(
        settings.workflow, "git_user_email", email if configured else None
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for key in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "EMAIL",
    ):
        monkeypatch.delenv(key, raising=False)

    def git(repo, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=check,
        )

    source = tmp_path / "source"
    _init_source_repo(source)
    (source / "base.txt").write_text("new base work\n")
    git(source, "add", "base.txt")
    git(
        source,
        "-c",
        "user.name=Source Author",
        "-c",
        "user.email=source@example.test",
        "commit",
        "-m",
        "Advance base",
    )
    runtime = _runtime_for(tmp_path)
    workspace_id = _sandbox_id()
    workspace = tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"
    kwargs = dict(
        workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
        current_workflow_id="workflow-1",
        current_step_execution_id="step-1",
        repository_source=str(source),
        starting_branch="feature",
    )
    if preparation == "existing":
        git(source, "clone", "--branch", "feature", str(source), str(workspace))
        kwargs.pop("repository_source")
    if preparation == "retry":
        await runtime._prepare_workspace(**kwargs)
        git(workspace, "config", "--local", "--remove-section", "user", check=False)
        (workspace / "repair.txt").write_text("saved repair\n")
        git(workspace, "add", "repair.txt")
    if preparation == "checkpoint":
        saved = tmp_path / "saved-checkout"
        git(source, "clone", "--branch", "feature", str(source), str(saved))
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for path, payload in {
                ".git/config": (saved / ".git/config").read_bytes(),
                "saved.txt": b"checkpoint progress\n",
            }.items():
                member = tarfile.TarInfo(path)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        kwargs["workspace_checkpoint_restore_ref"] = "artifact://checkpoint/candidate"
        kwargs["artifact_gateway"] = _FakeArtifactService(
            {"checkpoint/candidate": stream.getvalue()}
        )

    resolved = await runtime._prepare_workspace(**kwargs)
    assert resolved == workspace
    if preparation == "retry":
        assert (
            git(workspace, "diff", "--cached", "--name-only").stdout.strip()
            == "repair.txt"
        )
        assert (workspace / "repair.txt").read_text() == "saved repair\n"
    if preparation == "checkpoint":
        assert (workspace / "saved.txt").read_text() == "checkpoint progress\n"
    (workspace / "repair.txt").write_text("saved repair\n")
    git(workspace, "add", "repair.txt")
    committed = git(workspace, "commit", "-m", "Repair PR", check=False)
    assert committed.returncode == 0, committed.stderr
    git(workspace, "merge", "--no-edit", "origin/main")
    assert (
        git(workspace, "log", "-1", "--format=%an <%ae>|%cn <%ce>").stdout.strip()
        == f"{name} <{email}>|{name} <{email}>"
    )
    assert (workspace / "base.txt").read_text() == "new base work\n"
    git(workspace, "push", "origin", "HEAD:feature")
    assert (
        git(source, "rev-parse", "feature").stdout
        == git(workspace, "rev-parse", "HEAD").stdout
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
