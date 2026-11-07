"""MoonLadderStudios/MoonMind#5190: admitted GitHub -> retained period artifacts."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.artifact_access import access_for_test
from tests.integration.security import test_github_alert_import_journey as alerts
from tests.integration.security.test_github_alert_import_journey import Upstream
from tests.integration.security.test_recurring_assessments import invoke, read
from tests.unit.security.test_github_change_control import request

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
admitted = alerts.admitted
REPO = "acme/repo"
SINCE = "2026-11-01T00:00:00Z"
UNTIL = "2026-11-06T00:00:00Z"
MERGE, HEAD, DIRECT = "a" * 40, "b" * 40, "c" * 40


@pytest.fixture(autouse=True)
def collection_clock(monkeypatch):
    from moonmind.security import github_change_control as collector

    monkeypatch.setattr(collector, "utcnow", lambda: datetime(2026, 11, 7, tzinfo=UTC))


def pr(number, at=SINCE, sha=MERGE):
    return {
        "id": number + 100,
        "number": number,
        "merged_at": at,
        "created_at": "2026-10-30T00:00:00Z",
        "updated_at": at,
        "merge_commit_sha": sha,
        "head": {"sha": HEAD},
        "base": {"ref": "main"},
        "labels": [{"name": "emergency"}] if number == 2 else [],
        "body": "PRIVATE_BODY_MUST_NOT_BE_RETAINED",
    }


class GitHub:
    def __init__(
        self,
        *,
        deny=None,
        throttle=False,
        pagination=None,
        bad_next=None,
        deploy=True,
        status="success",
        production=True,
    ):
        self.requests, self.counts = [], Counter()
        self.deny, self.throttle = deny, throttle
        self.pagination, self.bad_next = pagination, bad_next
        self.deploy, self.status, self.production = deploy, status, production
        self.interrupt = None
        self.overrides = {}

    def __call__(self, req):
        self.requests.append(req)
        assert req.method == "GET"
        assert req.url.host == "api.github.com"
        assert req.headers["authorization"] == "Bearer selected-token"
        assert req.url.path.startswith(f"/repos/{REPO}")
        path = req.url.path.removeprefix(f"/repos/{REPO}").lstrip("/")
        page = int(req.url.params.get("page", 1))
        self.counts[(path, page)] += 1
        if self.interrupt == (path, page):
            self.interrupt = None
            raise asyncio.CancelledError()
        if (path, page) in self.overrides:
            return self.overrides[(path, page)](req)
        if self.deny == path:
            return httpx.Response(
                404 if not path else 403, json={"message": "PRIVATE_DENIED_BODY"}
            )
        if self.throttle and path == "pulls" and self.counts[(path, page)] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if not path:
            return httpx.Response(
                200, json={"id": 10, "full_name": REPO, "default_branch": "main"}
            )
        if path == "pulls":
            assert req.url.params["state"] == "closed"
            rows = [pr(1), pr(2, sha="d" * 40), pr(3, UNTIL)]
            if self.pagination == "pulls":
                if page == 1:
                    target = self.bad_next or str(req.url.copy_set_param("page", "2"))
                    return httpx.Response(
                        200, json=rows[:1], headers={"Link": f'<{target}>; rel="next"'}
                    )
                rows = [rows[0], *rows[1:]]
            return httpx.Response(200, json=rows)
        if path == "commits":
            assert req.url.params["since"]
            assert req.url.params["until"]
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": sha,
                        "commit": {"committer": {"date": SINCE}},
                        "parents": [],
                    }
                    for sha in (MERGE, DIRECT)
                ],
            )
        if path.endswith("/pulls"):
            return httpx.Response(200, json=[])
        if path.endswith("/reviews"):
            return httpx.Response(
                200,
                json=(
                    [
                        {
                            "id": 20,
                            "state": "APPROVED",
                            "commit_id": HEAD,
                            "submitted_at": "2026-10-31T20:00:00Z",
                            "user": {"id": 30},
                            "body": "PRIVATE_REVIEW_BODY",
                        }
                    ]
                    if path.startswith("pulls/1/")
                    else []
                ),
            )
        if path.endswith("/check-runs"):
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "check_runs": [
                        {
                            "id": 40,
                            "head_sha": path.split("/")[1],
                            "status": "completed",
                            "conclusion": "success",
                            "completed_at": SINCE,
                        }
                    ],
                },
            )
        if path.endswith("/statuses") and path.startswith("commits/"):
            return httpx.Response(200, json=[])
        if path == "actions/runs":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "workflow_runs": [
                        {
                            "id": 50,
                            "head_sha": req.url.params["head_sha"],
                            "run_attempt": 1,
                            "status": "completed",
                            "conclusion": "success",
                            "created_at": SINCE,
                        }
                    ],
                },
            )
        if path == "environments":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "environments": [{"id": 60, "name": "production"}],
                },
            )
        if path.startswith("rules/branches/"):
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "pull_request",
                        "parameters": {"required_approving_review_count": 1},
                    }
                ],
            )
        if path == "deployments":
            assert req.url.params["environment"] == "production"
            return httpx.Response(
                200,
                json=(
                    [
                        {
                            "id": 70,
                            "sha": MERGE,
                            "environment": "production",
                            "production_environment": self.production,
                            "created_at": SINCE,
                        }
                    ]
                    if self.deploy
                    else []
                ),
            )
        if path == "deployments/70/statuses":
            return httpx.Response(
                200, json=[{"id": 71, "state": self.status, "created_at": SINCE}]
            )
        raise AssertionError(str(req.url))


async def collect(admitted, monkeypatch, fake=None, **selection):
    await admitted.admit(operations=("read",))
    fake = fake or GitHub()
    Upstream(monkeypatch, fake)
    payload = request(**selection)
    payload["scope"]["assets"] = payload["githubChangeControl"]["repositories"]
    outputs = await invoke(admitted.service, payload)
    projection = outputs["githubChangeControl"]
    return fake, outputs, await read(admitted.service, projection["evidenceRef"])


async def test_synthetic_period_preserves_sources_samples_links_and_authority(
    admitted, monkeypatch
):
    fake, outputs, report = await collect(
        admitted, monkeypatch, GitHub(pagination="pulls"), sampleSize=1
    )
    projection = outputs["githubChangeControl"]
    population = await read(admitted.service, report["populationRef"])
    sample = await read(admitted.service, report["sampleRef"])
    assert (
        len(population["changes"]) == 2
    )  # inclusive start, exclusive end, duplicate merged PR
    assert population["coverage"][f"{REPO}:pulls"]["pages"] == 2
    assert population["coverageStatus"] == "complete"
    assert population["usage"]["requests"] == len(fake.requests)
    assert sample["populationRef"] == report["populationRef"]
    assert len(sample["selectedChangeIds"]) == 1
    assert sample["rationale"] and sample["limitations"] == population["limitations"]
    linked, unlinked = population["changes"]
    assert linked["productionLink"] == "recorded_success"
    assert linked["deploymentIds"] == [70]
    assert linked["approval"] == "observed"
    assert unlinked["productionLink"] == "unresolved"
    assert {e["kind"] for e in report["exceptions"]} >= {
        "possible_direct_push",
        "emergency_change",
        "missing_review",
        "missing_deployment",
    }
    assert all(e["evidenceRefs"] for e in report["exceptions"])
    assert all(e["reason"] for e in report["exceptions"])
    deployment_verification = population["verification"][
        population["deployments"][0]["verificationRef"]
    ]
    assert deployment_verification["checks"][0]["headSha"] == MERGE
    for page_ref in population["pageRefs"]:
        page = await read(admitted.service, page_ref)
        assert page["query"] is not None and page["collectedAt"]
        assert page["sourceUrl"].startswith("https://api.github.com/repos/")
        assert "PRIVATE_" not in json.dumps(page)
    evidence = await read(admitted.service, projection["reviewEvidenceRef"])
    assert evidence["kind"] == "technical_assessment"
    assert evidence["requirementIds"] == ["soc2.CC8.1", "iso27001.A.8.32"]
    assert evidence["provenance"]["sourceRef"] == projection["evidenceRef"]
    assert evidence["systems"] == [REPO]
    assert evidence["period"] == {"start": "2026-11-01", "end": "2026-11-05"}
    proof = await admitted.service.dependency_proof(
        root_artifact_id=projection["evidenceRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(population["pageRefs"]) <= {p.artifact_id for p in proof}
    _, summary = await admitted.service.read(
        artifact_id=outputs["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=access_for_test("workflow:assessment"),
    )
    assert projection["evidenceRef"].encode() in summary
    assert b"unresolved" in summary
    assert projection["populationRef"].encode() in summary
    with pytest.raises(TemporalArtifactAuthorizationError):
        await admitted.service.read(
            artifact_id=projection["evidenceRef"],
            access=access_for_test("workflow:other"),
        )


@pytest.mark.parametrize(
    "options", [{"deploy": False}, {"status": "failure"}, {"production": False}]
)
async def test_approved_merge_never_proves_production_without_successful_production_record(
    admitted, monkeypatch, options
):
    _, _, report = await collect(admitted, monkeypatch, GitHub(**options))
    population = await read(admitted.service, report["populationRef"])
    change = population["changes"][0]
    assert change["approval"] == "observed"
    assert change["productionLink"] == "unresolved"
    assert report["controlOperation"] == "not_established"


@pytest.mark.parametrize(
    "deny",
    ["", "pulls/1/reviews", "rules/branches/main", "deployments", "environments"],
)
async def test_hidden_or_denied_endpoints_are_incomplete_not_empty_success(
    admitted, monkeypatch, deny
):
    _, _, report = await collect(admitted, monkeypatch, GitHub(deny=deny))
    population = await read(admitted.service, report["populationRef"])
    assert population["coverageStatus"] == "incomplete"
    assert any(
        r["status"] in {"permission_denied", "not_found"}
        for r in population["coverage"].values()
    )
    assert "PRIVATE_DENIED_BODY" not in json.dumps(population)
    assert report["controlOperation"] == "not_established"


async def test_throttling_retries_boundedly_without_duplicate_population(
    admitted, monkeypatch
):
    fake, _, report = await collect(admitted, monkeypatch, GitHub(throttle=True))
    population = await read(admitted.service, report["populationRef"])
    assert fake.counts[("pulls", 1)] == 2
    assert len(population["changes"]) == 2
    assert population["usage"]["requests"] == len(fake.requests)
    assert population["coverageStatus"] == "complete"
    progress = await read(admitted.service, population["progressRef"])
    assert any(r["responseStatus"] == 429 for r in progress["receipts"])


@pytest.mark.parametrize(
    "bad_next",
    [
        "https://attacker.test/repos/acme/repo/pulls?page=2",
        "https://api.github.com/repos/acme/repo/pulls?page=9&state=closed",
    ],
)
async def test_missing_or_unsafe_pages_retain_first_page_and_incomplete_coverage(
    admitted, monkeypatch, bad_next
):
    fake, _, report = await collect(
        admitted, monkeypatch, GitHub(pagination="pulls", bad_next=bad_next)
    )
    population = await read(admitted.service, report["populationRef"])
    assert len(population["changes"]) == 1
    assert population["coverage"][f"{REPO}:pulls"]["status"] == "unsafe_pagination"
    assert population["coverageStatus"] == "incomplete"
    assert all(r.url.host == "api.github.com" for r in fake.requests)


async def test_saved_pages_restart_under_original_window_deadline_and_request_budget(
    admitted, monkeypatch
):
    await admitted.admit(operations=("read",))
    fake = GitHub(pagination="pulls")
    fake.interrupt = ("pulls", 2)
    Upstream(monkeypatch, fake)
    payload = request(maxRequests=80)
    # Call through the production dispatcher; cancellation occurs after page 1 was retained.
    with pytest.raises(asyncio.CancelledError):
        await invoke(admitted.service, payload)
    outputs = await invoke(admitted.service, payload)
    report = await read(admitted.service, outputs["githubChangeControl"]["evidenceRef"])
    population = await read(admitted.service, report["populationRef"])
    assert fake.counts[("pulls", 1)] == 1
    assert fake.counts[("pulls", 2)] == 2
    assert len(population["changes"]) == 2
    assert population["usage"]["requests"] == len(fake.requests)
    assert population["scope"]["since"].startswith("2026-11-01")
    assert population["scope"]["maxRequests"] == 80
    prior_calls = len(fake.requests)
    assert (await invoke(admitted.service, payload))["githubChangeControl"] == outputs[
        "githubChangeControl"
    ]
    assert len(fake.requests) == prior_calls


async def test_unadmitted_repository_does_not_use_ambient_or_other_repository_authority(
    admitted, monkeypatch
):
    fake, _, report = await collect(
        admitted, monkeypatch, repositories=[REPO, "hidden/repo"]
    )
    population = await read(admitted.service, report["populationRef"])
    assert (
        population["coverage"]["hidden/repo:repository"]["status"]
        == "authorization_unavailable"
    )
    assert population["coverageStatus"] == "incomplete"
    assert all(r.url.path.startswith(f"/repos/{REPO}") for r in fake.requests)


@pytest.mark.parametrize("status", [401, 403, 503])
async def test_missing_later_page_retains_population_and_original_failure(
    admitted, monkeypatch, status
):
    fake = GitHub(pagination="pulls")
    fake.overrides[("pulls", 2)] = lambda _: httpx.Response(status)
    fake, _, report = await collect(admitted, monkeypatch, fake)
    population = await read(admitted.service, report["populationRef"])
    assert len(population["changes"]) == 1
    assert population["coverageStatus"] == "incomplete"
    assert fake.counts[("pulls", 2)] == (3 if status == 503 else 1)
    progress = await read(admitted.service, population["progressRef"])
    assert any(r["responseStatus"] == status for r in progress["receipts"])


async def test_counted_endpoint_missing_page_is_incomplete(admitted, monkeypatch):
    fake = GitHub()
    fake.overrides[("environments", 1)] = lambda _: httpx.Response(
        200, json={"total_count": 2, "environments": [{"id": 60, "name": "production"}]}
    )
    _, _, report = await collect(admitted, monkeypatch, fake)
    population = await read(admitted.service, report["populationRef"])
    assert population["coverage"][f"{REPO}:environments"]["status"] == "missing_pages"
    assert population["coverageStatus"] == "incomplete"


@pytest.mark.parametrize(
    "selection,expected",
    [
        ({"maxRequests": 3}, "request_budget"),
        ({"maxPages": 1}, "page_budget"),
        ({"maxBytes": 2000}, "byte_budget"),
        ({"maxItems": 2}, "item_budget"),
    ],
)
async def test_collection_bounds_retain_partial_sources(
    admitted, monkeypatch, selection, expected
):
    fake, _, report = await collect(
        admitted, monkeypatch, GitHub(pagination="pulls"), **selection
    )
    population = await read(admitted.service, report["populationRef"])
    assert population["coverageStatus"] == "incomplete"
    assert expected in {r["status"] for r in population["coverage"].values()}
    assert population["usage"]["requests"] == len(fake.requests)
    assert population["usage"]["requests"] <= population["scope"]["maxRequests"]
    assert population["usage"]["bytes"] <= population["scope"]["maxBytes"]
    assert population["usage"]["items"] <= population["scope"]["maxItems"]


async def test_restart_does_not_extend_expired_collection_deadline(
    admitted, monkeypatch
):
    from moonmind.security import github_change_control as collector

    now = datetime(2026, 11, 7, tzinfo=UTC)
    monkeypatch.setattr(collector, "utcnow", lambda: now)
    await admitted.admit(operations=("read",))
    fake = GitHub(pagination="pulls")
    fake.interrupt = ("pulls", 2)
    Upstream(monkeypatch, fake)
    with pytest.raises(asyncio.CancelledError):
        await invoke(admitted.service, request(maxSeconds=20))
    prior = len(fake.requests)
    now += timedelta(seconds=21)
    outputs = await invoke(admitted.service, request(maxSeconds=20))
    report = await read(admitted.service, outputs["githubChangeControl"]["evidenceRef"])
    population = await read(admitted.service, report["populationRef"])
    assert len(fake.requests) == prior
    assert population["startedAt"] == "2026-11-07T00:00:00+00:00"
    assert population["deadline"] == "2026-11-07T00:00:20+00:00"
    assert population["coverage"][f"{REPO}:pulls"]["status"] == "time_budget"
    assert len(population["changes"]) == 1


async def test_period_artifact_is_consumed_by_existing_iso_framework_and_soc2_evidence_contract(
    admitted, monkeypatch
):
    from moonmind.security.frameworks import EvidencePeriod, FrameworkEvidence
    from tests.unit.security.test_ukgc_rts import fixture

    await admitted.admit(operations=("read",))
    Upstream(monkeypatch, GitHub())
    payload = request(requirementIds=["8.32", "CC8.1"])
    framework = fixture()["framework"]
    for row in framework["systemInputs"]["criticalSystems"]:
        row["systems"] = [REPO]
    for row in framework["systemInputs"]["thirdParties"]:
        row["systems"] = [REPO]
    framework.update(
        evidenceRefs=[],
        auditRef=None,
        period={"start": "2026-11-01", "end": "2026-11-05"},
    )
    payload["framework"] = framework
    outputs = await invoke(admitted.service, payload)
    projection = outputs["githubChangeControl"]
    preparation = await read(admitted.service, outputs["frameworkRef"])
    assert outputs["frameworkReadiness"] == "incomplete"
    assert any(
        e["provenance"]["sourceRef"] == projection["evidenceRef"]
        for e in preparation["evidence"]
    )
    assert all(
        row["status"] != "evidence_recorded" for row in preparation["requirements"]
    )
    evidence = FrameworkEvidence.model_validate(
        await read(admitted.service, projection["reviewEvidenceRef"])
    )
    assert "CC8.1" in evidence.requirement_ids and "8.32" in evidence.requirement_ids
    assert not evidence.covers(
        "CC8.1", [REPO], EvidencePeriod(start="2026-11-01", end="2026-11-05")
    )


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("commits", [{"sha": DIRECT, "commit": {"committer": {"date": "not-a-time"}}}]),
        (
            "actions/runs",
            {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 50,
                        "head_sha": HEAD,
                        "status": "ghp_" + "Z" * 36,
                        "created_at": SINCE,
                    }
                ],
            },
        ),
    ],
)
async def test_malformed_source_facts_cannot_be_empty_or_complete_evidence(
    admitted, monkeypatch, endpoint, payload
):
    fake = GitHub()
    fake.overrides[(endpoint, 1)] = lambda _: httpx.Response(200, json=payload)
    _, _, report = await collect(admitted, monkeypatch, fake)
    population = await read(admitted.service, report["populationRef"])
    assert population["coverageStatus"] == "incomplete"
    for ref in population["pageRefs"]:
        assert "ghp_" not in json.dumps(await read(admitted.service, ref))


async def test_verification_for_different_commit_is_a_gap_with_retained_identity(
    admitted, monkeypatch
):
    fake = GitHub()
    fake.overrides[(f"commits/{MERGE}/check-runs", 1)] = lambda _: httpx.Response(
        200,
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "id": 40,
                    "head_sha": DIRECT,
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
        },
    )
    _, _, report = await collect(admitted, monkeypatch, fake)
    population = await read(admitted.service, report["populationRef"])
    assert population["coverageStatus"] == "incomplete"
    assert (
        population["coverage"][f"{REPO}:checks:{MERGE}"]["status"]
        == "identity_mismatch"
    )
    assert (
        population["verification"][f"{REPO}:{MERGE}"]["checks"][0]["headSha"] == DIRECT
    )


async def test_shared_verification_has_one_population_owner(admitted, monkeypatch):
    _, _, report = await collect(admitted, monkeypatch)
    population = await read(admitted.service, report["populationRef"])
    assert (
        len(population["verification"]) == 3
    )  # two merge commits and the shared PR head
    assert all(
        f"{REPO}:{HEAD}" in change["verificationRefs"]
        for change in population["changes"]
    )
    assert population["deployments"][0]["verificationRef"] == f"{REPO}:{MERGE}"


async def test_large_population_parts_retain_sources_and_replay_without_collection(
    admitted, monkeypatch
):
    from moonmind.security import github_change_control as collector

    monkeypatch.setattr(collector, "_POPULATION_PART_BYTES", 1024, raising=False)
    fake, outputs, report = await collect(admitted, monkeypatch)
    manifest = await read(admitted.service, report["populationRef"])
    assert manifest["schemaVersion"] == "github-change-control-population-manifest/v1"
    parts = []
    for ref in manifest["partRefs"]:
        _, body = await admitted.service.read(
            artifact_id=ref, access=access_for_test("workflow:assessment")
        )
        assert len(body) <= 1024
        parts.append(body)
    population = json.loads(b"".join(parts))
    assert len(population["changes"]) == 2
    assert population["pageRefs"] == manifest["pageRefs"]
    proof = await admitted.service.dependency_proof(
        root_artifact_id=outputs["githubChangeControl"]["evidenceRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(manifest["partRefs"]) <= {p.artifact_id for p in proof}
    calls = len(fake.requests)
    repeated = await invoke(admitted.service, request())
    assert repeated["githubChangeControl"] == outputs["githubChangeControl"]
    assert len(fake.requests) == calls
