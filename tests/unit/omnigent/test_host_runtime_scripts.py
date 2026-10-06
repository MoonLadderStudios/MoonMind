import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService


def _build(
    *,
    target_path: str,
    github_attachment=None,
    enable_opencode_runtime: bool = False,
    enable_claude_runtime: bool = False,
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
        enable_claude_runtime=enable_claude_runtime,
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


def test_claude_runtime_keeps_background_tasks_in_the_foreground():
    # MoonMind settles a Claude step when its turn ends and then removes the
    # host. A background subagent or shell has no owner to resume the session
    # with its result, so the step finishes without the outputs that work was
    # meant to produce (escaped as a missing assessment verdict artifact).
    _script, environment = _build(
        target_path="/run/mm-credentials/claude", enable_claude_runtime=True
    )

    assert environment["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    # Omnigent filters the host environment before spawning a runner; the
    # Claude Code TUI only inherits names that survive that hop.
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" in set(
        environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")
    )


def test_claude_background_task_restriction_is_scoped_to_claude_hosts():
    _script, environment = _build(target_path="/run/mm-credentials/other")

    assert not any(name.startswith("CLAUDE_CODE_") for name in environment)


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
    assert environment["GIT_CONFIG_COUNT"] == "1"
    assert environment["GIT_CONFIG_KEY_0"] == "credential.https://github.com.helper"
    assert environment["GIT_CONFIG_VALUE_0"] == (
        "!/home/app/.omnigent/moonmind/bin/gh auth git-credential"
    )
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


@pytest.mark.parametrize("github_host", [None, "github.enterprise.test"])
def test_runtime_script_builder_remains_portable_with_admitted_attachment(github_host):
    """The native verifier imports only the builder, without the application."""

    builder = (
        Path(__file__).resolve().parents[3]
        / "moonmind/omnigent/host_services/runtime_scripts.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            """
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("runtime_scripts", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
attachment = (
    {"targetPath": "/run/mm-credentials/github", "githubHost": sys.argv[2]}
    if sys.argv[2] else None
)
script, environment = module.OmnigentRuntimeScriptService().build_entrypoint(
    credential_handles=[],
    skill_attachment={"targetPath": "/opt/moonmind-skills"},
    step_execution_id="workflow:run:node-1:execution:1",
    github_credential_attachment=attachment,
)
print(json.dumps({"script": script, "environment": environment}))
""",
            str(builder),
            github_host or "",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    if github_host:
        assert observed["environment"]["GH_HOST"] == github_host
        assert observed["environment"]["GIT_CONFIG_KEY_0"] == (
            f"credential.https://{github_host}.helper"
        )
        assert f"export GH_HOST={github_host}" in observed["script"]
    else:
        assert "GH_HOST" not in observed["environment"]
    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=observed["script"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


@pytest.mark.parametrize("github_host", ["github.com", "github.enterprise.test"])
def test_projected_github_cli_isolates_selected_config_from_ambient_tokens(
    tmp_path, monkeypatch, github_host
):
    import os

    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", github_host)

    script, environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[],
        skill_attachment={"targetPath": "/opt/moonmind-skills"},
        step_execution_id="workflow:run:node-1:execution:1",
        github_credential_attachment={
            "targetPath": "/run/mm-credentials/github",
            "githubHost": github_host,
        },
    )
    assert environment["GH_HOST"] == github_host
    assert environment["GIT_CONFIG_KEY_0"] == (
        f"credential.https://{github_host}.helper"
    )
    home = tmp_path / "home"
    credentials = tmp_path / "credentials"
    tools = tmp_path / "tools"
    binaries = tmp_path / "bin"
    skills = tmp_path / "skills"
    for directory in (home, credentials / "github", tools / "bin", binaries, skills):
        directory.mkdir(parents=True)
    (credentials / "github" / "hosts.yml").write_text(
        f"{github_host}:\n  oauth_token: selected-canary\n"
    )
    gh = tools / "bin" / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "print(json.dumps({'host': os.environ['GH_HOST'], 'ambient': [name for name in "
        "('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN') "
        "if os.getenv(name)], 'config': Path(os.environ['GH_CONFIG_DIR'], 'hosts.yml').read_text()}))\n"
    )
    gh.chmod(0o755)
    omnigent = binaries / "omnigent"
    omnigent.write_text(
        f"#!/bin/sh\nGH_TOKEN=ambient-again; export GH_TOKEN; "
        "GH_HOST=ambient-host; export GH_HOST; "
        f'exec "{home}/.omnigent/moonmind/bin/gh" "$@"\n'
    )
    omnigent.chmod(0o755)
    script = (
        script.replace("/home/app", str(home))
        .replace("/run/mm-credentials", str(credentials))
        .replace("/opt/moonmind-tools", str(tools))
    )
    result = subprocess.run(
        ["/bin/sh", "-ceu", script, "--", "http://test-omnigent"],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            **environment,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "MOONMIND_ACTIVE_SKILLS_DIR": str(skills),
            "GH_TOKEN": "ambient-canary",
            "GITHUB_TOKEN": "ambient-canary",
            "GH_ENTERPRISE_TOKEN": "ambient-canary",
            "GITHUB_ENTERPRISE_TOKEN": "ambient-canary",
        },
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["ambient"] == []
    assert observed["host"] == github_host
    assert "selected-canary" in observed["config"]
