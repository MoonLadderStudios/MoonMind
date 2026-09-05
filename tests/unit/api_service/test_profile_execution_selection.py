from types import SimpleNamespace as Row

import pytest
from fastapi import HTTPException

from api_service.services.profile_execution_selection import (
    select_execution_configuration,
    configuration_accepts_profile,
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
    latest = Row(version=2, digest="sha256:" + "b" * 64, document=old.document)
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
