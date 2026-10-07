"""Git setup for the profile-bound Omnigent path used by PR resolver #2767."""

import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.execution_profiles import compile_effective_launch
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from tests.helpers.git_transport import basic_authorization, start_synthetic_github
from tests.unit.omnigent.test_gh_config_migration_suppression import (
    _static_host_github_block,
)
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _binding,
    _egress_attestation,
    _FakeArtifactService,
    _host_lease,
    _init_source_repo,
    _runtime_for,
    _sandbox_id,
)

AMBIENT_TOKEN = "ambientTokenA"
SELECTED_TOKEN = "selectedTokenB"


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


def _ambient_worker_authority(monkeypatch, home: Path) -> bytes:
    """Give the worker every ambient Git/gh credential route, holding token A.

    Returns the model OAuth file bytes that must survive the operation.
    """
    home.mkdir(parents=True, exist_ok=True)
    (home / ".netrc").write_text(
        f"machine github.com login x-access-token password {AMBIENT_TOKEN}\n"
    )
    (home / ".gitconfig").write_text(
        "[http]\n"
        f"\textraHeader = X-Ambient: {AMBIENT_TOKEN}\n"
        "[credential]\n"
        '\thelper = "!f() { echo username=x-access-token; '
        f'echo password={AMBIENT_TOKEN}; }}; f"\n'
    )
    askpass = home / "askpass"
    askpass.write_text(f"#!/bin/sh\necho {AMBIENT_TOKEN}\n")
    askpass.chmod(0o700)
    oauth = home / ".codex" / "auth.json"
    oauth.parent.mkdir()
    oauth.write_text('{"tokens": {"access_token": "model-oauth"}}\n')
    monkeypatch.setenv("HOME", str(home))
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, AMBIENT_TOKEN)
    monkeypatch.setenv("GIT_ASKPASS", str(askpass))
    monkeypatch.setenv(
        "GIT_CONFIG_PARAMETERS",
        f"'http.extraheader'='Authorization: bearer {AMBIENT_TOKEN}'",
    )
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", f"X-Count-Ambient: {AMBIENT_TOKEN}")
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    return oauth.read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [True, False], ids=["selected-B", "anonymous"])
async def test_profile_bound_clone_sends_only_admitted_credential_over_real_git(
    tmp_path, monkeypatch, selected
) -> None:
    """#4011: real git through the production clone sends B, or nothing, never A."""
    transport_gen = start_synthetic_github(
        tmp_path / "transport", required_token=SELECTED_TOKEN if selected else None
    )
    transport = next(transport_gen)
    try:
        home = tmp_path / "worker-home"
        oauth_before = _ambient_worker_authority(monkeypatch, home)
        # Deployment-owned transport routing and trust survive isolation.
        monkeypatch.setenv("HTTPS_PROXY", transport.proxy_url)
        monkeypatch.setenv("https_proxy", transport.proxy_url)
        monkeypatch.setenv("GIT_SSL_CAINFO", str(transport.ca_path))
        runtime = _runtime_for(tmp_path)
        workspace_id = _sandbox_id()

        resolved = await runtime._prepare_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
            current_workflow_id="workflow-1",
            current_step_execution_id="step-1",
            repository_source="https://github.com/owner/repo.git",
            starting_branch="main",
            github_token=SELECTED_TOKEN if selected else None,
        )
    finally:
        transport_gen.close()

    assert (resolved / "README.md").read_text() == "synthetic transport\n"
    assert transport.requests, "the clone must use the synthetic transport"
    assert not any(AMBIENT_TOKEN in value for value in transport.header_values())
    if selected:
        assert set(transport.sent_authorizations()) == {
            basic_authorization(SELECTED_TOKEN)
        }
    else:
        assert transport.sent_authorizations() == []
    # The clean persisted remote carries no credential.
    remote = subprocess.run(
        ["git", "-C", str(resolved), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remote == "https://github.com/owner/repo.git"
    # The worker's HOME and the model's OAuth file are untouched.
    assert os.environ["HOME"] == str(home)
    assert (home / ".codex" / "auth.json").read_bytes() == oauth_before


@pytest.mark.asyncio
async def test_profile_bound_launch_projects_selected_gh_credential_off_metadata(
    tmp_path, monkeypatch
) -> None:
    """#4011: B reaches gh/git through the cache-volume projection, not Config.Env."""
    gh = shutil.which("gh") or "/opt/moonmind-tools/bin/gh"
    if not Path(gh).exists():
        pytest.skip("requires GitHub CLI for its offline Git credential protocol")
    _ambient_worker_authority(monkeypatch, tmp_path / "worker-home")
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "example.test/omnigent@sha256:" + "1" * 64)
    for name in ("OMNIGENT_HOST_IMAGE_REF", "OMNIGENT_SHARED_HOST_IMAGE_REF"):
        monkeypatch.setenv(name, "example.test/host@sha256:" + "2" * 64)
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )
    scripts = runtime._prepare_runtime_scripts(
        "lease-1", current_step_execution_id="workflow:run:node-1:execution:1"
    )
    skills = tmp_path / "skills"
    skills.mkdir()
    runtime.container_exists = AsyncMock(return_value=False)
    runtime._discover_upstream_path = AsyncMock(return_value="/usr/bin:/bin")
    runtime._run = AsyncMock(
        side_effect=lambda *args, **_kwargs: (
            (1, "", "container absent")
            if args[:2] == ("docker", "inspect")
            else (0, "", "")
        )
    )

    await runtime._launch_on_demand(
        binding=_binding().model_copy(
            update={"static_host_id": None, "host_launch_profile_ref": "codex-oauth-v1"}
        ),
        host_lease=_host_lease(),
        container_name="mm-host-lease-1",
        workspace_source=tmp_path,
        skill_projection=skills,
        runtime_scripts=scripts,
        current_step_execution_id="workflow:run:node-1:execution:1",
        github_token=SELECTED_TOKEN,
        effective_launch=compile_effective_launch(
            profile_ref="omnigent-codex@1",
            policy_ref="codex-on-demand@1",
            provider_profile_id="codex",
        ),
        egress_attestation=_egress_attestation(),
    )

    calls = runtime._run.await_args_list
    host = next(call for call in calls if call.args[:3] == ("docker", "run", "-d"))
    # No token value, and no bare ``--env NAME`` forwarding of one, reaches the
    # Docker CLI argv, labels, or the environment Docker would copy from.
    for token in (SELECTED_TOKEN, AMBIENT_TOKEN):
        assert not any(token in str(arg) for arg in host.args)
    env_values = [
        host.args[index + 1]
        for index, value in enumerate(host.args[:-1])
        if value == "--env"
    ]
    assert not any(
        value.split("=", 1)[0] in {"GH_TOKEN", "GITHUB_TOKEN"} for value in env_values
    )
    writer = next(call for call in calls if call.kwargs.get("input_bytes"))
    assert writer.kwargs["input_bytes"] == SELECTED_TOKEN.encode()
    assert not any(SELECTED_TOKEN in str(arg) for arg in writer.args)
    assert "--network" in writer.args and "none" in writer.args
    assert calls.index(writer) < calls.index(host)

    # Execute the production writer and the host's packaged GitHub setup
    # against a local cache, then read through real gh and git.
    home = tmp_path / "host-home"
    config_home = home / ".cache/moonmind-xdg"
    script = writer.args[writer.args.index("-ceu") + 1]
    subprocess.run(
        [
            "/bin/sh",
            "-ceu",
            script.replace("/home/app", str(home)),
            "--",
            str(os.getuid()),
            str(os.getgid()),
            writer.args[-1],
        ],
        input=writer.kwargs["input_bytes"],
        check=True,
    )
    xdg = next(value for value in env_values if value.startswith("XDG_CONFIG_HOME="))
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": xdg.split("=", 1)[1].replace("/home/app", str(home)),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_PROMPT_DISABLED": "1",
    }
    assert environment["XDG_CONFIG_HOME"] == str(config_home)
    block = (
        _static_host_github_block()
        .replace("/home/app", str(home))
        .replace("/opt/moonmind-tools/bin/gh", gh)
    )
    subprocess.run(["/bin/sh", "-c", block], env=environment, check=True)
    hosts = config_home / "gh/hosts.yml"
    assert stat.S_IMODE(hosts.stat().st_mode) == 0o600
    token = subprocess.run(
        [gh, "auth", "token", "--hostname", "github.com"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert token.stdout.strip() == SELECTED_TOKEN
    credential = subprocess.run(
        ["git", "credential", "fill"],
        env=environment,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"password={SELECTED_TOKEN}" in credential.stdout
    assert AMBIENT_TOKEN not in credential.stdout
