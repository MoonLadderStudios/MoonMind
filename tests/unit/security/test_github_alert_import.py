"""Issue #5045: bounded, read-only import of existing GitHub security alerts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from moonmind.auth.github_app import operations_to_permissions
from moonmind.security.assessments import (
    AssessmentRequest,
    Coverage,
    Finding,
    GitHubAlertImport,
    Observation,
)
from moonmind.security.github_alerts import (
    alert_observation,
    collect_github_alerts,
    correlate_alerts,
)
from moonmind.workflows.adapters.github_service import GitHubService

API = "https://api.github.com"
REPO = "acme/repo"
SUBJECT = "a" * 40
OTHER = "b" * 40
CANARY = "ghp_" + "Z" * 36
SCOPE = {
    "assets": ["poetry.lock", "app/main.py", REPO],
    "exclusions": [],
    "repositoryExceptions": [],
}


def code_alert(number, *, sha=SUBJECT, path="app/main.py", state="open", **extra):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/security/code-scanning/{number}",
        "state": state,
        "created_at": "2026-10-20T00:00:00Z",
        "rule": {"id": "py/sql-injection", "severity": "error"},
        "tool": {"name": "CodeQL", "version": "2.19.0"},
        "most_recent_instance": {
            "ref": "refs/heads/main",
            "commit_sha": sha,
            "analysis_key": ".github/workflows/codeql.yml:analyze",
            "location": {"path": path, "start_line": 3, "end_line": 3},
        },
        **extra,
    }


def dependabot_alert(
    number, *, state="open", ghsa="GHSA-xxxx-yyyy-zzzz", cve="CVE-2026-0001", **extra
):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/security/dependabot/{number}",
        "state": state,
        "security_advisory": {
            "ghsa_id": ghsa,
            "cve_id": cve,
            "severity": "high",
        },
        "security_vulnerability": {
            "package": {"ecosystem": "pip", "name": "example"},
            "vulnerable_version_range": "< 2.0",
            "first_patched_version": {"identifier": "2.0"},
        },
        "dependency": {"manifest_path": "poetry.lock", "scope": "runtime"},
        **extra,
    }


def secret_alert(number, *, sha=SUBJECT):
    return {
        "number": number,
        "html_url": f"https://github.com/{REPO}/security/secret-scanning/{number}",
        "state": "open",
        "secret_type": "github_personal_access_token",
        "secret": CANARY,
        "resolution_comment": f"leaked {CANARY} here",
        "first_location_detected": {
            "path": "app/main.py",
            "start_line": 9,
            "end_line": 9,
            "commit_sha": sha,
            "blob_sha": "c" * 40,
        },
    }


class FakeGitHub:
    """Controlled GitHub REST transport; records every request."""

    def __init__(self, routes=None, *, default_tip=SUBJECT):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default_tip = default_tip

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == f"/repos/{REPO}":
            return httpx.Response(
                200, json={"full_name": REPO, "id": 7, "default_branch": "main"}
            )
        if path.startswith(f"/repos/{REPO}/commits/"):
            return httpx.Response(200, json={"sha": self.default_tip})
        for suffix, pages in self.routes.items():
            if path == f"/repos/{REPO}/{suffix}":
                page = int(request.url.params.get("page", "1"))
                answer = pages[page - 1] if page <= len(pages) else []
                if callable(answer):
                    return answer(request)
                headers = {}
                if page < len(pages):
                    headers["Link"] = (
                        f"<{API}/repos/{REPO}/{suffix}?per_page=100&page={page + 1}>;"
                        ' rel="next"'
                    )
                return httpx.Response(200, json=answer, headers=headers)
        return httpx.Response(404, json={"message": "Not Found"})


def import_request(**overrides):
    return GitHubAlertImport.model_validate(
        {
            "repository": REPO,
            "categories": ["code-scanning", "dependabot", "secret-scanning"],
            "includeAnalyses": False,
            **overrides,
        }
    )


async def collect(fake, request=None):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as client:
        return await collect_github_alerts(
            client,
            api_base=API,
            headers={"Authorization": "Bearer selected"},
            request=request or import_request(),
        )


@pytest.mark.asyncio
async def test_multiple_pages_and_duplicate_ids_are_imported_once():
    fake = FakeGitHub(
        {
            "code-scanning/alerts": [
                [code_alert(1), code_alert(2)],
                [code_alert(2), code_alert(3)],
            ],
            "dependabot/alerts": [[]],
            "secret-scanning/alerts": [[]],
        }
    )
    native, usage = await collect(fake)
    code = native["categories"]["code-scanning"]
    assert code["outcome"] == "complete"
    assert [row["number"] for row in code["records"]] == [1, 2, 3]
    assert code["pages"] == 2 and code["duplicates"] == 1
    assert code["records"][0]["url"].endswith("/code-scanning/1")
    assert code["records"][0]["rule"]["id"] == "py/sql-injection"
    assert native["categories"]["dependabot"]["outcome"] == "complete"
    assert native["categories"]["dependabot"]["records"] == []
    assert usage["requests"] == len(fake.requests)
    assert {request.method for request in fake.requests} == {"GET"}
    assert all(
        request.headers["Authorization"] == "Bearer selected"
        for request in fake.requests
    )


@pytest.mark.asyncio
async def test_rate_limit_interruption_keeps_collected_pages_partial():
    def limited(_request):
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"},
        )

    fake = FakeGitHub({"code-scanning/alerts": [[code_alert(1)], limited]})
    native, _ = await collect(fake, import_request(categories=["code-scanning"]))
    code = native["categories"]["code-scanning"]
    assert code["outcome"] == "rate_limited"
    assert [row["number"] for row in code["records"]] == [1]
    assert "page=2" in code["continuation"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message", "outcome"),
    [
        (403, "Resource not accessible by integration", "permission_denied"),
        (401, "Bad credentials", "permission_denied"),
        (403, "Secret scanning is disabled on this repository.", "product_disabled"),
        (404, "no analysis found", "product_unavailable"),
        (429, "slow down", "rate_limited"),
    ],
)
async def test_unavailable_category_is_not_zero_alerts(status, message, outcome):
    fake = FakeGitHub(
        {
            "dependabot/alerts": [
                lambda _r: httpx.Response(status, json={"message": message})
            ],
            "code-scanning/alerts": [[code_alert(1)]],
        }
    )
    native, _ = await collect(
        fake, import_request(categories=["code-scanning", "dependabot"])
    )
    assert native["categories"]["dependabot"]["outcome"] == outcome
    assert native["categories"]["dependabot"]["records"] == []
    assert native["categories"]["code-scanning"]["outcome"] == "complete"
    observation, projection = alert_observation(
        AssessmentRequest.model_validate(assessment_request()),
        native,
        "art_native",
        datetime.now(UTC),
    )
    coverage = {row.tool: row.status for row in observation.coverage}
    assert coverage["github-dependabot"] != "complete"
    unverified = {row["category"]: row for row in projection["unverified"]}
    assert unverified["dependabot"]["outcome"] == outcome
    assert unverified["dependabot"]["nextStep"]


@pytest.mark.asyncio
async def test_malformed_and_missing_repository_are_distinct():
    fake = FakeGitHub({"code-scanning/alerts": [[{"state": "open"}]]})
    native, _ = await collect(fake, import_request(categories=["code-scanning"]))
    assert native["categories"]["code-scanning"]["outcome"] == "malformed_response"

    def missing(request):
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as client:
        native, _ = await collect_github_alerts(
            client, api_base=API, headers={}, request=import_request()
        )
    assert {row["outcome"] for row in native["categories"].values()} == {
        "repository_unavailable"
    }


@pytest.mark.asyncio
async def test_item_and_request_budgets_bound_output_and_record_continuation():
    pages = [[code_alert(n) for n in range(start, start + 5)] for start in (1, 6, 11)]
    fake = FakeGitHub({"code-scanning/alerts": pages})
    native, usage = await collect(
        fake, import_request(categories=["code-scanning"], maxItems=7)
    )
    code = native["categories"]["code-scanning"]
    assert code["outcome"] == "pagination_incomplete"
    assert len(code["records"]) <= 7 and usage["items"] <= 7
    assert code["continuation"]

    fake = FakeGitHub({"code-scanning/alerts": pages})
    native, usage = await collect(
        fake, import_request(categories=["code-scanning"], maxRequests=3)
    )
    assert native["categories"]["code-scanning"]["outcome"] == "pagination_incomplete"
    assert usage["requests"] == len(fake.requests) == 3


@pytest.mark.asyncio
async def test_transient_failures_retry_only_safe_reads_within_budget():
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json=[code_alert(1)])

    fake = FakeGitHub({"code-scanning/alerts": [flaky]})
    native, _ = await collect(fake, import_request(categories=["code-scanning"]))
    assert native["categories"]["code-scanning"]["outcome"] == "complete"
    assert len(calls) == 2 and {r.method for r in fake.requests} == {"GET"}


@pytest.mark.asyncio
async def test_next_link_cannot_leave_the_admitted_repository_endpoint():
    def first(_request):
        return httpx.Response(
            200,
            json=[code_alert(1)],
            headers={"Link": '<https://evil.example/repos/x/y/alerts>; rel="next"'},
        )

    fake = FakeGitHub({"code-scanning/alerts": [first]})
    native, _ = await collect(fake, import_request(categories=["code-scanning"]))
    assert native["categories"]["code-scanning"]["outcome"] == "malformed_response"
    assert all(request.url.host == "api.github.com" for request in fake.requests)


@pytest.mark.asyncio
async def test_secret_values_never_reach_native_evidence_or_projection():
    fake = FakeGitHub({"secret-scanning/alerts": [[secret_alert(4)]]})
    native, _ = await collect(fake, import_request(categories=["secret-scanning"]))
    observation, projection = alert_observation(
        AssessmentRequest.model_validate(assessment_request()),
        native,
        "art_native",
        datetime.now(UTC),
    )
    blob = json.dumps([native, projection, observation.model_dump(mode="json")])
    assert CANARY not in blob
    record = native["categories"]["secret-scanning"]["records"][0]
    assert "secret" not in record
    assert record["secretType"] == "github_personal_access_token"


@pytest.mark.asyncio
async def test_other_revision_alerts_are_labelled_not_confirmed_findings():
    fake = FakeGitHub(
        {
            "code-scanning/alerts": [
                [code_alert(1), code_alert(2, sha=OTHER), code_alert(3, path="x.py")]
            ],
            "dependabot/alerts": [[dependabot_alert(5)]],
        },
        default_tip=OTHER,
    )
    native, _ = await collect(
        fake,
        import_request(categories=["code-scanning", "dependabot"], revision=SUBJECT),
    )
    observation, projection = alert_observation(
        AssessmentRequest.model_validate(assessment_request()),
        native,
        "art_native",
        datetime.now(UTC),
    )
    assert [(f.tool, f.native_id) for f in observation.findings] == [
        ("github-code-scanning", "1"),
        # x.py is outside the admitted file scope; the repository asset holds it.
        ("github-code-scanning", "3"),
    ]
    assert observation.findings[1].asset == REPO
    other = {(row["category"], row["number"]) for row in projection["otherRevision"]}
    assert other == {("code-scanning", 2), ("dependabot", 5)}
    assert all(row.status == "partial" for row in observation.coverage)


@pytest.mark.asyncio
async def test_repeated_import_yields_identical_native_evidence_and_identities():
    routes = {
        "code-scanning/alerts": [[code_alert(1)]],
        "dependabot/alerts": [[dependabot_alert(5)]],
        "secret-scanning/alerts": [[]],
    }
    first, _ = await collect(FakeGitHub(routes))
    second, _ = await collect(FakeGitHub(routes))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    request = AssessmentRequest.model_validate(assessment_request())
    ids = [
        [
            f.finding_id
            for f in alert_observation(request, n, "r", datetime.now(UTC))[0].findings
        ]
        for n in (first, second)
    ]
    assert ids[0] == ids[1] and len(ids[0]) == 2


@pytest.mark.asyncio
async def test_correlation_requires_stable_identifiers_and_keeps_conflicts():
    fake = FakeGitHub(
        {
            "dependabot/alerts": [
                [
                    dependabot_alert(5),
                    dependabot_alert(
                        6, state="fixed", ghsa="GHSA-fixd-fixd-fixd", cve=None
                    ),
                ]
            ]
        }
    )
    native, _ = await collect(
        fake, import_request(categories=["dependabot"], state="all")
    )
    request = AssessmentRequest.model_validate(assessment_request())
    imported, _ = alert_observation(request, native, "r", datetime.now(UTC))
    local = Observation(
        scope=request.scope,
        observed_at=datetime.now(UTC),
        expires_at=datetime.now(UTC).replace(year=2099),
        coverage=[
            Coverage(
                source="repository",
                tool="trivy",
                assets=["poetry.lock"],
                rule_ids=[],
                all_rules=True,
                config_ref="trivy",
                status="complete",
            )
        ],
        findings=[
            Finding(
                source="repository",
                tool="trivy",
                rule_id=rule,
                asset="poetry.lock",
                native_id=rule,
                evidence_refs=["art_trivy"],
            )
            for rule in ("CVE-2026-0001", "GHSA-fixd-fixd-fixd", "CVE-similar")
        ],
        provenance={"scannerSummary": "art_trivy"},
    )
    result = correlate_alerts(native, imported, [local])
    assert [row["basis"] for row in result["matches"]] == [
        ["asset", "advisory:CVE-2026-0001"]
    ]
    assert [row["kind"] for row in result["conflicts"]] == [
        "github_closed_local_observed"
    ]
    # Correlation never replaces either source's facts.
    assert len(imported.findings) == 1 and len(local.findings) == 3


def assessment_request(**overrides):
    return {
        "scope": SCOPE,
        "tools": ["github-alerts"],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "githubAlerts": {
            "repository": REPO,
            "categories": ["code-scanning", "dependabot", "secret-scanning"],
        },
        **overrides,
    }


def test_github_alert_tool_requires_its_import_request():
    AssessmentRequest.model_validate(assessment_request())
    with pytest.raises(ValueError):
        AssessmentRequest.model_validate(assessment_request(githubAlerts=None))
    with pytest.raises(ValueError):
        AssessmentRequest.model_validate(
            {**assessment_request(), "tools": ["retained-report"], "reportRefs": ["r"]}
        )
    with pytest.raises(ValueError):
        GitHubAlertImport.model_validate({"repository": REPO, "maxItems": 5000})


def test_security_read_issues_only_read_alert_permissions():
    assert operations_to_permissions(["security_read"]) == {
        "security_events": "read",
        "vulnerability_alerts": "read",
        "secret_scanning_alerts": "read",
    }


@pytest.mark.asyncio
async def test_service_denied_acquisition_makes_no_request(monkeypatch):
    requests = []
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(
            transport=httpx.MockTransport(lambda r: requests.append(r)), **kw
        ),
    )

    async def denied(*_args, **_kwargs):
        raise ValueError("connection does not allow operation 'security_read'")

    monkeypatch.setattr(GitHubService, "_repository_reader", denied)
    native, _ = await GitHubService().read_security_alerts(
        import_request(), admitted_workflow_id="mm:run"
    )
    assert requests == []
    assert {row["outcome"] for row in native["categories"].values()} == {
        "authorization_unavailable"
    }
