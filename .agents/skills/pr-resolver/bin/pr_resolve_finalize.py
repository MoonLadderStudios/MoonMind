#!/usr/bin/env python3
"""Lightweight PR finalize helper for pr-resolver.

This script re-checks snapshot state and only performs merge/auto-merge decisions.
It is intended for low-cost follow-up runs after conflicts/comments/CI issues were fixed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFLICTING_MERGEABLE = {"CONFLICTING", "DIRTY"}
DIRECT_MERGE_STATE = {"CLEAN"}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _is_conflicting(pr: dict[str, Any]) -> bool:
    mergeable = pr.get("mergeable")
    merge_state = _normalize_text(pr.get("mergeStateStatus")).upper()
    if merge_state == "DIRTY":
        return True
    if isinstance(mergeable, bool):
        return mergeable is False
    mergeable_text = _normalize_text(mergeable).upper()
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
    if _normalize_text(ci.get("signalQuality")).lower() not in {"", "ok"}:
        return {"action": "blocked", "reason": "ci_signal_degraded"}
    if bool(ci.get("hasFailures")):
        return {"action": "blocked", "reason": "ci_failures"}
    if bool(ci.get("isRunning")):
        return {"action": "enable_auto_merge", "reason": "ci_running"}

    merge_state = _normalize_text(pr.get("mergeStateStatus")).upper()
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


def _merge_pr(pr_selector: str, merge_method: str, auto: bool) -> None:
    cmd = ["gh", "pr", "merge", pr_selector, f"--{merge_method}"]
    if auto:
        cmd.append("--auto")
    subprocess.run(cmd, check=True)


def _write_result(
    result_path: Path,
    *,
    snapshot: dict[str, Any],
    decision: str,
    merge_outcome: str,
    reason: str | None = None,
) -> None:
    pr = snapshot.get("pr") if isinstance(snapshot.get("pr"), dict) else {}
    payload: dict[str, Any] = {
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "decision": decision,
        "merge_outcome": merge_outcome,
    }
    if reason:
        payload["reason"] = reason
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
        pr_selector = _normalize_text(args.pr) or _normalize_text(pr.get("number"))
        if not pr_selector:
            raise RuntimeError("unable to determine PR selector from args or snapshot")

        action = decision["action"]
        reason = decision["reason"]

        if action == "enable_auto_merge":
            _merge_pr(pr_selector, args.merge_method, auto=True)
            _write_result(
                result_path,
                snapshot=snapshot,
                decision="enabled auto-merge while CI is running",
                merge_outcome="auto_merge_enabled",
                reason=reason,
            )
            print("Auto-merge enabled.")
            return

        if action == "merge_now":
            _merge_pr(pr_selector, args.merge_method, auto=False)
            _write_result(
                result_path,
                snapshot=snapshot,
                decision="merged immediately",
                merge_outcome="merged",
                reason=reason,
            )
            print("PR merged.")
            return

        _write_result(
            result_path,
            snapshot=snapshot,
            decision="blocked",
            merge_outcome="blocked",
            reason=reason,
        )
        print(f"Blocked: {reason}")
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        payload = {
            "decision": "failed",
            "merge_outcome": "failed",
            "reason": str(exc),
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
