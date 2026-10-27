"""Read-only import of a repository's existing GitHub security alerts.

GitHub stays the source of these facts. The import records what it read,
within which bounds and with which outcome. An unavailable category is never
zero alerts, and an alert known only at another revision never becomes a
finding for the assessed revision. Only allowlisted alert fields leave this
module, after the scanner sanitizer has removed secret values.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from moonmind.security.assessments import (
    AssessmentRequest,
    Coverage,
    Finding,
    GitHubAlertImport,
    Observation,
    content_key,
)
from moonmind.security.repository_scanners import ScannerOutputSanitizer

NATIVE_SCHEMA = "github-security-alerts/v1"
SOURCE = "github"
_PATHS = {
    "code-scanning": "code-scanning/alerts",
    "dependabot": "dependabot/alerts",
    "secret-scanning": "secret-scanning/alerts",
}
_RETRYABLE = {500, 502, 503, 504}
_TEXT = 512
_LISTED = 64
_COVERAGE = {
    "complete": "complete",
    "permission_denied": "authentication_failed",
    "authorization_unavailable": "authentication_failed",
    "product_disabled": "unsupported",
    "product_unavailable": "unsupported",
}
_PERMISSIONS = {
    "code-scanning": "security_events",
    "dependabot": "vulnerability_alerts",
    "secret-scanning": "secret_scanning_alerts",
}


class _Stop(Exception):
    def __init__(self, outcome: str, *, limit: str | None = None, reset=None):
        super().__init__(outcome)
        self.outcome = outcome
        self.limit = limit
        self.reset = reset


class _Budget:
    def __init__(self, request: GitHubAlertImport, clock):
        self.request = request
        self.clock = clock
        self.deadline = clock() + request.max_seconds
        self.requests = self.items = self.bytes = 0
        self.sanitizer = ScannerOutputSanitizer()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: Any) -> str | None:
    return value[:_TEXT] if isinstance(value, str) and value else None


def _revision(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        return value
    return None


def _login(value: Any) -> str | None:
    return _text(value.get("login")) if isinstance(value, dict) else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _classify(status: int, headers: httpx.Headers, body: bytes) -> tuple[str, Any]:
    try:
        message = str(json.loads(body).get("message") or "").lower()
    except (ValueError, AttributeError):
        message = ""
    reset = _text(headers.get("x-ratelimit-reset") or headers.get("retry-after"))
    if status == 429 or (
        status == 403
        and (
            headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in headers
            or "rate limit" in message
        )
    ):
        return "rate_limited", reset
    if status == 403 and any(
        word in message
        for word in ("disabled", "not enabled", "must be enabled", "advanced security")
    ):
        return "product_disabled", None
    if status in {401, 403}:
        return "permission_denied", None
    if status == 404:
        return "product_unavailable", None
    if status in {410, 451}:
        return "repository_unavailable", None
    return "request_rejected", None


def _next_link(header: str | None) -> str | None:
    for part in (header or "").split(","):
        url, _, params = part.partition(";")
        if 'rel="next"' in params:
            return url.strip().removeprefix("<").removesuffix(">")
    return None


async def _get(client: httpx.AsyncClient, budget: _Budget, url: str, headers):
    """One safe GET; transient failures retry within the same request budget."""
    failure = "upstream_unavailable"
    for _ in range(3):
        if budget.requests >= budget.request.max_requests:
            raise _Stop("pagination_incomplete", limit="request_budget")
        remaining = budget.deadline - budget.clock()
        if remaining <= 0:
            raise _Stop("pagination_incomplete", limit="time_budget")
        budget.requests += 1
        body = bytearray()
        try:
            async with client.stream(
                "GET", url, headers=headers, timeout=remaining
            ) as response:
                if response.status_code in _RETRYABLE:
                    failure = "upstream_unavailable"
                    continue
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if budget.bytes + len(body) > budget.request.max_bytes:
                        raise _Stop("pagination_incomplete", limit="byte_budget")
                status, response_headers = response.status_code, response.headers
        except httpx.TransportError:
            failure = "transport_unavailable"
            continue
        budget.bytes += len(body)
        if status != 200:
            outcome, reset = _classify(status, response_headers, bytes(body))
            raise _Stop(outcome, reset=reset)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise _Stop("malformed_response") from exc
        return payload, _next_link(response_headers.get("link"))
    raise _Stop(failure)


def _record(category: str, item: Any) -> dict:
    """Allowlisted native identity; secret values and raw payloads stay behind."""
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("number"), int)
        or isinstance(item.get("number"), bool)
        or item["number"] <= 0
        or not isinstance(item.get("state"), str)
    ):
        raise _Stop("malformed_response")
    url = item.get("html_url")
    record = {
        "number": item["number"],
        "url": (
            url[:_TEXT] if isinstance(url, str) and url.startswith("https://") else None
        ),
        "state": _text(item["state"]),
        "createdAt": _text(item.get("created_at")),
        "updatedAt": _text(item.get("updated_at")),
    }
    if category == "secret-scanning":
        location = _dict(item.get("first_location_detected"))
        record |= {
            "secretType": _text(item.get("secret_type")),
            "secretTypeDisplayName": _text(item.get("secret_type_display_name")),
            "validity": _text(item.get("validity")),
            "resolution": _text(item.get("resolution")),
            "resolvedAt": _text(item.get("resolved_at")),
            "resolvedBy": _login(item.get("resolved_by")),
            "resolutionComment": _text(item.get("resolution_comment")),
            "publiclyLeaked": item.get("publicly_leaked") is True,
            "pushProtectionBypassed": item.get("push_protection_bypassed") is True,
            "hasMoreLocations": item.get("has_more_locations") is True,
            "locator": {
                "path": _text(location.get("path")),
                "startLine": location.get("start_line"),
                "revision": _revision(location.get("commit_sha")),
                "basis": "first_location_detected",
            },
        }
        return record
    record |= {
        "fixedAt": _text(item.get("fixed_at")),
        "dismissedAt": _text(item.get("dismissed_at")),
        "dismissedBy": _login(item.get("dismissed_by")),
        "dismissedReason": _text(item.get("dismissed_reason")),
        "dismissedComment": _text(item.get("dismissed_comment")),
    }
    if category == "code-scanning":
        instance = _dict(item.get("most_recent_instance"))
        location = _dict(instance.get("location"))
        rule, tool = _dict(item.get("rule")), _dict(item.get("tool"))
        record |= {
            "rule": {
                "id": _text(rule.get("id")),
                "severity": _text(rule.get("severity")),
                "securitySeverityLevel": _text(rule.get("security_severity_level")),
            },
            "tool": {
                "name": _text(tool.get("name")),
                "version": _text(tool.get("version")),
                "guid": _text(tool.get("guid")),
            },
            "locator": {
                "path": _text(location.get("path")),
                "startLine": location.get("start_line"),
                "ref": _text(instance.get("ref")),
                "analysisKey": _text(instance.get("analysis_key")),
                "category": _text(instance.get("category")),
                "revision": _revision(instance.get("commit_sha")),
                "basis": "most_recent_instance",
            },
        }
        return record
    advisory = _dict(item.get("security_advisory"))
    vulnerability = _dict(item.get("security_vulnerability"))
    dependency = _dict(item.get("dependency"))
    package = _dict(vulnerability.get("package") or dependency.get("package"))
    record |= {
        "autoDismissedAt": _text(item.get("auto_dismissed_at")),
        "advisory": {
            "ghsaId": _text(advisory.get("ghsa_id")),
            "cveId": _text(advisory.get("cve_id")),
            "severity": _text(advisory.get("severity")),
        },
        "package": {
            "ecosystem": _text(package.get("ecosystem")),
            "name": _text(package.get("name")),
        },
        "vulnerableVersionRange": _text(vulnerability.get("vulnerable_version_range")),
        "firstPatchedVersion": _text(
            _dict(vulnerability.get("first_patched_version")).get("identifier")
        ),
        "scope": _text(dependency.get("scope")),
        # Dependabot reads the default branch's dependency graph; its alerts
        # carry no commit, so the category's observed revision applies.
        "locator": {
            "path": _text(dependency.get("manifest_path")),
            "revision": None,
            "basis": "default_branch_dependency_graph",
        },
    }
    return record


def _same_endpoint(url: str, expected: str) -> bool:
    actual, wanted = urlsplit(url), urlsplit(expected)
    return (actual.scheme, actual.netloc) == (
        wanted.scheme,
        wanted.netloc,
    ) and actual.path.rstrip("/").casefold() == wanted.path.rstrip("/").casefold()


async def _category(client, budget, root, headers, request, category, ref) -> dict:
    base = f"{root}/{_PATHS[category]}"
    filters: dict[str, Any] = {"per_page": request.per_page}
    if request.state == "open":
        filters["state"] = "open"
    if category == "code-scanning":
        filters["ref"] = ref
    result: dict[str, Any] = {
        "outcome": "complete",
        "limit": None,
        "rateLimitReset": None,
        "pages": 0,
        "duplicates": 0,
        "inconsistentPages": False,
        "continuation": None,
        "filters": filters,
        "records": [],
    }
    seen: dict[int, dict] = {}
    url: str | None = str(httpx.URL(base, params=filters))
    try:
        while url:
            payload, following = await _get(client, budget, url, headers)
            if not isinstance(payload, list):
                raise _Stop("malformed_response")
            if following is not None and not _same_endpoint(following, base):
                # A continuation must stay on the admitted repository endpoint.
                raise _Stop("malformed_response")
            for raw in payload:
                budget.sanitizer.observe_payload(raw)
            for record in [_record(category, item) for item in payload]:
                number = record["number"]
                if number in seen:
                    result["duplicates"] += 1
                    result["inconsistentPages"] |= seen[number] != record
                    continue
                if budget.items >= request.max_items:
                    raise _Stop("pagination_incomplete", limit="item_budget")
                budget.items += 1
                seen[number] = record
            result["pages"] += 1
            url = following
    except _Stop as stop:
        result.update(
            outcome=stop.outcome,
            limit=stop.limit,
            rateLimitReset=stop.reset,
            continuation=url,
        )
    result["records"] = [seen[number] for number in sorted(seen)]
    return result


async def _analyses(client, budget, root, headers, ref) -> dict:
    """Existing code-scanning analysis identity; SARIF bytes are not required."""
    url = str(
        httpx.URL(f"{root}/code-scanning/analyses", params={"ref": ref, "per_page": 10})
    )
    try:
        payload, _ = await _get(client, budget, url, headers)
        if not isinstance(payload, list) or not all(
            isinstance(i, dict) for i in payload
        ):
            raise _Stop("malformed_response")
    except _Stop as stop:
        return {"outcome": stop.outcome, "records": []}
    records = [
        {
            "id": item.get("id") if isinstance(item.get("id"), int) else None,
            "ref": _text(item.get("ref")),
            "commitSha": _revision(item.get("commit_sha")),
            "analysisKey": _text(item.get("analysis_key")),
            "category": _text(item.get("category")),
            "tool": {
                "name": _text(_dict(item.get("tool")).get("name")),
                "version": _text(_dict(item.get("tool")).get("version")),
            },
            "createdAt": _text(item.get("created_at")),
            "resultsCount": item.get("results_count"),
            "rulesCount": item.get("rules_count"),
            "sarifId": _text(item.get("sarif_id")),
        }
        for item in payload
    ]
    return {
        "outcome": "complete",
        "records": sorted(
            records, key=lambda row: row["createdAt"] or "", reverse=True
        ),
    }


def unavailable_alerts(
    request: GitHubAlertImport, outcome: str, *, error: str | None = None
) -> tuple[dict, dict]:
    """No request reached GitHub; every category reports why."""
    now = _now()
    native = _native(request)
    native["categories"] = {
        category: {
            "outcome": outcome,
            "error": error,
            "records": [],
            "pages": 0,
            "filters": {},
        }
        for category in request.categories
    }
    return native, {
        "startedAt": now,
        "completedAt": now,
        "requests": 0,
        "items": 0,
        "bytes": 0,
    }


def _native(request: GitHubAlertImport) -> dict:
    return {
        "schemaVersion": NATIVE_SCHEMA,
        "repository": {"fullName": request.repository},
        "subject": {
            "ref": request.ref,
            "revision": request.revision,
            "revisionSource": "admitted" if request.revision else None,
        },
        "categories": {},
        "analyses": None,
    }


async def collect_github_alerts(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    headers: dict[str, str],
    request: GitHubAlertImport,
    clock=time.monotonic,
) -> tuple[dict, dict]:
    """Return sanitized native evidence and the import's interval/usage.

    Every request is a GET on the admitted repository's endpoint. Collected
    pages survive a later failure; the category then reports that outcome.
    """
    started = _now()
    budget = _Budget(request, clock)
    native = _native(request)
    root = f"{api_base.rstrip('/')}/repos/{request.repository}"

    def usage():
        return {
            "startedAt": started,
            "completedAt": _now(),
            "requests": budget.requests,
            "items": budget.items,
            "bytes": budget.bytes,
        }

    try:
        repository, _ = await _get(client, budget, root, headers)
        if (
            not isinstance(repository, dict)
            or str(repository.get("full_name") or "").casefold()
            != request.repository.casefold()
            or not isinstance(repository.get("default_branch"), str)
        ):
            raise _Stop("repository_identity_mismatch")
    except _Stop as stop:
        outcome = {
            "product_unavailable": "repository_unavailable",
        }.get(stop.outcome, stop.outcome)
        native["categories"] = {
            category: {"outcome": outcome, "records": [], "pages": 0, "filters": {}}
            for category in request.categories
        }
        return native, usage()

    async def tip(ref: str) -> str | None:
        try:
            commit, _ = await _get(
                client, budget, f"{root}/commits/{quote(ref, safe='')}", headers
            )
        except _Stop:
            return None
        sha = commit.get("sha") if isinstance(commit, dict) else None
        return _revision(sha)

    default_ref = f"refs/heads/{repository['default_branch']}"
    default_tip = await tip(default_ref)
    ref = request.ref or default_ref
    subject = native["subject"]
    subject["ref"] = ref
    if request.revision is None:
        subject["revision"] = default_tip if ref == default_ref else await tip(ref)
        subject["revisionSource"] = "ref_tip_at_import"
    native["repository"] |= {
        "id": repository.get("id") if isinstance(repository.get("id"), int) else None,
        "defaultBranch": _text(repository["default_branch"]),
        "defaultBranchRevision": default_tip,
    }
    for category in request.categories:
        result = await _category(client, budget, root, headers, request, category, ref)
        if category == "code-scanning":
            if request.include_analyses and result["outcome"] in {
                "complete",
                "pagination_incomplete",
            }:
                native["analyses"] = await _analyses(client, budget, root, headers, ref)
            latest = (native["analyses"] or {}).get("records") or [{}]
            result["observedRevision"] = latest[0].get("commitSha")
        else:
            # These alert lists describe the repository's current default branch.
            result["observedRevision"] = default_tip if ref == default_ref else None
        if category == "dependabot":
            for record in result["records"]:
                record["locator"]["revision"] = result["observedRevision"]
        native["categories"][category] = result
    native["categories"] = budget.sanitizer.metadata(native["categories"])
    if native["analyses"] is not None:
        native["analyses"] = budget.sanitizer.metadata(native["analyses"])
    return native, usage()


def _rule(category: str, record: dict) -> str:
    if category == "code-scanning":
        value = record["rule"]["id"]
    elif category == "dependabot":
        value = record["advisory"]["ghsaId"] or record["advisory"]["cveId"]
    else:
        value = record["secretType"]
    return value or "unknown"


def _next_step(category: str, outcome: str) -> str:
    permission = _PERMISSIONS[category]
    return {
        "authorization_unavailable": (
            "Allow security_read on the admitted repository connection and its "
            "repository assignment, then rerun the import."
        ),
        "permission_denied": (
            f"Grant the connection's credential {permission} read access "
            "(GitHub App permission, or a token scope that can read these alerts), "
            "then rerun the import."
        ),
        "product_disabled": (
            f"{category} is disabled for the repository; enabling it is a separate "
            "operator decision. Rerun the import once it has results."
        ),
        "product_unavailable": (
            f"{category} has no results for this repository or ref; rerun the "
            "import after GitHub produces them."
        ),
        "rate_limited": "Rerun the import after the GitHub rate limit resets.",
        "repository_unavailable": (
            "Confirm the repository exists and the admitted connection can read it."
        ),
        "repository_identity_mismatch": (
            "Confirm the repository name; GitHub answered for another repository."
        ),
        "pagination_incomplete": (
            "Rerun with a larger GitHub import budget or narrower filters."
        ),
    }.get(
        outcome, "Rerun the import; GitHub returned an unreadable or failed response."
    )


def alert_observation(
    request: AssessmentRequest, native: dict, native_ref: str, observed_at: datetime
) -> tuple[Observation, dict]:
    """Project imported alerts onto the admitted scope and assessed revision."""
    config = request.github_alerts
    scope = request.scope
    subject = native["subject"]["revision"]
    findings, other, outside = [], [], []
    coverage, unverified, categories = [], [], {}
    for category, result in native["categories"].items():
        tool = f"{SOURCE}-{category}"
        counts = {"findings": 0, "closed": 0, "otherRevision": 0, "outOfScope": 0}
        for record in result["records"]:
            locator = record.get("locator") or {}
            alert = {
                "category": category,
                "number": record["number"],
                "url": record["url"],
            }
            if record["state"] != "open":
                counts["closed"] += 1
                continue
            if not subject or locator.get("revision") != subject:
                counts["otherRevision"] += 1
                other.append({**alert, "revision": locator.get("revision")})
                continue
            path = locator.get("path")
            asset = (
                path
                if path in scope.assets
                else config.repository if config.repository in scope.assets else None
            )
            if asset is None:
                counts["outOfScope"] += 1
                outside.append({**alert, "path": path})
                continue
            counts["findings"] += 1
            findings.append(
                Finding(
                    source=SOURCE,
                    tool=tool,
                    rule_id=_rule(category, record),
                    asset=asset,
                    native_id=str(record["number"]),
                    evidence_refs=[native_ref],
                )
            )
        outcome = result["outcome"]
        status = _COVERAGE.get(outcome, "partial" if result["records"] else "failed")
        if status == "complete" and (
            result.get("inconsistentPages")
            or not subject
            or result.get("observedRevision") != subject
            or counts["otherRevision"]
        ):
            status = "partial"
        coverage.append(
            Coverage(
                source=SOURCE,
                tool=tool,
                assets=scope.assets,
                rule_ids=[],
                config_ref=content_key(
                    [config.repository, native["subject"]["ref"], result["filters"]]
                ),
                status=status,
            )
        )
        if outcome != "complete":
            unverified.append(
                {
                    "category": category,
                    "outcome": outcome,
                    "nextStep": _next_step(category, outcome),
                }
            )
        categories[category] = {
            "outcome": outcome,
            "coverage": status,
            "limit": result.get("limit"),
            "error": result.get("error"),
            "rateLimitReset": result.get("rateLimitReset"),
            "pages": result.get("pages", 0),
            "items": len(result["records"]),
            "duplicates": result.get("duplicates", 0),
            "continuation": result.get("continuation"),
            "filters": result["filters"],
            "observedRevision": result.get("observedRevision"),
            **counts,
        }
    analyses = native.get("analyses")
    projection = {
        "repository": config.repository,
        "ref": native["subject"]["ref"],
        "subjectRevision": subject,
        "revisionSource": native["subject"]["revisionSource"],
        "defaultBranch": native["repository"].get("defaultBranch"),
        "defaultBranchRevision": native["repository"].get("defaultBranchRevision"),
        "nativeRef": native_ref,
        "categories": categories,
        "analyses": (
            None
            if analyses is None
            else {
                "outcome": analyses["outcome"],
                "count": len(analyses["records"]),
                "latestCommitSha": (analyses["records"] or [{}])[0].get("commitSha"),
            }
        ),
        "otherRevision": other[:_LISTED],
        "outOfScope": outside[:_LISTED],
        "listsTruncated": len(other) > _LISTED or len(outside) > _LISTED,
        "unverified": unverified,
    }
    return (
        Observation(
            scope=scope,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(days=1),
            coverage=coverage,
            findings=findings,
            provenance={
                "githubAlerts": native_ref,
                "githubRepository": config.repository,
                "githubRef": native["subject"]["ref"] or "unavailable",
                "githubRevision": subject or "unavailable",
            },
        ),
        projection,
    )


def _identifiers(category: str, record: dict) -> tuple[set[str], str]:
    if category == "dependabot":
        advisory = record["advisory"]
        return {
            value for value in (advisory["ghsaId"], advisory["cveId"]) if value
        }, "advisory"
    if category == "code-scanning" and record["rule"]["id"]:
        return {record["rule"]["id"]}, "rule"
    # Secret alerts share no stable identity with local findings once the
    # value is removed; both are preserved without a guessed match.
    return set(), ""


def _comparable_tool(category: str, record: dict, tool: str) -> bool:
    if category == "dependabot":
        return tool == "trivy"
    name = (record.get("tool") or {}).get("name") or ""
    return bool(name) and name.casefold() == tool.casefold()


def correlate_alerts(
    native: dict, imported: Observation, local: list[Observation]
) -> dict:
    """Relate imported alerts to local scanner findings by stable identifiers.

    A match needs the same asset and an upstream rule/advisory identifier the
    local tool reports. Similar prose never matches. Both sources' facts stay
    unchanged; disagreements are recorded as conflicts.
    """
    local_findings = [f for item in local for f in item.findings if f.source != SOURCE]
    local_coverage = [row for item in local for row in item.coverage]
    by_alert = {(f.tool, f.native_id): f for f in imported.findings}
    matches, conflicts = [], []
    for category, result in native["categories"].items():
        for record in result["records"]:
            identifiers, kind = _identifiers(category, record)
            path = (record.get("locator") or {}).get("path")
            if not identifiers or not path:
                continue
            alert = {"category": category, "number": record["number"]}
            hits = [
                f
                for f in local_findings
                if f.asset == path
                and f.rule_id in identifiers
                and _comparable_tool(category, record, f.tool)
            ]
            imported_finding = by_alert.get(
                (f"{SOURCE}-{category}", str(record["number"]))
            )
            for hit in hits:
                if record["state"] == "open":
                    matches.append(
                        {
                            "githubAlert": alert,
                            "githubFindingId": (
                                imported_finding.finding_id
                                if imported_finding
                                else None
                            ),
                            "localFindingId": hit.finding_id,
                            "basis": ["asset", f"{kind}:{hit.rule_id}"],
                        }
                    )
                else:
                    conflicts.append(
                        {
                            "kind": "github_closed_local_observed",
                            "githubAlert": alert,
                            "githubState": record["state"],
                            "localFindingId": hit.finding_id,
                        }
                    )
            if (
                imported_finding is not None
                and not hits
                and any(
                    row.status == "complete"
                    and path in row.assets
                    and _comparable_tool(category, record, row.tool)
                    and (row.all_rules or identifiers & set(row.rule_ids))
                    for row in local_coverage
                )
            ):
                conflicts.append(
                    {
                        "kind": "github_open_local_absent",
                        "githubAlert": alert,
                        "githubFindingId": imported_finding.finding_id,
                    }
                )
    return {
        "matches": matches[:256],
        "conflicts": conflicts[:256],
        "truncated": len(matches) > 256 or len(conflicts) > 256,
    }
