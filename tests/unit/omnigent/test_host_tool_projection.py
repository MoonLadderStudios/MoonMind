from pathlib import Path

import yaml


def test_static_hosts_use_image_owned_tools_without_volume_overlays() -> None:
    # MoonLadderStudios/MoonMind#4558: the selected image owns gh/moonmind.
    # PATH still leads with the image-owned tools directory, but no service
    # mounts a tools volume, binds a profile snippet over the image-owned
    # one, or waits on a tools initializer.
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
        volumes = " ".join(str(item) for item in service.get("volumes", []))
        assert "/opt/moonmind-tools" not in volumes, name
        assert "moonmind-tools.sh" not in volumes, name
        assert "omnigent-tools-init" not in service.get("depends_on", {}), name
        assert all("/usr/local/bin" not in volume for volume in service["volumes"])

    assert "omnigent-tools" not in compose.get("volumes", {})
    assert "omnigent-tools-init" not in compose["services"]
    assert "omnigent-tools-init" not in compose["services"][
        "temporal-worker-agent-runtime"
    ].get("depends_on", {})


def test_login_profile_prepends_tools_path_idempotently() -> None:
    # The profile snippet is now baked into the image from this source; host
    # launch relies on the image-owned copy at /etc/profile.d/.
    profile = Path("services/omnigent/scripts/moonmind-tools.sh").read_text()

    assert "*:/opt/moonmind-tools/bin:*)" in profile
    assert 'export PATH="/opt/moonmind-tools/bin${PATH:+:$PATH}"' in profile

    dockerfile = Path("services/omnigent/moonmind-host/Dockerfile").read_text()
    assert "services/omnigent/scripts/moonmind-tools.sh" in dockerfile
    assert "/etc/profile.d/moonmind-tools.sh" in dockerfile


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
