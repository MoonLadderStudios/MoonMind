from pathlib import Path

import yaml


def test_static_hosts_project_versioned_tools_without_covering_usr_local_bin() -> None:
    compose = yaml.safe_load(Path("docker-compose.yaml").read_text())
    expected_path = (
        "/opt/moonmind-tools/bin:${OMNIGENT_HOST_BASE_PATH:-/opt/venv/bin:"
        "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin}"
    )

    for name in ("omnigent-host", "omnigent-host-claude", "omnigent-host-codex"):
        service = compose["services"][name]
        environment = service["environment"]
        if isinstance(environment, list):
            assert f"PATH={expected_path}" in environment
        else:
            assert environment["PATH"] == expected_path
        assert "omnigent-tools:/opt/moonmind-tools:ro" in service["volumes"]
        assert service["depends_on"]["omnigent-tools-init"] == {
            "condition": "service_completed_successfully"
        }
        assert (
            "./services/omnigent/scripts/moonmind-tools.sh:"
            "/etc/profile.d/moonmind-tools.sh:ro"
        ) in service["volumes"]
        assert all("/usr/local/bin" not in volume for volume in service["volumes"])

    assert compose["volumes"]["omnigent-tools"]["name"] == (
        "moonmind-omnigent-tools-gh-${OMNIGENT_GH_VERSION:-2.76.2}"
    )
    assert "profiles" not in compose["services"]["omnigent-tools-init"]
    assert compose["services"]["temporal-worker-agent-runtime"]["depends_on"][
        "omnigent-tools-init"
    ] == {"condition": "service_completed_successfully"}


def test_login_profile_prepends_tools_path_idempotently() -> None:
    profile = Path("services/omnigent/scripts/moonmind-tools.sh").read_text()

    assert "*:/opt/moonmind-tools/bin:*)" in profile
    assert 'export PATH="/opt/moonmind-tools/bin${PATH:+:$PATH}"' in profile


def test_codex_host_materializes_gh_auth_outside_workspace_and_drops_token() -> None:
    # The generic static entrypoint owns GitHub materialization
    # (MoonLadderStudios/MoonMind#3834); the Codex wrapper only selects the
    # trusted pack ref and delegates.
    script = Path(
        "services/omnigent/scripts/start-omnigent-host.sh"
    ).read_text()

    materialize_index = script.index("github_config_home=")
    unset_index = script.index("unset github_token GH_TOKEN")
    host_start_index = script.index("exec omnigent host")

    assert "/home/app/.cache/moonmind-xdg" in script
    assert "/workspaces/run" not in script
    assert 'printf \'    oauth_token: %s\\n\' "$github_token"' in script
    assert materialize_index < unset_index < host_start_index

    wrapper = Path(
        "services/omnigent/scripts/start-codex-oauth-host.sh"
    ).read_text()
    assert "MOONMIND_OMNIGENT_RUNTIME_PACK_REF=codex-native-pack@1" in wrapper
    assert "exec /opt/moonmind/start-omnigent-host.sh" in wrapper
    assert "github_config_home" not in wrapper
