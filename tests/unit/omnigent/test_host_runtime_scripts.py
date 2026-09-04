import subprocess

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
    assert "> /home/app/.omnigent/moonmind/bin/moonmind-opencode-context" in script
    assert "> /home/app/.omnigent/moonmind/bin/opencode" in script
    assert (
        "exec /home/app/.omnigent/moonmind/bin/moonmind-opencode-context "
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


def test_non_opencode_materializer_does_not_inject_opencode_runtime_flags():
    _script, environment = _build(target_path="/run/mm-credentials/other")

    assert not any(name.startswith("OPENCODE_") for name in environment)
    # The plugin npm cache seeding lives inside the MOONMIND_OPENCODE_RUNTIME
    # guard, which this projection never enables.
    assert "MOONMIND_OPENCODE_RUNTIME" not in environment
    assert set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")) == {
        "MOONMIND_ACTIVE_SKILLS_DIR",
        "MOONMIND_STEP_EXECUTION_ID",
    }


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


def test_opencode_projection_restores_scoped_fanout_file_selector() -> None:
    runtime_environment = {
        "MOONMIND_URL": "http://api:8000",
        "MOONMIND_AGENT_RUN_ID": "agent-run-1",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
        "MOONMIND_STEP_ID": "step-1",
        "MOONMIND_RUNTIME_ID": "opencode-native",
        "MOONMIND_REPOSITORY_CONNECTION_REF": (
            "repository-connection:git-default"
        ),
        "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE": (
            "/run/moonmind-host-auth/execution-fanout"
        ),
    }
    script, environment = _build(
        target_path="",
        enable_opencode_runtime=True,
        runtime_environment=runtime_environment,
    )

    passthrough = set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(","))
    assert set(runtime_environment) <= passthrough
    assert {
        key: environment[key] for key in runtime_environment
    } == runtime_environment
    for key in runtime_environment:
        assert f"export {key}=$(cat " in script
    assert "MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN=" not in script
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
