"""Identity evidence behaviors for MoonLadderStudios/MoonMind#5191."""

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import AssessmentRequest

ORG = "example.okta.com"
PERSONNEL_CANARY = "unnecessary-personnel-attribute-canary"
SECRET_CANARY = "selected-provider-credential-canary"
SINCE = "2026-11-01T00:00:00Z"
UNTIL = "2026-11-06T00:00:00Z"
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def request(workforce_ref="art_workforce"):
    return {
        "scope": {"assets": [ORG]},
        "tools": ["identity-lifecycle"],
        "budget": {"baseSeconds": 120, "maxSeconds": 180, "progressStallSeconds": 60},
        "credentialRefs": ["db://okta-reader"],
        "networkRefs": ["identity-read"],
        "identityLifecycle": {
            "provider": "okta",
            "organisation": ORG,
            "since": SINCE,
            "until": UNTIL,
            "workforceRef": workforce_ref,
            "secretRef": "db://okta-reader",
            "egressPolicyRef": "identity-read",
            "removalHours": 24,
            "privilegedGroupIds": ["admins"],
            "requirementIds": ["5.16", "5.18", "6.5"],
        },
    }


def workforce():
    return {
        "schemaVersion": "workforce-events/v1",
        "organisation": ORG,
        "since": SINCE,
        "until": UNTIL,
        "roster": [
            {"personId": "p-join", "accountId": "u-join", "salary": PERSONNEL_CANARY},
            {"personId": "p-move", "accountId": "u-move"},
            {"personId": "p-leave", "accountId": "u-leave"},
            {"personId": "p-missing", "accountId": "u-missing"},
            {"personId": "p-unknown", "accountId": "u-unknown"},
            {"personId": "p-a", "accountId": "u-duplicate"},
            {"personId": "p-b", "accountId": "u-duplicate"},
            {"personId": "p-no-account", "accountId": "u-absent"},
            {"accountId": "u-service", "accountKind": "service"},
        ],
        "events": [
            {
                "id": "j1",
                "personId": "p-join",
                "type": "joiner",
                "effectiveAt": "2026-11-02T00:00:00Z",
            },
            {
                "id": "m1",
                "personId": "p-move",
                "type": "mover",
                "effectiveAt": "2026-11-02T00:00:00Z",
                "removeGroupIds": ["old-group"],
            },
            {
                "id": "l1",
                "personId": "p-leave",
                "type": "leaver",
                "effectiveAt": "2026-11-02T00:00:00Z",
            },
            {
                "id": "l2",
                "personId": "p-missing",
                "type": "leaver",
                "effectiveAt": "2026-11-02T00:00:00Z",
            },
            {
                "id": "l3",
                "personId": "p-unknown",
                "type": "leaver",
                "effectiveAt": None,
            },
        ],
    }


def api_users():
    return [
        {
            "id": f"u-{name}",
            "status": "DEPROVISIONED" if name in {"leave", "missing"} else "ACTIVE",
            "profile": {"email": PERSONNEL_CANARY, "firstName": PERSONNEL_CANARY},
        }
        for name in [
            "join",
            "move",
            "leave",
            "missing",
            "unknown",
            "duplicate",
            "service",
            "unmatched",
        ]
    ]


def api_events():
    def row(id, kind, account, at, group=None):
        return {
            "uuid": id,
            "eventType": kind,
            "published": at,
            "outcome": {"result": "SUCCESS"},
            "actor": {"displayName": PERSONNEL_CANARY},
            "target": [{"id": account, "type": "User", "displayName": PERSONNEL_CANARY}]
            + ([{"id": group, "type": "UserGroup"}] if group else []),
        }

    return [
        row("log-j", "user.lifecycle.activate", "u-join", "2026-11-02T01:00:00Z"),
        row(
            "log-m",
            "group.user_membership.remove",
            "u-move",
            "2026-11-02T02:00:00Z",
            "old-group",
        ),
        row("log-l", "user.lifecycle.deactivate", "u-leave", "2026-11-04T00:00:00Z"),
    ]


def test_identity_admission_reuses_shared_assessment_owner():
    admitted = AssessmentRequest.model_validate(request())
    assert admitted.identity_lifecycle.organisation == ORG
    assert admitted.identity_lifecycle.removal_hours == 24


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "arbitrary"},
        {"organisation": "localhost"},
        {"organisation": "example.okta.com.attacker.test"},
        {"organisation": "https://example.okta.com"},
        {"secretRef": "env://TOKEN"},
        {"until": SINCE},
        {"removalHours": -1},
        {"workforceRef": "https://arbitrary.test"},
    ],
)
def test_admission_refuses_unbounded_or_invalid_inputs(change):
    payload = request()
    payload["identityLifecycle"].update(change)
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"credentialRefs": []},
        {"networkRefs": []},
        {"credentialRefs": ["db://another-secret"]},
        {"scope": {"assets": ["another.okta.com"]}},
        {"tools": []},
    ],
)
def test_identity_cannot_escape_admitted_scope_or_references(change):
    payload = request()
    payload.update(change)
    with pytest.raises(ValidationError):
        AssessmentRequest.model_validate(payload)


def test_reconciliation_keeps_matches_limits_and_traceable_policy_exceptions():
    from moonmind.security.identity_lifecycle import Workforce, reconcile

    selected = AssessmentRequest.model_validate(request()).identity_lifecycle
    source = Workforce.model_validate(workforce())
    accounts = [
        {
            "id": x["id"],
            "status": x["status"],
            "groups": [],
            "roles": [],
            "evidenceRefs": ["art_accounts"],
        }
        for x in api_users()
    ]
    events = [
        {
            "id": e["uuid"],
            "type": e["eventType"],
            "published": e["published"],
            "accountIds": [e["target"][0]["id"]],
            "groupIds": [t["id"] for t in e["target"][1:]],
            "successful": True,
            "evidenceRefs": ["art_events"],
        }
        for e in api_events()
    ]
    report = reconcile(selected, source, accounts, events, observed_at=NOW)
    assert {r["type"] for r in report["lifecycle"]} == {"joiner", "mover", "leaver"}
    by_event = {r["workforceEventId"]: r for r in report["lifecycle"]}
    assert by_event["j1"]["status"] == "event_observed"
    assert by_event["m1"]["status"] == "event_observed"
    assert by_event["l1"]["status"] == "policy_exception"
    assert by_event["l1"]["recordedDelayHours"] == 48
    assert by_event["l1"]["evidenceRefs"] == ["art_workforce", "art_events"]
    assert by_event["l2"]["reason"] == "missing_historical_event"
    assert by_event["l3"]["reason"] == "unknown_effective_date"
    assert report["ambiguousAccounts"] == ["u-duplicate"]
    assert report["unmatchedPeople"] == ["p-no-account"]
    unmatched = {r["accountId"]: r for r in report["unmatchedAccounts"]}
    assert unmatched["u-service"]["accountKind"] == "service"
    assert report["accountKinds"]["u-service"] == "service"
    assert unmatched["u-unmatched"]["disposition"] == "review_required"
    assert report["controlOperation"] == "not_established"


def test_snapshot_and_failed_or_out_of_period_events_cannot_establish_removal():
    from moonmind.security.identity_lifecycle import Workforce, reconcile

    selected = AssessmentRequest.model_validate(request()).identity_lifecycle
    source = Workforce.model_validate(workforce())
    events = [
        {
            "id": "failed",
            "type": "user.lifecycle.deactivate",
            "published": "2026-11-03T00:00:00Z",
            "accountIds": ["u-leave"],
            "groupIds": [],
            "successful": False,
            "evidenceRefs": ["art_events"],
        }
    ]
    for accounts in (
        [],
        [
            {
                "id": "u-leave",
                "status": "DEPROVISIONED",
                "groups": [],
                "roles": [],
                "evidenceRefs": ["art_accounts"],
            }
        ],
    ):
        report = reconcile(selected, source, accounts, events, observed_at=NOW)
        assert report["controlOperation"] == "not_established"
        assert not any(r["status"] == "event_observed" for r in report["lifecycle"])


def test_duplicate_workforce_records_do_not_hide_ambiguous_source_ids():
    from moonmind.security.identity_lifecycle import Workforce, reconcile

    data = workforce()
    data["roster"].append(copy.deepcopy(data["roster"][0]))
    data["roster"].append({"personId": "p-other", "accountId": "u-leave"})
    report = reconcile(
        AssessmentRequest.model_validate(request()).identity_lifecycle,
        Workforce.model_validate(data),
        [],
        [],
        observed_at=NOW,
    )
    assert "u-leave" in report["ambiguousAccounts"]
    assert (
        next(r for r in report["lifecycle"] if r["workforceEventId"] == "l1")["reason"]
        == "ambiguous_identity"
    )
