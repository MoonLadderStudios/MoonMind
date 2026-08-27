from moonmind.omnigent.host_services.runtime_scripts import (
    OmnigentRuntimeScriptService,
)


def _build(*, target_path: str):
    return OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[
            {
                "credentialGeneration": 3,
                "attachments": [{"targetPath": target_path}],
            }
        ],
        skill_attachment={"targetPath": "/opt/moonmind/skills_active"},
    )


def test_opencode_materializer_pins_deterministic_server_startup_environment():
    _script, environment = _build(target_path="/run/mm-credentials/opencode")

    expected = {
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_DISABLE_MODELS_FETCH",
    }
    assert {name for name in expected if environment[name] == "1"} == expected
    assert set(environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"].split(",")) == expected


def test_non_opencode_materializer_does_not_inject_opencode_runtime_flags():
    _script, environment = _build(target_path="/run/mm-credentials/other")

    assert not any(name.startswith("OPENCODE_") for name in environment)
    assert "OMNIGENT_RUNNER_ENV_PASSTHROUGH" not in environment
