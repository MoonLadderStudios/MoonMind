#!/usr/bin/env python3
"""Bounded orchestration wrapper for finalize + full remediation retries."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pr_resolve_contract import (  # noqa: E402
    EXIT_CODE_ATTEMPTS_EXHAUSTED,
    EXIT_CODE_BLOCKED,
    EXIT_CODE_FAILED,
    EXIT_CODE_MERGED,
    FINALIZE_ONLY_RETRY_REASONS,
    FULL_REMEDIATION_REASONS,
    RESULT_SCHEMA_VERSION,
    classify_retry_action,
    compute_backoff_seconds,
    normalize_text,
    now_utc_iso,
    parse_reason,
    remediation_next_step,
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _normalize_status(payload: dict[str, Any]) -> str:
    status = normalize_text(payload.get("status")).lower()
    if status in {"merged", "blocked", "failed", "attempts_exhausted"}:
        return status
    merge_outcome = normalize_text(payload.get("merge_outcome")).lower()
    if merge_outcome == "merged":
        return "merged"
    if merge_outcome == "failed":
        return "failed"
    if merge_outcome == "attempts_exhausted":
        return "attempts_exhausted"
    return "blocked"


def _normalize_full_status(payload: dict[str, Any]) -> str:
    status = normalize_text(payload.get("status")).lower()
    if status in {"ready_for_finalize", "needs_remediation", "blocked", "failed"}:
        return status
    merge_outcome = normalize_text(payload.get("merge_outcome")).lower()
    if merge_outcome == "failed":
        return "failed"
    return "blocked"


def _is_safe_reason_transition(previous: str, current: str) -> bool:
    prev = normalize_text(previous)
    curr = normalize_text(current)
    if not prev or not curr or prev == curr:
        return True
    if prev in FULL_REMEDIATION_REASONS and curr in FULL_REMEDIATION_REASONS:
        return True
    if prev in FULL_REMEDIATION_REASONS and curr in FINALIZE_ONLY_RETRY_REASONS:
        return True
    if prev in FINALIZE_ONLY_RETRY_REASONS and curr in FULL_REMEDIATION_REASONS:
        return True
    if prev in FINALIZE_ONLY_RETRY_REASONS and curr in FINALIZE_ONLY_RETRY_REASONS:
        return True
    if prev == "merge_not_ready" and (
        curr in FULL_REMEDIATION_REASONS or curr in FINALIZE_ONLY_RETRY_REASONS
    ):
        return True
    if curr == "merge_not_ready" and prev in FINALIZE_ONLY_RETRY_REASONS:
        return True
    return False


def _build_result(
    *,
    status: str,
    decision: str,
    merge_outcome: str,
    final_reason: str | None,
    next_step: str,
    max_attempts: int,
    finalize_max_retries: int,
    fix_max_iterations: int,
    history: list[dict[str, Any]],
    escalations: int,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": "pr_resolve_orchestrate",
        "status": status,
        "merge_outcome": merge_outcome,
        "decision": decision,
        "reason": final_reason,
        "final_reason": final_reason,
        "next_step": next_step,
        "max_attempts": max_attempts,
        "finalize_max_retries": finalize_max_retries,
        "fix_max_iterations": fix_max_iterations,
        "escalations": escalations,
        "attempt_count": sum(1 for item in history if item.get("stage") == "finalize"),
        "started_at": started_at,
        "finished_at": finished_at,
        "attempt_history": history,
        "decisions": [
            f"{entry.get('stage')}:{entry.get('status')}:{entry.get('reason') or ''}"
            for entry in history
        ],
    }
    return payload


def run_orchestration(
    *,
    finalize_runner: Callable[[int], dict[str, Any]],
    full_runner: Callable[[int, int, str], dict[str, Any]],
    sleep_fn: Callable[[int], None],
    monotonic_fn: Callable[[], float],
    finalize_max_retries: int,
    fix_max_iterations: int,
    base_sleep_seconds: int,
    max_sleep_seconds: int,
    max_elapsed_seconds: int,
    merge_not_ready_grace_retries: int,
) -> tuple[dict[str, Any], int]:
    max_attempts = max(1, int(finalize_max_retries) + 1)
    history: list[dict[str, Any]] = []
    escalations = 0
    finalize_only_retry_index = 0
    grace_remaining = max(0, int(merge_not_ready_grace_retries))
    blocked_reason_previous = ""
    pending_progress_reason = ""

    started_at = now_utc_iso()
    started_monotonic = monotonic_fn()

    for attempt in range(1, max_attempts + 1):
        elapsed = monotonic_fn() - started_monotonic
        if elapsed > max_elapsed_seconds:
            result = _build_result(
                status="attempts_exhausted",
                decision="retry budget exhausted (timeout)",
                merge_outcome="attempts_exhausted",
                final_reason="timeout",
                next_step="manual_review",
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_ATTEMPTS_EXHAUSTED

        finalize_payload = finalize_runner(attempt)
        reason = parse_reason(finalize_payload)
        finalize_status = _normalize_status(finalize_payload)
        history.append(
            {
                "attempt": attempt,
                "stage": "finalize",
                "status": finalize_status,
                "reason": reason,
                "timestamp": now_utc_iso(),
            }
        )

        if finalize_status == "merged":
            result = _build_result(
                status="merged",
                decision="merged",
                merge_outcome="merged",
                final_reason=reason or "ci_complete",
                next_step="done",
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_MERGED

        if finalize_status == "failed":
            result = _build_result(
                status="failed",
                decision="finalize command failed",
                merge_outcome="failed",
                final_reason=reason or "finalize_failed",
                next_step="manual_review",
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_FAILED

        if (
            pending_progress_reason
            and reason
            and normalize_text(reason) == normalize_text(pending_progress_reason)
        ):
            result = _build_result(
                status="attempts_exhausted",
                decision="no progress after full remediation escalation",
                merge_outcome="attempts_exhausted",
                final_reason=reason,
                next_step=remediation_next_step(reason),
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_ATTEMPTS_EXHAUSTED
        pending_progress_reason = ""

        if (
            blocked_reason_previous
            and reason
            and normalize_text(reason) != normalize_text(blocked_reason_previous)
            and not _is_safe_reason_transition(blocked_reason_previous, reason)
        ):
            result = _build_result(
                status="blocked",
                decision="reason changed across retries; manual review required",
                merge_outcome="blocked",
                final_reason=reason,
                next_step="manual_review",
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_BLOCKED
        if reason:
            blocked_reason_previous = reason

        retry_action = classify_retry_action(
            reason,
            merge_not_ready_grace_remaining=grace_remaining,
        )
        if retry_action == "stop":
            result = _build_result(
                status="blocked",
                decision="blocked by non-retryable reason",
                merge_outcome="blocked",
                final_reason=reason or "blocked",
                next_step=remediation_next_step(reason),
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_BLOCKED

        if attempt >= max_attempts:
            result = _build_result(
                status="attempts_exhausted",
                decision="retry budget exhausted",
                merge_outcome="attempts_exhausted",
                final_reason=reason or "retry_cap_reached",
                next_step=remediation_next_step(reason),
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_ATTEMPTS_EXHAUSTED

        if retry_action == "finalize_only_retry":
            if reason == "merge_not_ready" and grace_remaining > 0:
                grace_remaining -= 1
            sleep_seconds = compute_backoff_seconds(
                finalize_only_retry_index,
                base_sleep_seconds=base_sleep_seconds,
                max_sleep_seconds=max_sleep_seconds,
            )
            finalize_only_retry_index += 1
            history.append(
                {
                    "attempt": attempt,
                    "stage": "wait",
                    "status": "scheduled",
                    "reason": reason,
                    "sleep_seconds": sleep_seconds,
                    "timestamp": now_utc_iso(),
                }
            )
            if sleep_seconds > 0:
                sleep_fn(sleep_seconds)
            continue

        escalations += 1
        pending_progress_reason = reason
        full_payload = full_runner(attempt, escalations, reason)
        full_status = _normalize_full_status(full_payload)
        full_reason = parse_reason(full_payload) or reason
        history.append(
            {
                "attempt": attempt,
                "stage": "full_remediation",
                "status": full_status,
                "reason": full_reason,
                "escalation": escalations,
                "max_iterations": fix_max_iterations,
                "timestamp": now_utc_iso(),
            }
        )
        if full_status == "failed":
            result = _build_result(
                status="failed",
                decision="full remediation command failed",
                merge_outcome="failed",
                final_reason=full_reason or "full_remediation_failed",
                next_step="manual_review",
                max_attempts=max_attempts,
                finalize_max_retries=finalize_max_retries,
                fix_max_iterations=fix_max_iterations,
                history=history,
                escalations=escalations,
                started_at=started_at,
                finished_at=now_utc_iso(),
            )
            return result, EXIT_CODE_FAILED

    result = _build_result(
        status="attempts_exhausted",
        decision="retry budget exhausted",
        merge_outcome="attempts_exhausted",
        final_reason=blocked_reason_previous or "retry_cap_reached",
        next_step=remediation_next_step(blocked_reason_previous),
        max_attempts=max_attempts,
        finalize_max_retries=finalize_max_retries,
        fix_max_iterations=fix_max_iterations,
        history=history,
        escalations=escalations,
        started_at=started_at,
        finished_at=now_utc_iso(),
    )
    return result, EXIT_CODE_ATTEMPTS_EXHAUSTED


def _run_command_and_read_result(
    cmd: list[str],
    *,
    result_path: Path,
) -> dict[str, Any]:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    payload = _read_json(result_path)
    if payload is None:
        payload = {
            "status": "failed",
            "merge_outcome": "failed",
            "reason": (
                f"command failed to produce result artifact: rc={result.returncode}; "
                f"stderr={(result.stderr or '').strip()}"
            ).strip(),
        }
    payload["_meta"] = {
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "command": cmd,
    }
    return payload


def _build_full_command_from_template(
    *,
    template: str,
    pr: str | None,
    merge_method: str,
    max_iterations: int,
    reason: str,
    snapshot_path: Path,
    result_path: Path,
) -> list[str]:
    rendered = template.format(
        python=sys.executable,
        pr=(pr or ""),
        merge_method=merge_method,
        max_iterations=max_iterations,
        reason=reason,
        snapshot_path=str(snapshot_path),
        result_path=str(result_path),
        full_script=str(Path(__file__).with_name("pr_resolve_full.py")),
    )
    return shlex.split(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orchestrate bounded finalize retries and full remediation escalations"
    )
    parser.add_argument("--pr", help="Optional PR selector (number, URL, or branch)")
    parser.add_argument(
        "--merge-method",
        default="squash",
        choices=["merge", "squash", "rebase"],
    )
    parser.add_argument(
        "--fix-max-iterations",
        type=int,
        default=3,
        help="Per full-remediation cycle max iterations.",
    )
    parser.add_argument(
        "--finalize-max-retries",
        type=int,
        default=6,
        help="Number of finalize retries after the initial attempt.",
    )
    parser.add_argument(
        "--base-sleep-seconds",
        type=int,
        default=30,
        help="Base sleep for finalize-only retries (exponential backoff).",
    )
    parser.add_argument(
        "--max-sleep-seconds",
        type=int,
        default=120,
        help="Max sleep for finalize-only retries.",
    )
    parser.add_argument(
        "--max-elapsed-seconds",
        type=int,
        default=1800,
        help="Hard wall-clock cap for orchestration execution.",
    )
    parser.add_argument(
        "--merge-not-ready-grace-retries",
        type=int,
        default=1,
        help="Allow N finalize-only retries for merge_not_ready before blocking.",
    )
    parser.add_argument(
        "--snapshot-path",
        default="var/pr_resolver/snapshot.json",
        help="Snapshot artifact path shared with finalize/full scripts.",
    )
    parser.add_argument(
        "--result-path",
        default="var/pr_resolver/result.json",
        help="Final orchestrated result path.",
    )
    parser.add_argument(
        "--attempt-artifacts-dir",
        default="var/pr_resolver/attempts",
        help="Directory for per-attempt finalize/full result artifacts.",
    )
    parser.add_argument(
        "--full-run-command",
        default="",
        help=(
            "Optional shell command template for full remediation. Supported "
            "placeholders: {python}, {full_script}, {pr}, {merge_method}, "
            "{max_iterations}, {reason}, {snapshot_path}, {result_path}."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not execute merge in finalize stage.",
    )
    args = parser.parse_args()

    finalize_script = Path(__file__).with_name("pr_resolve_finalize.py")
    full_script = Path(__file__).with_name("pr_resolve_full.py")
    result_path = Path(args.result_path)
    snapshot_path = Path(args.snapshot_path)
    attempts_dir = Path(args.attempt_artifacts_dir)
    attempts_dir.mkdir(parents=True, exist_ok=True)

    def finalize_runner(attempt: int) -> dict[str, Any]:
        finalize_result_path = attempts_dir / f"finalize_attempt_{attempt}.json"
        cmd = [
            sys.executable,
            str(finalize_script),
            "--merge-method",
            args.merge_method,
            "--snapshot-path",
            str(snapshot_path),
            "--result-path",
            str(finalize_result_path),
            "--strict-exit-codes",
        ]
        if args.pr:
            cmd.extend(["--pr", args.pr])
        if args.dry_run:
            cmd.append("--dry-run")
        return _run_command_and_read_result(cmd, result_path=finalize_result_path)

    def full_runner(attempt: int, escalation: int, reason: str) -> dict[str, Any]:
        full_result_path = attempts_dir / f"full_attempt_{attempt}_e{escalation}.json"
        if args.full_run_command.strip():
            cmd = _build_full_command_from_template(
                template=args.full_run_command,
                pr=args.pr,
                merge_method=args.merge_method,
                max_iterations=args.fix_max_iterations,
                reason=reason,
                snapshot_path=snapshot_path,
                result_path=full_result_path,
            )
        else:
            cmd = [
                sys.executable,
                str(full_script),
                "--merge-method",
                args.merge_method,
                "--max-iterations",
                str(args.fix_max_iterations),
                "--snapshot-path",
                str(snapshot_path),
                "--result-path",
                str(full_result_path),
            ]
            if args.pr:
                cmd.extend(["--pr", args.pr])
        return _run_command_and_read_result(cmd, result_path=full_result_path)

    result_payload, exit_code = run_orchestration(
        finalize_runner=finalize_runner,
        full_runner=full_runner,
        sleep_fn=time.sleep,
        monotonic_fn=time.monotonic,
        finalize_max_retries=args.finalize_max_retries,
        fix_max_iterations=args.fix_max_iterations,
        base_sleep_seconds=args.base_sleep_seconds,
        max_sleep_seconds=args.max_sleep_seconds,
        max_elapsed_seconds=args.max_elapsed_seconds,
        merge_not_ready_grace_retries=args.merge_not_ready_grace_retries,
    )

    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result_payload, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result_payload, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
