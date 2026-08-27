from moonmind.omnigent.host_services.runtime_scripts import (
    OmnigentRuntimeScriptService,
)


def _build(*, target_path: str, github_attachment=None):
    return OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[
            {
                "credentialGeneration": 3,
                "attachments": [{"targetPath": target_path}],
            }
        ],
        skill_attachment={"targetPath": "/opt/moonmind-skills"},
        github_credential_attachment=github_attachment,
    )


def test_opencode_materializer_pins_deterministic_server_startup_environment():
    _script, environment = _build(target_path="/run/mm-credentials/opencode")

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
        expected_flags | expected_proxies | {"MOONMIND_ACTIVE_SKILLS_DIR"}
    )


def test_non_opencode_materializer_does_not_inject_opencode_runtime_flags():
    _script, environment = _build(target_path="/run/mm-credentials/other")

    assert not any(name.startswith("OPENCODE_") for name in environment)
    assert environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] == (
        "MOONMIND_ACTIVE_SKILLS_DIR"
    )


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
        "!/home/app/.local/bin/gh auth git-credential"
    )
    assert environment["PATH"].startswith("/home/app/.local/bin:")
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
    assert "> /home/app/.local/bin/gh" in _script
