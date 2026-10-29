"""API-key Codex Provider Profiles run on the shared Omnigent lifecycle.

Omnigent's native Codex launch routes a ``key`` provider entry's ``openai``
family through a generated ``model_provider`` override (base URL, bearer auth
command, Responses wire). These tests pin the exact translation from a Codex
Provider Profile's declared launch environment and the production
provisioning boundary that writes it.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, ManagedAgentProviderProfile
from moonmind.omnigent.credential_materializers import (
    OmnigentCredentialProvisioningService,
    build_default_credential_materializer_registry,
)
from moonmind.omnigent.harness_platform.credential_bindings import ModelAuthorityBinding
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import DEFAULT_HOST_CLASS_TEMPLATES
from moonmind.omnigent.harness_platform.materializers import (
    codex_provider_config_endpoint,
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

_KEY = "sk-openai-api-test-value"


def _api_key_profile(**changes):
    # The seeded ``codex_openai_api`` Profile (api_service.main) and the
    # guided OpenAI API-key action both store this launch environment.
    values = {
        "profile_id": "codex_openai_api",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "credential_source": "secret_ref",
        "runtime_materialization_mode": "api_key_env",
        "secret_refs": {"openai_api_key": "env://OPENAI_API_KEY"},
        "env_template": {"OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"}},
        "file_templates": None,
        "home_path_overrides": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_openai_api_key_profile_translates_to_default_endpoint():
    endpoint, reason = codex_provider_config_endpoint(_api_key_profile())

    assert reason == ""
    assert endpoint is not None
    assert endpoint.model_dump(by_alias=True) == {
        "family": "openai",
        "secretRole": "openai_api_key",
        "baseUrl": "https://api.openai.com/v1",
        "defaultModel": None,
        "modelPins": {},
    }
    assert materializer_ref_for_profile(_api_key_profile()) == (
        "omnigent-provider-config@1"
    )


def test_custom_endpoint_is_carried_when_egress_admits_it():
    endpoint, _reason = codex_provider_config_endpoint(
        _api_key_profile(
            env_template={
                "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"},
                "OPENAI_BASE_URL": "https://eu.api.openai.com/v1/",
            }
        )
    )

    assert endpoint is not None
    assert endpoint.baseUrl == "https://eu.api.openai.com/v1"


# The seeded MiniMax Codex Profile brings its own config.toml provider table
# (Chat Completions wire, retry and idle tuning) through a file template.
_MINIMAX_CODEX = {
    "provider_id": "minimax",
    "runtime_materialization_mode": "composite",
    "secret_refs": {"provider_api_key": "env://MINIMAX_API_KEY"},
    "env_template": {"MINIMAX_API_KEY": {"from_secret_ref": "provider_api_key"}},
    "file_templates": [{"path": "{{runtime_support_dir}}/codex-home/config.toml"}],
    "home_path_overrides": {"CODEX_HOME": "{{runtime_support_dir}}/codex-home"},
}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (_MINIMAX_CODEX, "file templates"),
        (
            {
                "env_template": {
                    "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"},
                    "OPENAI_BASE_URL": "https://api.minimax.io/v1",
                }
            },
            "egress",
        ),
        (
            {
                "env_template": {
                    "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"},
                    "OPENAI_ORG_ID": "org-example",
                }
            },
            "OPENAI_ORG_ID",
        ),
        (
            {
                "env_template": {
                    "MINIMAX_API_KEY": {"from_secret_ref": "openai_api_key"},
                }
            },
            "MINIMAX_API_KEY cannot carry a secret",
        ),
        ({"secret_refs": {}}, "has no SecretRef"),
        ({"env_template": {}}, "no OPENAI_API_KEY"),
    ],
)
def test_untranslatable_codex_launch_environment_keeps_its_direct_route(
    changes, reason
):
    profile = _api_key_profile(**changes)

    endpoint, observed = codex_provider_config_endpoint(profile)

    assert endpoint is None
    assert reason in observed
    with pytest.raises(HarnessPlatformError) as error:
        materializer_ref_for_profile(profile)
    assert (
        error.value.code
        == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )


def test_oauth_codex_profiles_keep_their_enrolled_home():
    assert (
        materializer_ref_for_profile(
            _api_key_profile(
                credential_source="oauth_volume",
                runtime_materialization_mode="oauth_home",
            )
        )
        == "codex-oauth-home@1"
    )


def test_provider_config_binds_codex_native_on_demand_hosts():
    descriptor = validate_binding_materializer(
        materializer_ref="omnigent-provider-config@1",
        harness_implementation_ref="omnigent-harness-implementation:sha256:" + "c" * 64,
        harness_id="codex-native",
        host_mode="on-demand",
    )

    assert descriptor.acceptedAuthModels == ("omnigent-provider-config",)
    (codex_host_class,) = [
        template
        for template in DEFAULT_HOST_CLASS_TEMPLATES
        if template.host_class_id == "omnigent-codex"
    ]
    assert "omnigent-provider-config@1" in codex_host_class.materializer_refs


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
        return {"OPENAI_API_KEY": _KEY, "OTHER_KEY": "never-read"}[ref.locator]


async def _provision(tmp_path, *, profile_values):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/credentials.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex_openai_api",
                runtime_id="codex_cli",
                provider_id="openai",
                credential_generation=2,
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
                    providerProfileRef="codex_openai_api",
                    materializerRef="omnigent-provider-config@1",
                )
            },
            modelConfig=SimpleNamespace(
                qualifiedId="openai/gpt-5.5", routeRef="openai"
            ),
        )
    )
    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref="codex_openai_api",
        capacity_scope_ref="provider-profile:codex_openai_api",
        provider_lease_ref="provider-profile-lease:codex-lease",
        credential_generation=2,
        lease=CredentialLease(
            profile_id="codex_openai_api",
            runtime_id="codex_cli",
            lease_id="codex-lease",
            owner_id="workflow",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        ),
    )
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="codex-api-key",
        idempotencyKey="codex-api-key",
    )
    try:
        handles = await service.materialize_all(
            request=request,
            plan=plan,
            acquired_leases=(acquired,),
            writer_image_ref="ghcr.io/example/host@sha256:" + "2" * 64,
        )
    finally:
        await engine.dispose()
    return handles, backend, artifacts, resolver


@pytest.mark.asyncio
async def test_api_key_codex_profile_provisions_omnigent_provider_config(tmp_path):
    handles, backend, artifacts, resolver = await _provision(
        tmp_path,
        profile_values={
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "api_key_env",
            "secret_refs": {
                "openai_api_key": "env://OPENAI_API_KEY",
                "unused_role": "env://OTHER_KEY",
            },
            "env_template": {
                "OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"},
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
            },
        },
    )

    # Only the role the launch environment names is ever resolved.
    assert resolver.resolved == ["OPENAI_API_KEY"]
    (handle,) = handles
    assert handle.materializerRef == "omnigent-provider-config@1"
    assert handle.runtimeEnvironment == {
        "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"
    }
    (payload,) = [data for _argv, data in backend.calls if data]
    # The ``openai`` default is what Omnigent's native Codex launch resolves
    # for the codex harness; the plan's model becomes the provider default.
    assert json.loads(payload) == {
        "providers": {
            "moonmind": {
                "kind": "key",
                "default": ["openai", "pi"],
                "openai": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": _KEY,
                    "models": {"default": "gpt-5.5"},
                },
            }
        }
    }
    (evidence,) = artifacts.payloads
    assert evidence["providerBaseUrl"] == "https://api.openai.com/v1"
    inspectable = json.dumps(
        {
            "argv": [argv for argv, _data in backend.calls],
            "handle": handle.model_dump(by_alias=True, mode="json"),
            "evidence": artifacts.payloads,
        }
    )
    assert _KEY not in inspectable


@pytest.mark.asyncio
async def test_untranslatable_codex_profile_fails_closed_at_provisioning(tmp_path):
    with pytest.raises(HarnessPlatformError) as error:
        await _provision(
            tmp_path,
            profile_values={
                "credential_source": "secret_ref",
                **{
                    key: value
                    for key, value in _MINIMAX_CODEX.items()
                    if key != "provider_id"
                },
            },
        )

    assert (
        error.value.code
        == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
    assert "file templates" in str(error.value)


class _VolumeDocker:
    """Docker volume state for run-scoped provider-config volumes."""

    def __init__(self) -> None:
        self.volumes: set[str] = set()

    async def run(self, argv, *, input_bytes=None, timeout_seconds=60.0):
        argv = list(argv)
        if argv[:3] == ["docker", "volume", "create"]:
            self.volumes.add(argv[-1])
        elif argv[:3] == ["docker", "volume", "ls"]:
            name = argv[argv.index("--filter") + 1].removeprefix("name=^")[:-1]
            return 0, (b"owned" if name in self.volumes else b""), b""
        elif argv[:3] == ["docker", "volume", "rm"]:
            self.volumes.discard(argv[3])
        return 0, b"", b""


_SHAPES = {
    "claude_code": {
        "provider_id": "anthropic",
        "secret_refs": {"anthropic_api_key": "env://ANTHROPIC_API_KEY"},
        "env_template": {"ANTHROPIC_API_KEY": {"from_secret_ref": "anthropic_api_key"}},
        "model": ("anthropic/claude-sonnet-5", "anthropic"),
    },
    "codex_cli": {
        "provider_id": "openai",
        "secret_refs": {"openai_api_key": "env://OPENAI_API_KEY"},
        "env_template": {"OPENAI_API_KEY": {"from_secret_ref": "openai_api_key"}},
        "model": ("openai/gpt-5.5", "openai"),
    },
}


class _AnySecret:
    async def resolve(self, ref):
        return _KEY


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_id", sorted(_SHAPES))
async def test_sibling_attempt_credentials_are_isolated(tmp_path, runtime_id):
    """An interrupted step's new attempt gets its own provider config.

    Re-admission (a new ``admission_epoch`` on the same capacity lease) is how
    a restarted step attempt acquires credentials. Its run-scoped config volume
    must be distinct from the interrupted attempt's, so cleaning either one
    never removes the other's key.
    """

    shape = _SHAPES[runtime_id]
    profile_id = f"{runtime_id}_api"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/attempts.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=profile_id,
                runtime_id=runtime_id,
                provider_id=shape["provider_id"],
                credential_generation=4,
                credential_source="secret_ref",
                runtime_materialization_mode="api_key_env",
                secret_refs=shape["secret_refs"],
                env_template=shape["env_template"],
            )
        )
        await session.commit()
    docker = _VolumeDocker()
    service = OmnigentCredentialProvisioningService(
        session_factory=sessions,
        secret_resolution_service=OmnigentSecretResolutionService(
            session_factory=sessions, resolver=_AnySecret()
        ),
        registry=build_default_credential_materializer_registry(backend=docker),
        artifact_gateway=_Artifacts(),
    )
    qualified_id, route = shape["model"]
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={
                "primary-model": ModelAuthorityBinding(
                    authorityKind="provider_profile",
                    providerProfileRef=profile_id,
                    materializerRef="omnigent-provider-config@1",
                )
            },
            modelConfig=SimpleNamespace(qualifiedId=qualified_id, routeRef=route),
        )
    )

    async def provision(epoch: int):
        acquired = AcquiredProviderLease(
            slot="primary-model",
            provider_profile_ref=profile_id,
            capacity_scope_ref=f"provider-profile:{profile_id}",
            provider_lease_ref="provider-profile-lease:step-lease",
            credential_generation=4,
            lease=CredentialLease(
                profile_id=profile_id,
                runtime_id=runtime_id,
                lease_id="step-lease",
                owner_id="workflow",
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            ),
            owned_by_workflow=True,
            admission_epoch=epoch,
            admission_run_id="run-1",
        )
        return await service.materialize_all(
            request=AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId=f"{runtime_id}-attempt-{epoch}",
                idempotencyKey=f"{runtime_id}-attempt-{epoch}",
            ),
            plan=plan,
            acquired_leases=(acquired,),
            writer_image_ref="ghcr.io/example/host@sha256:" + "2" * 64,
        )

    try:
        (interrupted,) = await provision(1)
        (restarted,) = await provision(2)
        interrupted_volume = interrupted.attachments[0].sourceRef
        restarted_volume = restarted.attachments[0].sourceRef
        assert interrupted.credentialRuntimeRef != restarted.credentialRuntimeRef
        assert interrupted_volume != restarted_volume
        assert set(docker.volumes) == {interrupted_volume, restarted_volume}

        # Cleaning up the interrupted attempt leaves the running sibling intact.
        (result,) = await service.cleanup_all((interrupted,))
        assert result.removed is True
        assert set(docker.volumes) == {restarted_volume}
        # A repeated cleanup of the same attempt is idempotent and still never
        # reaches the sibling.
        (again,) = await service.cleanup_all((interrupted,))
        assert again.evidence == {"alreadyAbsent": True}
        assert set(docker.volumes) == {restarted_volume}
        (sibling,) = await service.cleanup_all((restarted,))
        assert sibling.removed is True and docker.volumes == set()
    finally:
        await engine.dispose()
