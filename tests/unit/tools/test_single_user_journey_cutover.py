"""Test the upgrade adapter against the real controller store and stack lock."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/controller"))
import server as controller  # noqa: E402


IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "moonmind-upgrade-4356.fixture"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../fixture\n")
    (repo / ".first-run-journey-project").write_text("moonmind-test-upgrade\n")
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    return repo


def adapter():
    spec = importlib.util.spec_from_file_location(
        "journey_cutover", ROOT / "tools/single_user_journey_cutover.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status", [None, "pending", "attention", "completed"])
def test_requires_passed_check_and_persisted_completion(checkout, monkeypatch, status):
    compose = checkout / "docker-compose.yaml"
    compose.write_text("services: {}\n")
    isolation = checkout / "isolation.json"
    isolation.write_text("{}")
    seen = []

    def converge(store, operation, *, runner, overlay):
        assert controller.lock_mod.StackLock(
            store.state_dir, operation["stack"]
        ).probe()
        assert Path(overlay).read_text() == f"MOONMIND_IMAGE={IMAGE}\n"
        assert operation["target"]["composeFiles"] == [str(compose), str(isolation)]
        seen.append(operation["operationId"])
        if status:
            store.record_routing_cutover(operation["operationId"], {"status": status})
        return {"status": "passed"}

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    kwargs = dict(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="candidate-sha",
        compose_files=[str(compose), str(isolation)],
        controller=controller,
    )
    result = adapter().run_cutover(**kwargs)
    assert result["passed"] is (status == "completed")
    again = adapter().run_cutover(**kwargs)
    assert seen[0] == seen[1]
    assert again["passed"] is result["passed"]


def test_none_check_fails_even_with_completed_receipt(checkout, monkeypatch):
    def converge(store, operation, **kwargs):
        store.record_routing_cutover(operation["operationId"], {"status": "completed"})
        return None

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    result = adapter().run_cutover(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="sha",
        compose_files=[str(checkout / "docker-compose.yaml")],
        controller=controller,
    )
    assert not result["passed"]


def test_refuses_non_test_project_before_controller_write(checkout):
    with pytest.raises(ValueError, match="project"):
        adapter().run_cutover(
            repo=checkout,
            project="moonmind",
            image=IMAGE,
            revision="sha",
            compose_files=[str(checkout / "docker-compose.yaml")],
            controller=controller,
        )
    assert not (checkout / "deploy/state/controller").exists()


def test_failed_check_rejects_completed_receipt(checkout, monkeypatch):
    def converge(store, operation, **kwargs):
        store.record_routing_cutover(operation["operationId"], {"status": "completed"})
        return {"status": "failed"}

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    assert not adapter().run_cutover(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="sha",
        compose_files=[str(checkout / "docker-compose.yaml")],
        controller=controller,
    )["passed"]


@pytest.mark.parametrize("fault", ["marker", "checkout", "compose", "image"])
def test_rejects_unowned_or_unresolved_target(checkout, fault, monkeypatch):
    monkeypatch.setattr(controller, "converge_routing_cutover", lambda *a, **k: None)
    image = IMAGE
    files = [str(checkout / "docker-compose.yaml")]
    if fault == "marker":
        (checkout / ".first-run-journey-project").write_text("moonmind-test-other")
    elif fault == "checkout":
        (checkout / ".git").unlink()
    elif fault == "compose":
        files = [str(checkout / "missing.yaml")]
    else:
        image = "candidate:latest"
    with pytest.raises(ValueError):
        adapter().run_cutover(
            repo=checkout,
            project="moonmind-test-upgrade",
            image=image,
            revision="sha",
            compose_files=files,
            controller=controller,
        )
    assert not (checkout / "deploy/state/controller").exists()


def test_first_timeout_and_helper_logs_survive_later_ownership_refusals(
    checkout, monkeypatch
):
    import json

    helper_id = "a" * 12
    commands = []
    launches = []

    class Runner:
        def run(self, args, timeout_seconds):
            args = tuple(args)
            commands.append((args, timeout_seconds))
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": helper_id if launches else ""}
            if args[:2] == ("docker", "inspect"):
                return {"exit": 0, "output": '{"running":true,"exitCode":0}'}
            if args[:2] == ("docker", "logs"):
                return {"exit": 0, "output": 'MOONMIND_OMNIGENT_RESULT={"status":"completed"}\ntoken=private-token'}
            if "run" in args:
                launches.append(args)
                raise controller.engine.CommandError(
                    "run", 124, "first timeout; password=private-password"
                )
            raise AssertionError(args)

    monkeypatch.setattr(controller.engine, "subprocess_runner", Runner)
    monkeypatch.setattr(controller, "ROUTING_CUTOVER_RETRY_SECONDS", 0)
    monkeypatch.setattr(controller, "_temporal_worker_images", lambda *a: (["worker"], []))
    evidence = checkout / "evidence/cutover-diagnostics.json"
    result = adapter().run_cutover(
        repo=checkout, project="moonmind-test-upgrade", image=IMAGE,
        revision="sha", compose_files=[str(checkout / "docker-compose.yaml")],
        controller=controller, diagnostics_path=evidence,
    )
    assert not result["passed"]  # A printed receipt is not a verified helper exit.
    assert "still running" in result["check"]["detail"]
    assert len(launches) == 1
    captured = json.loads(evidence.read_text())
    assert captured["firstFailure"]["exitStatus"] == 124
    assert "first timeout" in captured["firstFailure"]["output"]
    assert "MOONMIND_OMNIGENT_RESULT" in captured["helpers"][helper_id]["logs"]
    assert "private-token" not in evidence.read_text()
    assert "private-password" not in evidence.read_text()
    listings = [args for args, _ in commands if args[:2] == ("docker", "ps") and "--all" in args]
    assert len(listings) == 1
    assert "label=com.docker.compose.project=moonmind-test-upgrade" in listings[0]
    assert f"label={controller.OMNIGENT_OPERATION_LABEL}={result['operation']['operationId']}" in listings[0]
    inspections = [args for args, _ in commands if args[:2] == ("docker", "inspect")]
    assert len(inspections) == 1
    projection = inspections[0][inspections[0].index("--format") + 1]
    assert ".Config" not in projection and ".State.Health.Log" not in projection
    assert inspections[0][-1] == helper_id
    assert not any("kill" in args or "stop" in args or "rm" in args for args, _ in commands)
    assert all(0 < timeout <= 30 for args, timeout in commands if args[:2] in (("docker", "inspect"), ("docker", "logs")))


def test_diagnostic_failure_never_replaces_the_original_error(tmp_path, monkeypatch):
    module = adapter()
    error = controller.engine.CommandError("run", 124, "original timeout")
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            if "run" in args:
                raise error
            raise OSError("diagnostic Docker read failed")

    path = tmp_path / "diagnostics.json"
    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=path,
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    with pytest.raises(controller.engine.CommandError) as raised:
        runner.run(("docker", "compose", "--project-name", "moonmind-test-upgrade",
                    "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
                    "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE,
                    "-c", "pass"), 900)
    assert raised.value is error
    assert "original timeout" in path.read_text()
    assert "collectionError" in path.read_text()
    assert len(commands) == 2


def test_diagnostic_budget_bounds_observations_and_helper_count(tmp_path, monkeypatch):
    import json

    module = adapter()
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    reads = []

    class Runner:
        def run(self, args, timeout_seconds):
            if "run" in args:
                raise controller.engine.CommandError("run", 124, "original timeout")
            reads.append((tuple(args), timeout_seconds))
            clock[0] += 20
            return {"exit": 0, "output": "\n".join(f"{i:012x}" for i in range(10))}

    path = tmp_path / "diagnostics.json"
    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=path,
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    with pytest.raises(controller.engine.CommandError):
        runner.run(("docker", "compose", "--project-name", "moonmind-test-upgrade",
                    "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
                    "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE,
                    "-c", "pass"), 900)
    captured = json.loads(path.read_text())
    assert captured["omittedHelpers"] == 6
    assert len(reads) == 2
    assert reads[0][1] <= 30 and reads[1][1] <= 10
    assert "collectionError" in captured


def test_harness_stack_dump_preserves_helper_arguments_and_exit(tmp_path):
    import subprocess

    module = adapter()
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            return {"exit": 0, "output": ""}

    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=tmp_path / "diagnostics.json",
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    source = "import sys; print('receipt='+sys.argv[1]); sys.exit(7)"
    command = ("docker", "compose", "--project-name", "moonmind-test-upgrade",
               "run", "--label",
               f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op", "--entrypoint",
               "python", "temporal-worker-deployment-control", "-c", source, "unchanged-input")
    runner.run(command, 900)
    observed = commands[0]
    index = observed.index("-c") + 1
    assert observed[:index] == command[:index]
    assert observed[index + 1:] == command[index + 1:]
    assert "dump_traceback_later(120, repeat=False, exit=False)" in observed[index]
    result = subprocess.run(
        [sys.executable, "-c", observed[index], "unchanged-input"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 7
    assert result.stdout.strip() == "receipt=unchanged-input"
    assert "MOONMIND_CUTOVER_HELPER=python-started" in result.stderr
    assert "MOONMIND_CUTOVER_HELPER=launcher-exited" in result.stderr
    assert not (tmp_path / "diagnostics.json").exists()


def test_capture_is_redacted_bounded_and_combines_container_stderr(tmp_path):
    import json

    module = adapter()
    helper_id = "b" * 12

    class Runner:
        def run(self, args, timeout_seconds):
            if "run" in args:
                raise controller.engine.CommandError("run", 124, "initial timeout")
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": helper_id, "stdout": helper_id}
            if args[:2] == ("docker", "inspect"):
                return {"exit": 0, "output": "{}", "stdout": "{}"}
            return {"exit": 0, "stdout": "container stdout", "output": (
                "x" * 9000 + "\ncontainer stdout\ncontainer stderr\n"
                "password=hunter2 token=private-token secret=private-secret "
                "https://user:private-password@example.invalid/\n"
                "ghp_1234567890abcdefgh xoxb-1234567890abcdefgh sk-live-1234567890abcdefgh "
                'Authorization: Bearer abcdefghijklmnop\n'
                '{"token": "SYNTHETIC_TEST_TOKEN_12345"} '
                "api_key=SYNTHETIC_TEST_KEY_12345\nMOONMIND_OMNIGENT_RESULT={}"),
            }

    path = tmp_path / "diagnostics.json"
    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=path,
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    with pytest.raises(controller.engine.CommandError):
        runner.run(("docker", "compose", "--project-name", "moonmind-test-upgrade",
                    "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
                    "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE,
                    "-c", "pass"), 900)
    logs = json.loads(path.read_text())["helpers"][helper_id]["logs"]
    assert len(logs) <= 4000
    assert "container stderr" in logs and "MOONMIND_OMNIGENT_RESULT" in logs
    assert all(secret not in path.read_text() for secret in (
        "hunter2", "private-token", "private-secret", "private-password",
        "ghp_1234567890abcdefgh", "xoxb-1234567890abcdefgh",
        "sk-live-1234567890abcdefgh", "abcdefghijklmnop",
        "SYNTHETIC_TEST_TOKEN_12345", "SYNTHETIC_TEST_KEY_12345",
    ))
    scrub = module.artifact_redactor.redact_directory(tmp_path)
    assert scrub["files_scanned"] == 1
    assert "MOONMIND_OMNIGENT_RESULT" in path.read_text()


@pytest.mark.parametrize("fault", ["project", "label", "entrypoint", "service"])
def test_diagnostics_never_instrument_or_inspect_an_unmatched_helper(tmp_path, fault):
    module = adapter()
    error = RuntimeError("original")
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            raise error

    command = ["docker", "compose", "--project-name", "moonmind-test-upgrade",
               "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
               "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE, "-c", "pass"]
    if fault == "project":
        command[command.index("--project-name") + 1] = "moonmind"
    elif fault == "label":
        command[command.index("--label")] = "--env"  # A value alone is not a label.
    elif fault == "entrypoint":
        command[command.index("--entrypoint") + 1] = "sh"
    else:
        command[command.index(controller.LEGACY_CONTROL_SERVICE)] = "api"
    path = tmp_path / "diagnostics.json"
    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=path,
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    with pytest.raises(RuntimeError) as raised:
        runner.run(tuple(command), 900)
    assert raised.value is error
    assert commands == [tuple(command)]
    assert not path.exists()


def test_unexpected_capture_failure_does_not_replace_original(tmp_path, monkeypatch):
    module = adapter()
    error = controller.engine.CommandError("run", 124, "original timeout")

    class Runner:
        def run(self, args, timeout_seconds):
            raise error

    runner = module.DiagnosticRunner(
        Runner(), controller=controller, path=tmp_path / "diagnostics.json",
        project="moonmind-test-upgrade", operation_id="owned-op",
    )

    def fail(_error):
        raise ValueError("diagnostic persistence failure")

    monkeypatch.setattr(runner, "_capture", fail)
    with pytest.raises(controller.engine.CommandError) as raised:
        runner.run(("docker", "compose", "--project-name", "moonmind-test-upgrade",
                    "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
                    "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE,
                    "-c", "pass"), 900)
    assert raised.value is error


def test_real_runner_redaction_composes_without_leaking_header_values(tmp_path, monkeypatch):
    import json
    import subprocess

    module = adapter()
    helper_id = "c" * 12
    header = "Authorization: Bearer SYNTHETIC_TEST_TOKEN_12345"

    def command(args, **kwargs):
        if "run" in args:
            raise subprocess.TimeoutExpired(args, 900, stderr=header.encode())
        if args[:2] == ["docker", "ps"]:
            stdout, stderr = helper_id, ""
        elif args[:2] == ["docker", "inspect"]:
            stdout, stderr = "{}", ""
        else:
            stdout, stderr = "", header + '\n{"token": "tiny"} api_key=k\nhelper stalled'
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(controller.engine.subprocess, "run", command)
    path = tmp_path / "diagnostics.json"
    runner = module.DiagnosticRunner(
        controller.engine.subprocess_runner(), controller=controller, path=path,
        project="moonmind-test-upgrade", operation_id="owned-op",
    )
    with pytest.raises(controller.engine.CommandError):
        controller.engine.run_command(runner, (
            "docker", "compose", "--project-name", "moonmind-test-upgrade",
            "run", "--label", f"{controller.OMNIGENT_OPERATION_LABEL}=owned-op",
            "--entrypoint", "python", controller.LEGACY_CONTROL_SERVICE, "-c", "pass",
        ), timeout_seconds=900)
    text = path.read_text()
    assert "SYNTHETIC_TEST_TOKEN_12345" not in text
    assert "tiny" not in text and "api_key=k" not in text
    captured = json.loads(text)
    assert captured["firstFailure"]["exitStatus"] == 124
    assert "helper stalled" in captured["helpers"][helper_id]["logs"]
