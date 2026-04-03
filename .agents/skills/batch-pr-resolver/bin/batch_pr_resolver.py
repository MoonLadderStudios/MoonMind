#!/usr/bin/env python3
"""Create `pr-resolver` tasks for each open PR in a repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from moonmind.workflows.tasks.task_contract import resolve_publish_mode_for_skill


logger = logging.getLogger(__name__)

API_EXECUTIONS_ENDPOINT = "/api/executions"


@dataclass
class JobSubmission:
    queue_request: dict[str, Any]
    pr_number: int | str
    branch: str


@dataclass(frozen=True)
class RuntimeSelection:
    mode: str | None = None
    model: str | None = None
    effort: str | None = None
    provider_profile: str | None = None


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


def _repo_context_candidates(task_context_path: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if task_context_path:
        candidates.append(Path(task_context_path))
    for env_key in ("MOONMIND_TASK_CONTEXT_PATH", "TASK_CONTEXT_PATH"):
        env_value = str(os.getenv(env_key, "")).strip()
        if env_value:
            candidates.append(Path(env_value))
    candidates.extend(
        [Path("../artifacts/task_context.json"), Path("artifacts/task_context.json")]
    )
    return candidates


def _load_parent_repository(task_context_path: str | None = None) -> str | None:
    seen: set[str] = set()
    for candidate in _repo_context_candidates(task_context_path):
        identity = str(candidate.expanduser())
        if identity in seen:
            continue
        seen.add(identity)
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        normalized = _normalize_repo(payload.get("repository"))
        if normalized:
            return normalized
    return None


def _resolve_repo(raw_repo: str | None, task_context_path: str | None = None) -> str:
    if raw_repo is not None:
        normalized = _normalize_repo(raw_repo)
        if normalized:
            return normalized
        raise RuntimeError("Invalid --repo value; expected owner/repo format.")

    from_context = _load_parent_repository(task_context_path)
    if from_context:
        return from_context

    inferred_repo = _infer_repo_from_remote()
    if inferred_repo:
        return inferred_repo

    for env_key in ("WORKFLOW_GITHUB_REPOSITORY", "GITHUB_REPOSITORY", "MOONMIND_REPO"):
        normalized = _normalize_repo(os.getenv(env_key, ""))
        if normalized:
            return normalized
    return ""


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
            "number,title,headRefName,headRepositoryOwner,headRepository,isCrossRepository",
            "--limit",
            "100000",
        ]
    )
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("Unexpected `gh pr list` payload shape.")
    return parsed


def _is_local_head(pr: dict[str, Any], repo: str) -> bool:
    target_repo = repo.strip().lower()
    target_owner, target_repo_name = _parse_repo_parts(target_repo)

    is_cross = pr.get("isCrossRepository")
    if isinstance(is_cross, bool) and is_cross:
        return False

    head_repo = pr.get("headRepository")
    if isinstance(head_repo, dict):
        name_with_owner = str(head_repo.get("nameWithOwner") or "").strip().lower()
        if name_with_owner:
            return name_with_owner == target_repo

        head_repo_name = str(head_repo.get("name") or "").strip().lower()
        if head_repo_name:
            head_owner = (
                str(
                    (
                        pr.get("headRepositoryOwner")
                        if isinstance(pr.get("headRepositoryOwner"), dict)
                        else {}
                    ).get("login", "")
                )
                .strip()
                .lower()
            )
            if head_owner:
                return head_owner == target_owner and head_repo_name == target_repo_name

            return False

    owner_obj = pr.get("headRepositoryOwner")
    if isinstance(owner_obj, dict):
        head_owner = str(owner_obj.get("login") or "").strip().lower()
        if head_owner:
            return head_owner == target_owner

    return False


def _parse_repo_parts(repo: str) -> tuple[str, str]:
    parts = (repo or "").strip().split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _extract_branch(pr: dict[str, Any]) -> str:
    branch = str(pr.get("headRefName") or "").strip()
    if not branch:
        raise RuntimeError(f"PR #{pr.get('number')}: missing headRefName")
    return branch


def _normalize_runtime_mode(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate else None


def _runtime_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _load_parent_runtime_selection(
    task_context_path: str | None = None,
) -> RuntimeSelection | None:
    candidates: list[Path] = []
    if task_context_path:
        candidates.append(Path(task_context_path))
    for env_key in ("MOONMIND_TASK_CONTEXT_PATH", "TASK_CONTEXT_PATH"):
        env_value = _runtime_text(os.getenv(env_key))
        if env_value:
            candidates.append(Path(env_value))
    candidates.extend(
        [Path("../artifacts/task_context.json"), Path("artifacts/task_context.json")]
    )

    seen: set[str] = set()
    for candidate in candidates:
        identity = str(candidate.expanduser())
        if identity in seen:
            continue
        seen.add(identity)
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        runtime_config = (
            payload.get("runtimeConfig")
            if isinstance(payload.get("runtimeConfig"), dict)
            else {}
        )
        runtime_node = (
            payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
        )
        mode = _normalize_runtime_mode(
            runtime_config.get("mode")
            or runtime_node.get("mode")
            or payload.get("runtime")
        )
        if not mode:
            continue

        model = _runtime_text(runtime_config.get("model") or runtime_node.get("model"))
        effort = _runtime_text(
            runtime_config.get("effort") or runtime_node.get("effort")
        )
        provider_profile = _runtime_text(
            runtime_config.get("providerProfile") or runtime_node.get("providerProfile")
        )
        return RuntimeSelection(
            mode=mode, model=model, effort=effort, provider_profile=provider_profile
        )
    return None


def _resolve_runtime_selection(args: argparse.Namespace) -> RuntimeSelection:
    inherited = _load_parent_runtime_selection(args.task_context_path)
    configured_default_mode = _normalize_runtime_mode(
        os.getenv("MOONMIND_DEFAULT_TASK_RUNTIME")
    )
    runtime_mode = _normalize_runtime_mode(args.runtime_mode) or (
        inherited.mode if inherited else configured_default_mode
    )
    runtime_model = _runtime_text(args.runtime_model)
    runtime_effort = _runtime_text(args.runtime_effort)
    runtime_provider_profile = _runtime_text(getattr(args, "runtime_provider_profile", None))
    if runtime_model is None and inherited is not None:
        runtime_model = inherited.model
    if runtime_effort is None and inherited is not None:
        runtime_effort = inherited.effort
    if runtime_provider_profile is None and inherited is not None:
        runtime_provider_profile = inherited.provider_profile

    return RuntimeSelection(
        mode=runtime_mode,
        model=runtime_model,
        effort=runtime_effort,
        provider_profile=runtime_provider_profile,
    )


def _build_queue_request(
    repo: str,
    pr_number: int | str,
    branch: str,
    *,
    runtime: RuntimeSelection,
    merge_method: str,
    max_iterations: int,
    priority: int,
    max_attempts: int,
    skill_version: str = "1.0",
) -> dict[str, Any]:
    publish_mode = resolve_publish_mode_for_skill("pr-resolver", "none")
    runtime_payload: dict[str, Any] = {}
    if runtime.mode:
        runtime_payload["mode"] = runtime.mode
    if runtime.model:
        runtime_payload["model"] = runtime.model
    if runtime.effort:
        runtime_payload["effort"] = runtime.effort
    if runtime.provider_profile:
        runtime_payload["providerProfile"] = runtime.provider_profile

    payload_dict: dict[str, Any] = {
        "repository": repo,
        "requiredCapabilities": ["gh"],
        "task": {
            "instructions": f"Resolve PR #{pr_number} on branch `{branch}`.",
            "skill": {
                "name": "pr-resolver",
                "version": skill_version,
            },
            "inputs": {
                "repo": repo,
                "pr": str(pr_number),
                "branch": branch,
                "mergeMethod": merge_method,
                "maxIterations": max_iterations,
            },
            "git": {
                "startingBranch": branch,
                "targetBranch": branch,
            },
            "publish": {"mode": publish_mode},
        },
    }

    if runtime.mode:
        payload_dict["targetRuntime"] = runtime.mode
    if runtime_payload:
        payload_dict["task"]["runtime"] = runtime_payload

    request: dict[str, Any] = {
        "type": "task",
        "priority": priority,
        "maxAttempts": max_attempts,
        "payload": payload_dict,
    }

    return request


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
    parser.add_argument(
        "--task-context-path",
        default=None,
        help=(
            "Optional path to parent task_context.json for runtime inheritance "
            "(default: auto-detect ../artifacts/task_context.json)."
        ),
    )
    parser.add_argument(
        "--runtime-mode",
        default=None,
        help="Explicit runtime mode for queued pr-resolver tasks.",
    )
    parser.add_argument(
        "--runtime-model",
        default=None,
        help="Explicit runtime model for queued pr-resolver tasks.",
    )
    parser.add_argument(
        "--runtime-effort",
        default=None,
        help="Explicit runtime effort for queued pr-resolver tasks.",
    )
    parser.add_argument(
        "--runtime-provider-profile",
        default=None,
        help="Explicit runtime provider profile for queued pr-resolver tasks.",
    )
    parser.add_argument("--merge-method", default="squash")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument(
        "--skill-version",
        default="1.0",
        help="Skill registry version for the pr-resolver skill (default: 1.0).",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="artifacts",
        help="Directory to write artifacts to.",
    )
    return parser.parse_args()


def _read_worker_token() -> str | None:
    """Read the MoonMind worker token from env or token file."""
    token = str(os.getenv("MOONMIND_WORKER_TOKEN", "")).strip()
    if token:
        return token
    token_file = str(os.getenv("MOONMIND_WORKER_TOKEN_FILE", "")).strip()
    if token_file:
        path = Path(token_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    return None


async def _submit_jobs_via_http(
    queue_requests: list[JobSubmission],
    *,
    moonmind_url: str,
    worker_token: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Submit jobs to the MoonMind queue API (Temporal-aware path)."""
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if worker_token:
        headers["X-MoonMind-Worker-Token"] = worker_token
    base = moonmind_url.rstrip("/")
    async with httpx.AsyncClient(
        base_url=base, timeout=30.0, headers=headers
    ) as client:
        for submission in queue_requests:
            request = submission.queue_request
            body = {
                "type": str(request["type"]),
                "payload": request["payload"],
                "priority": int(request.get("priority", 0)),
                "maxAttempts": int(request.get("maxAttempts", 3)),
            }
            try:
                response = await client.post(API_EXECUTIONS_ENDPOINT, json=body)
                response.raise_for_status()
                data = response.json()
                job_id = str(data.get("taskId", "")) or "(unknown)"
                created.append(
                    {
                        "pr": submission.pr_number,
                        "branch": submission.branch,
                        "jobId": job_id,
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


async def _submit_jobs_via_db(
    queue_requests: list[JobSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fallback: submit jobs directly to the DB queue (skips Temporal routing)."""
    from api_service.db.base import get_async_session_context
    from moonmind.workflows import get_agent_queue_service

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
            
            kwargs = {
                "job_type": queue_type,
                "payload": payload,
                "priority": priority,
                "max_attempts": max_attempts,
            }

            try:
                job = await service.create_job(**kwargs)
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


async def _submit_jobs(
    queue_requests: list[JobSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Submit jobs via the MoonMind HTTP API (Temporal-aware), with DB fallback."""
    moonmind_url = str(os.getenv("MOONMIND_URL", "")).strip()
    if moonmind_url:
        worker_token = _read_worker_token()
        return await _submit_jobs_via_http(
            queue_requests,
            moonmind_url=moonmind_url,
            worker_token=worker_token,
        )
    # Fallback for environments without a running API (e.g. direct invocation).
    logger.warning(
        "MOONMIND_URL is not set; submitting jobs directly to the DB queue. "
        "This bypasses Temporal routing and should only be used in dev/test environments."
    )
    return await _submit_jobs_via_db(queue_requests)


def _build_request_records(
    repo: str,
    open_prs: list[dict[str, Any]],
    args: argparse.Namespace,
    runtime: RuntimeSelection,
) -> tuple[list[JobSubmission], list[dict[str, Any]]]:
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

    def _get_pr_number(pr_data: dict[str, Any]) -> int:
        try:
            return int(pr_data.get("number", 0))
        except (ValueError, TypeError):
            return 0

    open_prs_sorted = sorted(open_prs, key=_get_pr_number)

    for pr in open_prs_sorted:
        number = pr.get("number")
        branch = _extract_branch(pr)
        if not _is_local_head(pr, repo=repo):
            skipped.append({"pr": number, "branch": branch, "reason": "fork-pr"})
            continue

        queue_request = _build_queue_request(
            repo,
            number,
            branch,
            runtime=runtime,
            merge_method=args.merge_method,
            max_iterations=args.max_iterations,
            priority=args.priority,
            max_attempts=args.max_attempts,
            skill_version=args.skill_version,
        )
        queue_requests.append(
            JobSubmission(queue_request=queue_request, pr_number=number, branch=branch)
        )

    return queue_requests, skipped


def _write_artifacts(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _format_failure_message(exc: BaseException) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"error: batch-pr-resolver failed: {detail}"


async def main() -> int:
    args = _parse_args()
    repo = _resolve_repo(args.repo, args.task_context_path)
    if not repo:
        raise RuntimeError("No repository provided and none could be inferred.")
    if args.state.strip() != "open":
        print(f"warning: non-open state requested: {args.state}")
    runtime = _resolve_runtime_selection(args)

    open_prs = _run_pr_list(repo=repo, state=args.state)
    queue_requests, skipped = _build_request_records(repo, open_prs, args, runtime)
    created, errors = await _submit_jobs(queue_requests)

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": os.getenv("GITHUB_ACTOR") or os.getenv("USER") or "unknown",
        "repository": repo,
        "state": args.state,
        "runtime": {
            "mode": runtime.mode,
            "model": runtime.model,
            "effort": runtime.effort,
            "providerProfile": runtime.provider_profile,
        },
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
    except Exception as exc:
        print(_format_failure_message(exc), flush=True)
        raise SystemExit(1)
