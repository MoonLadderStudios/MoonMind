"""Model credential provisioning leaves repository authority with its owner."""

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
from moonmind.omnigent.harness_platform.credential_bindings import (
    ModelAuthorityBinding,
    RepositoryAuthorityBinding,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_model_lease", [False, True])
async def test_mixed_plan_provisions_only_model_credentials(
    tmp_path, missing_model_lease
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/credentials.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = LocalTemporalArtifactStore(tmp_path / "blobs")

    class RejectSecretLookup:
        async def resolve(self, _ref):
            raise AssertionError("Credentialless model must not read a secret")

    class ArtifactGateway:
        async def write_json(self, *, request, name, payload, link_type):
            async with sessions() as session:
                service = TemporalArtifactService(
                    TemporalArtifactRepository(session), store=store
                )
                artifact, _ = await service.create(
                    principal="service:credential-test",
                    metadata_json={"name": name},
                    content_type="application/json",
                )
                await service.write_complete(
                    artifact_id=artifact.artifact_id,
                    principal="service:credential-test",
                    payload=json.dumps(payload).encode(),
                    content_type="application/json",
                )
                return f"artifact:{artifact.artifact_id}"

    try:
        async with sessions() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="model-profile",
                    runtime_id="opencode",
                    provider_id="opencode",
                    credential_generation=1,
                )
            )
            await session.commit()
        model_binding = ModelAuthorityBinding(
            authorityKind="provider_profile",
            providerProfileRef="model-profile",
            materializerRef="none@1",
        )
        bindings = {"primary-model": model_binding}
        for slot, role in (
            ("source", "source_read"),
            ("destination", "destination_write"),
        ):
            bindings[slot] = RepositoryAuthorityBinding(
                authorityKind="repository_connection",
                connectionRef="repository-connection:test",
                repositoryAccessSnapshotRef="repository-access-snapshot:sha256:"
                + "a" * 64,
                materializerRef="repository-broker@1",
                repositoryRole=role,
            )
        plan = SimpleNamespace(
            payload=SimpleNamespace(
                credentialBindings=bindings,
                modelConfig=SimpleNamespace(
                    qualifiedId="opencode/example", routeRef="opencode"
                ),
            )
        )
        lease = CredentialLease(
            profile_id="model-profile",
            runtime_id="opencode",
            lease_id="model-lease",
            owner_id="workflow",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        )
        acquired = AcquiredProviderLease(
            slot="primary-model",
            provider_profile_ref="model-profile",
            capacity_scope_ref="provider-profile:model-profile",
            provider_lease_ref="provider-profile-lease:model-lease",
            credential_generation=1,
            lease=lease,
        )
        service = OmnigentCredentialProvisioningService(
            session_factory=sessions,
            secret_resolution_service=OmnigentSecretResolutionService(
                session_factory=sessions, resolver=RejectSecretLookup()
            ),
            registry=build_default_credential_materializer_registry(),
            artifact_gateway=ArtifactGateway(),
        )
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="credential-test",
            idempotencyKey="credential-test",
        )
        if missing_model_lease:
            with pytest.raises(HarnessPlatformError, match="primary-model is missing"):
                await service.materialize_all(
                    request=request,
                    plan=plan,
                    acquired_leases=(),
                    writer_image_ref="unused",
                )
            return
        handles = await service.materialize_all(
            request=request,
            plan=plan,
            acquired_leases=(acquired,),
            writer_image_ref="unused",
        )
        assert len(handles) == 1
        assert handles[0].providerProfileRef == "model-profile"
        async with sessions() as session:
            rows = (
                (await session.execute(select(OmnigentCredentialRuntimeRecord)))
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].cleanup_state == "active"
            artifact_service = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            _, payload = await artifact_service.read(
                artifact_id=handles[0].attestationRef.removeprefix("artifact:"),
                principal="service:credential-test",
            )
            assert json.loads(payload)["secretCopied"] is False
        await service.cleanup_all(handles)
        async with sessions() as session:
            row = await session.get(
                OmnigentCredentialRuntimeRecord, handles[0].credentialRuntimeRef
            )
            assert row.cleanup_state == "cleaned"
    finally:
        await engine.dispose()
