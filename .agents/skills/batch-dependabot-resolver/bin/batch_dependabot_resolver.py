#!/usr/bin/env python3
"""Discover open Dependabot version-bump PRs and enqueue one `pr-resolver` task each.

MM-803: Create batch-dependabot-resolver skill.

This skill is intentionally a narrower discovery/filter layer on top of the same
`pr-resolver` child mechanism used by `batch-pr-resolver`. It only matches PRs
that are:

* open,
* not forks / cross-repository PRs,
* authored by ``dependabot[bot]``,
* on a Dependabot-owned branch (``dependabot/...`` by default), and
* titled like a standard version bump (``Bump <dep> from <old> to <new>``).

Each matching PR is submitted as a ``pr-resolver`` workflow using the same
canonical payload shape as ``batch-pr-resolver``. A stable idempotency key based
on repository, PR number, and head SHA prevents duplicate resolver workflows for
the same unchanged Dependabot PR across separate scheduled runs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
from moonmind.workflows.tasks.runtime_defaults import normalize_runtime_id
from moonmind.workflows.tasks.task_contract import resolve_publish_mode_for_skill

logger = logging.getLogger(__name__)

API_EXECUTIONS_ENDPOINT = "/api/executions"
IDEMPOTENCY_KEY_MAX_LENGTH = 128
IDEMPOTENCY_KEY_PREFIX = "batch-dependabot-resolver"

DEPENDABOT_AUTHOR_LOGIN = "dependabot[bot]"
DEPENDABOT_BRANCH_PREFIX = "dependabot/"
DEFAULT_TITLE_REGEX = r"^Bump .+ from \S+ to \S+$"
SECURITY_LABEL_NAMES = frozenset({"security"})

# Light alias normalization so an operator-supplied package-manager allowlist
# (e.g. ``npm`` or ``github-actions``) matches the ecosystem segment Dependabot
# actually encodes in the branch name (e.g. ``npm_and_yarn``/``github_actions``).
_PACKAGE_MANAGER_ALIASES = {
    "npm": "npm_and_yarn",
    "yarn": "npm_and_yarn",
    "npmandyarn": "npm_and_yarn",
    "githubactions": "github_actions",
    "actions": "github_actions",
    "pip": "pip",
    "pipenv": "pip",
    "poetry": "pip",
    "uv": "pip",
    "gomod": "gomod",
    "go": "gomod",
    "cargo": "cargo",
    "docker": "docker",
    "bundler": "bundler",
    "composer": "composer",
    "gradle": "gradle",
    "maven": "maven",
    "nuget": "nuget",
}


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
            ",".join(
                [
                    "number",
                    "title",
                    "author",
                    "headRefName",
                    "headRefOid",
                    "headRepository",
                    "headRepositoryOwner",
                    "isCrossRepository",
                    "labels",
                ]
            ),
            "--limit",
            "100000",
        ]
    )
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise RuntimeError("Unexpected `gh pr list` payload shape.")
    return parsed


def _parse_repo_parts(repo: str) -> tuple[str, str]:
    parts = (repo or "").strip().split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


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


def _extract_branch(pr: dict[str, Any]) -> str:
    branch = str(pr.get("headRefName") or "").strip()
    if not branch:
        raise RuntimeError(f"PR #{pr.get('number')}: missing headRefName")
    return branch


def _extract_head_sha(pr: dict[str, Any]) -> str:
    head_sha = str(pr.get("headRefOid") or "").strip()
    if not head_sha:
        raise RuntimeError(f"PR #{pr.get('number')}: missing headRefOid")
    return head_sha


def _author_login(pr: dict[str, Any]) -> str:
    author = pr.get("author")
    if isinstance(author, dict):
        return str(author.get("login") or "").strip()
    return ""


def _is_dependabot_author(pr: dict[str, Any]) -> bool:
    return _author_login(pr).lower() == DEPENDABOT_AUTHOR_LOGIN


def _is_dependabot_branch(branch: str) -> bool:
    return str(branch or "").strip().lower().startswith(DEPENDABOT_BRANCH_PREFIX)


def _title_matches(title: str, title_regex: str) -> bool:
    candidate = str(title or "").strip()
    if not candidate:
        return False
    try:
        return re.match(title_regex, candidate) is not None
    except re.error as exc:
        raise RuntimeError(f"invalid title regex: {title_regex!r}: {exc}") from exc


def _branch_package_manager(branch: str) -> str | None:
    """Return the Dependabot ecosystem segment encoded in the branch name."""

    candidate = str(branch or "").strip()
    if not candidate.lower().startswith(DEPENDABOT_BRANCH_PREFIX):
        return None
    remainder = candidate[len(DEPENDABOT_BRANCH_PREFIX) :]
    segment = remainder.split("/", 1)[0].strip()
    return segment or None


def _normalize_package_manager(value: str) -> str:
    collapsed = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    return _PACKAGE_MANAGER_ALIASES.get(collapsed, collapsed)


def _package_manager_matches(branch: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return True
    segment = _branch_package_manager(branch)
    if not segment:
        return False
    normalized_segment = _normalize_package_manager(segment)
    allowed = {_normalize_package_manager(entry) for entry in allowlist if entry}
    return normalized_segment in allowed


def _is_security_update(pr: dict[str, Any]) -> bool:
    labels = pr.get("labels")
    if not isinstance(labels, list):
        return False
    for label in labels:
        if isinstance(label, dict):
            name = str(label.get("name") or "").strip().lower()
        else:
            name = str(label or "").strip().lower()
        if name in SECURITY_LABEL_NAMES:
            return True
    return False


def _classify_pr(
    pr: dict[str, Any],
    *,
    repo: str,
    title_regex: str,
    package_managers: list[str],
    include_security_updates: bool,
) -> str | None:
    """Return a skip reason for a non-matching PR, or ``None`` when it matches."""

    if not _is_local_head(pr, repo=repo):
        return "fork-pr"
    if not _is_dependabot_author(pr):
        return "non-dependabot-author"
    branch = _extract_branch(pr)
    if not _is_dependabot_branch(branch):
        return "non-dependabot-branch"
    if not _title_matches(str(pr.get("title") or ""), title_regex):
        return "non-version-bump-title"
    if not _package_manager_matches(branch, package_managers):
        return "package-manager-not-allowed"
    if not include_security_updates and _is_security_update(pr):
        return "security-update-excluded"
    return None


def _normalize_runtime_mode(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate if candidate else None


def _runtime_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _runtime_modes_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_runtime_id(left) == normalize_runtime_id(right)


def _session_artifact_spool_path() -> Path | None:
    raw = _runtime_text(os.getenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"))
    if not raw:
        return None
    return Path(raw)


def _resolve_path(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except OSError:
        return path


def _default_artifacts_dir_requested(raw_artifacts_dir: str) -> bool:
    raw = str(raw_artifacts_dir or "").strip()
    if not raw:
        return True
    candidate = Path(raw)
    return not candidate.is_absolute() and candidate.parts == ("artifacts",)


def _artifacts_dir_from_task_context_path(path: Path) -> Path | None:
    resolved = _resolve_path(path)
    if resolved.name == "task_context.json" and resolved.parent.name == "artifacts":
        return resolved.parent
    return None


def _resolve_artifacts_dir(
    raw_artifacts_dir: str,
    task_context_path: str | None = None,
) -> Path:
    raw = str(raw_artifacts_dir or "").strip()
    if not _default_artifacts_dir_requested(raw):
        return Path(raw)

    spool_path = _session_artifact_spool_path()
    if spool_path is not None:
        return spool_path

    for candidate in _repo_context_candidates(task_context_path):
        artifacts_dir = _artifacts_dir_from_task_context_path(candidate)
        if artifacts_dir is not None:
            return artifacts_dir

    return Path(raw or "artifacts")


def _child_idempotency_key(
    *,
    repo: str,
    pr_number: int | str,
    head_sha: str,
) -> str:
    """Return a stable cross-run idempotency key for a Dependabot PR.

    The key is stable across separate scheduled runs for the same PR at the same
    head commit, so an unchanged Dependabot PR only ever gets one resolver. When
    Dependabot rebases or updates the PR the head SHA changes and a new resolver
    is allowed.
    """

    head = str(head_sha or "").strip()
    if not head:
        raise RuntimeError("cannot build idempotency key without a head SHA")

    literal = f"{IDEMPOTENCY_KEY_PREFIX}:{repo}:pr:{pr_number}:head:{head}"
    if len(literal) <= IDEMPOTENCY_KEY_MAX_LENGTH:
        return literal

    # Fall back to a hashed variant for unusually long repository names while
    # keeping the same stable inputs (repo, PR number, head SHA).
    canonical = json.dumps(
        {"repo": repo, "pr": str(pr_number), "head": head},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    key = f"{IDEMPOTENCY_KEY_PREFIX}:pr:{pr_number}:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise RuntimeError("generated child idempotency key exceeds storage limit")
    return key


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
            runtime_config.get("providerProfile")
            or runtime_config.get("profileId")
            or runtime_node.get("providerProfile")
            or runtime_node.get("profileId")
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
    runtime_execution_profile_ref = _runtime_text(
        os.getenv("MOONMIND_EXECUTION_PROFILE_REF")
    )
    runtime_execution_profile_runtime = _runtime_text(
        os.getenv("MOONMIND_EXECUTION_PROFILE_RUNTIME")
    )
    # Resolution order, most to least specific: explicit args, the parent
    # runtime copied from task_context.json, the caller's own execution profile
    # (the runtime this skill is currently executing under), and finally the
    # generic system default. The execution profile must beat
    # MOONMIND_DEFAULT_TASK_RUNTIME so children inherit the caller's runtime.
    execution_profile_mode = (
        _normalize_runtime_mode(runtime_execution_profile_runtime)
        if runtime_execution_profile_ref
        else None
    )
    runtime_mode = (
        _normalize_runtime_mode(args.runtime_mode)
        or (inherited.mode if inherited else None)
        or execution_profile_mode
        or configured_default_mode
    )
    runtime_model = _runtime_text(args.runtime_model)
    runtime_effort = _runtime_text(args.runtime_effort)
    runtime_provider_profile = _runtime_text(
        getattr(args, "runtime_provider_profile", None)
    )
    if runtime_model is None and inherited is not None:
        runtime_model = inherited.model
    if runtime_effort is None and inherited is not None:
        runtime_effort = inherited.effort
    if runtime_provider_profile is None and inherited is not None:
        runtime_provider_profile = inherited.provider_profile
    if runtime_provider_profile is None and _runtime_modes_match(
        runtime_mode,
        runtime_execution_profile_runtime,
    ):
        runtime_provider_profile = runtime_execution_profile_ref

    return RuntimeSelection(
        mode=runtime_mode,
        model=runtime_model,
        effort=runtime_effort,
        provider_profile=runtime_provider_profile,
    )


def _task_workflow_id_from_env() -> str | None:
    """Return the parent task workflow id when the skill runs inside one."""

    for env_key in (
        "MOONMIND_TASK_WORKFLOW_ID",
        "MOONMIND_WORKFLOW_ID",
        "TEMPORAL_WORKFLOW_ID",
    ):
        value = _runtime_text(os.getenv(env_key))
        if value:
            return value
    return None


def _task_run_id_from_env() -> str | None:
    for env_key in ("MOONMIND_TASK_RUN_ID", "MOONMIND_RUN_ID", "TASK_RUN_ID"):
        value = _runtime_text(os.getenv(env_key))
        if value:
            return value
    return None


def _build_queue_request(
    repo: str,
    pr_number: int | str,
    branch: str,
    head_sha: str,
    *,
    runtime: RuntimeSelection,
    merge_method: str,
    max_iterations: int,
    priority: int,
    max_attempts: int,
    skill_version: str = "1.0",
    inherit_runtime_from_caller: bool = False,
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
        runtime_payload["executionProfileRef"] = runtime.provider_profile

    payload_dict: dict[str, Any] = {
        "repository": repo,
        "requiredCapabilities": ["gh"],
        "task": {
            "title": branch,
            "instructions": f"Resolve Dependabot PR #{pr_number} on branch `{branch}`.",
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
    payload_dict["idempotencyKey"] = _child_idempotency_key(
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
    )

    # Server-side inheritance contract: when running inside a task with a
    # task-scoped credential, opt into runtimeInheritance="caller" so the API
    # copies the parent's effective runtime/provider profile. The explicit
    # targetRuntime/task.runtime fallback below is preserved for deployments
    # that do not yet honour the inheritance contract.
    if inherit_runtime_from_caller:
        payload_dict["runtimeInheritance"] = "caller"

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


def _parse_package_managers(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,\s]+", str(value).strip())
    return [part for part in parts if part]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Submit pr-resolver tasks for every open Dependabot version-bump PR "
            "in a repository."
        )
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
        "--title-regex",
        default=DEFAULT_TITLE_REGEX,
        help=(
            "Regex matched against PR titles to identify version bumps "
            f"(default: {DEFAULT_TITLE_REGEX!r})."
        ),
    )
    parser.add_argument(
        "--package-managers",
        default=None,
        help=(
            "Optional comma-separated allowlist of Dependabot package managers "
            "(e.g. pip,npm,github-actions). When omitted, all are allowed."
        ),
    )
    parser.add_argument(
        "--include-security-updates",
        dest="include_security_updates",
        action="store_true",
        default=True,
        help="Include Dependabot security-update PRs (default: true).",
    )
    parser.add_argument(
        "--exclude-security-updates",
        dest="include_security_updates",
        action="store_false",
        help="Skip PRs labelled as security updates.",
    )
    parser.add_argument(
        "--max-prs",
        type=int,
        default=None,
        help="Optional safety cap on the number of resolver workflows to queue.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Discover and match PRs but do not submit resolver workflows.",
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
    # Assert task identity so the executions API can grant the
    # runtime-inheritance scopes when the request includes
    # runtimeInheritance="caller".
    task_workflow_id = _task_workflow_id_from_env()
    if task_workflow_id:
        headers["X-MoonMind-Task-Workflow-Id"] = task_workflow_id
    task_run_id = _task_run_id_from_env()
    if task_run_id:
        headers["X-MoonMind-Task-Run-Identifier"] = task_run_id
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
                job_id = (
                    str(
                        data.get("workflowId")
                        or data.get("taskId")
                        or data.get("id")
                        or ""
                    )
                    or "(unknown)"
                )
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


async def _submit_jobs(
    queue_requests: list[JobSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Submit jobs through the MoonMind Temporal execution API."""
    moonmind_url = str(os.getenv("MOONMIND_URL", "")).strip()
    if moonmind_url:
        worker_token = _read_worker_token()
        return await _submit_jobs_via_http(
            queue_requests,
            moonmind_url=moonmind_url,
            worker_token=worker_token,
        )

    message = (
        "MOONMIND_URL is not set; batch-dependabot-resolver requires the MoonMind "
        "Temporal execution API and cannot submit via the removed legacy DB queue."
    )
    return [], [
        {
            "pr": submission.pr_number,
            "branch": submission.branch,
            "error": message,
        }
        for submission in queue_requests
    ]


def _build_request_records(
    repo: str,
    open_prs: list[dict[str, Any]],
    args: argparse.Namespace,
    runtime: RuntimeSelection,
) -> tuple[list[JobSubmission], list[dict[str, Any]], list[dict[str, Any]]]:
    queue_requests: list[JobSubmission] = []
    matched: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    package_managers = _parse_package_managers(args.package_managers)

    def _get_pr_number(pr_data: dict[str, Any]) -> int:
        try:
            return int(pr_data.get("number", 0))
        except (ValueError, TypeError):
            return 0

    open_prs_sorted = sorted(open_prs, key=_get_pr_number)
    inherit_from_caller = _task_workflow_id_from_env() is not None
    max_prs = args.max_prs if args.max_prs is not None and args.max_prs >= 0 else None

    for pr in open_prs_sorted:
        number = pr.get("number")
        branch = str(pr.get("headRefName") or "").strip()
        reason = _classify_pr(
            pr,
            repo=repo,
            title_regex=args.title_regex,
            package_managers=package_managers,
            include_security_updates=args.include_security_updates,
        )
        if reason is not None:
            skipped.append({"pr": number, "branch": branch, "reason": reason})
            continue

        if max_prs is not None and len(matched) >= max_prs:
            skipped.append({"pr": number, "branch": branch, "reason": "max-prs-cap"})
            continue

        head_sha = _extract_head_sha(pr)
        matched.append({"pr": number, "branch": branch, "headSha": head_sha})

        queue_request = _build_queue_request(
            repo,
            number,
            branch,
            head_sha,
            runtime=runtime,
            merge_method=args.merge_method,
            max_iterations=args.max_iterations,
            priority=args.priority,
            max_attempts=args.max_attempts,
            skill_version=args.skill_version,
            inherit_runtime_from_caller=inherit_from_caller,
        )
        queue_requests.append(
            JobSubmission(queue_request=queue_request, pr_number=number, branch=branch)
        )

    return queue_requests, matched, skipped


def _write_artifacts(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _write_run_artifacts(artifacts_dir: Path, payload: dict[str, Any]) -> None:
    """Write the result payload and (when applicable) the no-op outcome file.

    A ``skill_outcome.json`` with ``status: "no_op"`` is written only when the
    run produced zero queued executions AND encountered no errors — i.e. the run
    is a deliberate no-op (no matching Dependabot PRs), not a failed-to-do
    anything case. Dry-run executions are likewise treated as intentional no-ops.
    """
    _write_artifacts(
        artifacts_dir / "batch_dependabot_resolver_result.json", payload
    )
    if payload["created"] == 0 and not payload["errors"]:
        reason = "dry_run" if payload.get("dryRun") else "no_dependabot_prs_matched"
        _write_artifacts(
            artifacts_dir / "skill_outcome.json",
            {
                "schema_version": 1,
                "status": "no_op",
                "reason": reason,
                "evidence": {
                    "discovered": payload["discovered"],
                    "matched": payload["matched"],
                    "skipped": payload["skipped"],
                },
            },
        )


async def main() -> int:
    args = _parse_args()
    repo = _resolve_repo(args.repo, args.task_context_path)
    if not repo:
        raise RuntimeError("No repository provided and none could be inferred.")
    if args.state.strip() != "open":
        print(f"warning: non-open state requested: {args.state}")
    runtime = _resolve_runtime_selection(args)

    open_prs = _run_pr_list(repo=repo, state=args.state)
    queue_requests, matched, skipped = _build_request_records(
        repo, open_prs, args, runtime
    )

    if args.dry_run:
        created: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        planned = [
            {
                "pr": submission.pr_number,
                "branch": submission.branch,
                "idempotencyKey": submission.queue_request["payload"].get(
                    "idempotencyKey"
                ),
            }
            for submission in queue_requests
        ]
    else:
        created, errors = await _submit_jobs(queue_requests)
        planned = []

    discovered_prs = [
        {
            "pr": pr.get("number"),
            "title": pr.get("title"),
            "author": _author_login(pr),
            "branch": str(pr.get("headRefName") or "").strip(),
        }
        for pr in open_prs
    ]

    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": os.getenv("GITHUB_ACTOR") or os.getenv("USER") or "unknown",
        "repository": repo,
        "state": args.state,
        "dryRun": bool(args.dry_run),
        "titleRegex": args.title_regex,
        "packageManagers": _parse_package_managers(args.package_managers),
        "includeSecurityUpdates": bool(args.include_security_updates),
        "maxPrs": args.max_prs,
        "runtime": {
            "mode": runtime.mode,
            "model": runtime.model,
            "effort": runtime.effort,
            "executionProfileRef": runtime.provider_profile,
        },
        "discovered": len(open_prs),
        "discoveredPrs": discovered_prs,
        "matched": len(matched),
        "matchedPrs": matched,
        "created": len(created),
        "queued": created,
        "planned": planned,
        "skipped": skipped,
        "errors": errors,
    }
    if payload["created"] == 0 and not args.dry_run:
        payload["message"] = "No matching Dependabot PRs were queued."

    artifacts_dir = _resolve_artifacts_dir(args.artifacts_dir, args.task_context_path)
    _write_run_artifacts(artifacts_dir, payload)

    print(json.dumps(payload, indent=2))
    print(
        f"discovered={payload['discovered']} matched={payload['matched']} "
        f"queued={payload['created']} skipped={len(skipped)} errors={len(errors)} "
        f"dryRun={payload['dryRun']} repo={repo} state={args.state}"
    )
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        print(
            "error: batch-dependabot-resolver failed. See logs for details.",
            flush=True,
        )
        raise SystemExit(1)
