"""Host-owned controller lifecycle: install/update/restore, never self-replace."""
from conftest import load

import pytest


def test_bootstrap_refuses_to_run_inside_the_controller(controller_path, tmp_path):
    bootstrap = load("bootstrap")
    with pytest.raises(bootstrap.InsideControllerError):
        bootstrap.main(
            ["install", "--state-dir", str(tmp_path)],
            env={"MOONMIND_CONTROLLER_MANAGED": "1"},
        )


def test_bootstrap_install_writes_compose_project_and_secret(
    controller_path, tmp_path
):
    bootstrap = load("bootstrap")
    assert (
        bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    )
    rendered = (tmp_path / "controller-compose.yaml").read_text()
    assert "moonmind-controller" in rendered
    assert "/var/run/docker.sock" in rendered
    assert "unless-stopped" in rendered
    secret_file = tmp_path / "secrets" / "controller-bearer"
    assert secret_file.exists()
    assert len(secret_file.read_text().strip()) >= 32


def test_bootstrap_update_serializes_against_active_mutation(
    controller_path, tmp_path
):
    bootstrap = load("bootstrap")
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    record = load("record")
    store = record.OperationStore(tmp_path)
    store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    with pytest.raises(bootstrap.ActiveOperationError):
        bootstrap.main(["update", "--state-dir", str(tmp_path)], env={})
