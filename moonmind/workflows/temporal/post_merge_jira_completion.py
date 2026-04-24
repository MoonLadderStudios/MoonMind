"""Deterministic helpers for post-merge Jira completion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from moonmind.utils.logging import SecretRedactor, redact_sensitive_text

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b", re.IGNORECASE)
_CREDENTIAL_KEYS = {
    "authorization",
    "auth",
    "cookie",
    "password",
    "token",
    "access_token",
    "refresh_token",
}

@dataclass(frozen=True, slots=True)
class JiraIssueCandidate:
    issue_key: str
    source: str

@dataclass(frozen=True, slots=True)
class PostMergeJiraCompletionConfig:
    enabled: bool = True
    issueKey: str | None = None
    transitionId: str | None = None
    transitionName: str | None = None
    strategy: str = "done_category"
    required: bool = True
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "PostMergeJiraCompletionConfig":
        raw = payload if isinstance(payload, Mapping) else {}
        return cls(
            enabled=_coerce_bool(raw.get("enabled"), default=True),
            issueKey=_normalize_issue_key(raw.get("issueKey") or raw.get("issue_key")),
            transitionId=_normalize_text(
                raw.get("transitionId") or raw.get("transition_id")
            ),
            transitionName=_normalize_text(
                raw.get("transitionName") or raw.get("transition_name")
            ),
            strategy=_normalize_text(raw.get("strategy")) or "done_category",
            required=_coerce_bool(raw.get("required"), default=True),
            fields=dict(raw.get("fields") or {}) if isinstance(raw.get("fields"), Mapping) else {},
        )

@dataclass(frozen=True, slots=True)
class PostMergeJiraCompletionDecision:
    status: str
    required: bool
    issueResolution: dict[str, Any]
    transition: dict[str, Any] | None = None
    alreadyDone: bool = False
    transitioned: bool = False
    reason: str | None = None
    artifactRefs: dict[str, Any] = field(default_factory=dict)

    def to_summary(self) -> dict[str, Any]:
        resolution = _sanitize_mapping(self.issueResolution)
        transition = _sanitize_mapping(self.transition or {})
        payload: dict[str, Any] = {
            "status": self.status,
            "required": self.required,
            "issueKey": resolution.get("issueKey"),
            "issueKeySource": resolution.get("source"),
            "alreadyDone": self.alreadyDone,
            "transitioned": self.transitioned,
            "reason": self.reason,
        }
        if transition:
            payload["transitionId"] = transition.get("transitionId")
            payload["transitionName"] = transition.get("transitionName")
            payload["toStatusName"] = transition.get("toStatusName")
            payload["toStatusCategory"] = transition.get("toStatusCategory")
        artifact_refs = _sanitize_mapping(self.artifactRefs)
        if artifact_refs:
            payload["artifactRefs"] = artifact_refs
        return {key: value for key, value in payload.items() if value is not None}

    def model_dump(self, *, by_alias: bool = True, mode: str = "json") -> dict[str, Any]:
        del by_alias, mode
        payload = self.to_summary()
        payload["issueResolution"] = _sanitize_mapping(self.issueResolution)
        if self.transition is not None:
            payload["transition"] = _sanitize_mapping(self.transition)
        return payload

def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None

def _normalize_issue_key(value: Any) -> str | None:
    candidate = _normalize_text(value)
    if not candidate:
        return None
    match = _JIRA_KEY_RE.fullmatch(candidate)
    if not match:
        return None
    return match.group(1).upper()

def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default

def candidate_keys_from_payload(payload: Mapping[str, Any]) -> list[JiraIssueCandidate]:
    config = PostMergeJiraCompletionConfig.from_payload(
        payload.get("postMergeJira") if isinstance(payload, Mapping) else {}
    )
    explicit = _normalize_issue_key(config.issueKey)
    if explicit:
        return [JiraIssueCandidate(issue_key=explicit, source="explicit_post_merge")]

    candidates: list[JiraIssueCandidate] = []
    _append_candidate(candidates, payload.get("jiraIssueKey"), "merge_automation")
    context = payload.get("candidateContext")
    if isinstance(context, Mapping):
        _append_candidate(candidates, context.get("taskOriginIssueKey"), "task_origin")
        _append_candidate(candidates, context.get("taskMetadataIssueKey"), "task_metadata")
        _append_candidate(candidates, context.get("publishContextIssueKey"), "publish_context")
        for item in context.get("prMetadataKeys") or []:
            _append_candidate(candidates, item, "pr_metadata")
    pull_request = payload.get("pullRequest")
    if isinstance(pull_request, Mapping):
        for key in ("title", "body", "headBranch"):
            for match in _JIRA_KEY_RE.findall(str(pull_request.get(key) or "")):
                _append_candidate(candidates, match, "pr_metadata")
    return candidates

def _append_candidate(
    candidates: list[JiraIssueCandidate],
    value: Any,
    source: str,
) -> None:
    issue_key = _normalize_issue_key(value)
    if issue_key:
        candidates.append(JiraIssueCandidate(issue_key=issue_key, source=source))

async def resolve_issue_key(
    candidates: list[JiraIssueCandidate],
    *,
    get_issue: Callable[[str], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if not candidates:
        return {
            "status": "missing",
            "issueKey": None,
            "source": None,
            "candidates": [],
            "reason": "No authoritative Jira issue key was found.",
        }

    evidence: list[dict[str, Any]] = []
    candidates_by_key: dict[str, list[JiraIssueCandidate]] = {}
    ordered_keys: list[str] = []
    for candidate in candidates:
        issue_key = _normalize_issue_key(candidate.issue_key)
        if not issue_key:
            evidence.append(
                {
                    "issueKey": str(candidate.issue_key or ""),
                    "source": candidate.source,
                    "validated": False,
                    "reason": "Malformed Jira issue key.",
                }
            )
            continue
        if issue_key not in candidates_by_key:
            candidates_by_key[issue_key] = []
            ordered_keys.append(issue_key)
        candidates_by_key[issue_key].append(candidate)

    by_key: dict[str, dict[str, Any]] = {}
    for issue_key in ordered_keys:
        grouped_candidates = candidates_by_key[issue_key]
        first_candidate = grouped_candidates[0]
        try:
            issue = await get_issue(issue_key)
            status = _issue_status(issue)
            item = {
                "issueKey": issue_key,
                "source": first_candidate.source,
                "validated": True,
                "statusName": status.get("name"),
                "statusCategory": status.get("category"),
            }
        except Exception as exc:
            item = {
                "issueKey": issue_key,
                "source": first_candidate.source,
                "validated": False,
                "reason": _scrub_exception(exc)[:300],
            }
        for candidate in grouped_candidates:
            candidate_item = dict(item)
            candidate_item["source"] = candidate.source
            evidence.append(candidate_item)
        if item.get("validated") and issue_key not in by_key:
            by_key[issue_key] = item

    if not by_key:
        return {
            "status": "invalid",
            "issueKey": None,
            "source": None,
            "candidates": evidence,
            "reason": "No candidate Jira issue key validated through the trusted Jira service.",
        }
    if len(by_key) > 1:
        return {
            "status": "ambiguous",
            "issueKey": None,
            "source": None,
            "candidates": evidence,
            "reason": "Multiple validated Jira issue keys were found.",
        }
    selected = next(iter(by_key.values()))
    return {
        "status": "resolved",
        "issueKey": selected["issueKey"],
        "source": selected["source"],
        "candidates": evidence,
        "reason": None,
    }

def _transition_target(transition: Mapping[str, Any]) -> Mapping[str, Any]:
    target = transition.get("to")
    if not isinstance(target, Mapping):
        return {}
    return target

def _transition_to_status_name(transition: Mapping[str, Any]) -> str:
    return str(_transition_target(transition).get("name") or "")

def _read_failure_decision(
    *,
    required: bool,
    issue_resolution: Mapping[str, Any],
    exc: Exception,
) -> PostMergeJiraCompletionDecision:
    return PostMergeJiraCompletionDecision(
        status="failed",
        required=required,
        issueResolution=dict(issue_resolution),
        reason=_scrub_exception(exc)[:500],
    )

def _scrub_exception(exc: Exception) -> str:
    return redact_sensitive_text(SecretRedactor().scrub(str(exc)))

def select_done_transition(
    *,
    issue: Mapping[str, Any],
    transitions: list[Mapping[str, Any]],
    config: PostMergeJiraCompletionConfig,
) -> dict[str, Any]:
    issue_status = _issue_status(issue)
    if _is_done_category(issue_status.get("category")):
        return {"status": "noop_already_done", "transition": None, "reason": None}

    if config.transitionId:
        matching = [
            item for item in transitions if str(item.get("id") or "").strip() == config.transitionId
        ]
        if not matching:
            return {
                "status": "blocked",
                "transition": None,
                "reason": "Configured Jira transitionId is not currently available.",
            }
        return _transition_selection(matching[0], config=config)

    if config.transitionName:
        target = config.transitionName.strip().lower()
        matching = [
            item for item in transitions if str(item.get("name") or "").strip().lower() == target
        ]
        if len(matching) != 1:
            return {
                "status": "blocked",
                "transition": None,
                "reason": "Configured Jira transitionName is not uniquely available.",
            }
        return _transition_selection(matching[0], config=config)

    done_transitions = [
        item for item in transitions if _is_done_category(_transition_category(item))
    ]
    if len(done_transitions) != 1:
        return {
            "status": "blocked",
            "transition": None,
            "reason": "Expected exactly one done-category Jira transition.",
        }
    return _transition_selection(done_transitions[0], config=config)

def _transition_selection(
    transition: Mapping[str, Any],
    *,
    config: PostMergeJiraCompletionConfig,
) -> dict[str, Any]:
    missing = _missing_required_fields(transition, config.fields)
    if missing:
        return {
            "status": "blocked",
            "transition": None,
            "reason": f"Jira transition has required field(s) without configured defaults: {', '.join(missing)}",
        }
    return {
        "status": "selected",
        "transition": {
            "transitionId": str(transition.get("id") or ""),
            "transitionName": str(transition.get("name") or ""),
            "toStatusName": _transition_to_status_name(transition),
            "toStatusCategory": _transition_category(transition),
        },
        "reason": None,
    }

def _missing_required_fields(
    transition: Mapping[str, Any],
    configured_fields: Mapping[str, Any],
) -> list[str]:
    raw_fields = transition.get("fields")
    if not isinstance(raw_fields, Mapping):
        return []
    missing: list[str] = []
    for field_id, field_meta in raw_fields.items():
        if not isinstance(field_meta, Mapping):
            continue
        if field_meta.get("required") and field_id not in configured_fields:
            missing.append(str(field_id))
    return missing

async def complete_post_merge_jira(
    payload: Mapping[str, Any],
    *,
    get_issue: Callable[[str], Awaitable[dict[str, Any]]],
    get_transitions: Callable[[str], Awaitable[list[dict[str, Any]]]],
    transition_issue: Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> PostMergeJiraCompletionDecision:
    config = PostMergeJiraCompletionConfig.from_payload(payload.get("postMergeJira"))
    candidates = candidate_keys_from_payload(payload)
    if not config.enabled:
        return PostMergeJiraCompletionDecision(
            status="skipped",
            required=False,
            issueResolution={"status": "missing", "candidates": []},
            reason="Post-merge Jira completion is disabled.",
        )
    if config.strategy != "done_category":
        return PostMergeJiraCompletionDecision(
            status="blocked",
            required=config.required,
            issueResolution={"status": "invalid", "candidates": []},
            reason=f"Unsupported post-merge Jira strategy: {config.strategy}",
        )

    resolution = await resolve_issue_key(candidates, get_issue=get_issue)
    if resolution.get("status") != "resolved":
        return PostMergeJiraCompletionDecision(
            status="blocked",
            required=config.required,
            issueResolution=resolution,
            reason=str(resolution.get("reason") or "Jira issue could not be resolved."),
        )

    issue_key = str(resolution["issueKey"])
    try:
        issue = await get_issue(issue_key)
        transitions = await get_transitions(issue_key)
    except Exception as exc:
        return _read_failure_decision(
            required=config.required,
            issue_resolution=resolution,
            exc=exc,
        )
    selected = select_done_transition(issue=issue, transitions=transitions, config=config)
    if selected["status"] == "noop_already_done":
        return PostMergeJiraCompletionDecision(
            status="noop_already_done",
            required=config.required,
            issueResolution=resolution,
            alreadyDone=True,
            reason="Jira issue is already in a done-category status.",
        )
    if selected["status"] != "selected":
        return PostMergeJiraCompletionDecision(
            status="blocked",
            required=config.required,
            issueResolution=resolution,
            reason=str(selected.get("reason") or "No safe Jira transition was selected."),
        )

    transition = selected["transition"]
    try:
        await transition_issue(issue_key, str(transition["transitionId"]), config.fields)
    except Exception as exc:
        return PostMergeJiraCompletionDecision(
            status="failed",
            required=config.required,
            issueResolution=resolution,
            transition=transition,
            reason=_scrub_exception(exc)[:500],
        )
    return PostMergeJiraCompletionDecision(
        status="succeeded",
        required=config.required,
        issueResolution=resolution,
        transition=transition,
        transitioned=True,
    )

def _issue_status(issue: Mapping[str, Any]) -> dict[str, str | None]:
    fields = issue.get("fields") if isinstance(issue, Mapping) else {}
    status = fields.get("status") if isinstance(fields, Mapping) else {}
    if not isinstance(status, Mapping):
        status = {}
    category = status.get("statusCategory")
    if not isinstance(category, Mapping):
        category = {}
    return {
        "name": _normalize_text(status.get("name")),
        "category": _normalize_text(category.get("key") or category.get("name")),
    }

def _transition_category(transition: Mapping[str, Any]) -> str | None:
    target = _transition_target(transition)
    category = target.get("statusCategory")
    if not isinstance(category, Mapping):
        return None
    return _normalize_text(category.get("key") or category.get("name"))

def _is_done_category(value: Any) -> bool:
    return str(value or "").strip().lower() == "done"

def _sanitize_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        normalized_key = str(key)
        if normalized_key.lower() in _CREDENTIAL_KEYS:
            continue
        if isinstance(value, Mapping):
            result[normalized_key] = _sanitize_mapping(value)
        elif isinstance(value, list):
            result[normalized_key] = [
                _sanitize_mapping(item) if isinstance(item, Mapping) else item
                for item in value[:20]
            ]
        else:
            result[normalized_key] = value
    return result
