#!/usr/bin/env python3
"""Assemble a non-secret Omnigent live-verification status document.

Source issue: MoonLadderStudios/MoonMind#3710.

This is a credential-free consumer of the GitHub Actions / self-hosted runner
API.  It projects only non-secret facts (runner online/busy state, oldest
queued provider-verification job age, latest live-conformance run outcome and
matrix modes, and the latest published acceptance manifest freshness/digests)
into the status document consumed by
``tools/omnigent_live_verification_health.py``.

The network-facing ``main`` is intentionally thin; the transform
(:func:`build_status_document`) is pure and fully unit-tested.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.live_verification_health import (  # noqa: E402
    DEFAULT_MAX_QUEUE_AGE_SECONDS,
    REQUIRED_LIVE_MATRIX_MODES,
)

PROVIDER_RUNNER_LABEL = "omnigent-provider-verification"
_QUEUED_STATES = {"queued", "in_progress", "pending", "waiting", "requested"}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _runner_labels(runner: Mapping[str, Any]) -> set[str]:
    labels = runner.get("labels") or []
    names: set[str] = set()
    for label in labels:
        if isinstance(label, Mapping):
            name = label.get("name")
        else:
            name = label
        if isinstance(name, str):
            names.add(name)
    return names


def _select_provider_runner(
    runners: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for runner in runners:
        if PROVIDER_RUNNER_LABEL in _runner_labels(runner):
            return runner
    return None


def _modes_from_jobs(jobs: Sequence[Mapping[str, Any]]) -> list[str]:
    observed: list[str] = []
    for job in jobs:
        name = str(job.get("name", "")).lower()
        for mode in REQUIRED_LIVE_MATRIX_MODES:
            if mode in name and mode not in observed:
                observed.append(mode)
    return observed


def build_status_document(
    *,
    runners: Sequence[Mapping[str, Any]],
    workflow_runs: Sequence[Mapping[str, Any]],
    latest_run_jobs: Sequence[Mapping[str, Any]] = (),
    manifest: Mapping[str, Any] | None,
    deployed_commit: str,
    required_digests: Mapping[str, str],
    protected_tier_required: bool = True,
    tier4_healthy: bool = True,
    max_queue_age_seconds: int = DEFAULT_MAX_QUEUE_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Transform raw API projections into a non-secret status document."""
    observed_at = now or datetime.now(timezone.utc)

    provider_runner = _select_provider_runner(runners)
    if provider_runner is None:
        runner_doc = {"status": "offline", "busy": False}
    else:
        runner_doc = {
            "status": str(provider_runner.get("status", "offline")),
            "busy": bool(provider_runner.get("busy", False)),
        }

    # Oldest queued/in-progress provider-verification run age.
    oldest_age: int | None = None
    latest_run: Mapping[str, Any] | None = None
    for run in workflow_runs:
        status = str(run.get("status", "")).strip().lower()
        created = _parse_timestamp(run.get("created_at") or run.get("run_started_at"))
        if status in _QUEUED_STATES and created is not None:
            age = int((observed_at - created).total_seconds())
            oldest_age = age if oldest_age is None else max(oldest_age, age)
        if status == "completed" and latest_run is None:
            latest_run = run

    latest_run_doc: dict[str, Any] | None = None
    if latest_run is not None:
        conclusion = str(latest_run.get("conclusion", "")).strip().lower()
        latest_run_doc = {
            "status": "success" if conclusion == "success" else conclusion or "unknown",
            "sourceCommit": str(latest_run.get("head_sha", "")),
            "startedAt": latest_run.get("run_started_at"),
            "completedAt": latest_run.get("updated_at"),
            "modes": _modes_from_jobs(latest_run_jobs),
        }

    document = {
        "deployedCommit": deployed_commit,
        "requiredDigests": {
            "server": required_digests.get("server"),
            "host": required_digests.get("host"),
        },
        "runner": runner_doc,
        "queue": {"oldestQueuedAgeSeconds": oldest_age},
        "latestRun": latest_run_doc,
        "manifest": dict(manifest) if isinstance(manifest, Mapping) else None,
        "maxQueueAgeSeconds": max_queue_age_seconds,
        "protectedTierRequired": bool(protected_tier_required),
        "tier4Healthy": bool(tier4_healthy),
    }
    return document


def _gh_api(repository: str, path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="omnigent-live-conformance.yml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        runners = _gh_api(args.repository, "actions/runners").get("runners", [])
        runs = _gh_api(
            args.repository,
            f"actions/workflows/{args.workflow}/runs?per_page=20",
        ).get("workflow_runs", [])
        latest_completed = next(
            (r for r in runs if str(r.get("status", "")).lower() == "completed"),
            None,
        )
        latest_run_jobs: list[Mapping[str, Any]] = []
        if latest_completed is not None:
            latest_run_jobs = _gh_api(
                args.repository, f"actions/runs/{latest_completed['id']}/jobs"
            ).get("jobs", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        print(f"::warning::could not read GitHub Actions status: {exc}")
        runners, runs, latest_run_jobs = [], [], []

    document = build_status_document(
        runners=runners,
        workflow_runs=runs,
        latest_run_jobs=latest_run_jobs,
        manifest=None,
        deployed_commit=os.environ.get("DEPLOYED_COMMIT", ""),
        required_digests={
            "server": os.environ.get("REQUIRED_SERVER_DIGEST"),
            "host": os.environ.get("REQUIRED_HOST_DIGEST"),
        },
        protected_tier_required=os.environ.get("PROTECTED_TIER_REQUIRED", "true")
        != "false",
    )
    Path(args.output).write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote non-secret live status document to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
