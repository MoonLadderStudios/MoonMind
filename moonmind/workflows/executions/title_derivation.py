"""Deterministic workflow title synthesis for task-shaped submissions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from moonmind.workflows.temporal.title_search import tokenize_title

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
    "run preset",
    "run workflow",
    "load jira preset brief",
    "check jira blockers before implementation",
    "move jira issue to in progress",
}

_ACRONYMS = {
    "ci": "CI",
    "github": "GitHub",
    "jira": "Jira",
    "pr": "PR",
}

_REPOSITORY_KEYS = {
    "repository",
    "repositoryref",
    "repositoryurl",
    "repository_url",
    "reporef",
    "repo",
    "repo_ref",
    "repourl",
    "repo_url",
    "giturl",
}
_ISSUE_KEYS = {
    "issue",
    "issue_key",
    "issue_url",
    "issuekey",
    "issueurl",
    "jira_issue",
    "jira_issue_key",
    "jira_issue_url",
    "jiraissue",
    "jiraissuekey",
    "jiraissueurl",
}
_PR_KEYS = {
    "pr",
    "prnumber",
    "pr_number",
    "prurl",
    "pr_url",
    "pullrequest",
    "pull_request",
    "pullrequestnumber",
    "pull_request_number",
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
_PR_URL_RE = re.compile(r"/pull/(\d+)(?=$|[/?#]|\b)", re.IGNORECASE)
_PR_TEXT_RE = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)\b", re.IGNORECASE)
_GITHUB_SHORTHAND_RE = re.compile(r"(?<![\w/])#(\d+)\b")
_MAX_PR_NUMBER_DIGITS = 10


@dataclass(frozen=True)
class TitleTarget:
    kind: Literal["jira_issue", "github_issue", "pull_request", "branch", "check"]
    provider: str | None
    key: str
    summary: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SynthesizedWorkflowTitle:
    display_title: str
    summary: str | None
    source: Literal[
        "user_explicit",
        "preset_template",
        "integration_target",
        "capability_target",
        "fallback",
    ]
    confidence: Literal["high", "medium", "low"]
    targets: tuple[TitleTarget, ...]
    search_tokens: tuple[str, ...]


@dataclass(frozen=True)
class _RankedTitleTarget:
    kind: str
    value: str
    priority: int
    path: str


def is_generic_title(title: str | None) -> bool:
    normalized = re.sub(r"[\W_]+", " ", str(title or "").strip().casefold())
    normalized = " ".join(normalized.split())
    return normalized in _GENERIC_TITLES


def synthesize_workflow_title(
    *,
    current_title: str | None,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> str | None:
    result = synthesize_execution_title(
        requested_title=current_title,
        parameters={"workflow": task_payload},
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    )
    if result.source == "fallback" and not result.targets:
        return None
    return result.display_title or None


def synthesize_execution_title(
    *,
    requested_title: str | None,
    parameters: Mapping[str, Any] | None,
    workflow_type: str | None = None,
    repository: str | None = None,
    integration: str | None = None,
    summary: str | None = None,
    normalized_tool: Mapping[str, Any] | None = None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> SynthesizedWorkflowTitle:
    params = parameters or {}
    task_payload = _execution_task_payload(params)
    current_title = requested_title
    if current_title is None:
        current_title = _clean_text(task_payload.get("title"))
    explicit = str(current_title or "").strip()
    if explicit and not _is_generated_title(explicit, task_payload, normalized_steps):
        return _title_result(
            display_title=explicit[:_MAX_TASK_TITLE_LENGTH],
            summary=summary,
            source="user_explicit",
            confidence="high",
            targets=(),
        )

    jira_result = _synthesize_jira_preset_title(
        task_payload=task_payload,
        integration=integration,
        summary=summary,
    )
    if jira_result is not None:
        return jira_result

    display_title = _synthesize_legacy_target_title(
        current_title=current_title,
        task_payload=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    )
    if display_title:
        return _title_result(
            display_title=display_title,
            summary=summary,
            source="capability_target",
            confidence="medium",
            targets=(),
        )

    fallback = _workflow_type_fallback(workflow_type)
    return _title_result(
        display_title=fallback,
        summary=summary,
        source="fallback",
        confidence="low",
        targets=(),
    )


def enrich_jira_implement_title(
    *,
    current_title: str | None,
    title_source: str | None,
    issue: Mapping[str, Any],
) -> SynthesizedWorkflowTitle | None:
    if title_source not in {"preset_template", "integration_target"}:
        return None
    target = _coerce_jira_title_target(issue)
    if target is None or not target.summary:
        return None
    key_only_title = f"Jira Implement: {target.key}"
    enriched_title = f"{key_only_title} — {target.summary}"
    if current_title == enriched_title:
        return None
    if current_title not in {key_only_title, "Jira Implement"}:
        return None
    return _title_result(
        display_title=enriched_title,
        summary=target.summary,
        source="integration_target",
        confidence="high",
        targets=(target,),
    )


def _synthesize_legacy_target_title(
    *,
    current_title: str | None,
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]],
) -> str | None:
    explicit = str(current_title or "").strip()
    if explicit and not _is_generated_title(explicit, task_payload, normalized_steps):
        return explicit

    label = _capability_label(task_payload, normalized_tool, normalized_steps)
    targets = _collect_structured_targets(task_payload)
    fallback_targets = _collect_text_fallback_targets(current_title, task_payload)
    if not targets:
        targets = fallback_targets
    elif fallback_targets and not any(
        target.kind in {"issue", "pull_request"} for target in targets
    ):
        targets = _rank_targets([*targets, *fallback_targets])
    if not targets:
        return None

    rendered_targets = " — ".join(target.value for target in targets[:2])
    return f"{label}: {rendered_targets}"[:_MAX_TASK_TITLE_LENGTH]


def _title_result(
    *,
    display_title: str,
    summary: str | None,
    source: Literal[
        "user_explicit",
        "preset_template",
        "integration_target",
        "capability_target",
        "fallback",
    ],
    confidence: Literal["high", "medium", "low"],
    targets: tuple[TitleTarget, ...],
) -> SynthesizedWorkflowTitle:
    return SynthesizedWorkflowTitle(
        display_title=display_title[:_MAX_TASK_TITLE_LENGTH],
        summary=summary,
        source=source,
        confidence=confidence,
        targets=targets,
        search_tokens=tuple(tokenize_title(display_title)),
    )


def _execution_task_payload(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
    workflow_payload = _mapping(parameters.get("workflow"))
    if workflow_payload is not None:
        return workflow_payload
    task_payload = _mapping(parameters.get("task"))
    if task_payload is not None:
        return task_payload
    return parameters


def _is_generated_title(
    title: str | None,
    task_payload: Mapping[str, Any],
    normalized_steps: Sequence[Mapping[str, Any]],
) -> bool:
    if is_generic_title(title):
        return True
    explicit = str(title or "").strip()
    if not explicit:
        return True
    steps: list[Mapping[str, Any]] = list(normalized_steps)
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list):
        steps.extend(item for item in raw_steps if isinstance(item, Mapping))
    preset_slug = _preset_slug(task_payload)
    if preset_slug and len(steps) > 1:
        first_title = _clean_text(steps[0].get("title")) if steps else ""
        if first_title and first_title == explicit:
            return True
    return False


def _preset_slug(task_payload: Mapping[str, Any]) -> str:
    for source in (
        _mapping(task_payload.get("taskTemplate")),
        _mapping(task_payload.get("preset")),
        _mapping(task_payload.get("template")),
    ):
        if source is None:
            continue
        value = _clean_text(source.get("slug") or source.get("id") or source.get("name"))
        if value:
            return value
    return ""


def _synthesize_jira_preset_title(
    *,
    task_payload: Mapping[str, Any],
    integration: str | None,
    summary: str | None,
) -> SynthesizedWorkflowTitle | None:
    preset_slug = _preset_slug(task_payload)
    if preset_slug != "jira-implement":
        return None
    label = _jira_title_label(task_payload)
    target = _extract_jira_title_target(task_payload)
    if target is None:
        return _title_result(
            display_title=label,
            summary=summary,
            source="preset_template" if preset_slug else "fallback",
            confidence="medium" if preset_slug else "low",
            targets=(),
        )
    rendered = f"{label}: {target.key}"
    if target.summary:
        rendered = f"{rendered} — {target.summary}"
    return _title_result(
        display_title=rendered,
        summary=target.summary or summary,
        source="integration_target",
        confidence="high",
        targets=(target,),
    )


def _jira_title_label(task_payload: Mapping[str, Any]) -> str:
    annotations = _mapping(task_payload.get("annotations")) or {}
    synthesis = _mapping(annotations.get("titleSynthesis")) or {}
    label = _clean_text(synthesis.get("label"))
    if label:
        return label
    template = _mapping(task_payload.get("taskTemplate"))
    if template is not None:
        label = _clean_text(template.get("title") or template.get("label"))
        if label:
            return label
    return "Jira Implement"


def _extract_jira_title_target(task_payload: Mapping[str, Any]) -> TitleTarget | None:
    candidates: list[Any] = []
    inputs = _mapping(task_payload.get("inputs")) or {}
    candidates.extend(
        [
            inputs.get("jira_issue"),
            inputs.get("jira_issue_key"),
            inputs.get("jiraIssue"),
            inputs.get("jiraIssueKey"),
            task_payload.get("jira_issue"),
            task_payload.get("jira_issue_key"),
        ]
    )
    for item in task_payload.get("appliedStepTemplates") or ():
        if isinstance(item, Mapping):
            item_inputs = _mapping(item.get("inputs")) or {}
            candidates.extend(
                [item_inputs.get("jira_issue"), item_inputs.get("jira_issue_key")]
            )
    traceability = _mapping(task_payload.get("traceability")) or {}
    candidates.append(traceability.get("sourceIssueKey"))

    for candidate in candidates:
        target = _coerce_jira_title_target(candidate)
        if target is not None:
            return target
    return None


def _coerce_jira_title_target(value: Any) -> TitleTarget | None:
    if isinstance(value, Mapping):
        key = _clean_text(value.get("key") or value.get("issueKey"))
        issue_key = _extract_issue(key)
        if not issue_key:
            return None
        return TitleTarget(
            kind="jira_issue",
            provider="jira",
            key=issue_key,
            summary=_bounded_summary(value.get("summary")),
            url=_clean_text(value.get("url")) or None,
        )
    text = _clean_text(value)
    issue_key = _extract_issue(text)
    if not issue_key:
        return None
    return TitleTarget(kind="jira_issue", provider="jira", key=issue_key)


def _bounded_summary(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    return " ".join(text.split())[:80]


def _workflow_type_fallback(workflow_type: str | None) -> str:
    value = str(workflow_type or "").strip()
    if value.endswith(".UserWorkflow"):
        return "Workflow"
    return _identifier_to_label(value.rsplit(".", 1)[-1]) if value else "Workflow"


def _capability_label(
    task_payload: Mapping[str, Any],
    normalized_tool: Mapping[str, Any] | None,
    normalized_steps: Sequence[Mapping[str, Any]],
) -> str:
    raw_tool_payloads = [
        payload
        for payload in (
            _mapping(task_payload.get("tool")),
            _mapping(task_payload.get("skill")),
        )
        if payload is not None
    ]
    workflow_payload = _mapping(task_payload.get("workflow"))
    if workflow_payload is not None:
        raw_tool_payloads.extend(
            payload
            for payload in (
                _mapping(workflow_payload.get("tool")),
                _mapping(workflow_payload.get("skill")),
            )
            if payload is not None
        )

    tool_payloads = [
        payload
        for payload in (normalized_tool, *raw_tool_payloads)
        if payload is not None
    ]

    for tool_payload in tool_payloads:
        for key in ("label", "displayName", "display_name"):
            value = _clean_text(tool_payload.get(key))
            if value:
                return value
        value = _clean_text(tool_payload.get("title"))
        if value and not is_generic_title(value):
            return value

    for tool_payload in tool_payloads:
        for key in ("name", "id", "slug"):
            value = _clean_text(tool_payload.get(key))
            if value:
                return _identifier_to_label(value)
        value = _clean_text(tool_payload.get("type"))
        if value and not is_generic_title(value) and value.casefold() != "skill":
            return _identifier_to_label(value)

    if len(normalized_steps) == 1:
        value = _clean_text(normalized_steps[0].get("title"))
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


def _collect_structured_targets(task_payload: Mapping[str, Any]) -> list[_RankedTitleTarget]:
    targets: list[_RankedTitleTarget] = []
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
    targets: list[_RankedTitleTarget],
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
            targets.append(_RankedTitleTarget("issue", issue, 0, path))
            return
        pr = _extract_pr(text)
        if pr:
            targets.append(_RankedTitleTarget("pull_request", pr, 1, path))
            return
        return
    if key in _PR_KEYS:
        pr = _extract_pr(text, allow_bare_number=True, allow_shorthand=True)
        if pr:
            targets.append(_RankedTitleTarget("pull_request", pr, 1, path))
            return
        issue = _extract_issue(text)
        if issue:
            targets.append(_RankedTitleTarget("issue", issue, 0, path))
            return
        return
    if key in _BRANCH_KEYS and _looks_like_branch(text):
        targets.append(_RankedTitleTarget("branch", text, 2, path))
        return
    if key in _CHECK_KEYS:
        targets.append(_RankedTitleTarget("check", f"failing check: {text}", 3, path))
        return

    issue = _extract_issue(text)
    if issue:
        targets.append(_RankedTitleTarget("issue", issue, 0, path))
        return
    pr = _extract_pr(text)
    if pr:
        targets.append(_RankedTitleTarget("pull_request", pr, 1, path))
        return


def _collect_text_fallback_targets(
    current_title: str | None,
    task_payload: Mapping[str, Any],
) -> list[_RankedTitleTarget]:
    candidates = [_clean_text(current_title), _clean_text(task_payload.get("instructions"))]
    workflow = _mapping(task_payload.get("workflow"))
    if workflow is not None:
        candidates.append(_clean_text(workflow.get("instructions")))

    targets: list[_RankedTitleTarget] = []
    for index, text in enumerate(candidate for candidate in candidates if candidate):
        issue = _extract_issue(text)
        if issue:
            targets.append(_RankedTitleTarget("issue", issue, 0, f"text[{index}]"))
        pr = _extract_pr(text, allow_shorthand=True)
        if pr:
            targets.append(_RankedTitleTarget("pull_request", pr, 1, f"text[{index}]"))
    return _rank_targets(targets)


def _rank_targets(targets: list[_RankedTitleTarget]) -> list[_RankedTitleTarget]:
    seen: set[tuple[str, str]] = set()
    seen_kinds: set[str] = set()
    unique: list[_RankedTitleTarget] = []
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


def _extract_pr(
    text: str,
    *,
    allow_bare_number: bool = False,
    allow_shorthand: bool = False,
) -> str | None:
    patterns = [_PR_URL_RE, _PR_TEXT_RE]
    if allow_shorthand:
        patterns.append(_GITHUB_SHORTHAND_RE)
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _render_pr_number(match.group(1))
    if allow_bare_number and text.isdigit():
        return _render_pr_number(text)
    return None


def _render_pr_number(digits: str) -> str | None:
    pr_num = digits.lstrip("0") or "0"
    if len(pr_num) > _MAX_PR_NUMBER_DIGITS:
        return None
    return f"PR #{pr_num}"


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
