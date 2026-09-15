#!/usr/bin/env python3
"""Resolve issue targets and queue one child MoonMind workflow per target.

The provider Skill resolves targets into the canonical resolved-target shape
and writes them to a JSON file. GitHub issue-number ranges are resolved directly
by this portable helper so a numeric range remains search criteria rather than
being mistaken for a target list. The helper submits one child execution per
resolved target through the internal Temporal execution API.
Every child inherits the parent runtime via ``runtimeInheritance="caller"``
(with a fallback copy of the effective runtime fields) and shares a single
publish policy. A summary artifact links every queued child workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SHARED_ROOT = Path(__file__).resolve().parent
_CLIENT_PATH = SHARED_ROOT / "workflow_execution_client.py"
_CLIENT_SPEC = importlib.util.spec_from_file_location(
    "batch_workflows_execution_client", _CLIENT_PATH
)
if _CLIENT_SPEC is None or _CLIENT_SPEC.loader is None:
    raise RuntimeError(f"resolved skill snapshot is missing portable client: {_CLIENT_PATH}")
_CLIENT = importlib.util.module_from_spec(_CLIENT_SPEC)
_CLIENT_SPEC.loader.exec_module(_CLIENT)
child_idempotency_key = _CLIENT.child_idempotency_key
normalize_publish_mode = _CLIENT.normalize_publish_mode
normalize_runtime_id = _CLIENT.normalize_runtime_id
validate_execution_envelope = _CLIENT.validate_execution_envelope

_BATCH_TARGETS_PATH = SHARED_ROOT / "repository_batch_targets.py"
_BATCH_TARGETS_SPEC = importlib.util.spec_from_file_location(
    "batch_workflows_repository_targets", _BATCH_TARGETS_PATH
)
if _BATCH_TARGETS_SPEC is None or _BATCH_TARGETS_SPEC.loader is None:
    raise RuntimeError(
        "resolved skill snapshot is missing portable batch targets: "
        f"{_BATCH_TARGETS_PATH}"
    )
_BATCH_TARGETS = importlib.util.module_from_spec(_BATCH_TARGETS_SPEC)
sys.modules[_BATCH_TARGETS_SPEC.name] = _BATCH_TARGETS
_BATCH_TARGETS_SPEC.loader.exec_module(_BATCH_TARGETS)

logger = logging.getLogger(__name__)

API_EXECUTIONS_ENDPOINT = "/api/executions"
IDEMPOTENCY_KEY_MAX_LENGTH = _CLIENT.IDEMPOTENCY_KEY_MAX_LENGTH
PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE = "pr_with_merge_automation"
SUPPORTED_RUN_REFS = frozenset(
    {
        ("jira", "skill", "jira-verify"),
        ("jira", "preset", "jira-implement"),
        ("jira", "preset", "jira-orchestrate"),
        ("github", "preset", "github-issue-implement"),
        ("github", "preset", "github-issue-orchestrate"),
    }
)
_GITHUB_RANGE_PATTERN = re.compile(r"^(?P<start>\d+)-(?P<end>\d+)$")
_GITHUB_GRAPHQL_CHUNK_SIZE = 50
_GITHUB_MAX_SEARCH_WIDTH = 1000


class BatchInputError(ValueError):
    """Raised when provider-specific batch search input is invalid."""


@dataclass
class ChildSubmission:
    queue_request: dict[str, Any]
    provider: str
    ref: str


@dataclass
class SkippedTarget:
    ref: str
    reason: str


@dataclass(frozen=True)
class RuntimeSelection:
    mode: str | None = None
    model: str | None = None
    effort: str | None = None
    provider_profile: str | None = None


@dataclass
class TargetConfig:
    target_kind: str
    target_slug: str
    publish_mode: str = "pr"
    constraints: str = ""
    run_verify: bool = True
    update_status: bool = False
    required_capabilities: list[str] = field(default_factory=list)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _normalize_publish_mode(value: str | None) -> str:
    return normalize_publish_mode(value)


def _publish_payload_for_mode(publish_mode: str) -> dict[str, Any]:
    if publish_mode == PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE:
        return {
            "mode": "pr",
            "mergeAutomation": {"enabled": True},
        }
    return {"mode": publish_mode}


def _normalize_repo(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    return candidate or None


def _git_branch_is_valid(value: Any) -> bool:
    """Validate a branch with Git's canonical ref-name implementation."""

    branch = _text(value)
    if branch is None:
        return False
    try:
        completed = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("Git branch validation is unavailable") from exc
    return completed.returncode == 0


def parse_github_issue_range(value: str) -> tuple[int, int]:
    """Parse an inclusive GitHub issue-number search range."""

    candidate = str(value or "").strip()
    match = _GITHUB_RANGE_PATTERN.fullmatch(candidate)
    if match is None:
        raise BatchInputError("GitHub issue range must use START-END")
    start = int(match.group("start"))
    end = int(match.group("end"))
    if start <= 0 or end <= 0:
        raise BatchInputError("GitHub issue range numbers must be positive integers")
    if start > end:
        raise BatchInputError(
            "GitHub issue range START must be less than or equal to END"
        )
    return start, end


def _github_repository_parts(repository: str) -> tuple[str, str]:
    normalized = _normalize_repo(repository)
    parts = normalized.split("/") if normalized else []
    if len(parts) != 2 or not all(parts):
        raise BatchInputError("GitHub repository must use owner/repository")
    return parts[0], parts[1]


def _github_issue_range_query(numbers: list[int]) -> str:
    issue_fields = """
      number
      title
      body
      url
      state
      labels(first: 100) { nodes { name } }
    """.strip()
    selections = "\n".join(
        f"issue{number}: issue(number: {number}) {{ {issue_fields} }}"
        for number in numbers
    )
    return (
        "query($owner: String!, $name: String!) {\n"
        "  repository(owner: $owner, name: $name) {\n"
        "    defaultBranchRef { name }\n"
        f"{selections}\n"
        "  }\n"
        "}"
    )


def resolve_github_issue_range(
    repository: str,
    issue_range: str,
    *,
    connection_ref: str,
    run_command: Any = subprocess.run,
) -> list[dict[str, Any]]:
    """Return only open GitHub Issues whose numbers fall in ``issue_range``.

    GitHub's shared issue/PR numbering means numeric members of the range may be
    pull requests or may not exist. Querying the GraphQL ``issue(number:)`` field
    makes those entries resolve to null, so neither can become a workflow target.
    Closed issues and responses without an explicit open state are also omitted.
    """

    owner, name = _github_repository_parts(repository)
    normalized_repository = f"{owner}/{name}"
    normalized_connection_ref = _text(connection_ref)
    if normalized_connection_ref is None:
        raise BatchInputError(
            "GitHub issue discovery requires parent repository connection authority"
        )
    start, end = parse_github_issue_range(issue_range)
    search_width = end - start + 1
    if search_width > _GITHUB_MAX_SEARCH_WIDTH:
        raise BatchInputError(
            "GitHub issue range may span no more than "
            f"{_GITHUB_MAX_SEARCH_WIDTH} numbers"
        )
    targets: list[dict[str, Any]] = []
    resolved_default_branch: str | None = None

    for chunk_start in range(start, end + 1, _GITHUB_GRAPHQL_CHUNK_SIZE):
        numbers = list(
            range(chunk_start, min(end + 1, chunk_start + _GITHUB_GRAPHQL_CHUNK_SIZE))
        )
        query = _github_issue_range_query(numbers)
        completed = run_command(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            detail = _text(completed.stderr) or "invalid JSON"
            raise RuntimeError(
                f"GitHub issue discovery failed: {detail[:1024]}"
            ) from exc
        if not isinstance(response, dict):
            raise RuntimeError("GitHub issue discovery returned an invalid response")
        errors = (
            response.get("errors")
            if isinstance(response.get("errors"), list)
            else []
        )
        expected_missing_aliases = {f"issue{number}" for number in numbers}
        unexpected_errors = [
            error
            for error in errors
            if not (
                isinstance(error, dict)
                and error.get("type") == "NOT_FOUND"
                and isinstance(error.get("path"), list)
                and len(error["path"]) == 2
                and error["path"][0] == "repository"
                and error["path"][1] in expected_missing_aliases
                and str(error.get("message") or "").startswith(
                    "Could not resolve to an Issue with the number of "
                )
            )
        ]
        if unexpected_errors:
            raise RuntimeError(
                f"GitHub issue discovery failed: {json.dumps(unexpected_errors)[:1024]}"
            )
        if completed.returncode != 0 and not errors:
            detail = _text(completed.stderr) or "unknown error"
            raise RuntimeError(f"GitHub issue discovery failed: {detail[:1024]}")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        repository_data = data.get("repository")
        if not isinstance(repository_data, dict):
            raise RuntimeError(
                f"GitHub repository was not found or is not readable: {normalized_repository}"
            )
        default_branch_ref = repository_data.get("defaultBranchRef")
        default_branch = (
            _text(default_branch_ref.get("name"))
            if isinstance(default_branch_ref, dict)
            else None
        )
        if not _git_branch_is_valid(default_branch):
            raise RuntimeError(
                "GitHub repository default branch is unavailable or unsafe: "
                f"{normalized_repository}"
            )
        if (
            resolved_default_branch is not None
            and resolved_default_branch != default_branch
        ):
            raise RuntimeError(
                "GitHub repository default branch changed during issue discovery: "
                f"{normalized_repository}"
            )
        resolved_default_branch = default_branch
        repository_target = {
            "provider": "git",
            "connectionRef": normalized_connection_ref,
            "repository": {"name": normalized_repository},
            "branch": {"name": default_branch},
        }

        for number in numbers:
            issue = repository_data.get(f"issue{number}")
            if not isinstance(issue, dict):
                continue
            issue_state = (_text(issue.get("state")) or "").upper()
            if issue_state != "OPEN":
                continue
            labels_node = (
                issue.get("labels") if isinstance(issue.get("labels"), dict) else {}
            )
            labels = [
                str(node.get("name"))
                for node in labels_node.get("nodes", [])
                if isinstance(node, dict) and _text(node.get("name"))
            ]
            resolved_number = int(issue.get("number"))
            targets.append(
                {
                    "provider": "github",
                    "ref": f"{normalized_repository}#{resolved_number}",
                    "githubIssue": {
                        "repository": normalized_repository,
                        "number": resolved_number,
                        "title": str(issue.get("title") or ""),
                        "body": str(issue.get("body") or ""),
                        "url": str(issue.get("url") or ""),
                        "state": issue_state.lower(),
                        "labels": labels,
                    },
                    "repository": normalized_repository,
                    "repositoryTarget": repository_target,
                }
            )

    return targets


def parse_run_ref(value: str | None) -> tuple[str, str]:
    candidate = str(value or "").strip()
    if ":" not in candidate:
        raise ValueError(
            "run ref must use '<kind>:<slug>', for example skill:jira-verify"
        )
    kind, slug = candidate.split(":", 1)
    kind = kind.strip().lower()
    slug = slug.strip()
    if kind not in {"skill", "preset"} or not slug:
        raise ValueError("run ref must target skill:<name> or preset:<slug>")
    return kind, slug


def run_ref_for_config(config: TargetConfig) -> str:
    return f"{config.target_kind}:{config.target_slug}"


def _required_capabilities_for(
    provider: str, target_kind: str | None = None, target_slug: str | None = None
) -> list[str]:
    base = ["git"]
    if provider == "jira" and target_kind == "skill" and target_slug == "jira-verify":
        return base + ["jira"]
    if provider == "jira":
        base += ["jira", "gh"]
    elif provider == "github":
        base += ["gh"]
    return base


def child_goal_for_target(
    target: dict[str, Any], target_kind: str, target_slug: str
) -> str | None:
    """Return the goal text for the selected child run target.

    Returns ``None`` when the target cannot be auto-bound to the selected target.
    """

    provider = str(target.get("provider") or "").strip().lower()
    if (
        target_kind == "skill"
        and target_slug == "jira-verify"
        and provider == "jira"
    ):
        issue = (
            target.get("jiraIssue") if isinstance(target.get("jiraIssue"), dict) else {}
        )
        key = _text(issue.get("key")) or _text(target.get("ref"))
        if key:
            return f"Verify Jira issue {key}."
        return None
    if (
        target_kind == "preset"
        and target_slug in {"jira-implement", "jira-orchestrate"}
        and provider == "jira"
    ):
        issue = (
            target.get("jiraIssue") if isinstance(target.get("jiraIssue"), dict) else {}
        )
        key = _text(issue.get("key")) or _text(target.get("ref"))
        if key:
            action = "Orchestrate" if target_slug == "jira-orchestrate" else "Implement"
            return f"{action} Jira issue {key}."
        return None
    if (
        target_kind == "preset"
        and target_slug in {"github-issue-implement", "github-issue-orchestrate"}
        and provider == "github"
    ):
        issue = (
            target.get("githubIssue")
            if isinstance(target.get("githubIssue"), dict)
            else {}
        )
        repository = (
            _normalize_repo(issue.get("repository"))
            or _normalize_repo(target.get("repository"))
        )
        number = issue.get("number")
        ref = _text(target.get("ref"))
        if repository and number is not None:
            action = (
                "Orchestrate"
                if target_slug == "github-issue-orchestrate"
                else "Implement"
            )
            return f"{action} GitHub issue {repository}#{number}."
        if ref:
            action = (
                "Orchestrate"
                if target_slug == "github-issue-orchestrate"
                else "Implement"
            )
            return f"{action} GitHub issue {ref}."
        return None
    return None


def bind_child_inputs(
    target: dict[str, Any],
    target_kind: str,
    target_slug: str,
    constraints: str,
    fallback_repository: str | None = None,
    run_verify: bool = True,
    update_status: bool = False,
) -> dict[str, Any] | None:
    """Apply the default issue bindings for the selected child target.

    Mirrors the ``annotations.bindings`` declared by the ``batch-workflows``
    preset. Returns ``None`` when the target is not auto-bindable.
    """

    provider = str(target.get("provider") or "").strip().lower()
    normalized_fallback = _normalize_repo(fallback_repository)
    shared = _text(constraints)
    if (
        target_kind == "skill"
        and target_slug == "jira-verify"
        and provider == "jira"
    ):
        issue = (
            target.get("jiraIssue") if isinstance(target.get("jiraIssue"), dict) else {}
        )
        key = _text(issue.get("key")) or _text(target.get("ref"))
        if not key:
            return None
        inputs: dict[str, Any] = {
            "jira_issue": dict(issue) if issue else {"key": key},
            "jira_issue_key": key,
            "repository": (
                _normalize_repo(target.get("repository")) or normalized_fallback or ""
            ),
            "verification_mode": "auto",
            "update_status": bool(update_status),
            "constraints": shared or "",
        }
        return inputs
    if (
        target_kind == "preset"
        and target_slug in {"jira-implement", "jira-orchestrate"}
        and provider == "jira"
    ):
        issue = (
            target.get("jiraIssue") if isinstance(target.get("jiraIssue"), dict) else {}
        )
        key = _text(issue.get("key")) or _text(target.get("ref"))
        if not key:
            return None
        inputs: dict[str, Any] = {
            "jira_issue": dict(issue) if issue else {"key": key},
            "jira_issue_key": key,
            "constraints": shared or "",
            "run_verify": bool(run_verify),
        }
        return inputs
    if (
        target_kind == "preset"
        and target_slug in {"github-issue-implement", "github-issue-orchestrate"}
        and provider == "github"
    ):
        issue = (
            target.get("githubIssue")
            if isinstance(target.get("githubIssue"), dict)
            else {}
        )
        repository = (
            _normalize_repo(issue.get("repository"))
            or _normalize_repo(target.get("repository"))
            or normalized_fallback
        )
        number = issue.get("number")
        if not repository or number is None:
            return None
        resolved_issue = dict(issue)
        if not _text(resolved_issue.get("repository")):
            resolved_issue["repository"] = repository

        inputs = {
            "github_issue": resolved_issue,
            "github_issue_ref": f"{repository}#{number}",
            "constraints": shared or "",
            "run_verify": bool(run_verify),
        }
        return inputs
    return None


def _child_idempotency_key(
    *,
    batch_scope: str | None,
    provider: str,
    ref: str,
    target_kind: str,
    target_slug: str,
) -> str | None:
    scope = _text(batch_scope)
    if not scope:
        return None
    return child_idempotency_key(
        batch_scope=scope,
        provider=provider,
        ref=ref,
        target_kind=target_kind,
        target_slug=target_slug,
    )


def build_child_request(
    target: dict[str, Any],
    *,
    config: TargetConfig,
    runtime: RuntimeSelection,
    batch_scope: str | None = None,
    inherit_runtime_from_caller: bool = False,
    default_repository: str | None = None,
) -> dict[str, Any] | None:
    """Build a single ``POST /api/executions`` request for one resolved target.

    Returns ``None`` when the target cannot be auto-bound to the selected target.
    """

    provider = str(target.get("provider") or "").strip().lower()
    ref = _text(target.get("ref")) or ""
    repository = (
        _normalize_repo(target.get("repository"))
        or _normalize_repo(target.get("batch_repository"))
        or _normalize_repo(default_repository)
    )
    repository_target = target.get("repositoryTarget")
    if repository_target is not None:
        if not isinstance(repository_target, dict):
            raise BatchInputError("resolved repository target must be an object")
        provider_name = (_text(repository_target.get("provider")) or "").lower()
        connection_ref = _text(repository_target.get("connectionRef"))
        repository_node = repository_target.get("repository")
        branch_node = repository_target.get("branch")
        target_repository = (
            _normalize_repo(repository_node.get("name"))
            if isinstance(repository_node, dict)
            else None
        )
        target_branch = (
            _text(branch_node.get("name")) if isinstance(branch_node, dict) else None
        )
        if (
            provider_name != "git"
            or connection_ref is None
            or target_repository is None
            or target_branch is None
            or not _git_branch_is_valid(target_branch)
        ):
            raise BatchInputError(
                "resolved Git repository target requires provider, connection, "
                "repository, and a safe branch"
            )
        repository_target = {
            "provider": "git",
            "connectionRef": connection_ref,
            "repository": {"name": target_repository},
            "branch": {"name": target_branch},
        }
    if not ref:
        return None

    goal = child_goal_for_target(target, config.target_kind, config.target_slug)
    inputs = bind_child_inputs(
        target,
        config.target_kind,
        config.target_slug,
        config.constraints,
        fallback_repository=repository,
        run_verify=config.run_verify,
        update_status=config.update_status,
    )
    if goal is None or inputs is None:
        return None

    publish_mode = _normalize_publish_mode(config.publish_mode)
    required_capabilities = config.required_capabilities or _required_capabilities_for(
        provider,
        config.target_kind,
        config.target_slug,
    )

    task_payload: dict[str, Any] = {
        "goal": goal,
        "instructions": goal,
        "inputs": inputs,
        "publish": _publish_payload_for_mode(publish_mode),
    }
    if config.target_kind == "skill":
        task_payload["tool"] = {
            "type": "skill",
            "name": config.target_slug,
        }
    elif config.target_kind == "preset":
        # Author the child with the selected preset via ``taskTemplate`` so the
        # execution API expands the exact global preset instead of relying on
        # goal-only scheduler inference.
        task_payload["taskTemplate"] = {
            "slug": config.target_slug,
            "scope": "global",
        }
    else:
        return None

    payload_dict: dict[str, Any] = {
        "requiredCapabilities": required_capabilities,
        "task": task_payload,
    }
    if repository_target is not None:
        payload_dict["repository"] = repository_target
    elif repository:
        payload_dict["repository"] = repository

    # Server-side inheritance contract: when running inside a workflow with a
    # workflow-scoped credential, opt into runtimeInheritance="caller" so the API
    # copies the parent's effective runtime/provider profile. The explicit
    # targetRuntime/task.runtime fallback is preserved for deployments that do
    # not yet honour the inheritance contract.
    if inherit_runtime_from_caller:
        payload_dict["runtimeInheritance"] = "caller"

    runtime_payload: dict[str, Any] = {}
    if runtime.mode:
        runtime_payload["mode"] = runtime.mode
        payload_dict["targetRuntime"] = runtime.mode
    if runtime.model:
        runtime_payload["model"] = runtime.model
    if runtime.effort:
        runtime_payload["effort"] = runtime.effort
    if runtime.provider_profile:
        runtime_payload["executionProfileRef"] = runtime.provider_profile
    if runtime_payload:
        task_payload["runtime"] = runtime_payload

    idempotency_key = _child_idempotency_key(
        batch_scope=batch_scope,
        provider=provider,
        ref=ref,
        target_kind=config.target_kind,
        target_slug=config.target_slug,
    )
    if idempotency_key:
        payload_dict["idempotencyKey"] = idempotency_key

    return validate_execution_envelope(
        {
            "type": "task",
            "priority": 0,
            "maxAttempts": 3,
            "payload": payload_dict,
        }
    )


def build_child_requests(
    targets: list[dict[str, Any]],
    *,
    config: TargetConfig,
    runtime: RuntimeSelection,
    max_workflows: int,
    batch_scope: str | None = None,
    inherit_runtime_from_caller: bool = False,
    default_repository: str | None = None,
) -> tuple[list[ChildSubmission], list[SkippedTarget]]:
    """Build child requests, capped at ``max_workflows`` resolved targets."""

    submissions: list[ChildSubmission] = []
    skipped: list[SkippedTarget] = []

    limit = max(0, int(max_workflows))
    capped = targets[:limit]
    if len(targets) > limit:
        for overflow in targets[limit:]:
            skipped.append(
                SkippedTarget(
                    ref=str(overflow.get("ref") or "(unknown)"),
                    reason="max_workflows_exceeded",
                )
            )

    for target in capped:
        ref = str(target.get("ref") or "(unknown)")
        request = build_child_request(
            target,
            config=config,
            runtime=runtime,
            batch_scope=batch_scope,
            inherit_runtime_from_caller=inherit_runtime_from_caller,
            default_repository=default_repository,
        )
        if request is None:
            skipped.append(SkippedTarget(ref=ref, reason="unsupported_target"))
            continue
        submissions.append(
            ChildSubmission(
                queue_request=request,
                provider=str(target.get("provider") or "").strip().lower(),
                ref=ref,
            )
        )

    return submissions, skipped


# --------------------------------------------------------------------------- #
# Runtime inheritance + environment helpers (parallels batch-pr-resolver).
# --------------------------------------------------------------------------- #
def _normalize_runtime_mode(value: str | None) -> str | None:
    candidate = str(value or "").strip().lower()
    return candidate or None


def _runtime_modes_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_runtime_id(left) == normalize_runtime_id(right)


def _task_context_candidates(task_context_path: str | None) -> list[Path]:
    candidates: list[Path] = []
    if task_context_path:
        candidates.append(Path(task_context_path))
    for env_key in ("MOONMIND_TASK_CONTEXT_PATH", "TASK_CONTEXT_PATH"):
        env_value = _text(os.getenv(env_key))
        if env_value:
            candidates.append(Path(env_value))
    candidates.extend(
        [Path("../artifacts/task_context.json"), Path("artifacts/task_context.json")]
    )
    return candidates


def _load_parent_runtime_selection(
    task_context_path: str | None = None,
) -> RuntimeSelection | None:
    seen: set[str] = set()
    for candidate in _task_context_candidates(task_context_path):
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
        return RuntimeSelection(
            mode=mode,
            model=_text(runtime_config.get("model") or runtime_node.get("model")),
            effort=_text(runtime_config.get("effort") or runtime_node.get("effort")),
            provider_profile=_text(
                runtime_config.get("providerProfile")
                or runtime_config.get("profileId")
                or runtime_node.get("providerProfile")
                or runtime_node.get("profileId")
            ),
        )
    return None


def _load_parent_repository(task_context_path: str | None = None) -> str | None:
    seen: set[str] = set()
    for candidate in _task_context_candidates(task_context_path):
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


def _load_parent_repository_connection_ref(
    task_context_path: str | None = None,
) -> str | None:
    seen: set[str] = set()
    for candidate in _task_context_candidates(task_context_path):
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
        for target in (payload.get("repositoryTarget"), payload.get("repository")):
            if not isinstance(target, dict):
                continue
            if (_text(target.get("provider")) or "").lower() != "git":
                continue
            connection_ref = _text(target.get("connectionRef"))
            if connection_ref:
                return connection_ref
        auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
        connection_ref = _text(auth.get("repoAuthRef"))
        if connection_ref:
            return connection_ref
    return None


def _resolve_runtime_selection(task_context_path: str | None) -> RuntimeSelection:
    inherited = _load_parent_runtime_selection(task_context_path)
    configured_default_mode = _normalize_runtime_mode(
        os.getenv("MOONMIND_DEFAULT_RUNTIME")
    )
    execution_profile_ref = _text(os.getenv("MOONMIND_EXECUTION_PROFILE_REF"))
    execution_profile_runtime = _text(os.getenv("MOONMIND_EXECUTION_PROFILE_RUNTIME"))
    execution_profile_mode = (
        _normalize_runtime_mode(execution_profile_runtime)
        if execution_profile_ref
        else None
    )
    runtime_mode = (
        (inherited.mode if inherited else None)
        or execution_profile_mode
        or configured_default_mode
    )
    runtime_model = inherited.model if inherited else None
    runtime_effort = inherited.effort if inherited else None
    runtime_provider_profile = inherited.provider_profile if inherited else None
    if runtime_provider_profile is None and _runtime_modes_match(
        runtime_mode, execution_profile_runtime
    ):
        runtime_provider_profile = execution_profile_ref
    return RuntimeSelection(
        mode=runtime_mode,
        model=runtime_model,
        effort=runtime_effort,
        provider_profile=runtime_provider_profile,
    )


def _task_workflow_id_from_env() -> str | None:
    for env_key in (
        "MOONMIND_TASK_WORKFLOW_ID",
        "MOONMIND_WORKFLOW_ID",
        "TEMPORAL_WORKFLOW_ID",
    ):
        value = _text(os.getenv(env_key))
        if value:
            return value
    return None


def _agent_run_id_from_env() -> str | None:
    for env_key in ("MOONMIND_AGENT_RUN_ID", "MOONMIND_RUN_ID", "AGENT_RUN_ID"):
        value = _text(os.getenv(env_key))
        if value:
            return value
    return None


def _parent_run_scope(task_context_path: str | None) -> str | None:
    for env_key in ("MOONMIND_TASK_RUN_ID", "MOONMIND_RUN_ID", "TASK_RUN_ID"):
        value = _text(os.getenv(env_key))
        if value:
            return value
    spool = _text(os.getenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"))
    if spool:
        digest = hashlib.sha256(spool.encode("utf-8")).hexdigest()[:24]
        return f"path:{digest}"
    return None


def _session_artifact_spool_path() -> Path | None:
    raw = _text(os.getenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH"))
    return Path(raw) if raw else None


def _resolve_artifacts_dir(raw_artifacts_dir: str) -> Path:
    raw = str(raw_artifacts_dir or "").strip()
    if not raw or raw == "artifacts":
        spool = _session_artifact_spool_path()
        if spool is not None:
            return spool
    return Path(raw or "artifacts")


def _read_targets(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("targets")
    if not isinstance(payload, list):
        raise RuntimeError('targets file must be a JSON list (or {"targets": [...]}).')
    targets: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            targets.append(item)
    return targets


def _read_constraints(args: argparse.Namespace) -> str:
    if args.constraints is not None:
        return str(args.constraints)
    if args.constraints_file:
        path = Path(args.constraints_file)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def _read_worker_token() -> str | None:
    token = _text(os.getenv("MOONMIND_WORKER_TOKEN"))
    if token:
        return token
    token_file = _text(os.getenv("MOONMIND_WORKER_TOKEN_FILE"))
    if token_file:
        path = Path(token_file)
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or None
    return None


def _read_execution_fanout_token() -> str | None:
    token_file = _text(
        os.getenv("MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN_FILE")
    )
    if token_file:
        path = Path(token_file)
        if not path.is_file():
            raise RuntimeError(
                "execution fan-out capability file is unavailable: " + token_file
            )
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("execution fan-out capability file is empty")
        return token
    return _text(os.getenv("MOONMIND_EXECUTION_FANOUT_BEARER_TOKEN")) or None


def _auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    fanout_token = _read_execution_fanout_token()
    if fanout_token:
        headers["Authorization"] = f"Bearer {fanout_token}"
        headers["X-MoonMind-Execution-Fanout"] = "v1"
    worker_token = _read_worker_token()
    if worker_token:
        headers["X-MoonMind-Worker-Token"] = worker_token
    task_workflow_id = _task_workflow_id_from_env()
    if task_workflow_id:
        headers["X-MoonMind-Task-Workflow-Id"] = task_workflow_id
    agent_run_id = _agent_run_id_from_env()
    if agent_run_id:
        headers["X-MoonMind-Agent-Run-Identifier"] = agent_run_id
    return headers


def _describe_execution(*, moonmind_url: str, workflow_id: str) -> dict[str, Any]:
    """Verify one queued child via the execution API (lost-ack recovery)."""

    endpoint = (
        moonmind_url.rstrip("/")
        + API_EXECUTIONS_ENDPOINT
        + "/"
        + workflow_id
    )
    http_request = urllib.request.Request(
        endpoint, headers=_auth_headers(), method="GET"
    )
    with urllib.request.urlopen(http_request, timeout=30.0) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("execution API describe response must be a JSON object")
    return data


def _cancel_owned_execution(*, moonmind_url: str, workflow_id: str) -> dict[str, Any]:
    """Request cancellation of one owned child through the execution API."""

    endpoint = (
        moonmind_url.rstrip("/")
        + API_EXECUTIONS_ENDPOINT
        + "/"
        + workflow_id
        + "/cancel"
    )
    encoded = json.dumps({"reason": "repository batch cancel"}).encode("utf-8")
    http_request = urllib.request.Request(
        endpoint, data=encoded, headers=_auth_headers(), method="POST"
    )
    with urllib.request.urlopen(http_request, timeout=30.0) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("execution API cancel response must be a JSON object")
    return data


def _child_status_from_describe(described: dict[str, Any]) -> str:
    raw = ""
    for key in ("status", "state", "phase"):
        candidate = described.get(key)
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.strip().lower()
            break
    if raw in {"queued", "pending", "preparing", "scheduled", "waiting"}:
        return "queued" if raw in {"queued", "pending", "scheduled", "waiting"} else "running"
    if raw in {"running", "executing", "in_progress", "preparing"}:
        return "running"
    if raw in {"completed", "succeeded", "success", "complete"}:
        return "succeeded"
    if raw in {"failed", "error", "errored"}:
        return "failed"
    if raw in {"canceled", "cancelled", "terminated"}:
        return "canceled"
    if raw in {"blocked"}:
        return "blocked"
    return "unknown"


def _submit_jobs_via_http(
    submissions: list[ChildSubmission],
    *,
    moonmind_url: str,
    worker_token: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    headers = _auth_headers()
    endpoint = moonmind_url.rstrip("/") + API_EXECUTIONS_ENDPOINT
    for submission in submissions:
        envelope = submission.queue_request
        body = {
            "type": str(envelope["type"]),
            "payload": envelope["payload"],
            "priority": int(envelope.get("priority", 0)),
            "maxAttempts": int(envelope.get("maxAttempts", 3)),
        }
        try:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            http_request = urllib.request.Request(
                endpoint, data=encoded, headers=headers, method="POST"
            )
            with urllib.request.urlopen(http_request, timeout=30.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError("execution API response must be a JSON object")
            job_id = str(
                data.get("workflowId") or data.get("taskId") or data.get("id") or ""
            ).strip()
            if not job_id:
                raise RuntimeError("execution API response is missing workflowId")
            created.append(
                {
                    "provider": submission.provider,
                    "ref": submission.ref,
                    "workflowId": job_id,
                    "executionId": job_id,
                    "targetRef": submission.ref,
                    "idempotencyKey": str(
                        envelope.get("payload", {}).get("idempotencyKey") or ""
                    ),
                }
            )
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(65536).decode("utf-8", errors="replace")
                error_message = f"{exc}: {detail}"
            except Exception:
                error_message = str(exc)
            errors.append(
                {
                    "provider": submission.provider,
                    "ref": submission.ref,
                    "error": error_message,
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported per target
            errors.append(
                {
                    "provider": submission.provider,
                    "ref": submission.ref,
                    "error": str(exc),
                }
            )
    return created, errors


def _submit_jobs(
    submissions: list[ChildSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    moonmind_url = _text(os.getenv("MOONMIND_URL"))
    if moonmind_url:
        return _submit_jobs_via_http(
            submissions,
            moonmind_url=moonmind_url,
            worker_token=_read_worker_token(),
        )
    message = (
        "MOONMIND_URL is not set; batch-workflows requires the MoonMind Temporal "
        "execution API and cannot submit via the removed legacy DB queue."
    )
    return [], [
        {"provider": submission.provider, "ref": submission.ref, "error": message}
        for submission in submissions
    ]


def _write_artifacts(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve issue targets and queue one child MoonMind workflow per target."
    )
    parser.add_argument(
        "--targets", help="Path to resolved targets JSON."
    )
    parser.add_argument(
        "--github-issue-range",
        help="Inclusive GitHub issue-number search range in START-END form.",
    )
    parser.add_argument(
        "--github-repository",
        help="GitHub owner/repository; defaults to the parent task repository.",
    )
    parser.add_argument(
        "--repository-connection-ref",
        help=(
            "Canonical repository connection authority; defaults to the parent "
            "task context or MOONMIND_REPOSITORY_CONNECTION_REF."
        ),
    )
    parser.add_argument("--run-ref", required=True)
    parser.add_argument(
        "--publish-mode",
        default="pr",
        help=(
            "Direct-CLI fallback only. Every preset recipe forwards its explicit "
            "publish_mode value, whose effective default is derived at the "
            "preset layer (skill:jira-verify->none, implement presets->pr, "
            "GitHub presets->pr); the helper default never masks that "
            "derivation. Explicit none/branch/pr/pr_with_merge_automation are "
            "preserved verbatim."
        ),
    )
    parser.add_argument("--constraints", default=None)
    parser.add_argument("--constraints-file", default=None)
    parser.add_argument(
        "--run-verify",
        dest="run_verify",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--update-status",
        dest="update_status",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="For skill:jira-verify children, update Jira status only on PASS.",
    )
    parser.add_argument(
        "--max-workflows",
        type=int,
        default=25,
        help=(
            "Hard cap on queued children (default 25). The preset layer types "
            "max_workflows as a string for Jinja/CLI interpolation (default "
            "'25'); argparse coerces it to int here, so omitted, '25', and 25 "
            "are equivalent."
        ),
    )
    parser.add_argument(
        "--preflight-error",
        default=None,
        help=(
            "Record an input-validation failure without reading targets or "
            "queueing child workflows."
        ),
    )
    parser.add_argument(
        "--requested-count",
        type=int,
        default=0,
        help="Number of requested targets reported with --preflight-error.",
    )
    parser.add_argument("--task-context-path", default=None)
    parser.add_argument("--artifacts-dir", default="artifacts")
    parser.add_argument(
        "--repository-targets-file",
        default=None,
        help=(
            "Explicit bounded multi-repository target list (JSON array) for "
            "isolated fan-out (MoonLadderStudios/MoonMind#1657). Cannot be "
            "combined with --targets or --github-issue-range."
        ),
    )
    parser.add_argument(
        "--approved-batch-digest",
        default=None,
        help=(
            "Operator-approved repository-batch manifest digest. Preflight "
            "writes the manifest for review; dispatch requires this digest "
            "to match the frozen target set exactly."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        dest="allow_partial",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Explicitly allow dispatch of the preflight-accessible subset "
            "while recording inaccessible targets as skipped."
        ),
    )
    parser.add_argument(
        "--max-repositories",
        type=int,
        default=_BATCH_TARGETS.DEFAULT_MAX_REPOSITORY_TARGETS,
        help="Hard cap on repository-batch targets (default 10).",
    )
    parser.add_argument(
        "--batch-budget-file",
        default=None,
        help="Optional JSON budget descriptors for a repository batch.",
    )
    parser.add_argument(
        "--upstream-evidence-file",
        default=None,
        help=(
            "Optional JSON mapping of upstream target refs to verified "
            "revision/artifact evidence for dependent repository phases."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        dest="preflight_only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Freeze and report the batch manifest without dispatching.",
    )
    parser.add_argument(
        "--retry-failed-only",
        dest="retry_failed_only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Selected retry: resubmit failed/canceled/unknown targets from "
            "the prior aggregate without republishing completed ones."
        ),
    )
    parser.add_argument(
        "--cancel-owned",
        dest="cancel_owned",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Cancel owned queued/running children recorded in the prior "
            "aggregate instead of dispatching."
        ),
    )
    parser.add_argument(
        "--capacity-polls",
        type=int,
        default=6,
        help="Bounded capacity-gate polls before a waiting target blocks.",
    )
    parser.add_argument(
        "--capacity-poll-interval",
        type=float,
        default=10.0,
        help="Seconds between capacity-gate polls.",
    )
    return parser.parse_args(argv)


REPOSITORY_BATCH_MANIFEST_ARTIFACT = "batch-repository-manifest.json"
REPOSITORY_BATCH_RESULT_ARTIFACT = "batch-repositories-result.json"


def _load_json_document(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BatchInputError(f"JSON file not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON file {path}: {exc}") from exc


def _repository_batch_entries(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("targets")
    if not isinstance(raw, list) or not raw:
        raise _BATCH_TARGETS.RepositoryBatchError(
            "REPOSITORY_BATCH_EMPTY",
            "repository targets file must hold a non-empty JSON array",
        )
    return raw


def _refresh_owned_status(
    *, moonmind_url: str, workflow_id: str
) -> tuple[str, dict[str, Any] | None]:
    try:
        described = _describe_execution(
            moonmind_url=moonmind_url, workflow_id=workflow_id
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "admission_lost", None
        return "unknown", None
    except Exception:  # noqa: BLE001 - describe failures stay unknown
        return "unknown", None
    return _child_status_from_describe(described), described


def _wait_for_capacity(
    *,
    moonmind_url: str,
    owned: dict[str, str],
    max_concurrency: int,
    polls: int,
    interval: float,
) -> None:
    """Bounded N+1 capacity wait refreshing owned children in place."""

    remaining = max(0, int(polls))
    while remaining > 0:
        active = [
            workflow_id
            for workflow_id, status in owned.items()
            if status in {"queued", "running", "unknown", "waiting"}
        ]
        if len(active) < max_concurrency:
            return
        if interval > 0:
            time.sleep(interval)
        remaining -= 1
        for workflow_id in active:
            status, _ = _refresh_owned_status(
                moonmind_url=moonmind_url, workflow_id=workflow_id
            )
            if status == "admission_lost":
                owned.pop(workflow_id, None)
            elif status != "unknown":
                owned[workflow_id] = status


def _submit_repository_child(
    *,
    moonmind_url: str,
    envelope: dict[str, Any],
) -> tuple[str | None, str | None]:
    """POST one child and verify admission; never count unverified queueing."""

    body = {
        "type": str(envelope["type"]),
        "payload": envelope["payload"],
        "priority": int(envelope.get("priority", 0)),
        "maxAttempts": int(envelope.get("maxAttempts", 3)),
    }
    endpoint = moonmind_url.rstrip("/") + API_EXECUTIONS_ENDPOINT
    try:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        http_request = urllib.request.Request(
            endpoint, data=encoded, headers=_auth_headers(), method="POST"
        )
        with urllib.request.urlopen(http_request, timeout=30.0) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None, "execution API response must be a JSON object"
        workflow_id = str(
            data.get("workflowId") or data.get("taskId") or data.get("id") or ""
        ).strip()
        if not workflow_id:
            return None, "execution API response is missing workflowId"
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(65536).decode("utf-8", errors="replace")
            return None, f"{exc}: {detail}"
        except Exception:
            return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - reported per target
        # A transport failure after server-side admission is ambiguous: the
        # caller retries under the same idempotency key and reconciles.
        return None, f"submission_unconfirmed: {exc}"
    status, _ = _refresh_owned_status(
        moonmind_url=moonmind_url, workflow_id=workflow_id
    )
    if status == "admission_lost":
        return None, "admission_unconfirmed: child missing immediately after queueing"
    return workflow_id, None


def _run_cancel_owned(args: argparse.Namespace, artifacts_dir: Path) -> int:
    result_path = artifacts_dir / REPOSITORY_BATCH_RESULT_ARTIFACT
    prior = _load_json_document(result_path)
    if not isinstance(prior, dict) or not isinstance(prior.get("targets"), list):
        print("error: no repository-batch aggregate to cancel from", flush=True)
        return 2
    moonmind_url = _text(os.getenv("MOONMIND_URL"))
    if not moonmind_url:
        print("error: MOONMIND_URL is required to cancel owned children", flush=True)
        return 2
    manifest = {
        "digest": prior.get("manifestDigest"),
        "runRef": prior.get("runRef"),
    }
    per_target: list[dict[str, Any]] = []
    canceled = 0
    targeted = 0
    for item in prior["targets"]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        workflow_id = _text(item.get("workflowId"))
        if not workflow_id or status not in {"queued", "running", "unknown", "waiting"}:
            per_target.append(dict(item))
            continue
        targeted += 1
        try:
            _cancel_owned_execution(
                moonmind_url=moonmind_url, workflow_id=workflow_id
            )
            status_after, _ = _refresh_owned_status(
                moonmind_url=moonmind_url, workflow_id=workflow_id
            )
            final_status = (
                status_after if status_after in {"canceled", "failed", "succeeded"}
                else "canceled"
            )
            per_target.append({**item, "status": final_status, "reason": "cancel_requested"})
            if final_status == "canceled":
                canceled += 1
        except Exception as exc:  # noqa: BLE001 - reported per target
            per_target.append(
                {**item, "status": status, "reason": "cancel_failed", "error": str(exc)[:1024]}
            )
    aggregate = _BATCH_TARGETS.build_batch_aggregate_result(
        manifest=manifest,
        per_target=per_target,
        batch_status="canceled" if targeted and canceled == targeted else "partial",
    )
    _write_artifacts(result_path, aggregate)
    print(json.dumps(aggregate, indent=2))
    print(f"canceled={canceled} targeted={targeted}")
    return 0 if targeted and canceled == targeted else 1


def _run_repository_batch(args: argparse.Namespace, artifacts_dir: Path) -> int:
    manifest_path = artifacts_dir / REPOSITORY_BATCH_MANIFEST_ARTIFACT
    result_path = artifacts_dir / REPOSITORY_BATCH_RESULT_ARTIFACT
    execution_ref = _text(os.getenv("MOONMIND_STEP_EXECUTION_ID"))
    # Load the prior aggregate before replacing it: resume and selected
    # retry reconcile against it, and a corrupt prior fails closed instead
    # of silently submitting fresh duplicates.
    prior_result: dict[str, Any] | None = None
    if result_path.exists() and not args.preflight_only:
        raw_prior = _load_json_document(result_path)
        if not isinstance(raw_prior, dict) or not isinstance(
            raw_prior.get("targets"), list
        ):
            raise RuntimeError(
                f"prior repository-batch aggregate is corrupt: {result_path}"
            )
        prior_result = raw_prior
    try:
        result_path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot remove stale result artifact: {exc}") from exc

    def _fail_aggregate(
        manifest: dict[str, Any] | None,
        per_target: list[dict[str, Any]],
        *,
        code: str,
        message: str,
        batch_status: str,
        exit_code: int,
    ) -> int:
        aggregate = _BATCH_TARGETS.build_batch_aggregate_result(
            manifest=manifest or {"digest": None, "runRef": args.run_ref},
            per_target=per_target,
            batch_status=batch_status,
            failure={"code": code, "message": message[:1024]},
        )
        _write_artifacts(result_path, aggregate)
        print(json.dumps(aggregate, indent=2))
        return exit_code

    try:
        if not execution_ref:
            raise RuntimeError("MOONMIND_STEP_EXECUTION_ID is required")
        if args.targets is not None or args.github_issue_range:
            raise BatchInputError(
                "use either --repository-targets-file or the single-repository "
                "batch inputs, not both"
            )
        if not args.repository_targets_file:
            raise BatchInputError("--repository-targets-file is required")
        raw_targets = _load_json_document(Path(args.repository_targets_file))
        entries = _repository_batch_entries(raw_targets)
        budget_raw: Any = {}
        if args.batch_budget_file:
            budget_raw = _load_json_document(Path(args.batch_budget_file))
        elif args.max_repositories != _BATCH_TARGETS.DEFAULT_MAX_REPOSITORY_TARGETS:
            budget_raw = {"maxTargets": args.max_repositories}
        budget = _BATCH_TARGETS.parse_batch_budget(budget_raw)
        if args.max_repositories != _BATCH_TARGETS.DEFAULT_MAX_REPOSITORY_TARGETS:
            budget = _BATCH_TARGETS.RepositoryBatchBudget(
                max_targets=min(
                    args.max_repositories, _BATCH_TARGETS.HARD_MAX_REPOSITORY_TARGETS
                ),
                max_concurrency=budget.max_concurrency,
                max_child_spend_usd=budget.max_child_spend_usd,
                max_attempts_per_child=budget.max_attempts_per_child,
            )
        normalized = _BATCH_TARGETS.normalize_repository_batch(
            entries, max_targets=budget.max_targets
        )
        constraints = _read_constraints(args)
        manifest = _BATCH_TARGETS.freeze_repository_batch_manifest(
            normalized,
            task={
                "runRef": args.run_ref,
                "constraints": constraints,
                "publishMode": _normalize_publish_mode(args.publish_mode),
            },
            budget=budget,
        )
        _write_artifacts(manifest_path, manifest)
        if args.preflight_only:
            print(json.dumps(manifest, indent=2))
            print(f"manifest_digest={manifest['digest']} targets={len(normalized.targets)}")
            return 0
        try:
            _BATCH_TARGETS.verify_manifest_unchanged(
                manifest, args.approved_batch_digest or ""
            )
        except _BATCH_TARGETS.RepositoryBatchError as exc:
            return _fail_aggregate(
                manifest,
                [],
                code=exc.code,
                message=str(exc),
                batch_status="failed",
                exit_code=2,
            )
        preflight = _BATCH_TARGETS.preflight_repository_batch(
            normalized,
            allow_partial=bool(args.allow_partial),
            publish_mode=_normalize_publish_mode(args.publish_mode),
        )
        if preflight.blocked:
            return _fail_aggregate(
                manifest,
                [
                    {
                        "targetRef": item.target_ref,
                        "status": "blocked" if not item.accessible else "waiting",
                        "reason": item.reason,
                    }
                    for item in preflight.targets
                ],
                code="REPOSITORY_BATCH_PREFLIGHT_BLOCKED",
                message=preflight.failure or "preflight blocked dispatch",
                batch_status="blocked",
                exit_code=2,
            )
        accessible_refs = {
            item.target_ref for item in preflight.targets if item.accessible
        }
        skipped_entries: list[dict[str, Any]] = [
            {
                "targetRef": item.target_ref,
                "status": "skipped",
                "reason": item.reason,
            }
            for item in preflight.targets
            if not item.accessible
        ]
        releasable = [
            target for target in normalized.targets if target.target_ref in accessible_refs
        ]
        evidence_map: dict[str, Any] = {}
        if args.upstream_evidence_file:
            raw_evidence = _load_json_document(Path(args.upstream_evidence_file))
            if not isinstance(raw_evidence, dict):
                raise BatchInputError("--upstream-evidence-file must hold an object")
            evidence_map = raw_evidence
        phases = _BATCH_TARGETS.resolve_target_phases(releasable)
        ordered: list[_BATCH_TARGETS.ParsedRepositoryTarget] = []
        blocked_entries: list[dict[str, Any]] = []
        for phase in phases:
            releasable_phase, blocked_phase = _BATCH_TARGETS.gate_dependent_targets(
                phase,
                verify_upstream_evidence=(lambda ref: evidence_map.get(ref)),
            )
            ordered.extend(
                sorted(releasable_phase, key=lambda item: item.target_ref)
            )
            for blocked in blocked_phase:
                target = next(
                    item
                    for item in phase
                    if item.target_ref == blocked["targetRef"]
                )
                blocked_entries.append(
                    _BATCH_TARGETS.per_target_entry(
                        target, status="blocked", reason=blocked["reason"]
                    )
                )
        prior: dict[str, Any] | None = prior_result
        # Resume and selected retry reconcile against the prior aggregate
        # loaded before this run replaced it; a digest change submits fresh
        # under new idempotency keys instead of reusing prior admissions.
        to_submit, reused = _BATCH_TARGETS.reconcile_with_prior_result(
            manifest_digest=str(manifest["digest"]),
            targets=ordered,
            prior_result=prior,
            retry_failed_only=bool(args.retry_failed_only),
        )
        moonmind_url = _text(os.getenv("MOONMIND_URL"))
        if not moonmind_url:
            raise BatchInputError(
                "MOONMIND_URL is not set; repository batch requires the "
                "MoonMind Temporal execution API"
            )
        runtime = _resolve_runtime_selection(args.task_context_path)
        target_by_ref = {target.target_ref: target for target in ordered}
        per_target: list[dict[str, Any]] = []
        per_target.extend(skipped_entries)
        per_target.extend(blocked_entries)
        for reuse in reused:
            ref = reuse["targetRef"]
            target = target_by_ref.get(ref)
            if target is None:
                continue
            if reuse["reason"] == "already_queued" and reuse.get("workflowId"):
                status, _ = _refresh_owned_status(
                    moonmind_url=moonmind_url, workflow_id=reuse["workflowId"]
                )
                if status == "admission_lost":
                    to_submit.append((target, 0))
                    continue
                per_target.append(
                    _BATCH_TARGETS.per_target_entry(
                        target,
                        status=status if status != "unknown" else "unknown",
                        workflow_id=reuse["workflowId"],
                        reason="already_queued",
                    )
                )
            else:
                per_target.append(
                    {
                        "targetRef": ref,
                        "status": "skipped",
                        "reason": reuse["reason"],
                        "attempt": int(reuse.get("attempt") or 0),
                    }
                )
        owned: dict[str, str] = {}
        for item in per_target:
            workflow_id = _text(item.get("workflowId"))
            status = str(item.get("status") or "")
            if workflow_id and status in {"queued", "running", "unknown"}:
                owned[workflow_id] = status
        run_ref = str(args.run_ref)
        goal = (constraints.strip().splitlines() or [f"Execute {run_ref}."])[0][:500]
        submitted = 0
        submit_errors = 0
        for target, attempt in to_submit:
            _wait_for_capacity(
                moonmind_url=moonmind_url,
                owned=owned,
                max_concurrency=budget.max_concurrency,
                polls=int(args.capacity_polls),
                interval=float(args.capacity_poll_interval),
            )
            active_owned = sum(
                1 for status in owned.values() if status in {"queued", "running", "unknown", "waiting"}
            )
            capacity_hold = _BATCH_TARGETS.gate_on_capacity(
                running_owned=active_owned,
                max_concurrency=budget.max_concurrency,
                target_ref=target.target_ref,
            )
            if capacity_hold is not None:
                per_target.append(
                    _BATCH_TARGETS.per_target_entry(
                        target, status="blocked", reason="capacity_exhausted",
                        attempt=attempt,
                    )
                )
                continue
            envelope = _BATCH_TARGETS.build_multi_repo_child_request(
                target,
                manifest_digest=str(manifest["digest"]),
                run_ref=run_ref,
                goal=goal,
                constraints=constraints,
                publish_mode=str(manifest["publishMode"]),
                runtime_mode=runtime.mode,
                runtime_model=runtime.model,
                runtime_effort=runtime.effort,
                runtime_provider_profile=runtime.provider_profile,
                max_attempts=budget.max_attempts_per_child,
                attempt=attempt,
            )
            workflow_id, error = _submit_repository_child(
                moonmind_url=moonmind_url, envelope=envelope
            )
            idempotency_key = str(envelope["payload"].get("idempotencyKey") or "")
            if workflow_id is None:
                submit_errors += 1
                per_target.append(
                    _BATCH_TARGETS.per_target_entry(
                        target, status="unknown", reason="submission_unconfirmed",
                        idempotency_key=idempotency_key, error=error,
                        attempt=attempt,
                    )
                )
            else:
                submitted += 1
                owned[workflow_id] = "queued"
                per_target.append(
                    _BATCH_TARGETS.per_target_entry(
                        target, status="queued", workflow_id=workflow_id,
                        idempotency_key=idempotency_key, attempt=attempt,
                    )
                )
            interim = _BATCH_TARGETS.build_batch_aggregate_result(
                manifest=manifest, per_target=list(per_target),
                batch_status="partial" if submit_errors else "queued",
            )
            _write_artifacts(result_path, interim)
    except _BATCH_TARGETS.RepositoryBatchError as exc:
        return _fail_aggregate(
            None, [], code=exc.code, message=str(exc),
            batch_status="failed", exit_code=2,
        )
    except Exception as exc:  # evidence must survive dispatch failures
        failure_code = (
            "BATCH_FANOUT_INPUT_INVALID"
            if isinstance(exc, BatchInputError)
            else "BATCH_FANOUT_FAILED"
        )
        return _fail_aggregate(
            None, [], code=failure_code, message=str(exc),
            batch_status="failed",
            exit_code=2 if isinstance(exc, BatchInputError) else 1,
        )
    queued = sum(1 for item in per_target if item.get("status") == "queued")
    terminal_bad = sum(
        1 for item in per_target if item.get("status") in {"unknown", "blocked"}
    )
    if not per_target:
        batch_status = "no_op"
    elif submit_errors == 0 and terminal_bad == 0:
        batch_status = "queued"
    elif queued > 0:
        batch_status = "partial"
    else:
        batch_status = "failed"
    aggregate = _BATCH_TARGETS.build_batch_aggregate_result(
        manifest=manifest, per_target=per_target, batch_status=batch_status,
        failure=(
            {"code": "BATCH_FANOUT_PARTIAL_FAILURE", "message": "some targets need attention"}
            if batch_status in {"partial", "failed"} else None
        ),
    )
    _write_artifacts(result_path, aggregate)
    print(json.dumps(aggregate, indent=2))
    print(
        f"queued={submitted} errors={submit_errors} "
        f"targets={len(per_target)} manifest={manifest['digest']}"
    )
    return 0 if batch_status in {"queued", "no_op"} else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    targets_path = Path(args.targets) if args.targets else None
    artifacts_dir = _resolve_artifacts_dir(args.artifacts_dir)
    if args.repository_targets_file or args.cancel_owned:
        if args.cancel_owned:
            return _run_cancel_owned(args, artifacts_dir)
        return _run_repository_batch(args, artifacts_dir)
    result_path = artifacts_dir / "batch-workflows-result.json"
    try:
        result_path.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"cannot remove stale result artifact: {exc}") from exc
    execution_ref = _text(os.getenv("MOONMIND_STEP_EXECUTION_ID"))
    targets_digest = (
        hashlib.sha256(targets_path.read_bytes()).hexdigest()
        if targets_path is not None and targets_path.exists() and targets_path.is_file()
        else None
    )
    base_result: dict[str, Any] = {
        "schemaVersion": "moonmind.batch-workflows-result.v1",
        "contractId": "batch_workflows_fanout.v1",
        "executionRef": execution_ref,
        "targetsSha256": targets_digest,
        "status": "running",
        "runRef": args.run_ref,
        "requested": 0,
        "created": 0,
        "queued": [],
        "skipped": [],
        "errors": [],
        "failure": None,
    }
    _write_artifacts(result_path, base_result)
    try:
        if not execution_ref:
            raise RuntimeError("MOONMIND_STEP_EXECUTION_ID is required")
        preflight_error = _text(args.preflight_error)
        if preflight_error:
            if args.requested_count < 0:
                preflight_error = "--requested-count must be zero or greater"
            requested_count = max(0, args.requested_count)
            failed = {
                **base_result,
                "timestamp": datetime.now(UTC).isoformat(),
                "status": "failed",
                "requested": requested_count,
                "failure": {
                    "code": "BATCH_FANOUT_INPUT_INVALID",
                    "message": preflight_error[:1024],
                },
                "errors": [
                    {
                        "code": "BATCH_FANOUT_INPUT_INVALID",
                        "error": preflight_error[:1024],
                    }
                ],
            }
            _write_artifacts(result_path, failed)
            print(json.dumps(failed, indent=2))
            return 2
        if args.github_issue_range and targets_path is not None:
            raise BatchInputError(
                "use either --targets or --github-issue-range, not both"
            )
        batch_repository = _load_parent_repository(args.task_context_path)
        if args.github_issue_range:
            github_repository = (
                _normalize_repo(args.github_repository) or batch_repository
            )
            if not github_repository:
                raise BatchInputError(
                    "--github-repository is required when parent repository context is unavailable"
                )
            repository_connection_ref = (
                _text(args.repository_connection_ref)
                or _load_parent_repository_connection_ref(args.task_context_path)
                or _text(os.getenv("MOONMIND_REPOSITORY_CONNECTION_REF"))
            )
            if not repository_connection_ref:
                raise BatchInputError(
                    "--repository-connection-ref is required when parent repository "
                    "connection context is unavailable"
                )
            targets = resolve_github_issue_range(
                github_repository,
                args.github_issue_range,
                connection_ref=repository_connection_ref,
            )
            targets_path = artifacts_dir / "batch-workflows-targets.json"
            _write_artifacts(targets_path, {"targets": targets})
            base_result["targetsSha256"] = hashlib.sha256(
                targets_path.read_bytes()
            ).hexdigest()
        else:
            if targets_path is None:
                raise BatchInputError("--targets or --github-issue-range is required")
            if not targets_path.exists():
                raise RuntimeError(f"targets file not found: {targets_path}")
            targets = _read_targets(targets_path)
        constraints = _read_constraints(args)
        runtime = _resolve_runtime_selection(args.task_context_path)
        batch_scope = _parent_run_scope(args.task_context_path) or execution_ref
        inherit_from_caller = _task_workflow_id_from_env() is not None
        target_kind, target_slug = parse_run_ref(args.run_ref)

        config = TargetConfig(
        target_kind=target_kind,
        target_slug=target_slug,
        publish_mode=_normalize_publish_mode(args.publish_mode),
        constraints=constraints,
        run_verify=bool(args.run_verify),
        update_status=bool(args.update_status),
    )

        submissions, skipped = build_child_requests(
        targets,
        config=config,
        runtime=runtime,
        max_workflows=args.max_workflows,
        batch_scope=batch_scope,
        inherit_runtime_from_caller=inherit_from_caller,
        default_repository=batch_repository,
    )
        created, errors = _submit_jobs(submissions)
    except Exception as exc:  # evidence must survive every reachable preflight failure
        failure_code = (
            "BATCH_FANOUT_INPUT_INVALID"
            if isinstance(exc, BatchInputError)
            else "BATCH_FANOUT_FAILED"
        )
        failed = {
            **base_result,
            "status": "failed",
            "failure": {"code": failure_code, "message": str(exc)[:1024]},
            "errors": [{"code": failure_code, "error": str(exc)[:1024]}],
        }
        _write_artifacts(result_path, failed)
        print(json.dumps(failed, indent=2))
        return 2 if isinstance(exc, BatchInputError) else 1

    payload = {
        **base_result,
        "timestamp": datetime.now(UTC).isoformat(),
        "actor": os.getenv("GITHUB_ACTOR") or os.getenv("USER") or "unknown",
        "target": {
            "kind": config.target_kind,
            "slug": config.target_slug,
        },
        "publishMode": config.publish_mode,
        "runtime": {
            "inherit": "caller" if inherit_from_caller else None,
            "mode": runtime.mode,
            "model": runtime.model,
            "effort": runtime.effort,
            "executionProfileRef": runtime.provider_profile,
        },
        "status": (
            "no_op" if not targets else
            "queued" if len(created) == len(targets) and not errors and not skipped else
            "partial_failure" if created else "failed"
        ),
        "requested": len(targets),
        "created": len(created),
        "queued": created,
        "skipped": [{"ref": item.ref, "reason": item.reason} for item in skipped],
        "errors": errors,
        "failure": (
            {"code": "BATCH_FANOUT_PARTIAL_FAILURE" if created else "BATCH_FANOUT_FAILED"}
            if errors or skipped else None
        ),
    }
    if payload["created"] == 0:
        payload["message"] = "No child workflows were queued."

    _write_artifacts(result_path, payload)
    if payload["status"] == "no_op":
        _write_artifacts(
            artifacts_dir / "skill_outcome.json",
            {
                "schema_version": 1,
                "status": "no_op",
                "reason": "no_targets_queued",
                "evidence": {
                    "requested": payload["requested"],
                    "skipped": payload["skipped"],
                },
            },
        )

    print(json.dumps(payload, indent=2))
    print(
        f"queued={payload['created']} skipped={len(skipped)} errors={len(errors)} "
        f"target={run_ref_for_config(config)}"
    )
    return 0 if payload["status"] in {"queued", "no_op"} else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - surface root cause before exiting
        # Print the exception message and full traceback to stderr so runtime
        # failures (missing files, JSON decode errors, HTTP errors) are
        # diagnosable instead of being swallowed behind a generic message.
        print(f"error: batch-workflows failed: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
