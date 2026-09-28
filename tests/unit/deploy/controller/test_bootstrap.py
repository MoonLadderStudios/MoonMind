"""Host-owned controller lifecycle: install/update/restore, never self-replace."""
import json

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

    def fake_run(args, **_kwargs):
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

    def missing_docker(args, **_kwargs):
        raise OSError("no docker here")

    monkeypatch.setattr(bootstrap, "_run_capture", missing_docker)
    assert (
        bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    )
    record = bootstrap.load_controller_image(tmp_path)
    assert record is not None and record["verified"] is False
    with pytest.raises(bootstrap.ImageResolutionError):
        bootstrap.main(["start", "--state-dir", str(tmp_path)], env={})


def _stack_network_lookup(names):
    """Docker's answer for the stack's controller-access network lookup."""
    from types import SimpleNamespace

    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args[:3] == ["docker", "network", "ls"]:
            return SimpleNamespace(
                returncode=0, stdout="".join(f"{name}\n" for name in names), stderr=""
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="offline")

    return fake_run, calls


def test_bootstrap_attaches_the_controller_to_the_stack_access_network(
    controller_path, tmp_path, monkeypatch
):
    import yaml

    bootstrap = load("bootstrap")
    fake_run, calls = _stack_network_lookup(["moonmind_deployment-controller-network"])
    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)

    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0

    # The network is found from the stack's own Compose labels, not a name
    # the operator must declare.
    [lookup] = [args for args in calls if args[:3] == ["docker", "network", "ls"]]
    assert "label=com.docker.compose.project=moonmind" in lookup
    assert f"label=com.docker.compose.network={bootstrap.STACK_NETWORK_KEY}" in lookup
    rendered = yaml.safe_load((tmp_path / "controller-compose.yaml").read_text())
    service = rendered["services"]["controller"]
    assert service["networks"]["stack"]["aliases"] == [bootstrap.STACK_NETWORK_ALIAS]
    # The host-loopback endpoint stays on the controller's own network.
    assert "default" in service["networks"]
    port = bootstrap.load_identity(tmp_path)["port"]
    assert service["ports"] == [f"127.0.0.1:{port}:{port}"]
    assert rendered["networks"]["stack"] == {
        "name": "moonmind_deployment-controller-network",
        "external": True,
    }


def test_bootstrap_without_a_stack_network_keeps_the_controller_independent(
    controller_path, tmp_path, monkeypatch
):
    import yaml

    bootstrap = load("bootstrap")
    fake_run, _ = _stack_network_lookup([])
    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)

    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0

    # A missing stack must never keep the controller from starting.
    rendered = yaml.safe_load((tmp_path / "controller-compose.yaml").read_text())
    assert "networks" not in rendered
    assert "networks" not in rendered["services"]["controller"]


def test_bootstrap_start_attaches_a_stack_network_created_after_install(
    controller_path, tmp_path, monkeypatch
):
    import yaml

    bootstrap = load("bootstrap")
    fake_run, _ = _stack_network_lookup([])
    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    bootstrap.record_controller_image(
        tmp_path,
        requested=bootstrap.DEFAULT_IMAGE,
        pinned="ghcr.io/org/ctl@sha256:" + "a" * 64,
    )
    fake_run, _ = _stack_network_lookup(["moonmind_deployment-controller-network"])
    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    started = []
    monkeypatch.setattr(
        bootstrap,
        "_compose",
        lambda state_dir, project, *args: started.append(args) or 0,
    )

    assert bootstrap.main(["start", "--state-dir", str(tmp_path)], env={}) == 0

    assert started == [("up", "-d", "--wait")]
    rendered = yaml.safe_load((tmp_path / "controller-compose.yaml").read_text())
    assert rendered["services"]["controller"]["image"] == (
        "ghcr.io/org/ctl@sha256:" + "a" * 64
    )
    assert rendered["networks"]["stack"]["name"] == (
        "moonmind_deployment-controller-network"
    )


class _FakeDocker:
    """Docker CLI answers for a stack and a running controller container."""

    def __init__(self, *, project="moonmind", networks=(), joined=None):
        self.project = project
        self.networks = {
            name: f"{name}_deployment-controller-network" for name in networks
        }
        self.joined = {} if joined is None else joined
        self.calls = []

    def __call__(self, args, cwd=None):
        from types import SimpleNamespace

        self.calls.append((args, cwd))

        def answer(stdout="", returncode=0):
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

        if args[:3] == ["docker", "compose", "config"]:
            return answer(json.dumps({"name": self.project}))
        if args[:3] == ["docker", "network", "ls"]:
            wanted = args[args.index("--filter") + 1].split("=", 1)[1]
            project = wanted.removeprefix("com.docker.compose.project=")
            return answer(
                f"{self.networks[project]}\n" if project in self.networks else ""
            )
        if args[:2] == ["docker", "ps"]:
            return answer("c0ffee\n")
        if args[:2] == ["docker", "inspect"]:
            return answer(json.dumps(self.joined))
        if args[:3] == ["docker", "network", "connect"]:
            self.joined[args[-2]] = {"Aliases": [args[args.index("--alias") + 1]]}
            return answer()
        return answer(returncode=1)


def _verified(bootstrap, state_dir):
    bootstrap.record_controller_image(
        state_dir,
        requested=bootstrap.DEFAULT_IMAGE,
        pinned="ghcr.io/org/ctl@sha256:" + "a" * 64,
    )


def test_bootstrap_finds_the_stack_network_of_the_derived_compose_project(
    controller_path, tmp_path, monkeypatch
):
    import yaml

    bootstrap = load("bootstrap")
    repo = tmp_path / "checkout"
    repo.mkdir()
    docker = _FakeDocker(project="site-a", networks=["site-a"])
    monkeypatch.setattr(bootstrap, "_run_capture", docker)
    state_dir = tmp_path / "state"

    assert bootstrap.main(
        ["install", "--state-dir", str(state_dir), "--repo", str(repo)], env={}
    ) == 0

    # The project comes from the checkout's own rendered Compose config (as
    # the host update derives it), not the literal stack name.
    assert (["docker", "compose", "config", "--format", "json"], repo) in docker.calls
    rendered = yaml.safe_load((state_dir / "controller-compose.yaml").read_text())
    assert rendered["networks"]["stack"]["name"] == (
        "site-a_deployment-controller-network"
    )


def test_bootstrap_records_a_started_controller_for_the_stack_services(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap, "_run_capture", _FakeDocker())
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    assert "startedAt" not in bootstrap.load_identity(tmp_path)
    _verified(bootstrap, tmp_path)
    monkeypatch.setattr(bootstrap, "_compose", lambda *_args: 0)

    assert bootstrap.main(["start", "--state-dir", str(tmp_path)], env={}) == 0

    started_at = bootstrap.load_identity(tmp_path)["startedAt"]
    # Later commands keep the record the stack services rely on.
    assert bootstrap.main(["restore", "--state-dir", str(tmp_path)], env={}) == 0
    assert bootstrap.load_identity(tmp_path)["startedAt"] == started_at


def test_bootstrap_start_that_created_no_container_records_no_start(
    controller_path, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap, "_run_capture", _FakeDocker())
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    _verified(bootstrap, tmp_path)
    monkeypatch.setattr(bootstrap, "_compose", lambda *_args: 1)
    monkeypatch.setattr(
        bootstrap,
        "_run_capture",
        lambda args, cwd=None: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError):
        bootstrap.main(["start", "--state-dir", str(tmp_path)], env={})

    assert "startedAt" not in bootstrap.load_identity(tmp_path)


def test_bootstrap_attach_joins_a_running_controller_to_a_stack_created_after_it(
    controller_path, tmp_path, monkeypatch
):
    import yaml

    bootstrap = load("bootstrap")
    docker = _FakeDocker(project="site-b")
    monkeypatch.setattr(bootstrap, "_run_capture", docker)
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    _verified(bootstrap, tmp_path)
    assert "networks" not in yaml.safe_load(
        (tmp_path / "controller-compose.yaml").read_text()
    )
    # The stack comes up after the controller is already running.
    docker.networks["site-b"] = "site-b_deployment-controller-network"

    assert bootstrap.main(["attach", "--state-dir", str(tmp_path)], env={}) == 0
    # The host update passes the project it derived; rerunning is a no-op.
    assert bootstrap.main(
        ["attach", "--state-dir", str(tmp_path), "--stack-project", "site-b"], env={}
    ) == 0

    connects = [
        args for args, _ in docker.calls if args[:3] == ["docker", "network", "connect"]
    ]
    # Joined once under the alias the stack services call; the second run
    # is a no-op, and the running container is never recreated.
    assert connects == [
        [
            "docker",
            "network",
            "connect",
            "--alias",
            bootstrap.STACK_NETWORK_ALIAS,
            "site-b_deployment-controller-network",
            "c0ffee",
        ]
    ]
    rendered = yaml.safe_load((tmp_path / "controller-compose.yaml").read_text())
    assert rendered["networks"]["stack"]["name"] == (
        "site-b_deployment-controller-network"
    )
    assert rendered["services"]["controller"]["image"] == (
        "ghcr.io/org/ctl@sha256:" + "a" * 64
    )


def test_stack_services_that_call_the_controller_find_what_bootstrap_installs(
    controller_path,
):
    """The Compose services that submit updates reach the installed controller.

    The API and the deployment worker discover the controller from the
    deployment state they mount and reach it on the stack network bootstrap
    attaches it to, with no operator-declared endpoint or secret.
    """
    from pathlib import Path

    import yaml

    bootstrap = load("bootstrap")
    repo = Path(__file__).resolve().parents[4]
    compose = yaml.safe_load((repo / "docker-compose.yaml").read_text())
    state_relative = bootstrap.default_state_dir(repo).relative_to(repo)
    network = compose["networks"][bootstrap.STACK_NETWORK_KEY]
    # Only the declared callers share it; it grants no egress.
    assert network["internal"] is True
    members = sorted(
        name
        for name, service in compose["services"].items()
        if bootstrap.STACK_NETWORK_KEY in (service.get("networks") or [])
    )
    assert members == ["api", "temporal-worker-deployment-control"]
    for name in members:
        service = compose["services"][name]
        environment = dict(
            entry.split("=", 1) for entry in service["environment"] if "=" in entry
        )
        for key in (
            "MOONMIND_CONTROLLER_URL",
            "MOONMIND_CONTROLLER_SECRET",
            "MOONMIND_CONTROLLER_SECRET_FILE",
        ):
            assert key not in environment, (name, key)
        assert environment["MOONMIND_CONTROLLER_HOST"] == bootstrap.STACK_NETWORK_ALIAS
        mounts = {
            volume.split(":")[1]: volume.split(":")[0]
            for volume in service["volumes"]
            if isinstance(volume, str) and volume.count(":") >= 1
        }
        state_dir = Path(environment["MOONMIND_CONTROLLER_STATE_DIR"])
        mount_target = next(
            target for target in mounts if state_dir.is_relative_to(target)
        )
        # The container path resolves to bootstrap's default host state dir.
        assert Path(mounts[mount_target]) / state_dir.relative_to(mount_target) == (
            Path(".") / state_relative
        )
