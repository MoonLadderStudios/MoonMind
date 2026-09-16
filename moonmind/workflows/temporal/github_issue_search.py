"""Bounded GitHub issue selection at the trusted issue-loading Activity boundary."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from markdown_it import MarkdownIt

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.github_issue_admission import (
    ENTRYPOINT_SEARCH,
    admit_for_entrypoint,
    recandidate_after_abandon,
)
from moonmind.workflows.temporal.github_issue_lifecycle import (
    ELIGIBLE_SETTLED_STATES,
    SETTLED_RECOVERY_NEEDED,
    attempt_evidence_blocks_admission,
    interpret_issue,
)
from moonmind.workflows.temporal.issue_claim_store import ActiveIssueClaimConflict


#: An explicit, declared dependency convention. "Blocked from starting" means
#: no useful implementation can begin; "blocked from completing" means the work
#: can proceed but final acceptance or closure waits. An issue whose body says
#: its *completion* depends on other issues can still receive the independently
#: useful work it also describes.
PREREQUISITE_SCOPE_START = "start"
PREREQUISITE_SCOPE_COMPLETION = "completion"

# Only a declaration whose own words name completion is completion-scoped.
# "Integration prerequisites", "Depends on", "Blocked by", and child lists keep
# their established start-blocking meaning until each is reviewed and migrated.
_COMPLETION_KEYWORDS = ("completion depends on",)

_PREREQUISITE_DECLARATION_RE = re.compile(
    r"(?P<keyword>\bCompletion depends on|\bIntegration prerequisites:|"
    r"(?:^|(?<=[.!?]))[ \t]*(?:[-*] )?(?:Depends on|Blocked by):?)\s*"
    r"(?P<refs>.+?)(?=\.(?:\s|$)|\n|$)",
    re.IGNORECASE | re.MULTILINE,
)


def _declaration_scope(keyword: str) -> str:
    normalized = keyword.strip().lstrip("-* \t").casefold()
    return (
        PREREQUISITE_SCOPE_COMPLETION
        if normalized.startswith(_COMPLETION_KEYWORDS)
        else PREREQUISITE_SCOPE_START
    )


@dataclass
class PrerequisiteLookup:
    """Bound and reuse validated prerequisite reads within one admission scan."""

    states: dict[tuple[str, int], str] = field(default_factory=dict)
    requests: int = 0
    completion_dependencies: list[dict[str, Any]] = field(default_factory=list)


def declared_dependencies(body: str, repository: str) -> dict[tuple[str, int], str]:
    """Read declared dependencies and child lists, never contextual issue links.

    Each reference carries its declared scope. Only a declaration whose own
    words name completion (``Completion depends on``) is completion-scoped;
    everything else keeps its established start-blocking meaning, so no
    existing declaration is silently reinterpreted. Child lists and other
    conventions move deliberately, declaration by declaration.
    """
    declarations = [
        (match.group("refs"), _declaration_scope(match.group("keyword")), False)
        for match in _PREREQUISITE_DECLARATION_RE.finditer(body)
    ]
    # Child lists declare dependencies even when a stale checkbox claims
    # completion. Their state is resolved by the same GitHub lookup as
    # sentence declarations, with one shared identity cache and request budget.
    child_section_level: int | None = None
    tokens = MarkdownIt("commonmark").parse(body)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.level == 0:
            level = int(token.tag[1:])
            if child_section_level is not None and level <= child_section_level:
                child_section_level = None
            if child_section_level is None and tokens[index + 1].content.casefold() in {
                "child issues",
                "sub-issues",
            }:
                child_section_level = level
        elif (
            child_section_level is not None
            and token.type == "inline"
            and index >= 2
            and tokens[index - 1].type == "paragraph_open"
            and tokens[index - 2].type == "list_item_open"
        ):
            # Only the first paragraph of a rendered list item declares a
            # child. Markdown parsing excludes code and HTML-comment examples
            # while retaining legal heading indentation and nested sections.
            entry = re.sub(r"^\[[ xX]\]\s+", "", token.content)
            declarations.append((entry, PREREQUISITE_SCOPE_START, True))
    scopes: dict[tuple[str, int], str] = {}
    for declaration, scope, is_child in declarations:
        text = re.sub(
            r"\[[^\]\n]*\]\((https://github\.com/[\w.-]+/[\w.-]+/issues/[1-9]\d*)\)",
            r"\1",
            declaration,
        )
        text = re.sub(
            r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)",
            r"\1#\2",
            text,
        )
        if is_child:
            # A spaced dash introduces a title, including numeric titles such
            # as "2FA support". A spaced range must name its endpoint with #.
            text = re.split(r"\s+[-\u2013\u2014]\s+(?![\s#])", text, maxsplit=1)[0]
        # Consume only the leading reference list. Prose after the list can
        # contain parent, related, or other contextual issue references.
        text = re.sub(r"^issues?\s+", "", text, flags=re.IGNORECASE)
        while match := re.match(
            r"(?:([\w.-]+/[\w.-]+))?#([1-9]\d*)"
            r"(?:\s*[-\u2013\u2014]\s*#?([1-9]\d*))?",
            text,
        ):
            start = int(match.group(2))
            end = int(match.group(3) or start)
            if end < start or end - start >= 100:
                raise ValueError(
                    "GitHub prerequisite range is invalid or exceeds 100 issues."
                )
            for number in range(start, end + 1):
                key = (match.group(1) or repository, number)
                # A reference declared both ways keeps the stronger meaning:
                # nothing useful can start until it is resolved.
                if scopes.get(key) != PREREQUISITE_SCOPE_START:
                    scopes[key] = scope
            text = text[match.end():].lstrip(" \t,;")
            text = re.sub(r"^(?:and\b|&)\s*", "", text, flags=re.IGNORECASE)
    if len(scopes) > 100:
        raise ValueError("GitHub prerequisite declaration exceeds 100 issues.")
    return scopes


def declared_prerequisites(body: str, repository: str) -> list[tuple[str, int]]:
    """References that must be resolved before implementation can start."""
    return [
        key
        for key, scope in declared_dependencies(body, repository).items()
        if scope == PREREQUISITE_SCOPE_START
    ]


def declared_completion_dependencies(
    body: str, repository: str
) -> list[tuple[str, int]]:
    """References that gate acceptance or closure, not the start of work."""
    return [
        key
        for key, scope in declared_dependencies(body, repository).items()
        if scope == PREREQUISITE_SCOPE_COMPLETION
    ]


async def check_prerequisites(
    *,
    issue: Mapping[str, Any],
    repository: str,
    github_service: GitHubService,
    lookup: PrerequisiteLookup | None = None,
    include_completion: bool = False,
) -> list[dict[str, Any]]:
    """Resolve prerequisite state through authenticated GitHub reads only.

    Only an open start-blocking prerequisite denies selection. Open completion
    dependencies are recorded on ``lookup.completion_dependencies`` (when
    ``include_completion`` authorizes the extra reads) so the completion gate
    can hold closure without stopping independently useful implementation.
    """
    scopes = declared_dependencies(str(issue.get("body") or ""), repository)
    refs = [
        (repo, number, scope)
        for (repo, number), scope in scopes.items()
        if include_completion or scope == PREREQUISITE_SCOPE_START
    ]
    refs.sort(key=lambda ref: ref[2] != PREREQUISITE_SCOPE_START)
    if not refs:
        return []
    lookup = lookup if lookup is not None else PrerequisiteLookup()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for dependency_repo, number, scope in refs:
            key = (dependency_repo.casefold(), number)
            prerequisite_state = lookup.states.get(key)
            if prerequisite_state is None:
                if lookup.requests >= 100:
                    raise ValueError(
                        "GitHub issue selection exceeded the 100-request prerequisite lookup budget; "
                        "select an explicit issue or narrow the issue search."
                    )
                token, _error = await github_service.resolve_github_token(
                    repo=dependency_repo
                )
                if not token:
                    raise ValueError("GitHub prerequisite lookup is unavailable.")
                lookup.requests += 1
                try:
                    response = await client.get(
                        f"https://api.github.com/repos/{dependency_repo}/issues/{number}",
                        headers=github_service._github_headers(token),
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ValueError("GitHub prerequisite lookup failed.") from exc
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("state") not in {"open", "closed"}
                    or payload.get("number") != number
                    or not is_complete_open_issue(
                        {**payload, "state": "open"}, dependency_repo
                    )
                ):
                    raise ValueError("GitHub prerequisite identity or state is invalid.")
                lookup.states[key] = payload["state"]
                prerequisite_state = payload["state"]
            if prerequisite_state != "open":
                continue
            if scope == PREREQUISITE_SCOPE_START:
                return [_prerequisite_blocker(dependency_repo, number)]
            dependency = _prerequisite_blocker(
                dependency_repo, number, scope=PREREQUISITE_SCOPE_COMPLETION
            )
            if dependency not in lookup.completion_dependencies:
                lookup.completion_dependencies.append(dependency)
    return []


def _prerequisite_blocker(
    repository: str, number: int, *, scope: str = PREREQUISITE_SCOPE_START
) -> dict[str, Any]:
    return {
        "source": "prerequisite",
        "repository": repository,
        "number": number,
        "scope": scope,
        "statusKnown": True,
        "done": False,
    }


def is_complete_open_issue(payload: Any, repository: str) -> bool:
    """Validate raw GitHub evidence before normalization can hide missing fields."""

    if not isinstance(payload, Mapping) or "pull_request" in payload:
        return False
    number = payload.get("number")
    labels = payload.get("labels")
    return (
        type(number) is int
        and number > 0
        and payload.get("state") == "open"
        and str(payload.get("html_url")).casefold()
        == f"https://github.com/{repository}/issues/{number}".casefold()
        and isinstance(payload.get("title"), str)
        and bool(payload["title"].strip())
        and "body" in payload
        and isinstance(payload["body"], (str, type(None)))
        and isinstance(labels, list)
        and all(
            isinstance(label, Mapping)
            and isinstance(label.get("name"), str)
            and bool(label["name"].strip())
            for label in labels
        )
    )


_IN_PROGRESS_LABELS = frozenset(
    {
        "status: in-progress",
        "status:in-progress",
        "status/in-progress",
        "status_in-progress",
        "status in-progress",
        "status: in progress",
        "status: inprogress",
        "in-progress",
        "in_progress",
        "in progress",
    }
)


def has_in_progress_status(issue: Mapping[str, Any]) -> bool:
    """Return True when the issue already carries an in-progress status label.

    Conservative retained-history input: exact canonical ``status: in-progress``
    and historical alias spellings all count as in-progress so old selectors
    never silently reselect them. New admission logic lives in
    :func:`is_lifecycle_selectable_candidate`.
    """
    labels = issue.get("labels")
    if not isinstance(labels, list):
        return False
    for label in labels:
        if isinstance(label, Mapping):
            name = label.get("name")
        else:
            name = label
        if not isinstance(name, str):
            continue
        if name.strip().lower() in _IN_PROGRESS_LABELS:
            return True
    return False


def is_lifecycle_selectable_candidate(
    issue: Mapping[str, Any],
    attempt_context: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when the shared lifecycle policy admits the candidate.

    Only Available (fresh) and Recovery-needed (continuation) settled states
    are selectable, and supplied unresolved active-attempt evidence always
    blocks admission even when the in-progress label is missing.
    """
    labels = issue.get("labels")
    if isinstance(labels, list):
        names: list[Any] = []
        for label in labels:
            names.append(label.get("name") if isinstance(label, Mapping) else label)
    else:
        names = []
    interpretation = interpret_issue(
        {"state": issue.get("state", "open"), "labels": names}
    )
    if interpretation.settled not in ELIGIBLE_SETTLED_STATES:
        return False
    if attempt_evidence_blocks_admission(attempt_context):
        return False
    return True


def _recovery_handoff_usable(handoff: Mapping[str, Any] | None) -> bool:
    """Return True when supplied handoff evidence can drive a continuation."""
    if not isinstance(handoff, Mapping):
        return False

    def _truthy(value: Any) -> bool:
        if value is True:
            return True
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return bool(value)
        return False

    stopped = handoff.get("predecessor_stopped", handoff.get("predecessorStopped"))
    usable = handoff.get("handoff_usable", handoff.get("handoffUsable"))
    return _truthy(stopped) and _truthy(usable)


_AUTHOR_QUALIFIER_RE = re.compile(
    r"(?P<neg>[-+])?\bauthor\s*:\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s()]+)",
    re.IGNORECASE,
)


def _is_inside_quotes(text: str, pos: int) -> bool:
    """Return True when *pos* sits inside a single/double-quoted literal."""
    in_single = False
    in_double = False
    for ch in text[:pos]:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
    return in_single or in_double


def _strip_author_qualifiers(query: str) -> tuple[str, list[dict[str, Any]]]:
    """Remove backend-recognized author qualifiers outside quoted literals."""
    found: list[dict[str, Any]] = []

    def _replace(match: re.Match[str]) -> str:
        if _is_inside_quotes(query, match.start()):
            return match.group(0)
        found.append(
            {
                "raw": match.group(0),
                "negated": bool(match.group("neg")) and match.group("neg") == "-",
                "value": match.group("value"),
            }
        )
        return " "

    cleaned = _AUTHOR_QUALIFIER_RE.sub(_replace, query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, found


def _normalize_author_value(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def _validated_author_scope(
    *,
    query: str,
    authenticated_login: str,
) -> tuple[str, dict[str, Any] | None]:
    """Apply the self-only author constraint or return a field error.

    Returns ``(effective_query, error)`` where error carries ``reasonCode``
    ``conflicting_author_filter`` and ``field`` ``issueSearch`` when the
    user's own author qualifiers conflict with the required scope.
    """
    cleaned, qualifiers = _strip_author_qualifiers(query)
    if not qualifiers:
        return f"({query.strip()}) author:{authenticated_login}" if query.strip() else f"author:{authenticated_login}", None
    for qualifier in qualifiers:
        raw_value = _normalize_author_value(str(qualifier["value"]))
        if not raw_value:
            return "", {
                "reasonCode": "conflicting_author_filter",
                "field": "issueSearch",
                "error": (
                    "Unsupported author qualifier in the GitHub issue search field. "
                    'Remove the author: filter or explicitly enable "Include issues '
                    'created by other users" to search other accounts.'
                ),
            }
        negated = bool(qualifier["negated"])
        is_self = raw_value.casefold() in {"@me"} or raw_value.casefold() == authenticated_login.casefold()
        if negated or not is_self:
            return "", {
                "reasonCode": "conflicting_author_filter",
                "field": "issueSearch",
                "error": (
                    "The GitHub issue search field contains an author filter for another "
                    'account. Remove it or explicitly enable "Include issues created by '
                    'other users" to search other accounts.'
                ),
            }
    # All qualifiers reference the same authenticated account: normalize to a
    # single constraint without duplicates.
    if cleaned:
        return f"({cleaned}) author:{authenticated_login}", None
    return f"author:{authenticated_login}", None


def _selected_author_identity(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    user = candidate.get("user")
    if not isinstance(user, Mapping):
        return None
    user_id = user.get("id")
    login = user.get("login")
    if type(user_id) is not int or user_id <= 0:
        return None
    if not isinstance(login, str) or not login.strip():
        return None
    return {"id": user_id, "login": login.strip()}


def _record_rejection(
    counts: dict[str, Any], issue_number: int, reason: str, **details: Any
) -> None:
    """Retain bounded diagnostic samples without changing admission authority."""
    reasons = counts.setdefault("rejectionCounts", {})
    reasons[reason] = reasons.get(reason, 0) + 1
    samples = counts.setdefault("rejectedCandidates", [])
    # Keep owner diagnostics visible even behind a full page of label rejects.
    if len(samples) >= 20 and reason in CLAIM_EVIDENCE_EXCLUSIONS:
        for index, sample in enumerate(samples):
            if sample["reasonCode"] not in CLAIM_EVIDENCE_EXCLUSIONS:
                samples.pop(index)
                break
    if len(samples) < 20:
        samples.append({"issueNumber": issue_number, "reasonCode": reason, **details})
    counts["rejectedCandidatesTruncated"] = sum(reasons.values()) > len(samples)


#: Actionable distinctions for a candidate that still carries an advisory
#: in-progress label after reassessment. Diagnostics only; never GitHub labels.
_RESERVATION_EXCLUSION_REASONS = {
    "live_or_legacy_owner": "live_or_legacy_reservation",
    "successor_observed": "live_reservation",
    "claim_changed": "reservation_changed",
    "settled_status_retained": "lifecycle_ineligible",
    "operator_hold": "operator_hold",
    "claim_read_failure": "github_evidence_unavailable",
    "claim_actor_unavailable": "github_evidence_unavailable",
    "untrusted_claim_poster": "untrusted_claim_evidence",
    "claim_evidence_incomplete": "github_evidence_unavailable",
    "conflicting_attempt_copies": "conflicting_attempt_copies",
}


def reservation_exclusion_reason(reconciliation: Mapping[str, Any] | None) -> str:
    """Report why an advisory status label survived reassessment."""
    if not reconciliation:
        return "stale_status_label_unreconciled"
    return _RESERVATION_EXCLUSION_REASONS.get(
        str(reconciliation.get("reasonCode") or ""), "live_or_legacy_reservation"
    )


#: Exclusions backed by a specific attempt record, which therefore name an
#: owner worth keeping in the bounded diagnostic sample.
CLAIM_EVIDENCE_EXCLUSIONS = frozenset(
    {
        "active_attempt_conflict",
        "live_reservation",
        "legacy_reservation_awaiting_migration",
        "operator_hold",
        "conflicting_attempt_copies",
    }
)

#: Exclusions that mean "someone else is reserving this issue right now",
#: as opposed to lifecycle, author, or prerequisite exclusions.
RESERVATION_EXCLUSIONS = frozenset(
    {
        "active_attempt_conflict",
        "live_reservation",
        "live_or_legacy_reservation",
        "legacy_reservation_awaiting_migration",
        "operator_hold",
        "reservation_changed",
        "stale_status_label_unreconciled",
    }
)


def _exclusion_summary(counts: Mapping[str, Any]) -> str:
    reasons = counts.get("rejectionCounts", {})
    if not reasons:
        return ""
    summary = (
        " Exclusions: "
        + ", ".join(
            f"{reason.replace('_', ' ')}: {count}"
            for reason, count in sorted(reasons.items())
        )
        + "."
    )
    conflicts = sum(reasons.get(reason, 0) for reason in RESERVATION_EXCLUSIONS)
    if conflicts:
        samples = counts.get("rejectedCandidates", [])
        examples = [
            f"#{item['issueNumber']}"
            for item in samples
            if item["reasonCode"] in CLAIM_EVIDENCE_EXCLUSIONS
        ] or [
            f"#{item['issueNumber']}"
            for item in samples
            if item["reasonCode"] in RESERVATION_EXCLUSIONS
        ]
        examples = examples[:5]
        summary += f" {conflicts} candidate(s) are reserved by another attempt"
        if examples:
            summary += " (" + ", ".join(examples) + ")"
        summary += (
            ". Inspect searchEvidence.rejectedCandidates for the recorded owner "
            "and reservation status. A live reservation expires on its own; a "
            "legacy (version 1) reservation is retired by the operator-declared "
            "migration cutover, not by removing a status label."
        )
    return summary


async def resolve_issue(
    *,
    repository: str,
    query: str,
    github_service: GitHubService,
    blockers_from_issue: Callable[[Mapping[str, Any]], Awaitable[list[dict[str, Any]]]],
    reconcile_candidate: Callable[[int], Awaitable[Mapping[str, Any]]] | None = None,
    attempt_evidence_resolver: Callable[
        [Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]
    ]
    | None = None,
    recovery_handoff: Mapping[str, Any] | None = None,
    attempt_context: Mapping[str, Any] | None = None,
    pr_identities: Sequence[Mapping[str, Any]] | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    reads_complete: Mapping[str, Any] | None = None,
    active_attempt_comments: Sequence[Mapping[str, Any]] | None = None,
    own_announcement_abandoned: bool | None = None,
    writers_settled: bool | None = None,
    reserve_candidate: Callable[[int], Awaitable[bool]] | None = None,
    include_all_authors: bool = False,
) -> tuple[int | None, dict[str, Any]]:
    """Select the best search match, or first unblocked open issue, within 500 rows.

    The default selector admits only Available and Recovery-needed lifecycle
    states and skips issues already marked with an in-progress status label so
    concurrent work is not selected twice. Supplied unresolved active-attempt
    evidence blocks admission even when the label is missing.

    A Recovery-needed candidate additionally requires usable handoff evidence
    (``predecessor_stopped`` plus ``handoff_usable``): without it the later
    start transition denies the continuation deterministically, so the scan
    passes the candidate over instead of returning it.

    Every surviving candidate additionally passes the one shared exact-issue
    admission boundary used by explicit/orchestration/continuation paths
    (issue #4178) with its pinned repository/issue identity. Per-candidate
    attempt evidence comes from ``attempt_evidence_resolver`` when supplied,
    otherwise from the caller-supplied ``attempt_context``; the remaining
    Req-1 bundle entries (blockers on the fallback-scan path, PR identities,
    retry policy, read completeness, validated attempt comments) are threaded
    through when the caller supplies them. Incomplete pagination or failed
    reads remain unknown evidence upstream of this function and never an
    empty owner set.
    """

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(
            "GitHub issue search requires an explicit owner/repository scope."
        )
    if type(include_all_authors) is not bool:
        raise ValueError(
            "GitHub issue search requires include_all_authors to be a boolean."
        )
    normalized_query = query if isinstance(query, str) else ""
    is_blank_search = not normalized_query.strip()
    # Req 4 recandidate gate (issue #4178): a search may consider another
    # candidate only after its own abandoned announcement and writers are
    # conclusively settled. Ordinary first selections pass no signals and
    # proceed; a present-but-unsettled recandidate context blocks selection
    # rather than silently scanning on.
    if own_announcement_abandoned is not None or writers_settled is not None:
        gate = recandidate_after_abandon(
            own_announcement_abandoned=bool(own_announcement_abandoned),
            writers_settled=bool(writers_settled),
        )
        if not gate["allowed"]:
            return None, {
                "searchEvidence": {
                    "authorScope": "all" if include_all_authors else "authenticated_user",
                    "fallbackScanning": is_blank_search,
                    "pagesExamined": 0,
                    "candidatesExamined": 0,
                    "authorMismatchesSkipped": 0,
                },
                "error": gate["summary"],
            }
    evidence: dict[str, Any] = {
        "searchEvidence": {
            "authorScope": "all" if include_all_authors else "authenticated_user",
            "fallbackScanning": is_blank_search,
            "pagesExamined": 0,
            "candidatesExamined": 0,
            "authorMismatchesSkipped": 0,
        }
    }
    counts = evidence["searchEvidence"]
    token, error = await github_service.resolve_github_token(repo=repository)
    if not token:
        return None, {
            **evidence,
            "error": error or "GitHub issue search is unavailable.",
        }

    authenticated_user: dict[str, Any] | None = None
    effective_query = normalized_query
    if not include_all_authors:
        authenticated_user, identity_failure = await github_service.get_authenticated_user(
            token=token
        )
        if authenticated_user is None:
            failure = identity_failure or {}
            return None, {
                **evidence,
                "error": str(
                    failure.get("summary")
                    or "MoonMind could not determine the GitHub account used for this search."
                ),
                "reasonCode": str(failure.get("reasonCode") or "identity_unavailable"),
                **(
                    {"httpStatus": failure["httpStatus"]}
                    if failure.get("httpStatus") is not None
                    else {}
                ),
            }
        counts["authenticatedUser"] = dict(authenticated_user)
        if not is_blank_search:
            effective_query, scope_error = _validated_author_scope(
                query=normalized_query,
                authenticated_login=str(authenticated_user["login"]),
            )
            if scope_error is not None:
                return None, {
                    **evidence,
                    **scope_error,
                }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(1, 6):
            if not is_blank_search:
                url = "https://api.github.com/search/issues"
                if include_all_authors:
                    scoped_q = f"{normalized_query} repo:{repository} is:issue is:open"
                else:
                    scoped_q = (
                        f"{effective_query} repo:{repository} is:issue is:open"
                    )
                params = {
                    "q": scoped_q,
                    "per_page": 100,
                    "page": page,
                }
            else:
                url = f"https://api.github.com/repos/{repository}/issues"
                params = {
                    "state": "open",
                    "sort": "created",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                }
                if not include_all_authors and authenticated_user is not None:
                    params["creator"] = str(authenticated_user["login"])
            try:
                response = await client.get(
                    url, params=params, headers=github_service._github_headers(token)
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {401, 403}:
                    return None, {
                        **evidence,
                        "error": (
                            "GitHub issue search is not authorized for this repository."
                        ),
                        "reasonCode": "search_auth_failure",
                        "httpStatus": status,
                    }
                if status == 429:
                    return None, {
                        **evidence,
                        "error": "GitHub rate limit reached during issue search.",
                        "reasonCode": "provider_rate_limited",
                        "httpStatus": status,
                    }
                if status >= 500:
                    return None, {
                        **evidence,
                        "error": "GitHub is unavailable during issue search.",
                        "reasonCode": "provider_unavailable",
                        "httpStatus": status,
                    }
                return None, {
                    **evidence,
                    "error": f"GitHub issue search failed: {type(exc).__name__}.",
                    "reasonCode": "provider_unavailable",
                }
            except (httpx.HTTPError, ValueError) as exc:
                return None, {
                    **evidence,
                    "error": f"GitHub issue search failed: {type(exc).__name__}.",
                    "reasonCode": "provider_unavailable",
                }
            counts["pagesExamined"] += 1
            if not is_blank_search and (
                not isinstance(payload, Mapping)
                or payload.get("incomplete_results") is not False
            ):
                return None, {
                    **evidence,
                    "error": "GitHub returned incomplete or malformed search evidence.",
                    "reasonCode": "incomplete_evidence",
                }
            candidates = payload.get("items") if not is_blank_search else payload
            if not isinstance(candidates, list) or len(candidates) > 100:
                return None, {
                    **evidence,
                    "error": "GitHub returned malformed issue candidates.",
                    "reasonCode": "incomplete_evidence",
                }
            for candidate in candidates:
                counts["candidatesExamined"] += 1
                if not isinstance(candidate, Mapping):
                    return None, {
                        **evidence,
                        "error": "GitHub returned a malformed issue candidate.",
                        "reasonCode": "incomplete_evidence",
                    }
                if "pull_request" in candidate:
                    counts["pullRequestsSkipped"] = (
                        counts.get("pullRequestsSkipped", 0) + 1
                    )
                    continue
                if not is_complete_open_issue(candidate, repository):
                    return None, {
                        **evidence,
                        "error": "GitHub candidate identity, state, or blocker evidence is invalid.",
                        "reasonCode": "invalid_author_evidence"
                        if authenticated_user is not None
                        and not isinstance(candidate.get("user"), Mapping)
                        else "incomplete_evidence",
                    }
                # Self-only author validation happens before expensive
                # dependency/attempt checks and before any announcement or
                # mutation. Compare durable account IDs: a matching login with
                # a different ID does not qualify, while a matching ID with an
                # updated login is the same account.
                if authenticated_user is not None:
                    selected_author = _selected_author_identity(candidate)
                    if selected_author is None:
                        return None, {
                            **evidence,
                            "error": (
                                "GitHub returned an issue candidate without verifiable "
                                "author identity."
                            ),
                            "reasonCode": "invalid_author_evidence",
                        }
                    if selected_author["id"] != authenticated_user["id"]:
                        counts["authorMismatchesSkipped"] = (
                            int(counts.get("authorMismatchesSkipped") or 0) + 1
                        )
                        _record_rejection(
                            counts, candidate["number"], "author_mismatch"
                        )
                        continue
                normalized = dict(candidate)
                labels = candidate["labels"]
                normalized["labels"] = [label["name"] for label in labels]
                # A status label is advisory bookkeeping, never ownership. An
                # in-progress candidate is reassessed against current attempt
                # evidence; only a live reservation, an explicit hold, or
                # unreadable evidence still excludes it.
                reconciliation: Mapping[str, Any] | None = None
                advisory_only = has_in_progress_status(
                    normalized
                ) and is_lifecycle_selectable_candidate(
                    {
                        **normalized,
                        "labels": [
                            label
                            for label in normalized["labels"]
                            if str(label).strip().lower() not in _IN_PROGRESS_LABELS
                        ],
                    }
                )
                if advisory_only and reconcile_candidate is not None:
                    reconciliation = await reconcile_candidate(candidate["number"])
                    if reconciliation.get("reclaimed"):
                        normalized["labels"] = [
                            label
                            for label in normalized["labels"]
                            if str(label).strip().lower() not in _IN_PROGRESS_LABELS
                        ]
                if advisory_only and has_in_progress_status(normalized):
                    _record_rejection(
                        counts,
                        candidate["number"],
                        reservation_exclusion_reason(reconciliation),
                        reservationEvidence=dict(reconciliation or {}) or None,
                    )
                    continue
                if not is_lifecycle_selectable_candidate(normalized):
                    _record_rejection(
                        counts,
                        candidate["number"],
                        "lifecycle_ineligible",
                        lifecycleState=interpret_issue(normalized).settled,
                    )
                    continue
                if interpret_issue(
                    {"state": "open", "labels": normalized["labels"]}
                ).settled == SETTLED_RECOVERY_NEEDED and not _recovery_handoff_usable(
                    recovery_handoff
                ):
                    _record_rejection(
                        counts, candidate["number"], "recovery_handoff_missing"
                    )
                    continue
                if attempt_evidence_resolver is not None:
                    candidate_attempt_context: (
                        Mapping[str, Any] | None
                    ) = await attempt_evidence_resolver(normalized)
                    if attempt_evidence_blocks_admission(candidate_attempt_context):
                        _record_rejection(
                            counts, candidate["number"], "active_attempt_conflict"
                        )
                        continue
                else:
                    candidate_attempt_context = attempt_context
                # Same shared exact-issue admission boundary as explicit /
                # orchestration / continuation paths (issue #4178): the
                # search entrypoint admits with its pinned identity plus the
                # full Req-1 bundle. Trusted blocker evidence is resolved per
                # candidate and threaded into the admit decision itself (not a
                # post-hoc skip): a blocked candidate is denied as
                # blocked_prerequisite on both the query and fallback paths.
                candidate_blockers: (
                    list[dict[str, Any]] | None
                ) = await blockers_from_issue(normalized)
                shared = admit_for_entrypoint(
                    ENTRYPOINT_SEARCH,
                    repository=repository,
                    issue_number=int(candidate["number"]),
                    issue={"state": "open", "labels": normalized["labels"]},
                    attempt_context=candidate_attempt_context,
                    blockers=candidate_blockers,
                    pr_identities=pr_identities,
                    retry_policy=retry_policy,
                    reads_complete=reads_complete,
                    active_attempt_comments=active_attempt_comments,
                )
                if not shared.allowed:
                    if shared.reason_code == "read_failure":
                        return None, {
                            **evidence,
                            "error": shared.summary,
                            "reasonCode": "read_failure",
                        }
                    _record_rejection(counts, candidate["number"], shared.reason_code)
                    continue
                if candidate_blockers:
                    _record_rejection(
                        counts, candidate["number"], "blocked_prerequisite"
                    )
                    continue
                # Confirm current identity/lifecycle inside the bounded scan so
                # a stale first candidate does not fail the whole scheduled tick.
                try:
                    current_response = await client.get(
                        f"https://api.github.com/repos/{repository}/issues/{candidate['number']}",
                        headers=github_service._github_headers(token),
                    )
                    current_response.raise_for_status()
                    current = current_response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    return None, {
                        **evidence,
                        "error": f"Candidate confirmation failed: {type(exc).__name__}.",
                    }
                if (
                    not isinstance(current, Mapping)
                    or current.get("number") != candidate["number"]
                ):
                    return None, {
                        **evidence,
                        "error": "Candidate confirmation returned a different or malformed issue.",
                    }
                if current.get("state") == "closed":
                    _record_rejection(counts, candidate["number"], "candidate_closed")
                    continue
                if not is_complete_open_issue(current, repository):
                    return None, {
                        **evidence,
                        "error": "Candidate confirmation evidence is incomplete.",
                    }
                if not is_lifecycle_selectable_candidate(current):
                    _record_rejection(
                        counts,
                        candidate["number"],
                        "lifecycle_ineligible",
                        lifecycleState=interpret_issue(current).settled,
                    )
                    continue
                if await blockers_from_issue(current):
                    _record_rejection(
                        counts, candidate["number"], "blocked_prerequisite"
                    )
                    continue
                if interpret_issue(
                    current
                ).settled == SETTLED_RECOVERY_NEEDED and not _recovery_handoff_usable(
                    recovery_handoff
                ):
                    _record_rejection(
                        counts, candidate["number"], "recovery_handoff_missing"
                    )
                    continue
                # Recheck author scope on the authoritative issue read before
                # granting a durable claim; a search hit alone is not authority.
                selected_author = _selected_author_identity(current)
                if authenticated_user is not None:
                    if selected_author is None:
                        return None, {
                            **evidence,
                            "error": "Candidate confirmation author evidence is incomplete.",
                            "reasonCode": "invalid_author_evidence",
                        }
                    if selected_author["id"] != authenticated_user["id"]:
                        counts["authorMismatchesSkipped"] += 1
                        _record_rejection(
                            counts, candidate["number"], "author_mismatch"
                        )
                        continue
                try:
                    if reserve_candidate is not None and not await reserve_candidate(
                        int(candidate["number"])
                    ):
                        _record_rejection(
                            counts, candidate["number"], "reservation_rejected"
                        )
                        continue
                except ActiveIssueClaimConflict as exc:
                    _record_rejection(
                        counts,
                        candidate["number"],
                        exc.evidence.get("reasonCode") or "active_attempt_conflict",
                        claimEvidence=exc.evidence,
                    )
                    continue
                if selected_author is not None:
                    counts["selectedIssueAuthor"] = dict(selected_author)
                evidence["selectedIssue"] = dict(current)
                return candidate["number"], evidence
            if len(candidates) < 100:
                if any(
                    counts.get("rejectionCounts", {}).get(reason)
                    for reason in RESERVATION_EXCLUSIONS
                ):
                    return None, {
                        **evidence,
                        "disposition": "idle",
                        "summary": "No issue selected." + _exclusion_summary(counts),
                        "reasonCode": "unresolved_issue_attempts",
                    }
                if authenticated_user is not None:
                    return None, {
                        **evidence,
                        "disposition": "idle",
                        "summary": (
                            "No eligible open GitHub issue created by the authenticated "
                            "search account was found; candidate pages exhausted. "
                            "No other author's issue was selected."
                        )
                        + _exclusion_summary(counts),
                        "reasonCode": "no_eligible_self_authored_issue",
                    }
                return None, {
                    **evidence,
                    "disposition": "idle",
                    "summary": "No eligible open GitHub issue found; candidate pages exhausted."
                    + _exclusion_summary(counts),
                }
    if authenticated_user is not None:
        return None, {
            **evidence,
            "error": (
                "No eligible GitHub issue created by the authenticated search account "
                "was found within the 500-candidate scan limit. No other author's "
                "issue was selected."
            )
            + _exclusion_summary(counts),
            "reasonCode": "no_eligible_self_authored_issue",
        }
    return None, {
        **evidence,
        "error": "No eligible GitHub issue found within the 500-candidate scan limit."
        + _exclusion_summary(counts),
        "reasonCode": "no_eligible_candidate",
    }
