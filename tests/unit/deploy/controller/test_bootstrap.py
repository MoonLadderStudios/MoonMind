"""Host-owned controller lifecycle: install/update/restore, never self-replace."""

import pytest
from conftest import load


def test_installation_inspection_does_not_require_a_host_kernel_lock(
    controller_path, tmp_path, monkeypatch, capsys
):
    import json
    import subprocess
    import sys

    # The read-only preflight also runs under native Windows Python. Linux
    # exclusion belongs to the privileged helper, after path discovery.
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.delitem(sys.modules, "lock", raising=False)
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    state.mkdir()
    (state / "controller-compose.yaml").write_text("# installed project\n")
    config = {
        "name": "controller-installed",
        "services": {
            "controller": {
                "environment": {
                    "MOONMIND_CONTROLLER_PORT": "8519",
                    "MOONMIND_CONTROLLER_TARGET_REPO": "C:\\MoonMind",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/run/desktop/mnt/host/c/MoonMind",
                        "target": "C:\\MoonMind",
                    },
                    {
                        "type": "bind",
                        "source": "/run/desktop/mnt/host/c/state",
                        "target": "/var/lib/moonmind-controller",
                    },
                ],
            }
        },
    }
    monkeypatch.setattr(
        bootstrap,
        "_run_capture",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps(config), ""
        ),
    )
    assert (
        bootstrap.main(["describe-installation", "--state-dir", str(state)], env={})
        == 0
    )
    description = json.loads(capsys.readouterr().out)
    assert description["bindSources"] == {
        "repo": "/run/desktop/mnt/host/c/MoonMind",
        "state": "/run/desktop/mnt/host/c/state",
    }
    assert not (state / "controller-identity.json").exists()


@pytest.mark.parametrize(
    "command,active", [("recover-identity", False), ("ensure", False), ("ensure", True)]
)
def test_missing_identity_is_recovered_from_installed_compose_without_losing_work(
    controller_path, tmp_path, monkeypatch, command, active
):
    import json
    import os
    import subprocess

    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    identity = bootstrap.ensure_identity(
        state,
        tmp_path,
        8519,
        target_network="operator-link",
        target_project="operator-app",
    )
    identity["project"] = "operator-controller"
    compose = bootstrap.render_compose_file(
        state_dir=state,
        repo=tmp_path,
        port=8519,
        project=identity["project"],
        target_network=identity["targetNetwork"],
        target_project=identity["targetProject"],
    )
    original_compose = compose.read_bytes()
    (state / "controller-identity.json").unlink()
    store = load("record").OperationStore(state)
    operation = store.begin(
        stack="moonmind", desired_image="ghcr.io/org/app:next", source_revision="saved"
    )
    if not active:
        for _ in range(bootstrap.record_mod.MAX_AUTO_ATTEMPTS):
            store.record_attempt_error(operation["operationId"], error="original error")
    operation_file = state / "operations" / f"{operation['operationId']}.json"
    original_record = operation_file.read_bytes()
    commands = []
    recreated = []
    config = {
        "name": identity["project"],
        "services": {
            "controller": {
                "environment": {
                    "MOONMIND_CONTROLLER_PORT": "8519",
                    "MOONMIND_CONTROLLER_TARGET_REPO": str(tmp_path),
                    "MOONMIND_CONTROLLER_TARGET_PROJECT": "operator-app",
                },
                "networks": {"moonmind": {"aliases": ["moonmind-controller"]}},
                "ports": [
                    {"host_ip": "127.0.0.1", "published": "8519", "target": 8519}
                ],
                "volumes": [
                    {"type": "bind", "source": str(tmp_path), "target": str(tmp_path)},
                    {
                        "type": "bind",
                        "source": str(state),
                        "target": "/var/lib/moonmind-controller",
                    },
                ],
            }
        },
        "networks": {"moonmind": {"name": "operator-link", "external": True}},
    }

    def capture(args, **kwargs):
        if args[:3] == ["docker", "network", "inspect"]:
            assert args[3] == "operator-link"
            return subprocess.CompletedProcess(args, 0, "[]", "")
        commands.append(args)
        assert args == [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ]
        return subprocess.CompletedProcess(args, 0, json.dumps(config), "")

    monkeypatch.setattr(bootstrap, "_run_capture", capture)
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: set(bootstrap.REQUIRED_CAPABILITIES),
    )

    def image(project, target):
        assert project == "operator-app"
        return "sha256:" + "c" * 64

    monkeypatch.setattr(bootstrap, "_application_controller_image", image)
    monkeypatch.setattr(bootstrap, "_checked_capture", lambda _args: '""')
    # Recovery's real config reader must still execute before image-user inspection.
    original_capture = bootstrap._checked_capture

    def checked(args, **kwargs):
        if args[1] == "compose":
            return capture(args, **kwargs).stdout
        return original_capture(args)

    monkeypatch.setattr(bootstrap, "_checked_capture", checked)
    monkeypatch.setattr(
        bootstrap,
        "_compose",
        lambda *args, **kwargs: (args[2] == "up" and recreated.append(args)) or 0,
    )
    previous_umask = os.umask(0o077)
    try:
        assert (
            bootstrap.main(
                [command, "--state-dir", str(state), "--repo", str(tmp_path)], env={}
            )
            == 0
        )
    finally:
        os.umask(previous_umask)
    recovered = bootstrap.load_identity(state)
    assert {key: value for key, value in recovered.items() if key != "recordedAt"} == {
        key: value for key, value in identity.items() if key != "recordedAt"
    }
    assert (state / "controller-identity.json").stat().st_mode & 0o044 == 0o044
    assert operation_file.read_bytes() == original_record
    assert len(recreated) == (1 if command == "ensure" and not active else 0)
    if command == "recover-identity" or active:
        assert compose.read_bytes() == original_compose
    assert len(commands) == 1


def test_controller_refresh_reuses_a_staged_immutable_image_without_registry_access(
    controller_path, monkeypatch
):
    import subprocess

    bootstrap = load("bootstrap")
    concrete = "sha256:" + "c" * 64

    def run(command):
        assert command[1] != "pull", "The immutable target is already staged"
        output = concrete if command[1:3] == ["image", "inspect"] else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    assert bootstrap._application_controller_image("installed", concrete) == concrete


def test_ensure_refreshes_a_capable_controller_from_the_requested_release(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472)
    compose = bootstrap.render_compose_file(state_dir=state, repo=tmp_path)
    original_mounts = compose.read_text().split("    volumes:", 1)[1]
    concrete = "sha256:" + "b" * 64
    requested = "ghcr.io/org/app@sha256:" + "c" * 64
    selected = []
    recreations = []

    def select(project, image):
        selected.append(image)
        return concrete

    def recreate(state_dir, project, *args, **kwargs):
        assert lock_mod.StackLock(state_dir, "moonmind").probe()
        if args[0] == "up":
            recreations.append(compose.read_text())
        return 0

    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: set(bootstrap.REQUIRED_CAPABILITIES),
    )
    monkeypatch.setattr(bootstrap, "_application_controller_image", select)
    monkeypatch.setattr(bootstrap, "_checked_capture", lambda _args: '""')
    monkeypatch.setattr(bootstrap, "_compose", recreate)

    assert (
        bootstrap.main(
            [
                "ensure",
                "--state-dir",
                str(state),
                "--repo",
                str(tmp_path),
                "--image",
                requested,
            ],
            env={},
        )
        == 0
    )
    assert selected == [requested]
    assert len(recreations) == 1
    assert f"image: {concrete}" in recreations[0]
    assert compose.read_text().split("    volumes:", 1)[1] == original_mounts


def test_ensure_observes_a_capable_controller_with_active_work(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472)
    bootstrap.render_compose_file(state_dir=state, repo=tmp_path)
    store = load("record").OperationStore(state)
    operation = store.begin(
        stack="moonmind", desired_image="ghcr.io/org/app:next", source_revision=""
    )
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: set(bootstrap.REQUIRED_CAPABILITIES),
    )
    monkeypatch.setattr(
        bootstrap, "_run_capture", lambda *args: pytest.fail("Docker touched")
    )

    assert (
        bootstrap.main(
            [
                "ensure",
                "--state-dir",
                str(state),
                "--repo",
                str(tmp_path),
            ],
            env={},
        )
        == 0
    )
    assert store.load(operation["operationId"])["status"] == "pending"


def test_controller_refresh_prefers_requested_release_over_capable_installed_source(
    controller_path, monkeypatch
):
    import json
    import subprocess

    bootstrap = load("bootstrap")
    target = "ghcr.io/org/app@sha256:" + "b" * 64
    concrete = "sha256:" + "c" * 64
    probes = []

    def run(command):
        output = ""
        if command[1] == "ps":
            output = "installed-api"
        elif command[1] == "inspect":
            output = json.dumps(
                [
                    {
                        "Image": "sha256:installed",
                        "Config": {"Labels": {"com.docker.compose.service": "api"}},
                    }
                ]
            )
        elif command[1] == "image":
            output = concrete
        elif command[1] == "run":
            probes.append(command)
        else:
            assert command == ["docker", "pull", target]
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    assert bootstrap._application_controller_image("installed", target) == concrete
    assert len(probes) == 1
    assert concrete in probes[0]


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
    target_image = "sha256:" + "b" * 64
    upgraded = False
    commands = []
    recreations = []

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
            assert "--network=none" in command
            code = 10 if target_image in command else 0
            assert target_image in command or source_image in command
            return subprocess.CompletedProcess(command, code, "", "")
        if command[1] == "pull":
            assert command[2] == "ghcr.io/org/moonmind:old"
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["image", "inspect"]:
            if command[4] == "{{.Id}}":
                return subprocess.CompletedProcess(command, 0, target_image, "")
            assert command[4] == "{{json .Config.User}}"
            return subprocess.CompletedProcess(command, 0, json.dumps(image_user), "")
        raise AssertionError(command)

    def compose_run(state_dir, project, *args, **kwargs):
        nonlocal upgraded
        assert lock_mod.StackLock(state_dir, "moonmind").probe()
        if args[0] == "up":
            assert "--pull" in args and "never" in args
            recreations.append(compose.read_text())
            upgraded = True
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: (set(bootstrap.REQUIRED_CAPABILITIES) if upgraded else set()),
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
    # Reconciliation repeats safely; native Compose compares identical
    # definitions instead of force-recreating the healthy controller.
    assert len(recreations) == 2
    assert recreations[0] == recreations[1]


def test_ensure_prefer_requested_replaces_a_capable_controller_that_failed_the_release(
    controller_path, tmp_path, monkeypatch, capsys
):
    """A controller fix ships in the release the installed controller failed.

    Capability presence cannot prove the installed controller can apply that
    release; the requested release's own controller gets the retry.
    """
    import json
    import subprocess

    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472, target_project="installed")
    compose = bootstrap.render_compose_file(
        state_dir=state, repo=tmp_path, image="sha256:" + "a" * 64
    )
    original_mounts = compose.read_text().split("    volumes:", 1)[1]
    requested = "ghcr.io/org/moonmind:latest"
    concrete = "sha256:" + "c" * 64
    project = bootstrap.load_identity(state)["project"]
    commands = []
    recreations = []
    live = {"image": "sha256:" + "a" * 64, "up_fails": True}

    def run(command):
        commands.append(command)
        if command[:2] == ["docker", "pull"]:
            assert command[2] == requested
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["image", "inspect"]:
            if command[4] == "{{.Id}}":
                assert command[5] == requested
                return subprocess.CompletedProcess(command, 0, concrete + "\n", "")
            return subprocess.CompletedProcess(command, 0, '""', "")
        if command[1] == "run":
            assert concrete in command and "--network=none" in command
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "ps"]:
            # Only the running controller of this deployment's project.
            assert "--all" not in command and "-a" not in command
            assert f"label=com.docker.compose.project={project}" in command
            return subprocess.CompletedProcess(command, 0, "live-controller\n", "")
        if command[:2] == ["docker", "inspect"]:
            assert command[-1] == "live-controller"
            return subprocess.CompletedProcess(command, 0, live["image"] + "\n", "")
        # The installed application images are never consulted.
        raise AssertionError(command)

    def compose_run(state_dir, project, *args, **kwargs):
        assert lock_mod.StackLock(state_dir, "moonmind").probe()
        if args[0] != "up":
            return 0
        recreations.append(args)
        if live.pop("up_fails", False):
            return 1
        live["image"] = concrete
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: set(bootstrap.REQUIRED_CAPABILITIES),
    )
    monkeypatch.setattr(bootstrap, "_compose", compose_run)
    arguments = [
        "ensure",
        "--state-dir",
        str(state),
        "--repo",
        str(tmp_path),
        "--image",
        requested,
        "--controller-url",
        "http://controller:8472",
        "--prefer-requested",
    ]

    def receipt():
        lines = capsys.readouterr().out.splitlines()
        prefix = bootstrap.REFRESH_RECEIPT_PREFIX
        found = [line for line in lines if line.startswith(prefix)]
        return json.loads(found[-1][len(prefix) :]) if found else None

    # An interrupted recreation leaves the Compose file naming the requested
    # image while the old controller still runs; it is not a refresh.
    with pytest.raises(RuntimeError, match="recreation failed"):
        bootstrap.main(arguments, env={})
    assert f"image: {concrete}" in compose.read_text()
    assert receipt() is None

    # The next attempt reconciles against the live controller, not the file.
    assert bootstrap.main(arguments, env={}) == 0
    assert len(recreations) == 2
    text = compose.read_text()
    assert f"image: {concrete}" in text
    assert text.split("    volumes:", 1)[1] == original_mounts
    assert bootstrap.load_controller_image(state)["pinned"] == concrete
    assert receipt() == {"refreshed": True, "image": concrete}

    # The controller already runs the requested release: nothing to retry with.
    assert bootstrap.main(arguments, env={}) == 0
    assert len(recreations) == 2
    assert receipt() == {"refreshed": False, "image": concrete}

    load("record").OperationStore(state).begin(
        stack="moonmind", desired_image=requested, source_revision=""
    )
    commands.clear()
    with pytest.raises(bootstrap.ActiveOperationError):
        bootstrap.main(arguments, env={})
    assert commands == []


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
    assert len(probes) == 1
    assert concrete in probes[0]


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

    def recreate(state_dir, project, *args, **kwargs):
        nonlocal upgraded
        if args[0] != "up":
            return 0
        attempts.append(compose.read_text())
        if len(attempts) == 1:
            return 1
        upgraded = True
        return 0

    def capabilities(*args):
        if attempts and not upgraded:
            raise ConnectionRefusedError(
                "old controller stopped before recreation failed"
            )
        return set(bootstrap.REQUIRED_CAPABILITIES) if upgraded else set()

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


def test_bootstrap_install_writes_compose_project_and_secret(controller_path, tmp_path):
    bootstrap = load("bootstrap")
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    rendered = (tmp_path / "controller-compose.yaml").read_text()
    assert "moonmind-controller" in rendered
    assert "/var/run/docker.sock" in rendered
    assert "unless-stopped" in rendered
    secret_file = tmp_path / "secrets" / "controller-bearer"
    assert secret_file.exists()
    assert len(secret_file.read_text().strip()) >= 32


def test_bootstrap_update_serializes_against_active_mutation(controller_path, tmp_path):
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

    def fake_compose(state_dir, *args, **kwargs):
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
        repo=Path("C:/moonmind"),
    )
    content = rendered.read_text()
    assert "/run/desktop/mnt/host/c/moonmind" in content


def _legacy_desktop_binds(compose, mapping):
    """Spell bind sources the way the previous adapter rendered WSL paths."""
    text = compose.read_text()
    for local, desktop in mapping.items():
        text = text.replace(f"      - {local}:", f"      - {desktop}:")
    compose.write_text(text)


def test_ensure_replaces_a_stopped_controller_on_wsl_spelled_binds(
    controller_path, tmp_path, monkeypatch
):
    """A stopped controller is replaced, never restarted on stale state.

    Its recorded link-network ID can be gone after the target stack was
    redeployed, and binds rendered in the Desktop daemon namespace resolve
    to empty directories for a WSL distro's Docker client.
    """
    from types import SimpleNamespace

    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap.mounts_mod, "WSL_DRIVE_ROOT", tmp_path / "mnt")
    repo = tmp_path / "mnt" / "d" / "code" / "MoonMind"
    state = repo / "deploy" / "state" / "controller"
    state.mkdir(parents=True)
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(
        state, repo, 8472, target_network="moonmind_link", target_project="moonmind"
    )
    compose = bootstrap.render_compose_file(
        state_dir=state,
        repo=repo,
        target_network="moonmind_link",
        target_project="moonmind",
    )
    local_mounts = compose.read_text().split("    volumes:", 1)[1]
    _legacy_desktop_binds(
        compose,
        {
            state: "/run/desktop/mnt/host/d/code/MoonMind/deploy/state/controller",
            repo: "/run/desktop/mnt/host/d/code/MoonMind",
        },
    )
    calls = []
    upgraded = False

    def fake_run(args):
        calls.append(("run", tuple(args[:3])))
        if args[:3] == ["docker", "network", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        if args[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout='""', stderr="")
        return SimpleNamespace(returncode=0, stdout="id", stderr="")

    def fake_compose(state_dir, project, *args, **kwargs):
        nonlocal upgraded
        calls.append(("compose", args, compose.read_text()))
        upgraded = upgraded or args[0] == "up"
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    monkeypatch.setattr(
        bootstrap, "_application_controller_image", lambda *args: "sha256:" + "e" * 64
    )
    monkeypatch.setattr(
        bootstrap,
        "controller_capabilities",
        lambda *args: (set(bootstrap.REQUIRED_CAPABILITIES) if upgraded else set()),
    )
    assert (
        bootstrap.main(
            ["ensure", "--state-dir", str(state), "--repo", str(repo)], env={}
        )
        == 0
    )
    composes = [call for call in calls if call[0] == "compose"]
    assert [call[1] for call in composes] == [
        ("rm", "--force", "controller"),
        ("up", "-d", "--wait", "--wait-timeout", "30", "--pull", "never", "controller"),
    ]
    # The link network exists before Compose resolves it by name.
    kinds = [call[:2] for call in calls]
    assert kinds.index(("run", ("docker", "network", "create"))) < kinds.index(
        composes[0][:2]
    )
    # The recreated controller binds the directories this client sees.
    assert composes[1][2].split("    volumes:", 1)[1] == local_mounts


def test_controller_startup_deadline_returns_failure_to_the_lifecycle_owner(
    controller_path, tmp_path, monkeypatch
):
    """A restarting controller cannot hold startup indefinitely and prevent rollback."""
    import subprocess

    bootstrap = load("bootstrap")

    def restarting_controller(command, *, deadline):
        assert 0 < deadline - bootstrap.time.monotonic() <= 125
        if "up" in command:
            assert "--wait-timeout" in command
            timeout = int(command[command.index("--wait-timeout") + 1])
            assert 0 < timeout <= 30
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap, "_run_owned", restarting_controller)

    assert bootstrap._compose_up(tmp_path, "controller", "--pull", "never") == 1


@pytest.mark.parametrize("command", ["start", "update", "restore"])
def test_lifecycle_replaces_a_stopped_controller_before_up(
    controller_path, tmp_path, monkeypatch, command
):
    bootstrap = load("bootstrap")
    installed = "ghcr.io/moonladderstudios/moonmind@sha256:" + "d" * 64
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(tmp_path), "--image", installed], env={}
        )
        == 0
    )
    calls = []

    def fake_compose(state_dir, project, *args, **kwargs):
        calls.append(args)
        return 0

    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    monkeypatch.setattr(
        bootstrap, "ensure_target_network", lambda network, project: None
    )
    assert bootstrap.main([command, "--state-dir", str(tmp_path)], env={}) == 0
    ups = [index for index, args in enumerate(calls) if args[0] == "up"]
    assert len(ups) == 1
    assert calls[ups[0] - 1] == ("rm", "--force", "controller")
    assert calls[ups[0]][-1] == "controller"


def test_bootstrap_derives_stable_per_deployment_identity(controller_path, tmp_path):
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
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
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

    def fake_compose(state_dir, project, *args, **kwargs):
        calls.append(("compose", args))
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", fake_run)
    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    monkeypatch.setattr(
        bootstrap, "require_verified_image", lambda state_dir, requested: "img@sha256:x"
    )
    assert (
        bootstrap.main(
            ["install", "--state-dir", str(state), "--repo", str(repo)], env={}
        )
        == 0
    )
    calls.clear()
    assert (
        bootstrap.main(
            ["start", "--state-dir", str(state), "--repo", str(repo)], env={}
        )
        == 0
    )
    create = [
        args
        for kind, args in calls
        if kind == "run" and args[:3] == ("docker", "network", "create")
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
        spec = re.sub(
            r"\$\{[A-Z0-9_]+(?::-([^}]*))?\}", lambda m: m.group(1) or "", raw
        )
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
            target
            for kind, _, target, read_only in mounts
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
    assert (
        '    entrypoint: ["python", "/app/deploy/controller/server.py"]\n' in rendered
    )
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

    def compose_run(*args, **kwargs):
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
        lambda *args: (set(bootstrap.REQUIRED_CAPABILITIES) if upgraded else set()),
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


def _handoff_installation(bootstrap, record, tmp_path, *, status="staged"):
    state = tmp_path / "state"
    bootstrap.ensure_secret(state)
    bootstrap.ensure_identity(state, tmp_path, 8472, target_project="installed")
    compose = bootstrap.render_compose_file(
        state_dir=state, repo=tmp_path, image="sha256:" + "a" * 64
    )
    bootstrap.record_controller_image(
        state, requested="ghcr.io/org/moonmind:old", pinned="sha256:" + "a" * 64
    )
    store = record.OperationStore(state)
    operation = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/moonmind:latest",
        source_revision="",
        operation_id="ui-handoff",
    )
    store.mark_stage(operation["operationId"], stage=status)
    return state, compose, store


class _HandoffDocker:
    """The Docker CLI as the requested image's one-off sees it."""

    def __init__(self, *, settles=True):
        self.live = "sha256:" + "a" * 64
        self.restarts = "0" if settles else "1"
        self.commands = []

    def run(self, command, **kwargs):
        import subprocess

        self.commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            assert command[-1] == "ghcr.io/org/moonmind:latest"
            return subprocess.CompletedProcess(command, 0, "sha256:" + "c" * 64 + "\n", "")
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, "live-controller\n", "")
        if command[:2] == ["docker", "inspect"]:
            assert command[-1] == "live-controller"
            if "--format" not in command:
                import json

                project_filter = next(
                    arg
                    for cmd in reversed(self.commands)
                    if cmd[:2] == ["docker", "ps"]
                    for arg in cmd
                    if arg.startswith("label=com.docker.compose.project=")
                )
                project = project_filter.split("=", 2)[2]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        [
                            {
                                "Config": {
                                    "Labels": {
                                        "com.docker.compose.project": project,
                                        "com.docker.compose.service": "controller",
                                    }
                                },
                                "State": {"Status": "running"},
                                "Image": self.live,
                            }
                        ]
                    ),
                    "",
                )
            if command[3] == "{{.Image}}":
                return subprocess.CompletedProcess(command, 0, self.live + "\n", "")
            return subprocess.CompletedProcess(
                command, 0, f"{self.live} {self.restarts}\n", ""
            )
        raise AssertionError(command)


def _handoff(bootstrap, state, capsys):
    import json

    assert bootstrap.main(
        [
            "handoff",
            "--state-dir",
            str(state),
            "--operation",
            "ui-handoff",
            "--image",
            "ghcr.io/org/moonmind:latest",
        ],
        env={},
    ) == 0
    prefix = bootstrap.HANDOFF_RECEIPT_PREFIX
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith(prefix)]
    return json.loads(lines[-1][len(prefix) :])


def test_handoff_lets_the_requested_release_controller_resume_the_operation(
    controller_path, tmp_path, monkeypatch, capsys
):
    """A release brings the controller it needs without a host step."""
    bootstrap = load("bootstrap")
    record = load("record")
    lock = load("lock")
    state, compose, store = _handoff_installation(bootstrap, record, tmp_path)
    original_mounts = compose.read_text().split("    volumes:", 1)[1]
    docker = _HandoffDocker()
    recreations = []

    def compose_run(state_dir, project, *args, **kwargs):
        if args[0] == "up":
            recreations.append(args)
            docker.live = "sha256:" + "c" * 64
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", docker.run)
    monkeypatch.setattr(bootstrap, "_compose", compose_run)
    monkeypatch.setattr(bootstrap, "HANDOFF_SETTLE_SECONDS", 0)

    # The requesting controller holds the stack lock while it waits.
    with lock.StackLock(state, "moonmind").acquire():
        receipt = _handoff(bootstrap, state, capsys)

    concrete = "sha256:" + "c" * 64
    assert receipt == {"status": "handed-off", "image": concrete}
    assert len(recreations) == 1 and "--pull" in recreations[0]
    text = compose.read_text()
    assert f"image: {concrete}" in text
    assert text.split("    volumes:", 1)[1] == original_mounts
    assert bootstrap.load_controller_image(state)["pinned"] == concrete
    operation = store.load("ui-handoff")
    assert operation["status"] == "staged"
    assert operation["controllerHandoff"]["image"] == concrete
    assert operation["controllerHandoff"]["from"] == "sha256:" + "a" * 64

    # The resumed controller already runs the requested release.
    assert _handoff(bootstrap, state, capsys) == {"status": "current", "image": concrete}
    assert len(recreations) == 1


@pytest.mark.parametrize("blocker", ["other-work", "already-handed-off", "closed"])
def test_handoff_never_loops_or_interrupts_other_work(
    controller_path, tmp_path, monkeypatch, capsys, blocker
):
    bootstrap = load("bootstrap")
    record = load("record")
    state, compose, store = _handoff_installation(
        bootstrap, record, tmp_path, status="failed" if blocker == "closed" else "staged"
    )
    if blocker == "other-work":
        store.begin(stack="moonmind", desired_image="ghcr.io/org/moonmind:next",
                    source_revision="", operation_id="other")
    if blocker == "already-handed-off":
        store.record_controller_handoff(
            "ui-handoff", {"image": "sha256:" + "c" * 64, "attemptGroup": 1}
        )
    before = compose.read_text()
    monkeypatch.setattr(bootstrap, "_run_capture", _HandoffDocker().run)
    monkeypatch.setattr(
        bootstrap, "_compose", lambda *args: pytest.fail("controller recreated")
    )

    receipt = _handoff(bootstrap, state, capsys)

    assert receipt["status"] == "unavailable"
    assert compose.read_text() == before


@pytest.mark.parametrize("failure", ["recreation", "restarting"])
def test_failed_handoff_restores_the_previous_controller(
    controller_path, tmp_path, monkeypatch, capsys, failure
):
    """The previous controller keeps the operation when the new one cannot."""
    bootstrap = load("bootstrap")
    record = load("record")
    state, compose, store = _handoff_installation(bootstrap, record, tmp_path)
    before = compose.read_text()
    recorded = bootstrap.load_controller_image(state)
    docker = _HandoffDocker(settles=failure != "restarting")
    ups = []

    def compose_run(state_dir, project, *args, **kwargs):
        if args[0] != "up":
            return 0
        ups.append(compose.read_text())
        if len(ups) == 1:
            if failure == "recreation":
                return 1
            docker.live = "sha256:" + "c" * 64
        else:
            docker.live = "sha256:" + "a" * 64
            docker.restarts = "0"
        return 0

    monkeypatch.setattr(bootstrap, "_run_capture", docker.run)
    monkeypatch.setattr(bootstrap, "_compose", compose_run)
    monkeypatch.setattr(bootstrap, "HANDOFF_SETTLE_SECONDS", 0)

    receipt = _handoff(bootstrap, state, capsys)

    assert receipt["status"] == "unavailable"
    assert "previous controller restored" in receipt["reason"]
    assert len(ups) == 2 and ups[1] == before
    assert compose.read_text() == before
    restored = bootstrap.load_controller_image(state)
    assert (restored["requested"], restored["pinned"]) == (
        recorded["requested"], recorded["pinned"]
    )
    # The marker stops the restored controller from handing off again.
    assert store.load("ui-handoff")["controllerHandoff"]["attemptGroup"] == 1


def test_bootstrap_update_rechecks_open_operations_inside_the_stack_lock(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    record = load("record")
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    acquire = lock_mod.StackLock.acquire

    def submission_recorded_first(self):
        # A submission records intent between the first check and exclusion.
        record.OperationStore(tmp_path).begin(
            stack="moonmind", desired_image="img", source_revision=""
        )
        return acquire(self)

    monkeypatch.setattr(lock_mod.StackLock, "acquire", submission_recorded_first)
    monkeypatch.setattr(
        bootstrap, "_compose", lambda *args: pytest.fail("replaced a live owner")
    )
    monkeypatch.setattr(
        bootstrap, "ensure_target_network", lambda network, project: None
    )
    with pytest.raises(bootstrap.ActiveOperationError):
        bootstrap.main(["update", "--state-dir", str(tmp_path)], env={})


def test_bootstrap_restore_never_replaces_a_controller_that_owns_the_stack(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    lock_mod = load("lock")
    assert bootstrap.main(["install", "--state-dir", str(tmp_path)], env={}) == 0
    held_during_compose = []

    def fake_compose(state_dir, *args, **kwargs):
        held_during_compose.append(
            lock_mod.StackLock(state_dir, "moonmind").probe()
        )
        return 0

    monkeypatch.setattr(bootstrap, "_compose", fake_compose)
    monkeypatch.setattr(
        bootstrap, "ensure_target_network", lambda network, project: None
    )
    with lock_mod.StackLock(tmp_path, "moonmind").acquire(), pytest.raises(lock_mod.LockBusyError):
        bootstrap.main(["restore", "--state-dir", str(tmp_path)], env={})
    assert held_during_compose == []
    assert bootstrap.main(["restore", "--state-dir", str(tmp_path)], env={}) == 0
    assert held_during_compose and all(held_during_compose)


@pytest.mark.parametrize("restore_timeout", [False, True])
def test_handoff_startup_timeout_restores_saved_controller_and_keeps_failure(
    controller_path, tmp_path, monkeypatch, capsys, restore_timeout
):
    import subprocess

    bootstrap = load("bootstrap")
    record = load("record")
    state, compose, store = _handoff_installation(bootstrap, record, tmp_path)
    before = compose.read_text()
    recorded = bootstrap.load_controller_image(state)
    docker = _HandoffDocker()
    ups = []

    def run(command, **kwargs):
        if command[:2] != ["docker", "compose"]:
            return docker.run(command, **kwargs)
        assert 0 < kwargs["deadline"] - bootstrap.time.monotonic() <= 270
        if "up" in command:
            assert command[command.index("--wait-timeout") + 1] == "30"
            ups.append(compose.read_text())
            if len(ups) == 1 or restore_timeout:
                raise subprocess.TimeoutExpired(command, 120)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap, "_run_owned", run)
    monkeypatch.setattr(bootstrap, "HANDOFF_SETTLE_SECONDS", 0)
    receipt = _handoff(bootstrap, state, capsys)

    assert receipt["status"] == "unavailable"
    assert "timed out" in receipt["reason"]
    assert ("restore failed" if restore_timeout else "previous controller restored") in receipt["reason"]
    assert len(ups) == 2 and ups[1] == before
    assert compose.read_text() == before
    restored = bootstrap.load_controller_image(state)
    assert (restored["requested"], restored["pinned"]) == (recorded["requested"], recorded["pinned"])
    assert store.load("ui-handoff")["controllerHandoff"]["attemptGroup"] == 1


def test_controller_startup_cancellation_is_not_a_failed_handoff(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")

    def interrupted(command, **kwargs):
        assert 0 < kwargs["deadline"] - bootstrap.time.monotonic() <= 125
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap, "_run_owned", interrupted)
    with pytest.raises(KeyboardInterrupt):
        bootstrap._compose_up(tmp_path, "moonmind-test-cancel", "--pull", "never")


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_cold_controller_start_does_not_apply_handoff_budget_to_image_download(
    controller_path, tmp_path, monkeypatch, platform
):
    import subprocess

    bootstrap = load("bootstrap")
    calls = []
    monkeypatch.setattr(bootstrap.sys, "platform", platform)
    monkeypatch.setattr(
        bootstrap, "_run_owned", lambda *a, **k: pytest.fail("cold startup was bounded")
    )

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] is None
        if "up" in command:
            assert command[command.index("--wait-timeout") + 1] == "30"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    assert bootstrap._compose_up(tmp_path, "moonmind-test-cold") == 0
    assert any("up" in command for command in calls)


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux lifecycle helper")
@pytest.mark.parametrize("command", [("rm", "--force"), ("up", "--pull", "never")])
@pytest.mark.parametrize("reconcile_fails", [False, True])
def test_bounded_compose_timeout_cleans_plugin_and_reconciles(
    controller_path, tmp_path, monkeypatch, command, reconcile_fails
):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    bootstrap = load("bootstrap")
    marker = tmp_path / "plugin-pid"
    script = """
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, '-c',
    'import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)', sys.argv[1]])
time.sleep(60)
"""
    popen = subprocess.Popen

    def launch(args, **kwargs):
        if command[0] == "up" and "rm" in args:
            return popen([sys.executable, "-c", "pass"], **kwargs)
        return popen([sys.executable, "-c", script, str(marker)], **kwargs)

    def plugin_running():
        try:
            fields = Path(f"/proc/{int(marker.read_text())}/stat").read_text()
        except FileNotFoundError:
            return False
        return fields.rsplit(")", 1)[1].split()[0] != "Z"

    reconciled = []

    def reconcile(project, **kwargs):
        assert project == "moonmind-test-owned"
        assert not plugin_running()
        assert kwargs["deadline"] > time.monotonic()
        reconciled.append(project)
        if reconcile_fails:
            raise RuntimeError("controller state is uncertain")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", launch)
    monkeypatch.setattr(bootstrap, "_reconcile_controller", reconcile)
    monkeypatch.setattr(bootstrap, "CONTROLLER_START_COMMAND_TIMEOUT_SECONDS", 0.3)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="timed out") as caught:
            bootstrap._compose_up(tmp_path, "moonmind-test-owned", "--pull", "never")
        assert marker.exists()
        assert not plugin_running()
        assert reconciled == ["moonmind-test-owned"]
        if reconcile_fails:
            assert "controller state is uncertain" in str(caught.value)
        assert time.monotonic() - started < 7
    finally:
        if marker.exists() and plugin_running():
            os.kill(int(marker.read_text()), signal.SIGKILL)


def test_bounded_compose_does_not_reconcile_until_cleanup_is_verified(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")

    def unverified(*args, **kwargs):
        raise bootstrap.CommandCleanupError("plugin may still be running")

    monkeypatch.setattr(bootstrap, "_run_owned", unverified)
    monkeypatch.setattr(
        bootstrap,
        "_reconcile_controller",
        lambda *args, **kwargs: pytest.fail("inspected before plugin cleanup"),
    )
    with pytest.raises(bootstrap.CommandCleanupError, match="plugin may still"):
        bootstrap._compose_up(tmp_path, "moonmind-test-uncertain", "--pull", "never")


def test_handoff_preflight_timeout_is_a_bounded_receipt(
    controller_path, tmp_path, monkeypatch, capsys
):
    import subprocess

    bootstrap = load("bootstrap")
    state, compose, _ = _handoff_installation(bootstrap, load("record"), tmp_path)
    before = compose.read_text()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(bootstrap, "_run_capture", timeout)
    receipt = _handoff(bootstrap, state, capsys)
    assert receipt["status"] == "unavailable"
    assert compose.read_text() == before


def test_owned_command_uses_remaining_budget(controller_path, monkeypatch):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: 20)
    with pytest.raises(RuntimeError, match="budget"):
        bootstrap._run_owned(["must-not-start"], deadline=20)


@pytest.mark.parametrize(
    "failure", ["rm", "up", "settle", "inspect", "reconcile", "exhausted"]
)
def test_handoff_shares_deadline_and_reserves_bounded_recovery(
    controller_path, tmp_path, monkeypatch, capsys, failure
):
    import subprocess

    bootstrap = load("bootstrap")
    state, compose, _ = _handoff_installation(bootstrap, load("record"), tmp_path)
    original = compose.read_text()
    docker = _HandoffDocker()
    clock = [1000.0]
    calls = []
    failed = [False]
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        bootstrap.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )

    def run(command, *, deadline, **kwargs):
        # All subprocesses, including preflight and post-start probes, share
        # either the forward phase cutoff or the final recovery cutoff.
        assert deadline in {1180, 1270}
        assert deadline > clock[0]
        calls.append((command, deadline, clock[0]))
        recovering = deadline == 1270
        if command[:2] == ["docker", "compose"]:
            verb = "up" if "up" in command else "rm"
            if recovering:
                assert any(
                    c[:2] == ["docker", "inspect"] and "--format" not in c
                    for c, d, _ in calls
                    if d == 1270
                )
                docker.live = "sha256:" + "a" * 64
                docker.restarts = "0"
            elif verb == "up":
                docker.live = "sha256:" + "c" * 64
            if not recovering and (
                failure == verb or failure == "exhausted" and verb == "up"
            ):
                failed[0] = True
                clock[0] = deadline - 1
                raise subprocess.TimeoutExpired(command, 1)
            clock[0] += 20
            return subprocess.CompletedProcess(command, 0)
        if (
            not recovering
            and command[:2] == ["docker", "inspect"]
            and command[3] == "{{.Image}} {{.RestartCount}}"
        ):
            if failure in {"inspect", "reconcile", "exhausted"}:
                failed[0] = True
                clock[0] = deadline - 1
                raise subprocess.TimeoutExpired(command, 1)
            if failure == "settle":
                failed[0] = True
                docker.restarts = "1"
        if recovering and failure == "reconcile":
            raise subprocess.TimeoutExpired(command, 1)
        if recovering and failure == "exhausted":
            clock[0] = deadline
            raise RuntimeError("controller handoff budget exhausted")
        clock[0] += 1
        return docker.run(command)

    monkeypatch.setattr(bootstrap, "_run_owned", run)
    receipt = _handoff(bootstrap, state, capsys)
    assert failed[0]
    assert receipt["status"] == "unavailable"
    assert clock[0] <= 1270
    assert compose.read_text() == original
    if failure in {"reconcile", "exhausted"}:
        assert "restore failed" in receipt["reason"]
        assert not any(
            c[:2] == ["docker", "compose"] and d == 1270 for c, d, _ in calls
        )
    else:
        assert "previous controller restored" in receipt["reason"]


@pytest.mark.parametrize(
    "uncertainty",
    ["duplicate", "removing", "wrong-project", "inspection-error", "wrong-image"],
)
def test_handoff_does_not_mutate_after_uncertain_reconciliation(
    controller_path, monkeypatch, uncertainty
):
    import json
    import subprocess

    bootstrap = load("bootstrap")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "ps":
            return subprocess.CompletedProcess(
                command, 0, "one two" if uncertainty == "duplicate" else "one", ""
            )
        if uncertainty == "inspection-error":
            return subprocess.CompletedProcess(command, 1, "", "unavailable")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": (
                                    "other"
                                    if uncertainty == "wrong-project"
                                    else "exact"
                                ),
                                "com.docker.compose.service": "controller",
                            }
                        },
                        "State": {
                            "Status": (
                                "removing" if uncertainty == "removing" else "running"
                            )
                        },
                        "Image": "unknown" if uncertainty == "wrong-image" else "known",
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    with pytest.raises(RuntimeError):
        bootstrap._reconcile_controller(
            "exact", images={"known"}, deadline=bootstrap.time.monotonic() + 20
        )
    assert all(
        "label=com.docker.compose.project=exact" in c for c in calls if c[1] == "ps"
    )


@pytest.mark.skipif(
    __import__("sys").platform != "linux", reason="Linux handoff helper"
)
@pytest.mark.parametrize("mode", ["timeout", "interrupt", "sigterm", "success"])
def test_owned_command_stops_compose_plugin_before_recovery(
    controller_path, tmp_path, monkeypatch, mode
):
    import os
    import signal
    import subprocess
    import sys
    import time

    bootstrap = load("bootstrap")
    marker = tmp_path / "plugin-pid"
    # The CLI and its plugin ignore TERM. The plugin stays alive after the
    # launcher dies, reproducing subprocess.run's former single-PID cleanup.
    script = """
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c',
    'import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
    'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)', sys.argv[1]])
if sys.argv[2] == "success":
    while not os.path.exists(sys.argv[1]):
        time.sleep(0.01)
else:
    time.sleep(60)
"""
    if mode in {"interrupt", "sigterm"}:

        def interrupt(process, **kwargs):
            until = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(0.01)
            if mode == "sigterm":
                os.kill(os.getpid(), signal.SIGTERM)
            raise KeyboardInterrupt

        monkeypatch.setattr(subprocess.Popen, "communicate", interrupt)
    monkeypatch.setattr(bootstrap, "CONTROLLER_START_COMMAND_TIMEOUT_SECONDS", 0.3)
    started = time.monotonic()
    previous_handler = signal.signal(signal.SIGTERM, bootstrap._cancel_handoff)
    try:
        command = [sys.executable, "-c", script, str(marker), mode]
        if mode == "success":
            assert bootstrap._run_owned(command, deadline=started + 7).returncode == 0
        else:
            error = {
                "timeout": RuntimeError,
                "interrupt": KeyboardInterrupt,
                "sigterm": SystemExit,
            }[mode]
            with pytest.raises(error):
                bootstrap._run_owned(command, deadline=started + 7)
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    assert time.monotonic() - started < 7
    assert marker.exists()
    pid = int(marker.read_text())
    try:
        stat = __import__("pathlib").Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        pass
    else:
        assert stat.rsplit(")", 1)[1].split()[0] == "Z"
    # Only after the helper returns could the caller begin reconciliation.
    # No live plugin remains to race that inspection/recovery.


def test_owned_commands_spend_one_cumulative_allowance(controller_path, monkeypatch):
    import subprocess

    bootstrap = load("bootstrap")
    clock = [0]
    timeouts = []
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bootstrap, "_group_running", lambda pid: False)

    class Process:
        pid = 123
        returncode = 0

        def __init__(self, command, **kwargs):
            assert kwargs["start_new_session"] is True

        def communicate(self, *, timeout):
            timeouts.append(timeout)
            clock[0] += timeout
            if len(timeouts) > 1:
                raise subprocess.TimeoutExpired("compose up", timeout)
            return "", ""

        def poll(self):
            return 0

        def wait(self, *, timeout):
            return 0

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Process)
    monkeypatch.setattr(bootstrap.os, "killpg", lambda *args: None)
    bootstrap._run_owned(["compose", "rm"], deadline=180)
    with pytest.raises(RuntimeError, match="timed out"):
        bootstrap._run_owned(["compose", "up"], deadline=180)
    assert timeouts == [120, 55]
    assert clock[0] == 175
    with pytest.raises(RuntimeError, match="budget"):
        bootstrap._run_owned(["must-not-start"], deadline=180)


def test_handoff_skips_recovery_when_plugin_cleanup_is_unverified(
    controller_path, tmp_path, monkeypatch, capsys
):
    bootstrap = load("bootstrap")
    state, _, _ = _handoff_installation(bootstrap, load("record"), tmp_path)
    monkeypatch.setattr(bootstrap, "_run_capture", _HandoffDocker().run)
    mutations = []

    def compose(*args, **kwargs):
        mutations.append(args)
        raise bootstrap.CommandCleanupError("plugin may still be running")

    monkeypatch.setattr(bootstrap, "_compose", compose)
    receipt = _handoff(bootstrap, state, capsys)
    assert len(mutations) == 1
    assert "recovery skipped" in receipt["reason"]
    assert "plugin may still be running" in receipt["reason"]


def test_owned_command_preserves_timeout_when_cleanup_also_fails(
    controller_path, monkeypatch
):
    import subprocess

    bootstrap = load("bootstrap")

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *, timeout):
            raise subprocess.TimeoutExpired("docker compose up", timeout)

    def cleanup(*args, **kwargs):
        raise bootstrap.CommandCleanupError("plugin cleanup unverified")

    monkeypatch.setattr(bootstrap.subprocess, "Popen", Process)
    monkeypatch.setattr(bootstrap, "_stop_command", cleanup)
    with pytest.raises(bootstrap.CommandCleanupError) as caught:
        bootstrap._run_owned(
            ["docker", "compose", "up"], deadline=bootstrap.time.monotonic() + 10
        )
    assert "timed out" in str(caught.value)
    assert "plugin cleanup unverified" in str(caught.value)


def test_handoff_reconciles_known_restarting_candidate(controller_path, monkeypatch):
    import json
    import subprocess

    bootstrap = load("bootstrap")

    def run(command, **kwargs):
        if command[1] == "ps":
            return subprocess.CompletedProcess(command, 0, "candidate", "")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "exact",
                                "com.docker.compose.service": "controller",
                            }
                        },
                        "State": {"Status": "restarting", "Restarting": True},
                        "Image": "requested",
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(bootstrap, "_run_capture", run)
    bootstrap._reconcile_controller(
        "exact", images={"requested", "previous"}, deadline=10
    )


def test_handoff_sigterm_is_cancellation_not_recovery(
    controller_path, tmp_path, monkeypatch
):
    import os
    import signal

    bootstrap = load("bootstrap")
    state, _, _ = _handoff_installation(bootstrap, load("record"), tmp_path)
    previous = signal.getsignal(signal.SIGTERM)
    calls = []

    def cancelled(*args, **kwargs):
        calls.append(args)
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(bootstrap, "_run_capture", _HandoffDocker().run)
    monkeypatch.setattr(bootstrap, "_compose", cancelled)
    with pytest.raises(SystemExit) as caught:
        bootstrap.main(
            [
                "handoff",
                "--state-dir",
                str(state),
                "--operation",
                "ui-handoff",
                "--image",
                "ghcr.io/org/moonmind:latest",
            ],
            env={},
        )
    assert caught.value.code == 128 + signal.SIGTERM
    assert len(calls) == 1
    assert signal.getsignal(signal.SIGTERM) == previous


def test_handoff_keeps_original_error_when_saved_config_restore_fails(
    controller_path, tmp_path, monkeypatch, capsys
):
    from pathlib import Path

    bootstrap = load("bootstrap")
    state, compose, _ = _handoff_installation(bootstrap, load("record"), tmp_path)
    monkeypatch.setattr(bootstrap, "_run_capture", _HandoffDocker().run)
    writes = Path.write_text
    mutations = []

    def restore_error(path, *args, **kwargs):
        if path == compose:
            raise OSError("saved compose restore denied")
        return writes(path, *args, **kwargs)

    def failed_start(*args, **kwargs):
        mutations.append(args)
        raise RuntimeError("original startup timeout")

    monkeypatch.setattr(Path, "write_text", restore_error)
    monkeypatch.setattr(bootstrap, "_compose", failed_start)
    receipt = _handoff(bootstrap, state, capsys)
    assert len(mutations) == 1
    assert "original startup timeout" in receipt["reason"]
    assert "saved compose restore denied" in receipt["reason"]


def test_no_deadline_compose_startup_uses_owned_cleanup(
    controller_path, tmp_path, monkeypatch
):
    import subprocess

    bootstrap = load("bootstrap")
    calls = []

    def owned(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bootstrap, "_run_owned", owned)
    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda *a, **k: pytest.fail("unowned bounded command"),
    )
    assert bootstrap._compose_up(tmp_path, "exact-controller", "--pull", "never") == 0
    assert len(calls) == 2
    assert all(call[1]["deadline"] > bootstrap.time.monotonic() for call in calls)


@pytest.mark.skipif(
    __import__("sys").platform != "linux", reason="real process-group regression"
)
@pytest.mark.parametrize(
    "verb,mode,state",
    [
        ("rm", "timeout", "running"),
        ("up", "timeout", "running"),
        ("up", "timeout", "removing"),
        ("up", "timeout", "inspection-error"),
        ("up", "interrupt", "running"),
        ("up", "sigterm", "running"),
    ],
)
def test_ensure_stops_plugin_and_reconciles_before_timeout_returns(
    controller_path, tmp_path, monkeypatch, verb, mode, state
):
    import json
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    bootstrap = load("bootstrap")
    lock = load("lock")
    state_dir = tmp_path / "state"
    bootstrap.ensure_secret(state_dir)
    identity = bootstrap.ensure_identity(state_dir, tmp_path, 8472)
    bootstrap.render_compose_file(
        state_dir=state_dir, repo=tmp_path, image="sha256:" + "a" * 64
    )
    monkeypatch.setattr(bootstrap, "controller_capabilities", lambda *args: set())
    monkeypatch.setattr(
        bootstrap, "_application_controller_image", lambda *args: "sha256:" + "c" * 64
    )
    monkeypatch.setattr(bootstrap, "CONTROLLER_START_COMMAND_TIMEOUT_SECONDS", 0.3)
    marker = tmp_path / "plugin"
    mutations = []
    inspections = []
    real_popen = subprocess.Popen
    real_communicate = subprocess.Popen.communicate
    script = """
import signal,subprocess,sys,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, '-c',
 'import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
 'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)', sys.argv[1]])
time.sleep(60)
"""

    def plugin_stopped():
        assert marker.exists()
        try:
            value = Path(f"/proc/{int(marker.read_text())}/stat").read_text()
        except FileNotFoundError:
            return
        assert value.rsplit(")", 1)[1].split()[0] == "Z"

    def popen(command, **kwargs):
        assert command[:2] == ["docker", "compose"]
        assert command[command.index("--project-name") + 1] == identity["project"]
        assert kwargs["start_new_session"] is True
        current = "up" if "up" in command else "rm"
        mutations.append(current)
        process = real_popen(
            (
                [sys.executable, "-c", script, str(marker)]
                if current == verb
                else [sys.executable, "-c", "pass"]
            ),
            **kwargs,
        )
        process.test_blocked = current == verb
        return process

    def communicate(process, **kwargs):
        if process.test_blocked and mode != "timeout":
            until = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(0.01)
            if mode == "sigterm":
                os.kill(os.getpid(), signal.SIGTERM)
            raise KeyboardInterrupt
        return real_communicate(process, **kwargs)

    def inspect(command, **kwargs):
        plugin_stopped()
        assert lock.StackLock(state_dir, "moonmind").probe()
        assert kwargs["deadline"] > time.monotonic()
        inspections.append(command)
        if state == "inspection-error":
            return subprocess.CompletedProcess(command, 1, "", "unavailable")
        if command[1] == "ps":
            assert f"label=com.docker.compose.project={identity['project']}" in command
            assert "label=com.docker.compose.service=controller" in command
            return subprocess.CompletedProcess(command, 0, "exact-controller", "")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": identity["project"],
                                "com.docker.compose.service": "controller",
                            }
                        },
                        "Image": "sha256:" + "c" * 64,
                        "State": {"Status": state},
                    }
                ]
            ),
            "",
        )

    monkeypatch.setattr(real_popen, "communicate", communicate)
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    monkeypatch.setattr(bootstrap, "_run_capture", inspect)
    original_handler = signal.getsignal(signal.SIGTERM)
    error = {
        "timeout": RuntimeError,
        "interrupt": KeyboardInterrupt,
        "sigterm": SystemExit,
    }[mode]
    with pytest.raises(error) as caught:
        bootstrap.main(
            [
                "ensure",
                "--state-dir",
                str(state_dir),
                "--repo",
                str(tmp_path),
                "--image",
                "ghcr.io/org/moonmind:latest",
            ],
            env={},
        )
    plugin_stopped()
    assert mutations == (["rm"] if verb == "rm" else ["rm", "up"])
    assert signal.getsignal(signal.SIGTERM) == original_handler
    assert not lock.StackLock(state_dir, "moonmind").probe()
    if mode == "timeout":
        assert inspections
        assert "timed out" in str(caught.value)
        assert (
            "state reconciled" if state == "running" else "state unverified"
        ) in str(caught.value)
    else:
        assert not inspections


@pytest.mark.parametrize("outcome", ["gone", "live", "denied"])
def test_non_linux_posix_group_probe_fails_closed(
    controller_path, monkeypatch, outcome
):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    calls = []

    def probe(group, signum):
        calls.append((group, signum))
        if outcome == "gone":
            raise ProcessLookupError
        if outcome == "denied":
            raise PermissionError

    monkeypatch.setattr(bootstrap.os, "killpg", probe)
    if outcome == "denied":
        with pytest.raises(PermissionError):
            bootstrap._group_running(123)
    else:
        assert bootstrap._group_running(123) == (outcome == "live")
    assert calls == [(123, 0)]


def test_posix_cleanup_reaps_launcher_before_group_probe(controller_path, monkeypatch):
    bootstrap = load("bootstrap")
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    calls = []

    class Process:
        pid = 123

        def poll(self):
            calls.append("reaped")
            return 0

        def wait(self, *, timeout):
            calls.append("waited")

    def signal(group, signum):
        if signum == 0:
            assert "reaped" in calls
            raise ProcessLookupError
        calls.append("signalled")

    monkeypatch.setattr(bootstrap.os, "killpg", signal)
    bootstrap._stop_command(Process(), deadline=bootstrap.time.monotonic() + 5)
    assert calls == ["signalled", "reaped", "waited"]


def test_no_pull_startup_shares_budget_and_bounds_timeout_reconciliation(
    controller_path, tmp_path, monkeypatch
):
    import subprocess

    bootstrap = load("bootstrap")
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])

    def owned(command, *, deadline):
        assert deadline == 1125
        calls.append(command)
        if "rm" in command:
            clock[0] += 100
            return subprocess.CompletedProcess(command, 0)
        clock[0] = deadline
        raise bootstrap.CommandTimeoutError("startup timed out")

    def reconcile(project, *, deadline):
        assert project == "exact-controller"
        assert deadline == 1140
        raise RuntimeError("inspection budget exhausted")

    monkeypatch.setattr(bootstrap, "_run_owned", owned)
    monkeypatch.setattr(bootstrap, "_reconcile_controller", reconcile)
    with pytest.raises(
        RuntimeError,
        match="startup timed out; controller state unverified: inspection budget exhausted",
    ):
        bootstrap._compose_up(tmp_path, "exact-controller", "--pull", "never")
    assert len(calls) == 2


def test_no_pull_startup_skips_reconciliation_when_cleanup_is_unverified(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    calls = []

    def owned(*args, **kwargs):
        calls.append(args)
        raise bootstrap.CommandCleanupError("plugin cleanup unverified")

    monkeypatch.setattr(bootstrap, "_run_owned", owned)
    monkeypatch.setattr(
        bootstrap,
        "_reconcile_controller",
        lambda *a, **k: pytest.fail("unclean plugin was inspected/retried"),
    )
    with pytest.raises(
        bootstrap.CommandCleanupError, match="plugin cleanup unverified"
    ):
        bootstrap._compose_up(tmp_path, "exact-controller", "--pull", "never")
    assert len(calls) == 1


@pytest.mark.skipif(
    __import__("sys").platform != "linux", reason="real process-group regression"
)
@pytest.mark.parametrize("launcher_timeout", [False, True])
@pytest.mark.parametrize("cancel_at", ["probe", "wait"])
def test_cancellation_during_cleanup_stops_real_plugin(
    controller_path, tmp_path, monkeypatch, launcher_timeout, cancel_at
):
    import os
    import signal
    import sys
    import time
    from pathlib import Path

    bootstrap = load("bootstrap")
    marker = tmp_path / "plugin"
    script = """
import signal,subprocess,sys,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
subprocess.Popen([sys.executable, '-c',
 'import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
 'open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)', sys.argv[1]])
while not __import__('os').path.exists(sys.argv[1]): time.sleep(.01)
if sys.argv[2] == 'True': time.sleep(60)
"""
    monkeypatch.setattr(bootstrap, "CONTROLLER_START_COMMAND_TIMEOUT_SECONDS", 0.3)
    original = bootstrap._group_running if cancel_at == "probe" else time.sleep
    signalled = []

    def cancel(*args):
        if not signalled:
            signalled.append(True)
            os.kill(os.getpid(), signal.SIGTERM)
        return original(*args)

    if cancel_at == "probe":
        monkeypatch.setattr(bootstrap, "_group_running", cancel)
    else:
        monkeypatch.setattr(bootstrap.time, "sleep", cancel)
    handler = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit):
        bootstrap._run_owned(
            [sys.executable, "-c", script, str(marker), str(launcher_timeout)],
            deadline=time.monotonic() + 6,
        )
    assert signalled
    assert signal.getsignal(signal.SIGTERM) == handler
    try:
        stat = Path(f"/proc/{int(marker.read_text())}/stat").read_text()
    except FileNotFoundError:
        pass
    else:
        assert stat.rsplit(")", 1)[1].split()[0] == "Z"


def test_cancelled_unverified_cleanup_keeps_unsafe_retry_diagnostic(
    controller_path, monkeypatch, capsys
):
    import os
    import signal

    bootstrap = load("bootstrap")

    def cleanup(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        raise bootstrap.CommandCleanupError("plugin remains uncertain")

    monkeypatch.setattr(bootstrap, "_stop_command_group", cleanup)
    original = signal.signal(signal.SIGTERM, bootstrap._cancel_handoff)
    try:
        with pytest.raises(SystemExit):
            bootstrap._stop_command(object(), deadline=1)
        assert signal.getsignal(signal.SIGTERM) == bootstrap._cancel_handoff
    finally:
        signal.signal(signal.SIGTERM, original)
    assert "MOONMIND_CONTROLLER_RETRY_UNSAFE=" in capsys.readouterr().err


def test_no_pull_exhausted_budget_reconciles_without_launching_again(
    controller_path, tmp_path, monkeypatch
):
    bootstrap = load("bootstrap")
    clock = [1000.0]
    commands = []
    observed = []
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    original = bootstrap._run_owned

    def owned(command, *, deadline):
        commands.append(command)
        if "rm" in command:
            clock[0] = deadline - 4
            return __import__("subprocess").CompletedProcess(command, 0)
        return original(command, deadline=deadline)

    monkeypatch.setattr(bootstrap, "_run_owned", owned)
    monkeypatch.setattr(
        bootstrap.subprocess, "Popen", lambda *a, **k: pytest.fail("budget exhausted")
    )
    monkeypatch.setattr(
        bootstrap, "_reconcile_controller",
        lambda project, **k: observed.append(project) or "no controller container",
    )
    with pytest.raises(RuntimeError, match="budget exhausted; controller state reconciled"):
        bootstrap._compose_up(tmp_path, "exact-project", "--pull", "never")
    assert len(commands) == 2
    assert observed == ["exact-project"]
