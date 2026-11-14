"""First Compose start adopts persisted controller state without another writer."""

import json

import pytest
from conftest import load


def snapshot(repo, state, *, project="moonmind-test-first", image="sha256:abc"):
    return {
        "Id": "self",
        "Image": image,
        "Config": {
            "Image": "ghcr.io/moonladderstudios/moonmind:latest",
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": "controller",
                "moonmind.controller.managed": "true",
            },
        },
        "Mounts": [
            {"Type": "bind", "Source": str(repo), "Destination": "/deployment"},
            {
                "Type": "bind",
                "Source": str(state),
                "Destination": "/var/lib/moonmind-controller",
            },
        ],
        "NetworkSettings": {
            "Networks": {
                project + "_deployment-controller-network": {
                    "Aliases": ["moonmind-controller"]
                }
            },
            "Ports": {"8472/tcp": [{"HostIp": "127.0.0.1", "HostPort": "32100"}]},
        },
    }


def test_fresh_compose_initializes_same_project_and_preserves_secret(
    controller_path, tmp_path
):
    start = load("compose_start")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    row = snapshot(repo, state)
    commands = []

    def docker(*args):
        commands.append(args)
        return ""

    identity = start.initialize(state, row, docker=docker)
    assert identity["project"] == identity["targetProject"] == "moonmind-test-first"
    assert identity["port"] == 8472
    assert identity["hostPort"] == 32100
    bearer = (state / "secrets/controller-bearer").read_bytes()
    start.initialize(state, row, docker=docker)
    assert (state / "secrets/controller-bearer").read_bytes() == bearer
    manifest = (state / "controller-compose.yaml").read_text()
    assert "name: moonmind-test-first" in manifest
    assert "127.0.0.1:32100:8472" in manifest
    assert str(repo) in manifest
    assert (
        json.loads((state / "controller-image.json").read_text())["pinned"]
        == "sha256:abc"
    )


def test_stale_identity_without_old_container_is_repaired(controller_path, tmp_path):
    start = load("compose_start")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "old-owner", "port": 8520})
    )
    calls = []

    def docker(*args):
        calls.append(args)
        return "" if args[0] == "ps" else "[]"

    identity = start.initialize(state, snapshot(repo, state), docker=docker)
    assert identity["project"] == "moonmind-test-first"
    assert not any(c[0] in ("stop", "rm") for c in calls)


def test_legacy_live_owner_stopped_only_under_shared_lock(controller_path, tmp_path):
    start = load("compose_start")
    lock = start.lock_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "old-owner", "port": 8520})
    )
    row = snapshot(repo, state, project="old-owner")
    row["Id"] = "old-container"
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "ps":
            return "old-container"
        if args[0] == "inspect":
            return json.dumps([row])
        assert lock.StackLock(state, "moonmind").probe()
        return ""

    start.initialize(state, snapshot(repo, state), docker=docker)
    assert ("stop", "--time", "30", "old-container") in calls
    assert ("rm", "old-container") in calls


def test_busy_owner_is_not_stopped_or_overwritten(controller_path, tmp_path):
    start = load("compose_start")
    lock = start.lock_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    original = json.dumps({"project": "old-owner", "port": 8520})
    (state / "controller-identity.json").write_text(original)
    with lock.StackLock(state, "moonmind").acquire(), pytest.raises(lock.LockBusyError):
        start.initialize(
            state,
            snapshot(repo, state),
            docker=lambda *a: pytest.fail("Docker mutation while busy"),
        )
    assert (state / "controller-identity.json").read_text() == original


def test_wrong_state_owner_is_not_stopped(controller_path, tmp_path):
    start = load("compose_start")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "old-owner", "port": 8520})
    )
    other = snapshot(repo, tmp_path / "other", project="old-owner")

    def docker(*args):
        if args[0] == "ps":
            return "foreign"
        if args[0] == "inspect":
            return json.dumps([other])
        pytest.fail("must not stop an unowned container")

    with pytest.raises(RuntimeError, match="state bind"):
        start.initialize(state, snapshot(repo, state), docker=docker)


def test_refresh_cannot_downgrade_integrated_controller_safety(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    state.mkdir()
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "app", "targetProject": "app", "port": 8472})
    )
    compose = state / "controller-compose.yaml"
    compose.write_text(
        'name: app\nservices:\n  controller:\n    image: old\n    user: "0:0"\n'
    )
    monkeypatch.setattr(
        bootstrap, "controller_image_supports_compose_ownership", lambda *a, **kw: False
    )
    with pytest.raises(RuntimeError, match="Compose-owned"):
        bootstrap._retargeted_controller_lines(compose, "older")
    assert "image: old" in compose.read_text()


def test_lost_identity_discovers_prior_owner_by_exact_state_bind(
    controller_path, tmp_path
):
    start = load("compose_start")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    old = snapshot(repo, state, project="legacy")
    old["Id"] = "legacy-id"
    commands = []

    def docker(*args):
        commands.append(args)
        if args[0] == "ps":
            return "legacy-id"
        if args[0] == "inspect":
            return json.dumps([old])
        return ""

    start.initialize(state, snapshot(repo, state), docker=docker)
    assert ("stop", "--time", "30", "legacy-id") in commands


def test_host_rollback_keeps_safe_integrated_updater(
    controller_path, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    (state / "controller-identity.json").write_text(
        json.dumps(
            {"project": "app", "targetProject": "app", "port": 8472, "hostPort": 32100}
        )
    )
    manifest = state / "controller-compose.yaml"
    manifest.write_text("services:\n  controller:\n    image: safe-current\n")
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *a: set(bootstrap.REQUIRED_CAPABILITIES),
    )
    monkeypatch.setattr(
        bootstrap, "_application_controller_image", lambda *a: "old-updater"
    )
    monkeypatch.setattr(
        bootstrap, "controller_image_supports_compose_ownership", lambda *a: False
    )
    monkeypatch.setattr(
        bootstrap,
        "_compose_up",
        lambda *a, **kw: pytest.fail("must retain safe updater"),
    )
    assert (
        bootstrap.cmd_ensure(
            SimpleNamespace(
                state_dir=str(state),
                controller_url=None,
                stack="moonmind",
                image="old-app",
                target_project="app",
                prefer_requested=False,
            ),
            {},
        )
        == 0
    )
    assert "safe-current" in manifest.read_text()


def test_live_owner_with_open_work_is_not_interrupted(controller_path, tmp_path):
    start = load("compose_start")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "legacy", "port": 8520})
    )
    start.record_mod.OperationStore(state).begin(
        stack="moonmind", desired_image="app:next", source_revision=""
    )
    old = snapshot(repo, state, project="legacy")
    old["State"] = {"Running": True}

    def docker(*args):
        if args[0] == "ps":
            return "legacy-id"
        if args[0] == "inspect":
            return json.dumps([old])
        pytest.fail("active owner must not be stopped")

    with pytest.raises(RuntimeError, match="open deployment work"):
        start.initialize(state, snapshot(repo, state), docker=docker)


def test_direct_lifecycle_cannot_erase_safe_image_on_unsafe_rollback(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    state.mkdir()
    (state / "controller-identity.json").write_text(
        json.dumps({"project": "app", "targetProject": "app"})
    )
    bootstrap.record_controller_image(state, requested="safe", pinned="sha256:safe")
    original = (state / "controller-image.json").read_bytes()
    monkeypatch.setattr(
        bootstrap, "resolve_image_digest", lambda image: "sha256:" + "a" * 64
    )
    monkeypatch.setattr(
        bootstrap, "controller_image_supports_compose_ownership", lambda image: False
    )
    with pytest.raises(bootstrap.ImageResolutionError, match="Compose-owned"):
        bootstrap.pinned_controller_image(
            state, "ghcr.io/moonladderstudios/moonmind:old"
        )
    assert (state / "controller-image.json").read_bytes() == original


def test_host_access_changes_only_root_owned_bootstrap_paths(
    controller_path, tmp_path, monkeypatch
):
    from pathlib import Path
    from types import SimpleNamespace

    start = load("compose_start")
    repo = tmp_path / "repo"
    state = tmp_path / "state/controller"
    secret = state / "secrets/controller-bearer"
    paths = (
        state,
        secret.parent,
        secret,
        state / "controller-image.json",
        state / "controller-compose.yaml",
        state / "controller-identity.json",
    )
    changed = []
    modes = []

    def stat(path, **kwargs):
        if path == repo:
            return SimpleNamespace(st_uid=1234, st_gid=2345)
        assert path in paths
        return SimpleNamespace(st_uid=0, st_gid=0)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(start.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        start.os, "chown", lambda path, uid, gid: changed.append((path, uid, gid))
    )
    monkeypatch.setattr(
        start.os, "chmod", lambda path, mode: modes.append((path, mode))
    )
    start.prepare_host_access(state, repo)
    assert changed == [(path, 1234, 2345) for path in paths]
    assert modes == [(secret, 0o600)]
