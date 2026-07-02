#!/usr/bin/env python3
"""Queue one child MoonMind workflow per resolved issue target.

The agent resolves Jira status or other issue targets into the canonical
resolved-target shape (see SKILL.md) and writes them to a JSON file. This helper
reads that file plus the selected child run capability / publish policy and
submits one child execution per target through the internal Temporal execution
API. Every child inherits the parent runtime via ``runtimeInheritance="caller"``
(with a fallback copy of the effective runtime fields) and shares a single
publish policy. A summary artifact links every queued child workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
from moonmind.workflows.executions.execution_contract import SUPPORTED_PUBLISH_MODES
from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

logger = logging.getLogger(__name__)

API_EXECUTIONS_ENDPOINT = "/api/executions"
IDEMPOTENCY_KEY_MAX_LENGTH = 128
PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE = "pr_with_merge_automation"
BATCH_PUBLISH_MODES = SUPPORTED_PUBLISH_MODES | {PR_WITH_MERGE_AUTOMATION_PUBLISH_MODE}


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
    required_capabilities: list[str] = field(default_factory=list)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _normalize_publish_mode(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate not in BATCH_PUBLISH_MODES:
        return "pr"
    return candidate


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
        and target_slug == "jira-implement"
        and provider == "jira"
    ):
        issue = (
            target.get("jiraIssue") if isinstance(target.get("jiraIssue"), dict) else {}
        )
        key = _text(issue.get("key")) or _text(target.get("ref"))
        if key:
            return f"Implement Jira issue {key}."
        return None
    if (
        target_kind == "preset"
        and target_slug == "github-issue-implement"
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
            return f"Implement GitHub issue {repository}#{number}."
        if ref:
            return f"Implement GitHub issue {ref}."
        return None
    return None


def bind_child_inputs(
    target: dict[str, Any],
    target_kind: str,
    target_slug: str,
    constraints: str,
    fallback_repository: str | None = None,
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
            "update_status": False,
            "constraints": shared or "",
        }
        return inputs
    if (
        target_kind == "preset"
        and target_slug == "jira-implement"
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
        }
        return inputs
    if (
        target_kind == "preset"
        and target_slug == "github-issue-implement"
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
    components = {
        "scope": scope,
        "provider": provider,
        "ref": ref,
        "targetKind": target_kind,
        "targetSlug": target_slug,
    }
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    safe_ref = re.sub(r"[^A-Za-z0-9_.#/-]+", "_", ref)[:32]
    key = f"batch-workflows:{provider}:{safe_ref}:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        key = f"batch-workflows:sha256:{digest}"
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise RuntimeError("generated child idempotency key exceeds storage limit")
    return key


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
    if not ref:
        return None

    goal = child_goal_for_target(target, config.target_kind, config.target_slug)
    inputs = bind_child_inputs(
        target,
        config.target_kind,
        config.target_slug,
        config.constraints,
        fallback_repository=repository,
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
    if repository:
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

    return {
        "type": "task",
        "priority": 0,
        "maxAttempts": 3,
        "payload": payload_dict,
    }


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


async def _submit_jobs_via_http(
    submissions: list[ChildSubmission],
    *,
    moonmind_url: str,
    worker_token: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if worker_token:
        headers["X-MoonMind-Worker-Token"] = worker_token
    task_workflow_id = _task_workflow_id_from_env()
    if task_workflow_id:
        headers["X-MoonMind-Task-Workflow-Id"] = task_workflow_id
    agent_run_id = _agent_run_id_from_env()
    if agent_run_id:
        headers["X-MoonMind-Agent-Run-Identifier"] = agent_run_id
    base = moonmind_url.rstrip("/")
    async with httpx.AsyncClient(
        base_url=base, timeout=30.0, headers=headers
    ) as client:
        for submission in submissions:
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
                        "provider": submission.provider,
                        "ref": submission.ref,
                        "workflowId": job_id,
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


async def _submit_jobs(
    submissions: list[ChildSubmission],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    moonmind_url = _text(os.getenv("MOONMIND_URL"))
    if moonmind_url:
        return await _submit_jobs_via_http(
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
    path.write_text(json.dumps(payload, indent=2))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue one child MoonMind workflow per resolved issue target."
    )
    parser.add_argument(
        "--targets", required=True, help="Path to resolved targets JSON."
    )
    parser.add_argument("--run-ref", required=True)
    parser.add_argument("--publish-mode", default="pr")
    parser.add_argument("--constraints", default=None)
    parser.add_argument("--constraints-file", default=None)
    parser.add_argument("--max-workflows", type=int, default=25)
    parser.add_argument("--task-context-path", default=None)
    parser.add_argument("--artifacts-dir", default="artifacts")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    targets_path = Path(args.targets)
    if not targets_path.exists():
        raise RuntimeError(f"targets file not found: {targets_path}")
    targets = _read_targets(targets_path)
    constraints = _read_constraints(args)
    runtime = _resolve_runtime_selection(args.task_context_path)
    batch_repository = _load_parent_repository(args.task_context_path)
    batch_scope = _parent_run_scope(args.task_context_path)
    inherit_from_caller = _task_workflow_id_from_env() is not None
    target_kind, target_slug = parse_run_ref(args.run_ref)

    config = TargetConfig(
        target_kind=target_kind,
        target_slug=target_slug,
        publish_mode=_normalize_publish_mode(args.publish_mode),
        constraints=constraints,
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
    created, errors = await _submit_jobs(submissions)

    payload = {
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
        "requested": len(targets),
        "created": len(created),
        "queued": created,
        "skipped": [{"ref": item.ref, "reason": item.reason} for item in skipped],
        "errors": errors,
    }
    if payload["created"] == 0:
        payload["message"] = "No child workflows were queued."

    artifacts_dir = _resolve_artifacts_dir(args.artifacts_dir)
    _write_artifacts(artifacts_dir / "batch-workflows-result.json", payload)
    if payload["created"] == 0 and not errors:
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
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 - surface root cause before exiting
        # Print the exception message and full traceback to stderr so runtime
        # failures (missing files, JSON decode errors, HTTP errors) are
        # diagnosable instead of being swallowed behind a generic message.
        print(f"error: batch-workflows failed: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)
