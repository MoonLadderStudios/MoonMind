"""Issue #5045: GitHub alert import through admission, reader and artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.integration.security.test_recurring_assessments import invoke, put, read
from tests.unit.security.test_github_alert_import import (
    CANARY,
    OTHER,
    REPO,
    SCOPE,
    SUBJECT,
    FakeGitHub,
    code_alert,
    dependabot_alert,
    secret_alert,
)
from tests.unit.services.test_recurring_workflows_service import recurring_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
CONNECTION = "repository-connection:security-pat"
WITH_ALERTS = ("read", "security_read")


def analyses(sha=SUBJECT):
    return [
        {
            "id": 11,
            "ref": "refs/heads/main",
            "commit_sha": sha,
            "analysis_key": ".github/workflows/codeql.yml:analyze",
            "created_at": "2026-10-26T00:00:00Z",
            "tool": {"name": "CodeQL", "version": "2.19.0"},
            "results_count": 2,
            "rules_count": 400,
            "sarif_id": "sarif-1",
        }
    ]


def denied(_request):
    return httpx.Response(
        403, json={"message": "Resource not accessible by personal access token"}
    )


def rate_limited(_request):
    return httpx.Response(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1890000000"},
    )


class Upstream:
    """Swap controlled GitHub answers; every request reaches ``fake``."""

    def __init__(self, monkeypatch, fake):
        self.fake = fake
        original = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: original(
                transport=httpx.MockTransport(lambda r: self.fake(r)), **kwargs
            ),
        )


@pytest_asyncio.fixture
async def admitted(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
    monkeypatch.setenv("SELECTED_PAT", "selected-token")

    async def record(operations=WITH_ALERTS):
        connection = github_pat_connection(
            CONNECTION, "SELECTED_PAT", operations=operations
        )
        directory = tmp_path / "connections"
        directory.mkdir()
        return await record_repository_connections(
            monkeypatch,
            directory,
            connection,
            assignments=[
                github_repository_assignment(CONNECTION, REPO, operations=operations)
            ],
            admitted_runs={
                "assessment": {
                    "repository": {
                        "provider": "git",
                        "connectionRef": CONNECTION,
                        "repository": {"name": REPO},
                    }
                }
            },
        )

    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "retained"),
            )
            engines = []

            async def admit(**kwargs):
                engines.append(await record(**kwargs))

            yield SimpleNamespace(
                service=service, sessions=sessions, root=tmp_path, admit=admit
            )
            for engine in engines:
                await engine.dispose()


def request(**alerts):
    return {
        "scope": SCOPE,
        "tools": ["github-alerts"],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "githubAlerts": {"repository": REPO, **alerts},
    }


async def execution_bytes(service, workflow_id="assessment"):
    access = access_for_test(f"workflow:{workflow_id}")
    rows = await service.list_for_execution(
        namespace="default", workflow_id=workflow_id, run_id="run", access=access
    )
    bodies = []
    for row in rows:
        _, body = await service.read(artifact_id=row.artifact_id, access=access)
        bodies.append(body)
    return bodies


async def test_admitted_import_publishes_sanitized_evidence_and_projection(
    admitted, monkeypatch
):
    await admitted.admit()
    fake = FakeGitHub(
        {
            "code-scanning/alerts": [
                [code_alert(1), code_alert(2)],
                [code_alert(2), code_alert(3)],
            ],
            "code-scanning/analyses": [analyses()],
            "dependabot/alerts": [denied],
            "secret-scanning/alerts": [[secret_alert(4)]],
        }
    )
    Upstream(monkeypatch, fake)

    outputs = await invoke(admitted.service, request())

    github = outputs["githubAlerts"]
    assert github["subjectRevision"] == SUBJECT
    assert github["categories"]["code-scanning"] == {
        "outcome": "complete",
        "coverage": "complete",
        "findings": 3,
        "otherRevision": 0,
        "closed": 0,
        "outOfScope": 0,
    }
    assert github["categories"]["dependabot"]["outcome"] == "permission_denied"
    assert github["categories"]["dependabot"]["coverage"] == "authentication_failed"
    assert github["categories"]["secret-scanning"]["findings"] == 1
    assert [row["category"] for row in github["unverified"]] == ["dependabot"]
    assert "vulnerability_alerts" in github["unverified"][0]["nextStep"]
    # Read-only, on the admitted endpoint, with the selected credential only.
    assert {r.method for r in fake.requests} == {"GET"}
    assert {r.url.host for r in fake.requests} == {"api.github.com"}
    assert all(r.url.path.startswith(f"/repos/{REPO}") for r in fake.requests)
    assert {r.headers["Authorization"] for r in fake.requests} == {
        "Bearer selected-token"
    }
    comparison = await read(admitted.service, outputs["comparisonRef"])
    imported = [
        row["finding"]
        for row in comparison["findings"]
        if row["finding"]["source"] == "github"
    ]
    assert sorted((f["tool"], f["nativeId"]) for f in imported) == [
        ("github-code-scanning", "1"),
        ("github-code-scanning", "2"),
        ("github-code-scanning", "3"),
        ("github-secret-scanning", "4"),
    ]
    native = await read(admitted.service, github["nativeRef"])
    assert native["schemaVersion"] == "github-security-alerts/v1"
    assert native["categories"]["code-scanning"]["duplicates"] == 1
    assert native["analyses"]["records"][0]["sarifId"] == "sarif-1"
    work = await read(admitted.service, outputs["scanRef"])
    assert work["githubAlerts"]["interval"]["startedAt"]
    assert work["githubAlerts"]["usage"]["requests"] == len(fake.requests)
    # Secret values never reach any artifact or the agent-visible outputs.
    for body in await execution_bytes(admitted.service):
        assert CANARY.encode() not in body
    assert CANARY not in json.dumps(outputs)

    # Evidence survives the job: a new service over the same retained store
    # reads it without the session, transport or workspace that produced it.
    async with admitted.sessions() as session:
        later = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(admitted.root / "retained"),
        )
        assert (await read(later, github["nativeRef"])) == native
        assert (await read(later, outputs["scanRef"]))["githubAlerts"]["nativeRef"]


async def test_retry_reuses_partial_import_and_cannot_widen_scope(
    admitted, monkeypatch
):
    await admitted.admit()
    first = FakeGitHub(
        {
            "code-scanning/alerts": [[code_alert(1)], rate_limited],
            "dependabot/alerts": [denied],
        }
    )
    upstream = Upstream(monkeypatch, first)
    payload = request(categories=["code-scanning", "dependabot"])
    original = await invoke(admitted.service, payload, key="attempt-1")
    code = original["githubAlerts"]["categories"]["code-scanning"]
    assert code["outcome"] == "rate_limited" and code["coverage"] == "partial"
    assert code["findings"] == 1

    # The provider now answers everything and another credential is present.
    upstream.fake = FakeGitHub(
        {
            "code-scanning/alerts": [[code_alert(1)], [code_alert(2)]],
            "dependabot/alerts": [[dependabot_alert(5)]],
        }
    )
    monkeypatch.setenv("GITHUB_TOKEN", "different-ambient-token")
    retried = await invoke(admitted.service, payload, key="attempt-2")
    assert upstream.fake.requests == []
    assert retried["scanRef"] == original["scanRef"]
    assert retried["githubAlerts"] == original["githubAlerts"]


async def test_connection_without_security_read_reaches_no_provider(
    admitted, monkeypatch
):
    await admitted.admit(operations=("read",))
    fake = FakeGitHub({"code-scanning/alerts": [[code_alert(1)]]})
    Upstream(monkeypatch, fake)
    outputs = await invoke(admitted.service, request())
    assert fake.requests == []
    assert {
        row["outcome"] for row in outputs["githubAlerts"]["categories"].values()
    } == {"authorization_unavailable"}
    assert all(
        "security_read" in row["nextStep"]
        for row in outputs["githubAlerts"]["unverified"]
    )
    assert outputs["coverageStatus"] == "stale_or_partial"


async def test_new_execution_rereads_with_stable_upstream_identity(
    admitted, monkeypatch
):
    await admitted.admit()
    routes = {
        "code-scanning/alerts": [[code_alert(1)]],
        "code-scanning/analyses": [analyses()],
        "dependabot/alerts": [[dependabot_alert(5)]],
        "secret-scanning/alerts": [[]],
    }
    upstream = Upstream(monkeypatch, FakeGitHub(routes))
    first = await invoke(admitted.service, request())
    upstream.fake = FakeGitHub(routes)
    # A later scheduled occurrence is a new run of the admitted workflow.
    second = await invoke(
        admitted.service,
        {**request(), "baselineRef": first["comparisonRef"]},
        run_id="run-2",
    )
    assert upstream.fake.requests, "a later execution rereads live alerts"
    assert (await read(admitted.service, first["githubAlerts"]["nativeRef"])) == (
        await read(admitted.service, second["githubAlerts"]["nativeRef"])
    )
    comparison = await read(admitted.service, second["comparisonRef"])
    states = {
        row["finding"]["nativeId"]: row["state"]
        for row in comparison["findings"]
        if row["finding"]["source"] == "github"
    }
    assert states == {"1": "still_observed", "5": "still_observed"}
    assert second["counts"]["new"] == 0


async def test_complete_import_is_complete_and_later_absence_comparable(
    admitted, monkeypatch
):
    await admitted.admit()
    routes = {
        "code-scanning/alerts": [[code_alert(1), code_alert(2)]],
        "code-scanning/analyses": [analyses()],
        "dependabot/alerts": [[dependabot_alert(5)]],
        "secret-scanning/alerts": [[]],
    }
    upstream = Upstream(monkeypatch, FakeGitHub(routes))
    first = await invoke(admitted.service, request())
    # The first live import is complete coverage, not stale or partial.
    assert first["scanOutcome"] == "completed"
    assert first["coverageStatus"] == "complete"

    upstream.fake = FakeGitHub({**routes, "code-scanning/alerts": [[code_alert(1)]]})
    second = await invoke(
        admitted.service,
        {**request(), "baselineRef": first["comparisonRef"]},
        run_id="run-2",
    )
    comparison = await read(admitted.service, second["comparisonRef"])
    states = {
        row["finding"]["nativeId"]: row["state"]
        for row in comparison["findings"]
        if row["finding"]["source"] == "github"
    }
    assert states == {
        "1": "still_observed",
        "2": "absent_comparable",
        "5": "still_observed",
    }


async def test_revision_mismatch_stays_labelled_and_output_bounded(
    admitted, monkeypatch
):
    await admitted.admit()
    pages = [[code_alert(n, sha=OTHER) for n in range(s, s + 50)] for s in (1, 51, 101)]
    fake = FakeGitHub(
        {
            "code-scanning/alerts": pages,
            "code-scanning/analyses": [analyses(OTHER)],
            "dependabot/alerts": [[dependabot_alert(5)]],
        },
        default_tip=OTHER,
    )
    Upstream(monkeypatch, fake)
    outputs = await invoke(
        admitted.service,
        request(
            revision=SUBJECT,
            categories=["dependabot", "code-scanning"],
            # One shared item budget: Dependabot's alert, then 80 code alerts.
            maxItems=81,
        ),
    )
    categories = outputs["githubAlerts"]["categories"]
    assert categories["code-scanning"]["outcome"] == "pagination_incomplete"
    assert categories["code-scanning"]["findings"] == 0
    assert categories["code-scanning"]["otherRevision"] == 80
    assert categories["dependabot"]["findings"] == 0
    assert categories["dependabot"]["coverage"] == "partial"
    assert outputs["counts"]["new"] == 0
    assert len(json.dumps(outputs)) < 8192
    work = await read(admitted.service, outputs["scanRef"])
    assert len(work["githubAlerts"]["otherRevision"]) == 64
    assert work["githubAlerts"]["listsTruncated"] is True
    assert work["githubAlerts"]["categories"]["code-scanning"]["continuation"]


async def test_local_scanner_and_imported_alert_both_survive_correlation(
    admitted, monkeypatch
):
    await admitted.admit()
    now = datetime.now(UTC)
    local = {
        "schemaVersion": "security-observation/v1",
        "scope": SCOPE,
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=1)).isoformat(),
        "coverage": [
            {
                "source": "repository",
                "tool": "trivy",
                "assets": ["poetry.lock"],
                "ruleIds": [],
                "allRules": True,
                "configRef": "trivy:default",
                "status": "complete",
            }
        ],
        "findings": [
            {
                "source": "repository",
                "tool": "trivy",
                "ruleId": "CVE-2026-0001",
                "asset": "poetry.lock",
                "nativeId": "poetry.lock:example:1.0",
                "evidenceRefs": ["art_trivy_native"],
            }
        ],
        "provenance": {"scannerSummary": "art_trivy_summary"},
    }
    Upstream(
        monkeypatch,
        FakeGitHub(
            {
                "code-scanning/alerts": [[]],
                "code-scanning/analyses": [analyses()],
                "dependabot/alerts": [[dependabot_alert(5)]],
            }
        ),
    )
    outputs = await invoke(
        admitted.service,
        {
            **request(categories=["code-scanning", "dependabot"]),
            "tools": ["retained-report", "github-alerts"],
            "reportRefs": [await put(admitted.service, local)],
        },
    )
    work = await read(admitted.service, outputs["scanRef"])
    correlation = work["githubAlerts"]["correlation"]
    assert [row["basis"] for row in correlation["matches"]] == [
        ["asset", "advisory:CVE-2026-0001"]
    ]
    assert correlation["conflicts"] == []
    comparison = await read(admitted.service, outputs["comparisonRef"])
    assert sorted(row["finding"]["source"] for row in comparison["findings"]) == [
        "github",
        "repository",
    ]
