"""Provider-facing proposal delivery helpers.

The executable proposal payload stays in MoonMind storage. External tracker
issues are review artifacts with bounded controls and links back to evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from moonmind.utils.logging import SecretRedactor

_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|authorization|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_COMMAND_RE = re.compile(
    (
        r"^\s*/moonmind\s+(?P<action>promote|dismiss|defer|priority|"
        r"reprioritize|request[-_ ]revision)\b(?P<args>.*)$"
    ),
    re.IGNORECASE | re.MULTILINE,
)
_SUPPORTED_ACTIONS = frozenset(
    {"promote", "dismiss", "defer", "reprioritize", "request_revision"}
)
_ACTION_ALIASES = {
    "priority": "reprioritize",
    "request-revision": "request_revision",
    "request revision": "request_revision",
}


class ProposalDeliveryError(RuntimeError):
    """Raised when proposal delivery cannot be attempted safely."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        destination: str,
        retryable: bool = False,
        next_action: str = "Review proposal delivery policy and provider configuration.",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.destination = destination
        self.retryable = retryable
        self.next_action = next_action

    def to_output(self, *, record_id: str | None = None) -> dict[str, Any]:
        output: dict[str, Any] = {
            "provider": self.provider,
            "destination": self.destination,
            "sanitizedReason": str(self),
            "recoverableNextAction": self.next_action,
            "retryable": self.retryable,
        }
        if record_id:
            output["deliveryRecordId"] = record_id
        return output


@dataclass(frozen=True, slots=True)
class RenderedProposalIssue:
    """Provider-ready rendered review issue."""

    title: str
    body: str
    labels: tuple[str, ...] = ()
    fields: dict[str, Any] = field(default_factory=dict)
    marker: str = ""


@dataclass(frozen=True, slots=True)
class ProposalDeliveryRequest:
    """Compact delivery input built from a stored proposal record."""

    record_id: str
    provider: str
    repository: str
    title: str
    summary: str
    category: str | None
    tags: tuple[str, ...]
    priority: str
    dedup_key: str
    dedup_hash: str
    task_snapshot_ref: str | None
    task_create_request: Mapping[str, Any]
    origin_metadata: Mapping[str, Any]
    provider_metadata: Mapping[str, Any]
    resolved_policy: Mapping[str, Any]
    external_key: str | None = None
    external_url: str | None = None

    @property
    def destination(self) -> str:
        if self.provider == "jira":
            jira = _provider_config(self.provider_metadata, "jira")
            return _clean(jira.get("project_key") or jira.get("projectKey")) or self.repository
        github = _provider_config(self.provider_metadata, "github")
        return _clean(github.get("repository")) or self.repository


@dataclass(frozen=True, slots=True)
class ProposalDeliveryResult:
    """Sanitized result from provider delivery."""

    provider: str
    external_key: str
    external_url: str
    created: bool
    duplicate_source: str | None = None
    delivered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    warnings: tuple[str, ...] = ()
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def to_decision(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "externalKey": self.external_key,
            "externalUrl": self.external_url,
            "created": self.created,
            "duplicateSource": self.duplicate_source,
            "deliveredAt": self.delivered_at.isoformat(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ProviderDecisionEvent:
    """Bounded reviewer decision observed from a provider issue."""

    provider: str
    external_key: str
    provider_event_id: str
    actor: str
    body: str = ""
    action: str | None = None
    note: str | None = None
    authenticity_verified: bool = True
    actor_authorized: bool = True
    runtime_mode: str | None = None
    external_state: str | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ProviderDecisionResult:
    """Normalized reviewer decision accepted from a provider event."""

    accepted: bool
    decision: str | None
    note: str | None
    actor: str
    provider_event_id: str
    reason: str | None = None
    priority: str | None = None
    defer_until: str | None = None
    runtime_mode: str | None = None
    external_state: str | None = None
    promoted_execution_id: str | None = None


class ProposalIssueProvider(Protocol):
    """Provider port used by proposal delivery orchestration."""

    async def search_issue(self, request: ProposalDeliveryRequest) -> dict[str, Any] | None:
        pass

    async def create_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
    ) -> dict[str, Any]:
        pass

    async def update_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
        issue: Mapping[str, Any],
    ) -> dict[str, Any]:
        pass


class GitHubProposalIssueProvider:
    """Proposal issue provider backed by the trusted GitHub service."""

    def __init__(self, github_service: Any) -> None:
        self._github = github_service

    async def search_issue(self, request: ProposalDeliveryRequest) -> dict[str, Any] | None:
        if not hasattr(self._github, "search_issue_by_marker"):
            return None
        return await self._github.search_issue_by_marker(
            repo=request.destination,
            marker=request.dedup_hash,
        )

    async def create_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
    ) -> dict[str, Any]:
        result = await self._github.create_issue(
            repo=request.destination,
            title=rendered.title,
            body=rendered.body,
            labels=list(rendered.labels),
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(by_alias=True)
        return dict(result)

    async def update_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
        issue: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = await self._github.update_issue(
            repo=request.destination,
            issue_number=_clean(issue.get("key") or issue.get("number")),
            title=rendered.title,
            body=rendered.body,
            labels=list(rendered.labels),
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(by_alias=True)
        return dict(result)


class JiraProposalIssueProvider:
    """Proposal issue provider backed by the trusted Jira tool service."""

    def __init__(self, jira_service: Any) -> None:
        self._jira = jira_service

    async def search_issue(self, request: ProposalDeliveryRequest) -> dict[str, Any] | None:
        from moonmind.integrations.jira.models import SearchIssuesRequest

        marker = request.dedup_hash
        project_key = request.destination
        result = await self._jira.search_issues(
            SearchIssuesRequest(
                jql=f'text ~ "{marker}"',
                projectKey=project_key,
                fields=["summary"],
                maxResults=1,
            )
        )
        issues = result.get("issues") if isinstance(result, Mapping) else None
        if not isinstance(issues, list) or not issues:
            return None
        issue = issues[0]
        if not isinstance(issue, Mapping):
            return None
        key = _clean(issue.get("key"))
        return {
            "key": key,
            "url": _clean(issue.get("url") or issue.get("self")),
            "source": "provider_marker",
        }

    async def create_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
    ) -> dict[str, Any]:
        from moonmind.integrations.jira.models import CreateIssueRequest

        issue_type = _clean(rendered.fields.get("issueType")) or "Task"
        result = await self._jira.create_issue(
            CreateIssueRequest(
                projectKey=request.destination,
                issueTypeId=issue_type,
                summary=rendered.title,
                description=rendered.fields.get("description"),
                fields={
                    key: value
                    for key, value in rendered.fields.items()
                    if key not in {"projectKey", "issueType", "description"}
                },
            )
        )
        return {
            "external_key": result.get("issueKey"),
            "external_url": result.get("url") or result.get("self"),
        }

    async def update_issue(
        self,
        request: ProposalDeliveryRequest,
        rendered: RenderedProposalIssue,
        issue: Mapping[str, Any],
    ) -> dict[str, Any]:
        from moonmind.integrations.jira.models import EditIssueRequest

        issue_key = _clean(issue.get("key"))
        await self._jira.edit_issue(
            EditIssueRequest(
                issueKey=issue_key,
                fields={
                    key: value
                    for key, value in rendered.fields.items()
                    if key not in {"projectKey", "issueType"}
                },
                update={},
            )
        )
        return {
            "external_key": issue_key,
            "external_url": _clean(issue.get("url") or issue.get("self")) or None,
        }


def _clean(value: object) -> str:
    return str(value or "").strip()


def _provider_config(metadata: Mapping[str, Any], provider: str) -> dict[str, Any]:
    node = metadata.get(provider)
    return dict(node) if isinstance(node, Mapping) else {}


def _iter_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(_clean(item) for item in value if _clean(item))


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                return True
            if _contains_secret_key(child):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_contains_secret_key(item) for item in value)
    return False


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _safe_metadata(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def _safe_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if _SECRET_KEY_RE.search(str(key)):
            safe[str(key)] = "[REDACTED]"
        else:
            safe[str(key)] = _safe_value(value)
    return safe


def _marker(request: ProposalDeliveryRequest) -> str:
    snapshot = request.task_snapshot_ref or "stored-proposal-snapshot"
    return (
        "<!-- moonmind-proposal "
        f"record={request.record_id} dedup={request.dedup_hash} "
        f"snapshot={snapshot} -->"
    )


def _evidence_lines(request: ProposalDeliveryRequest) -> list[str]:
    metadata = request.origin_metadata
    lines: list[str] = []
    for label, key in (
        ("Workflow", "workflow_id"),
        ("Temporal run", "temporal_run_id"),
        ("Trigger repository", "trigger_repo"),
        ("Trigger job", "trigger_job_id"),
    ):
        value = _clean(metadata.get(key))
        if value:
            lines.append(f"- {label}: `{value}`")
    if request.task_snapshot_ref:
        lines.append(f"- Stored proposal snapshot: `{request.task_snapshot_ref}`")
    lines.append(f"- Dedup key: `{request.dedup_key}`")
    lines.append(f"- Dedup hash: `{request.dedup_hash}`")
    return lines


def _review_controls() -> str:
    return "\n".join(
        [
            "- `/moonmind promote` starts execution from the stored MoonMind snapshot.",
            "- `/moonmind dismiss <reason>` dismisses the proposal.",
            "- `/moonmind defer <date>` records a defer control.",
            "- `/moonmind priority <low|normal|high|urgent>` updates triage priority.",
            "- `/moonmind request-revision <reason>` records a revision request.",
        ]
    )


def _stored_snapshot_notice() -> str:
    return (
        "MoonMind executes the stored proposal snapshot. Edited issue text, "
        "comments, labels, or fields are review artifacts and are never used as "
        "replacement executable task payloads."
    )


def _text_to_adf_document(text: str) -> dict[str, Any]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    content: list[dict[str, Any]] = []
    for raw_paragraph in normalized.split("\n\n"):
        paragraph_content: list[dict[str, Any]] = []
        lines = raw_paragraph.split("\n")
        for index, line in enumerate(lines):
            if line:
                paragraph_content.append({"type": "text", "text": line})
            if index < len(lines) - 1:
                paragraph_content.append({"type": "hardBreak"})
        content.append({"type": "paragraph", "content": paragraph_content})
    return {"type": "doc", "version": 1, "content": content}


def render_github_issue(
    request: ProposalDeliveryRequest,
    *,
    redactor: SecretRedactor | None = None,
) -> RenderedProposalIssue:
    """Render a GitHub Issue body for proposal review."""

    redactor = redactor or SecretRedactor.from_environ(placeholder="[REDACTED]")
    provider_cfg = _provider_config(request.provider_metadata, "github")
    configured_labels = _iter_strings(provider_cfg.get("labels"))
    labels = tuple(dict.fromkeys(("moonmind-proposal", "proposal-review", *configured_labels)))
    title = f"[MoonMind proposal] {request.title}".strip()
    body = "\n\n".join(
        [
            _marker(request),
            f"## Proposal\n\n{request.summary}",
            (
                "## Review Context\n\n"
                f"- Repository: `{request.repository}`\n"
                f"- Category: `{request.category or 'uncategorized'}`\n"
                f"- Priority: `{request.priority}`\n"
                f"- Tags: `{', '.join(request.tags) or 'none'}`"
            ),
            "## Evidence Links\n\n" + "\n".join(_evidence_lines(request)),
            "## Reviewer Actions\n\n" + _review_controls(),
            "## Stored Snapshot Notice\n\n" + _stored_snapshot_notice(),
        ]
    )
    return RenderedProposalIssue(
        title=redactor.scrub(title),
        body=redactor.scrub(body),
        labels=labels,
        marker=_marker(request),
    )


def render_jira_issue(
    request: ProposalDeliveryRequest,
    *,
    redactor: SecretRedactor | None = None,
) -> RenderedProposalIssue:
    """Render Jira issue fields and ADF description for proposal review."""

    redactor = redactor or SecretRedactor.from_environ(placeholder="[REDACTED]")
    provider_cfg = _provider_config(request.provider_metadata, "jira")
    configured_labels = _iter_strings(provider_cfg.get("labels"))
    labels = tuple(dict.fromkeys(("moonmind-proposal", "proposal-review", *configured_labels)))
    summary = f"[MoonMind proposal] {request.title}".strip()
    description_text = "\n\n".join(
        [
            _marker(request),
            f"Proposal\n{request.summary}",
            (
                "Review Context\n"
                f"Repository: {request.repository}\n"
                f"Category: {request.category or 'uncategorized'}\n"
                f"Priority: {request.priority}\n"
                f"Tags: {', '.join(request.tags) or 'none'}"
            ),
            "Evidence Links\n" + "\n".join(_evidence_lines(request)),
            "Reviewer Actions\n" + _review_controls(),
            "Stored Snapshot Notice\n" + _stored_snapshot_notice(),
        ]
    )
    fields: dict[str, Any] = {
        "description": _text_to_adf_document(redactor.scrub(description_text)),
        "labels": list(labels),
    }
    project_key = _clean(provider_cfg.get("project_key") or provider_cfg.get("projectKey"))
    issue_type = _clean(provider_cfg.get("issue_type") or provider_cfg.get("issueType"))
    if project_key:
        fields["projectKey"] = project_key
    if issue_type:
        fields["issueType"] = issue_type
    return RenderedProposalIssue(
        title=redactor.scrub(summary),
        body=redactor.scrub(description_text),
        labels=labels,
        fields=fields,
        marker=_marker(request),
    )


def parse_provider_decision(event: ProviderDecisionEvent) -> ProviderDecisionResult:
    """Parse only bounded reviewer controls from provider events."""

    if not _clean(event.provider_event_id):
        return ProviderDecisionResult(
            accepted=False,
            decision=None,
            note=None,
            actor=event.actor,
            provider_event_id=event.provider_event_id,
            reason="missing_provider_event_id",
        )

    action = _normalize_decision_action(event.action)
    args = ""
    if not action:
        match = _COMMAND_RE.search(event.body or "")
        if match:
            action = _normalize_decision_action(match.group("action"))
            args = _clean(match.group("args"))
    if action not in _SUPPORTED_ACTIONS:
        return ProviderDecisionResult(
            accepted=False,
            decision=None,
            note=None,
            actor=event.actor,
            provider_event_id=event.provider_event_id,
            reason="unsupported_action",
        )
    runtime_mode = _clean(event.runtime_mode) or None
    if action == "promote" and args:
        runtime_mode, args = _extract_runtime_control(args, runtime_mode)
    note = _clean(event.note) or args or None
    priority: str | None = None
    defer_until: str | None = None
    if action == "reprioritize":
        candidate = _clean(args or event.note).lower()
        if candidate not in {"low", "normal", "high", "urgent"}:
            return ProviderDecisionResult(
                accepted=False,
                decision=None,
                note=None,
                actor=event.actor,
                provider_event_id=event.provider_event_id,
                reason="invalid_priority",
            )
        priority = candidate
    if action == "defer":
        defer_until = _clean(args or event.note) or None
    return ProviderDecisionResult(
        accepted=True,
        decision=action,
        note=note,
        actor=event.actor,
        provider_event_id=event.provider_event_id,
        priority=priority,
        defer_until=defer_until,
        runtime_mode=runtime_mode,
        external_state=_clean(event.external_state) or None,
    )


def _normalize_decision_action(action: object) -> str | None:
    token = _clean(action).lower().replace("_", "-")
    if not token:
        return None
    token = re.sub(r"\s+", " ", token)
    normalized = _ACTION_ALIASES.get(token, token.replace("-", "_"))
    return normalized if normalized in _SUPPORTED_ACTIONS else token


def _extract_runtime_control(args: str, existing: str | None) -> tuple[str | None, str]:
    parts = args.split()
    if not parts:
        return existing, ""
    runtime = existing
    remainder: list[str] = []
    skip = False
    for index, part in enumerate(parts):
        if skip:
            skip = False
            continue
        if part == "--runtime" and index + 1 < len(parts):
            runtime = _clean(parts[index + 1]) or runtime
            skip = True
            continue
        if part.startswith("--runtime="):
            runtime = _clean(part.split("=", 1)[1]) or runtime
            continue
        remainder.append(part)
    return runtime, " ".join(remainder)


def request_from_proposal(proposal: Any) -> ProposalDeliveryRequest:
    """Build a delivery request from a TaskProposal-like object."""

    return ProposalDeliveryRequest(
        record_id=str(getattr(proposal, "id", "")),
        provider=_clean(getattr(proposal, "provider", "github")).lower() or "github",
        repository=_clean(getattr(proposal, "repository", "")),
        title=_clean(getattr(proposal, "title", "")),
        summary=_clean(getattr(proposal, "summary", "")),
        category=getattr(proposal, "category", None),
        tags=tuple(str(tag) for tag in (getattr(proposal, "tags", None) or ())),
        priority=_clean(getattr(getattr(proposal, "review_priority", ""), "value", None))
        or _clean(getattr(proposal, "review_priority", "normal"))
        or "normal",
        dedup_key=_clean(getattr(proposal, "dedup_key", "")),
        dedup_hash=_clean(getattr(proposal, "dedup_hash", "")),
        task_snapshot_ref=getattr(proposal, "task_snapshot_ref", None),
        task_create_request=getattr(proposal, "task_create_request", None) or {},
        origin_metadata=getattr(proposal, "origin_metadata", None) or {},
        provider_metadata=getattr(proposal, "provider_metadata", None) or {},
        resolved_policy=getattr(proposal, "resolved_policy", None) or {},
        external_key=getattr(proposal, "external_key", None),
        external_url=getattr(proposal, "external_url", None),
    )


class ProposalDeliveryService:
    """Orchestrate dedup-first proposal delivery to provider issues."""

    def __init__(
        self,
        *,
        github: ProposalIssueProvider | None = None,
        jira: ProposalIssueProvider | None = None,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._providers = {"github": github, "jira": jira}
        self._redactor = redactor or SecretRedactor.from_environ(
            placeholder="[REDACTED]"
        )

    def _provider(self, provider: str) -> ProposalIssueProvider:
        selected = self._providers.get(provider)
        if selected is None:
            raise ProposalDeliveryError(
                f"{provider} proposal delivery is not configured",
                provider=provider,
                destination=provider,
                retryable=True,
                next_action="Configure the proposal delivery provider adapter.",
            )
        return selected

    def _validate_policy(self, request: ProposalDeliveryRequest) -> None:
        if request.provider not in {"github", "jira"}:
            raise ProposalDeliveryError(
                "provider must be github or jira",
                provider=request.provider or "unknown",
                destination=request.destination,
            )
        if _contains_secret_key(request.provider_metadata):
            raise ProposalDeliveryError(
                "provider metadata contains secret-like fields",
                provider=request.provider,
                destination=request.destination,
                next_action="Move credentials to the trusted provider secret boundary.",
            )
        policy = request.resolved_policy
        allowed_actions = set(_iter_strings(policy.get("allowedActions")))
        if allowed_actions and not allowed_actions.issubset(_SUPPORTED_ACTIONS):
            raise ProposalDeliveryError(
                "proposal reviewer actions are not allowed by policy",
                provider=request.provider,
                destination=request.destination,
            )
        if request.provider == "github":
            destination = request.destination
            if not _GITHUB_REPO_RE.match(destination):
                raise ProposalDeliveryError(
                    "GitHub proposal destination must be owner/repo",
                    provider=request.provider,
                    destination=destination,
                )
            allowed_repositories = set(_iter_strings(policy.get("allowedRepositories")))
            if allowed_repositories and destination.lower() not in {
                repo.lower() for repo in allowed_repositories
            }:
                raise ProposalDeliveryError(
                    "GitHub repository is not allowed by proposal delivery policy",
                    provider=request.provider,
                    destination=destination,
                )
            allowed_orgs = set(_iter_strings(policy.get("allowedOrganizations")))
            org = destination.split("/", 1)[0].lower()
            if allowed_orgs and org not in {item.lower() for item in allowed_orgs}:
                raise ProposalDeliveryError(
                    "GitHub organization is not allowed by proposal delivery policy",
                    provider=request.provider,
                    destination=destination,
                )
        else:
            project = request.destination
            allowed_projects = set(_iter_strings(policy.get("allowedProjects")))
            if allowed_projects and project.upper() not in {
                item.upper() for item in allowed_projects
            }:
                raise ProposalDeliveryError(
                    "Jira project is not allowed by proposal delivery policy",
                    provider=request.provider,
                    destination=project,
                )

    def render(self, request: ProposalDeliveryRequest) -> RenderedProposalIssue:
        if request.provider == "jira":
            return render_jira_issue(request, redactor=self._redactor)
        return render_github_issue(request, redactor=self._redactor)

    async def deliver(self, request: ProposalDeliveryRequest) -> ProposalDeliveryResult:
        self._validate_policy(request)
        provider = self._provider(request.provider)
        rendered = self.render(request)
        existing: Mapping[str, Any] | None = None
        if request.external_key or request.external_url:
            existing = {
                "key": request.external_key,
                "url": request.external_url,
                "source": "local_record",
            }
        if existing is None:
            existing = await provider.search_issue(request)
        if existing:
            payload = await provider.update_issue(request, rendered, existing)
            created = False
            duplicate_source = _clean(existing.get("source")) or "provider"
        else:
            payload = await provider.create_issue(request, rendered)
            created = True
            duplicate_source = None
        external_key = _clean(
            payload.get("external_key")
            or payload.get("externalKey")
            or payload.get("key")
            or payload.get("number")
            or request.external_key
        )
        external_url = _clean(
            payload.get("external_url")
            or payload.get("externalUrl")
            or payload.get("url")
            or payload.get("html_url")
            or request.external_url
        )
        if not external_key or not external_url:
            raise ProposalDeliveryError(
                "provider delivery did not return an external issue identity",
                provider=request.provider,
                destination=request.destination,
                retryable=True,
                next_action="Retry delivery after checking provider adapter response mapping.",
            )
        provider_metadata = _safe_metadata(
            {
                "marker": rendered.marker,
                "labels": list(rendered.labels),
                "created": created,
                "duplicateSource": duplicate_source,
            }
        )
        return ProposalDeliveryResult(
            provider=request.provider,
            external_key=external_key,
            external_url=external_url,
            created=created,
            duplicate_source=duplicate_source,
            provider_metadata=provider_metadata,
        )


__all__ = [
    "ProposalDeliveryError",
    "ProposalDeliveryRequest",
    "ProposalDeliveryResult",
    "ProposalDeliveryService",
    "ProposalIssueProvider",
    "GitHubProposalIssueProvider",
    "JiraProposalIssueProvider",
    "ProviderDecisionEvent",
    "ProviderDecisionResult",
    "RenderedProposalIssue",
    "parse_provider_decision",
    "render_github_issue",
    "render_jira_issue",
    "request_from_proposal",
]
