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


@dataclass
class PrerequisiteLookup:
    """Bound and reuse validated prerequisite reads within one admission scan."""

    states: dict[tuple[str, int], str] = field(default_factory=dict)
    requests: int = 0


def declared_prerequisites(body: str, repository: str) -> list[tuple[str, int]]:
    """Read declared prerequisites and child lists, never contextual issue links."""
    declarations = [
        (match.group(1), False)
        for match in re.finditer(
            r"(?:\bCompletion depends on|\bIntegration prerequisites:|"
            r"(?:^|(?<=[.!?]))[ \t]*(?:[-*] )?Depends on:?)\s*"
            r"(.+?)(?=\.(?:\s|$)|\n|$)",
            body,
            re.IGNORECASE | re.MULTILINE,
        )
    ]
    # Child lists declare completion dependencies even when a stale checkbox
    # claims completion. Their state is resolved by the same GitHub lookup as
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
            declarations.append((entry, True))
    refs: list[tuple[str, int]] = []
    for declaration, is_child in declarations:
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
            text = re.split(r"\s+[-–—]\s+(?![\s#])", text, maxsplit=1)[0]
        # Consume only the leading reference list. Prose after the list can
        # contain parent, related, or other contextual issue references.
        text = re.sub(r"^issues?\s+", "", text, flags=re.IGNORECASE)
        while match := re.match(
            r"(?:([\w.-]+/[\w.-]+))?#([1-9]\d*)" r"(?:\s*[-–—]\s*#?([1-9]\d*))?", text
        ):
            start = int(match.group(2))
            end = int(match.group(3) or start)
            if end < start or end - start >= 100:
                raise ValueError(
                    "GitHub prerequisite range is invalid or exceeds 100 issues."
                )
            refs.extend(
                (match.group(1) or repository, number)
                for number in range(start, end + 1)
            )
            text = text[match.end() :].lstrip(" \t,;")
            text = re.sub(r"^(?:and\b|&)\s*", "", text, flags=re.IGNORECASE)
    refs = list(dict.fromkeys(refs))
    if len(refs) > 100:
        raise ValueError("GitHub prerequisite declaration exceeds 100 issues.")
    return refs


async def check_prerequisites(
    *,
    issue: Mapping[str, Any],
    repository: str,
    github_service: GitHubService,
    lookup: PrerequisiteLookup | None = None,
) -> list[dict[str, Any]]:
    """Resolve prerequisite state through authenticated GitHub reads only."""
    refs = declared_prerequisites(str(issue.get("body") or ""), repository)
    if not refs:
        return []
    lookup = lookup if lookup is not None else PrerequisiteLookup()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for dependency_repo, number in refs:
            key = (dependency_repo.casefold(), number)
            prerequisite_state = lookup.states.get(key)
            if prerequisite_state == "closed":
                continue
            if prerequisite_state == "open":
                return [_prerequisite_blocker(dependency_repo, number)]
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
            if payload["state"] == "open":
                return [_prerequisite_blocker(dependency_repo, number)]
    return []


def _prerequisite_blocker(repository: str, number: int) -> dict[str, Any]:
    return {
        "source": "prerequisite",
        "repository": repository,
        "number": number,
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


# Author scope for GitHub Issue Search and Implement (issue #4257). The
# default work-selection boundary admits only issues created by the
# authenticated GitHub account performing the search; an explicit all-author
# opt-in removes that automatic constraint. Self-authorship never bypasses
# repository, open-state, lifecycle, blocker, attempt, recovery, retry,
# verification, or publication policy.
AUTHOR_SCOPE_SELF = "authenticated_user"
AUTHOR_SCOPE_ALL = "all"

SELF_ONLY_IDENTITY_ERROR = (
    "MoonMind could not determine the GitHub account used for this search. "
    'Use a user-associated GitHub credential, or explicitly enable "Include '
    'issues created by other users".'
)

CONFLICTING_AUTHOR_FILTER_ERROR = (
    "GitHub issue search includes an author filter for another account. "
    'Enable "Include issues created by other users" to search issues created '
    "by other accounts."
)


def parse_include_all_authors(value: Any) -> bool:
    """Strictly parse the author-scope boundary value.

    Only actual booleans are accepted; omitted input becomes ``False``.
    Strings (including ``"true"``/``"false"``), numbers, null, arrays, and
    objects are rejected rather than coerced by truthiness.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise ValueError(
        'GitHub issue search scope "include_all_authors" must be a boolean value.'
    )


def candidate_author_id(candidate: Mapping[str, Any]) -> int | None:
    """Return the candidate's author account ID, or None when missing/malformed.

    The durable account ID is authoritative: a matching login with a different
    ID does not qualify, while a matching ID with an updated login is the same
    account. Bot-created issues carry the actual creating account.
    """
    user = candidate.get("user")
    if not isinstance(user, Mapping):
        return None
    account_id = user.get("id")
    if type(account_id) is not int or account_id <= 0:
        return None
    return account_id


def candidate_author_login(candidate: Mapping[str, Any]) -> str:
    """Return the candidate's author login, or "" when absent."""
    user = candidate.get("user")
    if not isinstance(user, Mapping):
        return ""
    login = user.get("login")
    return login.strip() if isinstance(login, str) else ""


_AUTHOR_QUALIFIER_RE = re.compile(r"(?i)(?:^|[\s(])(?P<neg>-)?author:(?P<value>\S+)")
_NOT_AUTHOR_QUALIFIER_RE = re.compile(r"(?i)(?:^|[\s()])(?:NOT)\s+author:(?P<value>\S+)")


def _strip_quoted_literals(query: str) -> str:
    """Remove double/single-quoted spans so literal text is never a qualifier."""
    return re.sub(r'"[^"]*"|\'[^\']*\'', " ", query)


def _author_qualifier_values(query: str) -> tuple[list[str], list[str]]:
    """Split author qualifier values into included and excluded login sets."""
    stripped = _strip_quoted_literals(query)
    included: list[str] = []
    excluded: list[str] = []
    not_excluded: set[str] = set()
    for match in _NOT_AUTHOR_QUALIFIER_RE.finditer(stripped):
        value = match.group("value").strip().strip(",;()")
        if value:
            excluded.append(value)
            not_excluded.add(value.lower())
    for match in _AUTHOR_QUALIFIER_RE.finditer(stripped):
        # GitHub logins never contain these delimiters; strip surrounding
        # grouping/punctuation so "(author:@me)" normalizes instead of
        # conflicting.
        value = match.group("value").strip().strip(",;()")
        if not value:
            continue
        if value.lower() in not_excluded:
            # Already captured as a NOT-exclusion; the plain scan also sees
            # the token but the negation owns its meaning.
            continue
        (excluded if match.group("neg") else included).append(value)
    return included, excluded


def plan_author_scoped_query(
    *, query: str, login: str, include_all_authors: bool,
) -> tuple[str | None, dict[str, str] | None]:
    """Build the effective provider query for the requested author scope.

    Returns ``(effective_query, None)`` on success, or ``(None, failure)``
    with a field-addressable ``error``/``reasonCode`` when the authored query
    conflicts with the enforced scope. In all-author mode the authored query
    passes through unchanged. An empty effective remainder means the authored
    query carried only a normalized self-author qualifier.
    """
    if include_all_authors:
        return query, None
    normalized_login = login.strip()
    if not normalized_login:
        return None, {
            "error": SELF_ONLY_IDENTITY_ERROR,
            "reasonCode": "identity_unavailable",
        }
    included, excluded = _author_qualifier_values(query)
    self_lower = normalized_login.lower()
    for value in included:
        if value.lower() not in {"@me", self_lower}:
            return None, {
                "error": CONFLICTING_AUTHOR_FILTER_ERROR,
                "reasonCode": "conflicting_author_filter",
            }
    for value in excluded:
        if value.lower() in {"@me", self_lower}:
            return None, {
                "error": CONFLICTING_AUTHOR_FILTER_ERROR,
                "reasonCode": "conflicting_author_filter",
            }
    if len(set(value.lower() for value in included)) > 1:
        return None, {
            "error": CONFLICTING_AUTHOR_FILTER_ERROR,
            "reasonCode": "conflicting_author_filter",
        }
    has_top_or = (
        re.search(
            r"(?i)(?:^|[\s()])(?:OR)(?:$|[\s()])",
            _strip_quoted_literals(f" {query} "),
        )
        is not None
    )
    if has_top_or and (included or excluded):
        # An author qualifier combined with top-level OR cannot be normalized
        # without rewriting authored meaning; reject rather than widen.
        return None, {
            "error": CONFLICTING_AUTHOR_FILTER_ERROR,
            "reasonCode": "conflicting_author_filter",
        }
    # Normalize accepted self-author qualifiers away so the provider request
    # carries exactly one author constraint. Accepted NOT-exclusions of other
    # accounts are redundant under the automatic self constraint and are
    # removed wholly so no dangling NOT can negate the applied constraint.
    remainder = _NOT_AUTHOR_QUALIFIER_RE.sub(" ", query)
    remainder = _AUTHOR_QUALIFIER_RE.sub(" ", remainder)
    remainder = re.sub(r"\(\s*\)", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    if has_top_or and remainder:
        # Parenthesize so the author constraint governs the whole expression
        # instead of binding to one side of an OR. Candidate-side ID
        # validation remains authoritative regardless of provider parsing.
        remainder = f"({remainder})"
    effective = (
        f"{remainder} author:{normalized_login}".strip()
        if remainder
        else f"author:{normalized_login}"
    )
    return effective, None


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


async def resolve_issue(
    *,
    repository: str,
    query: str,
    github_service: GitHubService,
    blockers_from_issue: Callable[[Mapping[str, Any]], Awaitable[list[dict[str, Any]]]],
    attempt_evidence_resolver: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]] | None = None,
    recovery_handoff: Mapping[str, Any] | None = None,
    attempt_context: Mapping[str, Any] | None = None,
    pr_identities: Sequence[Mapping[str, Any]] | None = None,
    retry_policy: Mapping[str, Any] | None = None,
    reads_complete: Mapping[str, Any] | None = None,
    active_attempt_comments: Sequence[Mapping[str, Any]] | None = None,
    own_announcement_abandoned: bool | None = None,
    writers_settled: bool | None = None,
    include_all_authors: bool = False,
) -> tuple[int | None, dict[str, Any]]:
    """Select the best search match, or first unblocked open issue, within 500 rows.

    The default selector admits only Available and Recovery-needed lifecycle
    states and skips issues already marked with an in-progress status label so
    concurrent work is not selected twice. Supplied unresolved active-attempt
    evidence blocks admission even when the label is missing.

    Author scope (issue #4257): unless ``include_all_authors`` is true, only
    issues created by the authenticated GitHub account performing the search
    are eligible. The search credential is resolved once and reused for the
    authenticated ``GET /user`` identity lookup, candidate discovery, and
    provider query scoping; the durable account ID (not the login) validates
    returned candidates before any expensive dependency/attempt check or
    announcement. Identity failures stop selection with an actionable
    diagnostic and never fall back to all authors. The checkbox broadens only
    author eligibility: repository, open-state, lifecycle, blocker, attempt,
    recovery, and retry policy still apply, and dependency reads are never
    author-filtered.

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
    if not isinstance(include_all_authors, bool):
        raise ValueError(
            'GitHub issue search scope "include_all_authors" must be a boolean value.'
        )
    author_scope = AUTHOR_SCOPE_ALL if include_all_authors else AUTHOR_SCOPE_SELF
    blank_search = not str(query or "").strip()
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
                    "fallbackScanning": blank_search,
                    "pagesExamined": 0,
                    "candidatesExamined": 0,
                    "authorScope": author_scope,
                    "authorMismatchesSkipped": 0,
                },
                "error": gate["summary"],
            }
    evidence: dict[str, Any] = {
        "searchEvidence": {
            "fallbackScanning": blank_search,
            "pagesExamined": 0,
            "candidatesExamined": 0,
            "authorScope": author_scope,
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
    effective_query = query

    async with httpx.AsyncClient(timeout=30.0) as client:
        if not include_all_authors:
            # Resolve the search credential's account once within this
            # operation and reuse the same credential for identity,
            # discovery, and provider query scoping. The lookup is one
            # bounded request outside the candidate/prerequisite budgets.
            get_user = getattr(
                github_service,
                "get_authenticated_user",
                GitHubService.get_authenticated_user,
            )
            authenticated_user, identity_failure = await get_user(
                token=token, client=client
            )
            if identity_failure is not None or authenticated_user is None:
                failure = identity_failure or {}
                return None, {
                    **evidence,
                    "error": str(
                        failure.get("error") or SELF_ONLY_IDENTITY_ERROR
                    ),
                    "reasonCode": str(
                        failure.get("reasonCode") or "identity_unavailable"
                    ),
                }
            counts["authenticatedUser"] = {
                "id": authenticated_user["id"],
                "login": authenticated_user["login"],
            }
            if not blank_search:
                planned, query_failure = plan_author_scoped_query(
                    query=query,
                    login=str(authenticated_user["login"]),
                    include_all_authors=False,
                )
                if query_failure is not None or planned is None:
                    failure = query_failure or {}
                    return None, {
                        **evidence,
                        "error": str(
                            failure.get("error") or CONFLICTING_AUTHOR_FILTER_ERROR
                        ),
                        "reasonCode": str(
                            failure.get("reasonCode") or "conflicting_author_filter"
                        ),
                        "field": "issueSearch",
                        "inputPath": "preset.inputs.issue_search",
                    }
                effective_query = planned
        for page in range(1, 6):
            if not blank_search:
                url = "https://api.github.com/search/issues"
                params = {
                    "q": f"{effective_query} repo:{repository} is:issue is:open",
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
            except (httpx.HTTPError, ValueError) as exc:
                return None, {
                    **evidence,
                    "error": f"GitHub issue search failed: {type(exc).__name__}.",
                }
            counts["pagesExamined"] += 1
            if not blank_search and (
                not isinstance(payload, Mapping)
                or payload.get("incomplete_results") is not False
            ):
                return None, {
                    **evidence,
                    "error": "GitHub returned incomplete or malformed search evidence.",
                }
            candidates = payload.get("items") if not blank_search else payload
            if not isinstance(candidates, list) or len(candidates) > 100:
                return None, {
                    **evidence,
                    "error": "GitHub returned malformed issue candidates.",
                }
            for candidate in candidates:
                counts["candidatesExamined"] += 1
                if not isinstance(candidate, Mapping):
                    return None, {
                        **evidence,
                        "error": "GitHub returned a malformed issue candidate.",
                    }
                if "pull_request" in candidate:
                    continue
                if not is_complete_open_issue(candidate, repository):
                    return None, {
                        **evidence,
                        "error": "GitHub candidate identity, state, or blocker evidence is invalid.",
                    }
                if not include_all_authors and authenticated_user is not None:
                    author_id = candidate_author_id(candidate)
                    if author_id is None:
                        return None, {
                            **evidence,
                            "error": "GitHub returned invalid author evidence for a candidate.",
                            "reasonCode": "invalid_author_evidence",
                        }
                    if author_id != authenticated_user["id"]:
                        counts["authorMismatchesSkipped"] += 1
                        continue
                normalized = dict(candidate)
                labels = candidate["labels"]
                normalized["labels"] = [label["name"] for label in labels]
                if has_in_progress_status(normalized):
                    continue
                if not is_lifecycle_selectable_candidate(normalized):
                    continue
                if (
                    interpret_issue(
                        {"state": "open", "labels": normalized["labels"]}
                    ).settled == SETTLED_RECOVERY_NEEDED
                    and not _recovery_handoff_usable(recovery_handoff)
                ):
                    continue
                if attempt_evidence_resolver is not None:
                    candidate_attempt_context: Mapping[str, Any] | None = await attempt_evidence_resolver(normalized)
                    if attempt_evidence_blocks_admission(candidate_attempt_context):
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
                candidate_blockers: list[dict[str, Any]] | None = await blockers_from_issue(
                    normalized
                )
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
                    continue
                if candidate_blockers:
                    continue
                if not include_all_authors and authenticated_user is not None:
                    counts["selectedIssueAuthor"] = {
                        "id": authenticated_user["id"],
                        "login": candidate_author_login(candidate)
                        or str(authenticated_user["login"]),
                    }
                else:
                    counts["selectedIssueAuthor"] = {
                        "id": candidate_author_id(candidate),
                        "login": candidate_author_login(candidate),
                    }
                return candidate["number"], evidence
            if len(candidates) < 100:
                if not include_all_authors:
                    return None, {
                        **evidence,
                        "error": (
                            "No eligible open GitHub issue created by the "
                            "authenticated search account found; candidate pages "
                            "exhausted."
                        ),
                        "reasonCode": "no_eligible_self_authored_issue",
                    }
                return None, {
                    **evidence,
                    "error": "No eligible open GitHub issue found; candidate pages exhausted.",
                }
    if not include_all_authors:
        return None, {
            **evidence,
            "error": (
                "No eligible open GitHub issue created by the authenticated "
                "search account found within the 500-candidate scan limit."
            ),
            "reasonCode": "no_eligible_self_authored_issue",
        }
    return None, {
        **evidence,
        "error": "No eligible GitHub issue found within the 500-candidate scan limit.",
    }
