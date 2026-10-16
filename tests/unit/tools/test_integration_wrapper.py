"""Run the real hermetic integration wrapper against a disposable Docker CLI."""

from __future__ import annotations

import json
import os
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
if command[:1] == ["run"]:
    junit = re.search(r"--junitxml=(\S+)", command[-1])

    def report(status):
        if junit and config.get("write_junit", True):
            path = directory / junit.group(1)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"<testsuite failures='{int(status != 0)}'/>")
        raise SystemExit(status)

    signal.signal(signal.SIGINT, lambda *_: report(2))
    print("collected 3 items", flush=True)
    print("tests/integration/test_example.py::test_case FAILED", flush=True)
    Path(os.environ["WRAPPER_TEST_STARTED"]).write_text("started")
    time.sleep(config.get("run_sleep", 0))
    report(config.get("run_status", 0))
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
        runs = [command for command in self.compose_commands() if command[0] == "run"]
        assert len(runs) == 1
        return runs[0][-1]

    def downs(self) -> int:
        return sum(command[:1] == ["down"] for command in self.compose_commands())


def _make_wrapper(tmp_path: Path, repo: Path) -> Wrapper:
    tools = repo / "tools"
    tools.mkdir(parents=True)
    (tools / SCRIPT.name).write_text(SCRIPT.read_text())
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
    assert [command[0] for command in commands] == ["run", "ps", "logs", "down"]
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


def test_stale_report_never_stands_in_for_a_missing_one(wrapper: Wrapper) -> None:
    report = wrapper.repo / REPORT / "pytest-integration-ci.xml"
    report.parent.mkdir(parents=True)
    report.write_text("<testsuite failures='0'/>")

    status, _, _ = wrapper.run(run_status=3, write_junit=False)

    assert status == 3
    assert not report.exists()


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
    wrapper.env["MOONMIND_INTEGRATION_TIMEOUT"] = "1"

    status, _, elapsed = wrapper.run(run_sleep=120)

    assert status == 124
    assert elapsed < 30
    assert (
        "failures='1'"
        in (wrapper.repo / REPORT / "pytest-integration-ci.xml").read_text()
    )
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
