import pytest
from pydantic import ValidationError

from moonmind.security.application_access import (
    ApplicationAccessBundle,
    ApplicationAccessObservation,
    ApplicationAccessSpec,
    JsonAssertion,
    StateAssertion,
    assess_application_access,
    project_response,
    project_state,
    spec_digest,
)
from moonmind.security.authorization_matrix import AuthorizationMatrix


def spec(mutating=False, expected="deny"):
    return ApplicationAccessSpec.model_validate(
        {
            "source_ref": "fixture://application",
            "policy_ref": "policy://access",
            "policy_review_ref": "review://approved-v1",
            "authorization_matrix": {
                "identities": [
                    {
                        "id": "owner",
                        "account_ref": "account",
                        "session_ref": "owner-session",
                        "integration_ref": "app",
                        "credential_ref": "slot://owner",
                    },
                    {
                        "id": "anon",
                        "account_ref": "anonymous",
                        "session_ref": "anonymous",
                        "integration_ref": "app",
                        "anonymous": True,
                    },
                ],
                "cases": [
                    {
                        "id": "read",
                        "identity_ref": "anon",
                        "account_ref": "account",
                        "session_ref": "context",
                        "integration_ref": "app",
                        "resource_ref": "record",
                        "action": "update" if mutating else "read",
                        "expected": expected,
                    }
                ],
            },
            "resources": [
                {
                    "id": "record",
                    "owner_identity_ref": "owner",
                    "account_ref": "account",
                    "session_ref": "context",
                    "integration_ref": "app",
                }
            ],
            "cases": [
                dict(
                    case_ref="read",
                    resource_ref="record",
                    policy_rule_ref="policy://access/rule-1",
                    request={
                        "id": "request",
                        "path": "/record",
                        "method": "POST" if mutating else "GET",
                        "payload": {"mutation": "mutation-1"} if mutating else {},
                    },
                    identity_check={
                        "id": "identity",
                        "path": "/identity",
                        "method": "GET",
                    },
                    identity_assertions=[
                        {"id": "identity", "path": "anonymous", "value": True}
                    ],
                    allow_assertions=[
                        {"id": "allowed", "path": "record.id", "value": "record"}
                    ],
                    deny_assertions=[
                        {"id": "denied", "path": "error", "value": "forbidden"}
                    ],
                    protected_assertions=[
                        {
                            "id": "protected",
                            "path": "record.secret",
                            "operator": "exists",
                        }
                    ],
                    **(
                        {
                            "mutation_ref": "mutation-1",
                            "mutation_ref_path": "mutation",
                            "receipt_assertion": {
                                "id": "receipt",
                                "path": "mutation",
                                "value": "mutation-1",
                            },
                            "state_check": {
                                "id": "state",
                                "path": "/record/state",
                                "method": "GET",
                                "credential_ref": "slot://owner",
                            },
                            "state_assertions": [
                                {
                                    "id": "unchanged",
                                    "path": "version",
                                    "operator": "unchanged",
                                }
                            ],
                        }
                        if mutating
                        else {}
                    ),
                )
            ],
        }
    )


def observation(s, payload, **updates):
    case = s.cases[0]
    data = {
        "case_ref": "read",
        "identity_before": project_response(
            {"anonymous": True}, case.identity_assertions
        ),
        "identity_after": project_response(
            {"anonymous": True}, case.identity_assertions
        ),
        "response": project_response(
            payload,
            case.allow_assertions + case.deny_assertions + case.protected_assertions,
        ),
        "evidence_refs": ["artifact://sanitized"],
        "status": 403,
    }
    data.update(updates)
    return ApplicationAccessObservation(**data)


def assess(s, obs):
    return assess_application_access(
        s, ApplicationAccessBundle(spec_digest=spec_digest(s), observations=obs)
    )


def test_real_denial_and_success_error_page():
    s = spec()
    assert (
        assess(s, [observation(s, {"error": "forbidden"})]).cases[0].outcome == "pass"
    )
    assert (
        assess(s, [observation(s, {"error": "login"}, status=200)]).cases[0].outcome
        == "inconclusive"
    )


def test_denial_with_leaked_data_is_finding_without_secret_in_output():
    s = spec()
    result = assess(
        s,
        [
            observation(
                s, {"error": "forbidden", "record": {"secret": "PRIVATE_SECRET"}}
            )
        ],
    )
    assert result.cases[0].outcome == "finding"
    assert result.cases[0].access.observed == "allow"
    assert "PRIVATE_SECRET" not in result.model_dump_json()


@pytest.mark.parametrize(
    "updates",
    [
        {"identity_before": []},
        {"identity_after": []},
        {"stop_reason": "timeout"},
        {"response": []},
        {"identity_after": [{"id": "identity", "matched": False}]},
    ],
)
def test_incomplete_identity_or_observation_never_passes(updates):
    s = spec()
    assert (
        assess(s, [observation(s, {"error": "forbidden"}, **updates)]).cases[0].outcome
        == "inconclusive"
    )


def test_missing_observation_and_spec_mismatch():
    s = spec()
    assert assess(s, []).cases[0].outcome == "inconclusive"
    bundle = ApplicationAccessBundle(spec_digest="0" * 64, observations=[])
    assert assess_application_access(s, bundle).status == "partial"


def test_denied_mutation_and_missing_state():
    s = spec(mutating=True)
    obs = observation(
        s,
        {"error": "forbidden"},
        attempt_ref="attempt-1",
        receipt_ref="mutation-1",
        receipt={"id": "receipt", "matched": True},
    )
    assert assess(s, [obs]).cases[0].outcome == "inconclusive"
    obs.state = project_state(
        {"version": 1}, {"version": 2}, s.cases[0].state_assertions
    )
    assert assess(s, [obs]).cases[0].outcome == "finding"
    obs.state = project_state(
        {"version": 1}, {"version": 1}, s.cases[0].state_assertions
    )
    assert assess(s, [obs]).cases[0].outcome == "pass"


def test_assertions_are_bounded_and_missing_state_unknown():
    with pytest.raises(ValidationError):
        JsonAssertion(id="bad", path='__import__("os")', value=True)
    assert (
        project_state(
            {}, {}, [StateAssertion(id="x", path="version", operator="unchanged")]
        )[0].matched
        is None
    )
    assert (
        project_response({"v": 1}, [JsonAssertion(id="x", path="v", value=True)])[
            0
        ].matched
        is False
    )


def test_matrix_resource_context_preserves_distinct_objects():
    matrix = spec().authorization_matrix.model_dump()
    matrix["cases"].append(
        {**matrix["cases"][0], "id": "other", "resource_ref": "other"}
    )
    assert len(AuthorizationMatrix.model_validate(matrix).cases) == 2
    matrix["cases"][1]["resource_ref"] = "record"
    with pytest.raises(ValidationError):
        AuthorizationMatrix.model_validate(matrix)


def test_legacy_matrix_serialization_does_not_change():
    data = {
        "identities": [
            {
                "id": "owner",
                "account_ref": "account",
                "session_ref": "session",
                "integration_ref": "app",
                "credential_ref": "slot://owner",
            }
        ],
        "cases": [
            {
                "id": "read",
                "identity_ref": "owner",
                "account_ref": "account",
                "session_ref": "session",
                "integration_ref": "app",
                "action": "read",
                "expected": "allow",
            }
        ],
    }
    matrix = AuthorizationMatrix.model_validate(data)
    assert matrix.model_dump() == data
    assert "anonymous" not in matrix.model_dump_json(by_alias=True)
    assert "resourceRef" not in matrix.model_dump_json(by_alias=True)


def test_state_projection_survives_restart_without_values():
    from moonmind.security.application_access import compare_state, project_state_values

    assertions = [StateAssertion(id="x", path="secret", operator="unchanged")]
    before = project_state_values({"secret": "SECRET"}, assertions)
    assert "SECRET" not in str([p.model_dump() for p in before])
    after = project_state_values({"secret": "SECRET"}, assertions)
    assert compare_state(before, after, assertions)[0].matched is True


def test_mutation_receipt_must_bind_declared_mutation():
    s = spec(mutating=True)
    obs = observation(
        s,
        {"error": "forbidden"},
        attempt_ref="attempt-1",
        receipt_ref="another-mutation",
        receipt={"id": "receipt", "matched": True},
        state=project_state(
            {"version": 1}, {"version": 1}, s.cases[0].state_assertions
        ),
    )
    assert assess(s, [obs]).cases[0].outcome == "inconclusive"


@pytest.mark.parametrize(
    "change", ["resource", "credential", "policy", "receipt", "unsafe_probe"]
)
def test_spec_rejects_invalid_context_or_mutation_contract(change):
    data = spec(mutating=True).model_dump()
    case = data["cases"][0]
    if change == "resource":
        data["authorization_matrix"]["cases"][0]["resource_ref"] = "missing"
    elif change == "credential":
        case["request"]["credential_ref"] = "slot://owner"
    elif change == "policy":
        del data["policy_review_ref"]
    elif change == "receipt":
        case["receipt_assertion"]["value"] = "unrelated"
    else:
        case["identity_check"]["method"] = "POST"
    with pytest.raises(ValidationError):
        ApplicationAccessSpec.model_validate(data)


def test_positive_access_requires_protected_object_and_rejects_login_page():
    s = spec(expected="allow")
    assert (
        assess(s, [observation(s, {"record": {"id": "record"}}, status=200)])
        .cases[0]
        .outcome
        == "pass"
    )
    for payload in ({"record": {"id": "other-record"}}, {"login": True}, {}):
        assert (
            assess(s, [observation(s, payload, status=200)]).cases[0].outcome
            == "inconclusive"
        )


def test_duplicate_projection_and_duplicate_case_are_inconclusive():
    s = spec()
    obs = observation(s, {"error": "forbidden"})
    assert assess(s, [obs, obs]).status == "partial"
    obs.response.append(obs.response[0])
    assert assess(s, [obs]).cases[0].outcome == "inconclusive"


def test_changed_policy_invalidates_prior_observations():
    s = spec()
    bundle = ApplicationAccessBundle(
        spec_digest=spec_digest(s),
        observations=[observation(s, {"error": "forbidden"})],
    )
    s.policy_review_ref = "review://new-policy"
    assert assess_application_access(s, bundle).cases[0].outcome == "inconclusive"


def test_mutation_correlation_is_bound_to_transmitted_request():
    data = spec(mutating=True).model_dump()
    data["cases"][0]["request"]["payload"] = {"mutation": "another"}
    with pytest.raises(ValidationError):
        ApplicationAccessSpec.model_validate(data)


def test_failed_job_preserves_findings_but_is_partial():
    from moonmind.security.application_access import (
        ApplicationAccessReport,
        bind_job_result,
    )

    s = spec()
    report = assess(s, [observation(s, {"record": {"secret": "SECRET"}})])
    bound = bind_job_result(
        report.model_dump_json().encode(),
        "failed",
        1,
        {"spec": "artifact://spec", "capture": "artifact://capture"},
    )
    retained = ApplicationAccessReport.model_validate_json(bound)
    assert retained.status == "partial"
    assert retained.outcome == "violations"
    assert retained.cases[0].outcome == "finding"


@pytest.mark.parametrize("proof", ["leak", "state_change"])
def test_confirmed_violation_survives_missing_mutation_receipt(proof):
    s = spec(mutating=True)
    obs = observation(
        s,
        {
            "error": "forbidden",
            **({"record": {"secret": "PRIVATE"}} if proof == "leak" else {}),
        },
        attempt_ref="attempt-1",
        receipt={"id": "receipt", "matched": False},
        state=project_state(
            {"version": 1},
            {"version": 2 if proof == "state_change" else 1},
            s.cases[0].state_assertions,
        ),
    )
    report = assess(s, [obs])
    assert report.cases[0].outcome == "finding"
    assert report.cases[0].coverage == "partial"
    assert (report.status, report.outcome) == ("partial", "violations")


def test_observed_denial_and_unchanged_state_needs_no_mutation_receipt():
    s = spec(mutating=True)
    obs = observation(
        s,
        {"error": "forbidden"},
        attempt_ref="attempt-1",
        receipt={"id": "receipt", "matched": False},
        state=project_state(
            {"version": 1}, {"version": 1}, s.cases[0].state_assertions
        ),
    )
    assert assess(s, [obs]).cases[0].outcome == "pass"


def test_failed_identity_cannot_attribute_even_a_matching_protected_response():
    s = spec()
    report = assess(
        s,
        [
            observation(
                s,
                {"record": {"id": "record", "secret": "PRIVATE"}},
                stop_reason="identity_failed",
            )
        ],
    )
    assert report.cases[0].authentication_state == "unverified"
    assert report.cases[0].outcome == "inconclusive"


def test_denial_does_not_accept_an_unrelated_receipt_projection():
    s = spec(mutating=True)
    obs = observation(
        s,
        {"error": "forbidden"},
        attempt_ref="attempt-1",
        receipt={"id": "unrelated", "matched": False},
        state=project_state(
            {"version": 1}, {"version": 1}, s.cases[0].state_assertions
        ),
    )
    assert assess(s, [obs]).cases[0].outcome == "inconclusive"


def test_denied_authorized_mutation_with_unchanged_state_is_a_finding():
    data = spec(mutating=True, expected="allow").model_dump()
    data["cases"][0]["state_assertions"][0]["operator"] = "changed"
    selected = ApplicationAccessSpec.model_validate(data)
    obs = observation(
        selected,
        {"error": "forbidden"},
        attempt_ref="attempt-1",
        receipt={"id": "receipt", "matched": False},
        state=project_state(
            {"version": 1}, {"version": 1}, selected.cases[0].state_assertions
        ),
    )
    report = assess(selected, [obs])
    assert (report.status, report.outcome) == ("complete", "violations")
    assert report.cases[0].access.observed == "deny"
    assert report.cases[0].reasons == ["expected_access_denied"]
