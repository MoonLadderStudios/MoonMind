import base64
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from moonmind.omnigent.host_services.runtime_scripts import (
    OmnigentRuntimeScriptService,
)


def _build(
    *,
    target_path: str,
    github_attachment=None,
    enable_opencode_runtime: bool = False,
    runtime_environment=None,
):
    return OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[
            {
                "credentialGeneration": 3,
                "attachments": [{"targetPath": target_path}],
            }
        ],
        skill_attachment={"targetPath": "/opt/moonmind-skills"},
        step_execution_id="workflow:run:node-1:execution:1",
        github_credential_attachment=github_attachment,
        enable_opencode_runtime=enable_opencode_runtime,
        runtime_environment=runtime_environment,
    )


def test_opencode_materializer_pins_deterministic_server_startup_environment():
    script, environment = _build(target_path="/run/mm-credentials/opencode")

    expected_flags = {
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_DISABLE_MODELS_FETCH",
    }
    expected_proxies = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    assert {
        name for name in expected_flags if environment[name] == "1"
    } == expected_flags
    assert set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")) == (
        expected_flags
        | expected_proxies
        | {"MOONMIND_ACTIVE_SKILLS_DIR", "MOONMIND_STEP_EXECUTION_ID"}
    )
    assert (
        environment["MOONMIND_STEP_EXECUTION_ID"] == "workflow:run:node-1:execution:1"
    )
    assert "> /home/app/.omnigent/moonmind/bin/moonmind-context" in script
    assert "> /home/app/.omnigent/moonmind/bin/opencode" in script
    assert (
        "exec /home/app/.omnigent/moonmind/bin/moonmind-context "
        '/usr/local/bin/opencode "$@"'
    ) in script
    assert "MOONMIND_STEP_EXECUTION_ID=$(cat" in script
    assert "MOONMIND_ACTIVE_SKILLS_DIR=$(cat" in script
    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


_EGRESS_PROXY_NAMES = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


def test_non_opencode_materializer_does_not_inject_opencode_runtime_flags():
    _script, environment = _build(target_path="/run/mm-credentials/other")

    assert not any(name.startswith("OPENCODE_") for name in environment)
    # The plugin npm cache seeding lives inside the MOONMIND_OPENCODE_RUNTIME
    # guard, which this projection never enables.
    assert "MOONMIND_OPENCODE_RUNTIME" not in environment
    assert set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")) == {
        "MOONMIND_ACTIVE_SKILLS_DIR",
        "MOONMIND_STEP_EXECUTION_ID",
    } | _EGRESS_PROXY_NAMES


@pytest.mark.parametrize("target_path", ["/home/app/.claude", "/home/app/.codex"])
def test_oauth_home_runners_keep_the_restricted_egress_proxy(target_path):
    """Every on-demand host sits behind the egress proxy, not only OpenCode.

    Omnigent filters the host environment before spawning a runner, so a
    Claude or Codex runner that loses these names resolves provider DNS
    directly and fails (``EAI_AGAIN``) on the restricted network.
    """

    _script, environment = _build(target_path=target_path)

    passthrough = set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(","))
    assert _EGRESS_PROXY_NAMES <= passthrough
    assert not any(name.startswith("OPENCODE_") for name in environment)


def test_credentialless_opencode_runtime_builds_wrapper_without_auth_mount():
    script, environment = _build(
        target_path="",
        enable_opencode_runtime=True,
    )

    assert environment["MOONMIND_OPENCODE_RUNTIME"] == "1"
    assert environment["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert "> /home/app/.omnigent/moonmind/bin/opencode" in script
    assert 'if [ -d "/run/mm-credentials/opencode" ]; then cp ' in script
    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_github_projection_exposes_only_non_secret_cli_environment():
    _script, environment = _build(
        target_path="/run/mm-credentials/opencode",
        github_attachment={
            "targetPath": "/run/mm-credentials/github",
        },
    )

    assert environment["GH_CONFIG_DIR"] == "/home/app/.config/gh"
    assert "XDG_CONFIG_HOME" not in environment
    assert environment["GH_PROMPT_DISABLED"] == "1"
    assert environment["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert environment["GH_NO_EXTENSION_UPDATE_NOTIFIER"] == "1"
    # An empty entry resets the lists Git accumulated from inherited config,
    # so the admitted helper is the only helper Git asks for github.com and no
    # inherited Authorization header rides along (MoonLadderStudios/MoonMind#4011).
    assert environment["GIT_CONFIG_COUNT"] == "4"
    assert environment["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert environment["GIT_CONFIG_VALUE_0"] == ""
    assert environment["GIT_CONFIG_KEY_1"] == "credential.https://github.com.helper"
    assert environment["GIT_CONFIG_VALUE_1"] == (
        "!/home/app/.omnigent/moonmind/bin/gh auth git-credential"
    )
    assert environment["GIT_CONFIG_KEY_2"] == "http.https://github.com/.extraHeader"
    assert environment["GIT_CONFIG_VALUE_2"] == ""
    assert environment["GIT_CONFIG_KEY_3"] == "http.https://github.com/.emptyAuth"
    assert environment["GIT_CONFIG_VALUE_3"] == "true"
    assert environment["PATH"].startswith("/home/app/.omnigent/moonmind/bin:")
    passthrough = set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(","))
    proxy_names = {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    assert set(environment) >= passthrough - proxy_names
    assert {
        "GH_PROMPT_DISABLED",
        "GH_CONFIG_DIR",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_KEY_1",
        "GIT_CONFIG_VALUE_1",
        "GIT_CONFIG_KEY_2",
        "GIT_CONFIG_VALUE_2",
        "GIT_CONFIG_KEY_3",
        "GIT_CONFIG_VALUE_3",
    } <= passthrough
    assert not any("TOKEN" in name or "SECRET" in name for name in environment)
    assert "cp /run/mm-credentials/github/hosts.yml" in _script
    assert "/home/app/.config/gh/hosts.yml" in _script
    assert "> /home/app/.omnigent/moonmind/bin/gh" in _script
    assert "export GH_CONFIG_DIR=/home/app/.config/gh" in _script
    assert "GIT_CONFIG_VALUE_0" in _script


@pytest.mark.parametrize("capability", ["EXECUTION_FANOUT", "CONTAINER_JOBS"])
@pytest.mark.parametrize("enable_opencode", [False, True])
def test_generic_projection_restores_scoped_capability_file_selector(
    capability,
    enable_opencode,
) -> None:
    runtime_environment = {
        "MOONMIND_URL": "http://api:8000",
        "MOONMIND_AGENT_RUN_ID": "agent-run-1",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
        "MOONMIND_STEP_ID": "step-1",
        "MOONMIND_RUNTIME_ID": "opencode-native",
        "MOONMIND_REPOSITORY_CONNECTION_REF": ("repository-connection:git-default"),
        f"MOONMIND_{capability}_BEARER_TOKEN_FILE": (
            "/run/moonmind-host-auth/" + capability.lower().replace("_", "-")
        ),
    }
    if capability == "CONTAINER_JOBS":
        runtime_environment.update(
            {
                "MOONMIND_CONTAINER_JOBS_MCP_URL": "http://api:8000/mcp/container",
                "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
                "MOONMIND_CONTAINER_JOBS_SESSION_ID": "lease-1",
                "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
                "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": "sandbox-1",
                "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": "repo",
            }
        )
    script, environment = _build(
        target_path="",
        enable_opencode_runtime=enable_opencode,
        runtime_environment=runtime_environment,
    )

    passthrough = set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(","))
    assert set(runtime_environment) <= passthrough
    assert {key: environment[key] for key in runtime_environment} == runtime_environment
    for key in runtime_environment:
        assert f"export {key}=$(cat " in script
    assert f"MOONMIND_{capability}_BEARER_TOKEN=" not in script
    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_opencode_runtime_seeds_plugin_npm_cache_before_host_start():
    """OpenCode's first ``serve`` runs ``npm install @opencode-ai/plugin`` in a
    fresh per-session config directory; the entrypoint must make that install
    resolve from the image-owned cache before Omnigent starts the runner."""

    script, environment = _build(target_path="", enable_opencode_runtime=True)

    seed = "/opt/moonmind/opencode-npm-cache"
    cache = "/home/app/.omnigent/moonmind/opencode-npm-cache"
    guard = script.index(f"test -d {seed} || {{ ")
    copy = script.index(f"rm -rf {cache}; cp -a {seed} {cache}; ")
    npmrc = script.index(
        "printf '%s\\n' "
        f"'cache={cache}' 'prefer-offline=true' 'audit=false' 'fund=false' "
        "'update-notifier=false' > /home/app/.npmrc; chmod 0600 /home/app/.npmrc; "
    )
    host_start = script.index('exec omnigent host --server "$1" --non-interactive')
    assert guard < copy < npmrc < host_start
    # A host image without the warm cache fails closed with a named contract
    # instead of silently paying the cold registry install again.
    assert "exit 78; }" in script[guard:copy]
    assert "missing the warm plugin npm cache" in script[guard:copy]
    # npm reads the runtime home's .npmrc, so no npm_config_* value has to
    # survive Omnigent's host -> runner -> server environment filters.
    assert not any(name.lower().startswith("npm_config") for name in environment)
    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_projected_cli_restores_context_after_child_environment_is_stripped(tmp_path):
    replay = (
        Path(__file__).resolve().parents[3]
        / "tests/integration/reliability/replays/issue-brief-verification-handoff/runner_boundary.py"
    )
    spec = importlib.util.spec_from_file_location("runner_boundary", replay)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.exercise_projection(tmp_path)


# --- Admitted repository authority at the real Git/gh process boundary --------
# MoonLadderStudios/MoonMind#4011: the generated entrypoint is executed with the
# real clients, only its fixed container paths relocated under tmp_path. The
# admitted credential B is the projected hosts.yml; every ambient source A is a
# selector the official clients would otherwise prefer
# (https://cli.github.com/manual/gh_help_environment,
# https://git-scm.com/docs/gitcredentials).

_REAL_GH = shutil.which("gh")
_REAL_GIT = shutil.which("git")
_requires_real_git_and_gh = pytest.mark.skipif(
    not (_REAL_GH and _REAL_GIT), reason="requires the real git and gh clients"
)
_ADMITTED_B = "ghs_admittedRepositoryAuthorityB0000000000"
_AMBIENT_A = "ghp_ambientCredentialThatMustLoseA000000000"
_MODEL_OAUTH = '{"tokens": {"access_token": "model-oauth-home-credential"}}\n'
_AMBIENT_TOKEN_ENV = {
    "GH_TOKEN": _AMBIENT_A,
    "GITHUB_TOKEN": _AMBIENT_A,
    "GH_ENTERPRISE_TOKEN": _AMBIENT_A,
    "GITHUB_ENTERPRISE_TOKEN": _AMBIENT_A,
    "GH_HOST": "ambient.invalid",
}


def _ambient_helper(value: str) -> str:
    return (
        '"!f() { test \\"$1\\" = get || exit 0; '
        f'echo username=ambient; echo password={value}; }}; f"'
    )


def _run_generic_host(
    tmp_path: Path,
    *,
    project_github: bool,
    credential_url: str = "https://github.com",
    credential_host: str = "github.com",
    ambient: bool = True,
) -> tuple[dict[str, str], Path]:
    """Run the real generated entrypoint and return the host process env."""

    home = tmp_path / "home"
    tools = tmp_path / "tools"
    projection = tmp_path / "projection"
    skills = tmp_path / "skills"
    model = tmp_path / "model-credential"
    runtime_bin = home / ".omnigent/moonmind/bin"
    for directory in (runtime_bin, tools / "bin", skills, model):
        directory.mkdir(parents=True, exist_ok=True)
    # Inert host-launch sentinel: the entrypoint ends in ``exec omnigent host``.
    (runtime_bin / "omnigent").write_text("#!/bin/sh\nexit 0\n")
    (runtime_bin / "omnigent").chmod(0o700)
    (tools / "bin/gh").symlink_to(_REAL_GH)
    (model / ".moonmind-generation").write_text("3")
    (model / "auth.json").write_text(_MODEL_OAUTH)
    # The model's own OAuth home state lives beside the projected gh config.
    (home / ".codex").mkdir()
    (home / ".codex/auth.json").write_text(_MODEL_OAUTH)
    if project_github:
        projection.mkdir()
        (projection / "hosts.yml").write_text(
            f"{credential_host}:\n    user: x-access-token\n"
            f"    oauth_token: {_ADMITTED_B}\n    git_protocol: https\n"
        )
    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[
            {"credentialGeneration": 3, "attachments": [{"targetPath": str(model)}]}
        ],
        skill_attachment={"targetPath": str(skills)},
        step_execution_id="workflow:run:node-1:execution:1",
        tool_attachments=(
            {"targetPath": str(tools), "tools": [{"name": "gh", "path": "bin/gh"}]},
        ),
        github_credential_attachment=(
            {"targetPath": "/run/mm-credentials/github"} if project_github else None
        ),
    )

    def relocate(text: str) -> str:
        return (
            text.replace("/run/mm-credentials/github", str(projection))
            .replace("/home/app", str(home))
            .replace("/opt/moonmind-tools", str(tools))
            .replace("https://github.com", credential_url)
        )

    host_environment = {key: relocate(value) for key, value in environment.items()}
    host_environment = {relocate(key): value for key, value in host_environment.items()}
    host_environment["HOME"] = str(home)
    host_environment["GIT_TERMINAL_PROMPT"] = "0"
    # The host image carries no system credential layer of its own.
    host_environment["GIT_CONFIG_SYSTEM"] = os.devnull
    if ambient:
        # A login cache left in the home and a host-level configuration layer
        # both select A for the same endpoint.
        (home / ".gitconfig").write_text(
            f"[credential]\n\thelper = {_ambient_helper(_AMBIENT_A + '-global')}\n"
        )
        # libcurl answers the first challenge from this file before Git asks
        # any helper (MoonLadderStudios/MoonMind#4011).
        (home / ".netrc").write_text(
            f"machine {credential_host.split(':', 1)[0]} "
            f"login ambient password {_AMBIENT_A}-netrc\n"
        )
        (home / ".netrc").chmod(0o600)
        ambient_header = base64.b64encode(f"ambient:{_AMBIENT_A}".encode()).decode()
        (tmp_path / "system.gitconfig").write_text(
            f"[credential]\n\thelper = {_ambient_helper(_AMBIENT_A)}\n"
            f'[http "{credential_url}/"]\n'
            f"\textraHeader = Authorization: Basic {ambient_header}\n"
        )
        host_environment["GIT_CONFIG_SYSTEM"] = str(tmp_path / "system.gitconfig")
        host_environment.update(_AMBIENT_TOKEN_ENV)
    setup = subprocess.run(
        ["/bin/sh", "-ceu", relocate(script), "--", "http://fixture"],
        env=host_environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=home,
    )
    assert setup.returncode == 0, setup.stderr
    # The projection never removes the model's credential or OAuth home.
    assert (model / "auth.json").read_text() == _MODEL_OAUTH
    assert (home / ".codex/auth.json").read_text() == _MODEL_OAUTH
    if ambient:
        assert (home / ".netrc").read_text().endswith(f"{_AMBIENT_A}-netrc\n")
    return host_environment, home


def _credential_fill(environment: dict[str, str], host: str) -> str:
    completed = subprocess.run(
        [_REAL_GIT, "credential", "fill"],
        input=f"protocol=https\nhost={host}\npath=owner/repo.git\n\n",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@_requires_real_git_and_gh
def test_admitted_github_projection_wins_over_ambient_credentials(
    tmp_path: Path,
) -> None:
    environment, home = _run_generic_host(tmp_path, project_github=True)
    runtime_gh = str(home / ".omnigent/moonmind/bin/gh")

    token = subprocess.run(
        [runtime_gh, "auth", "token"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert token.returncode == 0, token.stderr
    assert token.stdout.strip() == _ADMITTED_B

    filled = _credential_fill(environment, "github.com")
    assert f"password={_ADMITTED_B}\n" in filled
    assert _AMBIENT_A not in filled

    # Native harnesses strip the runner environment; a login shell restores the
    # same projection and still refuses the ambient selectors the child keeps.
    child = {
        "HOME": str(home),
        "PATH": environment["PATH"],
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_SYSTEM": environment["GIT_CONFIG_SYSTEM"],
        **_AMBIENT_TOKEN_ENV,
    }
    login = subprocess.run(
        [
            "/bin/bash",
            "-lc",
            "gh auth token; printf 'protocol=https\\nhost=github.com\\n\\n' "
            "| git credential fill",
        ],
        env=child,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert login.returncode == 0, login.stderr
    assert login.stdout.splitlines()[0] == _ADMITTED_B
    assert f"password={_ADMITTED_B}" in login.stdout
    assert _AMBIENT_A not in login.stdout


@_requires_real_git_and_gh
def test_admitted_projection_is_the_only_credential_a_git_remote_receives(
    tmp_path: Path,
) -> None:
    from tests.support.credential_recording_remote import (
        credential_recording_remote,
    )

    with credential_recording_remote(tmp_path) as remote:
        environment, _home = _run_generic_host(
            tmp_path,
            project_github=True,
            credential_url=remote.url,
            credential_host=remote.host,
        )
        environment["GIT_SSL_CAINFO"] = str(remote.ca_file)
        subprocess.run(
            [_REAL_GIT, "ls-remote", f"{remote.url}/owner/repo.git"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            cwd=tmp_path,
        )
        received = list(remote.authorization_headers)

    admitted = (
        "Basic " + base64.b64encode(f"x-access-token:{_ADMITTED_B}".encode()).decode()
    )
    assert admitted in received
    decoded = [
        base64.b64decode(value.split(" ", 1)[1]).decode()
        for value in received
        if value.startswith("Basic ")
    ]
    assert not any(_AMBIENT_A in value for value in decoded)


@_requires_real_git_and_gh
def test_scratch_host_has_no_repository_token_and_still_inspects_gh(
    tmp_path: Path,
) -> None:
    environment, home = _run_generic_host(tmp_path, project_github=False, ambient=False)

    assert not (home / ".omnigent/moonmind/bin/gh").exists()
    assert not (home / ".config/gh").exists()
    assert not any(
        key.startswith(("GH_", "GIT_CONFIG_")) or "TOKEN" in key
        for key in environment
        if key != "GIT_CONFIG_SYSTEM"
    )
    helper = subprocess.run(
        [
            _REAL_GIT,
            "config",
            "--get-urlmatch",
            "credential.helper",
            "https://github.com",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert helper.returncode == 1 and not helper.stdout
    token = subprocess.run(
        ["gh", "auth", "token"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert token.returncode != 0 and not token.stdout.strip()
    # Plain build/version inspection never depends on a provider login.
    version = subprocess.run(
        ["gh", "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        cwd=environment["HOME"],
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("gh version ")
