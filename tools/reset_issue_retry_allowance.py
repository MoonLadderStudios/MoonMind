#!/usr/bin/env python3
"""Inventory, and on request record, audited GitHub issue retry resets.

Selection charges every attempt an issue's comments record against its retry
allowance. An attempt that never recorded its outcome is normally finished by
its owning deployment's claim sweep from Temporal history and runtime
evidence. When that evidence is gone (the deployment was replaced, Temporal
history expired, or another device owned the attempt), an operator records an
audited reset instead of deleting comments or editing counters by hand.

With no arguments this inventories the configured workflow repository
(``WORKFLOW_GITHUB_REPOSITORY``) and writes nothing. By default it plans a
reset only for issues whose exhausted allowance includes attempts that never
recorded an outcome; live reservations, holds, and unreadable evidence are
reported and never reset. Review the inventory, then apply:

    python tools/reset_issue_retry_allowance.py
    python tools/reset_issue_retry_allowance.py --issue 4503 --issue 4502 \\
        --apply --reason "Stranded attempt records; owning evidence is gone."

Each reset is one GitHub comment posted by the authenticated account, naming
who authorized it, when, why, and which attempts it supersedes. Earlier
attempts stay in the issue's history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.workflows.temporal.github_issue_retry_reset import (  # noqa: E402
    ACTION_REFUSE,
    ACTION_RESET,
    apply_retry_reset,
    inventory_retry_resets,
)


async def run(args: argparse.Namespace) -> int:
    from moonmind.workflows.adapters.github_service import GitHubService

    service = GitHubService()
    try:
        inventory = await inventory_retry_resets(
            service=service,
            repository=args.repository,
            issue_numbers=args.issue,
            limit=args.limit,
            include_recorded_outcomes=args.include_recorded_outcomes,
        )
    except ValueError as exc:
        print(f"Retry reset inventory unavailable: {exc}", file=sys.stderr)
        return 2
    plans = inventory["issues"]
    failed = 0
    applied = 0
    if args.apply:
        for plan in plans:
            if plan["action"] == ACTION_RESET:
                outcome = await apply_retry_reset(
                    service=service, plan=plan, reason=args.reason
                )
                plan["applyResult"] = outcome
                applied += int(bool(outcome.get("applied")))
                failed += int(not outcome.get("applied"))
    report = {
        **{key: value for key, value in inventory.items() if key != "issues"},
        "applied": args.apply,
        "resetsRecorded": applied,
        "issues": [
            plan for plan in plans if plan["action"] in {ACTION_RESET, ACTION_REFUSE}
        ],
    }
    if args.report:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "issues"}, indent=2))
    for plan in report["issues"]:
        retry = plan.get("retry") or {}
        line = f"  #{plan['issueNumber']}: {plan['action']} ({plan['reasonCode']})"
        if plan["action"] == ACTION_RESET:
            line += (
                f" supersedes {', '.join(plan['supersedes'])};"
                f" {retry.get('unresolvedAttempts', 0)} never recorded an outcome"
            )
        if "applyResult" in plan:
            line += f" -> {plan['applyResult'].get('reasonCode')}"
        print(line)
    if args.apply and not failed:
        print(
            "Resets recorded. The next search reassesses these issues from their history."
        )
    elif not args.apply and inventory["plannedResets"]:
        print(
            'Inventory only. Re-run with --apply --reason "..." to record these resets.'
        )
    return 2 if failed else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--repository",
        default="",
        help="owner/name; defaults to the configured WORKFLOW_GITHUB_REPOSITORY",
    )
    parser.add_argument(
        "--issue", type=int, action="append", default=[], help="Limit to this issue"
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument(
        "--include-recorded-outcomes",
        action="store_true",
        help=(
            "Also reset issues exhausted only by recorded outcomes (failed, "
            "no_work, implemented). By default those stay charged."
        ),
    )
    parser.add_argument("--report", default="", help="Write the JSON inventory here")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Record the planned resets. Without it this is an inventory only.",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="Why the reset is authorized; recorded in each reset comment",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.reason.strip():
        parser.error(
            "--apply requires --reason so each reset records why it was authorized"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
