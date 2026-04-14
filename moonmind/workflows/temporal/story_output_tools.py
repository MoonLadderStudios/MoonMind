"""First-party story output tools for workflow plans."""

from __future__ import annotations

import base64
import inspect
import json
import re
from typing import Any, Awaitable, Callable, Mapping, Sequence

import httpx

from moonmind.integrations.jira.models import (
    CreateIssueRequest,
    CreateSubtaskRequest,
    SearchIssuesRequest,
)
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.tool_plan_contracts import ToolResult

JIRA_STORY_TOOL_NAMES = frozenset({"jira-issue-creator", "story.create_jira_issues"})
JIRA_DESCRIPTION_MAX_CHARS = 32767
JIRA_DESCRIPTION_TRUNCATION_SUFFIX = "\n\n[Truncated by MoonMind before Jira export]"

StoryFetcher = Callable[
    [str, str, str],
    str | Awaitable[str],
]
JiraServiceFactory = Callable[[], JiraToolService]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _coerce_story_payload(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, Mapping):
        for key in ("stories", "userStories", "user_stories", "items", "issues"):
            stories = _list(value.get(key))
            if stories:
                return [dict(story) for story in stories if isinstance(story, Mapping)]
        return [dict(value)] if value.get("summary") or value.get("title") else []
    return [dict(story) for story in _list(value) if isinstance(story, Mapping)]


def _story_summary(story: Mapping[str, Any], *, index: int) -> str:
    for key in ("summary", "title", "name", "userStory", "user_story"):
        value = _string(story.get(key))
        if value:
            return value[:255]
    return f"Story {index}"


def _format_story_section(title: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        text = "\n".join(f"- {item}" for item in value if _string(item))
    else:
        text = json.dumps(value, indent=2, sort_keys=True)
    return f"\n\n{title}\n{text}" if text else ""


def _story_description(story: Mapping[str, Any]) -> str:
    description = _string(
        story.get("description")
        or story.get("body")
        or story.get("narrative")
        or story.get("userStory")
        or story.get("user_story")
    )
    sections = [
        _format_story_section(
            "Acceptance Criteria",
            story.get("acceptanceCriteria") or story.get("acceptance_criteria"),
        ),
        _format_story_section("Requirements", story.get("requirements")),
        _format_story_section(
            "Source",
            story.get("source") or story.get("traceability"),
        ),
    ]
    return (description + "".join(sections)).strip() or _story_summary(story, index=1)


def _truncate_jira_description(description: str) -> str:
    if len(description) <= JIRA_DESCRIPTION_MAX_CHARS:
        return description
    limit = JIRA_DESCRIPTION_MAX_CHARS - len(JIRA_DESCRIPTION_TRUNCATION_SUFFIX)
    return description[:limit].rstrip() + JIRA_DESCRIPTION_TRUNCATION_SUFFIX


def _parent_issue_key(
    *,
    story: Mapping[str, Any],
    jira_payload: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> str:
    for source in (story, jira_payload, inputs):
        value = source.get("parentIssueKey") or source.get("parent_issue_key")
        if isinstance(value, Mapping):
            value = value.get("key")
        normalized = _string(value)
        if normalized:
            return normalized
    parent = story.get("parent") or jira_payload.get("parent") or inputs.get("parent")
    if isinstance(parent, Mapping):
        return _string(parent.get("key") or parent.get("issueKey"))
    return _string(parent)


def _workflow_marker_label(
    *,
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    for source in (inputs, context or {}):
        for key in (
            "workflowId",
            "workflow_id",
            "runId",
            "run_id",
            "executionId",
            "execution_id",
            "taskRunId",
            "task_run_id",
        ):
            value = _string(source.get(key))
            if value:
                sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-_")
                if sanitized:
                    return f"moonmind-workflow-{sanitized}"[:255]
    return ""


def _issue_matches_summary(issue: Mapping[str, Any], summary: str) -> bool:
    fields = issue.get("fields")
    if isinstance(fields, Mapping):
        return _string(fields.get("summary")) == summary
    return _string(issue.get("summary")) == summary


def _extract_search_issues(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        candidates = payload.get("issues") or payload.get("items") or []
    else:
        candidates = payload
    return [dict(issue) for issue in _list(candidates) if isinstance(issue, Mapping)]


async def _find_existing_issue_for_story(
    *,
    service: JiraToolService,
    project_key: str,
    marker_label: str,
    summary: str,
) -> dict[str, Any] | None:
    if not marker_label or not hasattr(service, "search_issues"):
        return None
    try:
        payload = await service.search_issues(
            SearchIssuesRequest(
                projectKey=project_key,
                jql=f'labels = "{marker_label}"',
                fields=["summary", "labels"],
                maxResults=50,
            )
        )
    except Exception:
        return None
    for issue in _extract_search_issues(payload):
        if _issue_matches_summary(issue, summary):
            return {
                "created": False,
                "existing": True,
                "issueKey": issue.get("key") or issue.get("issueKey"),
                "issueId": issue.get("id") or issue.get("issueId"),
                "self": issue.get("self"),
            }
    return None


def _merge_fields(
    *,
    story: Mapping[str, Any],
    jira_payload: Mapping[str, Any],
    marker_label: str = "",
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for value in (jira_payload.get("fields"), story.get("fields")):
        if isinstance(value, Mapping):
            fields.update(dict(value))
    labels = []
    for value in (jira_payload.get("labels"), story.get("labels")):
        labels.extend(_string(item) for item in _list(value) if _string(item))
    if marker_label:
        labels.append(marker_label)
    if labels:
        fields["labels"] = list(dict.fromkeys(labels))
    return fields


async def _default_github_story_fetcher(repo: str, ref: str, path: str) -> str:
    token, _resolution_error = await GitHubService.resolve_github_token()
    headers = GitHubService._github_headers(token) if token else {}
    api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
    params = {"ref": ref} if ref else None
    async with httpx.AsyncClient(timeout=30.0) as client:
        if token:
            response = await client.get(api_url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            encoded = _string(payload.get("content"))
            if encoded:
                return base64.b64decode(encoded).decode("utf-8")
        raw_url = f"https://raw.githubusercontent.com/{repo}/{ref or 'main'}/{path}"
        response = await client.get(raw_url)
        response.raise_for_status()
        return response.text


def _fallback_result(
    *,
    reason: str,
    inputs: Mapping[str, Any],
    story_count: int = 0,
    created: Sequence[Mapping[str, Any]] = (),
) -> ToolResult:
    branch = _string(inputs.get("targetBranch") or inputs.get("branch"))
    base_ref = _string(inputs.get("startingBranch") or inputs.get("baseBranch"))
    created_issues = [dict(issue) for issue in created]
    story_output: dict[str, Any] = {
        "mode": "docs_tmp",
        "status": "fallback",
        "reason": reason,
        "storyCount": story_count,
        "path": _string(inputs.get("storyBreakdownPath")),
    }
    jira_output: dict[str, Any] = {}
    if created_issues:
        story_output["createdCount"] = len(created_issues)
        jira_output = {
            "createdCount": len(created_issues),
            "createdIssues": created_issues,
            "partial": True,
        }
    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": story_output,
            "jira": jira_output,
            "push_status": "",
            "push_branch": branch,
            "push_base_ref": base_ref,
            "repository": _string(inputs.get("repository") or inputs.get("repo")),
        },
    )


async def create_jira_issues_from_stories(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    jira_service_factory: JiraServiceFactory = JiraToolService,
    story_fetcher: StoryFetcher = _default_github_story_fetcher,
) -> ToolResult:
    """Create one Jira issue per story, or report docs/tmp fallback metadata."""

    story_output = _mapping(inputs.get("storyOutput") or inputs.get("story_output"))
    jira_payload = _mapping(story_output.get("jira") or inputs.get("jira"))
    project_key = _string(
        jira_payload.get("projectKey")
        or jira_payload.get("project_key")
        or inputs.get("projectKey")
        or inputs.get("project_key")
    )
    issue_type_id = _string(
        jira_payload.get("issueTypeId")
        or jira_payload.get("issue_type_id")
        or inputs.get("issueTypeId")
        or inputs.get("issue_type_id")
    )
    fallback_on_failure = str(
        story_output.get("fallback")
        or story_output.get("onFailure")
        or story_output.get("on_failure")
        or "docs_tmp"
    ).strip().lower() not in {"fail", "none", "false"}

    stories = _coerce_story_payload(
        inputs.get("stories")
        or inputs.get("storyBreakdown")
        or inputs.get("story_breakdown")
        or inputs.get("storyBreakdownJson")
    )
    if not stories:
        repo = _string(inputs.get("repository") or inputs.get("repo"))
        ref = _string(
            inputs.get("targetBranch")
            or inputs.get("branch")
            or inputs.get("startingBranch")
        )
        path = _string(
            inputs.get("storyBreakdownPath")
            or story_output.get("storyBreakdownPath")
        )
        if repo and ref and path:
            try:
                fetched = story_fetcher(repo, ref, path)
                if inspect.isawaitable(fetched):
                    fetched = await fetched  # type: ignore[assignment]
                stories = _coerce_story_payload(fetched)
            except Exception as exc:
                if fallback_on_failure:
                    return _fallback_result(
                        reason=f"Unable to read story breakdown for Jira output: {exc}",
                        inputs=inputs,
                    )
                raise

    if not stories:
        if fallback_on_failure:
            return _fallback_result(
                reason="No stories were available for Jira issue creation.",
                inputs=inputs,
            )
        raise ValueError("No stories were available for Jira issue creation.")
    if not project_key or not issue_type_id:
        if fallback_on_failure:
            return _fallback_result(
                reason="Jira projectKey and issueTypeId are required.",
                inputs=inputs,
                story_count=len(stories),
            )
        raise ValueError("Jira projectKey and issueTypeId are required.")

    service = jira_service_factory()
    created: list[dict[str, Any]] = []
    marker_label = _workflow_marker_label(inputs=inputs, context=_context)
    try:
        for index, story in enumerate(stories, start=1):
            summary = _story_summary(story, index=index)
            existing_issue = await _find_existing_issue_for_story(
                service=service,
                project_key=project_key,
                marker_label=marker_label,
                summary=summary,
            )
            if existing_issue:
                created.append(existing_issue)
                continue

            fields = _merge_fields(
                story=story,
                jira_payload=jira_payload,
                marker_label=marker_label,
            )
            description = _truncate_jira_description(_story_description(story))
            parent_issue_key = _parent_issue_key(
                story=story,
                jira_payload=jira_payload,
                inputs=inputs,
            )
            if parent_issue_key:
                request = CreateSubtaskRequest(
                    projectKey=project_key,
                    issueTypeId=issue_type_id,
                    summary=summary,
                    description=description,
                    parentIssueKey=parent_issue_key,
                    fields=fields,
                )
                result = await service.create_subtask(request)
            else:
                request = CreateIssueRequest(
                    projectKey=project_key,
                    issueTypeId=issue_type_id,
                    summary=summary,
                    description=description,
                    fields=fields,
                )
                result = await service.create_issue(request)
            created.append(dict(result))
    except Exception as exc:
        if fallback_on_failure:
            return _fallback_result(
                reason=f"Jira issue creation failed: {exc}",
                inputs=inputs,
                story_count=len(stories),
                created=created,
            )
        raise

    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": {
                "mode": "jira",
                "status": "jira_created",
                "storyCount": len(stories),
                "createdCount": len(created),
            },
            "jira": {
                "createdCount": len(created),
                "createdIssues": created,
            },
        },
    )


def register_story_output_tool_handlers(dispatcher: Any) -> None:
    for name in JIRA_STORY_TOOL_NAMES:
        dispatcher.register_skill(
            skill_name=name,
            version="1.0",
            handler=create_jira_issues_from_stories,
        )


__all__ = [
    "JIRA_STORY_TOOL_NAMES",
    "create_jira_issues_from_stories",
    "register_story_output_tool_handlers",
]
