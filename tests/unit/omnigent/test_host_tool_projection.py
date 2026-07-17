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


def test_login_profile_prepends_tools_path_idempotently() -> None:
    profile = Path("services/omnigent/scripts/moonmind-tools.sh").read_text()

    assert "*:/opt/moonmind-tools/bin:*)" in profile
    assert 'export PATH="/opt/moonmind-tools/bin:${PATH}"' in profile
