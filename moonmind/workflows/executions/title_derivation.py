"""Deterministic workflow title synthesis for task-shaped submissions."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

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
_GENERATED_STEP_TITLES = {
    "load jira preset brief",
    "check jira blockers before implementation",
    "move jira issue to in progress",
    "run preset",
    "run workflow",
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
    "github_issue",
    "github_issue_ref",
    "githubissue",
    "githubissueref",
    "issue",
    "issue_key",
    "issue_ref",
    "issue_url",
    "issuekey",
    "issueref",
    "issueurl",
    "jira_issue",
    "jira_issue_key",
    "jira_issue_url",
    "jiraissue",
    "jiraissuekey",
    "jiraissueurl",
    "sourceissuekey",
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
    "id",
    "mode",
    "model",
    "profileid",
    "profile_id",
    "requestedmodel",
    "requested_model",
    "targetruntime",
    "target_runtime",
    "stepid",
    "step_id",
    "templatestepid",
    "template_step_id",
}

_ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_GITHUB_ISSUE_REF_RE = re.compile(
    r"(?<![\w/])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)\b"
)
_GITHUB_ISSUE_URL_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)"
    r"(?=$|[/?#]|\b)",
    re.IGNORECASE,
)
_PR_URL_RE = re.compile(r"/pull/(\d+)(?=$|[/?#]|\b)", re.IGNORECASE)
_PR_TEXT_RE = re.compile(r"\b(?:PR|pull request)\s*#?(\d+)\b", re.IGNORECASE)
_GITHUB_SHORTHAND_RE = re.compile(r"(?<![\w/])#(\d+)\b")
_MAX_PR_NUMBER_DIGITS = 10
_MAX_GITHUB_REPOSITORY_LENGTH = 200
_GITHUB_REPOSITORY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


@dataclass(frozen=True)
class TitleTarget:
    kind: str
    value: str
    priority: int
    path: str
    provider: str | None = None
    key: str | None = None
    summary: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SynthesizedWorkflowTitle:
    display_title: str | None
    summary: str | None
    source: Literal[
        "user_explicit",
        "preset_template",
        "integration_target",
        "capability_target",
        "fallback",
    ]
    confidence: Literal["high", "medium", "low"]
    targets: tuple[TitleTarget, ...] = ()
    search_tokens: tuple[str, ...] = ()


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
        workflow_type=None,
        parameters=task_payload,
        normalized_tool=normalized_tool,
        normalized_steps=normalized_steps,
    )
    return result.display_title


def synthesize_execution_title(
    *,
    requested_title: str | None,
    workflow_type: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    repository: str | None = None,
    integration: str | None = None,
    summary: str | None = None,
    normalized_tool: Mapping[str, Any] | None = None,
    normalized_steps: Sequence[Mapping[str, Any]] = (),
) -> SynthesizedWorkflowTitle:
    task_payload = parameters or {}
    explicit = str(requested_title or "").strip()
    label = _capability_label(task_payload, normalized_tool, normalized_steps)
    generated_explicit = _is_generated_title(
        explicit,
        label=label,
        task_payload=task_payload,
        normalized_steps=normalized_steps,
    )

    targets = _collect_structured_targets(task_payload)
    fallback_targets = _collect_text_fallback_targets(requested_title, task_payload)
    if not targets:
        targets = fallback_targets
    elif fallback_targets and not any(
        target.kind in {"issue", "pull_request"} for target in targets
    ):
        targets = _rank_targets([*targets, *fallback_targets])
    synthesized_title = _render_title(label, targets)

    if (
        explicit
        and not is_generic_title(explicit)
        and not generated_explicit
        and explicit != synthesized_title
    ):
        return SynthesizedWorkflowTitle(
            display_title=explicit[:_MAX_TASK_TITLE_LENGTH],
            summary=summary,
            source="user_explicit",
            confidence="high",
            targets=tuple(targets),
        )

    if synthesized_title:
        return SynthesizedWorkflowTitle(
            display_title=synthesized_title,
            summary=summary,
            source=_target_source(targets, integration=integration),
            confidence="high",
            targets=tuple(targets),
        )

    fallback_title = _preset_fallback_title(label=label, task_payload=task_payload)
    if fallback_title:
        return SynthesizedWorkflowTitle(
            display_title=fallback_title,
            summary=summary,
            source="preset_template",
            confidence="medium",
        )

    workflow_fallback = _workflow_type_fallback(workflow_type)
    return SynthesizedWorkflowTitle(
        display_title=workflow_fallback,
        summary=summary,
        source="fallback",
        confidence="low",
    )


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

    template_payloads = _preset_payloads(task_payload)
    # A selected preset is the workflow-level capability. Its label must win over
    # the normalized first-step tool, which is only one implementation detail of
    # the expanded preset.
    tool_payloads = [
        payload
        for payload in (*template_payloads, normalized_tool, *raw_tool_payloads)
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


def _collect_structured_targets(task_payload: Mapping[str, Any]) -> list[TitleTarget]:
    targets: list[TitleTarget] = []
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
                if (
                    normalized_key in _REPOSITORY_KEYS
                    or normalized_key in _IGNORED_VALUE_KEYS
                ):
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
    targets: list[TitleTarget],
    key: str,
    value: Any,
    path: str,
) -> None:
    if key in _ISSUE_KEYS and isinstance(value, Mapping):
        provider = _issue_provider_for_key(key)
        issue = _extract_issue_from_mapping(
            value,
            path=path,
            provider=provider,
        )
        if issue is not None:
            targets.append(issue)
            return

    text = _clean_text(value)
    if not text:
        return

    if key in _ISSUE_KEYS:
        provider = _issue_provider_for_key(key)
        github_issue = _extract_github_issue(text)
        if github_issue and provider != "jira":
            targets.append(
                TitleTarget(
                    "issue",
                    github_issue,
                    0,
                    path,
                    provider="github",
                    key=github_issue,
                )
            )
            return
        issue = _extract_issue(text)
        if issue:
            targets.append(
                TitleTarget(
                    "issue",
                    issue,
                    0,
                    path,
                    provider="jira" if provider == "jira" else None,
                    key=issue,
                )
            )
            return
        pr = _extract_pr(text)
        if pr:
            targets.append(TitleTarget("pull_request", pr, 1, path, key=pr))
            return
        return
    if key in _PR_KEYS:
        pr = _extract_pr(text, allow_bare_number=True, allow_shorthand=True)
        if pr:
            targets.append(TitleTarget("pull_request", pr, 1, path, key=pr))
            return
        issue = _extract_issue(text)
        if issue:
            targets.append(TitleTarget("issue", issue, 0, path, key=issue))
            return
        return
    if key in _BRANCH_KEYS and _looks_like_branch(text):
        targets.append(TitleTarget("branch", text, 2, path, key=text))
        return
    if key in _CHECK_KEYS:
        targets.append(
            TitleTarget("check", f"failing check: {text}", 3, path, key=text)
        )
        return

    issue = _extract_issue(text)
    if issue:
        targets.append(TitleTarget("issue", issue, 0, path, key=issue))
        return
    github_issue = _extract_github_issue(text)
    if github_issue:
        targets.append(
            TitleTarget(
                "issue",
                github_issue,
                0,
                path,
                provider="github",
                key=github_issue,
            )
        )
        return
    pr = _extract_pr(text)
    if pr:
        targets.append(TitleTarget("pull_request", pr, 1, path, key=pr))
        return


def _collect_text_fallback_targets(
    current_title: str | None,
    task_payload: Mapping[str, Any],
) -> list[TitleTarget]:
    candidates = [
        _clean_text(current_title),
        _clean_text(task_payload.get("instructions")),
    ]
    workflow = _mapping(task_payload.get("workflow"))
    if workflow is not None:
        candidates.append(_clean_text(workflow.get("instructions")))

    targets: list[TitleTarget] = []
    for index, text in enumerate(candidate for candidate in candidates if candidate):
        issue = _extract_issue(text)
        if issue:
            targets.append(TitleTarget("issue", issue, 0, f"text[{index}]", key=issue))
        github_issue = _extract_github_issue(text)
        if github_issue:
            targets.append(
                TitleTarget(
                    "issue",
                    github_issue,
                    0,
                    f"text[{index}]",
                    provider="github",
                    key=github_issue,
                )
            )
        pr = _extract_pr(text, allow_shorthand=True)
        if pr:
            targets.append(TitleTarget("pull_request", pr, 1, f"text[{index}]", key=pr))
    return _rank_targets(targets)


def _rank_targets(targets: list[TitleTarget]) -> list[TitleTarget]:
    seen: set[tuple[str, str]] = set()
    seen_kinds: set[str] = set()
    unique: list[TitleTarget] = []
    for target in sorted(targets, key=lambda item: item.priority):
        key = (target.kind, target.value)
        if key in seen or target.kind in seen_kinds:
            continue
        seen.add(key)
        seen_kinds.add(target.kind)
        unique.append(target)
    return unique


def _render_title(label: str, targets: Sequence[TitleTarget]) -> str | None:
    if not targets:
        return None
    rendered_targets = " — ".join(_render_target(target) for target in targets[:2])
    return f"{label}: {rendered_targets}"[:_MAX_TASK_TITLE_LENGTH]


def _render_target(target: TitleTarget) -> str:
    if target.kind == "issue" and target.summary:
        return f"{target.key or target.value} — {target.summary}"
    return target.value


def _target_source(
    targets: Sequence[TitleTarget],
    *,
    integration: str | None,
) -> Literal["integration_target", "capability_target"]:
    normalized_integration = str(integration or "").strip().casefold()
    if normalized_integration or any(target.provider for target in targets):
        return "integration_target"
    if any(target.kind in {"issue", "pull_request"} for target in targets):
        return "integration_target"
    return "capability_target"


def _preset_fallback_title(
    *,
    label: str,
    task_payload: Mapping[str, Any],
) -> str | None:
    if _preset_slug(task_payload):
        return label[:_MAX_TASK_TITLE_LENGTH]
    return None


def _workflow_type_fallback(workflow_type: str | None) -> str | None:
    value = _clean_text(workflow_type)
    if not value:
        return None
    if value.endswith(".UserWorkflow"):
        return "Run"
    return _identifier_to_label(value.rsplit(".", 1)[-1])


def _is_generated_title(
    title: str,
    *,
    label: str,
    task_payload: Mapping[str, Any],
    normalized_steps: Sequence[Mapping[str, Any]],
) -> bool:
    if not title:
        return False
    normalized = " ".join(re.sub(r"[\W_]+", " ", title.casefold()).split())
    if normalized in _GENERATED_STEP_TITLES:
        return True
    preset_slug = _preset_slug(task_payload)
    if not preset_slug:
        return False
    preset_label = _identifier_to_label(preset_slug)
    if title.strip().casefold() in {
        label.strip().casefold(),
        preset_label.strip().casefold(),
    }:
        return True
    first_step_title = _first_step_title(task_payload, normalized_steps)
    return bool(
        first_step_title
        and title.strip().casefold() == first_step_title.strip().casefold()
    )


def _first_step_title(
    task_payload: Mapping[str, Any],
    normalized_steps: Sequence[Mapping[str, Any]],
) -> str | None:
    if len(normalized_steps) > 1:
        value = _clean_text(normalized_steps[0].get("title"))
        if value:
            return value
    raw_steps = task_payload.get("steps")
    if isinstance(raw_steps, list) and len(raw_steps) > 1:
        step = _mapping(raw_steps[0])
        value = _clean_text(step.get("title") if step else None)
        if value:
            return value
    workflow = _mapping(task_payload.get("workflow"))
    if workflow is not None:
        workflow_steps = workflow.get("steps")
        if isinstance(workflow_steps, list) and len(workflow_steps) > 1:
            step = _mapping(workflow_steps[0])
            value = _clean_text(step.get("title") if step else None)
            if value:
                return value
    return None


def _preset_slug(task_payload: Mapping[str, Any]) -> str | None:
    for template in _preset_payloads(task_payload):
        for key in ("slug", "name", "id"):
            value = _clean_text(template.get(key))
            if value:
                return value
    return None


def _preset_payloads(task_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    presets: list[Mapping[str, Any]] = []
    for payload in (task_payload, _mapping(task_payload.get("workflow"))):
        if payload is None:
            continue
        template = _mapping(payload.get("taskTemplate")) or _mapping(
            payload.get("task_template")
        )
        if template is not None:
            presets.append(template)
        applied_templates = payload.get("appliedStepTemplates") or payload.get(
            "applied_step_templates"
        )
        if isinstance(applied_templates, list):
            presets.extend(
                item for item in applied_templates if isinstance(item, Mapping)
            )
    return presets


def _extract_issue_from_mapping(
    value: Mapping[str, Any],
    *,
    path: str,
    provider: str | None,
) -> TitleTarget | None:
    repository = _first_clean_mapping_value(value, "repository", "repo")
    number = _first_clean_mapping_value(
        value,
        "number",
        "issueNumber",
        "issue_number",
    )
    if provider == "github" or (repository and number):
        github_issue = _render_github_issue(repository or "", number or "")
        if not github_issue:
            return None
        issue_summary = _first_clean_mapping_value(value, "summary", "title")
        issue_url = _first_clean_mapping_value(value, "url", "self")
        display_value = github_issue
        if issue_summary:
            display_value = f"{github_issue} — {issue_summary}"
        return TitleTarget(
            "issue",
            display_value,
            0,
            path,
            provider="github",
            key=github_issue,
            summary=issue_summary,
            url=issue_url,
        )

    key = _first_clean_mapping_value(value, "key", "issueKey", "issue_key")
    if key:
        issue_key = _extract_issue(key)
    else:
        issue_key = None
    if not issue_key:
        return None
    issue_summary = _first_clean_mapping_value(value, "summary", "title")
    issue_url = _first_clean_mapping_value(value, "url", "self")
    display_value = issue_key
    if issue_summary:
        display_value = f"{issue_key} — {issue_summary}"
    return TitleTarget(
        "issue",
        display_value,
        0,
        path,
        provider=provider,
        key=issue_key,
        summary=issue_summary,
        url=issue_url,
    )


def _first_clean_mapping_value(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        text = _clean_text(value.get(key))
        if text:
            return text
    return None


def _extract_issue(text: str) -> str | None:
    match = _ISSUE_RE.search(text.upper())
    return match.group(1) if match else None


def _issue_provider_for_key(key: str) -> str | None:
    if "github" in key:
        return "github"
    if "jira" in key:
        return "jira"
    return None


def _extract_github_issue(text: str) -> str | None:
    for pattern in (_GITHUB_ISSUE_URL_RE, _GITHUB_ISSUE_REF_RE):
        match = pattern.search(text)
        if match:
            return _render_github_issue(match.group(1), match.group(2))
    return None


def _render_github_issue(repository: str, digits: str) -> str | None:
    normalized_repository = repository.strip()
    if len(normalized_repository) > _MAX_GITHUB_REPOSITORY_LENGTH:
        return None
    repository_parts = normalized_repository.split("/")
    if len(repository_parts) != 2 or any(not part for part in repository_parts):
        return None
    if any(
        character not in _GITHUB_REPOSITORY_CHARS
        for part in repository_parts
        for character in part
    ):
        return None
    issue_num = digits.lstrip("0")
    if not issue_num or not issue_num.isdigit() or len(issue_num) > _MAX_PR_NUMBER_DIGITS:
        return None
    return f"{normalized_repository}#{issue_num}"


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
