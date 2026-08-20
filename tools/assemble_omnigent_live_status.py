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
import tempfile
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

# The live-conformance workflow publishes the acceptance manifest under this
# artifact-name prefix, with the manifest itself at this filename.
MANIFEST_ARTIFACT_PREFIX = "omnigent-live-published-matrix-"
MANIFEST_FILENAME = "published-matrix.json"


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


def aggregate_provider_runner_fleet(
    runners: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate every runner carrying the provider-verification label.

    A repository can register several runners with the label, and the API
    returns them in an arbitrary order.  Reporting only the first would mark
    the protected tier unavailable whenever that one runner happens to be
    offline or busy while another eligible runner is online and idle.  The
    fleet is therefore online when *at least one* labeled runner is online, and
    busy only when every online runner is busy (no idle capacity).
    """

    matching = [
        runner for runner in runners if PROVIDER_RUNNER_LABEL in _runner_labels(runner)
    ]
    online = [
        runner
        for runner in matching
        if str(runner.get("status", "")).strip().lower() == "online"
    ]
    idle = [runner for runner in online if not bool(runner.get("busy", False))]
    return {
        "status": "online" if online else "offline",
        # Busy means "no idle capacity across the eligible fleet"; with no
        # online runner the tier is offline rather than busy.
        "busy": bool(online) and not idle,
        "matchingCount": len(matching),
        "onlineCount": len(online),
        "idleCount": len(idle),
    }


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

    runner_doc = aggregate_provider_runner_fleet(runners)

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


def select_manifest_artifact(
    artifacts: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    """Pick the newest unexpired published acceptance-manifest artifact.

    The live-conformance workflow uploads the manifest as
    ``omnigent-live-published-matrix-<run id>-<run attempt>``.  Expired
    artifacts are unusable, and a later run attempt supersedes an earlier one,
    so selection is newest-first by creation time.
    """

    candidates = [
        artifact
        for artifact in artifacts
        if str(artifact.get("name", "")).startswith(MANIFEST_ARTIFACT_PREFIX)
        and not artifact.get("expired")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda artifact: (
            str(artifact.get("created_at") or ""),
            int(artifact.get("id") or 0),
        ),
    )


def read_manifest_from_download(download_dir: Path) -> Mapping[str, Any] | None:
    """Read ``published-matrix.json`` from an extracted artifact directory."""

    for candidate in sorted(download_dir.rglob(MANIFEST_FILENAME)):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            return payload
    return None


def _gh_api(repository: str, path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _download_published_manifest(
    repository: str, run_id: Any, artifact_name: str
) -> Mapping[str, Any] | None:
    """Download and read the manifest published by a live-conformance run."""

    with tempfile.TemporaryDirectory() as raw_dir:
        download_dir = Path(raw_dir)
        subprocess.run(
            [
                "gh", "run", "download", str(run_id),
                "--repo", repository,
                "--name", artifact_name,
                "--dir", str(download_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return read_manifest_from_download(download_dir)


def load_published_manifest(
    repository: str, workflow_runs: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Retrieve the acceptance manifest published by the newest passing run.

    Readiness is evaluated against published acceptance evidence, so the status
    document must carry the real manifest.  Without it every run would report a
    failed ``evidence_fresh`` / ``evidence_digests_match`` signal and the
    protected tier could never become ready even after a fully successful live
    matrix.  A missing, expired, or unreadable manifest still yields ``None``,
    which keeps the projection fail-closed for a genuine absence of evidence.
    """

    for run in workflow_runs:
        if str(run.get("status", "")).strip().lower() != "completed":
            continue
        if str(run.get("conclusion", "")).strip().lower() != "success":
            continue
        run_id = run.get("id")
        if run_id is None:
            continue
        try:
            artifacts = _gh_api(
                repository, f"actions/runs/{run_id}/artifacts?per_page=100"
            ).get("artifacts", [])
            artifact = select_manifest_artifact(artifacts)
            if artifact is None:
                continue
            manifest = _download_published_manifest(
                repository, run_id, str(artifact["name"])
            )
        except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, OSError) as exc:
            print(f"::warning::could not read published acceptance manifest: {exc}")
            return None
        if manifest is not None:
            return manifest
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="omnigent-live-conformance.yml")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--manifest",
        help="Path to an already-downloaded published acceptance manifest. When "
        "omitted the manifest published by the newest passing live-conformance "
        "run is retrieved from that run's artifacts.",
    )
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

    if args.manifest:
        try:
            manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::warning::could not read acceptance manifest: {exc}")
            manifest = None
        if not isinstance(manifest, Mapping):
            manifest = None
    else:
        manifest = load_published_manifest(args.repository, runs)
    if manifest is None:
        print("::warning::no published acceptance manifest available for evaluation")

    document = build_status_document(
        runners=runners,
        workflow_runs=runs,
        latest_run_jobs=latest_run_jobs,
        manifest=manifest,
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
