from pathlib import Path


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
    # The shared host entrypoint owns GitHub materialization for every
    # runtime pack.
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
