from types import SimpleNamespace as Row

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


@pytest.mark.parametrize("validation_result", [None, {}, {"ready": False}, {"ready": 1}])
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


@pytest.mark.parametrize("runtime", ["codex", "codex_cli", "claude", "claude_code", "jules"])
def test_direct_runtime_inventory_does_not_require_omnigent_configuration(runtime):
    native = provider(runtime_id=runtime)
    assert profile_has_native_inventory_route(native, [configuration()]) is True
    # Explicit Omnigent admission retains the configuration requirement.
    with pytest.raises(HTTPException):
        select_execution_configuration(native, [configuration()])


@pytest.mark.parametrize("runtime", ["opencode", "omnigent", "codex_cloud", "unknown"])
def test_unsupported_direct_runtime_keeps_configuration_requirement(runtime):
    assert profile_has_native_inventory_route(provider(runtime_id=runtime), []) is False


@pytest.mark.parametrize("validation_result", [None, {}, {"ready": False}, {"ready": True}])
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
            "profileId": "missing", "version": 1, "digest": "sha256:" + "a" * 64,
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
