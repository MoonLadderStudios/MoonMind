"""MoonLadderStudios/MoonMind#5191: Activity -> Secrets -> IdP -> retained review evidence."""

import asyncio
import json

import httpx
import pytest
from temporalio.exceptions import ApplicationError

from moonmind.workflows.skills.security_assessment import AssessmentExecution
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.integration.security.test_recurring_assessments import (
    environment,
    invoke,
    put,
    read,
)
from tests.unit.security.test_identity_lifecycle import (
    NOW,
    ORG,
    PERSONNEL_CANARY,
    SECRET_CANARY,
    SINCE,
    UNTIL,
    api_events,
    api_users,
    request,
    workforce,
)
from tests.unit.security.test_ukgc_rts import fixture as framework_fixture

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class Okta:
    def __init__(
        self,
        monkeypatch,
        *,
        deny=None,
        second_page_failure=False,
        bad_next=None,
        users=None,
        events=None,
    ):
        self.requests = []
        self.deny = deny
        self.second_page_failure = second_page_failure
        self.bad_next = bad_next
        self.users = api_users() if users is None else users
        self.events = api_events() if events is None else events
        original = httpx.AsyncClient

        def client(**kwargs):
            assert kwargs.pop("proxy") == "http://identity-proxy:3128"
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            return original(
                transport=httpx.MockTransport(lambda r: self.respond(r)), **kwargs
            )

        monkeypatch.setattr(httpx, "AsyncClient", client)

    def respond(self, req):
        self.requests.append(req)
        assert req.method == "GET"
        assert req.url.host == ORG
        assert req.headers["Authorization"] == "Bearer " + SECRET_CANARY
        if self.deny and self.deny in req.url.path:
            return httpx.Response(403, json={"errorSummary": SECRET_CANARY})
        if req.url.path == "/api/v1/users":
            assert req.url.params["fields"] == "id,status"
            if req.url.params.get("filter"):
                return httpx.Response(
                    200,
                    json=[u for u in self.users if u.get("status") == "DEPROVISIONED"],
                )
            users = [u for u in self.users if u.get("status") != "DEPROVISIONED"]
            if req.url.params.get("after"):
                if self.second_page_failure:
                    return httpx.Response(503, text=SECRET_CANARY)
                return httpx.Response(200, json=users[2:])
            next_url = self.bad_next or str(req.url.copy_set_param("after", "page2"))
            return httpx.Response(
                200, json=users[:2], headers={"Link": f'<{next_url}>; rel="next"'}
            )
        if req.url.path.endswith("/groups"):
            return httpx.Response(
                200, json=[{"id": "admins", "profile": {"name": PERSONNEL_CANARY}}]
            )
        if req.url.path.endswith("/roles"):
            assert not req.url.params
            return httpx.Response(
                200,
                json=[
                    {"id": "role1", "type": "SUPER_ADMIN", "label": PERSONNEL_CANARY}
                ],
            )
        if req.url.path == "/api/v1/logs":
            assert req.url.params["since"]
            assert req.url.params["until"].startswith("2026-11-06")
            assert req.url.params["sortOrder"] == "ASCENDING"
            return httpx.Response(200, json=self.events)
        raise AssertionError(req.url.path)


async def admission(environment, monkeypatch, *, data=None, **options):
    from api_service.services.secrets import SecretsService
    from moonmind.auth.resolvers import db_resolver
    from moonmind.security import identity_lifecycle

    monkeypatch.setattr(db_resolver, "async_session_maker", environment.sessions)
    monkeypatch.setattr(identity_lifecycle, "utcnow", lambda: NOW)
    monkeypatch.setenv("MOONMIND_EGRESS_PROXY", "http://identity-proxy:3128")
    monkeypatch.setenv(
        "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES",
        json.dumps(
            [
                {
                    "policyRef": "identity-read",
                    "allowedWorkflowIds": ["assessment"],
                    "allowedImageSourceRefs": ["identity-evidence"],
                    "destinations": [
                        {
                            "host": ORG,
                            "purpose": "provider-api",
                            "protocol": "https",
                            "port": 443,
                        }
                    ],
                }
            ]
        ),
    )
    async with environment.sessions() as session:
        await SecretsService.create_secret(session, "okta-reader", SECRET_CANARY)
    payload = request(await put(environment.service, data or workforce()))
    payload["identityLifecycle"].update(options)
    return payload


async def report(service, outputs):
    return await read(service, outputs["identityLifecycle"]["evidenceRef"])


async def test_supported_journey_uses_real_secrets_artifacts_and_review(
    environment, monkeypatch, caplog
):
    caplog.set_level("DEBUG", logger="moonmind.security")
    caplog.set_level("DEBUG", logger="httpx")
    payload = await admission(environment, monkeypatch)
    fake = Okta(monkeypatch)
    outputs = await invoke(environment.service, payload)
    identity = await report(environment.service, outputs)
    assert identity["schemaVersion"] == "identity-lifecycle-evidence/v1"
    assert identity["controlOperation"] == "not_established"
    assert len(identity["accounts"]) == len(api_users())
    assert identity["accounts"][0]["privileged"] is True
    assert identity["requestedPeriod"] == {
        "since": "2026-11-01T00:00:00+00:00",
        "until": "2026-11-06T00:00:00+00:00",
    }
    assert identity["coverage"]["users"]["pages"] == 2
    assert identity["coverage"]["deprovisioned"]["status"] == "complete"
    assert identity["coverage"]["logs"]["status"] == "complete"
    assert (
        identity["timestampSemantics"]["audit"]
        == "published_event_time_not_effective_removal"
    )
    assert identity["retention"]["days"] == 90
    assert identity["reviewRequests"]
    assert {"unknown_effective_date", "missing_historical_event"} <= set(
        identity["limitations"]
    )
    assert all(r["evidenceRefs"] for r in identity["lifecycle"])
    assert outputs["identityLifecycle"]["counts"]["policyExceptions"] == 1
    assert "u-leave" not in json.dumps(outputs)
    review = await read(
        environment.service, outputs["identityLifecycle"]["reviewEvidenceRef"]
    )
    assert review["kind"] == "technical_assessment"
    assert (
        review["provenance"]["sourceRef"] == outputs["identityLifecycle"]["evidenceRef"]
    )
    proof = await environment.service.dependency_proof(
        root_artifact_id=outputs["identityLifecycle"]["evidenceRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert payload["identityLifecycle"]["workforceRef"] in {
        p.artifact_id for p in proof
    }
    for ref in identity["pageRefs"]:
        _, body = await environment.service.read(
            artifact_id=ref, access=access_for_test("workflow:assessment")
        )
        assert PERSONNEL_CANARY.encode() not in body
        assert SECRET_CANARY.encode() not in body
    assert PERSONNEL_CANARY not in json.dumps(identity)
    assert SECRET_CANARY not in json.dumps(identity)
    assert SECRET_CANARY not in caplog.text
    assert PERSONNEL_CANARY not in caplog.text
    summary_ref = outputs["reportBundle"]["primary_report_ref"]["artifact_id"]
    _, summary = await environment.service.read(
        artifact_id=summary_ref, access=access_for_test("workflow:assessment")
    )
    for value in (SECRET_CANARY, PERSONNEL_CANARY, "u-leave", "p-leave"):
        assert value.encode() not in summary
    assert outputs["identityLifecycle"]["evidenceRef"].encode() in summary
    with pytest.raises(TemporalArtifactAuthorizationError):
        await environment.service.read(
            artifact_id=outputs["identityLifecycle"]["evidenceRef"],
            access=access_for_test("workflow:another"),
        )
    assert fake.requests


async def test_generated_evidence_enters_framework_without_compliance_coverage(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch)
    data = framework_fixture()["framework"]
    for row in data["systemInputs"]["criticalSystems"]:
        row["systems"] = [ORG]
    for row in data["systemInputs"]["thirdParties"]:
        row["systems"] = [ORG]
    for row in data.get("requirementChoices", {}).values():
        if row.get("systems"):
            row["systems"] = [ORG]
    data["evidenceRefs"] = []
    data["auditRef"] = None
    payload["framework"] = data
    Okta(monkeypatch)
    outputs = await invoke(environment.service, payload)
    preparation = await read(environment.service, outputs["frameworkRef"])
    assert outputs["frameworkReadiness"] == "incomplete"
    assert any(
        e["provenance"]["sourceRef"] == outputs["identityLifecycle"]["evidenceRef"]
        for e in preparation["evidence"]
    )
    assert all(r["status"] != "evidence_recorded" for r in preparation["requirements"])


@pytest.mark.parametrize("deny", ["/logs", "/roles", "/groups"])
async def test_denied_endpoints_are_limits_not_empty_evidence(
    environment, monkeypatch, deny
):
    payload = await admission(environment, monkeypatch)
    Okta(monkeypatch, deny=deny)
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert any(
        c["status"] == "permission_denied" for c in identity["coverage"].values()
    )
    assert "permission_denied" in identity["limitations"]
    assert identity["controlOperation"] == "not_established"


async def test_partial_pagination_reuses_original_evidence_on_retry(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch, since="2026-01-01T00:00:00Z")
    source = workforce()
    source["since"] = "2026-01-01T00:00:00Z"
    payload["identityLifecycle"]["workforceRef"] = await put(
        environment.service, source
    )
    fake = Okta(monkeypatch, second_page_failure=True)
    outputs = await invoke(environment.service, payload)
    original = await report(environment.service, outputs)
    assert original["coverage"]["users"]["status"] == "upstream_unavailable"
    assert "audit_retention_gap" in original["limitations"]
    assert "partial_pagination" in original["limitations"]
    count = len(fake.requests)
    fake.second_page_failure = False
    retry = await invoke(environment.service, payload, key="retry")
    assert len(fake.requests) == count
    assert await report(environment.service, retry) == original


async def test_restart_after_a_saved_page_preserves_collection_window(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch)
    fake = Okta(monkeypatch)
    original_write = AssessmentExecution.write
    interrupted = False

    async def write(self, body, **kwargs):
        nonlocal interrupted
        ref = await original_write(self, body, **kwargs)
        if kwargs["kind"] == "security.identity-page" and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return ref

    with monkeypatch.context() as patch:
        patch.setattr(AssessmentExecution, "write", write)
        with pytest.raises(asyncio.CancelledError):
            await invoke(environment.service, payload)
    first = [
        r
        for r in fake.requests
        if r.url.path == "/api/v1/users"
        and not r.url.params.get("filter")
        and not r.url.params.get("after")
    ]
    assert len(first) == 1
    async with environment.sessions() as session:
        later = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(environment.storage_root),
        )
        outputs = await invoke(later, payload, key="restart")
        identity = await report(later, outputs)
        assert identity["observedAt"] == NOW.isoformat()
        assert len(identity["accounts"]) == len(api_users())
    first = [
        r
        for r in fake.requests
        if r.url.path == "/api/v1/users"
        and not r.url.params.get("filter")
        and not r.url.params.get("after")
    ]
    assert len(first) == 1


@pytest.mark.parametrize(
    "link",
    [
        "https://attacker.test/api/v1/users?after=page2",
        "https://example.okta.com/api/v1/users/u-join/lifecycle/deactivate?after=page2",
        "https://example.okta.com/api/v1/users?after=page2&filter=status%20eq%20ACTIVE",
    ],
)
async def test_provider_pagination_cannot_change_authority_or_query(
    environment, monkeypatch, link
):
    payload = await admission(environment, monkeypatch)
    fake = Okta(monkeypatch, bad_next=link)
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert identity["coverage"]["users"]["status"] == "unsafe_pagination"
    assert not any(
        r.url.host == "attacker.test" or "lifecycle" in r.url.path
        for r in fake.requests
    )


async def test_no_egress_grant_sends_no_credentials_or_requests(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch)
    fake = Okta(monkeypatch)
    monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES", "[]")
    with pytest.raises(ApplicationError, match="INVALID_INPUT"):
        await invoke(environment.service, payload)
    assert not fake.requests


async def test_workforce_organisation_mismatch_fails_before_transport(
    environment, monkeypatch
):
    data = workforce()
    data["organisation"] = "another.okta.com"
    payload = await admission(environment, monkeypatch, data=data)
    fake = Okta(monkeypatch)
    with pytest.raises(ApplicationError, match="INVALID_INPUT"):
        await invoke(environment.service, payload)
    assert not fake.requests


@pytest.mark.parametrize(
    "options, expected",
    [
        ({"maxPages": 1}, "pagination_limit"),
        ({"maxRequests": 1}, "request_budget"),
        ({"maxItems": 1}, "item_budget"),
        ({"maxBytes": 1024}, "byte_budget"),
    ],
)
async def test_collection_limits_preserve_partial_coverage(
    environment, monkeypatch, options, expected
):
    payload = await admission(environment, monkeypatch, **options)
    Okta(monkeypatch)
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert expected in identity["limitations"]
    assert identity["controlOperation"] == "not_established"
    assert identity["usage"]["requests"] <= payload["identityLifecycle"].get(
        "maxRequests", 128
    )


async def test_duplicate_accounts_and_unknown_status_are_explicit(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch)
    users = api_users()
    users.append({"id": "u-join", "status": "SUSPENDED"})
    users.append({"id": "u-new", "status": "UNDOCUMENTED"})
    Okta(monkeypatch, users=users)
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert identity["duplicateAccountRecords"] == 1
    assert "u-join" in identity["ambiguousAccounts"]
    assert {"conflicting_account_records", "unknown_account_status"} <= set(
        identity["limitations"]
    )
    assert identity["coverage"]["users"]["status"] == "partial_records"
    row = next(r for r in identity["lifecycle"] if r["workforceEventId"] == "j1")
    assert row["reason"] == "ambiguous_identity"


async def test_malformed_memberships_cannot_establish_absence_of_privilege(
    environment, monkeypatch
):
    payload = await admission(environment, monkeypatch, privilegedGroupIds=[])
    fake = Okta(monkeypatch)
    original = fake.respond

    def respond(req):
        if req.url.path.endswith("/groups"):
            return httpx.Response(200, json=[{"profile": {"name": PERSONNEL_CANARY}}])
        if req.url.path.endswith("/roles"):
            return httpx.Response(200, json=[])
        return original(req)

    fake.respond = respond
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert "malformed_record" in identity["limitations"]
    assert all(a["privileged"] is None for a in identity["accounts"])


async def test_budget_expired_after_restart_keeps_saved_evidence_and_original_retention(
    environment, monkeypatch
):
    from datetime import timedelta
    from moonmind.security import identity_lifecycle

    payload = await admission(environment, monkeypatch)
    fake = Okta(monkeypatch)
    original_write = AssessmentExecution.write

    async def interrupted_write(self, body, **kwargs):
        ref = await original_write(self, body, **kwargs)
        if kwargs["kind"] == "security.identity-page":
            raise asyncio.CancelledError()
        return ref

    with monkeypatch.context() as patch:
        patch.setattr(AssessmentExecution, "write", interrupted_write)
        with pytest.raises(asyncio.CancelledError):
            await invoke(environment.service, payload)
    count = len(fake.requests)
    monkeypatch.setattr(identity_lifecycle, "utcnow", lambda: NOW + timedelta(days=1))
    outputs = await invoke(environment.service, payload, key="expired-restart")
    identity = await report(environment.service, outputs)
    assert len(fake.requests) == count
    assert identity["observedAt"] == NOW.isoformat()
    assert "time_budget" in identity["limitations"]
    assert identity["coverage"]["users"]["pages"] == 1
    assert {a["id"] for a in identity["accounts"]} == {"u-join", "u-move"}


@pytest.mark.parametrize(
    "options, users",
    [
        ({"maxItems": 1}, None),
        (
            {"maxBytes": 1024},
            [{"id": "u-large", "status": "ACTIVE", "profile": {"ignored": "x" * 2048}}],
        ),
    ],
)
async def test_exhausted_response_limits_send_no_further_requests(
    environment, monkeypatch, options, users
):
    payload = await admission(environment, monkeypatch, **options)
    fake = Okta(monkeypatch, users=users)
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert len(fake.requests) == 1
    assert identity["usage"]["items"] >= options.get("maxItems", 0)
    assert identity["usage"]["bytes"] >= options.get("maxBytes", 0)


async def test_provider_cannot_echo_the_credential_into_a_pagination_cursor(
    environment, monkeypatch, caplog
):
    payload = await admission(environment, monkeypatch)
    next_url = str(
        httpx.URL(f"https://{ORG}/api/v1/users").copy_merge_params(
            {
                "limit": 200,
                "fields": "id,status",
                "after": SECRET_CANARY,
            }
        )
    )
    Okta(monkeypatch, bad_next=next_url)
    caplog.set_level("DEBUG", logger="httpx")
    identity = await report(
        environment.service, await invoke(environment.service, payload)
    )
    assert identity["coverage"]["users"]["status"] == "unsafe_pagination"
    assert SECRET_CANARY not in caplog.text
    for ref in identity["pageRefs"]:
        _, body = await environment.service.read(
            artifact_id=ref, access=access_for_test("workflow:assessment")
        )
        assert SECRET_CANARY.encode() not in body
