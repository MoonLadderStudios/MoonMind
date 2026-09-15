#!/usr/bin/env python3
"""One authorized, reviewed batch migration of version-1 issue reservations.

Ordinary selection recovers expired (version-2) reservations on its own. This
tool exists for the one-time backlog that predates the lease agreement: it
takes an inventory first, and only rewrites comments when an operator supplies
an explicit cutover instant and ``--apply``.

Run the inventory, review it, stop or upgrade every deployment that can still
write the old protocol, then apply:

    python tools/recover_legacy_issue_claims.py --repository owner/name \\
        --cutover-at 2026-09-14T00:00:00Z --report var/artifacts/claim-recovery.json
    python tools/recover_legacy_issue_claims.py --repository owner/name \\
        --cutover-at 2026-09-14T00:00:00Z --report var/artifacts/claim-recovery.json --apply

The same cutover instant belongs in the deployment's
``MOONMIND_ISSUE_CLAIM_LEGACY_CUTOVER_AT`` so selection, publication checks,
and maintenance all read the migrated backlog the same way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from moonmind.workflows.temporal.github_issue_claim_migration import (  # noqa: E402
    WRITE_ACTIONS,
    apply_issue_recovery,
    parse_cutover,
    plan_issue_recovery,
)


async def _open_issues(
    *, service, repository: str, limit: int, explicit: list[int]
) -> list[dict[str, Any]]:
    if explicit:
        token, _ = await service.resolve_github_token(repo=repository)
        if not token:
            raise SystemExit("GitHub credentials are unavailable for this repository.")
        issues = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for number in explicit:
                response = await client.get(
                    f"https://api.github.com/repos/{repository}/issues/{number}",
                    headers=service._github_headers(token),
                )
                response.raise_for_status()
                issues.append(response.json())
        return issues
    token, _ = await service.resolve_github_token(repo=repository)
    if not token:
        raise SystemExit("GitHub credentials are unavailable for this repository.")
    issues: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 1
        while len(issues) < limit and page <= 10:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues",
                params={"state": "open", "per_page": 100, "page": page},
                headers=service._github_headers(token),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or not payload:
                break
            issues.extend(item for item in payload if "pull_request" not in item)
            page += 1
    return issues[:limit]


async def run(args: argparse.Namespace) -> int:
    from moonmind.workflows.adapters.github_service import GitHubService
    from moonmind.workflows.temporal.activities.github_issue_reconciliation_activities import (
        _trusted_posters,
    )

    cutover = parse_cutover(args.cutover_at)
    service = GitHubService()
    actor = await service.issue_claim_actor(repo=args.repository)
    if not actor.get("ok"):
        raise SystemExit("The authenticated GitHub claim actor could not be resolved.")
    trusted = _trusted_posters(service=service)
    issues = await _open_issues(
        service=service,
        repository=args.repository,
        limit=args.limit,
        explicit=args.issue,
    )
    plans: list[dict[str, Any]] = []
    for issue in issues:
        listed = await service.list_issue_comments(
            repo=args.repository, issue_number=int(issue["number"])
        )
        if not listed.get("ok") or not isinstance(listed.get("comments"), list):
            plans.append(
                {
                    "repository": args.repository,
                    "issueNumber": issue["number"],
                    "attempts": [],
                    "plannedWrites": 0,
                    "reasonCode": "claim_read_failure",
                }
            )
            continue
        plans.append(
            plan_issue_recovery(
                repository=args.repository,
                issue=issue,
                comments=listed["comments"],
                cutover=cutover,
                actor_id=actor["actorId"],
                trusted=trusted,
            )
        )
    applied = 0
    if args.apply:
        for plan in plans:
            if plan.get("plannedWrites"):
                outcome = await apply_issue_recovery(
                    service=service, plan=plan, cutover=cutover
                )
                plan["applyResult"] = outcome
                applied += int(outcome.get("applied") or 0)
    report = {
        "repository": args.repository,
        "cutoverAt": cutover.isoformat(),
        "applied": args.apply,
        "issuesExamined": len(plans),
        "reservationsRetired": applied,
        "plannedWrites": sum(int(plan.get("plannedWrites") or 0) for plan in plans),
        "staleStatusLabels": sum(
            1 for plan in plans if plan.get("staleStatusLabel")
        ),
        "issues": [plan for plan in plans if plan.get("attempts") or plan.get("staleStatusLabel")],
    }
    if args.report:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "issues"}, indent=2))
    for plan in report["issues"]:
        actions = sorted(
            {
                attempt["action"]
                for attempt in plan.get("attempts", [])
                if attempt["action"] in WRITE_ACTIONS
            }
        )
        if actions or plan.get("staleStatusLabel"):
            print(
                f"  #{plan['issueNumber']}: "
                + (", ".join(actions) or "stale_status_label")
                + f" -> {plan.get('nextAction', 'reassess')}"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/name")
    parser.add_argument(
        "--cutover-at",
        required=True,
        help=(
            "Operator-declared ISO-8601 instant after which version-1 "
            "reservations no longer hold write authority. Only claims GitHub "
            "timestamps before this instant are retired."
        ),
    )
    parser.add_argument(
        "--issue", type=int, action="append", default=[], help="Limit to this issue"
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--report", default="", help="Write the JSON inventory here")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite the planned comments. Without it this is an inventory only.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
