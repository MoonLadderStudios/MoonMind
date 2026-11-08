from datetime import datetime, timezone

import pytest

from moonmind.security.assessment_authorizations import (
    AssessmentAuthorizations,
    authorization_scope,
    prepare_authorizations_update,
    scope_digest,
)


def authorization(**changes):
    return {
        "ref": "client-review",
        "name": "Client review",
        "enabled": True,
        "authorityKind": "client_engagement",
        "authoritySource": "Client authorization record 12",
        "validFrom": "2026-11-08T10:00:00Z",
        "validUntil": "2026-11-09T10:00:00Z",
        "maxTotalJobSeconds": 600,
        "scope": {
            "assessmentRef": "client-review",
            "profileRefs": ["web-assessment"],
            "actionClasses": ["web-passive"],
            "targets": [
                {
                    "webOrigin": "https://example.test",
                    "webRoutes": [{"path": "/", "methods": ["GET"]}],
                }
            ],
            "maxJobs": 5,
            "maxJobSeconds": 120,
            "maxCpuMillis": 1000,
            "maxMemoryMiB": 128,
            "maxRequestsPerSecond": 10,
            "evidenceIntent": "Retain private assessment evidence",
        },
        **changes,
    }


def saved(value=None, previous=None):
    return prepare_authorizations_update(
        {"authorizations": [value or authorization()]},
        previous or {"authorizations": []},
    )


def test_generations_are_server_owned_and_exact_scope_is_stamped():
    document = saved(authorization(generation="caller-chosen"))
    record = AssessmentAuthorizations.model_validate(document).authorizations[0]
    assert record.generation != "caller-chosen"
    scope = authorization_scope(record)
    assert scope.engagement.authorization_ref == record.ref
    assert scope.engagement.authorization_generation == record.generation
    assert scope.engagement.valid_until == datetime(
        2026, 11, 9, 10, tzinfo=timezone.utc
    )
    assert scope.engagement.max_total_job_seconds == 600
    assert scope_digest(scope) == scope_digest(authorization_scope(record))


def test_label_and_unrelated_edits_preserve_generation_but_authority_changes_do_not():
    before = saved()
    item = before["authorizations"][0]
    renamed = saved({**item, "name": "Readable label"}, before)
    assert renamed["authorizations"][0]["generation"] == item["generation"]
    changed = saved({**item, "maxTotalJobSeconds": 601}, before)
    assert changed["authorizations"][0]["generation"] != item["generation"]
    revoked = saved({**item, "enabled": False}, before)
    restored = saved(item, revoked)
    assert restored["authorizations"][0]["generation"] not in {
        item["generation"],
        revoked["authorizations"][0]["generation"],
    }


def test_removal_and_recreation_cannot_resurrect_old_authority():
    before = saved()
    removed = prepare_authorizations_update({"authorizations": []}, before)
    recreated = saved(before["authorizations"][0], removed)
    assert (
        recreated["authorizations"][0]["generation"]
        != before["authorizations"][0]["generation"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"validUntil": "2026-11-08T09:00:00Z"},
        {"validFrom": "2026-11-08T10:00:00"},
        {"maxTotalJobSeconds": 0},
    ],
)
def test_invalid_limits_fail_closed(changes):
    with pytest.raises(ValueError):
        saved(authorization(**changes))


def test_record_cannot_embed_another_admission_or_duplicate_identity():
    record = AssessmentAuthorizations.model_validate(saved()).authorizations[0]
    scoped = authorization_scope(record).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    with pytest.raises(ValueError):
        saved(authorization(scope=scoped))
    with pytest.raises(ValueError):
        AssessmentAuthorizations.model_validate(
            {"authorizations": [authorization(), authorization()]}
        )


@pytest.mark.parametrize(
    "cpu,memory,rate", [(1001, 128, 10), (1000, 129, 10), (1000, 128, 11)]
)
def test_actual_job_respects_reviewed_resource_and_web_rate_limits(cpu, memory, rate):
    from types import SimpleNamespace
    from moonmind.security.workload_profiles import (
        AdmittedAssessmentScope,
        resolved_job_within_scope,
    )

    scope = AdmittedAssessmentScope.model_validate(
        {
            **authorization()["scope"],
            "maxCpuMillis": 1000,
            "maxMemoryMiB": 128,
            "maxRequestsPerSecond": 10,
            "networkRefs": ["review-network"],
        }
    )
    web = SimpleNamespace(
        assessment_ref="client-review",
        action_class="web-passive",
        egress_policy_ref="review-network",
        max_requests_per_second=rate,
        origin="https://example.test",
        routes=[SimpleNamespace(path="/", methods=["GET"])],
        exclusions=[],
        api_definition=None,
    )
    spec = SimpleNamespace(
        web_assessment=web,
        resources=SimpleNamespace(cpu_millis=cpu, memory_mib=memory),
        timeout_seconds=120,
    )
    with pytest.raises(PermissionError, match="assessment (limit|resource)"):
        resolved_job_within_scope(spec, scope, ())


def current_saved_authorization():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    return saved(
        authorization(
            validFrom=(now - timedelta(hours=1)).isoformat(),
            validUntil=(now + timedelta(hours=1)).isoformat(),
        )
    )
