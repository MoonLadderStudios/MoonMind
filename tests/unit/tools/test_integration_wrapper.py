"""Run the real hermetic integration wrapper against a disposable Docker CLI."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "tools/test_integration.sh"
REPORT = Path("artifacts/integration-ci")

DOCKER_COUNTERPART = r"""
import json, os, re, signal, sys, time
from pathlib import Path

args = sys.argv[1:]
config = json.loads(Path(os.environ["WRAPPER_TEST_CONFIG"]).read_text())
with Path(os.environ["WRAPPER_TEST_CALLS"]).open("a") as output:
    output.write(json.dumps(args) + "\n")
if args == ["compose", "version"] or args[:2] == ["image", "inspect"]:
    raise SystemExit(0)
directory = Path(args[args.index("--project-directory") + 1])
command = args[args.index("--project-directory") + 2:]
if command[:1] == ["port"]:
    time.sleep(config.get("public_port_sleep", 0))
    print(config.get("public_binding", "0.0.0.0:43127"))
    raise SystemExit(0)
if command[:1] == ["run"]:
    if command[-1] == "tests.integration.security.quality":
        quality = directory / os.environ.get("MOONMIND_SECURITY_QUALITY_OUTPUT", "artifacts/integration-ci/security-quality")
        assert (quality / "sample/native.txt").is_file()
        status = config.get("quality_status", 0)
        (quality / "summary.json").write_text(json.dumps({"passed": status == 0}))
        print("quality evidence checked", flush=True)
        raise SystemExit(status)
    if command[-2:-1] == ["-lc"] and command[-1].startswith("rm -rf -- "):
        import shlex, shutil
        target = directory / shlex.split(command[-1])[-1]
        assert target == directory / os.environ["MOONMIND_SECURITY_QUALITY_OUTPUT"]
        time.sleep(config.get("evidence_cleanup_sleep", 0))
        status = config.get("evidence_cleanup_status", 0)
        if status == 0:
            shutil.rmtree(target)
        raise SystemExit(status)
    if os.environ.get("MOONMIND_CI_RECEIPT") and not os.environ.get("MOONMIND_CI_EXPECTED_PYTHON"):
        raise SystemExit(9)
    print("receipt path: " + os.environ.get("MOONMIND_CI_RECEIPT", ""), flush=True)
    junit = re.search(r"--junitxml=(\S+)", command[-1])
    phase = "seed" if "storage-restart-seed.xml" in command[-1] else "verify" if "storage-restart-verify.xml" in command[-1] else "main"

    def report(status):
        if junit and config.get("write_junit", True):
            path = directory / junit.group(1)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"<testsuite failures='{int(status != 0)}'/>")
        if config.get("write_quality", True):
            quality = directory / os.environ.get("MOONMIND_SECURITY_QUALITY_OUTPUT", "artifacts/integration-ci/security-quality") / "sample"
            quality.mkdir(parents=True, exist_ok=True)
            (quality / "native.txt").write_text("actual retained fixture observation")
        raise SystemExit(status)

    signal.signal(signal.SIGINT, lambda *_: report(2))
    print("collected 3 items", flush=True)
    print("public storage endpoint: " + os.environ.get("MOONMIND_TEST_S3_PUBLIC_ENDPOINT", ""), flush=True)
    print("owned report root: " + os.environ.get("MOONMIND_INTEGRATION_REPORT_PATH", ""), flush=True)
    print("tests/integration/test_example.py::test_case FAILED", flush=True)
    Path(os.environ["WRAPPER_TEST_STARTED"]).write_text("started")
    time.sleep(config.get("run_sleep", 0))
    report(config.get(phase + "_status", config.get("run_status", 0)))
if command[:1] == ["restart"]:
    raise SystemExit(config.get("restart_status", 0))
if command[:1] in (["down"], ["logs"], ["ps"]):
    print(f"{command[0]} sk-live-wrapper_test_secret observation", flush=True)
    time.sleep(config.get(command[0] + "_sleep", 0))
"""


@dataclass
class Wrapper:
    repo: Path
    config: Path
    calls_path: Path
    started: Path
    env: dict[str, str]

    def start(self, **config: object) -> subprocess.Popen:
        self.config.write_text(json.dumps(config))
        return subprocess.Popen(
            ["bash", str(self.repo / "tools/test_integration.sh")],
            cwd=self.repo,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run(self, **config: object) -> tuple[int, str, float]:
        started = time.monotonic()
        process = self.start(**config)
        stdout, stderr = process.communicate(timeout=60)
        return process.returncode, stdout + stderr, time.monotonic() - started

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def compose_commands(self) -> list[list[str]]:
        return [
            call[call.index("--project-directory") + 2 :]
            for call in self.calls()
            if "--project-directory" in call
        ]

    def pytest_command(self) -> str:
        runs = [command for command in self.compose_commands() if command[0] == "run" and "pytest-integration-ci.xml" in command[-1]]
        assert len(runs) == 1
        return runs[0][-1]

    def downs(self) -> int:
        return sum(command[:1] == ["down"] for command in self.compose_commands())


def _make_wrapper(tmp_path: Path, repo: Path) -> Wrapper:
    tools = repo / "tools"
    tools.mkdir(parents=True)
    (tools / SCRIPT.name).write_text(SCRIPT.read_text())
    helper = tools / "gnu_timeout.sh"
    helper.write_text((SCRIPT.parent / helper.name).read_text())
    helper.chmod(0o755)
    (tools / "test_worker_count.py").write_text("print(4)\n")
    (tools / "ci").mkdir()
    (tools / "ci/write_backend_matrix_summary.py").write_text(
        (SCRIPT.parent / "ci/write_backend_matrix_summary.py").read_text()
    )
    (tools / "ci/hermetic_shards.py").write_text("# Optional hints absent.\n")
    (repo / "docker-compose.test.yaml").write_text("services: {}\n")
    (repo / ".env-template").write_text("TEMPLATE=1\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(f"#!{sys.executable}\n" + DOCKER_COUNTERPART)
    docker.chmod(0o755)
    env = {
        key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ
    }
    env.update(
        {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "MOONMIND_PYTHON_TEST_IMAGE": "moonmind-python-tests:unit",
            "MOONMIND_TEST_COMPOSE_PROJECT_NAME": "moonmind-test-wrapper-unit",
            "WRAPPER_TEST_CONFIG": str(tmp_path / "config.json"),
            "WRAPPER_TEST_CALLS": str(tmp_path / "docker-calls.jsonl"),
            "WRAPPER_TEST_STARTED": str(tmp_path / "started"),
        }
    )
    return Wrapper(
        repo,
        tmp_path / "config.json",
        tmp_path / "docker-calls.jsonl",
        tmp_path / "started",
        env,
    )


@pytest.fixture
def wrapper(tmp_path: Path) -> Wrapper:
    return _make_wrapper(tmp_path, tmp_path / "checkout")


def test_integration_uses_available_resources_without_a_fixed_two_worker_limit(wrapper):
    status, _, _ = wrapper.run()
    assert status == 0
    assert "-n 4" in wrapper.pytest_command()


@pytest.mark.parametrize("workers", ["0", "-1", "auto", "4 --maxfail=0"])
def test_invalid_worker_override_fails_before_running_tests(wrapper, workers):
    wrapper.env["MOONMIND_INTEGRATION_WORKERS"] = workers
    status, output, _ = wrapper.run()
    assert status == 2
    assert "positive integer" in output
    assert not any(command[0] == "run" for command in wrapper.compose_commands())


def test_ordinary_ci_stops_early_and_keeps_report_log_and_failure(
    wrapper: Wrapper,
) -> None:
    wrapper.env["MOONMIND_INTEGRATION_FAIL_FAST"] = "1"

    status, output, _ = wrapper.run(run_status=1)

    assert status == 1
    assert "--maxfail=1" in wrapper.pytest_command().split()
    report = wrapper.repo / REPORT
    assert "failures='1'" in (report / "pytest-integration-ci.xml").read_text()
    log = (report / "pytest-integration-ci.log").read_text()
    assert "test_case FAILED" in log
    # Row-owned diagnostics come from this wrapper's own project before teardown.
    commands = wrapper.compose_commands()
    assert [command[0] for command in commands] == ["up", "port", "run", "ps", "logs", "down"]
    assert all(
        call[call.index("--project-name") + 1] == "moonmind-test-wrapper-unit"
        for call in wrapper.calls()
        if "--project-name" in call
    )
    assert "ps" in (report / "compose-ps.txt").read_text()
    assert "logs" in (report / "compose-logs.txt").read_text()
    assert "test_case FAILED" in output


def test_interactive_and_scheduled_runs_keep_collecting_without_maxfail(
    wrapper: Wrapper,
) -> None:
    status, _, _ = wrapper.run(run_status=0)

    assert status == 0
    assert "--maxfail" not in wrapper.pytest_command()
    assert "--junitxml=artifacts/integration-ci/pytest-integration-ci.xml" in (
        wrapper.pytest_command().split()
    )
    assert (wrapper.repo / REPORT / "pytest-integration-ci.xml").is_file()
    assert not (wrapper.repo / REPORT / "compose-logs.txt").exists()
    assert wrapper.downs() == 1
    commands = wrapper.compose_commands()
    assert [command[0] for command in commands] == [
        "up", "port", "run", "images", "run", "restart", "up", "run", "run", "down"
    ]
    assert "storage-restart-seed.xml" in commands[4][-1]
    assert "storage-restart-verify.xml" in commands[7][-1]
    assert "--volumes" in commands[-1]


@pytest.mark.parametrize("quality_status", [0, 6])
@pytest.mark.parametrize("temporary_checkout", [False, True])
def test_quality_validation_uses_the_producing_project_and_keeps_its_result(
    tmp_path, quality_status, temporary_checkout
):
    repo_name = "work:checkout" if temporary_checkout else "checkout"
    wrapper = _make_wrapper(tmp_path, tmp_path / repo_name)
    wrapper.env["MOONMIND_AGENT_RUNTIME_STORE"] = str(tmp_path / "agent_jobs")

    status, output, _ = wrapper.run(write_quality=True, quality_status=quality_status)

    assert status == quality_status
    commands = wrapper.compose_commands()
    quality_runs = [
        command
        for command in commands
        if command[:1] == ["run"]
        and command[-1] == "tests.integration.security.quality"
    ]
    assert len(quality_runs) == 1
    assert quality_runs[0][-4:] == [
        "pytest", "python", "-m", "tests.integration.security.quality"
    ]
    assert len(
        {
            call[call.index("--project-directory") + 1]
            for call in wrapper.calls()
            if "--project-directory" in call
        }
    ) == 1
    quality = wrapper.repo / REPORT / "security-quality"
    assert (
        quality / "sample/native.txt"
    ).read_text() == "actual retained fixture observation"
    assert json.loads((quality / "summary.json").read_text()) == {
        "passed": quality_status == 0
    }
    assert "quality evidence checked" in output
    assert (wrapper.repo / REPORT / "compose-logs.txt").exists() == bool(quality_status)
    assert wrapper.downs() == 1


@pytest.mark.parametrize(
    "inherited_test_endpoint", [None, "", "http://installed-storage.invalid:9000"]
)
def test_public_downloads_use_the_disposable_published_port(
    wrapper: Wrapper, inherited_test_endpoint: str | None
) -> None:
    wrapper.env.update(
        {
            "TEMPORAL_ARTIFACT_S3_ENDPOINT": "http://installed-storage.invalid:9000",
            "TEMPORAL_ARTIFACT_S3_PUBLIC_ENDPOINT": "http://installed-storage.invalid:9000",
        }
    )
    if inherited_test_endpoint is not None:
        wrapper.env["MOONMIND_TEST_S3_PUBLIC_ENDPOINT"] = inherited_test_endpoint

    status, output, _ = wrapper.run()

    assert status == 0
    assert "public storage endpoint: http://host.docker.internal:43127" in output
    assert "installed-storage.invalid" not in output
    commands = wrapper.compose_commands()
    assert commands[0] == ["up", "-d", "--wait", "--wait-timeout", "90", "minio", "artifact-source"]
    assert commands[1] == ["port", "minio", "9000"]


@pytest.mark.parametrize("binding", ["", "0.0.0.0:0", "0.0.0.0:65536", "invalid"])
def test_missing_public_port_prevents_tests_from_using_an_unrelated_store(wrapper: Wrapper, binding: str) -> None:
    status, output, _ = wrapper.run(public_binding=binding)

    assert status == 2
    assert "disposable storage public port" in output
    assert not any(command[0] == "run" for command in wrapper.compose_commands())
    assert wrapper.downs() == 1


def test_stale_report_never_stands_in_for_a_missing_one(wrapper: Wrapper) -> None:
    report = wrapper.repo / REPORT / "pytest-integration-ci.xml"
    report.parent.mkdir(parents=True)
    report.write_text("<testsuite failures='0'/>")

    status, _, _ = wrapper.run(run_status=3, write_junit=False)

    assert status == 3
    assert not report.exists()


def test_failed_storage_restart_keeps_reports_and_stops_without_repeating_main(wrapper: Wrapper) -> None:
    status, _, _ = wrapper.run(restart_status=5)
    assert status == 5
    report = wrapper.repo / REPORT
    assert (report / "pytest-integration-ci.xml").exists()
    assert (report / "storage-restart-seed.xml").exists()
    assert not (report / "storage-restart-verify.xml").exists()
    assert (report / "compose-logs.txt").exists()
    assert len([command for command in wrapper.compose_commands() if command[0] == "run"]) == 2
    assert wrapper.downs() == 1


def test_temporary_checkout_exports_report_before_removal(tmp_path: Path) -> None:
    agent_jobs = tmp_path / "agent_jobs"
    wrapper = _make_wrapper(tmp_path, tmp_path / "work:checkout")
    wrapper.env["MOONMIND_AGENT_RUNTIME_STORE"] = str(agent_jobs)

    status, _, _ = wrapper.run(run_status=1)

    assert status == 1
    directories = {
        call[call.index("--project-directory") + 1]
        for call in wrapper.calls()
        if "--project-directory" in call
    }
    assert len(directories) == 1
    temporary = Path(directories.pop())
    assert temporary.parent == agent_jobs
    assert not temporary.exists()
    report = wrapper.repo / REPORT
    assert "failures='1'" in (report / "pytest-integration-ci.xml").read_text()
    assert "test_case FAILED" in (report / "pytest-integration-ci.log").read_text()


def test_temporary_checkout_retains_native_quality_evidence_on_failure(tmp_path):
    wrapper = _make_wrapper(tmp_path, tmp_path / "work:checkout")
    wrapper.env["MOONMIND_AGENT_RUNTIME_STORE"] = str(tmp_path / "agent_jobs")
    status, _, _ = wrapper.run(run_status=1, write_quality=True)
    assert status == 1
    assert (
        wrapper.repo / REPORT / "security-quality/sample/native.txt"
    ).read_text() == "actual retained fixture observation"


def test_failed_quality_export_keeps_the_only_recoverable_copy(tmp_path):
    wrapper = _make_wrapper(tmp_path, tmp_path / "work:checkout")
    wrapper.env["MOONMIND_AGENT_RUNTIME_STORE"] = str(tmp_path / "agent_jobs")
    copier = tmp_path / "bin/cp"
    copier.write_text(
        '#!/bin/sh\nif [ "$1" = "-R" ]; then exit 1; fi\n'
        f'exec "{shutil.which("cp")}" "$@"\n'
    )
    copier.chmod(0o755)
    status, output, _ = wrapper.run(run_status=1, write_quality=True)
    assert status == 1
    directory = Path(next(call[call.index("--project-directory") + 1] for call in wrapper.calls() if "--project-directory" in call))
    assert (directory / REPORT / "security-quality/sample/native.txt").is_file()
    assert str(directory) in output


def test_stuck_teardown_is_bounded_and_keeps_primary_status(wrapper: Wrapper) -> None:
    wrapper.env["MOONMIND_TEST_CLEANUP_TIMEOUT"] = "1"

    status, _, elapsed = wrapper.run(
        run_status=4, logs_sleep=120, ps_sleep=120, down_sleep=120
    )

    assert status == 4
    assert elapsed < 30
    assert wrapper.downs() == 1
    assert (wrapper.repo / REPORT / "pytest-integration-ci.xml").is_file()


def test_execution_budget_interrupts_pytest_cooperatively(wrapper: Wrapper) -> None:
    # Leave time for dependency startup and public-port discovery, then
    # interrupt a running pytest process and preserve its terminal report.
    wrapper.env["MOONMIND_INTEGRATION_TIMEOUT"] = "3"

    status, _, elapsed = wrapper.run(run_sleep=120)

    assert status == 124
    assert elapsed < 30
    assert (
        "failures='1'"
        in (wrapper.repo / REPORT / "pytest-integration-ci.xml").read_text()
    )
    assert wrapper.downs() == 1


def test_public_port_discovery_shares_the_pipeline_deadline(wrapper: Wrapper) -> None:
    wrapper.env["MOONMIND_INTEGRATION_TIMEOUT"] = "2"

    status, _, elapsed = wrapper.run(public_port_sleep=120)

    assert status == 124
    assert elapsed < 30
    assert not any(command[0] == "run" for command in wrapper.compose_commands())
    assert (wrapper.repo / REPORT / "compose-logs.txt").exists()
    assert wrapper.downs() == 1


def test_termination_runs_bounded_cleanup_and_reports_the_signal(
    wrapper: Wrapper,
) -> None:
    wrapper.env["MOONMIND_TEST_CLEANUP_TIMEOUT"] = "1"
    process = wrapper.start(run_sleep=120, down_sleep=120)
    deadline = time.monotonic() + 20
    while not wrapper.started.exists():
        assert time.monotonic() < deadline, "fake pytest never started"
        time.sleep(0.05)

    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)

    assert process.returncode == 143
    assert wrapper.downs() == 1
    assert (
        "test_case FAILED"
        in (wrapper.repo / REPORT / "pytest-integration-ci.log").read_text()
    )


def test_incompatible_timeout_fails_before_creating_resources(wrapper: Wrapper) -> None:
    binaries = Path(wrapper.env["PATH"].split(os.pathsep)[0])
    for name in ("gnutimeout", "timeout", "gtimeout"):
        candidate = binaries / name
        candidate.write_text('#!/bin/bash\necho "timeout (uutils coreutils) 0.10.0"\n')
        candidate.chmod(0o755)
    status, output, _ = wrapper.run()
    assert status == 125
    assert "GNU coreutils timeout is required" in output
    assert not wrapper.calls_path.exists()


@pytest.mark.parametrize("budget", ["0", "2"])
def test_missing_timeout_preserves_only_unbounded_host_mode(wrapper: Wrapper, budget) -> None:
    import shutil

    binaries = Path(wrapper.env["PATH"].split(os.pathsep)[0])
    for name in ("bash", "dirname", "cp", "mkdir", "rm", "tee", "cat", "python3"):
        (binaries / name).symlink_to(shutil.which(name))
    wrapper.env["PATH"] = str(binaries)
    wrapper.env["MOONMIND_INTEGRATION_TIMEOUT"] = budget
    status, output, _ = wrapper.run()
    if budget == "0":
        assert status == 0, output
        assert wrapper.downs() == 1
    else:
        assert status == 125
        assert "GNU coreutils timeout is required" in output
        assert not wrapper.calls_path.exists()

@pytest.mark.parametrize("shard", ["0", "3", "1 --maxfail=0", "all"])
def test_invalid_hermetic_shard_rejected_before_resources(wrapper, shard):
    wrapper.env["MOONMIND_HERMETIC_SHARD"] = shard
    status, output, _ = wrapper.run()
    assert status == 2
    assert "MOONMIND_HERMETIC_SHARD" in output
    assert not wrapper.calls_path.exists()


@pytest.mark.parametrize("shard", ["1", "2"])
def test_physical_shards_isolate_projects_reports_and_phase_ownership(wrapper, shard):
    wrapper.env["MOONMIND_HERMETIC_SHARD"] = shard
    status, output, _ = wrapper.run()
    assert status == 0
    assert f"owned report root: artifacts/integration-ci-hermetic-shard-{shard}" in output
    command = wrapper.pytest_command()
    assert f"--splits 2 --group {shard} --splitting-algorithm least_duration" in command
    report = wrapper.repo / f"artifacts/integration-ci-hermetic-shard-{shard}"
    assert (report / "pytest-integration-ci.xml").is_file()
    assert (report / "security-quality/sample/native.txt").is_file()
    assert not (wrapper.repo / REPORT).exists()
    assert all(
        call[call.index("--project-name") + 1] == f"moonmind-test-wrapper-unit-hermetic-{shard}"
        for call in wrapper.calls() if "--project-name" in call
    )
    commands = wrapper.compose_commands()
    assert not any(command[-1] == "tests.integration.security.quality" for command in commands)
    assert any(command[0] == "restart" for command in commands) == (shard == "1")
    assert (report / "storage-restart-seed.xml").exists() == (shard == "1")
    assert (report / "storage-restart-verify.xml").exists() == (shard == "1")
    assert wrapper.downs() == 1


def test_concurrent_shards_keep_failure_and_cancellation_cleanup_separate(wrapper, tmp_path):
    first = wrapper
    first.env["MOONMIND_HERMETIC_SHARD"] = "1"
    second_env = dict(first.env, MOONMIND_HERMETIC_SHARD="2")
    second = Wrapper(
        first.repo, tmp_path / "second-config.json", tmp_path / "second-calls.jsonl",
        tmp_path / "second-started", second_env,
    )
    second.env.update(
        WRAPPER_TEST_CONFIG=str(second.config), WRAPPER_TEST_CALLS=str(second.calls_path),
        WRAPPER_TEST_STARTED=str(second.started),
    )
    cancelled = second.start(run_sleep=120)
    deadline = time.monotonic() + 20
    try:
        while not second.started.exists():
            assert time.monotonic() < deadline, "second shard never started"
            time.sleep(0.05)
        status, _, _ = first.run(run_status=7)
        assert status == 7
        assert cancelled.poll() is None
        cancelled.send_signal(signal.SIGTERM)
        cancelled.communicate(timeout=30)
        assert cancelled.returncode == 143
    finally:
        if cancelled.poll() is None:
            cancelled.kill()
            cancelled.communicate(timeout=10)
    for shard, owner in ((1, first), (2, second)):
        assert owner.downs() == 1
        assert all(
            call[call.index("--project-name") + 1] == f"moonmind-test-wrapper-unit-hermetic-{shard}"
            for call in owner.calls() if "--project-name" in call
        )
        report = wrapper.repo / f"artifacts/integration-ci-hermetic-shard-{shard}"
        assert "test_case FAILED" in (report / "pytest-integration-ci.log").read_text()
    assert "failures='1'" in (
        wrapper.repo / "artifacts/integration-ci-hermetic-shard-1/pytest-integration-ci.xml"
    ).read_text()


def test_shard_temporary_checkout_retains_partial_evidence_on_failure(tmp_path):
    wrapper = _make_wrapper(tmp_path, tmp_path / "work:checkout")
    wrapper.env.update(
        MOONMIND_HERMETIC_SHARD="2",
        MOONMIND_AGENT_RUNTIME_STORE=str(tmp_path / "agent_jobs"),
    )
    status, _, _ = wrapper.run(run_status=1)
    assert status == 1
    report = wrapper.repo / "artifacts/integration-ci-hermetic-shard-2"
    assert (report / "pytest-integration-ci.xml").exists()
    assert (report / "security-quality/sample/native.txt").is_file()
    assert not any(command[0] == "restart" for command in wrapper.compose_commands())


def test_shard_run_clears_only_its_stale_receipts_and_security_evidence(wrapper):
    wrapper.env["MOONMIND_HERMETIC_SHARD"] = "2"
    own = wrapper.repo / "artifacts/integration-ci-hermetic-shard-2"
    sibling = wrapper.repo / "artifacts/integration-ci-hermetic-shard-1"
    for directory in (own, sibling):
        (directory / "security-quality/old-case").mkdir(parents=True)
        (directory / "security-quality/old-case/result.json").write_text("stale")
        (directory / "main.receipt.json").write_text("stale")
        (directory / "main.environment.json").write_text("stale")
        (directory / "qualification.json").write_text("current independent phase")
    remover = Path(wrapper.env["PATH"].split(os.pathsep)[0]) / "rm"
    remover.write_text(
        '#!/bin/sh\ncase "$*" in *security-quality*) echo "Permission denied" >&2; exit 1;; esac\n'
        f'exec "{shutil.which("rm")}" "$@"\n'
    )
    remover.chmod(0o755)
    status, _, _ = wrapper.run(run_status=7, write_junit=False, write_quality=False)
    assert status == 7
    assert not (own / "security-quality").exists()
    assert not (own / "main.receipt.json").exists()
    assert not (own / "main.environment.json").exists()
    assert (own / "qualification.json").exists()
    assert (sibling / "security-quality/old-case/result.json").read_text() == "stale"
    assert (sibling / "main.receipt.json").read_text() == "stale"
    assert (sibling / "main.environment.json").read_text() == "stale"


@pytest.mark.parametrize("report_path", [None, "artifacts/integration-ci-hermetic-shard-1"])
def test_storage_restart_state_uses_owned_report_directory(tmp_path, monkeypatch, report_path):
    from tests.integration.temporal.test_storage_restart import state_dir

    monkeypatch.chdir(tmp_path)
    if report_path is None:
        monkeypatch.delenv("MOONMIND_INTEGRATION_REPORT_PATH", raising=False)
    else:
        monkeypatch.setenv("MOONMIND_INTEGRATION_REPORT_PATH", report_path)
    expected = Path(report_path or "artifacts/integration-ci") / "storage-restart"
    assert state_dir() == expected
    assert expected.is_dir()


@pytest.mark.parametrize("enabled", [False, True])
def test_shard_receipt_is_opt_in(wrapper, enabled):
    wrapper.env["MOONMIND_HERMETIC_SHARD"] = "2"
    if enabled:
        wrapper.env.update(MOONMIND_CI_RECEIPT="artifacts/original.receipt.json", MOONMIND_CI_EXPECTED_PYTHON="3.12")
    status, output, _ = wrapper.run()
    assert status == 0
    expected = "artifacts/integration-ci-hermetic-shard-2/main.receipt.json" if enabled else ""
    assert f"receipt path: {expected}\n" in output


def test_failed_container_owned_evidence_cleanup_is_bounded_and_diagnosed(wrapper):
    quality = wrapper.repo / REPORT / "security-quality"
    quality.mkdir(parents=True)
    (quality / "result.json").write_text("retained prior evidence")
    wrapper.env["MOONMIND_INTEGRATION_TIMEOUT"] = "2"
    status, _, elapsed = wrapper.run(evidence_cleanup_sleep=120)
    assert status == 124
    assert elapsed < 30
    assert (quality / "result.json").exists()
    assert (wrapper.repo / REPORT / "compose-logs.txt").exists()
    assert not wrapper.started.exists()
    assert wrapper.downs() == 1


def _phase_timings(wrapper):
    return [json.loads(line) for line in (wrapper.repo / REPORT / "phase-timings.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("status", [0, 7])
def test_wrapper_retains_actual_phase_timings_and_primary_status(wrapper, status):
    wrapper.env.update(MOONMIND_CI_REVISION="revision", GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2")
    actual, _, _ = wrapper.run(run_status=status)
    assert actual == status
    rows = _phase_timings(wrapper)
    names = [row["phase"] for row in rows]
    assert names[:3] == ["storage-up", "storage-port", "main-pytest"]
    assert names[-1] == "compose-down"
    assert ("storage-seed" in names) == (status == 0)
    if status == 0:
        assert names == [
            "storage-up", "storage-port", "main-pytest", "storage-images",
            "storage-seed", "storage-restart", "storage-health", "storage-verify",
            "quality-summary", "compose-down",
        ]
    for row in rows:
        assert row["revision"] == "revision" and row["runId"] == "123" and row["attempt"] == "2"
        assert row["endMonotonic"] >= row["startMonotonic"]
        assert row["elapsedSeconds"] == pytest.approx(row["endMonotonic"] - row["startMonotonic"])
        assert "command" not in row
    assert next(row for row in rows if row["phase"] == "main-pytest")["exitCode"] == status


def test_cancelled_phase_and_teardown_timings_survive(wrapper):
    process = wrapper.start(run_sleep=120)
    deadline = time.monotonic() + 20
    while not wrapper.started.exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    process.send_signal(signal.SIGTERM)
    process.communicate(timeout=30)
    assert process.returncode == 143
    rows = _phase_timings(wrapper)
    assert [(row["phase"], row["exitCode"]) for row in rows][-2:] == [("main-pytest", 143), ("compose-down", 0)]


def test_timing_failure_does_not_replace_primary_failure(wrapper):
    helper = wrapper.repo / "tools/ci/write_backend_matrix_summary.py"
    helper.write_text("raise SystemExit(1)\n")
    status, output, _ = wrapper.run(run_status=7)
    assert status == 7
    assert "warning: integration phase clock unavailable" in output
    assert wrapper.downs() == 1


def test_phase_timings_survive_colon_checkout_export(tmp_path):
    wrapper = _make_wrapper(tmp_path, tmp_path / "work:checkout")
    wrapper.env["MOONMIND_AGENT_RUNTIME_STORE"] = str(tmp_path / "agent_jobs")
    status, _, _ = wrapper.run(run_status=7)
    assert status == 7
    assert [row["phase"] for row in _phase_timings(wrapper)] == [
        "storage-up", "storage-port", "main-pytest", "compose-down"
    ]
