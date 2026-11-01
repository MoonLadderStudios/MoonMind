"""API-key Claude Provider Profiles run on the shared Omnigent lifecycle.

The Omnigent provider config already carries a key-authenticated Claude
endpoint (base URL, static key, model pins) into the native Claude launch.
These tests pin the exact translation from a Provider Profile's declared
launch environment and the production provisioning boundary that writes it.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentCredentialRuntimeRecord,
)
from moonmind.omnigent.credential_materializers import (
    OmnigentCredentialProvisioningService,
    build_default_credential_materializer_registry,
)
from moonmind.omnigent.harness_platform.credential_bindings import ModelAuthorityBinding
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.materializers import (
    claude_provider_config_endpoint,
    materializer_ref_for_profile,
    validate_binding_materializer,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

_KEY = "sk-ant-api-test-value"


def _api_key_profile(**changes):
    values = {
        "profile_id": "claude_anthropic_api",
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        "env_template": {"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        "file_templates": None,
        "home_path_overrides": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


# The seeded MiniMax Claude profile (api_service.main) as stored.
_MINIMAX_ENV = {
    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
    "ANTHROPIC_AUTH_TOKEN": {"from_secret_ref": "provider_api_key"},
    "ANTHROPIC_MODEL": "MiniMax-M2.7",
    "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "MiniMax-M2.7",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "MiniMax-M2.7",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "MiniMax-M2.7",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}


def test_anthropic_api_key_profile_translates_to_default_endpoint():
    endpoint, reason = claude_provider_config_endpoint(_api_key_profile())

    assert reason == ""
    assert endpoint is not None
    assert endpoint.model_dump(by_alias=True) == {
        "family": "anthropic",
        "secretRole": "anthropic_api_key",
        "baseUrl": "https://api.anthropic.com",
        "defaultModel": None,
        "modelPins": {},
    }
    assert materializer_ref_for_profile(_api_key_profile()) == (
        "omnigent-provider-config@1"
    )


def test_minimax_profile_preserves_its_declared_launch_environment_on_shared_hosts():
    profile = _api_key_profile(
        provider_id="minimax",
        runtime_materialization_mode="env_bundle",
        env_template=_MINIMAX_ENV,
        secret_refs={"provider_api_key": "env://MINIMAX_API_KEY"},
    )
    endpoint, reason = claude_provider_config_endpoint(profile)
    assert reason == ""
    assert endpoint is not None
    assert endpoint.baseUrl == "https://api.minimax.io/anthropic"
    assert endpoint.launchEnvironment == {
        "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    assert materializer_ref_for_profile(profile) == "omnigent-provider-config@1"


def test_declared_endpoint_model_and_tier_pins_are_carried_exactly():
    profile = _api_key_profile(
        env_template={
            "ANTHROPIC_AUTH_TOKEN": {"from_secret_ref": "anthropic_api_key"},
            "ANTHROPIC_BASE_URL": "https://api.anthropic.com/",
            "ANTHROPIC_MODEL": "claude-sonnet-5",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
        }
    )

    endpoint, _reason = claude_provider_config_endpoint(profile)

    assert endpoint is not None
    assert endpoint.baseUrl == "https://api.anthropic.com"
    assert endpoint.defaultModel == "claude-sonnet-5"
    assert endpoint.modelPins == {
        "opus": "claude-opus-5-5",
        "haiku": "claude-haiku-4-5",
    }


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        # Unknown settings and unadmitted endpoints must still stay direct.
        (
            {
                "env_template": {**_MINIMAX_ENV, "CLAUDE_UNCARRIED_SETTING": "1"},
                "secret_refs": {"provider_api_key": "env://MINIMAX_API_KEY"},
            },
            "CLAUDE_UNCARRIED_SETTING",
        ),
        (
            {
                "env_template": {
                    "ANTHROPIC_AUTH_TOKEN": {"from_secret_ref": "anthropic_api_key"},
                    "ANTHROPIC_BASE_URL": "https://api.unadmitted.example/anthropic",
                }
            },
            "egress",
        ),
        (
            {
                "env_template": {
                    "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
                    "ANTHROPIC_BASE_URL": "http://api.anthropic.com",
                }
            },
            "egress",
        ),
        ({"file_templates": [{"path": "settings.json"}]}, "file templates"),
        (
            {"home_path_overrides": {"CLAUDE_HOME": "{{runtime_support_dir}}/c"}},
            "file templates",
        ),
        ({"secret_refs": {}}, "has no SecretRef"),
        (
            {
                "env_template": {
                    "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
                    "ANTHROPIC_MODEL": "{{model}}",
                }
            },
            "not a literal",
        ),
        ({"env_template": {}}, "no ANTHROPIC_API_KEY"),
    ],
)
def test_untranslatable_launch_environment_keeps_its_direct_route(changes, reason):
    profile = _api_key_profile(**changes)

    endpoint, observed = claude_provider_config_endpoint(profile)

    assert endpoint is None
    assert reason in observed
    with pytest.raises(HarnessPlatformError) as error:
        materializer_ref_for_profile(profile)
    assert (
        error.value.code
        == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )


def test_oauth_profiles_keep_their_enrolled_homes():
    assert (
        materializer_ref_for_profile(
            _api_key_profile(
                credential_source="oauth_volume",
                runtime_materialization_mode="oauth_home",
            )
        )
        == "claude-oauth-home@1"
    )
    assert (
        materializer_ref_for_profile(
            _api_key_profile(
                runtime_id="codex_cli",
                provider_id="openai",
                credential_source="oauth_volume",
            )
        )
        == "codex-oauth-home@1"
    )


def test_provider_config_binds_claude_native_on_demand_hosts():
    descriptor = validate_binding_materializer(
        materializer_ref="omnigent-provider-config@1",
        harness_implementation_ref="omnigent-harness-implementation:sha256:" + "c" * 64,
        harness_id="claude-native",
        host_mode="on-demand",
    )

    assert descriptor.acceptedAuthModels == ("omnigent-provider-config",)


class _DockerBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []

    async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
        self.calls.append((list(argv), input_bytes))
        return 0, b"", b""


class _Artifacts:
    def __init__(self) -> None:
        self.payloads: list[object] = []

    async def write_json(self, **kwargs):
        self.payloads.append(kwargs["payload"])
        return "artifact://omnigent/test/credential-attestation.json"


class _EnvSecrets:
    def __init__(self) -> None:
        self.resolved: list[str] = []

    async def resolve(self, ref):
        self.resolved.append(ref.locator)
        return {"ANTHROPIC_API_KEY": _KEY, "OTHER_KEY": "never-read"}[ref.locator]


async def _provision(tmp_path, *, profile_values, materializer_ref):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/credentials.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="claude_anthropic_api",
                runtime_id="claude_code",
                provider_id="anthropic",
                credential_generation=3,
                **profile_values,
            )
        )
        await session.commit()
    backend = _DockerBackend()
    artifacts = _Artifacts()
    resolver = _EnvSecrets()
    service = OmnigentCredentialProvisioningService(
        session_factory=sessions,
        secret_resolution_service=OmnigentSecretResolutionService(
            session_factory=sessions, resolver=resolver
        ),
        registry=build_default_credential_materializer_registry(backend=backend),
        artifact_gateway=artifacts,
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={
                "primary-model": ModelAuthorityBinding(
                    authorityKind="provider_profile",
                    providerProfileRef="claude_anthropic_api",
                    materializerRef=materializer_ref,
                )
            },
            modelConfig=SimpleNamespace(
                qualifiedId="anthropic/claude-sonnet-5", routeRef="anthropic"
            ),
        )
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="claude_anthropic_api",
        capacity_scope_ref="provider-profile:claude_anthropic_api",
        provider_lease_ref="provider-profile-lease:claude-lease",
        credential_generation=3,
        lease=CredentialLease(
            profile_id="claude_anthropic_api",
            runtime_id="claude_code",
            lease_id="claude-lease",
            owner_id="workflow",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="claude-api-key",
        idempotencyKey="claude-api-key",
    )
    try:
        handles = await service.materialize_all(
            request=request,
            plan=plan,
            acquired_leases=(acquired,),
            writer_image_ref="ghcr.io/example/host@sha256:" + "2" * 64,
        )
        async with sessions() as session:
            records = list(
                (await session.scalars(select(OmnigentCredentialRuntimeRecord))).all()
            )
    finally:
        await engine.dispose()
    return handles, records, backend, artifacts, resolver


@pytest.mark.asyncio
async def test_api_key_claude_profile_provisions_omnigent_provider_config(tmp_path):
    handles, records, backend, artifacts, resolver = await _provision(
        tmp_path,
        profile_values={
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "secret_refs": {
                "anthropic_api_key": "env://ANTHROPIC_API_KEY",
                "unused_role": "env://OTHER_KEY",
            },
            "env_template": {
                "ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"},
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5",
            },
        },
        materializer_ref="omnigent-provider-config@1",
    )

    # Only the role the launch environment names is ever resolved.
    assert resolver.resolved == ["ANTHROPIC_API_KEY"]
    (handle,) = handles
    assert handle.materializerRef == "omnigent-provider-config@1"
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"
    }
    assert handle.attachments[0].accessMode == "read-only"
    (payload,) = [data for _argv, data in backend.calls if data]
    config = json.loads(payload)
    assert config == {
        "providers": {
            "moonmind": {
                "kind": "key",
                "default": ["anthropic", "pi"],
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "api_key": _KEY,
                    "models": {"opus": "claude-opus-5-5", "default": "claude-sonnet-5"},
                },
            }
        }
    }
    (evidence,) = artifacts.payloads
    assert evidence["providerBaseUrl"] == "https://api.anthropic.com"
    inspectable = json.dumps(
        {
            "argv": [argv for argv, _data in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "evidence": artifacts.payloads,
        }
    )
    assert _KEY not in inspectable
    assert [record.cleanup_state for record in records] == ["active"]


@pytest.mark.asyncio
async def test_untranslatable_profile_fails_closed_at_provisioning(tmp_path):
    with pytest.raises(HarnessPlatformError) as error:
        await _provision(
            tmp_path,
            profile_values={
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "env_bundle",
                "secret_refs": {"provider_api_key": "env://ANTHROPIC_API_KEY"},
                "env_template": {
                    **_MINIMAX_ENV,
                    "ANTHROPIC_AUTH_TOKEN": {"from_secret_ref": "provider_api_key"},
                    "CLAUDE_UNCARRIED_SETTING": "1",
                },
            },
            materializer_ref="omnigent-provider-config@1",
        )

    assert (
        error.value.code
        == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
    assert "CLAUDE_UNCARRIED_SETTING" in str(error.value)


@pytest.mark.asyncio
async def test_env_bundle_materializes_only_the_key_and_carries_non_secret_tuning(
    tmp_path,
):
    handles, records, backend, artifacts, resolver = await _provision(
        tmp_path,
        profile_values={
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "env_bundle",
            "secret_refs": {"provider_api_key": "env://ANTHROPIC_API_KEY"},
            "env_template": _MINIMAX_ENV,
        },
        materializer_ref="omnigent-provider-config@1",
    )
    (handle,) = handles
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config",
        "ANTHROPIC_SMALL_FAST_MODEL": "MiniMax-M2.7",
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    assert resolver.resolved == ["ANTHROPIC_API_KEY"]
    (body,) = [data for _argv, data in backend.calls if data]
    family = json.loads(body)["providers"]["moonmind"]["anthropic"]
    assert family["base_url"] == "https://api.minimax.io/anthropic"
    assert family["api_key"] == _KEY
    assert family["models"] == {
        "default": "MiniMax-M2.7",
        "opus": "MiniMax-M2.7",
        "sonnet": "MiniMax-M2.7",
        "haiku": "MiniMax-M2.7",
    }
    assert _KEY not in json.dumps(
        {
            "commands": [argv for argv, _data in backend.calls],
            "handle": handle.model_dump(mode="json"),
            "attestations": artifacts.payloads,
        }
    )
    assert [record.cleanup_state for record in records] == ["active"]
