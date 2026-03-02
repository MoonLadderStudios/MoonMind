#!/usr/bin/env python3
"""Lightweight PR finalize helper for pr-resolver.

This script re-checks snapshot state and only performs merge/block decisions.
It is intended for low-cost follow-up runs after conflicts/comments/CI issues
were fixed.
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
    FINALIZE_ONLY_RETRY_REASONS,
    FULL_REMEDIATION_REASONS,
    RESULT_SCHEMA_VERSION,
    normalize_text,
    now_utc_iso,
    remediation_next_step,
)

CONFLICTING_MERGEABLE = {"CONFLICTING", "DIRTY"}
DIRECT_MERGE_STATE = {"CLEAN"}


def _is_conflicting(pr: dict[str, Any]) -> bool:
    mergeable = pr.get("mergeable")
    merge_state = normalize_text(pr.get("mergeStateStatus")).upper()
    if merge_state == "DIRTY":
        return True
    if isinstance(mergeable, bool):
        return mergeable is False
    mergeable_text = normalize_text(mergeable).upper()
    return mergeable_text in CONFLICTING_MERGEABLE


def evaluate_finalize_action(snapshot: dict[str, Any]) -> dict[str, str]:
    pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
    ci = snapshot.get("ci") if isinstance(snapshot.get("ci"), dict) else {}
    comments_fetch = (
        snapshot.get("commentsFetch")
        if isinstance(snapshot.get("commentsFetch"), dict)
        else {}
    )
    comments_summary = (
        snapshot.get("commentsSummary")
        if isinstance(snapshot.get("commentsSummary"), dict)
        else {}
    )

    if not bool(comments_fetch.get("succeeded")):
        return {"action": "blocked", "reason": "comments_unavailable"}
    if comments_summary.get("includeBotReviewComments") is not True:
        return {"action": "blocked", "reason": "comment_policy_not_enforced"}
    if bool(comments_summary.get("hasActionableComments")):
        return {"action": "blocked", "reason": "actionable_comments"}
    if _is_conflicting(pr):
        return {"action": "blocked", "reason": "merge_conflicts"}
    if normalize_text(ci.get("signalQuality")).lower() not in {"", "ok"}:
        return {"action": "blocked", "reason": "ci_signal_degraded"}
    if bool(ci.get("hasFailures")):
        return {"action": "blocked", "reason": "ci_failures"}
    if bool(ci.get("isRunning")):
        return {"action": "blocked", "reason": "ci_running"}

    merge_state = normalize_text(pr.get("mergeStateStatus")).upper()
    if merge_state in DIRECT_MERGE_STATE:
        return {"action": "merge_now", "reason": "ci_complete"}

    return {"action": "blocked", "reason": "merge_not_ready"}


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


def _merge_pr(pr_selector: str, merge_method: str) -> None:
    cmd = ["gh", "pr", "merge", pr_selector, f"--{merge_method}"]
    subprocess.run(cmd, check=True)


def _write_result(
    result_path: Path,
    *,
    snapshot: dict[str, Any],
    decision: str,
    merge_outcome: str,
    status: str,
    reason: str | None = None,
) -> None:
    pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
    next_step = "done"
    if status == "blocked":
        next_step = remediation_next_step(reason or "")
        if reason in FINALIZE_ONLY_RETRY_REASONS:
            next_step = "retry_finalize_after_backoff"
        if reason in FULL_REMEDIATION_REASONS:
            next_step = "run_full_remediation"

    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": "pr_resolve_finalize",
        "timestamp": now_utc_iso(),
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "decision": decision,
        "merge_outcome": merge_outcome,
        "status": status,
        "attempt": 1,
        "max_attempts": 1,
        "escalations": 0,
        "next_step": next_step,
    }
    if reason:
        payload["reason"] = reason
        payload["final_reason"] = reason
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize PR merge/auto-merge decisions"
    )
    parser.add_argument("--pr", help="Optional PR selector (number, URL, or branch)")
    parser.add_argument(
        "--merge-method",
        default="squash",
        choices=["merge", "squash", "rebase"],
        help="Merge strategy for gh pr merge",
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
        default="artifacts/pr_resolver_result.json",
        help="Result artifact path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute gh pr merge even if merge gates pass.",
    )
    parser.add_argument(
        "--strict-exit-codes",
        action="store_true",
        help="Return exit code 2 when blocked (default keeps blocked as exit code 0).",
    )
    args = parser.parse_args()

    snapshot_script = Path(__file__).with_name("pr_resolve_snapshot.py")
    snapshot_path = Path(args.snapshot_path)
    result_path = Path(args.result_path)

    try:
        if not args.skip_refresh:
            _run_snapshot(snapshot_script, args.pr)
        snapshot = _read_snapshot(snapshot_path)
        decision = evaluate_finalize_action(snapshot)

        pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
        pr_selector = normalize_text(args.pr) or normalize_text(pr.get("number"))
        if not pr_selector:
            raise RuntimeError("unable to determine PR selector from args or snapshot")

        action = decision["action"]
        reason = decision["reason"]

        if action == "merge_now":
            if args.dry_run:
                _write_result(
                    result_path,
                    snapshot=snapshot,
                    decision="merge gate passed (dry-run)",
                    merge_outcome="skipped",
                    status="blocked",
                    reason="dry_run",
                )
                print("Merge gate passed (dry-run).")
                sys.exit(EXIT_CODE_BLOCKED if args.strict_exit_codes else 0)
            _merge_pr(pr_selector, args.merge_method)
            _write_result(
                result_path,
                snapshot=snapshot,
                decision="merged immediately",
                merge_outcome="merged",
                status="merged",
                reason=reason,
            )
            print("PR merged.")
            sys.exit(EXIT_CODE_MERGED)

        _write_result(
            result_path,
            snapshot=snapshot,
            decision="blocked",
            merge_outcome="blocked",
            status="blocked",
            reason=reason,
        )
        print(f"Blocked: {reason}")
        sys.exit(EXIT_CODE_BLOCKED if args.strict_exit_codes else 0)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        payload = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "tool": "pr_resolve_finalize",
            "timestamp": now_utc_iso(),
            "decision": "failed",
            "merge_outcome": "failed",
            "status": "failed",
            "reason": str(exc),
            "final_reason": str(exc),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        sys.exit(EXIT_CODE_FAILED)


if __name__ == "__main__":
    main()
