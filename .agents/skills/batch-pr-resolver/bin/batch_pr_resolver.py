#!/usr/bin/env python3
"""Create `pr-resolver` tasks for each open PR in a repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_service.db.base import get_async_session_context
from moonmind.workflows import get_agent_queue_service


@dataclass
class JobSubmission:
    queue_request: dict[str, Any]
    pr_number: int | str
    branch: str


def _run_command(cmd: list[str]) -> str:
    """Run a command and return trimmed stdout text."""

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        message = stderr or (exc.stdout or "").strip() or str(exc)
        raise RuntimeError(f"command failed: {' '.join(cmd)}: {message}") from exc
    return (result.stdout or "").strip()


def _normalize_repo(value: str | None) -> str | None:
    if not value:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    if re.fullmatch(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", candidate):
        return candidate
    return None


def _infer_repo_from_remote() -> str | None:
    remote = _run_command(["git", "remote", "get-url", "origin"])
    remote = remote.strip()
    if remote.startswith("git@github.com:"):
        body = remote.split(":", 1)[1]
        if body.endswith(".git"):
            body = body[:-4]
        if "/" in body:
            return body
    match = re.search(
        r"github\.com[:/](?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\.git)?$", remote
    )
    if match:
        return match.group("repo")
    return None


def _resolve_repo(raw_repo: str | None) -> str:
    if raw_repo is not None:
        normalized = _normalize_repo(raw_repo)
        if normalized:
            return normalized
        raise RuntimeError("Invalid --repo value; expected owner/repo format.")
    for env_key in ("WORKFLOW_GITHUB_REPOSITORY", "GITHUB_REPOSITORY", "MOONMIND_REPO"):
        normalized = _normalize_repo(os.getenv(env_key, ""))
        if normalized:
            return normalized
    return _infer_repo_from_remote() or ""


def _run_pr_list(repo: str, state: str) -> list[dict[str, Any]]:
    raw = _run_command(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            state,
            "--json",
            "number,title,headRefName,headRepositoryOwner,headRepository",
            "--limit",
            "100000",
        ]
    )
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("Unexpected `gh pr list` payload shape.")
    return parsed


def _is_local_head(pr: dict[str, Any], repo: str) -> bool:
    head_repo = pr.get("headRepository")
    if isinstance(head_repo, dict):
        name_with_owner = str(head_repo.get("nameWithOwner") or "").strip().lower()
        if name_with_owner:
            return name_with_owner == repo.lower()

    return False


def _extract_branch(pr: dict[str, Any]) -> str:
    branch = str(pr.get("headRefName") or "").strip()
    if not branch:
        raise RuntimeError(f"PR #{pr.get('number')}: missing headRefName")
    return branch


def _build_queue_request(
    repo: str,
    pr_number: int | str,
    branch: str,
    *,
    merge_method: str,
    max_iterations: int,
    priority: int,
    max_attempts: int,
) -> dict[str, Any]:
    return {
        "type": "task",
        "priority": priority,
        "maxAttempts": max_attempts,
        "payload": {
            "repository": repo,
            "task": {
                "instructions": f"Resolve PR #{pr_number} on branch `{branch}`.",
                "skill": {
                    "id": "pr-resolver",
                    "args": {
                        "repo": repo,
                        "pr": str(pr_number),
                        "branch": branch,
                        "mergeMethod": merge_method,
                        "maxIterations": max_iterations,
                    },
                    "requiredCapabilities": ["gh"],
                },
                "runtime": {"mode": "codex"},
                "git": {
                    "startingBranch": branch,
                },
                "publish": {"mode": "none"},
            },
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit pr-resolver tasks for every open PR in a repository."
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Target repository in owner/repo format.",
    )
    parser.add_argument(
        "--state",
        default="open",
        help="PR state filter (default: open).",
    )
    parser.add_argument(
        "--include-forks",
        action="store_true",
        default=False,
        help="Include fork PRs when creating tasks.",
    )
    parser.add_argument(
        "--skip-existing-only",
        action="store_true",
        default=False,
        help="Deprecated compatibility alias; kept for older callers.",
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--merge-method", default="squash")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Directory to write artifacts to.",
    )
    return parser.parse_args()


async def _submit_jobs(
    queue_requests: list[JobSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    async with get_async_session_context() as session:
        service = get_agent_queue_service(session)
        for submission in queue_requests:
            request = submission.queue_request
            payload = request["payload"]
            queue_type = str(request["type"])
            priority = int(request.get("priority", 0))
            max_attempts = int(request.get("maxAttempts", 3))
            try:
                job = await service.create_job(
                    job_type=queue_type,
                    payload=payload,
                    priority=priority,
                    max_attempts=max_attempts,
                )
                created.append(
                    {
                        "pr": submission.pr_number,
                        "branch": submission.branch,
                        "jobId": str(job.id),
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "pr": submission.pr_number,
                        "branch": submission.branch,
                        "error": str(exc),
                    }
                )
        return created, errors


def _build_request_records(
    repo: str,
    open_prs: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queue_requests: list[JobSubmission] = []
    skipped: list[dict[str, Any]] = []

    if args.include_forks and args.skip_existing_only:
        raise RuntimeError(
            "--include-forks conflicts with --skip-existing-only; choose only one."
        )
    if args.include_forks:
        raise RuntimeError(
            "--include-forks is not supported for queued pr-resolver jobs because fork "
            "head branches are not reliably check-outable by the worker."
        )

    for pr in open_prs:
        number = pr.get("number")
        branch = _extract_branch(pr)
        if not _is_local_head(pr, repo=repo):
            skipped.append({"pr": number, "branch": branch, "reason": "fork-pr"})
            continue

        queue_request = _build_queue_request(
            repo,
            number,
            branch,
            merge_method=args.merge_method,
            max_iterations=args.max_iterations,
            priority=args.priority,
            max_attempts=args.max_attempts,
        )
        queue_requests.append(
            JobSubmission(queue_request=queue_request, pr_number=number, branch=branch)
        )

    return queue_requests, skipped


def _write_artifacts(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


async def main() -> int:
    args = _parse_args()
    repo = _resolve_repo(args.repo)
    if not repo:
        raise RuntimeError("No repository provided and none could be inferred.")
    if args.state.strip() != "open":
        print(f"warning: non-open state requested: {args.state}")

    open_prs = _run_pr_list(repo=repo, state=args.state)
    queue_requests, skipped = _build_request_records(repo, open_prs, args)
    created, errors = await _submit_jobs(queue_requests)

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": os.getenv("GITHUB_ACTOR") or os.getenv("USER") or "unknown",
        "repository": repo,
        "state": args.state,
        "requested": len(open_prs),
        "created": len(created),
        "queued": created,
        "skipped": skipped,
        "errors": errors,
    }
    if payload["created"] == 0:
        payload["message"] = "No matching PRs were queued."

    artifacts_path = Path(args.artifacts_dir) / "batch_pr_resolver_result.json"
    _write_artifacts(artifacts_path, payload)

    print(json.dumps(payload, indent=2))
    print(
        f"queued={payload['created']} skipped={len(skipped)} errors={len(errors)} "
        f"repo={repo} state={args.state}"
    )
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        print("error: batch-pr-resolver failed. See logs for details.", flush=True)
        raise SystemExit(1)
