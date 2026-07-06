from __future__ import annotations

import re
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_WORKFLOW_TITLE_LENGTH = 150

_GENERIC_TITLE_TOKENS = {
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
_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_PR_URL_RE = re.compile(r"/pull/(\d+)(?:\b|[/?#])", re.IGNORECASE)
_PR_TEXT_RE = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)\b", re.IGNORECASE)
_GITHUB_SHORTHAND_RE = re.compile(r"^#(\d+)$")

_ISSUE_FIELDS = {
    "jira_issue_key",
    "jiraissuekey",
    "issuekey",
    "issue",
    "issueurl",
}
_PR_FIELDS = {
    "pr",
    "pullrequest",
    "pull_request",
    "pullrequesturl",
    "prurl",
}
_BRANCH_FIELDS = {
    "branch",
    "startingbranch",
    "headbranch",
}
_CHECK_FIELDS = {
    "check",
    "checkname",
    "job",
    "jobname",
}
_REPOSITORY_FIELDS = {
    "repo",
    "reporef",
    "repository",
    "repositoryurl",
}
_CONTAINER_FIELDS = {
    "workflow",
    "tool",
    "skill",
    "steps",
    "inputs",
    "git",
    "instructions",
    "title",
    "type",
    "name",
    "id",
    "version",
    "label",
    "displayname",
    "display_name",
    "mode",
    "model",
    "effort",
    "targetruntime",
    "target_runtime",
    "runtime",
}
_SEMANTIC_FIELDS = (
    _ISSUE_FIELDS
    | _PR_FIELDS
    | _BRANCH_FIELDS
    | _CHECK_FIELDS
    | _REPOSITORY_FIELDS
    | _CONTAINER_FIELDS
)
_TARGET_RANK = {
    "issue": 0,
    "pull_request": 1,
    "branch": 2,
    "check": 3,
    "titled_input": 4,
    "short_text": 5,
}


@dataclass(frozen=True)
class TitleTarget:
    kind: str
    value: str
    rank: int


def is_generic_title(title: str | None) -> bool:
    text = "" if title is None else str(title).strip()
    punctuation_as_space = str.maketrans(
        {character: " " for character in string.punctuation}
    )
    normalized = text.translate(punctuation_as_space).lower()
    normalized = " ".join(normalized.split())
    return normalized in _GENERIC_TITLE_TOKENS


def capability_label_from_payload(
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    for source in (
        normalized_tool,
        _mapping(task_payload.get("tool")),
        _mapping(task_payload.get("skill")),
    ):
        if not source:
            continue
        label = _first_text(source, ("label", "displayName", "display_name", "title"))
        if label:
            return label
        identifier = _first_text(source, ("name", "id", "skill", "tool"))
        if identifier:
            return _identifier_to_label(identifier)

    steps = list(normalized_steps)
    if len(steps) == 1:
        title = _first_text(steps[0], ("title",))
        if title:
            return title

    return "Workflow"


def collect_title_targets(payload: Mapping[str, Any]) -> list[TitleTarget]:
    targets: list[TitleTarget] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        normalized = (kind, text.lower())
        if normalized in seen:
            return
        seen.add(normalized)
        targets.append(TitleTarget(kind=kind, value=text, rank=_TARGET_RANK[kind]))

    for key, value in _walk_values(payload):
        field = _normalize_field_name(key)
        text = _scalar_text(value)
        if not text or field in _REPOSITORY_FIELDS:
            continue

        if field in _ISSUE_FIELDS:
            issue = _extract_issue_key(text)
            if issue:
                add("issue", issue)
                continue
        if field in _PR_FIELDS:
            pr = _extract_pr_target(text)
            if pr:
                add("pull_request", pr)
                continue
        if field in _BRANCH_FIELDS:
            add("branch", text)
            continue
        if field in _CHECK_FIELDS:
            add("check", f"failing check: {text}")
            continue

        issue = _extract_issue_key(text)
        if issue:
            add("issue", issue)
            continue
        pr = _extract_pr_target(text)
        if pr:
            add("pull_request", pr)
            continue
        if field not in _SEMANTIC_FIELDS and _is_useful_short_text(text):
            add("short_text", text)

    return sorted(targets, key=lambda item: item.rank)


def render_title(
    label: str,
    targets: Sequence[TitleTarget],
    *,
    max_length: int = MAX_WORKFLOW_TITLE_LENGTH,
) -> str | None:
    if not label.strip() or not targets:
        return None

    selected: list[TitleTarget] = [targets[0]]
    for candidate in targets[1:]:
        if candidate.kind != selected[0].kind:
            selected.append(candidate)
            break

    target_text = " \u2014 ".join(target.value for target in selected)
    title = f"{label.strip()}: {target_text}"
    return title[:max_length]


def synthesize_workflow_title(
    *,
    current_title: str | None,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    explicit = str(current_title or "").strip()
    if explicit and not is_generic_title(explicit):
        return explicit[:MAX_WORKFLOW_TITLE_LENGTH]
    if not is_generic_title(current_title):
        return None

    label = capability_label_from_payload(
        task_payload,
        normalized_tool,
        normalized_steps,
    )
    if not label:
        return None
    targets = collect_title_targets(task_payload)
    if (
        label == "Workflow"
        and targets
        and targets[0].rank >= _TARGET_RANK["titled_input"]
    ):
        return None
    return render_title(label, targets)


def _identifier_to_label(identifier: str) -> str:
    tokens = re.split(r"[-_\s]+", identifier.strip())
    rendered = [
        _ACRONYMS.get(token.lower(), token.capitalize())
        for token in tokens
        if token
    ]
    return " ".join(rendered)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _first_text(source: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_field_name(key: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", key.strip().lower())


def _scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _walk_values(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
    max_depth: int = 5,
) -> list[tuple[str, Any]]:
    if depth > max_depth:
        return []
    if isinstance(value, Mapping):
        items: list[tuple[str, Any]] = []
        for child_key, child_value in value.items():
            text_key = str(child_key)
            items.extend(
                _walk_values(
                    child_value,
                    key=text_key,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            )
        return items
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = []
        for child in value:
            items.extend(
                _walk_values(child, key=key, depth=depth + 1, max_depth=max_depth)
            )
        return items
    return [(key, value)]


def _extract_issue_key(text: str) -> str | None:
    match = _ISSUE_KEY_RE.search(text)
    return match.group(1) if match else None


def _extract_pr_target(text: str) -> str | None:
    for pattern in (_PR_URL_RE, _PR_TEXT_RE, _GITHUB_SHORTHAND_RE):
        match = pattern.search(text)
        if match:
            return f"PR #{match.group(1)}"
    if text.isdigit():
        return f"PR #{text}"
    return None


def _is_useful_short_text(text: str) -> bool:
    if len(text) > 80:
        return False
    if is_generic_title(text):
        return False
    return bool(re.search(r"[A-Za-z0-9]", text))
