#!/usr/bin/env python3
"""Per-job pytest evidence summary for the backend matrix.

MoonLadderStudios/MoonMind#4370, #4371: each backend-matrix row streams its pytest
output through ``tee`` to a text log, keeps its JUnit report, and then calls
this small reporting hook to derive the slowest-test report, a per-shard
duration-hints snapshot, and a ``$GITHUB_STEP_SUMMARY`` section from standard
pytest/JUnit output. No new metrics service is introduced. Verification receipts use separate strict validation; an incomplete receipt fails its owning job.

Contract guarantees (checked by unit tests, not by live Actions runs):

- A missing JUnit file after process termination is reported as
  ``unavailable``/``interrupted`` -- never as zero tests or a passing suite.
- No progress-percentage parsing is used; counts come from the JUnit
  ``testsuite`` attributes only.
- An advisory timing parsing problem never fails the job: ``main()`` exits 0 and
  records the error in the summary instead.
- The committed partition input is never modified: duration hints are
  written only to the caller-supplied per-shard snapshot path, which must
  be a separate artifact file.

MoonLadderStudios/MoonMind#4629: the reliability rows also load this module
as a pytest plugin (``-p tools.ci.write_backend_matrix_summary``) so each
JUnit ``<testcase>`` records the actual pytest node ID (path, class and
parameter components) and the JUnit duration semantics. The per-shard
snapshot exports those exact IDs with the run/attempt/revision identity
that ``tools/ci/refresh_reliability_durations.py import`` checks before it
replaces the committed hints. Without the recorded property the snapshot
only offers the ``classname::name`` display guess and its ``nodeid`` is
null; malformed case times stay null instead of becoming a measured zero.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib import metadata
import math
import os
import platform
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


SLOWEST_LIMIT = 25
SUMMARY_SLOWEST_SHOWN = 10

# JUnit <testcase> properties recorded by the pytest_itemcollected hook below.
NODEID_PROPERTY = "pytest_nodeid"
DURATION_REPORT_PROPERTY = "junit_duration_report"
# The one documented cost meaning for observed durations: pytest's JUnit
# ``junit_duration_report = total``, i.e. setup + call + teardown of a case.
DURATION_SEMANTICS = "pytest-junit-total:setup+call+teardown"
SNAPSHOT_SCHEMA = "moonmind.reliability-durations-snapshot/v1"
RECEIPT_SCHEMA = "moonmind.pytest-verification/v1"
ENVIRONMENT_SCHEMA = "moonmind.pytest-environment/v1"
ENVIRONMENT_FIELDS = (
    "python",
    "dependencies",
    "platform",
    "environmentInputs",
    "dependencyInputs",
    "pytestEnvironment",
)


def environment_path(receipt: Path) -> Path:
    name = receipt.name.removesuffix(".receipt.json").removesuffix(".json")
    return receipt.with_name(name + ".environment.json")


def observed_environment(env=None) -> dict:
    env = os.environ if env is None else env
    root = Path(__file__).resolve().parents[2]
    return {
        "python": platform.python_version(),
        "dependencies": sorted(
            {(dist.metadata["Name"], dist.version) for dist in metadata.distributions()}
        ),
        "platform": platform.platform(),
        "environmentInputs": env.get(
            "MOONMIND_CI_ENVIRONMENT", "host-python:" + platform.python_version()
        ),
        "dependencyInputs": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("pyproject.toml", "poetry.lock")
        },
        "pytestEnvironment": {
            name: env.get(name, "")
            for name in (
                "PYTEST_ADDOPTS",
                "PYTEST_PLUGINS",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            )
        },
    }


def environment_fingerprint(environment: dict) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def record_environment(
    receipt: Path, *, expected_python: str, arguments: list[str], env=None
) -> None:
    env = os.environ if env is None else env
    if ".".join(platform.python_version_tuple()[:2]) != expected_python:
        raise ValueError(f"Verification environment requires Python {expected_python}")
    environment = observed_environment(env)
    payload = {
        "schema": ENVIRONMENT_SCHEMA,
        "suite": env.get("MOONMIND_CI_SUITE", ""),
        "revision": env.get("MOONMIND_CI_REVISION", ""),
        "runId": env.get("GITHUB_RUN_ID", ""),
        "attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "expectedPython": expected_python,
        "environment": environment,
        "fingerprint": environment_fingerprint(environment),
        "pytestArguments": arguments,
    }
    path = environment_path(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def run_verified_pytest(arguments: list[str]) -> int:
    """Capture the installed environment before pytest, then preserve its PID/signals."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-python", default=os.environ.get("MOONMIND_CI_EXPECTED_PYTHON")
    )
    options, pytest_args = parser.parse_known_args(arguments)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    if os.environ.get("MOONMIND_CI_RECEIPT"):
        if not options.expected_python:
            raise ValueError(
                "A verification environment must declare its Python version"
            )
        record_environment(
            Path(os.environ["MOONMIND_CI_RECEIPT"]),
            expected_python=options.expected_python,
            arguments=pytest_args,
        )
        os.environ["MOONMIND_CI_OWNER_PID"] = str(os.getpid())
    os.execv(sys.executable, [sys.executable, "-m", "pytest", *pytest_args])
    return 1  # pragma: no cover -- exec replaces this process


def pytest_configure(config) -> None:
    path = os.environ.get("MOONMIND_CI_RECEIPT")
    if (
        path
        and os.environ.get("MOONMIND_CI_OWNER_PID") == str(os.getpid())
        and not hasattr(config, "workerinput")
    ):
        config.pluginmanager.register(_VerificationReceipt(config, Path(path)))


class _VerificationReceipt:
    """One controller writes complete collection evidence, including xdist."""

    def __init__(self, config, path: Path):
        self.config, self.path = config, path
        self.started = time.monotonic()
        self.collected_at = None
        self.expected: set[str] | None = None
        self.cases: dict[str, dict] = {}
        self.errors: list[str] = []
        self.workers: set[str] = set()

    def _collection(self, ids):
        ids = list(ids)
        if len(ids) != len(set(ids)):
            self.errors.append("duplicate collected node IDs")
        if self.expected is not None and self.expected != set(ids):
            self.errors.append("workers collected different node IDs")
        self.expected = set(ids)
        if self.collected_at is None:
            self.collected_at = time.monotonic()

    def pytest_collection_finish(self, session):
        if not self.config.getoption("numprocesses", default=0):
            self._collection(item.nodeid for item in session.items)

    def pytest_xdist_node_collection_finished(self, node, ids):
        self.workers.add(node.gateway.id)
        self._collection(ids)

    def pytest_runtest_logreport(self, report):
        case = self.cases.setdefault(
            report.nodeid, {"nodeid": report.nodeid, "phases": {}}
        )
        context = getattr(report, "context", None)
        if context is not None:
            # A subtest reports within the parent's call phase, including over
            # xdist. Retain its result without counting that phase or time twice.
            case.setdefault("subtests", []).append(
                {
                    "outcome": (
                        "xfailed"
                        if report.skipped and hasattr(report, "wasxfail")
                        else report.outcome
                    ),
                    "seconds": report.duration,
                    "message": context.msg,
                    "parameters": dict(context.kwargs),
                }
            )
            if report.failed:
                case["outcome"] = "failed"
            return
        if report.when in case["phases"]:
            self.errors.append(f"duplicate phase: {report.nodeid} {report.when}")
        case["phases"][report.when] = {
            "outcome": report.outcome,
            "seconds": report.duration,
        }
        if report.failed:
            case["outcome"] = "failed"
        elif report.skipped:
            case["outcome"] = "xfailed" if hasattr(report, "wasxfail") else "skipped"
        else:
            case.setdefault("outcome", "passed")

    def pytest_sessionfinish(self, session, exitstatus):
        environment = observed_environment()
        expected = self.expected or set()
        complete = (
            bool(expected)
            and expected == self.cases.keys()
            and all(
                "teardown" in case["phases"]
                and (
                    "call" in case["phases"]
                    or case["outcome"] in {"skipped", "xfailed"}
                )
                for case in self.cases.values()
            )
        )
        payload = {
            "schema": RECEIPT_SCHEMA,
            "suite": os.environ.get("MOONMIND_CI_SUITE", ""),
            "revision": os.environ.get("MOONMIND_CI_REVISION", ""),
            "runId": os.environ.get("GITHUB_RUN_ID", ""),
            "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "status": (
                "passed"
                if int(exitstatus) == 0 and complete and not self.errors
                else "incomplete"
            ),
            "exitCode": int(exitstatus),
            "expectedNodeIds": sorted(expected),
            "cases": [self.cases[key] for key in sorted(self.cases)],
            "errors": self.errors,
            "workers": len(self.workers) or 1,
            **environment,
            "arguments": list(self.config.invocation_params.args),
            "collectionSeconds": (
                (self.collected_at - self.started) if self.collected_at else None
            ),
            "elapsedSeconds": time.monotonic() - self.started,
        }
        payload["environmentFingerprint"] = environment_fingerprint(environment)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2) + "\n")


_VerificationReceipt.pytest_xdist_node_collection_finished.pytest_impl = {
    "optionalhook": True
}
_VerificationReceipt.pytest_sessionfinish.pytest_impl = {"trylast": True}


def validate_receipt(path: Path, *, revision: str, run_id: str, attempt: str) -> dict:
    """Verification is strict; the advisory timing summary below stays tolerant."""
    try:
        receipt = json.loads(path.read_text())
        for key, value in {
            "schema": RECEIPT_SCHEMA,
            "revision": revision,
            "runId": run_id,
            "attempt": attempt,
            "status": "passed",
            "exitCode": 0,
        }.items():
            if receipt[key] != value:
                raise ValueError(f"{key} mismatch")
        manifest = json.loads(environment_path(path).read_text())
        if manifest.get("schema") != ENVIRONMENT_SCHEMA or any(
            manifest.get(key) != receipt.get(key)
            for key in ("suite", "revision", "runId", "attempt")
        ):
            raise ValueError("environment manifest identity mismatch")
        if set(manifest["environment"]) != set(ENVIRONMENT_FIELDS):
            raise ValueError("incomplete expected verification environment")
        observed = {key: receipt[key] for key in ENVIRONMENT_FIELDS}
        fingerprint = environment_fingerprint(observed)
        if (
            fingerprint != manifest["fingerprint"]
            or fingerprint != receipt.get("environmentFingerprint")
            or environment_fingerprint(manifest["environment"])
            != manifest["fingerprint"]
            or ".".join(receipt["python"].split(".")[:2]) != manifest["expectedPython"]
            or manifest["pytestArguments"] != receipt["arguments"]
        ):
            raise ValueError(
                "verification environment differs from its pre-test manifest"
            )
        root = Path(__file__).resolve().parents[2]
        declared = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("pyproject.toml", "poetry.lock")
        }
        if (
            observed["dependencyInputs"] != declared
            or not observed["dependencies"]
            or not observed["environmentInputs"]
        ):
            raise ValueError(
                "verification environment dependency inputs differ from this source"
            )
        expected = receipt["expectedNodeIds"]
        cases = receipt["cases"]
        actual = [case["nodeid"] for case in cases]
        if (
            not expected
            or len(expected) != len(set(expected))
            or set(expected) != set(actual)
            or len(actual) != len(set(actual))
        ):
            raise ValueError("missing, duplicate or extra node IDs")
        if receipt["errors"] or not receipt["suite"] or not receipt["python"]:
            raise ValueError("missing environment or collection failure")
        for case in cases:
            if (
                case["outcome"] not in {"passed", "skipped", "xfailed"}
                or "teardown" not in case["phases"]
            ):
                raise ValueError("unsuccessful or interrupted case")
            if "call" not in case["phases"] and case["outcome"] == "passed":
                raise ValueError("missing call")
            for phase in case["phases"].values():
                if (
                    phase["outcome"] not in {"passed", "skipped"}
                    or not math.isfinite(phase["seconds"])
                    or phase["seconds"] < 0
                ):
                    raise ValueError("invalid phase")
            for subtest in case.get("subtests", []):
                if (
                    subtest["outcome"] not in {"passed", "skipped", "xfailed"}
                    or not math.isfinite(subtest["seconds"])
                    or subtest["seconds"] < 0
                ):
                    raise ValueError("unsuccessful or invalid subtest")
        return receipt
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Invalid verification receipt {path}: {error}") from error


def write_frontend_receipt(report: Path, receipt: Path) -> None:
    payload = json.loads(report.read_text())
    assertions = [
        case for suite in payload["testResults"] for case in suite["assertionResults"]
    ]
    if (
        payload.get("success") is not True
        or not assertions
        or payload["numTotalTests"] != len(assertions)
        or any(
            case["status"] not in {"passed", "pending", "todo", "skipped"}
            for case in assertions
        )
    ):
        raise ValueError("Incomplete or unsuccessful frontend verification")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "moonmind.vitest-verification/v1",
                "revision": os.environ["MOONMIND_CI_REVISION"],
                "runId": os.environ["GITHUB_RUN_ID"],
                "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
                "report": payload,
            }
        )
        + "\n"
    )


def summarize_workflow_jobs(pages: list[dict], output: Path, summary: Path) -> dict:
    """Keep queue/runner/step observations separate from estimated savings."""

    def seconds(start, end):
        if not start or not end:
            return None
        return max(
            0,
            (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))
            ).total_seconds(),
        )

    def job_times(job):
        # GitHub can set started_at while a job is still queued. An executed
        # step witnesses that the interval began; a placeholder is not usage.
        allocated = any(
            step.get("started_at") and step.get("conclusion") != "skipped"
            for step in job.get("steps", [])
        )
        if allocated:
            return (
                seconds(job.get("created_at"), job.get("started_at")),
                seconds(job.get("started_at"), job.get("completed_at")),
            )
        if job["status"] != "completed":
            return None, None
        if job.get("conclusion") == "skipped":
            return 0, 0
        if (
            job.get("conclusion") == "cancelled"
            and "runner_id" in job
            and job["runner_id"] in (None, 0)
        ):
            return seconds(job.get("created_at"), job.get("completed_at")), 0
        return None, None

    jobs = []
    for page in pages:
        for job in page["jobs"]:
            queue_seconds, runner_seconds = job_times(job)
            jobs.append(
                {
                    "name": job["name"],
                    "id": job["id"],
                    "url": job["html_url"],
                    "status": job["status"],
                    "conclusion": job.get("conclusion"),
                    "queueSeconds": queue_seconds,
                    "runnerSeconds": runner_seconds,
                    "steps": [
                        {
                            "name": step["name"],
                            "conclusion": step.get("conclusion"),
                            "seconds": seconds(
                                step.get("started_at"), step.get("completed_at")
                            ),
                        }
                        for step in job.get("steps", [])
                    ],
                }
            )
    observed_runner = sum(
        job["runnerSeconds"] for job in jobs if job["runnerSeconds"] is not None
    )
    observed_queue = sum(
        job["queueSeconds"] for job in jobs if job["queueSeconds"] is not None
    )
    payload = {
        "schema": "moonmind.ci-job-timings/v1",
        "jobs": jobs,
        "runnerSeconds": (
            observed_runner
            if jobs and all(job["runnerSeconds"] is not None for job in jobs)
            else None
        ),
        "queueSeconds": (
            observed_queue
            if jobs and all(job["queueSeconds"] is not None for job in jobs)
            else None
        ),
        "observedRunnerSeconds": observed_runner,
        "observedQueueSeconds": observed_queue,
        "timingComplete": bool(jobs)
        and all(
            job["status"] == "completed"
            and job["runnerSeconds"] is not None
            and job["queueSeconds"] is not None
            for job in jobs
        ),
        "incompleteJobs": [job["name"] for job in jobs if job["status"] != "completed"],
        "unavailableTimingJobs": [
            job["name"]
            for job in jobs
            if job["status"] == "completed"
            and (job["runnerSeconds"] is None or job["queueSeconds"] is None)
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")
    with summary.open("a") as destination:
        destination.write(
            "\n## Observed CI timing\n\n| Job | Queue seconds | Runner seconds | Result |\n|---|---:|---:|---|\n"
        )
        for job in jobs:
            destination.write(
                f"| [{job['name']}]({job['url']}) | {job['queueSeconds']} | {job['runnerSeconds']} | {job['conclusion']} |\n"
            )
        destination.write(
            f"\nObserved allocated runner seconds: {observed_runner:.1f}; complete timing: {payload['timingComplete']}. "
            "Queued cancellations use no runner time. Unfinished or unavailable intervals leave totals incomplete. "
            "Queue waits and allocated runner intervals are additive, not wall-clock duration.\n"
        )
    return payload


def fetch_public_job_timings(
    repository: str, run_id: str, attempt: str, output: Path, summary: Path
) -> None:
    """Public run metadata needs no extra token permission or credentials."""
    pages = []
    try:
        for page in range(1, 101):
            url = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}"
            request = urllib.request.Request(
                url, headers={"User-Agent": "MoonMind-CI-timing"}
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            pages.append(payload)
            if len(payload["jobs"]) < 100:
                summarize_workflow_jobs(pages, output, summary)
                return
        raise ValueError("Job timing pagination exceeded its bound")
    except (OSError, ValueError, KeyError) as error:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps({"status": "unavailable", "reason": str(error)}) + "\n"
        )
        with summary.open("a") as destination:
            destination.write(f"\nCI timing metadata unavailable: {error}\n")


def pytest_itemcollected(item) -> None:  # noqa: ANN001 -- pytest plugin hook
    """Record the exact node ID and duration semantics in the JUnit report."""
    item.user_properties.append((NODEID_PROPERTY, item.nodeid))
    item.user_properties.append(
        (DURATION_REPORT_PROPERTY, item.config.getini("junit_duration_report"))
    )


@dataclass(frozen=True)
class CaseTiming:
    nodeid: str
    classname: str
    time: float | None
    junit_name: str = ""
    pytest_nodeid: str | None = None
    duration_report: str | None = None
    outcome: str = "passed"


@dataclass(frozen=True)
class JUnitSummary:
    tests: int
    failures: int
    errors: int
    skipped: int
    time: float
    cases: tuple[CaseTiming, ...]


@dataclass(frozen=True)
class LastActiveCase:
    """Explicit trailing live-test evidence derived from the streamed tee log.

    Only lines containing a pytest node ID (``::``) are surfaced, so secret
    material without a node ID never enters the step summary. The full
    streamed text log remains the primary record in the uploaded artifact.
    """

    last_case: str
    recent_cases: tuple[str, ...]
    total_lines: int


LAST_ACTIVE_SHOWN = 5
LAST_ACTIVE_SCAN_TAIL = 2000
LAST_ACTIVE_LINE_CAP = 300


def parse_junit(path: Path) -> JUnitSummary:
    """Parse a JUnit XML file produced by pytest --junitxml.

    Raises FileNotFoundError when the report is absent and ValueError when
    the XML cannot be interpreted. Counts come from testsuite attributes;
    individual <testcase> entries supply per-case timings.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    suites: list[ET.Element] = []
    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    else:
        raise ValueError(f"unexpected JUnit root tag: {root.tag!r}")

    tests = failures = errors = skipped = 0
    total_time = 0.0
    cases: list[CaseTiming] = []
    for suite in suites:
        tests += int(float(suite.get("tests", "0")))
        failures += int(float(suite.get("failures", "0")))
        errors += int(float(suite.get("errors", "0")))
        skipped += int(float(suite.get("skipped", "0")))
        total_time += float(suite.get("time", "0") or 0)
        for case in suite.iter("testcase"):
            classname = case.get("classname", "")
            name = case.get("name", "")
            properties = {
                prop.get("name"): prop.get("value")
                for prop in case.iter("property")
            }
            pytest_nodeid = properties.get(NODEID_PROPERTY) or None
            junit_name = f"{classname}::{name}" if classname else name
            try:
                duration: float | None = float(case.get("time", ""))
            except ValueError:
                duration = None
            if duration is not None and not math.isfinite(duration):
                duration = None
            cases.append(
                CaseTiming(
                    nodeid=pytest_nodeid or junit_name,
                    classname=classname,
                    time=duration,
                    junit_name=junit_name,
                    pytest_nodeid=pytest_nodeid,
                    duration_report=properties.get(DURATION_REPORT_PROPERTY),
                    outcome=_case_outcome(case),
                )
            )
    cases.sort(key=lambda c: (c.time is not None, c.time or 0.0), reverse=True)
    return JUnitSummary(
        tests=tests,
        failures=failures,
        errors=errors,
        skipped=skipped,
        time=total_time,
        cases=tuple(cases),
    )


def _case_outcome(case: ET.Element) -> str:
    tags = {child.tag for child in case}
    if "failure" in tags:
        return "failed"
    if "error" in tags:
        return "error"
    if "skipped" in tags:
        return "skipped"
    return "passed"


def _format_time(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.2f}s"


def write_slowest_report(summary: JUnitSummary, path: Path, limit: int = SLOWEST_LIMIT) -> None:
    """Write the slowest-test text report derived from JUnit timings."""
    lines = [
        f"# Slowest tests ({min(limit, len(summary.cases))} of {len(summary.cases)} cases)",
        f"# suite time: {summary.time:.2f}s tests={summary.tests} "
        f"failures={summary.failures} errors={summary.errors} skipped={summary.skipped}",
    ]
    for case in summary.cases[:limit]:
        lines.append(f"{_format_time(case.time)} {case.nodeid}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_durations_snapshot(
    suite: str,
    summary: JUnitSummary,
    path: Path,
    *,
    shard: str = "",
    revision: str = "",
    run_id: str = "",
    attempt: str = "",
    test_outcome: str = "",
) -> None:
    """Write the per-shard duration observations snapshot (separate artifact).

    This file is the #4629 import input for the current shard only. It must
    never overwrite a shared/committed selection-hints baseline; the import
    command accepts it only together with the other shards of the same
    run/attempt/revision. ``nodeid`` is the exact pytest node ID recorded by
    the plugin hook (null when absent) and ``duration`` is the JUnit total
    (null when the report's time was malformed).
    """
    semantics = {c.duration_report for c in summary.cases}
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "suite": suite,
        "shard": shard,
        "revision": revision,
        "runId": run_id,
        "attempt": attempt,
        "testOutcome": test_outcome,
        "durationSemantics": DURATION_SEMANTICS if semantics == {"total"} else None,
        "tests": summary.tests,
        "failures": summary.failures,
        "errors": summary.errors,
        "skipped": summary.skipped,
        "time": summary.time,
        "cases": [
            {
                "nodeid": c.pytest_nodeid,
                "junitName": c.junit_name,
                "classname": c.classname,
                "duration": c.time,
                "outcome": c.outcome,
            }
            for c in summary.cases
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def extract_last_active_case(log_path: Path, limit: int = LAST_ACTIVE_SHOWN) -> LastActiveCase | None:
    """Derive the trailing live-test node from the streamed pytest log.

    Scans only the tail of the known streamed log for lines carrying a
    pytest node ID (``::``) and returns the most recent ones. Returns None
    when the log is absent or holds no node-ID lines. Each surfaced line is
    capped so an unusually long line cannot bloat the step summary.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    total_lines = len(lines)
    if total_lines > LAST_ACTIVE_SCAN_TAIL:
        lines = lines[-LAST_ACTIVE_SCAN_TAIL:]
    node_lines = [line.strip()[:LAST_ACTIVE_LINE_CAP] for line in lines if "::" in line and line.strip()]
    if not node_lines:
        return None
    recent = tuple(node_lines[-limit:])
    return LastActiveCase(last_case=recent[-1], recent_cases=recent, total_lines=total_lines)


def classify_outcome(selected: bool, junit_exists: bool, test_outcome: str) -> str:
    """Map the row state to a human-readable outcome label."""
    normalized = (test_outcome or "unknown").strip().lower()
    if not selected:
        return "intentionally unselected"
    if normalized == "skipped":
        return "intentionally unselected"
    if not junit_exists:
        if normalized == "cancelled":
            return "canceled (interrupted -- no final JUnit report)"
        if normalized == "success":
            return "passed (interrupted -- no final JUnit report)"
        if normalized == "failure":
            return "failed (interrupted -- no final JUnit report)"
        return "unavailable (no final JUnit report)"
    if normalized == "cancelled":
        return "canceled"
    if normalized == "success":
        return "passed"
    if normalized == "failure":
        return "failed"
    return "unavailable"


# coreutils ``timeout`` exit statuses for the reliability shell deadline.
DEADLINE_STOPPED_STATUS = 124
HARD_KILLED_STATUS = 137


def classify_termination(selected: bool, exit_status: int | None) -> str:
    """Describe how the test command ended, from its recorded exit status.

    Only the recorded status is used: a missing status means the shell never
    reached the line after pytest (native step bound, cancellation or runner
    loss), so the end state is reported as unavailable, not inferred.
    """
    if not selected:
        return "intentionally unselected"
    if exit_status is None:
        return (
            "unavailable (no exit status recorded -- the native step bound, "
            "cancellation or runner loss stopped the step first)"
        )
    if exit_status == DEADLINE_STOPPED_STATUS:
        return (
            "cooperative deadline stop (exit status 124 -- the shell deadline "
            "interrupted pytest, which exited within its grace period)"
        )
    if exit_status == HARD_KILLED_STATUS:
        return (
            "hard kill (exit status 137 -- SIGKILL after the deadline grace "
            "period or from the OS; final reports may be missing)"
        )
    return f"pytest exited (exit status {exit_status})"


def render_summary(
    *,
    suite: str,
    shard: str,
    revision: str,
    run_id: str,
    attempt: str,
    selected: bool,
    test_outcome: str,
    junit: JUnitSummary | None,
    junit_path: str,
    junit_available: bool,
    log_path: str,
    log_available: bool,
    log_bytes: int | None,
    slowest_path: str,
    slowest_available: bool,
    durations_path: str,
    durations_available: bool,
    test_seconds: float | None,
    error: str | None,
    effective_command: str = "",
    pytest_timeout: str = "",
    step_budget: str = "",
    secondary_note: str | None = None,
    overhead_note: str | None = None,
    last_active: LastActiveCase | None = None,
    exit_status: int | None = None,
) -> str:
    """Render the per-job markdown appended to $GITHUB_STEP_SUMMARY."""
    identity = suite if not shard else f"{suite} (shard {shard})"
    outcome = classify_outcome(selected, junit_available, test_outcome)
    lines = [
        f"## Backend pytest evidence -- `{suite}`",
        "",
        f"- Suite/shard identity: `{identity}`",
        f"- Tested revision: `{revision or 'unavailable'}`",
        f"- Run/attempt: `{run_id or 'unavailable'}` / `{attempt or 'unavailable'}`",
        f"- Outcome: `{outcome}` (test step outcome: `{test_outcome or 'unavailable'}`)",
        f"- Termination: `{classify_termination(selected, exit_status)}`",
        "",
        "### Effective command and budgets",
        "",
    ]
    if effective_command:
        lines.append(f"- Effective pytest command: `{effective_command}`")
    else:
        lines.append("- Effective pytest command: unavailable (not recorded for this row).")
    budgets = []
    if pytest_timeout:
        budgets.append(f"per-test timeout `{pytest_timeout}`")
    if step_budget:
        budgets.append(f"step budget `{step_budget}`")
    if budgets:
        lines.append(f"- Budgets: {', '.join(budgets)} (measured).")
    else:
        lines.append("- Budgets: unavailable (not recorded for this row).")
    lines += [
        "",
        "### Counts (from JUnit testsuite attributes, never from progress %)",
        "",
    ]
    if junit is not None:
        lines.append(
            f"- Selected/collected/executed: tests=`{junit.tests}` "
            f"failures=`{junit.failures}` errors=`{junit.errors}` "
            f"skipped=`{junit.skipped}`"
        )
        lines.append(
            "- Note: `tests` is the JUnit collected total; intentionally "
            "unselected rows report `intentionally unselected`, never zero."
        )
    elif not selected:
        lines.append("- Selected/collected/executed: intentionally unselected (no tests run).")
    else:
        lines.append(
            "- Selected/collected/executed: unavailable (no final JUnit report; "
            "not zero tests, not a passing suite)."
        )
    lines += ["", "### Timings", ""]
    if test_seconds is not None:
        lines.append(f"- Test step wall time: `{test_seconds:.0f}s` (measured).")
    else:
        lines.append("- Test step wall time: unavailable (not measured or interrupted).")
    if junit is not None:
        lines.append(f"- JUnit suite time: `{junit.time:.2f}s` (available).")
    else:
        lines.append("- JUnit suite time: unavailable (no final JUnit report).")
    lines.append("- Setup: unavailable as a separate measurement in this row (included in wall time; see job logs).")
    lines.append("- Collection: unavailable as a separate measurement in this row (included in wall time; see job logs).")
    lines.append("- Cleanup: unavailable as a separate measurement in this row (runs as a separate always() step; see job logs).")
    lines += ["", "### Slowest cases", ""]
    if junit is not None and junit.cases:
        for case in junit.cases[:SUMMARY_SLOWEST_SHOWN]:
            lines.append(f"- `{_format_time(case.time)}` `{case.nodeid}`")
    elif not selected:
        lines.append("- Slowest cases: intentionally unselected.")
    else:
        lines.append("- Slowest cases: unavailable (no JUnit timings).")
    lines += ["", "### Last active case", ""]
    if not selected:
        lines.append("- Last active case: intentionally unselected.")
    elif not log_available:
        lines.append(
            "- Last active case: unavailable (no streamed log retained; "
            "live Actions output is the primary record)."
        )
    elif last_active is None:
        lines.append(
            "- Last active case: unavailable (no test node IDs in streamed log; "
            "see full text log)."
        )
    else:
        if junit is None:
            lines.append(
                "- Last active case (interrupted -- no final JUnit report; "
                "streamed log is the primary record):"
            )
        else:
            lines.append("- Last active case (trailing live node in streamed log):")
        lines.append(f"  - last: `{last_active.last_case}`")
        for case in last_active.recent_cases[:-1]:
            lines.append(f"  - recent: `{case}`")
    lines += ["", "### Retained evidence", ""]
    if log_available:
        size = f"`{log_bytes}` bytes" if log_bytes is not None else "available"
        lines.append(f"- Text log: `{log_path}` ({size}).")
    else:
        lines.append(f"- Text log: `{log_path}` (unavailable -- streamed live in Actions logs).")
    if junit_available:
        lines.append(f"- JUnit report: `{junit_path}` (available).")
    else:
        lines.append(
            f"- JUnit report: `{junit_path}` (unavailable -- interrupted before pytest "
            "wrote its final report; never treated as zero tests)."
        )
    if slowest_available:
        lines.append(f"- Slowest-test report: `{slowest_path}` (available).")
    else:
        lines.append(f"- Slowest-test report: `{slowest_path}` (unavailable).")
    if durations_available:
        lines.append(
            f"- Duration-hints snapshot: `{durations_path}` (available; per-shard "
            "artifact, never overwrites the shared selection baseline)."
        )
    else:
        lines.append(f"- Duration-hints snapshot: `{durations_path}` (unavailable).")
    lines += [
        "",
        "> Runner disappearance or a hard job kill may prevent final uploads. "
        + "Live Actions output (streamed via `tee`) remains the primary record in that case.",
    ]
    if overhead_note:
        lines += ["", "### Reporting overhead", "", overhead_note]
    if secondary_note:
        lines += [
            "",
            "### Secondary diagnostics/teardown (does not change the primary outcome)",
            "",
            secondary_note,
        ]
    if error:
        lines += ["", f"> Evidence hook note: `{error}` (test outcome unchanged)."]
    return "\n".join(lines) + "\n"


def _read_test_seconds(path: str | None) -> float | None:
    if not path:
        return None
    try:
        return float(Path(path).read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_exit_status(path: str | None) -> int | None:
    if not path:
        return None
    try:
        return int(Path(path).read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _read_collection_note(path: str | None) -> str | None:
    """Surface secondary diagnostics/teardown failures distinctly.

    Returns a markdown note when the collection-status file records a
    failed/timed-out/missing collection operation, else None. The primary
    pytest outcome is never altered here; this only makes secondary
    failures visible in the summary.
    """
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    lowered = text.lower()
    markers = ("failed", "timed out", "timed-out", "timeout", "missing", "unavailable")
    if not any(marker in lowered for marker in markers):
        return None
    # Keep the note compact: first few non-empty lines only.
    excerpt_lines = [line.strip() for line in text.splitlines() if line.strip()][:8]
    excerpt = "; ".join(excerpt_lines)[:800]
    return (
        f"- Secondary collection/teardown reported an issue (primary pytest "
        f"outcome unchanged): `{excerpt}`."
    )


def _evidence_bytes(*paths: str) -> int | None:
    total = 0
    seen_any = False
    for raw in paths:
        if not raw:
            continue
        try:
            total += Path(raw).stat().st_size
            seen_any = True
        except OSError:
            continue
    return total if seen_any else None


def build_evidence(args: argparse.Namespace) -> tuple[str, str | None]:
    """Parse JUnit, write slowest/durations files, return (markdown, error)."""
    import time

    hook_start = time.monotonic()
    junit: JUnitSummary | None = None
    error: str | None = None
    junit_path = Path(args.junit)
    junit_available = junit_path.is_file()
    if junit_available:
        try:
            junit = parse_junit(junit_path)
        except (OSError, ValueError, ET.ParseError) as exc:
            error = f"JUnit parse failed: {exc}"
            junit = None
            junit_available = False
    log_path = Path(args.log)
    log_available = log_path.is_file()
    log_bytes: int | None = None
    if log_available:
        try:
            log_bytes = log_path.stat().st_size
        except OSError:
            log_bytes = None
    slowest_available = False
    durations_available = False
    if junit is not None:
        try:
            write_slowest_report(junit, Path(args.slowest))
            slowest_available = True
        except OSError as exc:
            error = f"slowest-report write failed: {exc}"
        try:
            write_durations_snapshot(
                args.suite,
                junit,
                Path(args.durations_snapshot),
                shard=args.shard or "",
                revision=args.revision or "",
                run_id=args.run_id or "",
                attempt=args.attempt or "",
                test_outcome=args.test_outcome or "",
            )
            durations_available = True
        except OSError as exc:
            note = f"durations-snapshot write failed: {exc}"
            error = f"{error}; {note}" if error else note
    import time as _time

    hook_seconds = _time.monotonic() - hook_start
    evidence_total = _evidence_bytes(
        args.log if log_available else "",
        args.junit if (junit_available and junit is not None) else "",
        args.slowest if slowest_available else "",
        args.durations_snapshot if durations_available else "",
    )
    if evidence_total is not None:
        overhead_note = (
            f"- Reporting overhead: hook `{hook_seconds:.2f}s`, "
            f"retained evidence `{evidence_total}` bytes "
            f"(log/JUnit/slowest/snapshot only; rich bundles stay failure-gated)."
        )
    else:
        overhead_note = (
            f"- Reporting overhead: hook `{hook_seconds:.2f}s`, "
            f"no retained evidence files (interrupted before pytest wrote them)."
        )
    secondary_note = _read_collection_note(getattr(args, "collection_status", ""))
    last_active = extract_last_active_case(log_path) if log_available else None
    markdown = render_summary(
        suite=args.suite,
        shard=args.shard or "",
        revision=args.revision or "",
        run_id=args.run_id or "",
        attempt=args.attempt or "",
        selected=args.selected == "true",
        test_outcome=args.test_outcome or "unknown",
        junit=junit,
        junit_path=args.junit,
        junit_available=junit_available and junit is not None,
        log_path=args.log,
        log_available=log_available,
        log_bytes=log_bytes,
        slowest_path=args.slowest,
        slowest_available=slowest_available,
        durations_path=args.durations_snapshot,
        durations_available=durations_available,
        test_seconds=_read_test_seconds(args.test_seconds_file),
        error=error,
        effective_command=getattr(args, "effective_command", "") or "",
        pytest_timeout=getattr(args, "pytest_timeout", "") or "",
        step_budget=getattr(args, "step_budget", "") or "",
        secondary_note=secondary_note,
        overhead_note=overhead_note,
        last_active=last_active,
        exit_status=_read_exit_status(getattr(args, "test_status_file", "")),
    )
    return markdown, error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--shard", default="")
    parser.add_argument("--junit", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--slowest", required=True)
    parser.add_argument("--durations-snapshot", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--revision", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--attempt", default="")
    parser.add_argument("--selected", default="true", choices=["true", "false"])
    parser.add_argument("--test-outcome", default="unknown")
    parser.add_argument("--test-seconds-file", default="")
    parser.add_argument("--test-status-file", default="")
    parser.add_argument("--effective-command", default="")
    parser.add_argument("--pytest-timeout", default="")
    parser.add_argument("--step-budget", default="")
    parser.add_argument("--collection-status", default="")
    args = parser.parse_args(argv)
    # A diagnostic/timing parsing problem must never hide an unsuccessful
    # job or alter test selection: always exit 0 and record the note.
    try:
        markdown, _ = build_evidence(args)
    except Exception as exc:  # noqa: BLE001 -- hook must not fail the job
        markdown = render_summary(
            suite=args.suite,
            shard=args.shard or "",
            revision=args.revision or "",
            run_id=args.run_id or "",
            attempt=args.attempt or "",
            selected=args.selected == "true",
            test_outcome=args.test_outcome or "unknown",
            junit=None,
            junit_path=args.junit,
            junit_available=False,
            log_path=args.log,
            log_available=False,
            log_bytes=None,
            slowest_path=args.slowest,
            slowest_available=False,
            durations_path=args.durations_snapshot,
            durations_available=False,
            test_seconds=None,
            error=f"evidence hook failed: {exc}",
        )
    try:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as exc:
        print(f"warning: cannot append step summary: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(
        run_verified_pytest(sys.argv[2:]) if sys.argv[1:2] == ["run"] else main()
    )
