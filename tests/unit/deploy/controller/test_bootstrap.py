"""Host-owned controller lifecycle: install/update/restore, never self-replace."""
import pytest
from conftest import load


@pytest.mark.parametrize("image_user", ["", "1200:1200"])
def test_ensure_refreshes_old_controller_from_installed_source_before_rollback(
    controller_path, tmp_path, monkeypatch, image_user
):
    import json
    import subprocess

    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "deploy" / "state" / "controller"
    state.mkdir(parents=True)
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, repo, 8472, target_project="installed")
    compose = bootstrap.render_compose_file(state_dir=state, repo=repo)
    original_mounts = compose.read_text().split("    volumes:", 1)[1]
    source_image = "sha256:" + "a" * 64
    upgraded = False
    commands = []

    def run(command):
        commands.append(command)
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, "source-api\n", "")
        if command[1] == "inspect":
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    [
                        {
                            "Image": source_image,
                            "Config": {"Labels": {"com.docker.compose.service": "api"}},
                        }
                    ]
                ),
                "",
            )
        if command[1] == "run":
            assert source_image in command
            assert "--network=none" in command
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["image", "inspect"]:
            assert command[4] == "{{json .Config.User}}"
            return subprocess.CompletedProcess(command, 0, json.dumps(image_user), "")
        raise AssertionError(command)

    def compose_run(state_dir, project, *args):
        nonlocal upgraded
        assert lock_mod.StackLock(state_dir, "moonmind").probe()
        assert "--pull" in args and "never" in args
        upgraded = True
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: ({"active-journal-transition"} if upgraded else set()),
    )
    monkeypatch.setattr(bootstrap, "_compose", compose_run)
    assert (
        bootstrap.main(
            [
                "ensure",
                "--state-dir",
                str(state),
                "--repo",
                str(repo),
                "--image",
                "ghcr.io/org/moonmind:old",
                "--controller-url",
                "http://controller:8472",
            ],
            env={},
        )
        == 0
    )
    text = compose.read_text()
    assert f"image: {source_image}" in text
    assert 'entrypoint: ["python", "/app/deploy/controller/server.py"]' in text
    assert f'    user: {json.dumps(image_user or "0:0")}' in text
    assert text.split("    volumes:", 1)[1] == original_mounts
    assert bootstrap.load_controller_image(state)["pinned"] == source_image
    before = list(commands)
    assert (
        bootstrap.main(
            [
                "ensure",
                "--state-dir",
                str(state),
                "--repo",
                str(repo),
                "--image",
                "ghcr.io/org/moonmind:old",
                "--controller-url",
                "http://controller:8472",
            ],
            env={},
        )
        == 0
    )
    assert commands == before


def test_ensure_never_replaces_controller_with_open_operation(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472)
    bootstrap.render_compose_file(state_dir=state, repo=tmp_path)
    load("record").OperationStore(state).begin(
        stack="moonmind", desired_image="ghcr.io/org/app:next", source_revision=""
    )
    monkeypatch.setattr(bootstrap, "controller_capabilities", lambda *args: set())
    monkeypatch.setattr(
        bootstrap, "_run_capture", lambda *args: pytest.fail("Docker touched")
    )
    with pytest.raises(bootstrap.ActiveOperationError):
        bootstrap.main(
            ["ensure", "--state-dir", str(state), "--repo", str(tmp_path)], env={}
        )


def test_controller_prerequisite_uses_staged_target_when_installed_source_is_old(
    controller_path, monkeypatch
):
    import json
    import subprocess

    bootstrap = load("bootstrap")
    target = "ghcr.io/org/app:new"
    concrete = "sha256:" + "b" * 64
    commands = []

    def run(command):
        commands.append(command)
        result, output = 0, ""
        if command[1] == "ps":
            output = "installed-api"
        elif command[1] == "inspect":
            output = json.dumps(
                [
                    {
                        "Image": "sha256:old",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        elif command[1] == "run":
            result = 10 if "sha256:old" in command else 0
        elif command[1] == "image":
            output = concrete
        else:
            assert command == ["docker", "pull", target]
        return subprocess.CompletedProcess(command, result, output, "")

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    assert bootstrap._application_controller_image("installed", target) == concrete
    assert ["docker", "pull", target] in commands
    probes = [command for command in commands if command[1] == "run"]
    assert "sha256:old" in probes[0]
    assert concrete in probes[1]


def test_controller_prerequisite_can_retry_failed_recreation(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472)
    compose = bootstrap.render_compose_file(state_dir=state, repo=tmp_path)
    concrete = "sha256:" + "b" * 64
    attempts = []
    upgraded = False

    def recreate(*args):
        nonlocal upgraded
        attempts.append(compose.read_text())
        if len(attempts) == 1:
            return 1
        upgraded = True
        return 0

    def capabilities(*args):
        if attempts and not upgraded:
            raise ConnectionRefusedError("old controller stopped before recreation failed")
        return {"active-journal-transition"} if upgraded else set()

    monkeypatch.setattr(
        bootstrap, "_application_controller_image", lambda *args: concrete
    )
    monkeypatch.setattr(bootstrap, "controller_capabilities", capabilities)
    monkeypatch.setattr(bootstrap, "_checked_capture", lambda _args: '""')
    monkeypatch.setattr(bootstrap, "_compose", recreate)
    args = [
        "ensure",
        "--state-dir",
        str(state),
        "--repo",
        str(tmp_path),
        "--image",
        "ghcr.io/org/app:next",
    ]
    with pytest.raises(RuntimeError, match="recreation failed"):
        bootstrap.main(args, env={})
    assert bootstrap.load_controller_image(state)["pinned"] == concrete
    assert bootstrap.main(args, env={}) == 0
    assert attempts[0] == attempts[1]


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
            "repo_deployment-controller-network",
        )
    ]
    kinds = [kind for kind, _ in calls]
    assert kinds.index("compose") > kinds.index("run")


@pytest.mark.parametrize(
    ("dirname", "env", "argv", "expected"),
    [
        ("MoonMind", {}, [], "moonmind_deployment-controller-network"),
        ("moonmind-b", {}, [], "moonmind-b_deployment-controller-network"),
        (
            "MoonMind",
            {"COMPOSE_PROJECT_NAME": "site"},
            [],
            "site_deployment-controller-network",
        ),
        (
            "MoonMind",
            {},
            ["--target-project", "flagged"],
            "flagged_deployment-controller-network",
        ),
        (
            "MoonMind",
            {"MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK": "pinned"},
            [],
            "pinned",
        ),
    ],
)
def test_bootstrap_default_link_network_follows_the_compose_project(
    controller_path, tmp_path, dirname, env, argv, expected
):
    """Independent deployments never share the controller alias network."""
    bootstrap = load("bootstrap")
    repo = tmp_path / dirname
    repo.mkdir()
    state = tmp_path / "state"
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(state), "--repo", str(repo), *argv],
            env=env,
        )
        == 0
    )
    assert bootstrap.load_identity(state)["targetNetwork"] == expected
    rendered = (state / "controller-compose.yaml").read_text()
    assert f"name: {expected}\n    external: true" in rendered


def test_bootstrap_passes_the_recorded_target_project_to_the_controller(
    controller_path, tmp_path
):
    """A `-p` deployment's project reaches the controller's derived target."""
    bootstrap = load("bootstrap")
    repo = tmp_path / "MoonMind"
    repo.mkdir()
    state = tmp_path / "state"
    assert (
        bootstrap.main(
            [
                "install",
                "--state-dir",
                str(state),
                "--repo",
                str(repo),
                "--target-project",
                "my-instance",
            ],
            env={},
        )
        == 0
    )
    assert bootstrap.load_identity(state)["targetProject"] == "my-instance"
    rendered = (state / "controller-compose.yaml").read_text()
    assert 'MOONMIND_CONTROLLER_TARGET_PROJECT: "my-instance"' in rendered


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
        f"${{{bootstrap.TARGET_NETWORK_SETTING}:-"
        f"${{COMPOSE_PROJECT_NAME:-moonmind}}_{bootstrap.TARGET_NETWORK_KEY}}}"
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


def test_install_from_the_application_image_runs_its_shipped_controller(
    controller_path, tmp_path
):
    """The standalone controller image is unpublished, so the default install
    uses the MoonMind release image, which ships deploy/controller."""
    bootstrap = load("bootstrap")
    image = "ghcr.io/moonladderstudios/moonmind@sha256:" + "a" * 64
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(tmp_path), "--image", image], env={}
        )
        == 0
    )
    rendered = (tmp_path / "controller-compose.yaml").read_text()
    assert f"    image: {image}\n" in rendered
    assert '    entrypoint: ["python", "/app/deploy/controller/server.py"]\n' in rendered
    # The same Docker-socket authority as the root standalone image.
    assert '    user: "0:0"\n' in rendered
    # Already digest-pinned: verified without a registry round trip.
    assert bootstrap.load_controller_image(tmp_path)["verified"] is True


def test_standalone_controller_image_keeps_its_own_entrypoint(
    controller_path, tmp_path
):
    bootstrap = load("bootstrap")
    rendered = bootstrap.render_compose_file(
        state_dir=tmp_path,
        repo=tmp_path,
        image="ghcr.io/moonladderstudios/moonmind-controller@sha256:" + "b" * 64,
    ).read_text()
    assert "entrypoint:" not in rendered
    assert "    user:" not in rendered


def test_ensure_keeps_the_application_image_process_identity(
    controller_path, tmp_path, monkeypatch
):
    """ensure refreshes an application-image install without a second user."""
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472, target_project="installed")
    compose = bootstrap.render_compose_file(
        state_dir=state,
        repo=tmp_path,
        image="ghcr.io/moonladderstudios/moonmind@sha256:" + "c" * 64,
    )
    upgraded = False

    def compose_run(*args):
        nonlocal upgraded
        upgraded = True
        return 0

    monkeypatch.setattr(
        bootstrap, "_application_controller_image", lambda *args: "sha256:" + "d" * 64
    )
    monkeypatch.setattr(bootstrap, "_compose", compose_run)
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: ({"active-journal-transition"} if upgraded else set()),
    )
    assert (
        bootstrap.main(
            ["ensure", "--state-dir", str(state), "--repo", str(tmp_path)], env={}
        )
        == 0
    )
    text = compose.read_text()
    assert text.count("    entrypoint:") == 1
    assert text.count("    user:") == 1
    assert '    user: "0:0"\n' in text


def test_controller_capabilities_never_traverse_an_ambient_proxy(
    controller_path, monkeypatch
):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    bootstrap = load("bootstrap")
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            body = json.dumps({"capabilities": ["active-journal-transition"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    try:
        capabilities = bootstrap.controller_capabilities(
            f"http://127.0.0.1:{server.server_address[1]}", "bearer-value"
        )
    finally:
        server.shutdown()
        server.server_close()
    assert capabilities == {"active-journal-transition"}
    assert seen == ["Bearer bearer-value"]


@pytest.mark.parametrize("command", ["restore", "update", "start"])
def test_lifecycle_without_an_image_keeps_the_installed_application_controller(
    controller_path, tmp_path, monkeypatch, command
):
    """A repair never swaps the host-installed controller for an unpublished image."""
    bootstrap = load("bootstrap")
    installed = "ghcr.io/moonladderstudios/moonmind@sha256:" + "d" * 64
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(tmp_path), "--image", installed], env={}
        )
        == 0
    )
    monkeypatch.setattr(bootstrap, "_compose", lambda *args: 0)
    monkeypatch.setattr(
        bootstrap, "ensure_target_network", lambda network, project: None
    )

    assert bootstrap.main([command, "--state-dir", str(tmp_path)], env={}) == 0

    rendered = (tmp_path / "controller-compose.yaml").read_text()
    assert f"image: {installed}" in rendered
    assert "/app/deploy/controller/server.py" in rendered
    assert bootstrap.load_controller_image(tmp_path)["pinned"] == installed
