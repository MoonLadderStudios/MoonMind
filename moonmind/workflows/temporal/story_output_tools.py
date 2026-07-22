"""First-party story output tools for workflow plans."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from moonmind.config.settings import settings
from moonmind.integrations.jira.models import (
    CreateIssueRequest,
    CreateIssueLinkRequest,
    CreateSubtaskRequest,
    GetIssueRequest,
    GetTransitionsRequest,
    ListCreateIssueTypesRequest,
    SearchIssuesRequest,
    TransitionIssueRequest,
)
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.skills.tool_plan_contracts import ToolResult

JIRA_CREATE_ISSUES_TOOL_NAME = "story.create_jira_issues"
JIRA_ORCHESTRATE_TASKS_TOOL_NAME = "story.create_jira_orchestrate_tasks"
JIRA_IMPLEMENT_TASKS_TOOL_NAME = "story.create_jira_implement_tasks"
GITHUB_CREATE_ISSUES_TOOL_NAME = "story.create_github_issues"
GITHUB_ORCHESTRATE_WORKFLOWS_TOOL_NAME = (
    "story.create_github_issue_orchestrate_workflows"
)
GITHUB_IMPLEMENT_WORKFLOWS_TOOL_NAME = (
    "story.create_github_issue_implement_workflows"
)
JIRA_CHECK_BLOCKERS_TOOL_NAME = "jira.check_blockers"
JIRA_LOAD_PRESET_BRIEF_TOOL_NAME = "jira.load_preset_brief"
JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME = "jira.update_issue_status"
GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME = "github.load_issue_preset_brief"
GITHUB_CHECK_ISSUE_BLOCKERS_TOOL_NAME = "github.check_issue_blockers"
GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME = "github.update_issue_status"
JIRA_STORY_TOOL_NAMES = frozenset(
    {
        JIRA_CREATE_ISSUES_TOOL_NAME,
        JIRA_ORCHESTRATE_TASKS_TOOL_NAME,
        JIRA_IMPLEMENT_TASKS_TOOL_NAME,
    }
)
GITHUB_STORY_TOOL_NAMES = frozenset(
    {
        GITHUB_CREATE_ISSUES_TOOL_NAME,
        GITHUB_ORCHESTRATE_WORKFLOWS_TOOL_NAME,
        GITHUB_IMPLEMENT_WORKFLOWS_TOOL_NAME,
    }
)
_SOURCE_DOCUMENT_PATH_RE = re.compile(
    r"(?P<path>(?:docs/(?:[A-Za-z0-9_.@+=-]+/)*[A-Za-z0-9_.@+=-]+|"
    r"(?<![A-Za-z0-9_])AGENTS)\.md)"
)
_DOWNSTREAM_PRESET_ORCHESTRATE = "orchestrate"
_DOWNSTREAM_PRESET_IMPLEMENT = "implement"
_DOWNSTREAM_PRESETS: dict[str, dict[str, str]] = {
    _DOWNSTREAM_PRESET_ORCHESTRATE: {
        "slug": "jira-orchestrate",
        "label": "Jira Orchestrate",
        "idempotencyPrefix": "jira-orchestrate",
    },
    _DOWNSTREAM_PRESET_IMPLEMENT: {
        "slug": "jira-implement",
        "label": "Jira Implement",
        "idempotencyPrefix": "jira-implement",
    },
}
_GITHUB_DOWNSTREAM_PRESETS: dict[str, dict[str, str]] = {
    _DOWNSTREAM_PRESET_ORCHESTRATE: {
        "slug": "github-issue-orchestrate",
        "label": "GitHub Issue Orchestrate",
        "idempotencyPrefix": "github-issue-orchestrate",
    },
    _DOWNSTREAM_PRESET_IMPLEMENT: {
        "slug": "github-issue-implement",
        "label": "GitHub Issue Implement",
        "idempotencyPrefix": "github-issue-implement",
    },
}
JIRA_DESCRIPTION_MAX_CHARS = 32767
JIRA_DESCRIPTION_TRUNCATION_SUFFIX = "\n\n[Truncated by MoonMind before Jira export]"
JIRA_DEPENDENCY_MODE_NONE = "none"
JIRA_DEPENDENCY_MODE_LINEAR_BLOCKER_CHAIN = "linear_blocker_chain"
JIRA_DEPENDENCY_MODES = frozenset(
    {JIRA_DEPENDENCY_MODE_NONE, JIRA_DEPENDENCY_MODE_LINEAR_BLOCKER_CHAIN}
)
STORY_IMPLEMENTATION_STATUS_FULLY_IMPLEMENTED = "fully_implemented"
STORY_IMPLEMENTATION_STATUS_PARTIALLY_IMPLEMENTED = "partially_implemented"
STORY_IMPLEMENTATION_STATUS_UNVERIFIABLE = "unverifiable"
STORY_JIRA_ACTION_CREATE_ISSUE = "create_issue"
STORY_JIRA_ACTION_CREATE_REMAINING_WORK_ISSUE = "create_remaining_work_issue"
STORY_JIRA_ACTION_SKIP = "skip"
STORY_JIRA_ACTION_MANUAL_REVIEW = "manual_review"
STORY_JIRA_SKIP_ACTIONS = frozenset(
    {
        STORY_JIRA_ACTION_SKIP,
        "skip_issue",
        "skip_jira",
        "no_issue",
        "none",
    }
)
STORY_JIRA_BLOCK_ACTIONS = frozenset(
    {
        STORY_JIRA_ACTION_MANUAL_REVIEW,
        "block",
        "blocked",
        "manual_review_required",
        "do_not_create",
    }
)
STORY_JIRA_CREATE_ACTIONS = frozenset(
    {
        STORY_JIRA_ACTION_CREATE_ISSUE,
        "create",
        "create_jira_issue",
    }
)
_ACCEPTANCE_HEADING_RE = re.compile(
    r"(?im)^\s*(acceptance\s+criteria|acceptance|ac)\s*:?\s*$"
)

DOCUMENT_DISCOVER_TOOL_NAME = "document.discover"
DOCUMENT_UPDATE_TASKS_TOOL_NAME = "story.create_document_update_tasks"
_GITHUB_OWNER_REPO_PATTERN = re.compile(
    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
)

StoryFetcher = Callable[
    [str, str, str],
    str | Awaitable[str],
]
ArtifactReader = Callable[
    [str],
    str | bytes | Awaitable[str | bytes],
]
JiraServiceFactory = Callable[[], JiraToolService]
ExecutionCreator = Callable[..., Mapping[str, Any] | Awaitable[Mapping[str, Any]]]

def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}

def _string(value: Any) -> str:
    return str(value or "").strip()

def _list(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []

def _collapse_jira_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank and collapsed:
                collapsed.append("")
            previous_blank = True
            continue
        collapsed.append(line)
        previous_blank = False
    return "\n".join(collapsed).strip()

def _collect_adf_text(node: Any) -> list[str]:
    if not isinstance(node, Mapping):
        return []
    node_type = node.get("type")
    if node_type == "text":
        return [str(node.get("text") or "")]
    if node_type == "hardBreak":
        return ["\n"]
    content = node.get("content")
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    inline = node_type in {"paragraph", "heading", "listItem"}
    for child in content:
        child_parts = _collect_adf_text(child)
        if inline:
            parts.extend(child_parts)
        else:
            text = _collapse_jira_text("".join(child_parts))
            if text:
                parts.append(text)
    if inline:
        return [_collapse_jira_text("".join(parts))]
    return parts

def _normalize_jira_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _collapse_jira_text(value)
    if isinstance(value, Mapping):
        return _collapse_jira_text("\n".join(_collect_adf_text(value)))
    if isinstance(value, list):
        parts = [_normalize_jira_text(item) for item in value]
        return _collapse_jira_text("\n".join(part for part in parts if part))
    return _collapse_jira_text(str(value))

def _extract_acceptance_criteria(
    fields: Mapping[str, Any],
    names: Mapping[str, Any],
) -> str:
    for field_key, field_name in names.items():
        normalized_name = str(field_name or "").lower()
        if "acceptance" not in normalized_name:
            continue
        text = _normalize_jira_text(fields.get(field_key))
        if text:
            return text
    return ""

def _split_description_acceptance(description_text: str) -> tuple[str, str]:
    match = _ACCEPTANCE_HEADING_RE.search(description_text)
    if match is None:
        return description_text, ""
    before = description_text[: match.start()].strip()
    after = description_text[match.end() :].strip()
    return before, after

def _issue_url(payload: Mapping[str, Any]) -> str | None:
    browse = _string(payload.get("browseUrl") or payload.get("url"))
    if browse:
        return browse
    self_url = _string(payload.get("self"))
    marker = "/rest/api/"
    if marker in self_url:
        return f"{self_url.split(marker, 1)[0]}/browse/{payload.get('key')}"
    return None

def _first_string(*values: Any) -> str:
    for value in values:
        normalized = _string(value)
        if normalized:
            return normalized
    return ""

def _normalize_document_directory(value: str) -> str:
    return value.replace("\\", "/") if "\\" in value else value

def _path_from_existing_dir(value: Any) -> Path | None:
    normalized = _string(value)
    if not normalized:
        return None
    path = Path(normalized).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    return path if path.exists() and path.is_dir() else None

def _github_repository_slug(value: Any) -> str:
    normalized = _string(value).removesuffix(".git")
    if not normalized:
        return ""
    if _GITHUB_OWNER_REPO_PATTERN.fullmatch(normalized):
        return normalized
    if normalized.startswith("git@github.com:"):
        candidate = normalized.removeprefix("git@github.com:").removesuffix(".git")
        return candidate if _GITHUB_OWNER_REPO_PATTERN.fullmatch(candidate) else ""
    parsed = urlparse(normalized)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return ""
    candidate = f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return candidate if _GITHUB_OWNER_REPO_PATTERN.fullmatch(candidate) else ""

def _repository_from_inputs_or_context(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    context_mapping = _mapping(context)
    workspace_spec = _mapping(
        context_mapping.get("workspaceSpec") or context_mapping.get("workspace_spec")
    )
    return _first_string(
        inputs.get("repository"),
        inputs.get("repo"),
        inputs.get("githubRepository"),
        inputs.get("github_repository"),
        workspace_spec.get("repository"),
        workspace_spec.get("repo"),
        context_mapping.get("repository"),
        context_mapping.get("repo"),
        settings.workflow.github_repository,
    )

def _ref_from_inputs_or_context(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    context_mapping = _mapping(context)
    workspace_spec = _mapping(
        context_mapping.get("workspaceSpec") or context_mapping.get("workspace_spec")
    )
    return _first_string(
        inputs.get("ref"),
        inputs.get("branch"),
        inputs.get("startingBranch"),
        inputs.get("starting_branch"),
        workspace_spec.get("ref"),
        workspace_spec.get("branch"),
        workspace_spec.get("startingBranch"),
        workspace_spec.get("starting_branch"),
        context_mapping.get("ref"),
        context_mapping.get("branch"),
        context_mapping.get("startingBranch"),
        context_mapping.get("starting_branch"),
    )

def _repo_root_candidates(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> list[Path]:
    context_mapping = _mapping(context)
    workspace_spec = _mapping(
        context_mapping.get("workspaceSpec") or context_mapping.get("workspace_spec")
    )
    previous_outputs = _mapping(
        inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
        or context_mapping.get("previousOutputs")
        or context_mapping.get("previous_outputs")
    )
    previous_workspace_spec = _mapping(
        previous_outputs.get("workspaceSpec")
        or previous_outputs.get("workspace_spec")
    )
    candidates: list[Path] = []
    for source in (
        inputs,
        context_mapping,
        workspace_spec,
        previous_outputs,
        previous_workspace_spec,
    ):
        for key in (
            "repoRoot",
            "repo_root",
            "repositoryRoot",
            "repository_root",
            "workspaceRoot",
            "workspace_root",
            "workspacePath",
            "workspace_path",
            "repoPath",
            "repo_path",
            "path",
        ):
            path = _path_from_existing_dir(source.get(key))
            if path is not None and path not in candidates:
                candidates.append(path)

    repository_path = _path_from_existing_dir(
        inputs.get("repository") or inputs.get("repo")
    )
    if repository_path is not None and repository_path not in candidates:
        candidates.append(repository_path)

    configured = _path_from_existing_dir(settings.workflow.repo_root)
    if configured is not None and configured not in candidates:
        candidates.append(configured)

    cwd = Path.cwd().resolve()
    if cwd not in candidates:
        candidates.append(cwd)
    return candidates

def _resolve_local_document_root(
    *,
    directory: str,
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[Path, Path] | None:
    candidate_path = Path(directory).expanduser()
    if candidate_path.is_absolute():
        root = candidate_path.resolve()
        if root.exists() and root.is_dir():
            workspace_root = Path.cwd().resolve()
            output_base = workspace_root if root.is_relative_to(workspace_root) else root
            return root, output_base
        return None

    relative = Path(directory)
    for repo_root in _repo_root_candidates(inputs, context):
        root = (repo_root / relative).resolve()
        if not root.is_relative_to(repo_root):
            continue
        if root.exists() and root.is_dir():
            return root, repo_root
    return None

async def _github_default_branch(
    client: httpx.AsyncClient,
    *,
    repository: str,
    headers: Mapping[str, str],
) -> str:
    response = await client.get(
        f"https://api.github.com/repos/{repository}",
        headers=dict(headers),
    )
    response.raise_for_status()
    payload = response.json()
    return _string(payload.get("default_branch")) or "main"

async def _discover_github_document_paths(
    *,
    repository: str,
    directory: str,
    extensions: frozenset[str],
    ref: str,
) -> tuple[list[str], str, bool, bool]:
    repo_slug = _github_repository_slug(repository)
    if not repo_slug:
        raise ValueError(
            "repository must be a GitHub owner/repo slug or GitHub URL for remote discovery"
        )

    token, _resolution_error = await GitHubService.resolve_github_token()
    headers = GitHubService._github_headers(token) if token else {}
    normalized_directory = directory.strip("/")
    prefix = f"{normalized_directory}/" if normalized_directory else ""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resolved_ref = ref or await _github_default_branch(
            client,
            repository=repo_slug,
            headers=headers,
        )
        response = await client.get(
            f"https://api.github.com/repos/{repo_slug}/git/trees/{resolved_ref}",
            headers=headers,
            params={"recursive": "1"},
        )
        response.raise_for_status()
        payload = response.json()

    found_directory = normalized_directory == ""
    document_paths: list[str] = []
    for item in _list(payload.get("tree")):
        if not isinstance(item, Mapping):
            continue
        path = _string(item.get("path"))
        if not path:
            continue
        if normalized_directory and path == normalized_directory:
            found_directory = True
            continue
        if prefix and not path.startswith(prefix):
            continue
        found_directory = True
        if _string(item.get("type")).lower() != "blob":
            continue
        if any(path.endswith(ext) for ext in extensions):
            document_paths.append(path)

    return (
        sorted(document_paths),
        resolved_ref,
        bool(payload.get("truncated")),
        found_directory,
    )

def _issue_key(value: Mapping[str, Any]) -> str:
    return _string(value.get("key") or value.get("issueKey")).upper()

def _status_payload(issue: Mapping[str, Any]) -> dict[str, Any]:
    fields = _mapping(issue.get("fields"))
    return _mapping(fields.get("status") or issue.get("status"))

def _status_name(issue: Mapping[str, Any]) -> str:
    return _string(_status_payload(issue).get("name"))

def _status_is_done(issue: Mapping[str, Any]) -> bool:
    status = _status_payload(issue)
    if _string(status.get("name")).lower() == "done":
        return True
    category = _mapping(status.get("statusCategory") or status.get("category"))
    return _string(category.get("key") or category.get("name")).lower() == "done"

def _issue_links(issue: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = _mapping(issue.get("fields"))
    candidates = (
        fields.get("issuelinks")
        or fields.get("issueLinks")
        or issue.get("issuelinks")
        or issue.get("issueLinks")
        or []
    )
    return [dict(item) for item in _list(candidates) if isinstance(item, Mapping)]

def _is_blocking_link_type(
    link: Mapping[str, Any],
    *,
    link_type_name: str,
) -> bool:
    link_type = _mapping(link.get("type"))
    configured = _string(link_type_name).lower() or "blocks"
    name = _string(link_type.get("name")).lower()
    if name:
        return name == configured
    outward = _string(link_type.get("outward")).lower()
    inward = _string(link_type.get("inward")).lower()
    return outward == "blocks" and inward == "is blocked by"

def _blocking_issue_from_link(
    link: Mapping[str, Any],
    *,
    target_issue_key: str,
    link_type_name: str,
) -> dict[str, Any] | None:
    if not _is_blocking_link_type(link, link_type_name=link_type_name):
        return None

    target = target_issue_key.upper()
    outward_issue = _mapping(link.get("outwardIssue") or link.get("outward_issue"))
    inward_issue = _mapping(link.get("inwardIssue") or link.get("inward_issue"))
    outward_key = _issue_key(outward_issue)
    inward_key = _issue_key(inward_issue)

    if outward_issue and inward_issue:
        if inward_key == target and outward_key and outward_key != target:
            return outward_issue
        return None

    # Jira issue GET responses include only the other side of the link. For a
    # target blocked by another issue, Jira exposes that blocker as outwardIssue.
    if outward_issue and outward_key != target:
        return outward_issue
    return None

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

def _parse_story_breakdown_payload(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value

def _has_explicit_empty_story_list(value: Any) -> bool:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    if isinstance(value, Mapping):
        for key in ("stories", "userStories", "user_stories", "items", "issues"):
            if key not in value:
                continue
            stories_value = value.get(key)
            return (
                isinstance(stories_value, Sequence)
                and not isinstance(stories_value, (str, bytes, bytearray))
                and len(stories_value) == 0
            )
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value) == 0
    return False


def _story_breakdown_failure_reason(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ""
    if not isinstance(value, Mapping):
        return ""
    error = _mapping(value.get("error"))
    if not error:
        return ""
    code = _string(error.get("code")) or "story_breakdown_failed"
    message = _string(error.get("message")) or "Story breakdown failed."
    return f"Story breakdown failed before Jira creation: {code}: {message}"

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

def _normalized_story_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _string(value).lower()).strip("_")

def _story_issue_creation(story: Mapping[str, Any]) -> dict[str, Any]:
    value = (
        story.get("issueCreation")
        or story.get("issue_creation")
        or story.get("jiraCreation")
        or story.get("jira_creation")
    )
    return dict(value) if isinstance(value, Mapping) else {}

def _story_implementation_status(story: Mapping[str, Any]) -> str:
    return _normalized_story_token(
        story.get("implementationStatus")
        or story.get("implementation_status")
        or story.get("status")
    )

def _story_issue_creation_action(story: Mapping[str, Any]) -> str:
    issue_creation = _story_issue_creation(story)
    action = _normalized_story_token(
        issue_creation.get("action")
        or story.get("issueCreationAction")
        or story.get("issue_creation_action")
        or story.get("jiraCreationAction")
        or story.get("jira_creation_action")
    )
    if action:
        return action
    status = _story_implementation_status(story)
    if status == STORY_IMPLEMENTATION_STATUS_FULLY_IMPLEMENTED:
        return STORY_JIRA_ACTION_SKIP
    if status == STORY_IMPLEMENTATION_STATUS_PARTIALLY_IMPLEMENTED:
        return STORY_JIRA_ACTION_CREATE_REMAINING_WORK_ISSUE
    if status == STORY_IMPLEMENTATION_STATUS_UNVERIFIABLE:
        return STORY_JIRA_ACTION_MANUAL_REVIEW
    return STORY_JIRA_ACTION_CREATE_ISSUE

def _story_issue_creation_reason(story: Mapping[str, Any]) -> str:
    issue_creation = _story_issue_creation(story)
    return _string(
        issue_creation.get("reason")
        or story.get("issueCreationReason")
        or story.get("issue_creation_reason")
        or story.get("jiraCreationReason")
        or story.get("jira_creation_reason")
    )

def _story_remaining_work(story: Mapping[str, Any]) -> dict[str, Any]:
    value = story.get("remainingWork") or story.get("remaining_work")
    return dict(value) if isinstance(value, Mapping) else {}

def _story_implemented_evidence(story: Mapping[str, Any]) -> list[Any]:
    return _list(
        story.get("implementedEvidence")
        or story.get("implemented_evidence")
        or story.get("evidence")
    )

def _format_implemented_evidence_for_jira(story: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in _story_implemented_evidence(story):
        if isinstance(item, Mapping):
            requirement = _string(
                item.get("requirement")
                or item.get("coverageId")
                or item.get("coverage_id")
                or item.get("id")
            )
            status = _string(item.get("status"))
            evidence = _string(item.get("evidence") or item.get("details"))
            parts = [part for part in (requirement, status, evidence) if part]
            if parts:
                lines.append(" - ".join(parts))
        else:
            text = _string(item)
            if text:
                lines.append(text)
    return lines

def _remaining_work_list(
    remaining_work: Mapping[str, Any],
    *keys: str,
) -> list[Any]:
    for key in keys:
        items = _list(remaining_work.get(key))
        if items:
            return items
    return []

def _story_for_remaining_jira_work(
    story: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any] | None:
    remaining_work = _story_remaining_work(story)
    if not remaining_work:
        return None

    adjusted = dict(story)
    original_summary = _story_summary(story, index=index)
    adjusted["summary"] = _string(
        remaining_work.get("summary")
        or remaining_work.get("title")
    ) or f"Complete remaining work for {original_summary}"

    remaining_description = _string(
        remaining_work.get("description")
        or remaining_work.get("body")
        or remaining_work.get("narrative")
    )
    original_description = _story_description(story)
    evidence_lines = _format_implemented_evidence_for_jira(story)
    description_parts = [
        "Remaining Work\n" + (remaining_description or adjusted["summary"]),
        _format_story_section("Already Implemented Evidence", evidence_lines),
        _format_story_section("Original Story Scope", original_description),
    ]
    adjusted["description"] = "\n".join(
        part for part in description_parts if part
    ).strip()

    acceptance = _remaining_work_list(
        remaining_work,
        "acceptanceCriteria",
        "acceptance_criteria",
        "remainingAcceptanceCriteria",
        "remaining_acceptance_criteria",
    )
    if acceptance:
        adjusted["acceptanceCriteria"] = acceptance
        adjusted.pop("acceptance_criteria", None)
    else:
        adjusted.pop("acceptanceCriteria", None)
        adjusted.pop("acceptance_criteria", None)
    requirements = _remaining_work_list(
        remaining_work,
        "requirements",
        "remainingRequirements",
        "remaining_requirements",
    )
    if requirements:
        adjusted["requirements"] = requirements
    else:
        adjusted.pop("requirements", None)
    adjusted["originalStorySummary"] = original_summary
    return adjusted

def _story_reconciliation_record(
    story: Mapping[str, Any],
    *,
    index: int,
    action: str,
) -> dict[str, Any]:
    record = {
        "storyId": _story_id(story, index=index),
        "storyIndex": index,
        "summary": _story_summary(story, index=index),
        "implementationStatus": _story_implementation_status(story),
        "issueCreationAction": action,
        "jiraCreationAction": action,
    }
    reason = _story_issue_creation_reason(story)
    if reason:
        record["reason"] = reason
    evidence = _story_implemented_evidence(story)
    if evidence:
        record["implementedEvidence"] = evidence
    return record

def _reconcile_stories_for_issue_creation(
    stories: Sequence[Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    partial_adjusted: list[dict[str, Any]] = []

    for index, story in enumerate(stories, start=1):
        action = _story_issue_creation_action(story)
        status = _story_implementation_status(story)
        if action in STORY_JIRA_SKIP_ACTIONS or (
            status == STORY_IMPLEMENTATION_STATUS_FULLY_IMPLEMENTED
            and action not in STORY_JIRA_CREATE_ACTIONS
        ):
            skipped.append(
                _story_reconciliation_record(story, index=index, action=action)
            )
            continue
        if action in STORY_JIRA_BLOCK_ACTIONS or (
            status == STORY_IMPLEMENTATION_STATUS_UNVERIFIABLE
            and action not in STORY_JIRA_CREATE_ACTIONS
        ):
            blocked.append(
                _story_reconciliation_record(story, index=index, action=action)
            )
            continue
        if (
            action == STORY_JIRA_ACTION_CREATE_REMAINING_WORK_ISSUE
            or status == STORY_IMPLEMENTATION_STATUS_PARTIALLY_IMPLEMENTED
        ):
            adjusted = _story_for_remaining_jira_work(story, index=index)
            if adjusted is None:
                blocked_record = _story_reconciliation_record(
                    story,
                    index=index,
                    action=STORY_JIRA_ACTION_MANUAL_REVIEW,
                )
                blocked_record.setdefault(
                    "reason",
                    "Partially implemented stories require remainingWork before "
                    "issue creation can safely narrow the issue scope.",
                )
                blocked.append(blocked_record)
                continue
            partial_adjusted.append(
                _story_reconciliation_record(story, index=index, action=action)
            )
            eligible.append(adjusted)
            continue
        eligible.append(dict(story))

    return eligible, skipped, blocked, partial_adjusted

def _breakdown_source_path(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    source = value.get("source")
    if isinstance(source, Mapping):
        path = _string(source.get("referencePath") or source.get("path"))
        if path:
            return path
    for key in ("sourceDocument", "source_document"):
        path = _string(value.get(key))
        if path:
            return path
    return ""


def _normalize_source_document_class(value: Any) -> str:
    return _string(value).strip().lower().replace("_", "-")


def _breakdown_source_document_class(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    source = value.get("source")
    if isinstance(source, Mapping):
        source_class = _normalize_source_document_class(
            source.get("sourceDocumentClass")
            or source.get("source_document_class")
            or source.get("documentClass")
            or source.get("document_class")
        )
        if source_class:
            return source_class
    return _normalize_source_document_class(
        value.get("sourceDocumentClass")
        or value.get("source_document_class")
        or value.get("documentClass")
        or value.get("document_class")
    )


def _is_canonical_source_path(path: str) -> bool:
    normalized = _string(path).replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    if not normalized:
        return False
    if normalized == "AGENTS.md" or normalized.endswith("/AGENTS.md"):
        return True
    if normalized.startswith("docs/tmp/") or "/docs/tmp/" in normalized:
        return False
    return normalized.startswith("docs/") or "/docs/" in normalized


def _source_reference_requires_claim_ids(
    *,
    source_document_class: str,
    source_path: str,
) -> bool:
    source_class = _normalize_source_document_class(source_document_class)
    if _is_canonical_source_path(source_path):
        return True
    if source_class == "canonical-declarative":
        return True
    if source_class in {"declarative-text", "imperative-input"}:
        return False
    return _is_canonical_source_path(source_path)


def _story_source_reference(
    story: Mapping[str, Any],
    *,
    fallback_path: str = "",
) -> dict[str, Any]:
    source_ref = story.get("sourceReference") or story.get("source_reference")
    if isinstance(source_ref, Mapping):
        reference = dict(source_ref)
    elif isinstance(source_ref, str):
        reference = {"path": source_ref}
    else:
        reference = {}
    path = _string(reference.get("path") or fallback_path)
    if path:
        reference["path"] = path
    return reference

def _missing_source_reference_story_ids(
    stories: Sequence[Mapping[str, Any]],
    *,
    fallback_path: str,
) -> list[str]:
    missing: list[str] = []
    for index, story in enumerate(stories, start=1):
        reference = _story_source_reference(story, fallback_path=fallback_path)
        if not _string(reference.get("path")):
            missing.append(_story_id(story, index=index))
    return missing

def _story_source_claim_ids(reference: Mapping[str, Any]) -> list[str]:
    return [
        _string(item)
        for item in _list(reference.get("claimIds") or reference.get("claim_ids"))
        if _string(item)
    ]

def _missing_source_claim_story_ids(
    stories: Sequence[Mapping[str, Any]],
    *,
    fallback_path: str,
    source_document_class: str = "",
) -> list[str]:
    missing: list[str] = []
    for index, story in enumerate(stories, start=1):
        reference = _story_source_reference(story, fallback_path=fallback_path)
        source_path = _string(reference.get("path"))
        if (
            source_path
            and _source_reference_requires_claim_ids(
                source_document_class=source_document_class,
                source_path=source_path,
            )
            and not _story_source_claim_ids(reference)
        ):
            missing.append(_story_id(story, index=index))
    return missing

def _requires_story_source_reference(
    *,
    inputs: Mapping[str, Any],
    story_output: Mapping[str, Any],
    fallback_path: str,
) -> bool:
    raw_value = None
    policy_provided = False
    for container in (story_output, inputs):
        for key in ("sourceReferencePolicy", "source_reference_policy"):
            if key in container:
                raw_value = container.get(key)
                policy_provided = True
                break
        if policy_provided:
            break
    if raw_value is None:
        return bool(fallback_path)
    if isinstance(raw_value, bool):
        return raw_value

    raw_policy = str(raw_value).strip().lower()
    if raw_policy in {
        "true",
        "yes",
        "1",
        "on",
        "require",
        "required",
        "source_required",
        "source-reference-required",
        "source_reference_required",
    }:
        return True
    if raw_policy in {
        "false",
        "no",
        "0",
        "off",
        "allow_missing",
        "inline",
        "optional",
        "source_optional",
        "source-reference-optional",
        "source_reference_optional",
    }:
        return False
    return bool(fallback_path)

def _story_description_with_source(
    story: Mapping[str, Any],
    *,
    fallback_source_path: str,
) -> str:
    description = _story_description(story)
    reference = _story_source_reference(story, fallback_path=fallback_source_path)
    source_path = _string(reference.get("path"))
    source_lines: list[str] = []
    if source_path:
        source_lines.append(f"Source Document: {source_path}")
    source_issue_key = _string(
        reference.get("sourceIssueKey") or reference.get("source_issue_key")
    )
    if source_issue_key:
        source_lines.append(f"Source Issue: {source_issue_key}")
    title = _string(reference.get("title"))
    if title:
        source_lines.append(f"Source Title: {title}")
    sections = [
        _string(item)
        for item in _list(reference.get("sections"))
        if _string(item)
    ]
    if sections:
        source_lines.append(
            "Source Sections:\n" + "\n".join(f"- {item}" for item in sections)
        )
    claim_ids = [
        _string(item)
        for item in _list(reference.get("claimIds") or reference.get("claim_ids"))
        if _string(item)
    ]
    if claim_ids:
        source_lines.append(
            "Canonical Claim IDs:\n" + "\n".join(f"- {item}" for item in claim_ids)
        )
    coverage_ids = [
        _string(item)
        for item in _list(reference.get("coverageIds") or reference.get("coverage_ids"))
        if _string(item)
    ]
    if coverage_ids:
        source_lines.append(
            "Coverage IDs:\n" + "\n".join(f"- {item}" for item in coverage_ids)
        )
    if not source_lines:
        return description
    source_block = "Source Reference\n" + "\n".join(source_lines)
    if not description:
        return source_block
    return (source_block + "\n\n" + description).strip()

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
            "agentRunId",
            "agent_run_id",
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

def _story_id(story: Mapping[str, Any], *, index: int) -> str:
    for key in ("id", "storyId", "story_id", "key"):
        value = _string(story.get(key))
        if value:
            return value
    return f"STORY-{index:03d}"

def _story_dependency_ids(story: Mapping[str, Any]) -> list[str]:
    """Return the story IDs a story declares as prerequisites, in order.

    moonspec-breakdown emits a per-story ``dependencies`` field ("story IDs this
    story truly depends on"). Each entry may be a plain ID string or an object
    carrying the ID. Values are normalized to stable, de-duplicated ID strings.
    """
    raw = (
        story.get("dependencies")
        or story.get("dependencyIds")
        or story.get("dependency_ids")
    )
    ids: list[str] = []
    for item in _list(raw):
        if isinstance(item, Mapping):
            value = _string(
                item.get("id")
                or item.get("storyId")
                or item.get("story_id")
                or item.get("key")
            )
        else:
            value = _string(item)
        if value and value not in ids:
            ids.append(value)
    return ids

def _issue_mappings_from_inputs(
    inputs: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    jira_payload = _mapping(inputs.get("jira"))
    input_previous_outputs = _mapping(
        inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
    )
    context_previous_outputs = _mapping(
        (context or {}).get("previousOutputs")
        or (context or {}).get("previous_outputs")
    )
    input_previous_jira_payload = _mapping(input_previous_outputs.get("jira"))
    context_previous_jira_payload = _mapping(context_previous_outputs.get("jira"))
    candidates = (
        jira_payload.get("issueMappings")
        or jira_payload.get("issue_mappings")
        or inputs.get("issueMappings")
        or inputs.get("issue_mappings")
        or jira_payload.get("createdIssues")
        or inputs.get("createdIssues")
        or input_previous_jira_payload.get("issueMappings")
        or input_previous_jira_payload.get("issue_mappings")
        or input_previous_jira_payload.get("createdIssues")
        or input_previous_outputs.get("issueMappings")
        or input_previous_outputs.get("issue_mappings")
        or input_previous_outputs.get("createdIssues")
        or context_previous_jira_payload.get("issueMappings")
        or context_previous_jira_payload.get("issue_mappings")
        or context_previous_jira_payload.get("createdIssues")
        or context_previous_outputs.get("issueMappings")
        or context_previous_outputs.get("issue_mappings")
        or context_previous_outputs.get("createdIssues")
        or []
    )
    mappings = [dict(item) for item in _list(candidates) if isinstance(item, Mapping)]

    def sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
        raw_index = item.get("storyIndex") or item.get("story_index") or 0
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = 0
        return index, _string(item.get("storyId") or item.get("story_id"))

    # Order by declared story dependencies (prerequisites first) so the
    # downstream workflow dependsOn chain matches the Jira blocker chain built by
    # _create_dependency_links. storyIndex order is the stable tie-breaker, so
    # breakdowns without dependencies keep their original creation order.
    ordered = _order_issue_mappings_by_dependencies(sorted(mappings, key=sort_key))
    return [dict(item) for item in ordered]

def _source_issue_key(
    *,
    inputs: Mapping[str, Any],
    traceability: Mapping[str, Any],
) -> str:
    for source in (traceability, inputs):
        value = source.get("sourceIssueKey") or source.get("source_issue_key")
        if value:
            return _string(value)
    return ""


def _is_subtask_issue_type_name(issue_type_name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", issue_type_name.strip().lower())
    return normalized == "subtask"


def _stable_idempotency_key(
    *,
    source_issue_key: str,
    story_id: str,
    issue_key: str,
    prefix: str = "jira-orchestrate",
) -> str:
    raw = f"{prefix}:{source_issue_key}:{story_id}:{issue_key}"
    if len(raw) <= 128:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{source_issue_key}:{digest}"[:128]


def _jira_issue_input_from_mapping(
    *,
    mapping: Mapping[str, Any],
    issue_key: str,
    summary: str,
) -> dict[str, Any]:
    nested_issue = _mapping(
        mapping.get("jiraIssue")
        or mapping.get("jira_issue")
        or mapping.get("issue")
    )
    fields = _mapping(nested_issue.get("fields"))
    raw_field_status = fields.get("status")
    raw_status = nested_issue.get("status")
    raw_field_assignee = fields.get("assignee")
    raw_assignee = nested_issue.get("assignee")
    status = _mapping(raw_field_status or raw_status)
    assignee = _mapping(raw_field_assignee or raw_assignee)
    resolved_key = _first_string(
        issue_key,
        nested_issue.get("key"),
        nested_issue.get("issueKey"),
        nested_issue.get("issue_key"),
        mapping.get("key"),
        mapping.get("issueKey"),
        mapping.get("issue_key"),
    )
    issue: dict[str, Any] = {"key": resolved_key}

    resolved_summary = _first_string(
        summary,
        fields.get("summary"),
        nested_issue.get("summary"),
        mapping.get("summary"),
    )
    if resolved_summary:
        issue["summary"] = resolved_summary

    description = _normalize_jira_text(
        fields.get("description")
        or nested_issue.get("description")
        or nested_issue.get("descriptionText")
        or nested_issue.get("description_text")
        or mapping.get("description")
    )
    if description:
        issue["description"] = description

    url = _first_string(
        mapping.get("issueUrl"),
        mapping.get("issue_url"),
        mapping.get("url"),
        mapping.get("browseUrl"),
        nested_issue.get("url"),
        nested_issue.get("browseUrl"),
    )
    if not url:
        url = (
            _issue_url({**dict(mapping), "key": resolved_key})
            or _issue_url({**dict(nested_issue), "key": resolved_key})
            or ""
        )
    if url:
        issue["url"] = url

    status_name = _first_string(
        status.get("name"),
        raw_field_status if not isinstance(raw_field_status, Mapping) else None,
        raw_status if not isinstance(raw_status, Mapping) else None,
    )
    if status_name:
        issue["status"] = status_name

    assignee_name = _first_string(
        assignee.get("displayName"),
        assignee.get("name"),
        raw_field_assignee
        if not isinstance(raw_field_assignee, Mapping)
        else None,
        raw_assignee if not isinstance(raw_assignee, Mapping) else None,
    )
    if assignee_name:
        issue["assignee"] = assignee_name

    return issue


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _downstream_task_payload(
    *,
    mapping: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    traceability: Mapping[str, Any],
    depends_on: list[str],
    source_issue_key: str,
    target_preset: str = _DOWNSTREAM_PRESET_ORCHESTRATE,
) -> tuple[str, dict[str, Any]]:
    preset = _DOWNSTREAM_PRESETS[target_preset]
    preset_label = preset["label"]
    preset_slug = preset["slug"]
    issue_key = _string(mapping.get("issueKey") or mapping.get("issue_key"))
    story_id = _string(mapping.get("storyId") or mapping.get("story_id"))
    summary = _string(mapping.get("summary")) or issue_key
    source_brief_ref = _string(
        traceability.get("sourceBriefRef") or traceability.get("source_brief_ref")
    )
    source_design_path = _string(
        mapping.get("sourceDesignPath") or mapping.get("source_design_path")
    )
    source_claim_ids = [
        _string(item)
        for item in _list(
            mapping.get("sourceClaimIds") or mapping.get("source_claim_ids")
        )
        if _string(item)
    ]
    claim_line = (
        "Source canonical claim IDs: "
        + (", ".join(source_claim_ids) if source_claim_ids else "not provided")
        + ".\n"
    )
    instructions = (
        f"Run {preset_label} for {issue_key}.\n\n"
        f"Source story: {story_id or summary}.\n"
        f"Source summary: {summary}.\n"
        f"Source Jira issue: {source_issue_key or 'unknown'}.\n"
        f"Source design document: {source_design_path or 'not provided'}.\n"
        f"{claim_line}"
        f"Original brief reference: {source_brief_ref or 'not provided'}.\n\n"
        f"Use the existing {preset_label} workflow for this Jira issue. "
        "Do not run implementation inline inside the breakdown workflow."
    )
    runtime = _mapping(task_payload.get("runtime"))
    publish = _mapping(task_payload.get("publish"))
    configured_inputs = dict(_mapping(task_payload.get("inputs")))
    merge_automation_value = (
        task_payload.get("mergeAutomation")
        or task_payload.get("merge_automation")
        or publish.get("mergeAutomation")
        or publish.get("merge_automation")
    )
    if (
        isinstance(merge_automation_value, Mapping)
        and (merge_automation := dict(merge_automation_value))
        and _truthy(merge_automation.get("enabled"))
        and _string(publish.get("mode")).lower() == "pr"
    ):
        publish["mergeAutomation"] = {**merge_automation, "enabled": True}
        publish.pop("merge_automation", None)
    else:
        publish.pop("mergeAutomation", None)
        publish.pop("merge_automation", None)
    repository = _string(task_payload.get("repository") or task_payload.get("repo"))
    jira_issue_input = _jira_issue_input_from_mapping(
        mapping=mapping,
        issue_key=issue_key,
        summary=summary,
    )
    task: dict[str, Any] = {
        "title": f"Run {preset_label} for {issue_key}: {summary}",
        "instructions": instructions,
        "inputs": {
            **configured_inputs,
            "jira_issue": jira_issue_input,
            "source_design_path": source_design_path,
            "source_claim_ids": source_claim_ids,
            "constraints": (
                f"Preserve source issue {source_issue_key} traceability."
                if source_issue_key
                else ""
            ),
        },
        "taskTemplate": {
            "slug": preset_slug,
        },
    }
    if runtime:
        task["runtime"] = runtime
    if publish:
        task["publish"] = publish
    if repository:
        task["repository"] = repository
    if depends_on:
        task["dependsOn"] = list(depends_on)
    return task["title"], task

async def _create_jira_downstream_tasks_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
    target_preset: str,
) -> ToolResult:
    """Create dependent downstream Jira workflow executions from ordered issue mappings."""

    preset = _DOWNSTREAM_PRESETS[target_preset]
    preset_label = preset["label"]
    preset_idempotency_prefix = preset["idempotencyPrefix"]

    if execution_creator is None:
        raise ValueError(
            f"execution_creator is required for {preset_label} workflow creation."
        )

    context = _context or {}
    issue_mappings = _issue_mappings_from_inputs(inputs, context=context)
    orchestration_payload = _mapping(
        inputs.get("jiraOrchestration") or inputs.get("jira_orchestration")
    )
    task_payload = _mapping(
        orchestration_payload.get("task")
        or inputs.get("task")
    )
    traceability = _mapping(
        orchestration_payload.get("traceability")
        or inputs.get("traceability")
    )
    source_issue_key = _source_issue_key(inputs=inputs, traceability=traceability)
    source_brief_ref = _string(
        traceability.get("sourceBriefRef") or traceability.get("source_brief_ref")
    )
    repository = _string(task_payload.get("repository") or task_payload.get("repo"))
    owner_id = (
        _string(task_payload.get("ownerId") or task_payload.get("owner_id"))
        or _string(inputs.get("ownerId") or inputs.get("owner_id"))
        or _string(context.get("ownerId") or context.get("owner_id"))
        or None
    )
    owner_type = (
        _string(task_payload.get("ownerType") or task_payload.get("owner_type"))
        or _string(inputs.get("ownerType") or inputs.get("owner_type"))
        or _string(context.get("ownerType") or context.get("owner_type"))
        or None
    )

    tasks: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_stories: list[dict[str, Any]] = []
    previous_workflow_id = ""
    previous_story_id = ""
    # Map declared story IDs to their created workflow IDs so each downstream
    # task depends on the stories it declares as prerequisites (matching the Jira
    # blocker chain). ``issue_mappings`` is already ordered prerequisites-first by
    # ``_issue_mappings_from_inputs`` so every prerequisite is created first.
    workflow_id_by_story: dict[str, str] = {}
    mapping_story_ids = {
        _string(mapping.get("storyId") or mapping.get("story_id"))
        for mapping in issue_mappings
        if _string(mapping.get("storyId") or mapping.get("story_id"))
    }

    def _declared_prerequisites(mapping: Mapping[str, Any], story_id: str) -> list[str]:
        return [
            dep
            for dep in _story_dependency_ids(mapping)
            if dep in mapping_story_ids and dep != story_id
        ]

    # When no story declares a resolvable dependency, keep the historical linear
    # chain so dependency-free breakdowns still block each later story by the one
    # before it.
    declared_dependencies = any(
        _declared_prerequisites(
            mapping,
            _string(mapping.get("storyId") or mapping.get("story_id")),
        )
        for mapping in issue_mappings
    )

    for index, mapping in enumerate(issue_mappings, start=1):
        story_id = _string(mapping.get("storyId") or mapping.get("story_id")) or f"STORY-{index:03d}"
        story_index = mapping.get("storyIndex") or mapping.get("story_index") or index
        issue_key = _string(mapping.get("issueKey") or mapping.get("issue_key"))
        summary = _string(mapping.get("summary"))
        base_result = {
            "storyId": story_id,
            "storyIndex": story_index,
            "jiraIssueKey": issue_key,
        }
        if not issue_key:
            failures.append(
                {
                    **base_result,
                    "errorCode": "missing_issue_key",
                    "message": f"{preset_label} workflow creation requires issueKey.",
                }
            )
            skipped_stories.append({**base_result, "summary": summary})
            continue

        if declared_dependencies:
            depends_on_pairs = [
                (dep, workflow_id_by_story[dep])
                for dep in _declared_prerequisites(mapping, story_id)
                if dep in workflow_id_by_story
            ]
        elif previous_workflow_id:
            depends_on_pairs = [(previous_story_id, previous_workflow_id)]
        else:
            depends_on_pairs = []
        depends_on = [workflow_id for _dep_story_id, workflow_id in depends_on_pairs]
        title, task = _downstream_task_payload(
            mapping=mapping,
            task_payload=task_payload,
            traceability=traceability,
            depends_on=depends_on,
            source_issue_key=source_issue_key,
            target_preset=target_preset,
        )
        idempotency_key = _stable_idempotency_key(
            source_issue_key=source_issue_key,
            story_id=story_id,
            issue_key=issue_key,
            prefix=preset_idempotency_prefix,
        )
        try:
            created = execution_creator(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                owner_type=owner_type,
                title=title,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": repository or None,
                    "targetRuntime": _string(_mapping(task.get("runtime")).get("mode")) or None,
                    "publishMode": _string(_mapping(task.get("publish")).get("mode")) or None,
                    "workflow": task,
                    "traceability": {
                        "sourceIssueKey": source_issue_key,
                        "sourceBriefRef": source_brief_ref,
                    },
                },
                idempotency_key=idempotency_key,
                repository=repository or None,
                integration="jira",
                summary=f"{preset_label} workflow for {issue_key}.",
            )
            if inspect.isawaitable(created):
                created = await created  # type: ignore[assignment]
        except Exception as exc:
            failures.append(
                {
                    **base_result,
                    "errorCode": "task_creation_failed",
                    "message": str(exc) or "Downstream workflow creation failed.",
                    "dependsOn": depends_on,
                }
            )
            remaining = issue_mappings[index:]
            for skipped in remaining:
                skipped_stories.append(
                    {
                        "storyId": _string(
                            skipped.get("storyId") or skipped.get("story_id")
                        ),
                        "storyIndex": skipped.get("storyIndex")
                        or skipped.get("story_index"),
                        "jiraIssueKey": _string(
                            skipped.get("issueKey") or skipped.get("issue_key")
                        ),
                        "errorCode": "dependency_not_created",
                        "message": "Earlier downstream workflow creation failed.",
                    }
                )
            break

        created_mapping = dict(created)
        workflow_id = _string(
            created_mapping.get("workflowId") or created_mapping.get("workflow_id")
        )
        task_result = {
            **base_result,
            "workflowId": workflow_id,
            "runId": _string(created_mapping.get("runId") or created_mapping.get("run_id")),
            "title": _string(created_mapping.get("title")) or title,
            "created": not bool(created_mapping.get("existing")),
            "existing": bool(created_mapping.get("existing")),
            "dependsOn": depends_on,
            "idempotencyKey": idempotency_key,
        }
        tasks.append(task_result)
        for dep_story_id, dep_workflow_id in depends_on_pairs:
            dependencies.append(
                {
                    "fromWorkflowId": dep_workflow_id,
                    "toWorkflowId": workflow_id,
                    "fromStoryId": dep_story_id,
                    "toStoryId": story_id,
                    "status": "created",
                }
            )
        if story_id:
            workflow_id_by_story[story_id] = workflow_id
        previous_workflow_id = workflow_id
        previous_story_id = story_id

    if not issue_mappings:
        status = "no_downstream_tasks"
    elif failures or skipped_stories:
        status = "partial" if tasks else "no_downstream_tasks"
    else:
        status = "completed"
    workflow_status = (
        "no_downstream_workflows" if status == "no_downstream_tasks" else status
    )

    return ToolResult(
        status="COMPLETED",
        outputs={
            "jiraOrchestration": {
                "status": status,
                "workflowStatus": workflow_status,
                "storyCount": len(issue_mappings),
                "createdTaskCount": len(tasks),
                "createdWorkflowCount": len(tasks),
                "dependencyCount": len(dependencies),
                "tasks": tasks,
                "workflows": tasks,
                "workflowMappings": tasks,
                "dependencies": dependencies,
                "skippedStories": skipped_stories,
                "failures": failures,
                "traceability": {
                    "sourceIssueKey": source_issue_key,
                    "sourceBriefRef": source_brief_ref,
                },
            }
        },
    )

async def create_jira_orchestrate_tasks_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
) -> ToolResult:
    """Create dependent Jira Orchestrate workflow executions from ordered issue mappings."""

    return await _create_jira_downstream_tasks_from_issue_mappings(
        inputs,
        _context,
        execution_creator=execution_creator,
        target_preset=_DOWNSTREAM_PRESET_ORCHESTRATE,
    )


async def create_jira_implement_tasks_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
) -> ToolResult:
    """Create dependent Jira Implement workflow executions from ordered issue mappings."""

    return await _create_jira_downstream_tasks_from_issue_mappings(
        inputs,
        _context,
        execution_creator=execution_creator,
        target_preset=_DOWNSTREAM_PRESET_IMPLEMENT,
    )


def _github_issue_mappings_from_inputs(
    inputs: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    github_payload = _mapping(inputs.get("github"))
    input_previous_outputs = _mapping(
        inputs.get("previousOutputs") or inputs.get("previous_outputs")
    )
    context_previous_outputs = _mapping(
        (context or {}).get("previousOutputs")
        or (context or {}).get("previous_outputs")
    )
    input_previous_github_payload = _mapping(input_previous_outputs.get("github"))
    context_previous_github_payload = _mapping(context_previous_outputs.get("github"))
    candidates = (
        github_payload.get("issueMappings")
        or github_payload.get("issue_mappings")
        or inputs.get("issueMappings")
        or inputs.get("issue_mappings")
        or github_payload.get("createdIssues")
        or inputs.get("createdIssues")
        or input_previous_github_payload.get("issueMappings")
        or input_previous_github_payload.get("issue_mappings")
        or input_previous_github_payload.get("createdIssues")
        or input_previous_outputs.get("issueMappings")
        or input_previous_outputs.get("issue_mappings")
        or input_previous_outputs.get("createdIssues")
        or context_previous_github_payload.get("issueMappings")
        or context_previous_github_payload.get("issue_mappings")
        or context_previous_github_payload.get("createdIssues")
        or context_previous_outputs.get("issueMappings")
        or context_previous_outputs.get("issue_mappings")
        or context_previous_outputs.get("createdIssues")
        or []
    )
    mappings = [dict(item) for item in _list(candidates) if isinstance(item, Mapping)]

    def sort_key(item: Mapping[str, Any]) -> tuple[int, str]:
        raw_index = item.get("storyIndex") or item.get("story_index") or 0
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = 0
        return index, _string(item.get("storyId") or item.get("story_id"))

    return sorted(mappings, key=sort_key)


def _github_issue_input_from_mapping(
    *,
    mapping: Mapping[str, Any],
    repository: str,
    issue_number: str,
    summary: str,
) -> dict[str, Any]:
    nested_issue = _mapping(
        mapping.get("githubIssue")
        or mapping.get("github_issue")
        or mapping.get("issue")
    )
    resolved_repository = _first_string(
        repository,
        nested_issue.get("repository"),
        nested_issue.get("repo"),
        mapping.get("repository"),
        mapping.get("repo"),
    )
    resolved_number = _first_string(
        issue_number,
        nested_issue.get("number"),
        nested_issue.get("issueNumber"),
        nested_issue.get("issue_number"),
        mapping.get("number"),
        mapping.get("issueNumber"),
        mapping.get("issue_number"),
    )
    issue: dict[str, Any] = {
        "repository": resolved_repository,
        "number": int(resolved_number) if resolved_number.isdigit() else resolved_number,
    }
    resolved_title = _first_string(
        summary,
        nested_issue.get("title"),
        nested_issue.get("summary"),
        mapping.get("title"),
        mapping.get("summary"),
    )
    if resolved_title:
        issue["title"] = resolved_title
    url = _first_string(
        mapping.get("issueUrl"),
        mapping.get("issue_url"),
        mapping.get("url"),
        mapping.get("html_url"),
        nested_issue.get("url"),
        nested_issue.get("html_url"),
    )
    if url:
        issue["url"] = url
    return issue


def _github_downstream_workflow_payload(
    *,
    mapping: Mapping[str, Any],
    task_payload: Mapping[str, Any],
    traceability: Mapping[str, Any],
    depends_on: list[str],
    source_issue_key: str,
    target_preset: str,
) -> tuple[str, dict[str, Any]]:
    preset = _GITHUB_DOWNSTREAM_PRESETS[target_preset]
    preset_label = preset["label"]
    preset_slug = preset["slug"]
    repository = _string(
        mapping.get("repository")
        or mapping.get("repo")
        or task_payload.get("repository")
        or task_payload.get("repo")
    )
    issue_number = _string(
        mapping.get("issueNumber")
        or mapping.get("issue_number")
        or mapping.get("number")
    )
    story_id = _string(mapping.get("storyId") or mapping.get("story_id"))
    summary = _string(mapping.get("summary")) or (
        f"{repository}#{issue_number}" if repository and issue_number else issue_number
    )
    source_brief_ref = _string(
        traceability.get("sourceBriefRef") or traceability.get("source_brief_ref")
    )
    source_design_path = _string(
        mapping.get("sourceDesignPath") or mapping.get("source_design_path")
    )
    source_claim_ids = [
        _string(item)
        for item in _list(
            mapping.get("sourceClaimIds") or mapping.get("source_claim_ids")
        )
        if _string(item)
    ]
    github_issue_ref = (
        f"{repository}#{issue_number}" if repository and issue_number else "unknown"
    )
    claim_line = (
        "Source canonical claim IDs: "
        + (", ".join(source_claim_ids) if source_claim_ids else "not provided")
        + ".\n"
    )
    instructions = (
        f"Run {preset_label} for {github_issue_ref}.\n\n"
        f"Source story: {story_id or summary}.\n"
        f"Source summary: {summary}.\n"
        f"Source issue: {source_issue_key or 'unknown'}.\n"
        f"Source design document: {source_design_path or 'not provided'}.\n"
        f"{claim_line}"
        f"Original brief reference: {source_brief_ref or 'not provided'}.\n\n"
        f"Use the existing {preset_label} workflow for this GitHub issue. "
        "Do not run implementation inline inside the breakdown workflow."
    )
    runtime = _mapping(task_payload.get("runtime"))
    publish = _mapping(task_payload.get("publish"))
    configured_inputs = dict(_mapping(task_payload.get("inputs")))
    github_issue_input = _github_issue_input_from_mapping(
        mapping=mapping,
        repository=repository,
        issue_number=issue_number,
        summary=summary,
    )
    task: dict[str, Any] = {
        "title": f"Run {preset_label} for {github_issue_ref}: {summary}",
        "instructions": instructions,
        "inputs": {
            **configured_inputs,
            "github_issue": github_issue_input,
            "github_issue_ref": github_issue_ref,
            "source_design_path": source_design_path,
            "source_claim_ids": source_claim_ids,
            "constraints": (
                f"Preserve source issue {source_issue_key} traceability."
                if source_issue_key
                else ""
            ),
        },
        "taskTemplate": {
            "slug": preset_slug,
        },
    }
    if runtime:
        task["runtime"] = runtime
    if publish:
        task["publish"] = publish
    if repository:
        task["repository"] = repository
    if depends_on:
        task["dependsOn"] = list(depends_on)
    return task["title"], task


async def _create_github_downstream_workflows_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
    target_preset: str,
) -> ToolResult:
    """Create dependent downstream GitHub issue workflows from ordered mappings."""

    preset = _GITHUB_DOWNSTREAM_PRESETS[target_preset]
    preset_label = preset["label"]
    preset_idempotency_prefix = preset["idempotencyPrefix"]

    if execution_creator is None:
        raise ValueError(
            f"execution_creator is required for {preset_label} workflow creation."
        )

    context = _context or {}
    issue_mappings = _github_issue_mappings_from_inputs(inputs, context=context)
    orchestration_payload = _mapping(
        inputs.get("githubOrchestration") or inputs.get("github_orchestration")
    )
    task_payload = _mapping(orchestration_payload.get("task") or inputs.get("task"))
    traceability = _mapping(
        orchestration_payload.get("traceability") or inputs.get("traceability")
    )
    source_issue_key = _source_issue_key(inputs=inputs, traceability=traceability)
    source_brief_ref = _string(
        traceability.get("sourceBriefRef") or traceability.get("source_brief_ref")
    )
    repository = _string(task_payload.get("repository") or task_payload.get("repo"))
    owner_id = (
        _string(task_payload.get("ownerId") or task_payload.get("owner_id"))
        or _string(inputs.get("ownerId") or inputs.get("owner_id"))
        or _string(context.get("ownerId") or context.get("owner_id"))
        or None
    )
    owner_type = (
        _string(task_payload.get("ownerType") or task_payload.get("owner_type"))
        or _string(inputs.get("ownerType") or inputs.get("owner_type"))
        or _string(context.get("ownerType") or context.get("owner_type"))
        or None
    )

    workflows: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    skipped_stories: list[dict[str, Any]] = []
    previous_workflow_id = ""

    for index, mapping in enumerate(issue_mappings, start=1):
        story_id = (
            _string(mapping.get("storyId") or mapping.get("story_id"))
            or f"STORY-{index:03d}"
        )
        story_index = mapping.get("storyIndex") or mapping.get("story_index") or index
        issue_number = _string(
            mapping.get("issueNumber")
            or mapping.get("issue_number")
            or mapping.get("number")
        )
        mapping_repository = _string(
            mapping.get("repository") or mapping.get("repo") or repository
        )
        summary = _string(mapping.get("summary"))
        base_result = {
            "storyId": story_id,
            "storyIndex": story_index,
            "repository": mapping_repository,
            "githubIssueNumber": issue_number,
        }
        if not mapping_repository or not issue_number:
            failures.append(
                {
                    **base_result,
                    "errorCode": "missing_github_issue_ref",
                    "message": (
                        f"{preset_label} workflow creation requires repository "
                        "and issueNumber."
                    ),
                }
            )
            skipped_stories.append({**base_result, "summary": summary})
            continue

        depends_on = [previous_workflow_id] if previous_workflow_id else []
        title, task = _github_downstream_workflow_payload(
            mapping={**dict(mapping), "repository": mapping_repository},
            task_payload=task_payload,
            traceability=traceability,
            depends_on=depends_on,
            source_issue_key=source_issue_key,
            target_preset=target_preset,
        )
        idempotency_key = _stable_idempotency_key(
            source_issue_key=source_issue_key,
            story_id=story_id,
            issue_key=f"{mapping_repository}#{issue_number}",
            prefix=preset_idempotency_prefix,
        )
        try:
            created = execution_creator(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                owner_type=owner_type,
                title=title,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": mapping_repository,
                    "targetRuntime": _string(_mapping(task.get("runtime")).get("mode")) or None,
                    "publishMode": _string(_mapping(task.get("publish")).get("mode")) or None,
                    "workflow": task,
                    "traceability": {
                        "sourceIssueKey": source_issue_key,
                        "sourceBriefRef": source_brief_ref,
                    },
                },
                idempotency_key=idempotency_key,
                repository=mapping_repository,
                integration="github",
                summary=f"{preset_label} workflow for {mapping_repository}#{issue_number}.",
            )
            if inspect.isawaitable(created):
                created = await created  # type: ignore[assignment]
        except Exception as exc:
            failures.append(
                {
                    **base_result,
                    "errorCode": "workflow_creation_failed",
                    "message": str(exc) or "Downstream workflow creation failed.",
                    "dependsOn": depends_on,
                }
            )
            remaining = issue_mappings[index:]
            for skipped in remaining:
                skipped_repository = _string(
                    skipped.get("repository") or skipped.get("repo") or repository
                )
                skipped_stories.append(
                    {
                        "storyId": _string(skipped.get("storyId") or skipped.get("story_id")),
                        "storyIndex": skipped.get("storyIndex") or skipped.get("story_index"),
                        "repository": skipped_repository,
                        "githubIssueNumber": _string(
                            skipped.get("issueNumber")
                            or skipped.get("issue_number")
                            or skipped.get("number")
                        ),
                        "errorCode": "dependency_not_created",
                        "message": "Earlier downstream workflow creation failed.",
                    }
                )
            break

        created_mapping = dict(created)
        workflow_id = _string(
            created_mapping.get("workflowId") or created_mapping.get("workflow_id")
        )
        workflow_result = {
            **base_result,
            "workflowId": workflow_id,
            "runId": _string(created_mapping.get("runId") or created_mapping.get("run_id")),
            "title": _string(created_mapping.get("title")) or title,
            "created": not bool(created_mapping.get("existing")),
            "existing": bool(created_mapping.get("existing")),
            "dependsOn": depends_on,
            "idempotencyKey": idempotency_key,
        }
        workflows.append(workflow_result)
        if depends_on:
            dependencies.append(
                {
                    "fromWorkflowId": depends_on[0],
                    "toWorkflowId": workflow_id,
                    "fromStoryId": workflows[-2]["storyId"] if len(workflows) > 1 else "",
                    "toStoryId": story_id,
                    "status": "created",
                }
            )
        previous_workflow_id = workflow_id

    if not issue_mappings:
        status = "no_downstream_workflows"
    elif failures or skipped_stories:
        status = "partial" if workflows else "no_downstream_workflows"
    else:
        status = "completed"

    return ToolResult(
        status="COMPLETED",
        outputs={
            "githubWorkflowOrchestration": {
                "status": status,
                "storyCount": len(issue_mappings),
                "createdWorkflowCount": len(workflows),
                "dependencyMode": "workflow_linear_chain" if dependencies else "none",
                "dependencyCount": len(dependencies),
                "workflows": workflows,
                "dependencies": dependencies,
                "skippedStories": skipped_stories,
                "failures": failures,
                "traceability": {
                    "sourceIssueKey": source_issue_key,
                    "sourceBriefRef": source_brief_ref,
                },
            }
        },
    )


async def create_github_issue_orchestrate_workflows_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
) -> ToolResult:
    """Create dependent GitHub Issue Orchestrate workflows from issue mappings."""

    return await _create_github_downstream_workflows_from_issue_mappings(
        inputs,
        _context,
        execution_creator=execution_creator,
        target_preset=_DOWNSTREAM_PRESET_ORCHESTRATE,
    )


async def create_github_issue_implement_workflows_from_issue_mappings(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
) -> ToolResult:
    """Create dependent GitHub Issue Implement workflows from issue mappings."""

    return await _create_github_downstream_workflows_from_issue_mappings(
        inputs,
        _context,
        execution_creator=execution_creator,
        target_preset=_DOWNSTREAM_PRESET_IMPLEMENT,
    )


def _dependency_mode(
    *,
    inputs: Mapping[str, Any],
    story_output: Mapping[str, Any],
    jira_payload: Mapping[str, Any],
) -> tuple[str, str]:
    for source in (jira_payload, story_output, inputs):
        for key in ("dependencyMode", "dependency_mode", "jiraDependencyMode"):
            if key not in source:
                continue
            raw = source.get(key)
            normalized = str(raw or "").strip().lower()
            if not normalized:
                return "", "Jira dependencyMode must not be blank."
            if normalized not in JIRA_DEPENDENCY_MODES:
                return normalized, (
                    "Unsupported Jira dependencyMode "
                    f"'{normalized}'. Supported values: none, linear_blocker_chain."
                )
            return normalized, ""
    return JIRA_DEPENDENCY_MODE_NONE, ""

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
    dependency_mode: str = "",
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
    if dependency_mode and dependency_mode != JIRA_DEPENDENCY_MODE_NONE:
        story_output["dependencyMode"] = dependency_mode
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

def _jira_noop_result(
    *,
    original_story_count: int,
    dependency_mode: str,
    skipped_stories: Sequence[Mapping[str, Any]] = (),
    blocked_stories: Sequence[Mapping[str, Any]] = (),
    partial_stories_adjusted: Sequence[Mapping[str, Any]] = (),
    story_breakdown_artifact_ref: str = "",
    story_breakdown_path: str = "",
) -> ToolResult:
    story_output: dict[str, Any] = {
        "mode": "jira",
        "status": "jira_noop",
        "storyCount": original_story_count,
        "eligibleStoryCount": 0,
        "createdCount": 0,
        "dependencyMode": dependency_mode,
        "skippedStories": [dict(story) for story in skipped_stories],
        "blockedStories": [dict(story) for story in blocked_stories],
        "partialStoriesAdjusted": [
            dict(story) for story in partial_stories_adjusted
        ],
    }
    if story_breakdown_artifact_ref:
        story_output["storyBreakdownArtifactRef"] = story_breakdown_artifact_ref
    if story_breakdown_path:
        story_output["storyBreakdownPath"] = story_breakdown_path
    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": story_output,
            "jira": {
                "createdCount": 0,
                "createdIssues": [],
                "dependencyMode": dependency_mode,
                "issueMappings": [],
                "linkResults": [],
                "linkCount": 0,
                "dependencyChainComplete": (
                    None
                    if dependency_mode == JIRA_DEPENDENCY_MODE_NONE
                    else True
                ),
                "skippedStories": [dict(story) for story in skipped_stories],
                "blockedStories": [dict(story) for story in blocked_stories],
                "partialStoriesAdjusted": [
                    dict(story) for story in partial_stories_adjusted
                ],
            },
        },
    )

def _unpublished_handoff_reason(
    *,
    previous_outputs: Mapping[str, Any],
    ref: str,
) -> str:
    push_status = _string(
        previous_outputs.get("push_status")
        or previous_outputs.get("pushStatus")
    )
    push_branch = _string(
        previous_outputs.get("push_branch")
        or previous_outputs.get("pushBranch")
    )
    if not push_branch or ref != push_branch:
        return ""
    if push_status == "protected_branch":
        return (
            "Unable to read story breakdown for Jira output because "
            f"the previous step produced it on protected branch '{push_branch}' "
            "and it was not published. Jira story creation requires inline "
            "stories, storyBreakdownArtifactRef, or a readable repo/ref/path "
            "from a published handoff branch."
        )
    if push_status == "no_commits":
        return (
            "Unable to read story breakdown for Jira output because "
            f"the previous step made no commits on handoff branch '{push_branch}'. "
            "The story breakdown was not produced or was not captured as an "
            "inline story payload, storyBreakdownArtifactRef, or readable "
            "repo/ref/path handoff."
    )
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _artifact_handoff_required(story_output: Mapping[str, Any]) -> bool:
    if _truthy(
        story_output.get("requiresStoryBreakdownArtifactRef")
        or story_output.get("requires_story_breakdown_artifact_ref")
    ):
        return True
    handoff = str(
        story_output.get("handoff")
        or story_output.get("handoffMode")
        or story_output.get("handoff_mode")
        or story_output.get("storyBreakdownHandoff")
        or story_output.get("story_breakdown_handoff")
        or ""
    ).strip().lower()
    return handoff in {
        "artifact",
        "artifact_ref",
        "artifact-ref",
        "artifact_only",
        "artifact-only",
    }


def _missing_artifact_handoff_reason(
    *,
    path: str,
    repo: str,
    ref: str,
) -> str:
    location = f"`{path}`" if path else "the runtime story breakdown path"
    repo_hint = (
        f" The configured repo/ref fallback was {repo}@{ref}."
        if repo and ref
        else ""
    )
    return (
        "Unable to create Jira issues because Jira-mode story output requires "
        f"a durable storyBreakdownArtifactRef for {location}, but no artifact "
        "reference was provided by the previous step. The breakdown must be "
        "published from the managed agent workspace or passed inline before "
        "Jira issue creation runs."
        + repo_hint
    )


def _repo_handoff_read_failure_reason(
    *,
    repo: str,
    ref: str,
    path: str,
    error: Exception,
) -> str:
    return (
        "Unable to read story breakdown for Jira output from repository "
        f"'{repo}' at ref '{ref}' and path '{path}'. If this breakdown was "
        "generated by a previous agent step, it should be passed as "
        f"storyBreakdownArtifactRef instead of relying on GitHub. GitHub/read "
        f"error: {error}"
    )

def _issue_mapping(
    *,
    story: Mapping[str, Any],
    issue: Mapping[str, Any],
    index: int,
    summary: str,
    fallback_source_path: str = "",
) -> dict[str, Any]:
    mapping = dict(issue)
    mapping["storyId"] = _story_id(story, index=index)
    mapping["storyIndex"] = index
    mapping["summary"] = summary
    mapping["dependencies"] = _story_dependency_ids(story)
    source_reference = _story_source_reference(
        story, fallback_path=fallback_source_path
    )
    mapping["sourceDesignPath"] = _string(source_reference.get("path"))
    mapping["sourceClaimIds"] = _story_source_claim_ids(source_reference)
    return mapping

def _order_issue_mappings_by_dependencies(
    issue_mappings: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Order mappings so each story's declared prerequisites precede it.

    The linear blocker chain links consecutive mappings so the earlier one
    blocks the later one; that is only correct when prerequisites appear before
    the stories that depend on them. moonspec-breakdown is asked to emit stories
    in dependency order, but that ordering is advisory LLM guidance, whereas the
    per-story ``dependencies`` field is the authoritative signal. This performs a
    stable topological sort by that field so an out-of-order (or fully reversed)
    breakdown still produces a chain that runs prerequisites first instead of
    reversing the dependencies. Stories without dependencies keep their original
    relative order, and any dependency cycle falls back to original order for the
    unresolved remainder rather than dropping stories or failing.
    """

    known_ids = {
        _string(mapping.get("storyId"))
        for mapping in issue_mappings
        if _string(mapping.get("storyId"))
    }
    remaining: list[Mapping[str, Any]] = list(issue_mappings)
    ordered: list[Mapping[str, Any]] = []
    resolved: set[str] = set()
    while remaining:
        for position, mapping in enumerate(remaining):
            story_id = _string(mapping.get("storyId"))
            prerequisites = [
                dep
                for dep in _story_dependency_ids(mapping)
                if dep in known_ids and dep != story_id
            ]
            if all(dep in resolved for dep in prerequisites):
                ordered.append(mapping)
                if story_id:
                    resolved.add(story_id)
                remaining.pop(position)
                break
        else:
            # No remaining story has all prerequisites resolved (dependency
            # cycle, or only self/unknown references remain). Preserve the
            # remaining mappings in their original order to stay deterministic.
            ordered.extend(remaining)
            break
    return ordered

def _dependency_link_pairs(
    ordered_mappings: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Return ``(blocker, blocked)`` mapping pairs for the linear blocker chain.

    Each story is linked to the stories it declares as prerequisites, so a
    fan-out breakdown (for example STORY-002 and STORY-003 both depending only on
    STORY-001) produces the direct ``001 blocks 002`` and ``001 blocks 003``
    links instead of a misleading ``001 -> 002 -> 003`` adjacent chain that would
    fabricate ``002 blocks 003`` and drop the real ``001 blocks 003`` edge. When
    no story declares a resolvable dependency, fall back to linking each story to
    the immediately preceding one so dependency-free breakdowns keep the
    historical linear chain. A declared cycle emits a single deterministic link
    per story pair rather than reciprocal ``Blocks`` links.
    """

    mapping_by_id: dict[str, Mapping[str, Any]] = {}
    for mapping in ordered_mappings:
        story_id = _string(mapping.get("storyId"))
        if story_id and story_id not in mapping_by_id:
            mapping_by_id[story_id] = mapping

    def _prerequisites(mapping: Mapping[str, Any]) -> list[str]:
        story_id = _string(mapping.get("storyId"))
        return [
            dep
            for dep in _story_dependency_ids(mapping)
            if dep in mapping_by_id and dep != story_id
        ]

    if not any(_prerequisites(mapping) for mapping in ordered_mappings):
        return list(zip(ordered_mappings, ordered_mappings[1:]))

    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for mapping in ordered_mappings:
        story_id = _string(mapping.get("storyId"))
        for dep in _prerequisites(mapping):
            if (dep, story_id) in seen or (story_id, dep) in seen:
                continue
            seen.add((dep, story_id))
            pairs.append((mapping_by_id[dep], mapping))
    return pairs

async def _create_dependency_links(
    *,
    service: JiraToolService,
    dependency_mode: str,
    issue_mappings: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], bool | None]:
    if dependency_mode == JIRA_DEPENDENCY_MODE_NONE:
        return [], None
    if dependency_mode != JIRA_DEPENDENCY_MODE_LINEAR_BLOCKER_CHAIN:
        raise ValueError(f"Unsupported Jira dependencyMode '{dependency_mode}'.")

    ordered_mappings = _order_issue_mappings_by_dependencies(issue_mappings)
    link_results: list[dict[str, Any]] = []
    for previous, current in _dependency_link_pairs(ordered_mappings):
        blocks_issue_key = _string(previous.get("issueKey"))
        blocked_issue_key = _string(current.get("issueKey"))
        base_result = {
            "fromStoryId": _string(previous.get("storyId")),
            "fromStoryIndex": previous.get("storyIndex"),
            "toStoryId": _string(current.get("storyId")),
            "toStoryIndex": current.get("storyIndex"),
            "blocksIssueKey": blocks_issue_key,
            "blockedIssueKey": blocked_issue_key,
            "linkType": "Blocks",
        }
        if not blocks_issue_key or not blocked_issue_key:
            link_results.append(
                {
                    **base_result,
                    "status": "failed",
                    "errorCode": "jira_validation_failed",
                    "message": "Jira dependency link requires both issue keys.",
                }
            )
            continue
        try:
            result = await service.create_issue_link(
                CreateIssueLinkRequest(
                    blocksIssueKey=blocks_issue_key,
                    blockedIssueKey=blocked_issue_key,
                )
            )
        except Exception as exc:
            link_results.append(
                {
                    **base_result,
                    "status": "failed",
                    "errorCode": (
                        _string(getattr(exc, "code", ""))
                        or exc.__class__.__name__
                    ),
                    "message": "Jira dependency link creation failed.",
                }
            )
            continue
        status = "existing" if result.get("existing") else "created"
        link_results.append({**base_result, "status": status})

    chain_complete = all(
        item.get("status") in {"created", "existing"} for item in link_results
    )
    return link_results, chain_complete

def _extract_issue_type_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        candidates = (
            payload.get("issueTypes")
            or payload.get("issuetypes")
            or payload.get("values")
            or payload.get("items")
            or []
        )
    else:
        candidates = payload
    return [dict(item) for item in _list(candidates) if isinstance(item, Mapping)]

async def _resolve_issue_type_id(
    *,
    service: JiraToolService,
    project_key: str,
    issue_type_id: str,
    issue_type_name: str,
) -> str:
    if issue_type_id:
        return issue_type_id
    if not issue_type_name:
        return ""
    payload = await service.list_create_issue_types(
        ListCreateIssueTypesRequest(projectKey=project_key)
    )
    normalized_name = issue_type_name.strip().lower()
    for item in _extract_issue_type_items(payload):
        if _string(item.get("name")).lower() == normalized_name:
            return _string(item.get("id"))
    return ""

async def create_jira_issues_from_stories(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    jira_service_factory: JiraServiceFactory = JiraToolService,
    story_fetcher: StoryFetcher = _default_github_story_fetcher,
    artifact_reader: ArtifactReader | None = None,
) -> ToolResult:
    """Create one Jira issue per story, or report story-breakdown fallback metadata."""

    previous_outputs = _mapping(
        (_context or {}).get("previousOutputs")
        or (_context or {}).get("previous_outputs")
        or inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
    )
    story_output = _mapping(inputs.get("storyOutput") or inputs.get("story_output"))
    previous_story_output = _mapping(
        previous_outputs.get("storyOutput") or previous_outputs.get("story_output")
    )
    story_output_mode = str(
        story_output.get("mode") or story_output.get("target") or ""
    ).strip().lower()
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
    issue_type_name = _string(
        jira_payload.get("issueTypeName")
        or jira_payload.get("issue_type_name")
        or jira_payload.get("issueType")
        or jira_payload.get("issue_type")
        or inputs.get("issueTypeName")
        or inputs.get("issue_type_name")
        or inputs.get("issueType")
        or inputs.get("issue_type")
    )
    fallback_keys = ("fallback", "onFailure", "on_failure")
    fallback_configured = any(key in story_output for key in fallback_keys)
    fallback_on_failure = str(
        next(
            (
                story_output.get(key)
                for key in fallback_keys
                if key in story_output
            ),
            "docs_tmp",
        )
    ).strip().lower() not in {"fail", "none", "false"}
    fallback_for_missing_stories = fallback_on_failure and (
        story_output_mode != "jira" or fallback_configured
    )
    dependency_mode, dependency_mode_error = _dependency_mode(
        inputs=inputs,
        story_output=story_output,
        jira_payload=jira_payload,
    )
    if dependency_mode_error:
        if fallback_on_failure:
            return _fallback_result(
                reason=dependency_mode_error,
                inputs=inputs,
                dependency_mode=dependency_mode,
            )
        raise ValueError(dependency_mode_error)

    raw_story_payload = (
        inputs.get("stories")
        or inputs.get("storyBreakdown")
        or inputs.get("story_breakdown")
        or inputs.get("storyBreakdownJson")
        or previous_outputs.get("stories")
        or previous_outputs.get("storyBreakdown")
        or previous_outputs.get("story_breakdown")
        or previous_outputs.get("storyBreakdownJson")
        or previous_story_output.get("stories")
        or previous_story_output.get("storyBreakdown")
        or previous_story_output.get("story_breakdown")
        or previous_story_output.get("storyBreakdownJson")
    )
    parsed_story_payload = _parse_story_breakdown_payload(raw_story_payload)
    breakdown_source_path = _breakdown_source_path(parsed_story_payload)
    breakdown_source_document_class = _breakdown_source_document_class(
        parsed_story_payload
    )
    stories = _coerce_story_payload(parsed_story_payload)
    artifact_ref_was_read = False
    artifact_payload_had_explicit_empty_stories = False
    artifact_payload_failure_reason = ""
    artifact_ref = ""
    if not stories:
        artifact_ref = _string(
            inputs.get("storyBreakdownArtifactRef")
            or inputs.get("story_breakdown_artifact_ref")
            or story_output.get("storyBreakdownArtifactRef")
            or story_output.get("story_breakdown_artifact_ref")
            or previous_outputs.get("storyBreakdownArtifactRef")
            or previous_outputs.get("story_breakdown_artifact_ref")
            or previous_story_output.get("storyBreakdownArtifactRef")
            or previous_story_output.get("story_breakdown_artifact_ref")
        )
        if artifact_ref:
            try:
                if artifact_reader is None:
                    raise ValueError(
                        "storyBreakdownArtifactRef was provided, but this worker "
                        "has no artifact reader configured."
                    )
                artifact_payload = artifact_reader(artifact_ref)
                if inspect.isawaitable(artifact_payload):
                    artifact_payload = await artifact_payload  # type: ignore[assignment]
                parsed_payload = _parse_story_breakdown_payload(artifact_payload)
                breakdown_source_path = _breakdown_source_path(parsed_payload)
                breakdown_source_document_class = _breakdown_source_document_class(
                    parsed_payload
                )
                artifact_payload_failure_reason = _story_breakdown_failure_reason(
                    parsed_payload
                )
                stories = _coerce_story_payload(parsed_payload)
                artifact_ref_was_read = True
                artifact_payload_had_explicit_empty_stories = (
                    _has_explicit_empty_story_list(parsed_payload)
                )
            except Exception as exc:
                if fallback_for_missing_stories:
                    return _fallback_result(
                        reason=(
                            "Unable to read story breakdown artifact for Jira "
                            f"output: {exc}"
                        ),
                        inputs=inputs,
                        dependency_mode=dependency_mode,
                    )
                raise
    if (
        not stories
        and artifact_ref_was_read
        and artifact_payload_failure_reason
    ):
        if fallback_for_missing_stories:
            return _fallback_result(
                reason=artifact_payload_failure_reason,
                inputs=inputs,
                dependency_mode=dependency_mode,
            )
        raise ValueError(artifact_payload_failure_reason)
    if (
        not stories
        and artifact_ref_was_read
        and artifact_payload_had_explicit_empty_stories
    ):
        return _jira_noop_result(
            original_story_count=0,
            dependency_mode=dependency_mode,
            story_breakdown_artifact_ref=artifact_ref,
            story_breakdown_path=_string(
                inputs.get("storyBreakdownPath")
                or story_output.get("storyBreakdownPath")
                or previous_outputs.get("storyBreakdownPath")
                or previous_story_output.get("storyBreakdownPath")
            ),
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
            or previous_outputs.get("storyBreakdownPath")
            or previous_story_output.get("storyBreakdownPath")
        )
        if repo and ref and path:
            if (
                story_output_mode == "jira"
                and _artifact_handoff_required(story_output)
            ):
                reason = _missing_artifact_handoff_reason(
                    path=path,
                    repo=repo,
                    ref=ref,
                )
                if fallback_for_missing_stories:
                    return _fallback_result(
                        reason=reason,
                        inputs=inputs,
                        dependency_mode=dependency_mode,
                    )
                raise ValueError(reason)
            unpublished_reason = _unpublished_handoff_reason(
                previous_outputs=previous_outputs,
                ref=ref,
            )
            if unpublished_reason:
                raise ValueError(unpublished_reason)
            try:
                fetched = story_fetcher(repo, ref, path)
                if inspect.isawaitable(fetched):
                    fetched = await fetched  # type: ignore[assignment]
                fetched_payload = _parse_story_breakdown_payload(fetched)
                breakdown_source_path = _breakdown_source_path(fetched_payload)
                breakdown_source_document_class = _breakdown_source_document_class(
                    fetched_payload
                )
                stories = _coerce_story_payload(fetched_payload)
            except Exception as exc:
                reason = _repo_handoff_read_failure_reason(
                    repo=repo,
                    ref=ref,
                    path=path,
                    error=exc,
                )
                if fallback_for_missing_stories:
                    return _fallback_result(
                        reason=reason,
                        inputs=inputs,
                        dependency_mode=dependency_mode,
                    )
                raise ValueError(reason) from exc

    if not stories:
        if fallback_for_missing_stories:
            return _fallback_result(
                reason="No stories were available for Jira issue creation.",
                inputs=inputs,
                dependency_mode=dependency_mode,
            )
        raise ValueError("No stories were available for Jira issue creation.")

    original_story_count = len(stories)
    (
        stories,
        skipped_stories,
        blocked_stories,
        partial_stories_adjusted,
    ) = _reconcile_stories_for_issue_creation(stories)
    if not stories:
        return _jira_noop_result(
            original_story_count=original_story_count,
            dependency_mode=dependency_mode,
            skipped_stories=skipped_stories,
            blocked_stories=blocked_stories,
            partial_stories_adjusted=partial_stories_adjusted,
        )

    if not project_key:
        reason = (
            "Jira projectKey and issueTypeId are required."
            if not (issue_type_id or issue_type_name)
            else "Jira projectKey is required."
        )
        if fallback_on_failure:
            return _fallback_result(
                reason=reason,
                inputs=inputs,
                story_count=len(stories),
                dependency_mode=dependency_mode,
            )
        raise ValueError(reason)

    requires_source_reference = _requires_story_source_reference(
        inputs=inputs,
        story_output=story_output,
        fallback_path=breakdown_source_path,
    )
    if requires_source_reference:
        missing_source_ids = _missing_source_reference_story_ids(
            stories,
            fallback_path=breakdown_source_path,
        )
        if missing_source_ids:
            reason = (
                "Jira story creation requires sourceReference.path or breakdown "
                "source.referencePath for every story. Missing: "
                + ", ".join(missing_source_ids)
            )
            if fallback_on_failure:
                return _fallback_result(
                    reason=reason,
                    inputs=inputs,
                    story_count=len(stories),
                    dependency_mode=dependency_mode,
                )
            raise ValueError(reason)
    missing_claim_ids = (
        _missing_source_claim_story_ids(
            stories,
            fallback_path=breakdown_source_path,
            source_document_class=breakdown_source_document_class,
        )
        if requires_source_reference
        else []
    )
    if missing_claim_ids:
        reason = (
            "Jira story creation requires sourceReference.claimIds for every "
            "canonical declarative story with sourceReference.path or breakdown "
            "source.referencePath. Missing: "
            + ", ".join(missing_claim_ids)
        )
        if fallback_on_failure:
            return _fallback_result(
                reason=reason,
                inputs=inputs,
                story_count=len(stories),
                dependency_mode=dependency_mode,
            )
        raise ValueError(reason)

    service = jira_service_factory()
    try:
        issue_type_id = await _resolve_issue_type_id(
            service=service,
            project_key=project_key,
            issue_type_id=issue_type_id,
            issue_type_name=issue_type_name,
        )
    except Exception as exc:
        if fallback_on_failure:
            return _fallback_result(
                reason=f"Unable to resolve Jira issue type: {exc}",
                inputs=inputs,
                story_count=len(stories),
                dependency_mode=dependency_mode,
            )
        raise
    if not issue_type_id:
        reason = (
            "Jira issueTypeId is required or issueTypeName must resolve to a "
            "creatable issue type."
        )
        if fallback_on_failure:
            return _fallback_result(
                reason=reason,
                inputs=inputs,
                story_count=len(stories),
                dependency_mode=dependency_mode,
            )
        raise ValueError(reason)

    is_subtask_issue_type = _is_subtask_issue_type_name(issue_type_name)
    source_issue_key = _source_issue_key(
        inputs=inputs,
        traceability=jira_payload,
    )
    source_parent_issue_key = source_issue_key if is_subtask_issue_type else ""
    explicit_parent_issue_key = _parent_issue_key(
        story={},
        jira_payload=jira_payload,
        inputs=inputs,
    )
    has_all_story_parent_issue_keys = all(
        _parent_issue_key(story=story, jira_payload={}, inputs={})
        for story in stories
    )
    if (
        is_subtask_issue_type
        and not source_parent_issue_key
        and not explicit_parent_issue_key
        and not has_all_story_parent_issue_keys
    ):
        reason = (
            "Jira issueTypeName 'Sub-task' requires sourceIssueKey or "
            "parentIssueKey for every story so created issues can be attached "
            "to a parent issue."
        )
        if fallback_on_failure:
            return _fallback_result(
                reason=reason,
                inputs=inputs,
                story_count=len(stories),
                dependency_mode=dependency_mode,
            )
        raise ValueError(reason)

    created: list[dict[str, Any]] = []
    issue_mappings: list[dict[str, Any]] = []
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
                issue_mappings.append(
                    _issue_mapping(
                        story=story,
                        issue=existing_issue,
                        index=index,
                        summary=summary,
                        fallback_source_path=breakdown_source_path,
                    )
                )
                continue

            fields = _merge_fields(
                story=story,
                jira_payload=jira_payload,
                marker_label=marker_label,
            )
            description = _truncate_jira_description(
                _story_description_with_source(
                    story,
                    fallback_source_path=breakdown_source_path,
                )
            )
            parent_issue_key = _parent_issue_key(
                story=story,
                jira_payload=jira_payload,
                inputs=inputs,
            ) or source_parent_issue_key
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
            issue_result = dict(result)
            created.append(issue_result)
            issue_mappings.append(
                _issue_mapping(
                    story=story,
                    issue=issue_result,
                    index=index,
                    summary=summary,
                    fallback_source_path=breakdown_source_path,
                )
            )
    except Exception as exc:
        if fallback_on_failure:
            return _fallback_result(
                reason=f"Jira issue creation failed: {exc}",
                inputs=inputs,
                story_count=len(stories),
                created=created,
                dependency_mode=dependency_mode,
            )
        raise

    link_results, dependency_chain_complete = await _create_dependency_links(
        service=service,
        dependency_mode=dependency_mode,
        issue_mappings=issue_mappings,
    )
    link_count = sum(
        1 for item in link_results if item.get("status") in {"created", "existing"}
    )
    partial = bool(blocked_stories) or any(
        item.get("status") == "failed" for item in link_results
    )
    story_status = "jira_partial" if partial else "jira_created"

    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": {
                "mode": "jira",
                "status": story_status,
                "storyCount": original_story_count,
                "eligibleStoryCount": len(stories),
                "createdCount": len(created),
                "dependencyMode": dependency_mode,
                "skippedStories": skipped_stories,
                "blockedStories": blocked_stories,
                "partialStoriesAdjusted": partial_stories_adjusted,
            },
            "jira": {
                "createdCount": len(created),
                "createdIssues": created,
                "dependencyMode": dependency_mode,
                "issueMappings": issue_mappings,
                "linkResults": link_results,
                "linkCount": link_count,
                "dependencyChainComplete": dependency_chain_complete,
                "skippedStories": skipped_stories,
                "blockedStories": blocked_stories,
                "partialStoriesAdjusted": partial_stories_adjusted,
                **({"partial": True} if partial else {}),
            },
        },
    )

def _github_story_output_payload(
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    return _mapping(inputs.get("github") or inputs.get("storyOutput") or inputs.get("story_output"))


def _github_repository_from_inputs(inputs: Mapping[str, Any]) -> str:
    github_payload = _github_story_output_payload(inputs)
    return _string(
        inputs.get("repository")
        or inputs.get("repo")
        or github_payload.get("repository")
        or github_payload.get("repo")
    )


def _github_labels(
    *,
    story: Mapping[str, Any],
    github_payload: Mapping[str, Any],
    marker_label: str,
) -> list[str]:
    labels: list[str] = []
    for source in (github_payload, story):
        for item in _list(source.get("labels")):
            label = _string(item.get("name") if isinstance(item, Mapping) else item)
            if label:
                labels.append(label)
    if marker_label:
        labels.append(marker_label)
    return list(dict.fromkeys(labels))


def _github_issue_mapping(
    *,
    story: Mapping[str, Any],
    issue: Mapping[str, Any],
    repository: str,
    index: int,
    summary: str,
    fallback_source_path: str,
) -> dict[str, Any]:
    reference = _story_source_reference(story, fallback_path=fallback_source_path)
    number = _string(
        issue.get("number")
        or issue.get("issueNumber")
        or issue.get("issue_number")
        or issue.get("externalKey")
        or issue.get("external_key")
    )
    url = _string(
        issue.get("html_url")
        or issue.get("issueUrl")
        or issue.get("issue_url")
        or issue.get("externalUrl")
        or issue.get("external_url")
        or issue.get("url")
    )
    mapping = {
        "storyId": _story_id(story, index=index),
        "storyIndex": index,
        "summary": summary,
        "repository": repository,
        "issueNumber": number,
        "issueUrl": url,
        "sourceDesignPath": _string(reference.get("path")),
        "sourceTitle": _string(reference.get("title")),
        "sourceClaimIds": _story_source_claim_ids(reference),
        "sourceSections": [
            _string(item) for item in _list(reference.get("sections")) if _string(item)
        ],
        "sourceIssueKey": _string(
            reference.get("sourceIssueKey") or reference.get("source_issue_key")
        ),
    }
    coverage_ids = [
        _string(item)
        for item in _list(reference.get("coverageIds") or reference.get("coverage_ids"))
        if _string(item)
    ]
    if coverage_ids:
        mapping["coverageIds"] = coverage_ids
    return mapping


def _github_noop_result(
    *,
    original_story_count: int,
    skipped_stories: Sequence[Mapping[str, Any]] = (),
    blocked_stories: Sequence[Mapping[str, Any]] = (),
    partial_stories_adjusted: Sequence[Mapping[str, Any]] = (),
    story_breakdown_artifact_ref: str = "",
    story_breakdown_path: str = "",
) -> ToolResult:
    story_output: dict[str, Any] = {
        "mode": "github",
        "status": "github_noop",
        "storyCount": original_story_count,
        "eligibleStoryCount": 0,
        "createdCount": 0,
        "dependencyMode": "none",
        "dependencyCount": 0,
        "skippedStories": [dict(story) for story in skipped_stories],
        "blockedStories": [dict(story) for story in blocked_stories],
        "partialStoriesAdjusted": [dict(story) for story in partial_stories_adjusted],
    }
    if story_breakdown_artifact_ref:
        story_output["storyBreakdownArtifactRef"] = story_breakdown_artifact_ref
    if story_breakdown_path:
        story_output["storyBreakdownPath"] = story_breakdown_path
    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": story_output,
            "github": {
                "createdCount": 0,
                "createdIssues": [],
                "dependencyMode": "none",
                "dependencyCount": 0,
                "issueMappings": [],
                "skippedStories": story_output["skippedStories"],
                "blockedStories": story_output["blockedStories"],
                "partialStoriesAdjusted": story_output["partialStoriesAdjusted"],
            },
        },
    )


async def create_github_issues_from_stories(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    github_service_factory: Callable[[], GitHubService] = GitHubService,
    story_fetcher: StoryFetcher = _default_github_story_fetcher,
    artifact_reader: ArtifactReader | None = None,
) -> ToolResult:
    """Create one GitHub issue per reconciled story candidate."""

    previous_outputs = _mapping(
        (_context or {}).get("previousOutputs")
        or (_context or {}).get("previous_outputs")
        or inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
    )
    story_output = _mapping(inputs.get("storyOutput") or inputs.get("story_output"))
    previous_story_output = _mapping(
        previous_outputs.get("storyOutput") or previous_outputs.get("story_output")
    )
    github_payload = _mapping(story_output.get("github") or inputs.get("github"))
    repository = _github_repository_from_inputs(
        {**dict(inputs), "github": {**github_payload, **_mapping(inputs.get("github"))}}
    )
    if not repository:
        raise ValueError("repository is required for GitHub story issue creation.")

    dependency_mode = _string(
        github_payload.get("dependencyMode")
        or github_payload.get("dependency_mode")
        or story_output.get("dependencyMode")
        or story_output.get("dependency_mode")
        or inputs.get("dependencyMode")
        or inputs.get("dependency_mode")
        or "none"
    ).lower()
    if dependency_mode != "none":
        raise ValueError(
            "GitHub story issue creation supports dependencyMode 'none' only; "
            "GitHub issue dependencies are not claimed without a trusted API result."
        )

    raw_story_payload = (
        inputs.get("stories")
        or inputs.get("storyBreakdown")
        or inputs.get("story_breakdown")
        or inputs.get("storyBreakdownJson")
        or previous_outputs.get("stories")
        or previous_outputs.get("storyBreakdown")
        or previous_outputs.get("story_breakdown")
        or previous_outputs.get("storyBreakdownJson")
        or previous_story_output.get("stories")
        or previous_story_output.get("storyBreakdown")
        or previous_story_output.get("story_breakdown")
        or previous_story_output.get("storyBreakdownJson")
    )
    parsed_story_payload = _parse_story_breakdown_payload(raw_story_payload)
    breakdown_source_path = _breakdown_source_path(parsed_story_payload)
    breakdown_source_document_class = _breakdown_source_document_class(
        parsed_story_payload
    )
    stories = _coerce_story_payload(parsed_story_payload)
    artifact_ref_was_read = False
    artifact_payload_had_explicit_empty_stories = False
    artifact_payload_failure_reason = ""
    artifact_ref = ""
    if not stories:
        artifact_ref = _string(
            inputs.get("storyBreakdownArtifactRef")
            or inputs.get("story_breakdown_artifact_ref")
            or story_output.get("storyBreakdownArtifactRef")
            or story_output.get("story_breakdown_artifact_ref")
            or previous_outputs.get("storyBreakdownArtifactRef")
            or previous_outputs.get("story_breakdown_artifact_ref")
            or previous_story_output.get("storyBreakdownArtifactRef")
            or previous_story_output.get("story_breakdown_artifact_ref")
        )
        if artifact_ref:
            if artifact_reader is None:
                raise ValueError(
                    "storyBreakdownArtifactRef was provided, but this worker "
                    "has no artifact reader configured."
                )
            artifact_payload = artifact_reader(artifact_ref)
            if inspect.isawaitable(artifact_payload):
                artifact_payload = await artifact_payload  # type: ignore[assignment]
            parsed_payload = _parse_story_breakdown_payload(artifact_payload)
            breakdown_source_path = _breakdown_source_path(parsed_payload)
            breakdown_source_document_class = _breakdown_source_document_class(
                parsed_payload
            )
            artifact_payload_failure_reason = _story_breakdown_failure_reason(
                parsed_payload
            )
            stories = _coerce_story_payload(parsed_payload)
            artifact_ref_was_read = True
            artifact_payload_had_explicit_empty_stories = (
                _has_explicit_empty_story_list(parsed_payload)
            )
    if not stories and artifact_ref_was_read and artifact_payload_failure_reason:
        raise ValueError(artifact_payload_failure_reason)
    if (
        not stories
        and artifact_ref_was_read
        and artifact_payload_had_explicit_empty_stories
    ):
        return _github_noop_result(
            original_story_count=0,
            story_breakdown_artifact_ref=artifact_ref,
            story_breakdown_path=_string(
                inputs.get("storyBreakdownPath")
                or story_output.get("storyBreakdownPath")
                or previous_outputs.get("storyBreakdownPath")
                or previous_story_output.get("storyBreakdownPath")
            ),
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
            or previous_outputs.get("storyBreakdownPath")
            or previous_story_output.get("storyBreakdownPath")
        )
        if repo and ref and path:
            fetched = story_fetcher(repo, ref, path)
            if inspect.isawaitable(fetched):
                fetched = await fetched  # type: ignore[assignment]
            fetched_payload = _parse_story_breakdown_payload(fetched)
            breakdown_source_path = _breakdown_source_path(fetched_payload)
            breakdown_source_document_class = _breakdown_source_document_class(
                fetched_payload
            )
            stories = _coerce_story_payload(fetched_payload)
    if not stories:
        raise ValueError("No stories were available for GitHub issue creation.")

    original_story_count = len(stories)
    (
        stories,
        skipped_stories,
        blocked_stories,
        partial_stories_adjusted,
    ) = _reconcile_stories_for_issue_creation(stories)
    if not stories:
        return _github_noop_result(
            original_story_count=original_story_count,
            skipped_stories=skipped_stories,
            blocked_stories=blocked_stories,
            partial_stories_adjusted=partial_stories_adjusted,
        )

    missing_claim_ids = _missing_source_claim_story_ids(
        stories,
        fallback_path=breakdown_source_path,
        source_document_class=breakdown_source_document_class,
    )
    if missing_claim_ids:
        raise ValueError(
            "GitHub story creation requires sourceReference.claimIds for every "
            "canonical declarative story with sourceReference.path or breakdown "
            "source.referencePath. Missing: "
            + ", ".join(missing_claim_ids)
        )

    service = github_service_factory()
    marker_label = _workflow_marker_label(inputs=inputs, context=_context)
    created: list[dict[str, Any]] = []
    issue_mappings: list[dict[str, Any]] = []
    for index, story in enumerate(stories, start=1):
        summary = _story_summary(story, index=index)
        result = await service.create_issue(
            repo=repository,
            title=summary,
            body=_story_description_with_source(
                story,
                fallback_source_path=breakdown_source_path,
            ),
            labels=_github_labels(
                story=story,
                github_payload=github_payload,
                marker_label=marker_label,
            ),
            github_token=None,
        )
        issue_result = (
            result.model_dump(by_alias=True)
            if hasattr(result, "model_dump")
            else dict(result)
        )
        if not bool(issue_result.get("created")):
            raise ValueError(
                _string(issue_result.get("summary"))
                or "GitHub issue creation failed."
            )
        created.append(issue_result)
        issue_mappings.append(
            _github_issue_mapping(
                story=story,
                issue=issue_result,
                repository=repository,
                index=index,
                summary=summary,
                fallback_source_path=breakdown_source_path,
            )
        )

    partial = bool(blocked_stories)
    story_status = "github_partial" if partial else "github_created"
    return ToolResult(
        status="COMPLETED",
        outputs={
            "storyOutput": {
                "mode": "github",
                "status": story_status,
                "storyCount": original_story_count,
                "eligibleStoryCount": len(stories),
                "createdCount": len(created),
                "dependencyMode": "none",
                "dependencyCount": 0,
                "skippedStories": skipped_stories,
                "blockedStories": blocked_stories,
                "partialStoriesAdjusted": partial_stories_adjusted,
            },
            "github": {
                "repository": repository,
                "createdCount": len(created),
                "createdIssues": created,
                "dependencyMode": "none",
                "dependencyCount": 0,
                "issueMappings": issue_mappings,
                "skippedStories": skipped_stories,
                "blockedStories": blocked_stories,
                "partialStoriesAdjusted": partial_stories_adjusted,
                **({"partial": True} if partial else {}),
            },
        },
    )


def _blocker_preflight_target_issue_key(inputs: Mapping[str, Any]) -> str:
    nested = _mapping(
        inputs.get("blockerPreflight")
        or inputs.get("blocker_preflight")
        or inputs.get("jira")
    )
    return _string(
        inputs.get("targetIssueKey")
        or inputs.get("target_issue_key")
        or inputs.get("issueKey")
        or inputs.get("issue_key")
        or inputs.get("jiraIssueKey")
        or inputs.get("jira_issue_key")
        or nested.get("targetIssueKey")
        or nested.get("target_issue_key")
        or nested.get("issueKey")
        or nested.get("issue_key")
    ).upper()

def _blocker_preflight_link_type(inputs: Mapping[str, Any]) -> str:
    nested = _mapping(
        inputs.get("blockerPreflight")
        or inputs.get("blocker_preflight")
        or inputs.get("jira")
    )
    return _string(
        inputs.get("linkType")
        or inputs.get("link_type")
        or nested.get("linkType")
        or nested.get("link_type")
        or "Blocks"
    )

def _blocked_summary(
    *,
    target_issue_key: str,
    unresolved: Sequence[Mapping[str, Any]],
) -> str:
    if not unresolved:
        return (
            f"Could not determine Jira blockers for {target_issue_key} from "
            "trusted Jira data."
        )
    formatted = []
    for item in unresolved:
        issue_key = _string(item.get("issueKey"))
        status = _string(item.get("status")) or "unknown"
        formatted.append(f"{issue_key} ({status})" if issue_key else status)
    joined = ", ".join(formatted)
    return f"{target_issue_key} is blocked by unresolved Jira issue(s): {joined}."

async def check_jira_blockers(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    jira_service_factory: JiraServiceFactory = JiraToolService,
) -> ToolResult:
    """Deterministically stop Jira Orchestrate only for true inbound blockers."""

    target_issue_key = _blocker_preflight_target_issue_key(inputs)
    link_type = _blocker_preflight_link_type(inputs)
    if not target_issue_key:
        raise ValueError("targetIssueKey is required for Jira blocker preflight.")

    assessment_verdict, _ = await _resolve_jira_assessment_verdict(inputs, _context)
    assessment_ref = _assessment_artifact_ref(inputs, _context)
    assessment_output: dict[str, Any] = {}
    if assessment_verdict:
        assessment_output["assessmentVerdict"] = assessment_verdict
    # Carry the durable ref forward so the next step (In Progress transition)
    # can resolve the verdict by ref even though this step sits between it and
    # the assessment agent step.
    if assessment_ref:
        assessment_output["assessmentArtifactRef"] = assessment_ref

    service = jira_service_factory()
    try:
        target_issue = await service.get_issue(
            GetIssueRequest(
                issueKey=target_issue_key,
                fields=["status", "issuelinks"],
            )
        )
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        summary = (
            f"Could not determine Jira blockers for {target_issue_key} through "
            f"trusted Jira data ({code})."
        )
        return ToolResult(
            status="COMPLETED",
            outputs={
                "targetIssueKey": target_issue_key,
                "decision": "blocked",
                "blockingIssues": [],
                "summary": summary,
                **assessment_output,
            },
        )

    target_mapping = _mapping(target_issue)
    blockers: list[dict[str, Any]] = []
    for link in _issue_links(target_mapping):
        blocker_issue = _blocking_issue_from_link(
            link,
            target_issue_key=target_issue_key,
            link_type_name=link_type,
        )
        if blocker_issue is None:
            continue
        blocker_key = _issue_key(blocker_issue)
        if not blocker_key:
            blockers.append(
                {
                    "issueKey": "",
                    "status": "unknown",
                    "statusKnown": False,
                    "linkType": link_type,
                    "relationship": "blocks",
                }
            )
            continue
        if not _status_name(blocker_issue):
            try:
                fetched = await service.get_issue(
                    GetIssueRequest(issueKey=blocker_key, fields=["status"])
                )
                if isinstance(fetched, Mapping):
                    blocker_issue = dict(fetched)
            except Exception:
                blocker_issue = {"key": blocker_key}
        status = _status_name(blocker_issue)
        blockers.append(
            {
                "issueKey": blocker_key,
                "status": status or "unknown",
                "statusKnown": bool(status),
                "linkType": link_type,
                "relationship": "blocks",
                "done": _status_is_done(blocker_issue),
            }
        )

    unresolved = [
        item
        for item in blockers
        if not bool(item.get("statusKnown")) or not bool(item.get("done"))
    ]
    if unresolved:
        return ToolResult(
            status="COMPLETED",
            outputs={
                "targetIssueKey": target_issue_key,
                "decision": "blocked",
                "blockingIssues": unresolved,
                "resolvedBlockingIssues": [
                    item for item in blockers if bool(item.get("done"))
                ],
                "summary": _blocked_summary(
                    target_issue_key=target_issue_key,
                    unresolved=unresolved,
                ),
                **assessment_output,
            },
        )

    summary = (
        f"{target_issue_key} has no Jira blocker links."
        if not blockers
        else f"All Jira blockers for {target_issue_key} are Done."
    )
    return ToolResult(
        status="COMPLETED",
        outputs={
            "targetIssueKey": target_issue_key,
            "decision": "continue",
            "blockingIssues": [],
            "resolvedBlockingIssues": blockers,
            "summary": summary,
            **assessment_output,
        },
    )

def _load_preset_brief_issue_key(inputs: Mapping[str, Any]) -> str:
    nested = _mapping(inputs.get("jira") or inputs.get("issue"))
    return _string(
        inputs.get("issueKey")
        or inputs.get("issue_key")
        or inputs.get("jiraIssueKey")
        or inputs.get("jira_issue_key")
        or nested.get("issueKey")
        or nested.get("issue_key")
        or nested.get("key")
    ).upper()


def _source_resolution_root(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> Path | None:
    context_mapping = _mapping(context)
    root = _string(
        inputs.get("repositoryRoot")
        or inputs.get("repository_root")
        or inputs.get("repoRoot")
        or inputs.get("repo_root")
        or context_mapping.get("repositoryRoot")
        or context_mapping.get("repository_root")
        or context_mapping.get("repoRoot")
        or context_mapping.get("repo_root")
        or context_mapping.get("workspacePath")
        or context_mapping.get("workspace_path")
    )
    return Path(root) if root else None


def _normalize_source_document_path(value: object) -> str:
    candidate = _string(value).strip()
    if not candidate:
        return ""
    candidate = candidate.replace("\\", "/")
    candidate = candidate.strip("`'\"<>()[]{}")
    candidate = candidate.rstrip(".,;:!?")
    while candidate.startswith("./"):
        candidate = candidate[2:]

    path = PurePosixPath(candidate)
    if path.is_absolute() or not path.parts:
        return ""
    if any(part in {"", ".", ".."} for part in path.parts):
        return ""
    normalized = path.as_posix()
    if normalized == "AGENTS.md":
        return normalized
    if normalized.startswith("docs/") and normalized.endswith(".md"):
        return normalized
    return ""


def _source_document_path_exists(
    repo_root: Path | None,
    source_path: str,
) -> bool | None:
    if repo_root is None:
        return None
    if not source_path:
        return False
    try:
        path = repo_root.joinpath(*PurePosixPath(source_path).parts)
        return path.is_file()
    except (OSError, ValueError):
        return False


def _source_path_candidates(
    *,
    texts: Sequence[tuple[str, str]],
    repo_root: Path | None,
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for source_field, text in texts:
        if not text:
            continue
        for match in _SOURCE_DOCUMENT_PATH_RE.finditer(text):
            source_path = _normalize_source_document_path(match.group("path"))
            if not source_path:
                continue
            candidate = candidates.setdefault(
                source_path,
                {
                    "path": source_path,
                    "sourceField": source_field,
                    "sourceFields": [],
                    "exists": _source_document_path_exists(repo_root, source_path),
                },
            )
            source_fields = candidate["sourceFields"]
            if source_field not in source_fields:
                source_fields.append(source_field)
    return list(candidates.values())


def _resolve_source_design_path_from_issue(
    *,
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    summary: str,
    description_text: str,
    acceptance_text: str,
    preset_brief: str,
) -> dict[str, Any]:
    repo_root = _source_resolution_root(inputs, context)
    explicit_path = _normalize_source_document_path(
        inputs.get("sourceDesignPath")
        or inputs.get("source_design_path")
        or inputs.get("declarativeDocumentPath")
        or inputs.get("declarative_document_path")
    )
    if explicit_path:
        exists = _source_document_path_exists(repo_root, explicit_path)
        resolved = exists is not False
        return {
            "status": "resolved" if resolved else "invalid_explicit_path",
            "selectedPath": explicit_path if resolved else "",
            "candidatePaths": [
                {
                    "path": explicit_path,
                    "sourceField": "input.source_design_path",
                    "sourceFields": ["input.source_design_path"],
                    "exists": exists,
                }
            ],
            "reason": (
                "explicit source_design_path exists in the repository checkout"
                if exists is True
                else (
                    "explicit source_design_path selected without repository-root "
                    "validation"
                    if exists is None
                    else "explicit source_design_path was not found in the repository checkout"
                )
            ),
        }

    candidate_paths = _source_path_candidates(
        texts=(
            ("jira.summary", summary),
            ("jira.description", description_text),
            ("jira.acceptanceCriteria", acceptance_text),
            ("jira.presetBrief", preset_brief),
        ),
        repo_root=repo_root,
    )
    existing_paths = [item for item in candidate_paths if item["exists"] is True]
    if len(existing_paths) == 1 or (
        repo_root is None and len(candidate_paths) == 1
    ):
        selected = existing_paths[0] if existing_paths else candidate_paths[0]
        return {
            "status": "resolved",
            "selectedPath": selected["path"],
            "candidatePaths": candidate_paths,
            "reason": (
                f"found one existing canonical document path in "
                f"{selected['sourceField']}"
                if selected["exists"] is True
                else (
                    f"found one canonical document path in "
                    f"{selected['sourceField']}; repository-root validation not run"
                )
            ),
        }
    if len(existing_paths) > 1:
        return {
            "status": "ambiguous",
            "selectedPath": "",
            "candidatePaths": candidate_paths,
            "reason": "multiple existing canonical document paths were found",
        }
    if candidate_paths:
        return {
            "status": "invalid_candidates",
            "selectedPath": "",
            "candidatePaths": candidate_paths,
            "reason": (
                "canonical document path candidates were mentioned, but none "
                "exist in the repository checkout"
            ),
        }
    return {
        "status": "not_found",
        "selectedPath": "",
        "candidatePaths": [],
        "reason": "no canonical document path was found in the trusted Jira issue",
    }


async def load_jira_preset_brief(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    jira_service_factory: JiraServiceFactory = JiraToolService,
) -> ToolResult:
    """Load a compact Jira preset brief through MoonMind's trusted Jira service."""

    issue_key = _load_preset_brief_issue_key(inputs)
    if not issue_key:
        raise ValueError("issueKey is required for Jira preset brief loading.")

    service = jira_service_factory()
    try:
        issue_payload = await service.get_issue(
            GetIssueRequest(issueKey=issue_key, expand=["names"])
        )
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        return ToolResult(
            status="FAILED",
            outputs={
                "error": (
                    f"Could not load Jira preset brief for {issue_key} through "
                    f"trusted Jira data ({code})."
                ),
                "jiraIssueKey": issue_key,
            },
        )

    issue = _mapping(issue_payload)
    fields = _mapping(issue.get("fields"))
    names = _mapping(issue.get("names"))
    summary = _string(fields.get("summary"))
    description_text = _normalize_jira_text(fields.get("description"))
    acceptance_text = _extract_acceptance_criteria(fields, names)
    if not acceptance_text:
        description_text, acceptance_text = _split_description_acceptance(description_text)
    status = _mapping(fields.get("status"))
    issue_type = _mapping(fields.get("issuetype"))
    assignee = _mapping(fields.get("assignee"))
    resolved_key = _string(issue.get("key")).upper() or issue_key

    preset_parts = [f"{resolved_key}: {summary}".strip()]
    if description_text:
        preset_parts.append(description_text)
    if acceptance_text:
        preset_parts.append(f"Acceptance criteria\n{acceptance_text}")
    preset_brief = "\n\n".join(part for part in preset_parts if part)
    step_parts = [f"Complete Jira issue {resolved_key}: {summary}".strip()]
    if description_text:
        step_parts.append(f"Description\n{description_text}")
    if acceptance_text:
        step_parts.append(f"Acceptance criteria\n{acceptance_text}")
    step_instructions = "\n\n".join(part for part in step_parts if part)
    source_resolution = _resolve_source_design_path_from_issue(
        inputs=inputs,
        context=_context,
        summary=summary,
        description_text=description_text,
        acceptance_text=acceptance_text,
        preset_brief=preset_brief,
    )
    resolved_source_path = _string(source_resolution.get("selectedPath"))
    artifact_path = _first_string(
        inputs.get("artifactPath"),
        inputs.get("artifact_path"),
        inputs.get("briefArtifactPath"),
        inputs.get("brief_artifact_path"),
        "artifacts/jira-implement-brief.json",
    )

    summary_text = f"Loaded Jira preset brief for {resolved_key} from trusted Jira data."
    if resolved_source_path:
        summary_text = (
            f"{summary_text} Resolved source design path {resolved_source_path}."
        )

    outputs: dict[str, Any] = {
        "trustedSource": "moonmind.jira.get_issue",
        "jiraIssueKey": resolved_key,
        "jiraPresetBrief": preset_brief,
        "presetBrief": preset_brief,
        "jiraStepInstructions": step_instructions,
        "artifactPath": artifact_path,
        "sourceResolution": source_resolution,
        "jiraIssue": {
            "key": resolved_key,
            "summary": summary,
            "descriptionText": description_text,
            "acceptanceCriteriaText": acceptance_text,
            "status": _string(status.get("name")),
            "issueType": _string(issue_type.get("name")),
            "assignee": _string(assignee.get("displayName")),
            "url": _issue_url(issue),
        },
        "summary": summary_text,
    }
    if resolved_source_path:
        outputs["resolvedSourceDesignPath"] = resolved_source_path

    return ToolResult(status="COMPLETED", outputs=outputs)


def _jira_update_issue_key(inputs: Mapping[str, Any]) -> str:
    return _load_preset_brief_issue_key(inputs)


def _jira_status_mode(inputs: Mapping[str, Any]) -> str:
    mode = _string(inputs.get("mode")).lower().replace("-", "_").replace(" ", "_")
    if mode:
        return mode
    target = _string(
        inputs.get("targetStatus")
        or inputs.get("target_status")
        or inputs.get("statusName")
        or inputs.get("status_name")
    )
    return target.lower().replace("-", "_").replace(" ", "_") if target else "start"


def _jira_target_status(inputs: Mapping[str, Any]) -> str:
    explicit = _first_string(
        inputs.get("targetStatus"),
        inputs.get("target_status"),
        inputs.get("statusName"),
        inputs.get("status_name"),
    )
    if explicit:
        return explicit
    mode = _jira_status_mode(inputs)
    mapped = {
        "start": "In Progress",
        "in_progress": "In Progress",
        "review": "Review",
        "code_review": "Review",
        "done": "Done",
    }.get(mode)
    if mapped:
        return mapped
    return mode.replace("_", " ").title() if mode else ""


def _jira_status_match_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _string(value).lower())


def _jira_transition_candidate(transition: Mapping[str, Any]) -> dict[str, Any]:
    to_status = _mapping(transition.get("to"))
    category = _mapping(to_status.get("statusCategory") or to_status.get("category"))
    return {
        "transitionId": _string(transition.get("id") or transition.get("transitionId")),
        "transitionName": _string(transition.get("name")),
        "toStatusName": _string(to_status.get("name")),
        "toStatusId": _string(to_status.get("id")),
        "toStatusCategory": _string(category.get("key") or category.get("name")),
    }


def _find_jira_status_transition(
    transitions: Sequence[Any],
    *,
    target_status: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    target_token = _jira_status_match_token(target_status)
    candidates: list[dict[str, Any]] = []
    for item in transitions:
        if not isinstance(item, Mapping):
            continue
        candidate = _jira_transition_candidate(item)
        if not candidate["transitionId"]:
            continue
        candidates.append(candidate)
        tokens = {
            _jira_status_match_token(candidate["toStatusName"]),
            _jira_status_match_token(candidate["transitionName"]),
        }
        if target_token and target_token in tokens:
            return candidate, candidates
    return None, candidates


def _jira_current_status_output(issue: Mapping[str, Any]) -> dict[str, Any]:
    status = _status_payload(issue)
    category = _mapping(status.get("statusCategory") or status.get("category"))
    return {
        "name": _string(status.get("name")),
        "id": _string(status.get("id")),
        "category": _string(category.get("key") or category.get("name")),
    }


async def update_jira_issue_status(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    jira_service_factory: JiraServiceFactory = JiraToolService,
) -> ToolResult:
    issue_key = _jira_update_issue_key(inputs)
    target_status = _jira_target_status(inputs)
    mode = _jira_status_mode(inputs)
    if not issue_key:
        raise ValueError("issueKey is required for Jira issue status updates.")
    if not target_status:
        raise ValueError("targetStatus is required for Jira issue status updates.")

    if mode in {"start", "in_progress"} or (
        _jira_status_match_token(target_status) == "inprogress"
    ):
        if inputs.get("assessmentArtifactPath") or inputs.get("assessment_artifact_path"):
            assessment_verdict, assessment_available = (
                await _resolve_jira_assessment_verdict(inputs, _context)
            )
            if not assessment_available:
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "issueKey": issue_key,
                        "targetStatus": target_status,
                        "decision": "blocked",
                        "summary": "Jira In Progress update requires an assessment verdict, but it was unavailable.",
                    },
                )
            if assessment_verdict == "FULLY_IMPLEMENTED":
                return ToolResult(
                    status="COMPLETED",
                    outputs={
                        "issueKey": issue_key,
                        "targetStatus": target_status,
                        "decision": "skipped",
                        "assessmentVerdict": assessment_verdict,
                        "summary": f"Skipped Jira In Progress update for {issue_key} because assessment verdict is FULLY_IMPLEMENTED.",
                    },
                )
            if assessment_verdict == "BLOCKED":
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "issueKey": issue_key,
                        "targetStatus": target_status,
                        "decision": "blocked",
                        "assessmentVerdict": assessment_verdict,
                        "summary": f"Skipped Jira In Progress update for {issue_key} because assessment verdict is BLOCKED.",
                    },
                )

    service = jira_service_factory()
    try:
        issue = await service.get_issue(
            GetIssueRequest(
                issueKey=issue_key,
                fields=["status", "summary", "issuetype"],
            )
        )
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        return ToolResult(
            status="FAILED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "decision": "blocked",
                "summary": f"Could not fetch Jira issue {issue_key} before status update ({code}).",
            },
        )

    issue_mapping = _mapping(issue)
    current_status = _jira_current_status_output(issue_mapping)
    if _jira_status_match_token(current_status.get("name")) == _jira_status_match_token(
        target_status
    ):
        return ToolResult(
            status="COMPLETED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "confirmedStatus": current_status,
                "decision": "already_satisfied",
                "transitioned": False,
                "summary": f"Jira issue {issue_key} is already in status {current_status['name']}.",
            },
        )

    try:
        transition_payload = await service.get_transitions(
            GetTransitionsRequest(issueKey=issue_key, expandFields=True)
        )
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        return ToolResult(
            status="FAILED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "decision": "blocked",
                "summary": f"Could not list Jira transitions for {issue_key} ({code}).",
            },
        )

    transitions = _list(_mapping(transition_payload).get("transitions"))
    selected_transition, candidates = _find_jira_status_transition(
        transitions,
        target_status=target_status,
    )
    if selected_transition is None:
        return ToolResult(
            status="FAILED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "decision": "blocked",
                "availableTransitions": candidates,
                "summary": f"No Jira transition for {issue_key} matched target status {target_status}.",
            },
        )

    try:
        await service.transition_issue(
            TransitionIssueRequest(
                issueKey=issue_key,
                transitionId=selected_transition["transitionId"],
                fields=_mapping(inputs.get("fields")),
                update=_mapping(inputs.get("update")),
            )
        )
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        return ToolResult(
            status="FAILED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "selectedTransition": selected_transition,
                "decision": "blocked",
                "summary": f"Jira transition for {issue_key} failed ({code}).",
            },
        )

    try:
        confirmed_issue = await service.get_issue(
            GetIssueRequest(
                issueKey=issue_key,
                fields=["status", "summary", "issuetype"],
            )
        )
        confirmed_status = _jira_current_status_output(_mapping(confirmed_issue))
    except Exception as exc:
        code = _string(getattr(exc, "code", "")) or exc.__class__.__name__
        return ToolResult(
            status="COMPLETED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "selectedTransition": selected_transition,
                "decision": "transitioned_unverified",
                "transitioned": True,
                "transitionId": selected_transition["transitionId"],
                "summary": f"Transitioned Jira issue {issue_key} toward {target_status}, but confirmation fetch failed ({code}).",
            },
        )

    if _jira_status_match_token(confirmed_status.get("name")) != _jira_status_match_token(
        target_status
    ):
        return ToolResult(
            status="FAILED",
            outputs={
                "issueKey": issue_key,
                "targetStatus": target_status,
                "currentStatus": current_status,
                "confirmedStatus": confirmed_status,
                "selectedTransition": selected_transition,
                "decision": "blocked",
                "transitioned": True,
                "transitionId": selected_transition["transitionId"],
                "summary": f"Jira issue {issue_key} transition completed, but confirmed status is {confirmed_status.get('name') or 'unknown'} instead of {target_status}.",
            },
        )

    return ToolResult(
        status="COMPLETED",
        outputs={
            "issueKey": issue_key,
            "targetStatus": target_status,
            "currentStatus": current_status,
            "confirmedStatus": confirmed_status,
            "selectedTransition": selected_transition,
            "decision": "transitioned",
            "transitioned": True,
            "transitionId": selected_transition["transitionId"],
            "transitionName": selected_transition["transitionName"],
            "summary": f"Transitioned Jira issue {issue_key} to {confirmed_status['name']}.",
        },
    )


def _github_issue_inputs(inputs: Mapping[str, Any]) -> tuple[str, int]:
    nested = _mapping(inputs.get("github") or inputs.get("issue"))
    repository = _string(
        inputs.get("repository")
        or inputs.get("repo")
        or nested.get("repository")
        or nested.get("repo")
    )
    issue_number_raw = (
        inputs.get("issueNumber")
        or inputs.get("issue_number")
        or inputs.get("number")
        or nested.get("issueNumber")
        or nested.get("issue_number")
        or nested.get("number")
    )
    try:
        issue_number = int(str(issue_number_raw).strip())
    except (TypeError, ValueError):
        issue_number = 0
    if not repository or issue_number <= 0:
        raise ValueError("repository and issueNumber are required for GitHub issue tools.")
    return repository, issue_number


def _github_issue_payload(data: Mapping[str, Any], repository: str) -> dict[str, Any]:
    labels = data.get("labels")
    normalized_labels: list[str] = []
    if isinstance(labels, Sequence) and not isinstance(labels, (str, bytes, bytearray)):
        for item in labels:
            if isinstance(item, Mapping):
                label = _string(item.get("name"))
            else:
                label = _string(item)
            if label:
                normalized_labels.append(label)
    number_raw = data.get("number")
    try:
        number = int(str(number_raw).strip())
    except (TypeError, ValueError):
        number = 0
    return {
        "repository": repository,
        "number": number,
        "title": _string(data.get("title")),
        "body": _string(data.get("body")),
        "url": _string(data.get("html_url") or data.get("url")),
        "state": _string(data.get("state")),
        "labels": normalized_labels,
    }


async def _fetch_github_issue(
    *,
    repository: str,
    issue_number: int,
    github_service_factory: Callable[[], GitHubService] = GitHubService,
) -> tuple[dict[str, Any] | None, str | None]:
    service = github_service_factory()
    token, resolution_error = await service.resolve_github_token(repo=repository)
    if not token:
        return None, resolution_error or "GitHub issue lookup is unavailable."
    headers = service._github_headers(token)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues/{issue_number}",
                headers=headers,
            )
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as exc:
            summary = service._github_permission_summary(exc.response)
            return None, (
                f"GitHub issue fetch failed with HTTP {exc.response.status_code}"
                + (f" for {repository}#{issue_number}. {summary}" if summary else f" for {repository}#{issue_number}.")
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return None, f"GitHub issue fetch failed: {exc.__class__.__name__}"


async def load_github_issue_preset_brief(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    github_service_factory: Callable[[], GitHubService] = GitHubService,
) -> ToolResult:
    """Load a compact GitHub issue preset brief through trusted GitHub data."""

    repository, issue_number = _github_issue_inputs(inputs)
    issue_data, error = await _fetch_github_issue(
        repository=repository,
        issue_number=issue_number,
        github_service_factory=github_service_factory,
    )
    if issue_data is None:
        return ToolResult(
            status="FAILED",
            outputs={
                "error": error or "Could not load GitHub issue preset brief.",
                "repository": repository,
                "issueNumber": issue_number,
            },
        )
    issue = _github_issue_payload(issue_data, repository)
    issue_ref = f"{repository}#{issue['number'] or issue_number}"
    body = _string(issue.get("body"))
    title = _string(issue.get("title"))
    labels = issue.get("labels") if isinstance(issue.get("labels"), list) else []
    brief_parts = [f"{issue_ref}: {title}".strip()]
    if body:
        brief_parts.append(body)
    if labels:
        brief_parts.append("Labels: " + ", ".join(str(label) for label in labels))
    preset_brief = "\n\n".join(part for part in brief_parts if part)
    artifact_path = _first_string(
        inputs.get("artifactPath"),
        inputs.get("artifact_path"),
        inputs.get("briefArtifactPath"),
        inputs.get("brief_artifact_path"),
        "artifacts/github-issue-implement-brief.json",
    )
    return ToolResult(
        status="COMPLETED",
        outputs={
            "trustedSource": "moonmind.github.get_issue",
            "issue": issue,
            "presetBrief": preset_brief,
            "artifactPath": artifact_path,
            "summary": f"Loaded GitHub issue preset brief for {issue_ref} from trusted GitHub data.",
        },
    )


def _github_blockers_from_issue(issue: Mapping[str, Any]) -> list[dict[str, Any]]:
    labels = [str(label).strip().lower() for label in issue.get("labels") or []]
    blockers: list[dict[str, Any]] = []
    for label in labels:
        if label in {"blocked", "status: blocked", "status/blocked"} or label.startswith("blocked:"):
            blockers.append({"source": "label", "label": label, "statusKnown": False, "done": False})
    body = _string(issue.get("body"))
    match = re.search(r"(?im)^#+\s*block(?:ed|ers|ing)\b(?P<section>.*?)(?:^#+\s|\Z)", body, flags=re.DOTALL)
    if match:
        section = match.group("section").strip()
        if section and not re.search(r"(?i)\b(none|n/a|no blockers?)\b", section):
            blockers.append({"source": "body", "section": section[:1000], "statusKnown": False, "done": False})
    return blockers


async def check_github_issue_blockers(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    github_service_factory: Callable[[], GitHubService] = GitHubService,
) -> ToolResult:
    repository, issue_number = _github_issue_inputs(inputs)
    issue_data, error = await _fetch_github_issue(
        repository=repository,
        issue_number=issue_number,
        github_service_factory=github_service_factory,
    )
    issue_ref = f"{repository}#{issue_number}"
    # Carry the assessment verdict + durable ref forward so the In Progress step
    # (which sits after this blocker step) can resolve the verdict by ref without
    # sharing the assessment agent's filesystem.
    assessment_verdict, _ = await _augment_assessment_verdict_with_ref(
        _assessment_verdict_from_artifact(inputs, _context),
        inputs,
        _context,
    )
    assessment_ref = _assessment_artifact_ref(inputs, _context)
    assessment_output: dict[str, Any] = {}
    if assessment_verdict:
        assessment_output["assessmentVerdict"] = assessment_verdict
    if assessment_ref:
        assessment_output["assessmentArtifactRef"] = assessment_ref
    if issue_data is None:
        return ToolResult(
            status="FAILED",
            outputs={"issueRef": issue_ref, "decision": "blocked", "summary": error or "GitHub blocker check failed.", **assessment_output},
        )
    issue = _github_issue_payload(issue_data, repository)
    blockers = _github_blockers_from_issue(issue)
    if blockers:
        return ToolResult(
            status="COMPLETED",
            outputs={
                "issueRef": issue_ref,
                "decision": "blocked",
                "blockingIssues": blockers,
                "summary": f"GitHub issue {issue_ref} has unresolved blocker evidence.",
                **assessment_output,
            },
        )
    return ToolResult(
        status="COMPLETED",
        outputs={
            "issueRef": issue_ref,
            "decision": "continue",
            "blockingIssues": [],
            "summary": f"GitHub issue {issue_ref} has no configured blocker evidence.",
            **assessment_output,
        },
    )


_GITHUB_STATUS_ACTIONS = {
    "start": {"labelsToAdd": ["status: in-progress"], "labelsToRemove": ["status: todo"], "comment": True},
    "in_progress": {"labelsToAdd": ["status: in-progress"], "labelsToRemove": ["status: todo"], "comment": True},
    "code_review": {"labelsToAdd": ["status: code-review"], "labelsToRemove": ["status: in-progress"], "commentPullRequestUrl": True},
    "done": {"labelsToAdd": ["status: done"], "labelsToRemove": ["status: code-review"], "closeIssue": True},
}


def _github_status_mode(inputs: Mapping[str, Any]) -> str:
    mode = _string(inputs.get("mode")).lower().replace(" ", "_")
    if mode:
        return mode
    target = _string(inputs.get("targetStatus")).lower().replace(" ", "_")
    return target or "start"


def _pull_request_url_from_artifact_path(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    artifact_path = _string(
        inputs.get("pullRequestArtifactPath")
        or inputs.get("pull_request_artifact_path")
    )
    if not artifact_path:
        return ""
    raw_path = Path(artifact_path).expanduser()
    candidate_paths: list[Path] = []
    if raw_path.is_absolute():
        candidate_paths.append(raw_path.resolve())
    else:
        for root in _repo_root_candidates(inputs, context):
            candidate = (root / raw_path).resolve()
            if candidate.is_relative_to(root):
                candidate_paths.append(candidate)
    for candidate in candidate_paths:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        nested_pull_request = _mapping(payload.get("pullRequest") or payload.get("pull_request"))
        return _first_string(
            payload.get("pullRequestUrl"),
            payload.get("pull_request_url"),
            payload.get("prUrl"),
            payload.get("url"),
            nested_pull_request.get("url"),
        )
    return ""


def _local_json_artifact_from_path(
    *,
    artifact_path: str,
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    raw_path = Path(artifact_path).expanduser()
    candidate_paths: list[Path] = []
    if raw_path.is_absolute():
        candidate_paths.append(raw_path.resolve())
    else:
        for root in _repo_root_candidates(inputs, context):
            candidate = (root / raw_path).resolve()
            if candidate.is_relative_to(root):
                candidate_paths.append(candidate)
    for candidate in candidate_paths:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            return dict(payload)
    return None


def _assessment_verdict_from_artifact(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, bool]:
    artifact_path = _string(
        inputs.get("assessmentArtifactPath")
        or inputs.get("assessment_artifact_path")
    )
    if not artifact_path:
        return "", True
    payload = _local_json_artifact_from_path(
        artifact_path=artifact_path,
        inputs=inputs,
        context=context,
    )
    if payload is None:
        return "", False
    verdict = _string(payload.get("verdict")).upper()
    return verdict, True


_ASSESSMENT_VERDICTS = frozenset(
    {"FULLY_IMPLEMENTED", "PARTIALLY_IMPLEMENTED", "NOT_IMPLEMENTED", "BLOCKED"}
)


def _normalize_assessment_verdict(value: Any) -> str:
    verdict = _string(value).upper()
    return verdict if verdict in _ASSESSMENT_VERDICTS else ""


def _assessment_verdict_from_mapping(payload: Mapping[str, Any]) -> str:
    for key in (
        "assessmentVerdict",
        "assessment_verdict",
        "jiraAssessmentVerdict",
        "jira_assessment_verdict",
        "initialAssessmentVerdict",
        "initial_assessment_verdict",
        "verdict",
    ):
        verdict = _normalize_assessment_verdict(payload.get(key))
        if verdict:
            return verdict
    for key in (
        "assessment",
        "jiraAssessment",
        "jira_assessment",
        "jiraImplementAssessment",
        "jira_implement_assessment",
    ):
        nested = _mapping(payload.get(key))
        verdict = _assessment_verdict_from_mapping(nested) if nested else ""
        if verdict:
            return verdict
    return ""


def _assessment_verdict_from_text(value: Any) -> str:
    text = _string(value)
    if not text:
        return ""
    verdict_pattern = (
        r"(FULLY_IMPLEMENTED|PARTIALLY_IMPLEMENTED|NOT_IMPLEMENTED|BLOCKED)"
    )
    verdict_prefix = r"[\s:`*_\"']*"
    verdict_suffix = r"(?:_+(?!\w)|(?![-\w]))"
    assessment_separator = r"[\s:.,;!?\-\u2010-\u2015`*_\"'\[\]\(\)]*"
    issue_ref_pattern = r"`?[A-Z][A-Z0-9]+-\d+`?"
    patterns = (
        r"(?im)^\s*#{1,6}\s*verdict\s*[:\-]\s*"
        rf"{verdict_prefix}{verdict_pattern}{verdict_suffix}",
        r"(?im)^\s*verdict\s*[:\-]\s*"
        rf"{verdict_prefix}{verdict_pattern}{verdict_suffix}",
        r"(?is)\bassessment\s+complete\b"
        rf"{assessment_separator}"
        rf"(?:(?:for|on)\b{assessment_separator}"
        rf"{issue_ref_pattern}{assessment_separator})?"
        rf"(?:{issue_ref_pattern}{assessment_separator})?"
        rf"(?:(?:is|was|has|verdict|status)\b{assessment_separator})?"
        rf"{verdict_pattern}{verdict_suffix}",
        r"(?is)\brecorded\s+verdict\b[^.\n:]*[:\s`]+"
        rf"{verdict_prefix}{verdict_pattern}{verdict_suffix}",
        r"(?is)['\"]verdict['\"]\s*:\s*['\"]"
        rf"{verdict_pattern}['\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_assessment_verdict(match.group(1))
    return ""


def _jira_assessment_verdict(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, bool]:
    artifact_path = _string(
        inputs.get("assessmentArtifactPath")
        or inputs.get("assessment_artifact_path")
    )
    if artifact_path:
        artifact_verdict, artifact_available = _assessment_verdict_from_artifact(
            inputs,
            context,
        )
        if artifact_available and artifact_verdict:
            return artifact_verdict, True

    verdict = _assessment_verdict_from_mapping(inputs)
    if verdict:
        return verdict, True

    previous_outputs = _mapping(
        inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
        or (context or {}).get("previousOutputs")
        or (context or {}).get("previous_outputs")
    )
    verdict = _assessment_verdict_from_mapping(previous_outputs)
    if verdict:
        return verdict, True
    for key in ("lastAssistantText", "assistantText", "summary", "operator_summary"):
        verdict = _assessment_verdict_from_text(previous_outputs.get(key))
        if verdict:
            return verdict, True

    for key in ("lastAssistantText", "assistantText", "summary", "operator_summary"):
        verdict = _assessment_verdict_from_text(inputs.get(key))
        if verdict:
            return verdict, True

    if artifact_path:
        return "", False
    return "", True


def _assessment_artifact_ref(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    """Return the durable artifact ref for the assessment verdict, if present.

    The assessment agent step publishes its structured verdict JSON to the
    MoonMind artifact store and surfaces the ref as ``assessmentArtifactRef``.
    That ref rides ``previousOutputs`` between steps, so downstream deterministic
    tools can read the verdict without sharing the agent's filesystem — the
    bridge-compatible channel that keeps working when agentic compute runs on an
    Omnigent host rather than a co-located worker volume.
    """

    previous_outputs = _mapping(
        inputs.get("previousOutputs")
        or inputs.get("previous_outputs")
        or (context or {}).get("previousOutputs")
        or (context or {}).get("previous_outputs")
    )
    for source in (inputs, previous_outputs, _mapping(context)):
        ref = _first_string(
            source.get("assessmentArtifactRef"),
            source.get("assessment_artifact_ref"),
        )
        if ref:
            return ref
    return ""


async def _read_json_artifact_by_ref(
    ref: str,
    context: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Read a JSON artifact payload by ref using the injected artifact service.

    Returns ``None`` when no service is available (for example a fleet without an
    artifact binding, or unit tests) or the artifact cannot be read/parsed, so
    callers degrade to their other verdict sources instead of failing.
    """

    service = (context or {}).get("temporal_artifact_service")
    if service is None or not ref:
        return None
    principal = (
        _string((context or {}).get("deployment_evidence_principal"))
        or "system:agent_runtime"
    )
    try:
        _artifact, payload = await service.read(
            artifact_id=ref,
            principal=principal,
            allow_restricted_raw=True,
        )
    except Exception:
        return None
    if isinstance(payload, Mapping):
        return payload
    try:
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, Mapping) else None


async def _augment_assessment_verdict_with_ref(
    base: tuple[str, bool],
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, bool]:
    """Fall back to the published assessment artifact ref when no verdict is found.

    The ref path can only UPGRADE a missing verdict to a real one; it never
    downgrades an existing ``(verdict, available)`` result, keeping behavior
    identical for in-flight runs that carry no ref. This is the bridge-compatible
    channel: it resolves via the artifact store, so it works even when the
    assessment ran on an Omnigent host whose workspace the tool cannot mount.
    """

    verdict, available = base
    if verdict:
        return verdict, True
    ref = _assessment_artifact_ref(inputs, context)
    if ref:
        payload = await _read_json_artifact_by_ref(ref, context)
        if payload is None:
            return verdict, False
        if payload is not None:
            ref_verdict = _normalize_assessment_verdict(payload.get("verdict"))
            if ref_verdict:
                return ref_verdict, True
    return verdict, available


async def _resolve_jira_assessment_verdict(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, bool]:
    """Resolve the Jira assessment verdict, preferring durable in-payload sources.

    Tries the synchronous sources first (compact ``assessmentVerdict`` in
    ``previousOutputs``, a locally resolvable handoff file, then free text), then
    the published artifact ref.
    """

    return await _augment_assessment_verdict_with_ref(
        _jira_assessment_verdict(inputs, context), inputs, context
    )


def _verification_verdict_from_payload(payload: Mapping[str, Any]) -> str:
    for key in (
        "verdict",
        "gateVerdict",
        "gate_verdict",
        "moonSpecVerdict",
        "moonspecVerdict",
        "verificationVerdict",
        "verification_verdict",
    ):
        verdict = _string(payload.get(key)).upper()
        if verdict:
            return verdict
    return ""


def _github_status_previous_outputs(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    input_previous_outputs = _mapping(
        inputs.get("previousOutputs") or inputs.get("previous_outputs")
    )
    context_previous_outputs = _mapping(
        (context or {}).get("previousOutputs")
        or (context or {}).get("previous_outputs")
    )
    previous_outputs: dict[str, Any] = {}
    previous_outputs.update(context_previous_outputs)
    previous_outputs.update(input_previous_outputs)
    return previous_outputs


def _github_status_verification_payload_from_previous_outputs(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    previous_outputs = _github_status_previous_outputs(inputs, context)
    candidates = [
        inputs.get("verificationPayload"),
        inputs.get("verification_payload"),
        inputs.get("moonSpecVerify"),
        inputs.get("moonspec_verify"),
        previous_outputs.get("moonSpecVerify"),
        previous_outputs.get("moonspecVerify"),
        previous_outputs.get("moonspec_verify"),
    ]
    metadata = _mapping(previous_outputs.get("metadata"))
    candidates.extend(
        [
            metadata.get("moonSpecVerify"),
            metadata.get("moonspecVerify"),
            metadata.get("moonspec_verify"),
        ]
    )
    for candidate in candidates:
        payload = _mapping(candidate)
        if payload:
            return payload
    return None


def _github_status_verification_ref_from_previous_outputs(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    previous_outputs = _github_status_previous_outputs(inputs, context)
    metadata = _mapping(previous_outputs.get("metadata"))
    return _first_string(
        inputs.get("verificationArtifactRef"),
        inputs.get("verification_artifact_ref"),
        inputs.get("moonSpecVerifyArtifactRef"),
        inputs.get("moonspec_verify_artifact_ref"),
        previous_outputs.get("moonSpecVerifyArtifactRef"),
        previous_outputs.get("moonspecVerifyArtifactRef"),
        previous_outputs.get("moonspec_verify_artifact_ref"),
        previous_outputs.get("gateResultRef"),
        previous_outputs.get("gate_result_ref"),
        metadata.get("moonSpecVerifyArtifactRef"),
        metadata.get("moonspecVerifyArtifactRef"),
        metadata.get("moonspec_verify_artifact_ref"),
        metadata.get("gateResultRef"),
        metadata.get("gate_result_ref"),
    )


def _github_status_verification_verdict(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, bool]:
    artifact_path = _string(
        inputs.get("verificationArtifactPath")
        or inputs.get("verification_artifact_path")
    )
    if artifact_path:
        payload = _local_json_artifact_from_path(
            artifact_path=artifact_path,
            inputs=inputs,
            context=context,
        )
        if payload is not None:
            return _verification_verdict_from_payload(payload), True

    payload = _github_status_verification_payload_from_previous_outputs(
        inputs,
        context,
    )
    if payload is not None:
        return _verification_verdict_from_payload(payload), True

    if _github_status_verification_ref_from_previous_outputs(inputs, context):
        return "", False

    if not artifact_path:
        return "", True
    return "", False


def _github_status_pull_request_url(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> str:
    previous_outputs = _github_status_previous_outputs(inputs, context)
    previous_metadata = _mapping(previous_outputs.get("metadata"))
    publish_context = _mapping(previous_outputs.get("publishContext"))
    snake_publish_context = _mapping(previous_outputs.get("publish_context"))
    return _first_string(
        inputs.get("pullRequestUrl"),
        inputs.get("pull_request_url"),
        previous_outputs.get("pullRequestUrl"),
        previous_outputs.get("pull_request_url"),
        previous_outputs.get("prUrl"),
        previous_outputs.get("pr_url"),
        previous_metadata.get("pullRequestUrl"),
        previous_metadata.get("pull_request_url"),
        previous_metadata.get("prUrl"),
        previous_metadata.get("pr_url"),
        publish_context.get("pullRequestUrl"),
        publish_context.get("pull_request_url"),
        publish_context.get("prUrl"),
        publish_context.get("pr_url"),
        snake_publish_context.get("pullRequestUrl"),
        snake_publish_context.get("pull_request_url"),
        snake_publish_context.get("prUrl"),
        snake_publish_context.get("pr_url"),
        _pull_request_url_from_artifact_path(inputs, context),
    )


def _github_status_publish_change_evidence(
    inputs: Mapping[str, Any],
    context: Mapping[str, Any] | None,
) -> tuple[str, int]:
    previous_outputs = _github_status_previous_outputs(inputs, context)
    sources = (
        inputs,
        previous_outputs,
        _mapping(previous_outputs.get("metadata")),
        _mapping(previous_outputs.get("publishContext")),
        _mapping(previous_outputs.get("publish_context")),
    )
    push_status = ""
    commit_count = 0
    for source in sources:
        candidate_status = _first_string(
            source.get("push_status"),
            source.get("pushStatus"),
        ).lower()
        if candidate_status:
            push_status = candidate_status
        raw_count = source.get("push_commit_count")
        if raw_count is None:
            raw_count = source.get("pushCommitCount")
        if raw_count is None:
            raw_count = source.get("commitCount")
        if isinstance(raw_count, bool):
            continue
        if isinstance(raw_count, (int, float)):
            commit_count = max(commit_count, int(raw_count))
        elif isinstance(raw_count, str) and raw_count.strip().isdigit():
            commit_count = max(commit_count, int(raw_count.strip()))
    return push_status, commit_count


def _github_status_requires_verification(inputs: Mapping[str, Any]) -> bool:
    if "requireVerification" not in inputs:
        return True
    value = inputs.get("requireVerification")
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if not normalized:
        return True
    return normalized not in {"0", "false", "no", "off"}


async def update_github_issue_status(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    github_service_factory: Callable[[], GitHubService] = GitHubService,
) -> ToolResult:
    repository, issue_number = _github_issue_inputs(inputs)
    mode = _github_status_mode(inputs)
    assessment_verdict, assessment_available = (
        await _augment_assessment_verdict_with_ref(
            _assessment_verdict_from_artifact(inputs, _context),
            inputs,
            _context,
        )
    )
    issue_ref = f"{repository}#{issue_number}"
    require_verification = _github_status_requires_verification(inputs)
    if mode in {"start", "in_progress"} and (
        inputs.get("assessmentArtifactPath") or inputs.get("assessment_artifact_path")
        or _assessment_artifact_ref(inputs, _context)
    ):
        if not assessment_available:
            return ToolResult(
                status="FAILED",
                outputs={
                    "issueRef": issue_ref,
                    "decision": "blocked",
                    "summary": "GitHub issue status update requires an assessment artifact, but it was unavailable.",
                },
            )
        if assessment_verdict == "FULLY_IMPLEMENTED":
            return ToolResult(
                status="COMPLETED",
                outputs={
                    "issueRef": issue_ref,
                    "decision": "skipped",
                    "assessmentVerdict": assessment_verdict,
                    "summary": f"Skipped GitHub issue In Progress update for {issue_ref} because assessment verdict is FULLY_IMPLEMENTED.",
                },
            )
        if assessment_verdict == "BLOCKED":
            return ToolResult(
                status="FAILED",
                outputs={
                    "issueRef": issue_ref,
                    "decision": "blocked",
                    "assessmentVerdict": assessment_verdict,
                    "summary": f"Skipped GitHub issue In Progress update for {issue_ref} because assessment verdict is BLOCKED.",
                },
            )
    pull_request_url = _github_status_pull_request_url(inputs, _context)
    if mode == "finalize_after_pr_or_done":
        push_status, commit_count = _github_status_publish_change_evidence(
            inputs,
            _context,
        )
        if not pull_request_url and (
            push_status in {"pushed", "published"} or commit_count > 0
        ):
            return ToolResult(
                status="FAILED",
                outputs={
                    "issueRef": issue_ref,
                    "decision": "blocked",
                    "pushStatus": push_status,
                    "commitCount": commit_count,
                    "summary": (
                        "Skipped GitHub issue finalization because repository changes "
                        "were published without an authoritative pull request URL."
                    ),
                },
            )
        if pull_request_url and require_verification:
            if not (
                inputs.get("verificationArtifactPath")
                or inputs.get("verification_artifact_path")
            ):
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "issueRef": issue_ref,
                        "decision": "blocked",
                        "summary": "GitHub issue Code Review update requires a verification artifact path.",
                    },
                )
            verification_verdict, verification_available = (
                _github_status_verification_verdict(inputs, _context)
            )
            if not verification_available:
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "issueRef": issue_ref,
                        "decision": "blocked",
                        "summary": "GitHub issue Code Review update requires a verification artifact, but it was unavailable.",
                    },
                )
            if (
                inputs.get("verificationArtifactPath")
                or inputs.get("verification_artifact_path")
            ) and verification_verdict != "FULLY_IMPLEMENTED":
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "issueRef": issue_ref,
                        "decision": "blocked",
                        "verificationVerdict": verification_verdict,
                        "summary": f"Skipped GitHub issue Code Review update for {issue_ref} because verification verdict is not FULLY_IMPLEMENTED.",
                    },
                )
        mode = "code_review" if pull_request_url else "done"
    actions = _GITHUB_STATUS_ACTIONS.get(mode, {})
    service = github_service_factory()
    token, resolution_error = await service.resolve_github_token(repo=repository)
    if not token:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "summary": resolution_error or "GitHub issue update is unavailable."})
    headers = service._github_headers(token)
    issue_data, error = await _fetch_github_issue(
        repository=repository,
        issue_number=issue_number,
        github_service_factory=github_service_factory,
    )
    if issue_data is None:
        return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "summary": error or "GitHub issue update fetch failed."})
    issue = _github_issue_payload(issue_data, repository)
    current_labels = [str(label) for label in issue.get("labels") or []]
    remove = {str(label).lower() for label in actions.get("labelsToRemove") or []}
    labels = [label for label in current_labels if label.lower() not in remove]
    for label in actions.get("labelsToAdd") or []:
        if str(label).lower() not in {existing.lower() for existing in labels}:
            labels.append(str(label))
    patch_payload: dict[str, Any] = {"labels": labels}
    if actions.get("closeIssue"):
        patch_payload["state"] = "closed"
    applied: list[str] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.patch(
                f"https://api.github.com/repos/{repository}/issues/{issue_number}",
                headers=headers,
                json=patch_payload,
            )
            response.raise_for_status()
            updated = response.json()
            applied.append("patch_issue")
            comment_body = ""
            pr_url = pull_request_url
            if actions.get("commentPullRequestUrl") and pr_url:
                comment_body = f"Implementation pull request: {pr_url}"
            elif actions.get("comment"):
                comment_body = f"MoonMind started implementation for {issue_ref}."
            if comment_body:
                comment_response = await client.post(
                    f"https://api.github.com/repos/{repository}/issues/{issue_number}/comments",
                    headers=headers,
                    json={"body": comment_body},
                )
                comment_response.raise_for_status()
                applied.append("comment")
        except httpx.HTTPStatusError as exc:
            summary = service._github_permission_summary(exc.response)
            return ToolResult(
                status="FAILED",
                outputs={"issueRef": issue_ref, "summary": f"GitHub issue update failed with HTTP {exc.response.status_code}. {summary}".strip()},
            )
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return ToolResult(status="FAILED", outputs={"issueRef": issue_ref, "summary": f"GitHub issue update failed: {exc.__class__.__name__}"})
    updated_issue = _github_issue_payload(updated, repository)
    issue_url = updated_issue.get("url") or issue.get("url")
    summary = f"Updated GitHub issue {issue_ref} with mode {mode}."
    return ToolResult(
        status="COMPLETED",
        outputs={
            "issueUrl": issue_url,
            "appliedActions": applied,
            "confirmedState": updated_issue.get("state"),
            "confirmedLabels": updated_issue.get("labels"),
            "summary": summary,
            "sideEffect": {
                "effectClass": "external_non_idempotent",
                "kind": "github",
                "operation": (
                    "github.issue.close"
                    if actions.get("closeIssue")
                    else "github.issue.update"
                ),
                "target": issue_url,
                "summary": summary,
            },
        },
    )


async def discover_documents(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
) -> ToolResult:
    """Discover .md, .txt, and .tex files in a directory."""

    directory = _string(inputs.get("directory") or inputs.get("path"))
    if not directory:
        return ToolResult(
            status="FAILED",
            outputs={
                "documentPaths": [],
                "error": "Missing required input: directory or path.",
            },
        )

    extensions = frozenset(
        _list(
            inputs.get("extensions")
            or [".md", ".txt", ".tex"]
        )
    )
    if not extensions:
        extensions = frozenset({".md", ".txt", ".tex"})

    normalized_directory = _normalize_document_directory(directory)
    local_root = _resolve_local_document_root(
        directory=normalized_directory,
        inputs=inputs,
        context=_context,
    )
    if local_root is None and not Path(normalized_directory).expanduser().is_absolute():
        repository = _repository_from_inputs_or_context(inputs, _context)
        if repository:
            try:
                document_paths, resolved_ref, truncated, found_directory = (
                    await _discover_github_document_paths(
                        repository=repository,
                        directory=normalized_directory,
                        extensions=extensions,
                        ref=_ref_from_inputs_or_context(inputs, _context),
                    )
                )
            except Exception as exc:
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "documentPaths": [],
                        "error": (
                            "Directory does not exist in local repo roots and remote "
                            f"repository discovery failed for {directory}: {exc}"
                        ),
                    },
                )
            if not found_directory:
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "documentPaths": [],
                        "error": (
                            "Directory does not exist or is not a directory in "
                            f"repository {repository}: {directory}"
                        ),
                    },
                )
            if truncated:
                return ToolResult(
                    status="FAILED",
                    outputs={
                        "documentPaths": [],
                        "error": (
                            "GitHub returned a truncated repository tree for "
                            f"{_github_repository_slug(repository) or repository}@"
                            f"{resolved_ref}; remote document discovery cannot "
                            "produce a complete listing for "
                            f"{normalized_directory or '<repo root>'}"
                        ),
                    },
                )
            return ToolResult(
                status="COMPLETED",
                outputs={
                    "directory": normalized_directory,
                    "repository": _github_repository_slug(repository) or repository,
                    "ref": resolved_ref,
                    "source": "github",
                    "extensions": sorted(extensions),
                    "documentCount": len(document_paths),
                    "documentPaths": document_paths,
                    "truncated": truncated,
                },
            )

    if local_root is None:
        return ToolResult(
            status="FAILED",
            outputs={
                "documentPaths": [],
                "error": f"Directory does not exist or is not a directory: {directory}",
            },
        )

    root, output_base = local_root
    document_paths: list[str] = []
    for ext in extensions:
        pattern = f"*{ext}"
        for path in root.rglob(pattern):
            if path.is_file():
                document_paths.append(path.relative_to(output_base).as_posix())

    document_paths = sorted(document_paths)

    return ToolResult(
        status="COMPLETED",
        outputs={
            "directory": str(root),
            "source": "filesystem",
            "extensions": sorted(extensions),
            "documentCount": len(document_paths),
            "documentPaths": document_paths,
        },
    )

def _document_update_task_payload(
    *,
    document_path: str,
    task_payload: Mapping[str, Any],
    traceability: Mapping[str, Any],
    depends_on: Sequence[str],
    source_directory: str,
) -> tuple[str, dict[str, Any]]:
    instructions = (
        f"Update the technical document at {document_path} to align with the current "
        "codebase implementation. Follow the document-update skill workflow."
    )
    runtime = _mapping(task_payload.get("runtime"))
    publish = _mapping(task_payload.get("publish"))
    repository = _string(task_payload.get("repository") or task_payload.get("repo"))
    task_inputs = {
        "document_path": document_path,
        "source_directory": source_directory,
    }
    task: dict[str, Any] = {
        "title": f"Document update: {Path(document_path).name}",
        "instructions": instructions,
        "inputs": dict(task_inputs),
        "skill": {
            "name": "document-update",
            "args": dict(task_inputs),
        },
    }
    if runtime:
        task["runtime"] = runtime
    if publish:
        task["publish"] = publish
    if repository:
        task["repository"] = repository
    if depends_on:
        task["dependsOn"] = list(depends_on)
    selected_skill = _string(traceability.get("selectedSkill") or traceability.get("selected_skill"))
    if selected_skill:
        metadata = task.setdefault("metadata", {})
        moonmind = metadata.setdefault("moonmind", {})
        moonmind["selectedSkill"] = selected_skill
        metadata["moonmind"] = moonmind
        task["metadata"] = metadata
    return task["title"], task

async def create_document_update_tasks_from_paths(
    inputs: Mapping[str, Any],
    _context: Mapping[str, Any] | None = None,
    *,
    execution_creator: ExecutionCreator | None = None,
) -> ToolResult:
    """Create document-update tasks from a list of document paths."""

    if execution_creator is None:
        raise ValueError("execution_creator is required for document update workflow creation.")

    context = _context or {}
    previous_outputs = _mapping(context.get("previousOutputs") or context.get("previous_outputs"))
    document_paths: list[str] = []
    raw_paths = inputs.get("documentPaths") or inputs.get("document_paths")
    if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes, bytearray)):
        document_paths = [str(p) for p in raw_paths if _string(p)]
    if not document_paths and previous_outputs:
        raw_paths = previous_outputs.get("documentPaths") or previous_outputs.get("document_paths")
        if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes, bytearray)):
            document_paths = [str(p) for p in raw_paths if _string(p)]

    orchestration_payload = _mapping(
        inputs.get("documentUpdateOrchestration")
        or inputs.get("document_update_orchestration")
    )
    task_payload = _mapping(
        orchestration_payload.get("task")
        or inputs.get("task")
    )
    traceability = _mapping(
        orchestration_payload.get("traceability")
        or inputs.get("traceability")
    )
    source_directory = _string(
        traceability.get("sourceDirectory")
        or traceability.get("source_directory")
        or inputs.get("sourceDirectory")
        or inputs.get("source_directory")
        or ""
    )
    repository = _string(task_payload.get("repository") or task_payload.get("repo"))
    owner_id = (
        _string(task_payload.get("ownerId") or task_payload.get("owner_id"))
        or _string(inputs.get("ownerId") or inputs.get("owner_id"))
        or _string(context.get("ownerId") or context.get("owner_id"))
        or None
    )
    owner_type = (
        _string(task_payload.get("ownerType") or task_payload.get("owner_type"))
        or _string(inputs.get("ownerType") or inputs.get("owner_type"))
        or _string(context.get("ownerType") or context.get("owner_type"))
        or None
    )

    tasks: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    previous_workflow_id = ""

    for index, document_path in enumerate(document_paths, start=1):
        base_result = {
            "documentIndex": index,
            "documentPath": document_path,
        }
        depends_on = [previous_workflow_id] if previous_workflow_id else []
        title, task = _document_update_task_payload(
            document_path=document_path,
            task_payload=task_payload,
            traceability=traceability,
            depends_on=depends_on,
            source_directory=source_directory,
        )
        idempotency_key = _stable_idempotency_key(
            source_issue_key=source_directory,
            story_id=f"doc-{index:03d}",
            issue_key=document_path,
        )
        try:
            created = execution_creator(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                owner_type=owner_type,
                title=title,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": repository or None,
                    "targetRuntime": _string(_mapping(task.get("runtime")).get("mode")) or None,
                    "publishMode": _string(_mapping(task.get("publish")).get("mode")) or None,
                    "workflow": task,
                    "traceability": {
                        "sourceDirectory": source_directory,
                    },
                },
                idempotency_key=idempotency_key,
                repository=repository or None,
                integration="document_update",
                summary=f"Document update workflow for {document_path}.",
            )
            if inspect.isawaitable(created):
                created = await created  # type: ignore[assignment]
        except Exception as exc:
            failures.append(
                {
                    **base_result,
                    "errorCode": "task_creation_failed",
                    "message": str(exc) or "Downstream workflow creation failed.",
                    "dependsOn": depends_on,
                }
            )
            remaining = document_paths[index:]
            for skipped_path in remaining:
                failures.append(
                    {
                        "documentIndex": document_paths.index(skipped_path) + 1,
                        "documentPath": skipped_path,
                        "errorCode": "dependency_not_created",
                        "message": "Earlier downstream workflow creation failed.",
                    }
                )
            break

        created_mapping = dict(created)
        workflow_id = _string(
            created_mapping.get("workflowId") or created_mapping.get("workflow_id")
        )
        task_result = {
            **base_result,
            "workflowId": workflow_id,
            "runId": _string(created_mapping.get("runId") or created_mapping.get("run_id")),
            "title": _string(created_mapping.get("title")) or title,
            "created": not bool(created_mapping.get("existing")),
            "existing": bool(created_mapping.get("existing")),
            "dependsOn": depends_on,
            "idempotencyKey": idempotency_key,
        }
        tasks.append(task_result)
        if depends_on:
            dependencies.append(
                {
                    "fromWorkflowId": depends_on[0],
                    "toWorkflowId": workflow_id,
                    "fromDocumentPath": tasks[-2]["documentPath"] if len(tasks) > 1 else "",
                    "toDocumentPath": document_path,
                    "status": "created",
                }
            )
        previous_workflow_id = workflow_id

    if not document_paths:
        status = "no_downstream_tasks"
    elif failures:
        status = "partial" if tasks else "no_downstream_tasks"
    else:
        status = "completed"
    workflow_status = (
        "no_downstream_workflows" if status == "no_downstream_tasks" else status
    )

    return ToolResult(
        status="COMPLETED",
        outputs={
            "documentUpdateOrchestration": {
                "status": status,
                "workflowStatus": workflow_status,
                "documentCount": len(document_paths),
                "createdTaskCount": len(tasks),
                "createdWorkflowCount": len(tasks),
                "dependencyCount": len(dependencies),
                "tasks": tasks,
                "workflows": tasks,
                "workflowMappings": tasks,
                "dependencies": dependencies,
                "failures": failures,
                "traceability": {
                    "sourceDirectory": source_directory,
                },
            }
        },
    )

def register_story_output_tool_handlers(
    dispatcher: Any,
    *,
    execution_creator: ExecutionCreator | None = None,
    artifact_reader: ArtifactReader | None = None,
) -> None:
    async def _create_jira_issues(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_jira_issues_from_stories(
            inputs,
            context,
            artifact_reader=artifact_reader,
        )

    dispatcher.register_skill(
        skill_name=JIRA_CREATE_ISSUES_TOOL_NAME,
        handler=_create_jira_issues,
    )

    async def _create_jira_orchestrate_tasks(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_jira_orchestrate_tasks_from_issue_mappings(
            inputs,
            context,
            execution_creator=execution_creator,
        )

    dispatcher.register_skill(
        skill_name=JIRA_ORCHESTRATE_TASKS_TOOL_NAME,
        handler=_create_jira_orchestrate_tasks,
    )

    async def _create_jira_implement_tasks(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_jira_implement_tasks_from_issue_mappings(
            inputs,
            context,
            execution_creator=execution_creator,
        )

    dispatcher.register_skill(
        skill_name=JIRA_IMPLEMENT_TASKS_TOOL_NAME,
        handler=_create_jira_implement_tasks,
    )

    async def _create_github_issues(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_github_issues_from_stories(
            inputs,
            context,
            artifact_reader=artifact_reader,
        )

    dispatcher.register_skill(
        skill_name=GITHUB_CREATE_ISSUES_TOOL_NAME,
        handler=_create_github_issues,
    )

    async def _create_github_issue_orchestrate_workflows(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_github_issue_orchestrate_workflows_from_issue_mappings(
            inputs,
            context,
            execution_creator=execution_creator,
        )

    dispatcher.register_skill(
        skill_name=GITHUB_ORCHESTRATE_WORKFLOWS_TOOL_NAME,
        handler=_create_github_issue_orchestrate_workflows,
    )

    async def _create_github_issue_implement_workflows(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_github_issue_implement_workflows_from_issue_mappings(
            inputs,
            context,
            execution_creator=execution_creator,
        )

    dispatcher.register_skill(
        skill_name=GITHUB_IMPLEMENT_WORKFLOWS_TOOL_NAME,
        handler=_create_github_issue_implement_workflows,
    )

    async def _check_jira_blockers(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await check_jira_blockers(inputs, context)

    dispatcher.register_skill(
        skill_name=JIRA_CHECK_BLOCKERS_TOOL_NAME,
        handler=_check_jira_blockers,
    )

    async def _load_jira_preset_brief(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await load_jira_preset_brief(inputs, context)

    dispatcher.register_skill(
        skill_name=JIRA_LOAD_PRESET_BRIEF_TOOL_NAME,
        handler=_load_jira_preset_brief,
    )

    async def _update_jira_issue_status(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await update_jira_issue_status(inputs, context)

    dispatcher.register_skill(
        skill_name=JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME,
        handler=_update_jira_issue_status,
    )

    async def _load_github_issue_preset_brief(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await load_github_issue_preset_brief(inputs, context)

    dispatcher.register_skill(
        skill_name=GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME,
        handler=_load_github_issue_preset_brief,
    )

    async def _check_github_issue_blockers(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await check_github_issue_blockers(inputs, context)

    dispatcher.register_skill(
        skill_name=GITHUB_CHECK_ISSUE_BLOCKERS_TOOL_NAME,
        handler=_check_github_issue_blockers,
    )

    async def _update_github_issue_status(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await update_github_issue_status(inputs, context)

    dispatcher.register_skill(
        skill_name=GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME,
        handler=_update_github_issue_status,
    )

    async def _discover_documents(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await discover_documents(inputs, context)

    dispatcher.register_skill(
        skill_name=DOCUMENT_DISCOVER_TOOL_NAME,
        handler=_discover_documents,
    )

    async def _create_document_update_tasks(
        inputs: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        return await create_document_update_tasks_from_paths(
            inputs,
            context,
            execution_creator=execution_creator,
        )

    dispatcher.register_skill(
        skill_name=DOCUMENT_UPDATE_TASKS_TOOL_NAME,
        handler=_create_document_update_tasks,
    )

__all__ = [
    "GITHUB_CREATE_ISSUES_TOOL_NAME",
    "GITHUB_IMPLEMENT_WORKFLOWS_TOOL_NAME",
    "GITHUB_ORCHESTRATE_WORKFLOWS_TOOL_NAME",
    "JIRA_CHECK_BLOCKERS_TOOL_NAME",
    "JIRA_CREATE_ISSUES_TOOL_NAME",
    "JIRA_IMPLEMENT_TASKS_TOOL_NAME",
    "JIRA_LOAD_PRESET_BRIEF_TOOL_NAME",
    "JIRA_UPDATE_ISSUE_STATUS_TOOL_NAME",
    "GITHUB_LOAD_ISSUE_PRESET_BRIEF_TOOL_NAME",
    "GITHUB_CHECK_ISSUE_BLOCKERS_TOOL_NAME",
    "GITHUB_UPDATE_ISSUE_STATUS_TOOL_NAME",
    "GITHUB_STORY_TOOL_NAMES",
    "JIRA_ORCHESTRATE_TASKS_TOOL_NAME",
    "JIRA_STORY_TOOL_NAMES",
    "check_jira_blockers",
    "create_github_issue_implement_workflows_from_issue_mappings",
    "create_github_issue_orchestrate_workflows_from_issue_mappings",
    "create_github_issues_from_stories",
    "create_jira_issues_from_stories",
    "create_jira_orchestrate_tasks_from_issue_mappings",
    "create_jira_implement_tasks_from_issue_mappings",
    "discover_documents",
    "create_document_update_tasks_from_paths",
    "load_jira_preset_brief",
    "update_jira_issue_status",
    "DOCUMENT_DISCOVER_TOOL_NAME",
    "DOCUMENT_UPDATE_TASKS_TOOL_NAME",
    "register_story_output_tool_handlers",
]
