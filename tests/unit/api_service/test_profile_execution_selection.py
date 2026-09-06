from types import SimpleNamespace as Row

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from api_service.services.profile_execution_selection import (
    configuration_accepts_profile,
    profile_has_native_inventory_route,
    select_execution_configuration,
)


def provider(**changes):
    return Row(
        **{
            "profile_id": "zen",
            "runtime_id": "opencode",
            "provider_id": "opencode",
            "credential_source": "none",
            "runtime_materialization_mode": "config_bundle",
            "execution_configuration": None,
            **changes,
        }
    )


def configuration(name="behavior", *, default=False, providers=None):
    return (
        Row(profile_id=name, default_for_runtime=default, active_version=1),
        Row(
            version=1,
            digest="sha256:" + "a" * 64,
            validation_result={"ready": True},
            document={
                "providerRequirements": {
                    "runtimeId": "opencode",
                    "credentialSource": "none",
                    "materializationMode": "config_bundle",
                    "providerIds": providers or ["opencode"],
                },
                "execution": {"allowedLaunchPolicyRefs": ["isolated@1"]},
            },
        ),
    )


@pytest.mark.parametrize(
    "catalog", [None, {}, {"validatedAt": "2000-01-01", "imageRef": "old"}]
)
def test_profile_configuration_is_independent_of_discovery(catalog):
    selected = select_execution_configuration(
        provider(model_catalog_evidence_json=catalog),
        [configuration()],
    )
    assert selected["providerProfileRef"] == "zen"
    assert selected["profileId"] == "behavior"
    assert selected["launchPolicyRef"] == "isolated@1"


def test_authentication_contract_cannot_cross_zen_and_go():
    assert not configuration_accepts_profile(
        configuration()[1].document,
        provider(
            provider_id="opencode-go",
            credential_source="secret_ref",
        ),
    )


@pytest.mark.parametrize(
    "validation_result", [None, {}, {"ready": False}, {"ready": 1}]
)
def test_unvalidated_configuration_cannot_be_selected(validation_result):
    candidate = configuration()
    candidate[1].validation_result = validation_result
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(provider(), [candidate])
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "profile_execution_configuration_required"


def test_configuration_without_validation_result_cannot_be_selected():
    candidate = configuration()
    del candidate[1].validation_result
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(provider(), [candidate])
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "runtime", ["codex", "codex_cli", "claude", "claude_code", "jules"]
)
def test_direct_runtime_inventory_does_not_require_omnigent_configuration(runtime):
    native = provider(runtime_id=runtime)
    assert profile_has_native_inventory_route(native, [configuration()]) is True
    # Explicit Omnigent admission retains the configuration requirement.
    with pytest.raises(HTTPException):
        select_execution_configuration(native, [configuration()])


@pytest.mark.parametrize("runtime", ["opencode", "omnigent", "codex_cloud", "unknown"])
def test_unsupported_direct_runtime_keeps_configuration_requirement(runtime):
    assert profile_has_native_inventory_route(provider(runtime_id=runtime), []) is False


@pytest.mark.parametrize(
    "validation_result", [None, {}, {"ready": False}, {"ready": True}]
)
def test_compatible_configuration_prevents_native_route(validation_result):
    native = provider(runtime_id="codex_cli")
    candidate = configuration()
    candidate[1].document["providerRequirements"]["runtimeId"] = native.runtime_id
    candidate[1].validation_result = validation_result
    assert profile_has_native_inventory_route(native, [candidate]) is False


def test_explicit_configuration_pin_prevents_native_route_when_missing():
    native = provider(
        runtime_id="codex_cli",
        execution_configuration={
            "profileId": "missing",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        },
    )
    assert profile_has_native_inventory_route(native, []) is False


def test_unready_pinned_configuration_does_not_substitute_ready_default():
    custom = configuration("custom")
    custom[1].validation_result = {"ready": False}
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(
            provider(
                execution_configuration={
                    "profileId": "custom",
                    "version": 1,
                    "digest": custom[1].digest,
                }
            ),
            [configuration("stock", default=True), custom],
        )
    assert error.value.status_code == 409
    assert error.value.detail["profileId"] == "zen"


def test_unready_default_cannot_make_ready_custom_configuration_default():
    stock = configuration("stock", default=True)
    stock[1].validation_result = {"ready": False}
    selected = select_execution_configuration(
        provider(), [stock, configuration("custom")]
    )
    assert selected["profileId"] == "custom"
    assert selected["defaultForRuntime"] is False


def test_explicit_configuration_wins_over_deployment_default():
    selected = select_execution_configuration(
        provider(
            execution_configuration={
                "profileId": "custom",
                "version": 1,
                "digest": "sha256:" + "a" * 64,
            }
        ),
        [configuration("stock", default=True), configuration("custom")],
    )
    assert selected["profileId"] == "custom"


def test_missing_pinned_version_does_not_substitute_default():
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(
            provider(
                execution_configuration={
                    "profileId": "custom",
                    "version": 2,
                    "digest": "sha256:" + "b" * 64,
                }
            ),
            [configuration("stock", default=True), configuration("custom")],
        )
    assert error.value.status_code == 409


def test_ambiguous_custom_configuration_is_actionable():
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(
            provider(), [configuration("one"), configuration("two")]
        )
    assert error.value.detail["code"] == "profile_execution_configuration_required"


def test_automatic_and_explicit_configuration_resolve_same_identity():
    automatic = select_execution_configuration(
        provider(), [configuration(default=True)]
    )
    explicit = select_execution_configuration(
        provider(
            execution_configuration={
                key: automatic[key] for key in ("profileId", "version", "digest")
            }
        ),
        [configuration(default=True)],
    )
    assert explicit == automatic


def test_pinned_configuration_survives_active_version_advancement():
    row, old = configuration()
    row.active_version = 2
    latest = Row(
        version=2,
        digest="sha256:" + "b" * 64,
        document=old.document,
        validation_result={"ready": True},
    )
    selected = select_execution_configuration(
        provider(
            execution_configuration={
                "profileId": row.profile_id,
                "version": 1,
                "digest": old.digest,
            }
        ),
        [(row, old), (row, latest)],
    )
    assert selected["version"] == 1
    assert (
        select_execution_configuration(provider(), [(row, old), (row, latest)])[
            "version"
        ]
        == 2
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_compatible_defaults_require_profile_settings(reverse):
    candidates = [
        configuration("first", default=True),
        configuration("second", default=True),
    ]
    if reverse:
        candidates.reverse()
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(provider(), candidates)
    assert error.value.status_code == 409
    assert "Profile settings" in error.value.detail["message"]


@pytest.mark.parametrize(
    "expected",
    [None, {"profileId": "behavior", "version": 1, "digest": "sha256:" + "a" * 64}],
)
def test_configuration_expectation_supports_old_requests_and_exact_identity(expected):
    from api_service.services.profile_execution_selection import (
        validate_execution_configuration_expectation,
    )

    resolved = select_execution_configuration(provider(), [configuration()])
    validate_execution_configuration_expectation(expected, resolved)


@pytest.mark.parametrize("changed", ["profileId", "version", "digest"])
def test_configuration_expectation_rejects_changed_identity(changed):
    from api_service.services.profile_execution_selection import (
        validate_execution_configuration_expectation,
    )

    resolved = select_execution_configuration(provider(), [configuration()])
    expected = {key: resolved[key] for key in ("profileId", "version", "digest")}
    expected[changed] = {
        "profileId": "other",
        "version": 2,
        "digest": "sha256:" + "b" * 64,
    }[changed]
    with pytest.raises(HTTPException) as error:
        validate_execution_configuration_expectation(expected, resolved)
    assert error.value.status_code == 409


def _snapshot(**overrides):
    base = {
        "profileId": "behavior",
        "version": 1,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "zen",
        "executionProfileRef": "omnigent-opencode@1",
    }
    base.update(overrides)
    return base


def test_shared_boundary_accepts_matching_intent():
    # MoonLadderStudios/MoonMind#3833: every new-admission consumer funnels
    # through one boundary instead of a per-surface map.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    snapshot = _snapshot()
    validate_omnigent_selection_agreement(
        expected_execution_configuration={
            "profileId": "behavior",
            "version": 1,
            "digest": "sha256:" + "a" * 64,
        },
        authored_omnigent={"executionTargetRef": "omnigent-opencode@1"},
        profile_snapshot=snapshot,
        selected_provider_profile_id="zen",
    )
    # Legacy profileId@version form resolves unambiguously as well.
    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent={"executionTargetRef": "behavior@1"},
        profile_snapshot=snapshot,
        selected_provider_profile_id="zen",
    )
    # Genuinely unauthored older clients omit every expectation.
    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent=None,
        profile_snapshot=snapshot,
        selected_provider_profile_id=None,
    )


def test_shared_boundary_rejects_stale_configuration():
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration={
                "profileId": "other",
                "version": 1,
                "digest": "sha256:" + "a" * 64,
            },
            authored_omnigent=None,
            profile_snapshot=_snapshot(),
            selected_provider_profile_id="zen",
        )
    assert error.value.status_code == 409
    assert (
        error.value.detail["code"] == "profile_execution_configuration_changed"
    )


def test_shared_boundary_rejects_profile_and_target_conflicts():
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration=None,
            authored_omnigent=None,
            profile_snapshot=_snapshot(),
            selected_provider_profile_id="other-account",
        )
    assert error.value.status_code == 422
    assert "must use the selected Profile" in str(error.value.detail)

    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration=None,
            authored_omnigent={"executionTargetRef": "codex.legacy-other"},
            profile_snapshot=_snapshot(),
            selected_provider_profile_id="zen",
        )
    assert error.value.status_code == 422
    assert "must match the selected" in str(error.value.detail)


# --- MoonLadderStudios/MoonMind#3833 remaining work --------------------------
# The tests below close the named verifier gaps hermetically: they drive the
# single common fixture (frontend/src/runtime/fixtures/profile-first-authoring.json)
# through the real shared resolution + intent/conflict boundary without mocking
# admission success, and pin the transition/inventory/qualification matrices at
# the production functions that own them.

_COMMON_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend/src/runtime/fixtures/profile-first-authoring.json"
)


def _fixture_document():
    return json.loads(_COMMON_FIXTURE_PATH.read_text())


def _fixture_provider(fixture):
    # Attribute values mirror the API-level replay
    # (tests/unit/api/routers/test_profile_first_authoring.py), which proves
    # this exact provider/document pair resolves through the real stack.
    return provider(
        profile_id=fixture["provider"]["profile_id"],
        runtime_id="opencode",
        provider_id=fixture["provider"]["provider_id"],
        credential_source="secret_ref",
        runtime_materialization_mode="config_bundle",
    )


def _fixture_configuration(fixture):
    section = fixture["configuration"]
    version_payload = section["versions"][0]
    return (
        Row(
            profile_id=section["profileId"],
            default_for_runtime=section["defaultForRuntime"],
            active_version=section["activeVersion"],
        ),
        Row(
            version=version_payload["version"],
            digest=version_payload["digest"],
            document=version_payload["document"],
            validation_result={
                "ready": version_payload["validationResult"]["ready"]
            },
        ),
    )


def test_common_fixture_resolves_displayed_request_and_plan_identities():
    # AC2: the mixed legacy-Codex/generic-OpenCode catalog plus the OpenCode Go
    # Profile passes shared resolution with a stable Omnigent identity: the
    # displayed selection reproduces the pinned execution_selection verbatim
    # and agrees with the admitted plan's harness.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    fixture = _fixture_document()
    selection = select_execution_configuration(
        _fixture_provider(fixture), [_fixture_configuration(fixture)]
    )
    assert selection == fixture["provider"]["execution_selection"]
    assert selection["harnessId"] == fixture["expectedPlan"]["harnessId"]
    # The submitted request carries exactly the displayed immutable reference.
    request_configuration = fixture["request"]["payload"]["task"]["runtime"][
        "executionConfiguration"
    ]
    assert request_configuration == {
        key: selection[key] for key in ("profileId", "version", "digest")
    }
    # The ordinary fixture Create carries no independent executionTargetRef
    # for the v2 profile, so the shared boundary admits it against the
    # resolved snapshot without a competing target authority.
    validate_omnigent_selection_agreement(
        expected_execution_configuration=request_configuration,
        authored_omnigent=None,
        profile_snapshot=dict(selection),
        selected_provider_profile_id=fixture["provider"]["profile_id"],
    )


def test_common_fixture_rejects_stale_request_and_wrong_account():
    # AC2/AC3: a delayed response (stale configuration) is an actionable
    # conflict, never a silent substitution; a switched account is rejected.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    fixture = _fixture_document()
    selection = select_execution_configuration(
        _fixture_provider(fixture), [_fixture_configuration(fixture)]
    )
    stale = dict(fixture["request"]["payload"]["task"]["runtime"][
        "executionConfiguration"
    ])
    stale["version"] += 1
    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration=stale,
            authored_omnigent=None,
            profile_snapshot=dict(selection),
            selected_provider_profile_id=fixture["provider"]["profile_id"],
        )
    assert error.value.status_code == 409
    assert (
        error.value.detail["code"] == "profile_execution_configuration_changed"
    )
    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration=fixture["request"]["payload"][
                "task"
            ]["runtime"]["executionConfiguration"],
            authored_omnigent=None,
            profile_snapshot=dict(selection),
            selected_provider_profile_id="another-account",
        )
    assert error.value.status_code == 422


def _codex_incompatible_configuration(name="codex-behavior"):
    row, version = configuration(name)
    version.document["providerRequirements"]["runtimeId"] = "codex_cli"
    version.document["providerRequirements"]["providerIds"] = ["openai"]
    return (row, version)


@pytest.mark.parametrize("reverse", [False, True])
def test_reordered_mixed_harness_catalog_preserves_selection(reverse):
    # AC3: catalog order, metadata refresh position, and a competing
    # mixed-harness row cannot displace explicit Profile-owned intent.
    candidates = [configuration(), _codex_incompatible_configuration()]
    if reverse:
        candidates.reverse()
    selected = select_execution_configuration(provider(), candidates)
    assert selected["profileId"] == "behavior"
    assert selected["providerProfileRef"] == "zen"


@pytest.mark.parametrize("reverse", [False, True])
def test_untouched_and_reselected_defaults_agree_under_reordered_catalog(reverse):
    # AC3: an untouched default and an explicitly reselected identical
    # reference resolve to the same identity regardless of catalog order.
    candidates = [configuration("stock", default=True), configuration("custom")]
    if reverse:
        candidates.reverse()
    automatic = select_execution_configuration(provider(), candidates)
    explicit = select_execution_configuration(
        provider(
            execution_configuration={
                key: automatic[key] for key in ("profileId", "version", "digest")
            }
        ),
        candidates,
    )
    assert explicit == automatic


def test_rapid_profile_switching_evaluates_each_intent_statelessly():
    # AC3: rapid Profile/runtime changes are evaluated per call. The first
    # intent is admitted, a switched account without a matching snapshot is
    # rejected, and reselecting the matching account is admitted again.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    snapshot = _snapshot()
    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent=None,
        profile_snapshot=snapshot,
        selected_provider_profile_id="zen",
    )
    with pytest.raises(HTTPException) as error:
        validate_omnigent_selection_agreement(
            expected_execution_configuration=None,
            authored_omnigent=None,
            profile_snapshot=snapshot,
            selected_provider_profile_id="switched-account",
        )
    assert error.value.status_code == 422
    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent={"executionTargetRef": "behavior@1"},
        profile_snapshot=snapshot,
        selected_provider_profile_id="zen",
    )


@pytest.mark.parametrize("authored", [None, "stale-string", 42, ["omnigent-opencode@1"]])
def test_partial_or_delayed_authored_target_is_not_an_explicit_target(authored):
    # AC3: delayed responses and immediate submission may carry partial,
    # non-mapping omnigent payloads; the boundary treats them as unauthored
    # rather than as conflicting explicit targets.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent=authored,
        profile_snapshot=_snapshot(),
        selected_provider_profile_id="zen",
    )


def test_selection_carries_no_model_or_effort_override():
    # AC3: derived tier defaults must not become authored hard overrides at
    # this boundary. The selection carries only configuration identity,
    # harness, policy, and default provenance; model/effort authority stays
    # with the Provider Profile and explicit task overrides downstream.
    selected = select_execution_configuration(provider(), [configuration()])
    assert set(selected) == {
        "profileId",
        "version",
        "digest",
        "providerProfileRef",
        "harnessId",
        "launchPolicyRef",
        "defaultForRuntime",
    }


def test_removed_profile_yields_precise_setup_error():
    # AC4: a removed historical Profile (no candidate rows reach selection)
    # is a precise setup error naming the account, not a silent substitution.
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(provider(), [])
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "profile_execution_configuration_required"
    assert "unavailable, unvalidated, or incompatible" in error.value.detail["message"]
    assert error.value.detail["profileId"] == "zen"


def test_denied_credential_class_yields_unavailable_not_substitution():
    # AC4: an incompatible (denied) credential class is reported for the
    # selected account; a ready configuration for another account must not
    # silently rescue the selection.
    denied = provider(
        profile_id="opencode-go",
        provider_id="opencode-go",
        credential_source="secret_ref",
    )
    candidate = configuration()
    with pytest.raises(HTTPException) as error:
        select_execution_configuration(denied, [candidate])
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "profile_execution_configuration_required"
    assert error.value.detail["profileId"] == "opencode-go"


@pytest.mark.parametrize(
    "noise",
    [
        {"model_catalog_evidence_json": None},
        {"model_catalog_evidence_json": {"validatedAt": "2000-01-01"}},
        {"capacity_exhausted": True, "worker_unavailable": True, "degraded": True},
        {"model_catalog_evidence_json": {}, "capacity_exhausted": True},
    ],
)
def test_capacity_and_discovery_signals_never_change_selection(noise):
    # AC4: temporary provider/host/worker capacity pressure and a missing or
    # stale model-discovery refresh never change inventory, account, model,
    # runtime, or billing authority. Selection is identical with and without
    # the signals, so capacity can only cause durable waiting downstream.
    baseline = select_execution_configuration(provider(), [configuration()])
    assert (
        select_execution_configuration(provider(**noise), [configuration()])
        == baseline
    )


def test_malformed_expectation_is_rejected_as_invalid_reference():
    # AC5: a structurally invalid executionConfiguration reference is a 422
    # contract, distinct from a stale-but-wellformed reference (409) and from
    # a genuinely unauthored older client (admitted via profile-first).
    from api_service.services.profile_execution_selection import (
        validate_execution_configuration_expectation,
    )

    resolved = select_execution_configuration(provider(), [configuration()])
    with pytest.raises(HTTPException) as error:
        validate_execution_configuration_expectation(
            {"profileId": "", "version": 0, "digest": "not-a-digest"}, resolved
        )
    assert error.value.status_code == 422


def test_genuinely_unauthored_client_keeps_profile_first_resolution():
    # AC5/AC6: older clients that omit every expectation retain the shared
    # profile-first resolver instead of being forced through a configuration
    # ceremony. Direct-Claude semantics stay explicit: a native route never
    # satisfies an Omnigent admission.
    from api_service.services.profile_execution_selection import (
        validate_omnigent_selection_agreement,
    )

    snapshot = _snapshot()
    validate_omnigent_selection_agreement(
        expected_execution_configuration=None,
        authored_omnigent=None,
        profile_snapshot=snapshot,
        selected_provider_profile_id=None,
    )
    assert profile_has_native_inventory_route(
        provider(runtime_id="claude_code"), [configuration()]
    ) is True
    with pytest.raises(HTTPException):
        select_execution_configuration(
            provider(runtime_id="claude_code"), [configuration()]
        )
