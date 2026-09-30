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
    monkeypatch.setattr(
        bootstrap, "ensure_target_network", lambda network, project: None
    )
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


def test_bootstrap_install_links_the_controller_to_the_api_network(
    controller_path, tmp_path
):
    bootstrap = load("bootstrap")
    repo = tmp_path / "MoonMind"
    repo.mkdir()
    (repo / ".env").write_text(
        "MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK=site_controller-link\n"
    )
    state = tmp_path / "state"
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(state), "--repo", str(repo)], env={}
        )
        == 0
    )
    rendered = (state / "controller-compose.yaml").read_text()
    # The API reaches the controller by alias on the deployment-owned private
    # network named by the same setting Compose interpolates for the API.
    assert "aliases:\n          - moonmind-controller" in rendered
    assert "name: site_controller-link\n    external: true" in rendered
    # The controller derives UI submission targets from this read-only mount.
    assert f'MOONMIND_CONTROLLER_TARGET_REPO: "{repo.resolve()}"' in rendered
    identity = bootstrap.load_identity(state)
    assert identity["targetNetwork"] == "site_controller-link"
    assert identity["targetProject"] == "moonmind"


def test_bootstrap_start_creates_a_missing_api_network_before_up(
    controller_path, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    bootstrap = load("bootstrap")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = tmp_path / "state"
    calls = []

    def fake_run(args):
        calls.append(("run", tuple(args)))
        if args[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        if args[:3] == ["docker", "network", "create"]:
            return SimpleNamespace(returncode=0, stdout="id", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_compose(state_dir, project, *args):
        calls.append(("compose", args))
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    monkeypatch.setattr(
        bootstrap, "require_verified_image", lambda state_dir, requested: "img@sha256:x"
    )
    assert bootstrap.main(
        ["install", "--state-dir", str(state), "--repo", str(repo)], env={}
    ) == 0
    calls.clear()
    assert bootstrap.main(
        ["start", "--state-dir", str(state), "--repo", str(repo)], env={}
    ) == 0
    create = [
        args for kind, args in calls if kind == "run" and args[:3] == ("docker", "network", "create")
    ]
    assert create == [
        (
            "docker",
            "network",
            "create",
            "--internal",
            "--label",
            "com.docker.compose.project=repo",
            "--label",
            "com.docker.compose.network=deployment-controller-network",
            "moonmind_deployment-controller-network",
        )
    ]
    kinds = [kind for kind, _ in calls]
    assert kinds.index("compose") > kinds.index("run")


def test_only_trusted_deployment_services_join_the_controller_link(controller_path):
    """The controller endpoint is reachable by the API and deployment worker only."""
    import re
    from pathlib import Path

    import yaml

    bootstrap = load("bootstrap")
    compose = yaml.safe_load(
        (Path(controller_path).parents[1] / "docker-compose.yaml").read_text()
    )
    link = compose["networks"][bootstrap.TARGET_NETWORK_KEY]
    assert link["internal"] is True
    # Compose names the network from the same setting bootstrap resolves.
    assert link["name"] == (
        f"${{{bootstrap.TARGET_NETWORK_SETTING}:-{bootstrap.DEFAULT_TARGET_NETWORK}}}"
    )
    attached = sorted(
        name
        for name, service in compose["services"].items()
        if bootstrap.TARGET_NETWORK_KEY in (service.get("networks") or [])
    )
    assert attached == ["api", "temporal-worker-deployment-control"]
    for name in attached:
        environment = compose["services"][name]["environment"]
        state_dirs = [
            item
            for item in environment
            if re.match(r"^MOONMIND_CONTROLLER_STATE_DIR=", item)
        ]
        # The controller state is read from the deployment state mount; the
        # controller secret is never an environment value.
        assert state_dirs == [
            "MOONMIND_CONTROLLER_STATE_DIR=/workspace/deployment_state/controller"
        ]
        assert not any("CONTROLLER_SECRET" in item for item in environment)


def _compose_mounts(service):
    """Yield (type, source, target, read_only) for a service's volumes.

    Short-syntax entries use each ``${NAME:-default}`` default, which is what
    a default install renders.
    """
    import re

    for raw in service.get("volumes") or []:
        if isinstance(raw, dict):
            yield (
                raw.get("type"),
                raw.get("source"),
                raw["target"],
                raw.get("read_only") is True,
            )
            continue
        spec = re.sub(r"\$\{[A-Z0-9_]+(?::-([^}]*))?\}", lambda m: m.group(1) or "", raw)
        source, target, *mode = spec.split(":")
        kind = "bind" if source.startswith((".", "/")) else "volume"
        yield kind, source, target, "ro" in (mode[0].split(",") if mode else [])


def test_no_service_outside_the_controller_link_can_see_controller_state(
    controller_path,
):
    """Only the API (read-only) and deployment worker see the controller state.

    Bootstrap keeps the controller bearer secret and the operation records
    the controller applies on restart in the checkout's deployment state.
    Agent-facing services mount parts of that checkout, so each view they
    have of the controller state must be an empty read-only tmpfs.
    """
    from pathlib import Path, PurePosixPath

    import yaml

    bootstrap = load("bootstrap")
    repo = Path(controller_path).parents[1]
    compose = yaml.safe_load((repo / "docker-compose.yaml").read_text())
    state = PurePosixPath(*bootstrap.default_state_dir(Path(".")).parts)
    trusted = {
        name
        for name, service in compose["services"].items()
        if bootstrap.TARGET_NETWORK_KEY in (service.get("networks") or [])
    }
    exposed_views = {}
    for name, service in compose["services"].items():
        mounts = list(_compose_mounts(service))
        shadows = {
            target for kind, _, target, read_only in mounts
            if kind == "tmpfs" and read_only
        }
        for kind, source, target, read_only in mounts:
            if kind != "bind" or not source.startswith("."):
                continue
            source = PurePosixPath(source)
            if source != state and source not in state.parents:
                continue
            view = str(PurePosixPath(target) / state.relative_to(source))
            if name == "api":
                assert read_only, "the API reads controller state read-only"
            if name in trusted:
                continue
            exposed_views.setdefault(name, []).append(view)
            assert view in shadows, f"{name} can reach controller state at {view}"
    # The agent runtime hosts managed Skills and mounts both the checkout and
    # the deployment state, so both views are shadowed.
    assert sorted(exposed_views["temporal-worker-agent-runtime"]) == [
        "/workspace/deployment_state/controller",
        "/workspace/host_project/deploy/state/controller",
    ]
    # The (otherwise ignored) state directory is part of the checkout, so
    # Docker never creates that mountpoint as the daemon's user before
    # bootstrap, running as the operator, writes there.
    assert (repo / state).is_dir()
