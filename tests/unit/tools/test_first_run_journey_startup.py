"""Run the real journey shell against disposable external counterparts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "tools/first_run_journey_3938.sh"
CREDENTIAL_KEYS = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENCODE_API_KEY",
    "GITHUB_PAT",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)
SECRETS = (
    "sk-startup_test_secret",
    "ghp_startup_test_secret",
    "github_pat_startup_test_secret",
    "key=startup_test_secret",
)

DOCKER_COUNTERPART = r"""
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
if args == ["compose", "version"]:
    print("Docker Compose test counterpart")
    raise SystemExit(0)
config = json.loads(Path(os.environ["STARTUP_TEST_CONFIG"]).read_text())
calls = Path(os.environ["STARTUP_TEST_DOCKER_CALLS"])
image = os.environ.get("MOONMIND_IMAGE", "")
phase = "source" if image == "test-source" else "candidate"
footprint = config.get("footprint", {}).get(phase, {})
if args[:1] != ["compose"]:
    with calls.with_suffix(".host.jsonl").open("a") as output:
        output.write(json.dumps(args) + "\n")
    print(footprint.get(args[0], ""))
    raise SystemExit(footprint.get("host_status", 0))
previous = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
directory = Path(args[args.index("--project-directory") + 1])
command = args[args.index("--project-directory") + 2:]
startup = command[:1] == ["up"] and "600" in command
attempt = 1 + sum(item["startup"] and item["phase"] == phase for item in previous)
statuses = config.get(phase + "_statuses", [0])
status = statuses[min(attempt - 1, len(statuses) - 1)] if startup else 0
entry = {
    "command": command, "project": args[args.index("--project-name") + 1],
    "directory": str(directory), "phase": phase, "startup": startup,
    "attempt": attempt, "status": status,
    "env_exists": (Path(os.environ["STARTUP_TEST_REPO"]) / ".env").exists(),
    "credentials": [key for key in config["credential_keys"] if key in os.environ],
}
with calls.open("a") as output:
    output.write(json.dumps(entry) + "\n")
if startup:
    for index in range(config.get("lines", 80)):
        print(f"startup-line-{index:05d} sk-startup_test_secret ghp_startup_test_secret github_pat_startup_test_secret key=startup_test_secret",
              file=sys.stderr if index % 2 else sys.stdout, flush=True)
        if index == 0 and config.get("separate_receipts"):
            log = Path(os.environ["FIRST_RUN_3938_LOG_DIR"]) / "fresh/compose-startup-candidate-1.log"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if log.is_file() and "startup-line-00000" in log.read_text():
                    entry["early_receipt_observed"] = True
                    break
                time.sleep(0.01)
            calls.write_text("".join(json.dumps(item) + "\n" for item in [*previous, entry]))
            time.sleep(0.04)
    if config.get("summary_failure"):
        log = Path(os.environ["FIRST_RUN_3938_LOG_DIR"]) / "upgrade" / f"compose-startup-source-{attempt}.log"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if log.is_file() and log.stat().st_size:
                log.rename(log.with_suffix(".recorded"))
                log.symlink_to("/dev/full")
                break
            time.sleep(0.01)
    raise SystemExit(status)
if "config" in command[:3]:
    print(json.dumps(config.get("compose_config", {"services": {"minio": {"image": "test-minio"}}})))
elif command[:1] == ["top"]:
    print(footprint.get("top", ""))
    if footprint.get("top_stderr"):
        print(footprint["top_stderr"], file=sys.stderr)
    raise SystemExit(footprint.get("top_status", 0))
elif command[:1] == ["logs"]:
    print("cleanup sk-startup_test_secret ghp_startup_test_secret github_pat_startup_test_secret key=startup_test_secret")
else:
    print("external Docker observation")
"""

API_COUNTERPART = r"""
import json, os, sys
from pathlib import Path

call = Path(os.environ["STARTUP_TEST_API_CALLS"])
with call.open("a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\n")
state = Path(sys.argv[sys.argv.index("--state-file") + 1])
state.parent.mkdir(parents=True, exist_ok=True)
state.write_text("{}")
"""


def _executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n" + source)
    path.chmod(0o755)


@dataclass
class Journey:
    repo: Path
    logs: Path
    config: Path
    docker_calls: Path
    api_calls: Path
    env: dict[str, str]
    source_revision: str

    def run(
        self, *, upgrade: bool = False, **config: object
    ) -> subprocess.CompletedProcess:
        self.config.write_text(
            json.dumps({"credential_keys": CREDENTIAL_KEYS, **config})
        )
        return subprocess.run(
            [
                "bash",
                str(self.repo / "tools/first_run_journey_3938.sh"),
                *(["--upgrade"] if upgrade else []),
            ],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.docker_calls.read_text().splitlines()]

    def startups(self) -> list[dict]:
        return [call for call in self.calls() if call["startup"]]

    def assert_cleaned(self) -> None:
        calls = self.calls()
        assert sum(call["command"][:1] == ["down"] for call in calls) == 1
        assert all(call["project"] == "moonmind-test-startup-unit" for call in calls)
        assert all(not call["env_exists"] and not call["credentials"] for call in calls)
        assert (self.repo / ".env").read_text() == "CALLER_ENV=must-survive\n"
        for call in calls:
            if call["directory"] != str(self.repo):
                assert not Path(call["directory"]).exists()


@pytest.fixture
def journey(tmp_path: Path) -> Journey:
    repo = tmp_path / "checkout"
    tools = repo / "tools"
    tools.mkdir(parents=True)
    (tools / SCRIPT.name).write_text(SCRIPT.read_text())
    (tools / "single_user_journey_checks.py").write_text(API_COUNTERPART)
    (tools / "single_user_journey_browser.mjs").write_text(
        "// External browser counterpart.\n"
    )
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "fixture-release").write_text("source\n")
    for command in (
        ["git", "init", "--quiet", str(repo)],
        ["git", "-C", str(repo), "config", "user.name", "Journey fixture"],
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        ["git", "-C", str(repo), "add", "."],
        ["git", "-C", str(repo), "commit", "--quiet", "-m", "Source fixture"],
    ):
        subprocess.run(command, check=True, capture_output=True)
    source_revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    (repo / "fixture-release").write_text("candidate\n")
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--quiet", "-am", "Candidate fixture"],
        check=True,
        capture_output=True,
    )
    (repo / ".env").write_text("CALLER_ENV=must-survive\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    config = tmp_path / "external-config.json"
    docker_calls = tmp_path / "docker-calls.jsonl"
    api_calls = tmp_path / "api-calls.jsonl"
    _executable(binaries / "docker", DOCKER_COUNTERPART)
    _executable(
        binaries / "curl",
        "import json, os\nfrom pathlib import Path\n"
        "config = json.loads(Path(os.environ['STARTUP_TEST_CONFIG']).read_text())\n"
        "print(json.dumps({'status': 'ok', 'db': 'connected', "
        "'migration_required': False, 'setup_required': False, **config.get('health', {})}))\n",
    )
    _executable(binaries / "node", "raise SystemExit(0)\n")
    env = {
        key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ
    }
    env.update(
        {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "TMPDIR": str(tmp_path),
            "MOONMIND_TEST_COMPOSE_PROJECT_NAME": "moonmind-test-startup-unit",
            "MOONMIND_IMAGE": "test-candidate",
            "FIRST_RUN_3938_UPGRADE_FROM": "test-source",
            "FIRST_RUN_3938_UPGRADE_FROM_REVISION": source_revision,
            "FIRST_RUN_3938_LOG_DIR": str(tmp_path / "logs"),
            "STARTUP_TEST_REPO": str(repo),
            "STARTUP_TEST_CONFIG": str(config),
            "STARTUP_TEST_DOCKER_CALLS": str(docker_calls),
            "STARTUP_TEST_API_CALLS": str(api_calls),
            **{key: "synthetic-inherited-credential" for key in CREDENTIAL_KEYS},
        }
    )
    return Journey(
        repo, tmp_path / "logs", config, docker_calls, api_calls, env, source_revision
    )


def _startup_log(journey: Journey, mode: str, phase: str, attempt: int) -> str:
    path = journey.logs / mode / f"compose-startup-{phase}-{attempt}.log"
    assert path.is_file(), "Compose startup output must be retained as an attempt log"
    return path.read_text()


def _assert_redacted(text: str) -> None:
    assert all(secret not in text for secret in SECRETS)
    assert "***" in text


def test_failed_startup_retains_early_redacted_receipts_and_original_status(
    journey: Journey,
) -> None:
    result = journey.run(candidate_statuses=[7], separate_receipts=True)
    assert result.returncode != 0
    assert len(journey.startups()) == 1
    journey.assert_cleaned()
    output = _startup_log(journey, "fresh", "candidate", 1)
    assert journey.startups()[0].get(
        "early_receipt_observed"
    ), "Receipt must be logged before Compose exits"
    assert "startup-line-00000" in output and "startup-line-00079" in output
    _assert_redacted(output + result.stdout + result.stderr)
    receipts = [
        datetime.fromisoformat(line.split(" ", 1)[0])
        for line in output.splitlines()
        if "startup-line-" in line
    ]
    assert all(receipt.utcoffset().total_seconds() == 0 for receipt in receipts)
    assert (receipts[-1] - receipts[0]).total_seconds() >= 0.03
    assert re.search(
        r"compose-startup phase=candidate attempt=1 elapsed_seconds=\d+ exit_status=7",
        output,
    )
    assert "startup-line-00000" not in result.stdout
    assert "startup-line-00079" in result.stdout


@pytest.mark.parametrize("line_count", [40, 5005, 5100])
def test_startup_stream_retention_is_bounded_without_tail_overlap(
    journey: Journey, line_count: int
) -> None:
    result = journey.run(candidate_statuses=[7], lines=line_count)
    assert result.returncode != 0
    output = _startup_log(journey, "fresh", "candidate", 1)
    retained = [int(value) for value in re.findall(r"startup-line-(\d+)", output)]
    expected = list(range(min(line_count, 5000)))
    expected += list(range(max(5000, line_count - 40), line_count))
    assert retained == expected
    assert len(retained) == len(set(retained))
    if line_count > 5040:
        assert f"{line_count - 5040} omitted between first 5000 and final 40" in output
    else:
        assert "startup output truncated" not in output
    _assert_redacted(output + result.stdout + result.stderr)
    journey.assert_cleaned()


def test_upgrade_source_retries_three_times_and_candidate_once(
    journey: Journey,
) -> None:
    result = journey.run(
        upgrade=True, source_statuses=[7, 8, 0], candidate_statuses=[9], lines=2
    )
    assert result.returncode != 0
    startups = journey.startups()
    assert [(call["phase"], call["status"]) for call in startups] == [
        ("source", 7),
        ("source", 8),
        ("source", 0),
        ("candidate", 9),
    ]
    for phase, attempt, status in (
        ("source", 1, 7),
        ("source", 2, 8),
        ("source", 3, 0),
        ("candidate", 1, 9),
    ):
        output = _startup_log(journey, "upgrade", phase, attempt)
        assert f"phase={phase} attempt={attempt}" in output
        assert f"exit_status={status}" in output
    journey.assert_cleaned()


def test_upgrade_source_failure_stops_at_three_attempts(journey: Journey) -> None:
    result = journey.run(upgrade=True, source_statuses=[7], lines=2)
    assert result.returncode != 0
    assert [(call["phase"], call["status"]) for call in journey.startups()] == [
        ("source", 7)
    ] * 3
    assert not journey.api_calls.exists()
    journey.assert_cleaned()


@pytest.mark.parametrize("failure", ["open", "write", "summary"])
def test_logging_failure_never_retries_successful_compose(
    journey: Journey, failure: str
) -> None:
    log = journey.logs / "upgrade/compose-startup-source-1.log"
    log.parent.mkdir(parents=True)
    if failure == "open":
        log.mkdir()
    elif failure == "write":
        log.symlink_to("/dev/full")
    result = journey.run(
        upgrade=True,
        source_statuses=[0],
        candidate_statuses=[0],
        lines=0 if failure == "open" else 1,
        summary_failure=failure == "summary",
    )
    assert result.returncode != 0
    assert [(call["phase"], call["status"]) for call in journey.startups()] == [
        ("source", 0)
    ]
    assert "startup diagnostics" in result.stderr
    assert not journey.api_calls.exists()
    journey.assert_cleaned()


@pytest.mark.parametrize("problem", ["migration_required", "setup_required"])
def test_healthy_compose_does_not_hide_api_readiness_failure(
    journey: Journey, problem: str
) -> None:
    result = journey.run(candidate_statuses=[0], lines=2, health={problem: True})
    assert result.returncode != 0
    assert problem in result.stderr
    assert not journey.api_calls.exists()
    journey.assert_cleaned()


def test_successful_startup_preserves_complete_fresh_journey_and_cleanup(
    journey: Journey,
) -> None:
    result = journey.run(candidate_statuses=[0], lines=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(journey.startups()) == 1
    calls = [json.loads(line)[0] for line in journey.api_calls.read_text().splitlines()]
    assert calls == [
        "populate",
        "vector_free",
        "canceled",
        "credential",
        "verify",
        "release",
    ]
    assert any(call["command"][:1] == ["restart"] for call in journey.calls())
    output = _startup_log(journey, "fresh", "candidate", 1)
    assert "phase=candidate attempt=1" in output and "exit_status=0" in output
    journey.assert_cleaned()


COMPOSE_MODEL = {
    "services": {
        "minio": {"image": "test-minio"},
        "api": {"depends_on": {"init-db": {"condition": "service_completed_successfully"}}},
        "init-db": {"restart": "no"},
        "temporal-worker-workflow": {},
        "temporal-ui": {"profiles": ["temporal-ui"]},
        "omnigent-host-claude": {"profiles": ["omnigent-host-claude"]},
    }
}


def _top(workflow_rows: list[str], top_format: str) -> str:
    if top_format == "table":
        # Current Docker Compose v2 prints one table for every selected service.
        return "\n".join(
            [
                "SERVICE                    #   UID    PID    PPID   C    STIME   TTY   TIME       CMD",
                *(f"temporal-worker-workflow   1   {row}" for row in workflow_rows),
            ]
        )
    return "\n".join(
        [
            "moonmind-test-startup-unit-temporal-worker-workflow-1",
            "UID    PID    PPID   C    STIME   TTY   TIME       CMD",
            *workflow_rows,
        ]
    )


def _footprint(
    workflow_rows: list[str], workflow_mib: int, top_format: str = "table"
) -> dict[str, str]:
    stats = [
        {"ID": "a1", "Name": "api-1", "CPUPerc": "1.0%", "MemUsage": "300MiB / 7.7GiB", "PIDs": "20"},
        {"ID": "w1", "Name": "workflow-1", "CPUPerc": "2.0%", "MemUsage": f"{workflow_mib}MiB / 7.7GiB", "PIDs": "40"},
        {"ID": "h1", "Name": "host-1", "CPUPerc": "0.5%", "MemUsage": "1GiB / 7.7GiB", "PIDs": "5"},
    ]
    return {
        "ps": "a1\tapi\nw1\ttemporal-worker-workflow\nh1\tomnigent-host-claude",
        "stats": "\n".join(json.dumps(row) for row in stats),
        "top": _top(workflow_rows, top_format),
    }


@pytest.mark.parametrize("top_format", ["table", "per-container"])
def test_upgrade_records_before_and_after_resource_footprint(
    journey: Journey, top_format: str
) -> None:
    init = "root 1 0 0 10:00 ? 00:00:00 /sbin/docker-init -- python"
    supervisor = [
        init,
        "app 7 1 1 10:00 ? 00:00:01 python start-workflow-worker-group.py key=startup_test_secret",
        "app 9 7 9 10:00 ? 00:00:05 python -m moonmind.workflows.temporal.worker_runtime",
        "app 10 7 9 10:00 ? 00:00:05 python -m moonmind.workflows.temporal.worker_runtime",
    ]
    single = [init, "app 7 1 9 10:00 ? 00:00:05 python -m moonmind.workflows.temporal.worker_runtime"]
    result = journey.run(
        upgrade=True,
        source_statuses=[0],
        candidate_statuses=[0],
        lines=1,
        compose_config=COMPOSE_MODEL,
        footprint={
            "source": _footprint(supervisor, 600, top_format),
            "candidate": _footprint(single, 250, top_format),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source = (journey.logs / "upgrade/resource-footprint-source.log").read_text()
    candidate = (journey.logs / "upgrade/resource-footprint-candidate.log").read_text()
    for text in (source, candidate):
        assert "long-lived: api minio temporal-worker-workflow" in text
        assert "one-shot init: init-db" in text
        assert "optional profiles (not running): temporal-ui[temporal-ui]" in text
        assert "on-demand (profile service running): omnigent-host-claude[omnigent-host-claude]" in text
        assert "temporal-worker-workflow" in text and "docker-init" in text
        assert "startup_test_secret" not in text
    assert (
        "footprint phase=source running_containers=3 memory_mib=1924 "
        "workflow_worker_processes=4 workflow_worker_python_processes=3 "
        "workflow_worker_memory_mib=600"
    ) in source
    assert (
        "footprint phase=candidate running_containers=3 memory_mib=1574 "
        "workflow_worker_processes=2 workflow_worker_python_processes=1 "
        "workflow_worker_memory_mib=250"
    ) in candidate
    host_calls = [
        json.loads(line)
        for line in journey.docker_calls.with_suffix(".host.jsonl").read_text().splitlines()
    ]
    assert all(
        "label=com.docker.compose.project=moonmind-test-startup-unit" in call
        for call in host_calls
        if call[0] == "ps"
    )
    journey.assert_cleaned()


def test_unavailable_resource_footprint_never_fails_the_journey(journey: Journey) -> None:
    result = journey.run(
        candidate_statuses=[0],
        lines=1,
        footprint={"candidate": {"host_status": 1, "stats": "not json"}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    footprint = (journey.logs / "fresh/resource-footprint-candidate.log").read_text()
    assert "footprint phase=candidate unavailable" in footprint
    journey.assert_cleaned()


@pytest.mark.parametrize(
    ("top", "status", "stderr"),
    [
        ("unexpected compose top output", 0, ""),
        ("", 1, "service temporal-worker-workflow is not running"),
    ],
)
def test_unobserved_worker_processes_are_unavailable_not_zero(
    journey: Journey, top: str, status: int, stderr: str
) -> None:
    footprint = _footprint([], 250) | {"top": top, "top_status": status, "top_stderr": stderr}
    result = journey.run(
        candidate_statuses=[0], lines=1, compose_config=COMPOSE_MODEL,
        footprint={"candidate": footprint},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    text = (journey.logs / "fresh/resource-footprint-candidate.log").read_text()
    assert (
        "footprint phase=candidate running_containers=3 memory_mib=1574 "
        "workflow_worker_processes=unavailable workflow_worker_python_processes=unavailable "
        "workflow_worker_memory_mib=250"
    ) in text
    assert f"compose top exit_status={status}" in text
    if stderr:
        assert stderr in text
