#!/usr/bin/env python3
"""Deterministic full remediation gate helper for pr-resolver.

This script does not merge directly. It refreshes snapshot state, classifies the
blocking reason, and emits a structured artifact to drive remediation steps.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pr_resolve_contract import (  # noqa: E402
    EXIT_CODE_BLOCKED,
    EXIT_CODE_FAILED,
    EXIT_CODE_MERGED,
    FULL_REMEDIATION_REASONS,
    RESULT_SCHEMA_VERSION,
    now_utc_iso,
    normalize_text,
    remediation_next_step,
)
from pr_resolve_finalize import evaluate_finalize_action  # noqa: E402


def _run_snapshot(snapshot_script: Path, pr: str | None) -> None:
    cmd = [sys.executable, str(snapshot_script)]
    if pr:
        cmd.extend(["--pr", pr])
    subprocess.run(cmd, check=True)


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"snapshot not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"snapshot is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"snapshot must be a JSON object: {path}")
    return payload


def _write_result(
    result_path: Path,
    *,
    snapshot: dict[str, Any],
    status: str,
    merge_outcome: str,
    decision: str,
    reason: str | None,
    next_step: str,
    max_iterations: int,
    merge_method: str,
) -> None:
    pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": "pr_resolve_full",
        "timestamp": now_utc_iso(),
        "status": status,
        "merge_outcome": merge_outcome,
        "decision": decision,
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "max_iterations": int(max_iterations),
        "merge_method": merge_method,
        "next_step": next_step,
        "attempt_history": [
            {
                "stage": "full_remediation",
                "status": status,
                "reason": reason,
                "decision": decision,
                "timestamp": now_utc_iso(),
            }
        ],
    }
    if reason:
        payload["reason"] = reason
        payload["final_reason"] = reason
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def evaluate_full_state(snapshot: dict[str, Any]) -> dict[str, str]:
    decision = evaluate_finalize_action(snapshot)
    action = normalize_text(decision.get("action"))
    reason = normalize_text(decision.get("reason"))

    if action == "merge_now":
        return {
            "status": "ready_for_finalize",
            "merge_outcome": "skipped",
            "decision": "ready for finalize merge pass",
            "reason": reason or "ci_complete",
            "next_step": "run_finalize",
        }

    if reason in FULL_REMEDIATION_REASONS:
        return {
            "status": "needs_remediation",
            "merge_outcome": "blocked",
            "decision": "remediation required",
            "reason": reason,
            "next_step": remediation_next_step(reason),
        }

    return {
        "status": "blocked",
        "merge_outcome": "blocked",
        "decision": "blocked",
        "reason": reason or "unknown_blocker",
        "next_step": remediation_next_step(reason),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full PR resolver gate checker and remediation classifier"
    )
    parser.add_argument("--pr", help="Optional PR selector (number, URL, or branch)")
    parser.add_argument(
        "--merge-method",
        default="squash",
        choices=["merge", "squash", "rebase"],
        help="Merge strategy metadata for artifact output.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=3,
        help="Maximum full remediation iterations (metadata/guardrail).",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Use existing artifacts/pr_resolver_snapshot.json without refreshing",
    )
    parser.add_argument(
        "--snapshot-path",
        default="artifacts/pr_resolver_snapshot.json",
        help="Snapshot path to read/write",
    )
    parser.add_argument(
        "--result-path",
        default="artifacts/pr_resolver_full_result.json",
        help="Full remediation result artifact path",
    )
    args = parser.parse_args()

    snapshot_script = Path(__file__).with_name("pr_resolve_snapshot.py")
    snapshot_path = Path(args.snapshot_path)
    result_path = Path(args.result_path)

    try:
        if not args.skip_refresh:
            _run_snapshot(snapshot_script, args.pr)
        snapshot = _read_snapshot(snapshot_path)
        evaluation = evaluate_full_state(snapshot)
        _write_result(
            result_path,
            snapshot=snapshot,
            status=evaluation["status"],
            merge_outcome=evaluation["merge_outcome"],
            decision=evaluation["decision"],
            reason=evaluation["reason"],
            next_step=evaluation["next_step"],
            max_iterations=args.max_iterations,
            merge_method=args.merge_method,
        )
        print(json.dumps(evaluation, indent=2))
        if evaluation["status"] == "ready_for_finalize":
            sys.exit(EXIT_CODE_MERGED)
        sys.exit(EXIT_CODE_BLOCKED)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "tool": "pr_resolve_full",
            "timestamp": now_utc_iso(),
            "status": "failed",
            "merge_outcome": "failed",
            "decision": "failed",
            "reason": str(exc),
            "final_reason": str(exc),
            "next_step": "manual_review",
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        sys.exit(EXIT_CODE_FAILED)


if __name__ == "__main__":
    main()
