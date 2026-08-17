#!/usr/bin/env python3
"""Project Omnigent protected-live verification health and fail closed.

Source issue: MoonLadderStudios/MoonMind#3710.

This is a lightweight, credential-free consumer of a non-secret status
document (assembled from the GitHub Actions / self-hosted runner API and the
latest published acceptance manifest).  It emits a versioned readiness
projection and exits non-zero when the protected provider-verification tier is
queued, offline, stale, incomplete, or missing a successful canary for the
deployed commit and required image digests.

Usage:
    python tools/omnigent_live_verification_health.py --status status.json
    cat status.json | python tools/omnigent_live_verification_health.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.omnigent.live_verification_health import (  # noqa: E402
    DEFAULT_MAX_QUEUE_AGE_SECONDS,
    LiveVerificationHealthError,
    evaluate_live_verification_health,
)


def evaluate_status_document(status: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a non-secret status document into a readiness projection."""
    if not isinstance(status, Mapping):
        raise LiveVerificationHealthError("status document must be an object")
    required_digests = status.get("requiredDigests") or {}
    if not isinstance(required_digests, Mapping):
        raise LiveVerificationHealthError("requiredDigests must be an object")
    return evaluate_live_verification_health(
        runner=status.get("runner") or {},
        queue=status.get("queue") or {},
        latest_run=status.get("latestRun"),
        manifest=status.get("manifest"),
        deployed_commit=str(status.get("deployedCommit", "")),
        required_digests=required_digests,
        max_queue_age_seconds=int(
            status.get("maxQueueAgeSeconds", DEFAULT_MAX_QUEUE_AGE_SECONDS)
        ),
        protected_tier_required=bool(status.get("protectedTierRequired", True)),
        tier4_healthy=bool(status.get("tier4Healthy", True)),
    )


def _load_status(path: str | None) -> Mapping[str, Any]:
    raw = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        help="Path to the non-secret status JSON document (defaults to stdin).",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the readiness projection JSON.",
    )
    args = parser.parse_args(argv)

    try:
        status = _load_status(args.status)
        projection = evaluate_status_document(status)
    except (LiveVerificationHealthError, json.JSONDecodeError, OSError) as exc:
        print(f"::error::live verification health could not be evaluated: {exc}")
        return 2

    serialized = json.dumps(projection, indent=2)
    print(serialized)
    if args.output:
        Path(args.output).write_text(serialized + "\n", encoding="utf-8")

    if not projection["rolloutReady"]:
        reasons = ", ".join(projection["notReadyReasons"]) or "unknown"
        print(f"::error::Omnigent live verification is not ready: {reasons}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
