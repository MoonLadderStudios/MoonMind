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


def test_bootstrap_update_holds_the_target_stack_lock(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    held_during_compose = {}

    def fake_compose(state_dir, *args):
        held_during_compose["stack_locked"] = lock_mod.StackLock(
            state_dir, "moonmind"
        ).probe()
        return 0

    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    assert bootstrap.main(["update", "--state-dir", str(tmp_path)], env={}) == 0
    # Controller replacement shares the target stack's exclusion boundary,
    # the same lock submissions hold across pull/apply.
    assert held_during_compose.get("stack_locked") is True


def test_bootstrap_render_resolves_daemon_visible_bind_sources(
    controller_path, tmp_path
):
    bootstrap = load("bootstrap")
    from pathlib import Path

    rendered = bootstrap.render_compose_file(
        state_dir=tmp_path,
        repo=Path("/mnt/c/moonmind"),
    )
    content = rendered.read_text()
    assert "/run/desktop/mnt/host/c/moonmind" in content


def test_bootstrap_derives_stable_per_deployment_identity(
    controller_path, tmp_path
):
    import hashlib

    bootstrap = load("bootstrap")
    from pathlib import Path

    first = bootstrap.project_for_repo(Path("/srv/deploy-a"))
    assert first.startswith("moonmind-controller-")
    assert bootstrap.project_for_repo(Path("/srv/deploy-a")) == first
    assert bootstrap.project_for_repo(Path("/srv/deploy-b")) != first
    port_a = bootstrap.port_for_repo(Path("/srv/deploy-a"))
    port_b = bootstrap.port_for_repo(Path("/srv/deploy-b"))
    assert 8472 <= port_a < 8572
    assert 8472 <= port_b < 8572
    assert hashlib.sha256(b"x").hexdigest()  # sanity: stdlib hash only


def test_bootstrap_identity_persists_across_commands(controller_path, tmp_path):
    bootstrap = load("bootstrap")
    from pathlib import Path

    identity = bootstrap.ensure_identity(tmp_path, Path("/srv/deploy-a"), None)
    assert identity["project"].startswith("moonmind-controller-")
    assert identity["port"] == bootstrap.port_for_repo(Path("/srv/deploy-a"))
    # An explicit port wins; the recorded project stays stable.
    again = bootstrap.ensure_identity(tmp_path, Path("/srv/deploy-a"), 9999)
    assert again["project"] == identity["project"]
    assert again["port"] == 9999
    assert bootstrap.load_identity(tmp_path)["port"] == 9999


def test_bootstrap_resolve_image_digest_accepts_pinned_and_refuses_garbage(
    controller_path,
):
    import pytest

    bootstrap = load("bootstrap")
    pinned = "ghcr.io/org/ctl@sha256:" + "a" * 64
    assert bootstrap.resolve_image_digest(pinned) == "sha256:" + "a" * 64
    with pytest.raises(bootstrap.ImageResolutionError):
        bootstrap.resolve_image_digest("ghcr.io/org/ctl@sha256:nothex")


def test_bootstrap_resolve_image_digest_computes_manifest_digest(
    controller_path, monkeypatch
):
    import hashlib
    from types import SimpleNamespace

    bootstrap = load("bootstrap")
    raw = b'{"schemaVersion": 2}'
    calls = []

    def fake_run(args):
        calls.append(args)
        assert args[:4] == ["docker", "buildx", "imagetools", "inspect"]
        return SimpleNamespace(returncode=0, stdout=raw.decode(), stderr="")

    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    digest = bootstrap.resolve_image_digest("ghcr.io/org/ctl:latest")
    assert digest == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert calls


def test_bootstrap_offline_install_records_unverified_and_start_refuses(
    controller_path, tmp_path, monkeypatch
):
    import pytest

    bootstrap = load("bootstrap")

    def missing_docker(args):
        raise OSError("no docker here")

    monkeypatch.setattr(bootstrap, "_run_capture", missing_docker)
    assert (
        bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    )
    record = bootstrap.load_controller_image(tmp_path)
    assert record is not None and record["verified"] is False
    with pytest.raises(bootstrap.ImageResolutionError):
        bootstrap.main(["start", "--state-dir", str(tmp_path)], env={})
