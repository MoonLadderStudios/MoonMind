"""Host handoff proves the updater's Docker transport before deployment."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "update_transport",
    ROOT / ".agents/skills/update-moonmind/scripts/update_release.py",
)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)
SERVICE = "temporal-worker-deployment-control"
COMMAND = ["docker", "compose", "--project-name", "installed", "-f", "release.yaml"]
DNS_ERROR = "lookup docker-proxy on 127.0.0.11:53: no such host"
MOUNT_ERROR = "OCI runtime create failed: error mounting docker.sock: not a directory"


def transport(monkeypatch, outcomes, *, endpoint="tcp://docker-proxy:2375", starts=()):
    commands = []
    probes = iter(outcomes)
    repairs = iter(starts)
    config = {
        "services": {
            SERVICE: {"environment": {"DOCKER_HOST": endpoint}},
            "docker-proxy": {"networks": {"private": {}}},
        },
        "networks": {"private": {"name": "operator-private-network"}},
    }

    def command(args, **kwargs):
        commands.append(list(args))
        assert kwargs["env"] == {"SYSTEM_DOCKER_HOST": endpoint}
        if "config" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps(config), stderr="")
        if "run" in args:
            policies = [
                json.loads(Path(args[index + 1]).read_text())
                for index, value in enumerate(args[:-1])
                if value == "-f" and Path(args[index + 1]).is_file()
            ]
            assert policies[-1] == {"services": {SERVICE: {"pull_policy": "never"}}}
            outcome = next(probes)
        elif "up" in args:
            outcome = next(repairs, "ok")
            assert args[-1] == "docker-proxy"
            assert "--no-deps" in args
            assert "down" not in args
        else:
            raise AssertionError(args)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(
            returncode=0 if outcome == "ok" else 1,
            stdout="28.0.0\n" if outcome == "ok" else "",
            stderr="" if outcome == "ok" else outcome,
        )

    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(
        update,
        "_legacy_transport_lease",
        lambda *_args: nullcontext(lambda: None),
        raising=False,
    )
    return commands, {"SYSTEM_DOCKER_HOST": endpoint}


@pytest.mark.parametrize(
    "endpoint", ["tcp://docker-proxy:2375", "tcp://operator-docker:2376"]
)
def test_working_transport_preserves_proxy_and_explicit_endpoint(
    tmp_path, monkeypatch, endpoint
):
    commands, env = transport(monkeypatch, ["ok"], endpoint=endpoint)
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert not any("up" in command for command in commands)
    probe = commands[-1]
    assert probe[: len(COMMAND)] == COMMAND
    assert probe[-3:] == ["info", "--format", "{{.ServerVersion}}"]
    assert "--no-deps" in probe
    assert "--pull" not in probe
    assert not Path(probe[probe.index("run") - 1]).exists()


@pytest.mark.parametrize("pull_policy", [None, "always", "never", "missing", "build"])
def test_distinct_worker_acquisition_preserves_compose_policy_and_file_selection(
    tmp_path, monkeypatch, pull_policy
):
    image = "registry.example/worker:operator-selected"
    selected = [*COMMAND, "-f", str(tmp_path / "operator-override.json")]
    worker = {
        "image": image,
        "platform": "linux/amd64",
        "build": {"context": "./operator-worker"},
        "environment": {"DOCKER_HOST": "tcp://operator-docker:2376"},
    }
    if pull_policy is not None:
        worker["pull_policy"] = pull_policy
    events = []
    configs = []

    def host_run(args, **kwargs):
        if "config" in args:
            configs.append(args)
            return json.dumps({"services": {SERVICE: worker}})
        assert args == [
            *selected,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            "docker",
            SERVICE,
            "--version",
        ]
        assert kwargs == {"cwd": tmp_path, "env": env}
        events.append("acquire")
        return "Docker version 28.0.0"

    def subprocess_run(args, **kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            events.append("inspect")
            return SimpleNamespace(returncode=0, stdout="cached-image", stderr="")
        assert "run" in args and "info" in args
        assert args[: len(selected)] == selected
        overlay = json.loads(Path(args[args.index("run") - 1]).read_text())
        assert overlay == {"services": {SERVICE: {"pull_policy": "never"}}}
        events.append("probe")
        return SimpleNamespace(returncode=0, stdout="28.0.0\n", stderr="")

    monkeypatch.setattr(update, "run", host_run)
    monkeypatch.setattr(update.subprocess, "run", subprocess_run)
    env = {"MOONMIND_IMAGE": "registry.example/application@sha256:selected"}
    update._ensure_legacy_docker_transport(selected, tmp_path, env)
    assert configs[0] == [*selected, "config", "--format", "json"]
    assert events == ["acquire", "probe"]


@pytest.mark.parametrize(
    "failure", ["No such image: operator-worker", "Registry refused image acquisition"]
)
def test_worker_acquisition_failure_stops_before_transport_probe_or_repair(
    tmp_path, monkeypatch, failure
):
    events = []

    def host_run(args, **kwargs):
        if "config" in args:
            return json.dumps(
                {
                    "services": {
                        SERVICE: {
                            "image": "registry.example/worker:operator-selected",
                            "pull_policy": "never",
                            "environment": {"DOCKER_HOST": "tcp://docker-proxy:2375"},
                        },
                        "docker-proxy": {},
                    }
                }
            )
        events.append("acquire")
        raise RuntimeError(failure)

    def subprocess_run(args, **kwargs):
        events.append("inspect" if "inspect" in args else "probe")
        return SimpleNamespace(returncode=0, stdout="cached-image", stderr="")

    monkeypatch.setattr(update, "run", host_run)
    monkeypatch.setattr(update.subprocess, "run", subprocess_run)
    monkeypatch.setattr(
        update, "_legacy_transport_lease", lambda *_args: events.append("lease")
    )
    with pytest.raises(RuntimeError, match=failure):
        update._ensure_legacy_docker_transport(
            COMMAND,
            tmp_path,
            {"MOONMIND_IMAGE": "registry.example/application@sha256:selected"},
        )
    assert events == ["acquire"]


def test_verified_default_worker_image_does_not_need_another_acquisition(
    tmp_path, monkeypatch
):
    image = "registry.example/application@sha256:selected"
    events = []

    def host_run(args, **kwargs):
        assert "config" in args
        return json.dumps(
            {
                "services": {
                    SERVICE: {
                        "image": image,
                        "environment": {"DOCKER_HOST": "tcp://operator-docker:2376"},
                    }
                }
            }
        )

    def subprocess_run(args, **kwargs):
        assert "info" in args
        events.append("probe")
        return SimpleNamespace(returncode=0, stdout="28.0.0\n", stderr="")

    monkeypatch.setattr(update, "run", host_run)
    monkeypatch.setattr(update.subprocess, "run", subprocess_run)
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, {"MOONMIND_IMAGE": image})
    assert events == ["probe"]


def test_proxy_restored_by_concurrent_owner_is_reprobed_under_shared_lease(
    tmp_path, monkeypatch
):
    commands, env = transport(monkeypatch, [DNS_ERROR, "ok"])
    lease_events = []

    @contextmanager
    def lease(command, repo, environment):
        assert command[: len(COMMAND)] == COMMAND
        assert repo == tmp_path
        assert environment == env
        lease_events.append("acquired")
        try:
            yield lambda: None
        finally:
            lease_events.append("released")

    monkeypatch.setattr(update, "_legacy_transport_lease", lease, raising=False)
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert lease_events == ["acquired", "released"]
    assert not any("up" in command for command in commands)


def test_shared_lease_refusal_preserves_original_error_without_proxy_mutation(
    tmp_path, monkeypatch
):
    commands, env = transport(monkeypatch, [DNS_ERROR, DNS_ERROR, "ok"])

    @contextmanager
    def lease(*_args):
        raise RuntimeError("Deployment update for stack 'moonmind' is already running")
        yield

    monkeypatch.setattr(update, "_legacy_transport_lease", lease, raising=False)
    with pytest.raises(RuntimeError) as error:
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert DNS_ERROR in str(error.value)
    assert "already running" in str(error.value)
    assert not any("up" in command for command in commands)


def test_failed_proxy_repair_releases_shared_lease(tmp_path, monkeypatch):
    commands, env = transport(monkeypatch, [DNS_ERROR] * 8)
    lease_events = []

    @contextmanager
    def lease(*_args):
        lease_events.append("acquired")
        try:
            yield lambda: None
        finally:
            lease_events.append("released")

    monkeypatch.setattr(update, "_legacy_transport_lease", lease, raising=False)
    with pytest.raises(RuntimeError, match="no such host"):
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert lease_events == ["acquired", "released"]
    assert len([command for command in commands if "up" in command]) == 2


def test_lost_shared_lease_prevents_host_proxy_mutation(tmp_path, monkeypatch):
    commands, env = transport(monkeypatch, [DNS_ERROR] * 8)

    def held():
        raise RuntimeError("Shared deployment lock holder exited")

    @contextmanager
    def lease(*_args):
        yield held

    monkeypatch.setattr(update, "_legacy_transport_lease", lease)
    with pytest.raises(RuntimeError) as error:
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert "holder exited" in str(error.value)
    assert DNS_ERROR in str(error.value)
    assert not any("up" in command for command in commands)


@pytest.mark.parametrize(
    "loss_point",
    ["locked_probe", "start", "start_probe", "recreate", "recreate_probe"],
)
def test_lease_loss_during_recovery_prevents_handoff(tmp_path, monkeypatch, loss_point):
    outcomes = (
        [DNS_ERROR, "ok"]
        if loss_point == "locked_probe"
        else [DNS_ERROR] * (5 if loss_point.startswith("recreate") else 2) + ["ok"]
    )
    commands, env = transport(monkeypatch, outcomes)
    original_run = update.subprocess.run
    counts = {"run": 0, "up": 0}
    lost = False

    def run(args, **kwargs):
        nonlocal lost
        result = original_run(args, **kwargs)
        for operation in counts:
            if operation in args:
                counts[operation] += 1
        lost = lost or (
            (loss_point == "locked_probe" and counts["run"] == 2)
            or (loss_point == "start" and counts["up"] == 1)
            or (loss_point == "start_probe" and counts["run"] == 3)
            or (loss_point == "recreate" and counts["up"] == 2)
            or (loss_point == "recreate_probe" and counts["run"] == 6)
        )
        return result

    def held():
        if lost:
            raise RuntimeError("Shared deployment lock holder exited")

    @contextmanager
    def lease(*_args):
        yield held

    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.setattr(update, "_legacy_transport_lease", lease)
    with pytest.raises(RuntimeError, match="holder exited"):
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert len([command for command in commands if "up" in command]) <= 2


def test_missing_or_stopped_proxy_is_started_before_handoff(tmp_path, monkeypatch):
    commands, env = transport(monkeypatch, [DNS_ERROR, DNS_ERROR, "ok"])
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    repairs = [command for command in commands if "up" in command]
    assert len(repairs) == 1
    assert "--no-recreate" in repairs[0]
    assert commands[-1][-3:] == ["info", "--format", "{{.ServerVersion}}"]


def test_stale_socket_start_failure_recreates_only_proxy_once(tmp_path, monkeypatch):
    commands, env = transport(
        monkeypatch, [DNS_ERROR] * 5 + ["ok"], starts=[MOUNT_ERROR, "ok"]
    )
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    repairs = [command for command in commands if "up" in command]
    assert len(repairs) == 2
    assert "--no-recreate" in repairs[0]
    assert "--force-recreate" in repairs[1]
    assert all(command[-1] == "docker-proxy" for command in repairs)


def test_failed_start_acknowledgment_is_reconciled_before_recreation(
    tmp_path, monkeypatch
):
    commands, env = transport(
        monkeypatch,
        [DNS_ERROR, DNS_ERROR, "ok"],
        starts=[subprocess.TimeoutExpired(COMMAND, 60)],
    )
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert len([command for command in commands if "up" in command]) == 1


def test_proxy_readiness_retries_without_recreation(tmp_path, monkeypatch):
    commands, env = transport(monkeypatch, [DNS_ERROR] * 3 + ["ok"])
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert len([command for command in commands if "up" in command]) == 1


def test_broken_proxy_recovery_is_bounded_and_preserves_redacted_errors(
    tmp_path, monkeypatch
):
    commands, env = transport(
        monkeypatch, [DNS_ERROR] * 8, starts=[MOUNT_ERROR, "password=private-value"]
    )
    with pytest.raises(RuntimeError) as error:
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert DNS_ERROR in str(error.value)
    assert MOUNT_ERROR in str(error.value)
    assert "private-value" not in str(error.value)
    assert len([command for command in commands if "up" in command]) == 2
    assert all("--submit" not in command for command in commands)


def test_explicit_unreachable_endpoint_never_mutates_proxy(tmp_path, monkeypatch):
    commands, env = transport(
        monkeypatch, ["connection refused"], endpoint="tcp://remote:2376"
    )
    with pytest.raises(RuntimeError, match="connection refused"):
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert not any("up" in command for command in commands)


def test_probe_timeout_is_reconciled_without_mutating_proxy(tmp_path, monkeypatch):
    commands, env = transport(
        monkeypatch, [subprocess.TimeoutExpired(COMMAND, 30), "ok"]
    )
    update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert not any("up" in command for command in commands)


def test_updater_container_start_failure_leaves_healthy_proxy_intact(
    tmp_path, monkeypatch
):
    commands, env = transport(monkeypatch, [MOUNT_ERROR] * 7)
    with pytest.raises(RuntimeError, match="error mounting"):
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert not any("up" in command for command in commands)


@pytest.mark.parametrize(
    "registry_error", ["no such host", "connection refused", "503 Service Unavailable"]
)
def test_updater_image_pull_failure_does_not_recreate_proxy(
    tmp_path, monkeypatch, registry_error
):
    commands, env = transport(
        monkeypatch,
        [
            f'Error response from daemon: Get "https://registry.invalid/v2/": {registry_error}'
        ]
        * 7,
    )
    with pytest.raises(RuntimeError, match="registry.invalid"):
        update._ensure_legacy_docker_transport(COMMAND, tmp_path, env)
    assert not any("up" in command for command in commands)


def lease_child(monkeypatch, diagnostic, *, exit_code=None, ignore_eof=False):
    events = []

    class Child:
        def __init__(self, command, **kwargs):
            self.command = command
            self.returncode = exit_code
            self.stdin = SimpleNamespace(close=self.close_stdin)
            kwargs["stdout"].write(diagnostic.encode())
            kwargs["stdout"].flush()
            assert kwargs["stderr"] == subprocess.STDOUT
            assert kwargs["stdin"] == subprocess.PIPE
            events.append("started")

        def close_stdin(self):
            events.append("stdin closed")
            if not ignore_eof:
                self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, *, timeout):
            events.append(("wait", timeout))
            if self.returncode is None:
                raise subprocess.TimeoutExpired(self.command, timeout)
            return self.returncode

        def kill(self):
            events.append("killed")
            self.returncode = -9

    children = []

    def start(command, **kwargs):
        child = Child(command, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(update.subprocess, "Popen", start)
    return children, events


def test_lease_holder_releases_stdin_on_repair_failure(tmp_path, monkeypatch):
    children, events = lease_child(
        monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n"
    )
    with pytest.raises(RuntimeError, match="repair failed"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            assert events == ["started"]
            raise RuntimeError("repair failed")
    assert events == ["started", "stdin closed", ("wait", 10)]
    holder = children[0].command
    assert holder[: len(COMMAND)] == COMMAND
    assert "--no-deps" in holder
    assert "--pull" not in holder
    assert "FileDeploymentUpdateLockManager" in holder[-1]
    assert "manager.acquire('moonmind')" in holder[-1]


def test_lease_guard_detects_holder_exit_before_host_mutation(tmp_path, monkeypatch):
    children, _ = lease_child(monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n")
    with update._legacy_transport_lease(COMMAND, tmp_path, {}) as held:
        held()
        children[0].returncode = 1
        with pytest.raises(RuntimeError, match="holder exited"):
            held()


def test_abnormal_holder_exit_blocks_handoff_even_when_container_is_gone(
    tmp_path, monkeypatch
):
    children, _ = lease_child(monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n")
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Error: No such container"
        ),
    )
    with pytest.raises(RuntimeError, match="holder exited"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            children[0].returncode = 137
            # Closing stdin cannot reset the status of an already exited child.
            children[0].stdin.close = lambda: None


def test_lease_holder_refusal_reports_redacted_diagnostics(tmp_path, monkeypatch):
    _, events = lease_child(
        monkeypatch, "DEPLOYMENT_LOCKED token=private-value\n", exit_code=1
    )
    with pytest.raises(RuntimeError) as error:
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            pytest.fail("lease was never acquired")
    assert "DEPLOYMENT_LOCKED" in str(error.value)
    assert "private-value" not in str(error.value)
    assert "stdin closed" in events


def test_lease_holder_readiness_has_bounded_wait(tmp_path, monkeypatch):
    _, events = lease_child(monkeypatch, "")
    monkeypatch.setattr(update, "_LEGACY_TRANSPORT_LEASE_TIMEOUT_SECONDS", 0)
    with pytest.raises(RuntimeError, match="lock was not acquired"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            pytest.fail("holder never confirmed readiness")
    assert events == ["started", "stdin closed", ("wait", 10)]


def test_lease_cleanup_removes_only_owned_holder_after_lost_eof_acknowledgment(
    tmp_path, monkeypatch
):
    children, events = lease_child(
        monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n", ignore_eof=True
    )
    removed = []

    def cleanup(command, **kwargs):
        assert kwargs["timeout"] == 10
        removed.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(update.subprocess, "run", cleanup)
    with pytest.raises(RuntimeError, match="original repair failure"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            raise RuntimeError("original repair failure")
    holder = children[0].command
    name = holder[holder.index("--name") + 1]
    assert removed == [["docker", "rm", "--force", name]]
    assert events[-2:] == ["killed", ("wait", 5)]


def test_uncertain_holder_cleanup_fails_release_handoff(tmp_path, monkeypatch):
    lease_child(
        monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n", ignore_eof=True
    )
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Docker daemon is unavailable"
        ),
    )
    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            pass


def test_holder_cleanup_failure_preserves_primary_repair_error(
    tmp_path, monkeypatch, capsys
):
    children, events = lease_child(
        monkeypatch, update._LEGACY_TRANSPORT_LEASE_READY + "\n", ignore_eof=True
    )
    monkeypatch.setattr(
        update.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Docker daemon is unavailable"
        ),
    )
    with pytest.raises(RuntimeError, match="original proxy error"):
        with update._legacy_transport_lease(COMMAND, tmp_path, {}):
            raise RuntimeError("original proxy error")
    assert "cleanup unavailable" in capsys.readouterr().out
    assert children[0].returncode is not None
    assert "killed" in events
