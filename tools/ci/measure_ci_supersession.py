#!/usr/bin/env python3
"""Measure superseded CI work from GitHub Actions run and job metadata.

Compares queue waits, allocated runner time, cancellation age and heavy-tier
starts in superseded runs against completed candidates, so admission changes
are judged by observed runs rather than estimates. Job intervals reuse the
``ci-timings`` semantics: only an executed step proves a runner was allocated.

Runs are measured from their first attempt, the candidate a newer push can
supersede. Cohorts come from observed job creation, not dates. When selection
finished before ``preflight-policy``, ungated heavy owners are created before
preflight completes and gated ones after it. Otherwise the run cannot show
which behavior applied and stays unobserved.

Usage (from the repository root, with an authenticated ``gh``):
  python -m tools.ci.measure_ci_supersession fetch --dir DIR [--runs 300]
  python -m tools.ci.measure_ci_supersession summarize --dir DIR --output FILE
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.ci.write_backend_matrix_summary import (
    interval_seconds,
    job_allocated,
    observed_job_seconds,
)

SCHEMA = "moonmind.ci-supersession/v1"
PREFLIGHT_JOB = "preflight-policy"
SELECTION_JOB = "select-test-suites"
# The selected hermetic, journey, reliability, exact-artifact and host-recovery
# tier, plus the image builds that exist only to feed it.
HEAVY_JOB_PREFIXES = (
    "integration-hermetic",
    "integration-ci",
    "backend-matrix (reliability",
    "prepare-app-image",
    "prepare-recovery-images",
    "omnigent-exact-artifact",
    "omnigent-host-recovery",
)
COMPLETED_CANDIDATE = ("success", "failure")


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _group(run: dict) -> tuple[str, str]:
    # Mirrors the workflow's isolation: one group per PR head or event ref.
    repository = (run.get("head_repository") or {}).get("full_name", "")
    return run["event"], f"{repository}:{run.get('head_branch') or ''}"


def _distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"samples": 0, "median": None, "p90": None, "max": None}
    return {
        "samples": len(values),
        "median": statistics.median(values),
        "p90": values[max(0, math.ceil(0.9 * len(values)) - 1)],
        "max": values[-1],
    }


def _is_heavy(job: dict) -> bool:
    return job["name"].startswith(HEAVY_JOB_PREFIXES)


def _completed(jobs: list[dict], name: str) -> str | None:
    job = next((job for job in jobs if job["name"] == name), None)
    return job and job.get("completed_at")


def _observe_run(run: dict, jobs: list[dict], successor: dict | None) -> dict:
    preflight = next((job for job in jobs if job["name"] == PREFLIGHT_JOB), None)
    preflight_done = _completed(jobs, PREFLIGHT_JOB)
    selection_done = _completed(jobs, SELECTION_JOB)
    heavy = [job for job in jobs if _is_heavy(job)]
    started_heavy = [job for job in heavy if job_allocated(job)]
    queue = runner = heavy_runner = 0.0
    complete = run.get("status") == "completed"
    for job in jobs:
        queue_seconds, runner_seconds = observed_job_seconds(job)
        if runner_seconds is None:
            complete = False
            continue
        runner += runner_seconds
        if _is_heavy(job):
            heavy_runner += runner_seconds
        if job_allocated(job):
            queue += queue_seconds or 0
    if run.get("conclusion") in COMPLETED_CANDIDATE:
        outcome = run["conclusion"]
    elif run.get("conclusion") == "cancelled":
        outcome = "superseded" if successor else "cancelled"
    elif run.get("status") != "completed":
        outcome = "in_progress"
    else:
        outcome = run.get("conclusion") or "unknown"
    heavy_before = 0.0
    for job in started_heavy:
        end = job.get("completed_at")
        if preflight_done and (not end or preflight_done < end):
            end = preflight_done
        heavy_before += interval_seconds(job["started_at"], end) or 0
    gated = None
    if heavy and preflight_done and selection_done and selection_done < preflight_done:
        gated = all(_time(job["created_at"]) >= _time(preflight_done) for job in heavy)
    return {
        "id": run["id"],
        "attempts": run.get("run_attempt", 1),
        "event": run["event"],
        "group": "/".join(_group(run)),
        "createdAt": run["created_at"],
        "outcome": outcome,
        "cancellationAgeSeconds": (
            interval_seconds(run["created_at"], successor["created_at"])
            if outcome == "superseded"
            else None
        ),
        "queueSeconds": queue,
        "runnerSeconds": runner,
        "heavyRunnerSeconds": heavy_runner,
        "heavyRunnerSecondsBeforePreflight": heavy_before,
        "heavyJobsStarted": len(started_heavy),
        "heavyJobsStartedBeforePreflight": sum(
            1
            for job in started_heavy
            if not preflight_done or _time(job["started_at"]) < _time(preflight_done)
        ),
        "preflightSeconds": preflight
        and interval_seconds(preflight.get("started_at"), preflight_done),
        # Time the gate holds heavy owners after selection: its latency cost.
        "admissionDelaySeconds": interval_seconds(selection_done, preflight_done),
        "preflightGated": gated,
        "timingComplete": complete,
        "_jobQueueSeconds": [
            observed_job_seconds(job)[0] for job in jobs if job_allocated(job)
        ],
    }


def _cohort(rows: list[dict]) -> dict:
    superseded = [row for row in rows if row["outcome"] == "superseded"]
    completed = [row for row in rows if row["outcome"] in COMPLETED_CANDIDATE]
    return {
        "runs": len(rows),
        "completedCandidates": len(completed),
        "superseded": len(superseded),
        "otherCancelled": sum(row["outcome"] == "cancelled" for row in rows),
        "inProgress": sum(row["outcome"] == "in_progress" for row in rows),
        "rerunRuns": sum(row["attempts"] > 1 for row in rows),
        "timingIncompleteRuns": sum(not row["timingComplete"] for row in rows),
        "supersededWithHeavyStart": sum(
            row["heavyJobsStarted"] > 0 for row in superseded
        ),
        "heavyJobsStartedInSuperseded": sum(
            row["heavyJobsStarted"] for row in superseded
        ),
        "heavyJobsStartedInCompleted": sum(
            row["heavyJobsStarted"] for row in completed
        ),
        "heavyJobsStartedBeforePreflight": sum(
            row["heavyJobsStartedBeforePreflight"] for row in rows
        ),
        "runnerHours": {
            "all": sum(row["runnerSeconds"] for row in rows) / 3600,
            "superseded": sum(row["runnerSeconds"] for row in superseded) / 3600,
            "supersededHeavy": sum(row["heavyRunnerSeconds"] for row in superseded)
            / 3600,
            "supersededHeavyBeforePreflight": sum(
                row["heavyRunnerSecondsBeforePreflight"] for row in superseded
            )
            / 3600,
        },
        "admissionDelaySeconds": _distribution(
            [
                row["admissionDelaySeconds"]
                for row in completed
                if row["admissionDelaySeconds"] is not None
            ]
        ),
        "cancellationAgeSeconds": _distribution(
            [row["cancellationAgeSeconds"] for row in superseded]
        ),
        "jobQueueSeconds": _distribution(
            [
                value
                for row in rows
                for value in row["_jobQueueSeconds"]
                if value is not None
            ]
        ),
        "preflightSeconds": _distribution(
            [
                row["preflightSeconds"]
                for row in rows
                if row["preflightSeconds"] is not None
            ]
        ),
    }


def measure(runs: list[dict], jobs_by_run: dict[int, list[dict]]) -> dict:
    runs = sorted(runs, key=lambda run: run["created_at"])
    successors: dict[int, dict] = {}
    intervals = []
    last_in_group: dict[tuple[str, str], dict] = {}
    for run in runs:
        previous = last_in_group.get(_group(run))
        if previous:
            if run["event"] == "pull_request":
                intervals.append(
                    interval_seconds(previous["created_at"], run["created_at"])
                )
            # cancel-in-progress fires when the newer run in the group queues.
            if previous.get("conclusion") == "cancelled" and (
                run["created_at"] <= previous["updated_at"]
            ):
                successors[previous["id"]] = run
        last_in_group[_group(run)] = run

    rows = []
    for run in runs:
        row = _observe_run(
            run, jobs_by_run.get(run["id"], []), successors.get(run["id"])
        )
        row["concurrentRunsAtCreation"] = sum(
            1
            for other in runs
            if other is not run
            and other["created_at"] < run["created_at"]
            and (
                other.get("status") != "completed"
                or other["updated_at"] > run["created_at"]
            )
        )
        rows.append(row)

    cohorts = {
        name: _cohort([row for row in rows if row["preflightGated"] is value])
        for name, value in (("ungated", False), ("gated", True), ("unobserved", None))
    }
    for row in rows:
        del row["_jobQueueSeconds"]
    events: dict[str, int] = {}
    for run in runs:
        events[run["event"]] = events.get(run["event"], 0) + 1
    return {
        "schema": SCHEMA,
        "window": {
            "from": runs[0]["created_at"] if runs else None,
            "to": runs[-1]["created_at"] if runs else None,
            "runs": len(runs),
            "events": events,
        },
        "cohorts": cohorts,
        "pushIntervalSeconds": _distribution(intervals),
        "concurrentRunsAtCreation": _distribution(
            [row["concurrentRunsAtCreation"] for row in rows]
        ),
        "runs": rows,
    }


def fetch(api, repository: str, workflow: str, *, runs: int, directory: Path) -> None:
    """Save raw run metadata and first-attempt jobs for ``summarize``."""
    collected: list[dict] = []
    for page in range(1, 101):
        batch = api(
            f"repos/{repository}/actions/workflows/{workflow}/runs?per_page=100&page={page}"
        )["workflow_runs"]
        collected.extend(batch)
        if len(collected) >= runs or len(batch) < 100:
            break
    collected = collected[:runs]
    (directory / "jobs").mkdir(parents=True, exist_ok=True)
    (directory / "runs.json").write_text(json.dumps(collected, indent=1) + "\n")
    for run in collected:
        pages = []
        for page in range(1, 101):
            pages.append(
                api(
                    f"repos/{repository}/actions/runs/{run['id']}/attempts/1/jobs?per_page=100&page={page}"
                )
            )
            if len(pages[-1]["jobs"]) < 100:
                break
        (directory / "jobs" / f"{run['id']}.json").write_text(json.dumps(pages) + "\n")


def _gh_api(path: str) -> dict:
    result = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def _render(report: dict) -> str:
    lines = [
        f"Window {report['window']['from']} .. {report['window']['to']}: "
        f"{report['window']['runs']} runs {report['window']['events']}",
        "",
        "| Cohort | Runs | Completed | Superseded | Superseded w/ heavy start | "
        "Heavy jobs started before preflight | Runner h | Superseded runner h | "
        "Superseded heavy runner h (before preflight) | "
        "Cancellation age median/p90 s | Job queue median/p90 s | "
        "Admission delay median/p90 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for name, cohort in report["cohorts"].items():
        age, queue = cohort["cancellationAgeSeconds"], cohort["jobQueueSeconds"]
        delay, hours = cohort["admissionDelaySeconds"], cohort["runnerHours"]
        lines.append(
            f"| {name} | {cohort['runs']} | {cohort['completedCandidates']} | "
            f"{cohort['superseded']} | {cohort['supersededWithHeavyStart']} | "
            f"{cohort['heavyJobsStartedBeforePreflight']} | {hours['all']:.1f} | "
            f"{hours['superseded']:.1f} | {hours['supersededHeavy']:.1f} "
            f"({hours['supersededHeavyBeforePreflight']:.1f}) | "
            f"{age['median']}/{age['p90']} | {queue['median']}/{queue['p90']} | "
            f"{delay['median']}/{delay['p90']} |"
        )
    push = report["pushIntervalSeconds"]
    lines.append(
        f"\nPR push interval median/p90 s: {push['median']}/{push['p90']} "
        f"({push['samples']} samples)."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--dir", type=Path, required=True)
    fetch_parser.add_argument("--repository", default="MoonLadderStudios/MoonMind")
    fetch_parser.add_argument("--workflow", default="pytest-unit-tests.yml")
    fetch_parser.add_argument("--runs", type=int, default=300)
    summarize_parser = commands.add_parser("summarize")
    summarize_parser.add_argument("--dir", type=Path, required=True)
    summarize_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "fetch":
        fetch(
            _gh_api, args.repository, args.workflow, runs=args.runs, directory=args.dir
        )
        return 0
    runs = json.loads((args.dir / "runs.json").read_text())
    jobs_by_run = {}
    for run in runs:
        path = args.dir / "jobs" / f"{run['id']}.json"
        if path.exists():
            jobs_by_run[run["id"]] = [
                job for page in json.loads(path.read_text()) for job in page["jobs"]
            ]
    report = measure(runs, jobs_by_run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(_render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
