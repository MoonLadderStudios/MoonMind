"""GitHub Projects discovery and board projection for the existing issue lifecycle.

Only trusted Activities call this module. Project membership orders candidates;
it never grants issue ownership, publication authority, or completion evidence.
"""

from __future__ import annotations

import json
import math
import re
import shlex
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from moonmind.workflows.adapters.github_service import GitHubService


_PROJECT_URL = re.compile(
    r"https://github\.com/(?P<kind>orgs|users)/(?P<owner>[A-Za-z0-9_.-]+)/"
    r"projects/(?P<number>[1-9]\d*)(?:/views/(?P<view>[1-9]\d*))?/?$"
)
_READY_EQUIVALENTS = {"selected for development", "ready for development"}
_PHASE_NAMES = {
    "in_progress": ("In Progress",),
    "code_review": ("In Review", "Code Review", "Review"),
    "closed": ("Done", "Completed", "Complete"),
    "recovery_needed": ("Recovery Needed",),
    "needs_attention": ("Needs Attention",),
}


class ProjectError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProjectClient:
    def __init__(self, service: GitHubService, repository: str):
        self.service, self.repository = service, repository

    async def graphql(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        token, error = await self.service.resolve_github_token(
            repo=self.repository,
            operations=("issues", "projects"),
        )
        if not token:
            raise ProjectError(
                "project_auth_unavailable",
                error or "GitHub project access is unavailable.",
            )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.github.com/graphql",
                    json={"query": query, "variables": dict(variables)},
                    headers=self.service._github_headers(token),
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            code = (
                "project_permission_denied"
                if exc.response.status_code in {401, 403, 404}
                else "project_provider_unavailable"
            )
            raise ProjectError(
                code,
                f"GitHub project request failed (HTTP {exc.response.status_code}).",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProjectError(
                "project_outcome_unknown",
                "GitHub project request could not be confirmed; reread before retrying.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("errors")
            or not isinstance(payload.get("data"), dict)
        ):
            errors = payload.get("errors", []) if isinstance(payload, dict) else []
            denied = any(
                e.get("type") in {"FORBIDDEN", "NOT_FOUND", "INSUFFICIENT_SCOPES"}
                for e in errors
                if isinstance(e, dict)
            )
            raise ProjectError(
                "project_permission_denied" if denied else "project_read_incomplete",
                "GitHub project access or response is incomplete. Check project read/write permissions (project scope for a classic token).",
            )
        return payload["data"]

    async def read_item(self, binding: Mapping[str, Any]) -> dict[str, Any]:
        data = await self.graphql(
            """query ProjectItemPhase($item: ID!) {
          node(id: $item) { ... on ProjectV2Item {
            id isArchived project { id }
            content { __typename ... on Issue { number repository { nameWithOwner } } }
            fieldValues(first: 100) { pageInfo { hasNextPage } nodes {
              ... on ProjectV2ItemFieldSingleSelectValue {
                optionId field { ... on ProjectV2SingleSelectField { id } }
              }
            } }
          } }
        }""",
            {"item": binding["itemId"]},
        )
        node = data.get("node")
        if not isinstance(node, dict) or node.get("id") != binding["itemId"]:
            raise ProjectError(
                "project_item_unavailable", "Selected project item is unavailable."
            )
        content = node.get("content") or {}
        if (
            node.get("isArchived") is not False
            or content.get("__typename") != "Issue"
            or content.get("number") != binding["issueNumber"]
            or str(
                (content.get("repository") or {}).get("nameWithOwner", "")
            ).casefold()
            != self.repository.casefold()
            or (node.get("project") or {}).get("id") != binding["projectId"]
        ):
            raise ProjectError(
                "project_item_changed",
                "Project item identity, membership, or archive state changed.",
            )
        values = node.get("fieldValues") or {}
        if values.get("pageInfo", {}).get("hasNextPage") is not False or not isinstance(
            values.get("nodes"), list
        ):
            raise ProjectError(
                "project_read_incomplete", "Project item field values are incomplete."
            )
        matches = [
            v
            for v in values["nodes"]
            if isinstance(v, dict)
            and (v.get("field") or {}).get("id") == binding["fieldId"]
        ]
        return {"optionId": matches[0].get("optionId") if len(matches) == 1 else None}


def _option(
    options: list[dict], names: list[str] | tuple[str, ...], *, required: bool
) -> str | None:
    wanted = {name.strip().casefold() for name in names}
    matches = [
        o for o in options if str(o.get("name", "")).strip().casefold() in wanted
    ]
    if len(matches) == 1:
        return matches[0]["id"]
    if required or len(matches) > 1:
        raise ProjectError(
            "project_column_selection_required",
            f"Select an unambiguous project column for: {', '.join(names)}.",
        )
    return None


def _filters(raw: str) -> list[tuple[str, str, bool]]:
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise ProjectError(
            "unsupported_project_view_filter",
            "The project view filter cannot be interpreted; configure a supported view.",
        ) from exc
    result = []
    for token in tokens:
        negative = token.startswith("-")
        key, separator, value = token.lstrip("-").partition(":")
        if (
            not separator
            or not value
            or key not in {"is", "state", "repo", "status", "label"}
            or (key == "is" and value not in {"issue", "open", "closed", "draft", "pr"})
            or (key == "state" and value not in {"open", "closed"})
        ):
            raise ProjectError(
                "unsupported_project_view_filter",
                "This project view uses unsupported filters; select a view with is/state/repo/status/label qualifiers or an unfiltered project URL.",
            )
        result.append((key, value, negative))
    return result


@dataclass
class ProjectQueue:
    items: list[dict[str, Any]]
    bindings: dict[int, dict[str, Any]]
    evidence: dict[str, Any]
    filters: list[tuple[str, str, bool]]

    def matches(self, issue: Mapping[str, Any], binding: Mapping[str, Any]) -> bool:
        for key, value, negative in self.filters:
            choices = {v.strip().casefold() for v in value.split(",")}
            actual = {
                "is": {"issue", str(issue.get("state", "")).casefold()},
                "state": {str(issue.get("state", "")).casefold()},
                "repo": {str(binding["repository"]).casefold()},
                "status": {str(binding["sourceColumn"]).casefold()},
                "label": {
                    str(label.get("name", "")).casefold()
                    for label in issue.get("labels", [])
                    if isinstance(label, dict)
                },
            }[key]
            if bool(actual & choices) == negative:
                return False
        return True


async def _repository_projects(
    client: ProjectClient, *, owner_id: str = ""
) -> tuple[list[dict[str, Any]], str]:
    """Read a complete bounded list before deciding that a match is unique."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", client.repository):
        raise ProjectError(
            "invalid_repository",
            "Select a GitHub owner/repository before discovering its project.",
        )
    repository_owner, repository_name = client.repository.split("/", 1)
    query = """query RepositoryProjects($owner: String!, $name: String!, $after: String) {
      repository(owner: $owner, name: $name) {
        nameWithOwner owner { id }
        projectsV2(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor } nodes { id title url closed }
        }
      }
    }"""
    if owner_id:
        query = """query RepositoryOwnerProjects($owner: ID!, $after: String) {
          node(id: $owner) { id
            ... on Organization { projectsV2(first: 100, after: $after) {
              pageInfo { hasNextPage endCursor } nodes { id title url closed }
            } }
            ... on User { projectsV2(first: 100, after: $after) {
              pageInfo { hasNextPage endCursor } nodes { id title url closed }
            } }
          }
        }"""
    projects, seen_ids, seen_cursors = [], set(), set()
    cursor, resolved_owner = None, owner_id
    for _ in range(5):
        variables = {"owner": owner_id or repository_owner, "after": cursor}
        if not owner_id:
            variables["name"] = repository_name
        data = await client.graphql(query, variables)
        scope = data.get("node" if owner_id else "repository")
        if not isinstance(scope, dict):
            raise ProjectError(
                "project_discovery_incomplete",
                "Repository project discovery is unavailable; check access or specify a project URL.",
            )
        if owner_id:
            identity_matches = scope.get("id") == owner_id
        else:
            identity_matches = (
                str(scope.get("nameWithOwner", "")).casefold()
                == client.repository.casefold()
            )
            current_owner = (scope.get("owner") or {}).get("id")
            identity_matches = (
                identity_matches
                and bool(current_owner)
                and (not resolved_owner or current_owner == resolved_owner)
            )
            resolved_owner = current_owner
        connection = scope.get("projectsV2") or {}
        page, info = connection.get("nodes"), connection.get("pageInfo") or {}
        if (
            not identity_matches
            or not isinstance(page, list)
            or len(page) > 100
            or type(info.get("hasNextPage")) is not bool
        ):
            raise ProjectError(
                "project_discovery_incomplete",
                "Repository identity or project listing is incomplete; specify a project URL.",
            )
        for project in page:
            if (
                not isinstance(project, dict)
                or any(
                    not isinstance(project.get(key), str) or not project[key].strip()
                    for key in ("id", "title", "url")
                )
                or type(project.get("closed")) is not bool
                or project["id"] in seen_ids
            ):
                raise ProjectError(
                    "project_discovery_incomplete",
                    "Project discovery returned incomplete or duplicate identities; specify a project URL.",
                )
            seen_ids.add(project["id"])
            if not project["closed"]:
                projects.append(project)
        if not info["hasNextPage"]:
            return projects, resolved_owner
        cursor = info.get("endCursor")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    raise ProjectError(
        "project_discovery_incomplete",
        "Project discovery exceeds its 500-project limit or pagination did not advance; specify a project URL.",
    )


async def _resolve_project_source(
    client: ProjectClient, source: Mapping[str, Any]
) -> tuple[str, str, str | None]:
    explicit = str(source.get("url") or "").strip()
    if explicit:
        return explicit, "explicit", None
    linked, owner_id = await _repository_projects(client)
    repository_name = client.repository.split("/", 1)[1].casefold()
    candidates = linked
    resolution = "repository_link"
    if not linked:
        candidates, _ = await _repository_projects(client, owner_id=owner_id)
        candidates = [
            project
            for project in candidates
            if project["title"].strip().casefold() == repository_name
        ]
        resolution = "repository_name"
    if len(candidates) > 1:
        candidates = [
            project
            for project in candidates
            if project["title"].strip().casefold() == repository_name
        ]
        if len(candidates) != 1:
            raise ProjectError(
                "project_selection_required",
                "Multiple projects match the selected repository; enter an explicit GitHub project URL.",
            )
    if not candidates:
        raise ProjectError(
            "project_not_found",
            "No open linked project or unique same-name project was found for the selected repository; enter a GitHub project URL.",
        )
    project = candidates[0]
    return project["url"], resolution, project["id"]


async def discover_project_queue(
    service: GitHubService, repository: str, source: Mapping[str, Any]
) -> ProjectQueue:
    client = ProjectClient(service, repository)
    project_url, resolution, expected_project_id = await _resolve_project_source(
        client, source
    )
    match = _PROJECT_URL.fullmatch(project_url)
    if not match:
        raise ProjectError(
            "invalid_project_url",
            "Use a GitHub organization or user project URL, optionally including /views/N.",
        )
    view = int(match["view"]) if match["view"] else None
    query = """query ProjectQueueMetadata($owner: String!, $number: Int!%s) {
      owner: %s(login: $owner) { projectV2(number: $number) {
        id title viewerCanUpdate
        fields(first: 100) { pageInfo { hasNextPage } nodes {
          __typename ... on ProjectV2SingleSelectField { id name options { id name } }
          ... on ProjectV2Field { id name dataType }
        } } %s
      } }
    }""" % (
        ", $view: Int!" if view else "",
        "organization" if match["kind"] == "orgs" else "user",
        "view(number: $view) { id filter }" if view else "",
    )
    variables = {"owner": match["owner"], "number": int(match["number"])}
    if view:
        variables["view"] = view
    data = await client.graphql(query, variables)
    project = (data.get("owner") or {}).get("projectV2")
    if not isinstance(project, dict) or not project.get("id"):
        raise ProjectError(
            "project_permission_denied",
            "The project is unavailable to the selected GitHub connection.",
        )
    if expected_project_id and project["id"] != expected_project_id:
        raise ProjectError(
            "project_discovery_incomplete",
            "The discovered project identity changed before admission; retry discovery.",
        )
    if project.get("viewerCanUpdate") is not True:
        raise ProjectError(
            "project_permission_denied",
            "Project write access is required before consuming issues.",
        )
    fields = project.get("fields") or {}
    if fields.get("pageInfo", {}).get("hasNextPage") is not False or not isinstance(
        fields.get("nodes"), list
    ):
        raise ProjectError(
            "project_read_incomplete", "Project field discovery is incomplete."
        )
    if view and not isinstance(project.get("view"), dict):
        raise ProjectError(
            "project_view_unavailable", "The requested project view is unavailable."
        )
    filters = (
        _filters(str((project.get("view") or {}).get("filter") or "")) if view else []
    )
    field_name = str(source.get("field") or "Status").strip()
    matches = [
        f
        for f in fields["nodes"]
        if isinstance(f, dict)
        and str(f.get("name", "")).casefold() == field_name.casefold()
    ]
    if (
        len(matches) != 1
        or matches[0].get("__typename") != "ProjectV2SingleSelectField"
    ):
        raise ProjectError(
            "project_column_field_required",
            "Select a single-select project column field (Status by default).",
        )
    field = matches[0]
    options = field.get("options") or []
    columns = source.get("columns") or []
    if isinstance(columns, str):
        columns = [name.strip() for name in columns.splitlines() if name.strip()]
    if not isinstance(columns, list) or any(
        not isinstance(name, str) or not name.strip() for name in columns
    ):
        raise ProjectError(
            "project_column_selection_required",
            "List source columns in priority order, one per line.",
        )
    if columns:
        ordered = [_option(options, [name], required=True) for name in columns]
    else:
        ready = _option(options, ["Ready"], required=False)
        ordered = [
            ready or _option(options, tuple(sorted(_READY_EQUIVALENTS)), required=True)
        ]
    if len(set(ordered)) != len(ordered):
        raise ProjectError(
            "project_column_selection_required", "Source columns must be unique."
        )
    overrides = source.get("phases") or {}
    if not isinstance(overrides, Mapping) or set(overrides) - set(_PHASE_NAMES):
        raise ProjectError(
            "invalid_project_phases",
            "Project phase mappings contain unsupported lifecycle phases.",
        )
    phases = {
        phase: _option(
            options,
            [str(overrides[phase])] if overrides.get(phase) else names,
            required=bool(overrides.get(phase))
            or phase in {"in_progress", "code_review", "closed"},
        )
        for phase, names in _PHASE_NAMES.items()
    }
    ordering = str(source.get("order") or "oldest")
    if ordering not in {"oldest", "priority"}:
        raise ProjectError(
            "invalid_project_order", "Project ordering must be oldest or priority."
        )
    priority_name = str(source.get("priorityField") or "Priority")
    priority_fields = [
        f
        for f in fields["nodes"]
        if isinstance(f, dict) and f.get("name") == priority_name
    ]
    if ordering == "priority" and (
        len(priority_fields) != 1
        or not (
            priority_fields[0].get("options")
            or priority_fields[0].get("dataType") == "NUMBER"
        )
    ):
        raise ProjectError(
            "project_priority_field_required",
            "Select a numeric or single-select priority field; lower numbers/earlier options run first.",
        )
    ranks = (
        {o["id"]: i for i, o in enumerate(priority_fields[0].get("options", []))}
        if priority_fields
        else {}
    )
    provider_query = f"is:issue is:open repo:{repository}"
    if field["name"].casefold() == "status":
        provider_query += " status:" + ",".join(
            json.dumps(next(o["name"] for o in options if o["id"] == column))
            for column in ordered
        )
    if view and project["view"].get("filter"):
        provider_query += " " + project["view"]["filter"]
    nodes, seen = [], set()
    cursor = None
    for _ in range(5):
        data = await client.graphql(
            """query ProjectQueueItems($project: ID!, $after: String, $field: String!, $priority: String!, $query: String!) {
          node(id: $project) { ... on ProjectV2 { items(first: 100, after: $after, query: $query) {
            pageInfo { hasNextPage endCursor } nodes {
              id isArchived column: fieldValueByName(name: $field) { ... on ProjectV2ItemFieldSingleSelectValue { optionId } }
              priority: fieldValueByName(name: $priority) {
                ... on ProjectV2ItemFieldSingleSelectValue { optionId }
                ... on ProjectV2ItemFieldNumberValue { number }
              }
              content { __typename ... on Issue { id number state createdAt repository { nameWithOwner } } }
            }
          } } }
        }""",
            {
                "project": project["id"],
                "after": cursor,
                "field": field["name"],
                "priority": priority_name,
                "query": provider_query,
            },
        )
        connection = (data.get("node") or {}).get("items") or {}
        page, info = connection.get("nodes"), connection.get("pageInfo") or {}
        if (
            not isinstance(page, list)
            or len(page) > 100
            or type(info.get("hasNextPage")) is not bool
        ):
            raise ProjectError(
                "project_scan_incomplete", "Project item discovery is incomplete."
            )
        nodes.extend(page)
        if not info["hasNextPage"]:
            break
        next_cursor = info.get("endCursor")
        if not next_cursor or next_cursor in seen:
            raise ProjectError(
                "project_scan_incomplete", "Project pagination did not advance."
            )
        seen.add(next_cursor)
        cursor = next_cursor
    else:
        raise ProjectError(
            "project_scan_incomplete",
            "Project exceeds the 500-item discovery limit; use a narrower supported source.",
        )
    skipped, candidates, bindings = {}, [], {}
    option_names = {o["id"]: o["name"] for o in options}
    for node in nodes:
        if not isinstance(node, dict):
            raise ProjectError(
                "project_scan_incomplete", "GitHub returned an invalid project item."
            )
        content = node.get("content") or {}
        kind = content.get("__typename")
        column = (node.get("column") or {}).get("optionId")
        reason = None
        if kind != "Issue":
            reason = (
                "draft_issue"
                if kind == "DraftIssue"
                else "pull_request" if kind == "PullRequest" else "unreadable_item"
            )
        elif node.get("isArchived") is not False:
            reason = "archived"
        elif (
            str((content.get("repository") or {}).get("nameWithOwner", "")).casefold()
            != repository.casefold()
        ):
            reason = "other_repository"
        elif content.get("state") != "OPEN":
            reason = "closed"
        elif column not in ordered:
            reason = "other_column"
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        number = content.get("number")
        if (
            type(number) is not int
            or number < 1
            or not node.get("id")
            or not content.get("createdAt")
            or number in bindings
        ):
            raise ProjectError(
                "project_scan_incomplete",
                "GitHub returned incomplete or duplicate issue identity.",
            )
        priority = node.get("priority") or {}
        rank = (
            priority.get("number", ranks.get(priority.get("optionId"), float("inf")))
            if ordering == "priority"
            else 0
        )
        if (
            ordering == "priority"
            and "number" in priority
            and (
                isinstance(rank, bool)
                or not isinstance(rank, (int, float))
                or not math.isfinite(rank)
            )
        ):
            raise ProjectError(
                "project_read_incomplete",
                "GitHub returned an invalid numeric priority.",
            )
        candidates.append(
            {
                **node,
                "rank": (ordered.index(column), rank, content["createdAt"], number),
            }
        )
        bindings[number] = {
            "projectId": project["id"],
            "projectUrl": project_url,
            "itemId": node["id"],
            "fieldId": field["id"],
            "repository": repository,
            "issueNumber": number,
            "sourceColumn": option_names[column],
            "sourceOptionId": column,
            "lastOptionId": column,
            "phases": {**phases, "available": column},
        }
    candidates.sort(key=lambda candidate: candidate["rank"])
    return ProjectQueue(
        candidates,
        bindings,
        {
            "projectId": project["id"],
            "url": project_url,
            "resolution": resolution,
            "field": field["name"],
            "columns": [option_names[c] for c in ordered],
            "order": ordering,
            "itemsExamined": len(nodes),
            "skipped": skipped,
        },
        filters,
    )


async def _read_issue(
    service: GitHubService, repository: str, number: int
) -> dict[str, Any]:
    token, error = await service.resolve_github_token(
        repo=repository, operations=("issues",)
    )
    if not token:
        raise ProjectError(
            "project_auth_unavailable", error or "Issue access is unavailable."
        )
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"https://api.github.com/repos/{repository}/issues/{number}",
                headers=service._github_headers(token),
            )
            response.raise_for_status()
            issue = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProjectError(
            "project_read_incomplete",
            "Issue lifecycle could not be confirmed for project synchronization.",
        ) from exc
    if (
        not isinstance(issue, dict)
        or issue.get("number") != number
        or "pull_request" in issue
        or str(issue.get("html_url", "")).casefold()
        != f"https://github.com/{repository}/issues/{number}".casefold()
        or issue.get("state") not in {"open", "closed"}
        or not isinstance(issue.get("labels"), list)
    ):
        raise ProjectError(
            "project_read_incomplete", "Issue lifecycle or identity is incomplete."
        )
    return issue


async def _completion_confirmed(
    service, receipt, binding, lifecycle_result, finalization
):
    if lifecycle_result.get("lifecycleSettled") == "closed" and lifecycle_result.get(
        "mutationOutcome"
    ) in {"applied", "already_applied"}:
        return True
    if binding.get("completionVerified"):
        return True
    plan = (finalization or {}).get("plan") or {}
    transition = plan.get("transition") or {}
    observation = ((finalization or {}).get("observation") or {}).get(
        "mutationOutcome"
    ) or {}
    if (
        plan.get("releasable") is True
        and transition.get("allowed") is True
        and transition.get("toTarget") == "to_closed"
        and observation.get("outcome") in {"applied", "already_applied"}
    ):
        return True
    candidate = binding.get("reviewCandidate")
    if not candidate:
        return False
    # A PR-only owner can finish before the eventual merge. Reuse the same
    # exact-PR/head/target validation as the existing merge handoff boundary.
    from moonmind.workflows.temporal.story_output_tools import (
        _validate_post_merge_issue_handoff,
    )

    reason = await _validate_post_merge_issue_handoff(
        service,
        repository=receipt.repository,
        issue_ref=f"{receipt.repository}#{receipt.issue_number}",
        pull_request=candidate,
        expected_ref=str(binding.get("completionTargetRef") or ""),
        admitted_workflow_id=receipt.owner.split("/", 1)[-1],
    )
    return reason is None


async def sync_claim_project(
    store, receipt, service, *, lifecycle_result=None
) -> dict[str, Any]:
    try:
        return await _sync_claim_project(
            store, receipt, service, lifecycle_result=lifecycle_result
        )
    except Exception as exc:
        return {
            "status": "pending",
            "reasonCode": "project_sync_unavailable",
            "summary": f"Project synchronization deferred ({type(exc).__name__}).",
        }


async def _sync_claim_project(
    store, receipt, service, *, lifecycle_result=None
) -> dict[str, Any]:
    """Reconcile one saved projection without changing issue or PR outcomes.

    Persist the intended option before the mutation. Lost acknowledgements are
    resolved by reading that option on retry. GitHub offers no conditional field
    update: an unexpected observed move suspends future writes, but cannot fence
    an operator racing an already submitted request.
    """
    if not getattr(receipt, "project_binding_json", None):
        return {"status": "skipped"}
    lifecycle_result = lifecycle_result or {}
    from moonmind.workflows.temporal.github_issue_lifecycle import interpret_issue

    async with store.locked(receipt.owner) as row:
        binding = dict(row.project_binding_json or {})
        try:
            issue = await _read_issue(service, receipt.repository, receipt.issue_number)
            phase = interpret_issue(issue).settled
            # A successor owns projection from its own claim. Retained receipts
            # must not race that owner after release or continuation.
            if row.ownership_ended:
                successor = await store.active_for_issue(
                    receipt.repository, receipt.issue_number
                )
                if successor is not None and successor.owner != receipt.owner:
                    binding["retired"] = True
                    row.project_binding_json = binding
                    return {
                        "status": "skipped",
                        "reasonCode": "project_successor_owned",
                    }
            if phase == "closed":
                if issue.get("state_reason") == "not_planned":
                    binding["retired"] = True
                    row.project_binding_json = binding
                    return {
                        "status": "skipped",
                        "reasonCode": "project_closed_without_completion",
                    }
                if not await _completion_confirmed(
                    service, receipt, binding, lifecycle_result, row.finalization_json
                ):
                    raise ProjectError(
                        "project_completion_unverified",
                        "Done awaits verified completion on the intended destination.",
                    )
                binding["completionVerified"] = True
            elif phase == "code_review" and lifecycle_result.get("mutationOutcome") in {
                "applied",
                "already_applied",
            }:
                handoff = lifecycle_result.get("attemptHandoff") or {}
                url, head = handoff.get("prUrl"), handoff.get("prHead")
                match = re.fullmatch(
                    r"https://github\.com/[^/]+/[^/]+/pull/([1-9]\d*)", str(url or "")
                )
                if match and head:
                    binding["reviewCandidate"] = {
                        "repo": receipt.repository,
                        "url": url,
                        "number": int(match[1]),
                        "headSha": head,
                        "baseBranch": handoff.get("prBase", ""),
                    }
            target = binding["phases"].get(phase)
            if not target:
                binding["lastSync"] = {
                    "status": "skipped",
                    "reasonCode": "project_phase_unmapped",
                    "phase": phase,
                }
                row.project_binding_json = binding
                return binding["lastSync"]
            client = ProjectClient(service, receipt.repository)
            observed = (await client.read_item(binding))["optionId"]
            if observed == target:
                binding.pop("suspended", None)
                binding.update(lastOptionId=target, pendingOptionId=None, phase=phase)
                result = {"status": "confirmed", "phase": phase, "optionId": target}
            elif observed not in {
                binding.get("lastOptionId"),
                binding.get("pendingOptionId"),
            }:
                binding["suspended"] = True
                result = {
                    "status": "suspended",
                    "reasonCode": "project_operator_move",
                    "summary": "Project column changed externally; board synchronization is suspended to preserve that move.",
                }
            else:
                binding.pop("suspended", None)
                binding["pendingOptionId"] = target
                row.project_binding_json = dict(binding)
                # Commit the effect intent while retaining the claim lock's
                # existing serialization, just as issue comment publication does.
                from sqlalchemy.ext.asyncio import async_object_session

                await async_object_session(row).commit()
                data = await client.graphql(
                    """mutation UpdateProjectPhase($project: ID!, $item: ID!, $field: ID!, $option: String!) {
                  updateProjectV2ItemFieldValue(input: {
                    projectId: $project itemId: $item fieldId: $field
                    value: { singleSelectOptionId: $option }
                  }) { projectV2Item { id } }
                }""",
                    {
                        "project": binding["projectId"],
                        "item": binding["itemId"],
                        "field": binding["fieldId"],
                        "option": target,
                    },
                )
                if (data.get("updateProjectV2ItemFieldValue") or {}).get(
                    "projectV2Item", {}
                ).get("id") != binding["itemId"]:
                    raise ProjectError(
                        "project_outcome_unknown",
                        "Project phase update has no confirmed item receipt.",
                    )
                if (await client.read_item(binding))["optionId"] != target:
                    raise ProjectError(
                        "project_outcome_unknown",
                        "Project phase update was not observed on readback.",
                    )
                binding.update(lastOptionId=target, pendingOptionId=None, phase=phase)
                result = {"status": "confirmed", "phase": phase, "optionId": target}
            if result["status"] == "confirmed" and phase == "closed":
                binding["retired"] = True
        except ProjectError as exc:
            result = {"status": "pending", "reasonCode": exc.code, "summary": str(exc)}
        except Exception as exc:
            # Provider/repository reader failures cannot erase the original
            # implementation result or leak raw transport/credential messages.
            result = {
                "status": "pending",
                "reasonCode": "project_sync_unavailable",
                "summary": f"Project synchronization deferred ({type(exc).__name__}).",
            }
        binding["lastSync"] = result
        row.project_binding_json = dict(binding)
        return result


async def reconcile_project_claims(*, state, store=None, service=None, repository=""):
    """Bounded rotating projection sweep owned by existing issue maintenance."""
    import asyncio
    import time
    from sqlalchemy import select
    from api_service.db.models import GitHubIssueClaim
    from moonmind.workflows.temporal.issue_claim_store import (
        ClaimReceipt,
        IssueClaimStore,
    )

    store, service = store or IssueClaimStore(), service or GitHubService()
    cursor_key = repository.casefold() or "*"
    cursors = state.setdefault("projectClaimCursor", {})
    query = select(GitHubIssueClaim).where(
        GitHubIssueClaim.project_binding_json.is_not(None)
    )
    if repository:
        query = query.where(GitHubIssueClaim.repository == repository.casefold())
    async with store.sessions() as session:
        rows = (
            (
                await session.execute(
                    query.where(GitHubIssueClaim.owner > cursors.get(cursor_key, ""))
                    .order_by(GitHubIssueClaim.owner)
                    .limit(25)
                )
            )
            .scalars()
            .all()
        )
        receipts = [ClaimReceipt.from_row(row) for row in rows]
    results = []
    started = time.monotonic()
    processed = 0
    for receipt in receipts:
        if time.monotonic() - started >= 30:
            break
        processed += 1
        cursors[cursor_key] = receipt.owner
        if not receipt.project_binding_json or receipt.project_binding_json.get(
            "retired"
        ):
            continue
        try:
            async with asyncio.timeout(10):
                result = await sync_claim_project(store, receipt, service)
        except TimeoutError:
            result = {"status": "pending", "reasonCode": "project_sync_timeout"}
        results.append(
            {
                "owner": receipt.owner,
                "repository": receipt.repository,
                "issueNumber": receipt.issue_number,
                **result,
            }
        )
    if processed == len(receipts) and len(receipts) < 25:
        cursors[cursor_key] = ""
    return results
