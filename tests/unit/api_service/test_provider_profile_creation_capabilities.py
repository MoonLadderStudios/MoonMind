"""Creation-capability regressions for MoonLadderStudios/MoonMind#3820."""

from __future__ import annotations

import pytest

from api_service.services import provider_profile_creation
from api_service.services.provider_profile_creation import (
    ExpertManualCredentialCapability,
    RuntimeProviderAuthenticationCapability,
    expert_manual_credential_launch_ready,
    infer_authentication_method,
    provider_profile_creation_capabilities,
    required_secret_roles,
    validate_manual_credential_contract,
    validate_credential_contract,
)


def test_first_party_openai_creation_capabilities_are_guided_and_locked() -> None:
    capabilities = provider_profile_creation_capabilities(
        runtime_id="codex_cli",
        provider_id="openai",
    )

    methods = {
        method["id"]: method for method in capabilities["authentication_methods"]
    }
    assert list(methods) == ["oauth", "api_key"]
    assert methods["oauth"]["fields"]["credential_source"] == {
        "value": "oauth_volume",
        "source": "runtime_provider_strategy",
        "editable": False,
        "lock_reason": "OAuth enrollment owns the credential source.",
    }
    assert methods["oauth"]["fields"]["runtime_materialization_mode"][
        "value"
    ] == "oauth_home"
    assert methods["oauth"]["imported_volume"] == {
        "supported": True,
        "mount_path": "/home/app/.codex",
        "source": "runtime_provider_strategy",
        "lock_reason": "The runtime strategy owns the credential mount path.",
    }
    assert methods["api_key"]["secret_roles"] == [
        {
            "role": "openai_api_key",
            "label": "OpenAI API key",
            "required": True,
            "compatible_schemes": ["db", "env"],
        }
    ]


def test_credential_free_method_requires_authoritative_backend_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = RuntimeProviderAuthenticationCapability(
        runtime_id="custom_runtime",
        provider_id="local_provider",
        credential_free=True,
    )
    monkeypatch.setattr(
        provider_profile_creation,
        "_RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES",
        (capability,),
    )
    unsupported = provider_profile_creation_capabilities(
        runtime_id="custom_runtime",
        provider_id="unsupported_provider",
    )
    authorized = provider_profile_creation_capabilities(
        runtime_id=capability.runtime_id,
        provider_id=capability.provider_id,
    )

    assert unsupported["supported"] is False
    assert unsupported["authentication_methods"] == []
    assert [method["id"] for method in authorized["authentication_methods"]] == [
        "none"
    ]
    assert authorized["authentication_methods"][0]["label"] == "No credentials"


def test_required_secret_roles_are_backend_declared() -> None:
    assert required_secret_roles("claude_code", "anthropic") == (
        "anthropic_api_key",
    )
    assert required_secret_roles("custom_runtime", "custom_provider") == ()


@pytest.mark.parametrize(
    ("credential_source", "materialization_mode"),
    [
        ("oauth_volume", "api_key_env"),
        ("secret_ref", "oauth_home"),
        ("none", "oauth_home"),
    ],
)
def test_incoherent_source_and_materialization_are_rejected(
    credential_source: str,
    materialization_mode: str,
) -> None:
    with pytest.raises(ValueError, match="Incoherent credential contract"):
        validate_credential_contract(
            credential_source=credential_source,
            runtime_materialization_mode=materialization_mode,
        )


def test_unknown_existing_authentication_contract_is_not_silently_replaced() -> None:
    capabilities = provider_profile_creation_capabilities(
        runtime_id="codex_cli",
        provider_id="openai",
    )

    assert (
        infer_authentication_method(
            credential_source="secret_ref",
            runtime_materialization_mode="config_bundle",
            authentication_methods=capabilities["authentication_methods"],
        )
        is None
    )
    assert (
        infer_authentication_method(
            credential_source="secret_ref",
            runtime_materialization_mode="api_key_env",
            authentication_methods=capabilities["authentication_methods"],
        )
        == "api_key"
    )


def test_pending_oauth_setup_is_an_explicit_typed_authentication_state() -> None:
    capabilities = provider_profile_creation_capabilities(
        runtime_id="codex_cli",
        provider_id="openai",
    )

    assert (
        infer_authentication_method(
            credential_source="none",
            runtime_materialization_mode="api_key_env",
            authentication_methods=capabilities["authentication_methods"],
            auth_state="oauth_pending",
        )
        == "oauth"
    )
    assert (
        infer_authentication_method(
            credential_source="none",
            runtime_materialization_mode="api_key_env",
            authentication_methods=capabilities["authentication_methods"],
            auth_state="connected",
        )
        is None
    )


def test_locked_guided_contract_cannot_be_submitted_as_manual_overrides() -> None:
    capabilities = provider_profile_creation_capabilities(
        runtime_id="codex_cli",
        provider_id="openai",
    )

    with pytest.raises(ValueError, match="does not advertise expert manual"):
        validate_manual_credential_contract(
            credential_source="secret_ref",
            runtime_materialization_mode="api_key_env",
            authentication_methods=capabilities["authentication_methods"],
        )


def test_exact_expert_manual_contract_requires_typed_backend_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = RuntimeProviderAuthenticationCapability(
        runtime_id="custom_runtime",
        provider_id="custom_provider",
        expert_manual_credentials=(
            ExpertManualCredentialCapability(
                authentication_method="api_key",
                label="Expert API key",
                credential_source="secret_ref",
                runtime_materialization_mode="config_bundle",
            ),
        ),
    )
    monkeypatch.setattr(
        provider_profile_creation,
        "_RUNTIME_PROVIDER_AUTHENTICATION_CAPABILITIES",
        (capability,),
    )
    capabilities = provider_profile_creation_capabilities(
        runtime_id=capability.runtime_id,
        provider_id=capability.provider_id,
    )

    validate_manual_credential_contract(
        credential_source="secret_ref",
        runtime_materialization_mode="config_bundle",
        authentication_methods=capabilities["authentication_methods"],
    )
    with pytest.raises(ValueError, match="does not match"):
        validate_manual_credential_contract(
            credential_source="none",
            runtime_materialization_mode="composite",
            authentication_methods=capabilities["authentication_methods"],
        )


def test_expert_minimax_capability_requires_provider_launch_templates() -> None:
    capabilities = provider_profile_creation_capabilities(
        runtime_id="codex_cli",
        provider_id="minimax",
    )
    method = capabilities["authentication_methods"][0]

    assert method["launch_ready_after_setup"] is False
    assert [role["role"] for role in method["secret_roles"]] == [
        "provider_api_key"
    ]
    common = {
        "runtime_id": "codex_cli",
        "provider_id": "minimax",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "composite",
        "secret_refs": {"provider_api_key": "env://MINIMAX_API_KEY"},
        "command_behavior": {},
    }
    assert (
        expert_manual_credential_launch_ready(
            **common,
            env_template={},
            file_templates=[],
            home_path_overrides={},
        )
        is False
    )
    assert expert_manual_credential_launch_ready(
        **common,
        env_template={
            "MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}
        },
        file_templates=[{"path": "config.toml", "content": "profile"}],
        home_path_overrides={"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
    )
