"""Deterministic workflow title synthesis for task-shaped submissions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_MAX_TASK_TITLE_LENGTH = 150
_MAX_TRAVERSAL_DEPTH = 6
_MAX_VISITED_VALUES = 200

_GENERIC_TITLES = {
    "",
    "run",
    "new run",
    "untitled",
    "untitled run",
    "workflow",
    "new workflow",
}

_ACRONYMS = {
    "ci": "CI",
    "github": "GitHub",
    "jira": "Jira",
    "pr": "PR",
}

_REPOSITORY_KEYS = {"repository", "repo", "reporef", "repo_ref"}
_ISSUE_KEYS = {"jiraissuekey", "jira_issue_key", "issuekey", "issue", "issueurl"}
_PR_KEYS = {
    "pr",
    "prnumber",
    "pr_number",
    "prurl",
    "pr_url",
    "pullrequest",
    "pull_request",
    "pullrequesturl",
    "pull_request_url",
}
_BRANCH_KEYS = {
    "branch",
    "ref",
    "startingbranch",
    "starting_branch",
    "headbranch",
    "head_branch",
}
_CHECK_KEYS = {"check", "checkname", "check_name", "job", "jobname", "job_name"}
_IGNORED_VALUE_KEYS = {
    "effort",
    "mode",
    "model",
    "profileid",
    "profile_id",
    "requestedmodel",
    "requested_model",
    "targetruntime",
    "target_runtime",
}

_ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_PR_URL_RE = re.compile(r"/pull/(\d+)(?:\b|/)?", re.IGNORECASE)
_PR_TEXT_RE = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)\b", re.IGNORECASE)
_GITHUB_SHORTHAND_RE = re.compile(r"(?<![\w/])#(\d+)\b")


@dataclass(frozen=True)
class _TitleTarget:
    kind: str
    value: str
    priority: int
    path: str


def is_generic_title(title: str | None) -> bool:
    normalized = re.sub(r"[^\w\s]+", " ", str(title or "").strip().casefold())
    normalized = " ".join(normalized.split())
    return normalized in _GENERIC_TITLES


def synthesize_workflow_title(
    *,
    current_title: str | None,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    explicit = str(current_title or "").strip()
    if explicit and not is_generic_title(explicit):
        return explicit
    if explicit == "" and current_title is not None and not is_generic_title(current_title):
        return current_title

    label = _capability_label(task_payload, normalized_tool, normalized_steps)
    targets = _collect_structured_targets(task_payload)
    if not targets:
        targets = _collect_text_fallback_targets(current_title, task_payload)
    if not targets:
        return None

    rendered_targets = " — ".join(target.value for target in targets[:2])
    return f"{label}: {rendered_targets}"[:_MAX_TASK_TITLE_LENGTH]


def _capability_label(
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]],
) -> str:
    tool_payload = normalized_tool or _mapping(task_payload.get("tool")) or _mapping(
        task_payload.get("skill")
    )
    workflow_payload = _mapping(task_payload.get("workflow"))
    if tool_payload is None and workflow_payload is not None:
        tool_payload = _mapping(workflow_payload.get("tool")) or _mapping(
            workflow_payload.get("skill")
        )

    if tool_payload is not None:
        for key in ("label", "displayName", "display_name", "title"):
            value = _clean_text(tool_payload.get(key))
            if value:
                return value
        for key in ("name", "id", "slug", "type"):
            value = _clean_text(tool_payload.get(key))
            if value:
                return _identifier_to_label(value)

    for step in normalized_steps:
        value = _clean_text(step.get("title"))
        if value:
            return value

    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list) and len(raw_steps) == 1:
        step = _mapping(raw_steps[0])
        value = _clean_text(step.get("title") if step else None)
        if value:
            return value

    return "Workflow"


def _identifier_to_label(value: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    words = re.sub(r"[-_.:/]+", " ", words)
    rendered: list[str] = []
    for word in words.split():
        normalized = word.casefold()
        rendered.append(_ACRONYMS.get(normalized, word[:1].upper() + word[1:].lower()))
    return " ".join(rendered) or "Workflow"


def _collect_structured_targets(task_payload: Mapping[str, Any]) -> list[_TitleTarget]:
    targets: list[_TitleTarget] = []
    visited = 0

    def walk(value: Any, path: str, depth: int, parent_key: str = "") -> None:
        nonlocal visited
        if visited >= _MAX_VISITED_VALUES or depth > _MAX_TRAVERSAL_DEPTH:
            return
        visited += 1

        if isinstance(value, Mapping):
            for raw_key, raw_child in value.items():
                key = str(raw_key)
                normalized_key = _normalize_key(key)
                if normalized_key in _REPOSITORY_KEYS or normalized_key in _IGNORED_VALUE_KEYS:
                    continue
                child_path = f"{path}.{key}" if path else key
                if normalized_key in {"instructions", "title"}:
                    continue
                _append_targets_for_value(
                    targets=targets,
                    key=normalized_key,
                    value=raw_child,
                    path=child_path,
                )
                walk(raw_child, child_path, depth + 1, normalized_key)
            return

        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", depth + 1, parent_key)

    walk(task_payload, "", 0)
    return _rank_targets(targets)


def _append_targets_for_value(
    *,
    targets: list[_TitleTarget],
    key: str,
    value: Any,
    path: str,
) -> None:
    text = _clean_text(value)
    if not text:
        return

    if key in _ISSUE_KEYS:
        issue = _extract_issue(text)
        if issue:
            targets.append(_TitleTarget("issue", issue, 0, path))
            return
    if key in _PR_KEYS:
        pr = _extract_pr(text)
        if pr:
            targets.append(_TitleTarget("pull_request", pr, 1, path))
            return
    if key in _BRANCH_KEYS and _looks_like_branch(text):
        targets.append(_TitleTarget("branch", text, 2, path))
        return
    if key in _CHECK_KEYS:
        targets.append(_TitleTarget("check", f"failing check: {text}", 3, path))
        return

    issue = _extract_issue(text)
    if issue:
        targets.append(_TitleTarget("issue", issue, 0, path))
        return
    pr = _extract_pr(text)
    if pr:
        targets.append(_TitleTarget("pull_request", pr, 1, path))
        return


def _collect_text_fallback_targets(
    current_title: str | None,
    task_payload: Mapping[str, Any],
) -> list[_TitleTarget]:
    candidates = [_clean_text(current_title), _clean_text(task_payload.get("instructions"))]
    workflow = _mapping(task_payload.get("workflow"))
    if workflow is not None:
        candidates.append(_clean_text(workflow.get("instructions")))

    targets: list[_TitleTarget] = []
    for index, text in enumerate(candidate for candidate in candidates if candidate):
        issue = _extract_issue(text)
        if issue:
            targets.append(_TitleTarget("issue", issue, 0, f"text[{index}]"))
            continue
        pr = _extract_pr(text)
        if pr:
            targets.append(_TitleTarget("pull_request", pr, 1, f"text[{index}]"))
    return _rank_targets(targets)


def _rank_targets(targets: list[_TitleTarget]) -> list[_TitleTarget]:
    seen: set[tuple[str, str]] = set()
    seen_kinds: set[str] = set()
    unique: list[_TitleTarget] = []
    for target in sorted(targets, key=lambda item: item.priority):
        key = (target.kind, target.value)
        if key in seen or target.kind in seen_kinds:
            continue
        seen.add(key)
        seen_kinds.add(target.kind)
        unique.append(target)
    return unique


def _extract_issue(text: str) -> str | None:
    match = _ISSUE_RE.search(text.upper())
    return match.group(1) if match else None


def _extract_pr(text: str) -> str | None:
    for pattern in (_PR_URL_RE, _PR_TEXT_RE, _GITHUB_SHORTHAND_RE):
        match = pattern.search(text)
        if match:
            return f"PR #{int(match.group(1))}"
    if text.isdigit():
        return f"PR #{int(text)}"
    return None


def _looks_like_branch(text: str) -> bool:
    return bool(text and not text.isspace())


def _clean_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "", key.casefold())
