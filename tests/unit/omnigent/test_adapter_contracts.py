"""Shared behavior contracts every Omnigent adapter implementation must pass.

Source issue: MoonLadderStudios/MoonMind#3711 (required work 7).

Each contract is written once against a narrow port and is parametrized across
its hermetic implementation and its production implementation. A test double
that quietly diverges from the deployed adapter — different idempotency,
different fencing, different conflict vocabulary — fails here instead of in
production.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbExecutionPlanUsageStore,
    DbRuntimeBindingStore,
    ExecutionPlanUsageIdentity,
    InMemoryExecutionPlanStore,
    InMemoryExecutionPlanUsageStore,
    InMemoryRuntimeBindingStore,
    SessionExecutionPlanStore,
)
from moonmind.omnigent.host_leases import (
    DbOmnigentHostLeaseRepository,
    InMemoryOmnigentHostLeaseRepository,
)


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/contracts.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _plan(*, model: str = "opencode/model", harness: str = "opencode-native"):
    digest = compute_model_config_digest(
        qualifiedId=model,
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": harness,
            "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
            + "3" * 64,
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": (
                "omnigent-credential-bindings:primary@1#sha256:" + "5" * 64
            ),
            "credentialBindings": {
                "primary-model": {
                    "providerProfileRef": "opencode-go-primary",
                    "materializerRef": "opencode-auth-json@1",
                }
            },
            "hostClassRef": "omnigent-opencode@1",
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": digest,
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
    )


# --------------------------------------------------------------------------
# Execution-plan store contract
# --------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def plan_stores(session_factory):
    """Yield every implementation of the execution-plan storage port."""

    session = session_factory()

    class _SessionScopedStore:
        """Adapt the API-transaction store to the same construction shape."""

        def __init__(self) -> None:
            self._inner = SessionExecutionPlanStore(session)

        async def load(self, plan_ref):
            return await self._inner.load(plan_ref)

        async def persist(self, envelope):
            persisted = await self._inner.persist(envelope)
            await session.commit()
            return persisted

        async def load_or_compile(self, *, compile_fn, compile_kwargs):
            envelope = await self._inner.load_or_compile(
                compile_fn=compile_fn, compile_kwargs=compile_kwargs
            )
            await session.commit()
            return envelope

    try:
        yield {
            "in_memory": InMemoryExecutionPlanStore(),
            "postgres_style": DbExecutionPlanStore(session_factory),
            "api_transaction": _SessionScopedStore(),
        }
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "implementation", ("in_memory", "postgres_style", "api_transaction")
)
async def test_execution_plan_store_is_immutable_and_idempotent(
    plan_stores, implementation: str
) -> None:
    store = plan_stores[implementation]
    envelope = _plan()

    assert await store.load(envelope.planRef) is None

    persisted = await store.persist(envelope)
    assert persisted.planRef == envelope.planRef

    # Re-persisting identical immutable authority is idempotent, not a conflict.
    assert (await store.persist(envelope)).planRef == envelope.planRef

    loaded = await store.load(envelope.planRef)
    assert loaded is not None
    assert loaded.payload.harnessId == envelope.payload.harnessId
    assert loaded.payload.executionRealizerRef == "generic-omnigent-host@1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "implementation", ("in_memory", "postgres_style", "api_transaction")
)
async def test_execution_plan_store_rejects_workflow_authored_realizer(
    plan_stores, implementation: str
) -> None:
    store = plan_stores[implementation]

    captured: dict[str, object] = {}

    def compile_fn(**kwargs):
        captured.update(kwargs)
        return _plan()

    envelope = await store.load_or_compile(
        compile_fn=compile_fn,
        compile_kwargs={
            "execution_realizer_ref": "codex-profile-bound@1",
            "executionRealizerRef": "codex-profile-bound@1",
        },
    )
    assert "execution_realizer_ref" not in captured
    assert "executionRealizerRef" not in captured
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"

    # A second compile of the same immutable authority returns the stored plan.
    assert (
        await store.load_or_compile(compile_fn=compile_fn, compile_kwargs={})
    ).planRef == envelope.planRef


# --------------------------------------------------------------------------
# Execution-plan usage (retry identity) contract
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_execution_plan_usage_binds_one_plan_per_idempotency_key(
    session_factory, implementation: str
) -> None:
    if implementation == "in_memory":
        store = InMemoryExecutionPlanUsageStore(InMemoryExecutionPlanStore())
    else:
        store = DbExecutionPlanUsageStore(session_factory)

    identity = ExecutionPlanUsageIdentity(
        workflow_id="workflow-1",
        step_execution_id="workflow-1:run-1:step:1",
        idempotency_key="idem-1",
    )
    payload = {"executionProfileRef": "omnigent-opencode@1", "attempt": 1}

    async def compile_first():
        return _plan(model="opencode/first")

    async def compile_second():
        return _plan(model="opencode/second")

    first = await store.load_or_bind(
        identity=identity, request_payload=payload, compile_fn=compile_first
    )
    # A retry with the same authored request reuses the identical plan even when
    # recompilation would now select different model authority.
    replayed = await store.load_or_bind(
        identity=identity, request_payload=payload, compile_fn=compile_second
    )
    assert replayed.planRef == first.planRef

    with pytest.raises(HarnessPlatformError):
        await store.load_or_bind(
            identity=identity,
            request_payload={**payload, "attempt": 2},
            compile_fn=compile_second,
        )


# --------------------------------------------------------------------------
# Runtime-binding store contract
# --------------------------------------------------------------------------


def _provider_leases(generation: int = 7):
    return {
        "primary-model": {
            "providerProfileRef": "provider-1",
            "providerLeaseRef": "lease-1",
            "credentialGeneration": generation,
            "credentialRuntimeRef": f"credential-runtime:lease-1:{generation}",
        }
    }


async def _runtime_binding_store(session_factory, implementation: str, plan_ref: str):
    if implementation == "in_memory":
        return InMemoryRuntimeBindingStore()
    from api_service.db.models import OmnigentExecutionPlanRecord

    async with session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan_ref,
                schema_version="moonmind.omnigent-execution-plan-envelope.v1",
                payload_json={},
                agent_profile_snapshot_ref="omnigent-agent-profile:sha256:" + "1" * 64,
                credential_binding_set_ref="binding-set",
                harness_id="opencode-native",
                harness_implementation_ref="impl",
                host_class_ref="omnigent-opencode@1",
                launch_policy_ref="omnigent-on-demand@1",
                execution_realizer_ref="generic-omnigent-host@1",
                support_combination_key="support",
            )
        )
        await session.commit()
    return DbRuntimeBindingStore(session_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_runtime_binding_creation_is_idempotent_per_execution_scope(
    session_factory, implementation: str
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "a" * 64
    store = await _runtime_binding_store(session_factory, implementation, plan_ref)

    created = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="workflow-1",
        provider_leases=_provider_leases(),
    )
    again = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="workflow-1",
        provider_leases=_provider_leases(),
    )
    assert again.runtimeBindingRef == created.runtimeBindingRef
    assert created.hostBindingRef is None
    assert created.providerLeases["primary-model"].credentialGeneration == 7

    state = await store.get_current_state(plan_ref, "workflow-1")
    assert state is not None
    assert state.binding.runtimeBindingRef == created.runtimeBindingRef

    fetched = await store.get(created.runtimeBindingRef)
    assert fetched is not None
    assert fetched.executionPlanRef == plan_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_runtime_binding_fences_a_stale_writer(
    session_factory, implementation: str
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "b" * 64
    store = await _runtime_binding_store(session_factory, implementation, plan_ref)

    created = await store.create_initial(
        execution_plan_ref=plan_ref,
        execution_scope_ref="workflow-1",
        provider_leases=_provider_leases(),
    )
    state = await store.get_current_state(plan_ref, "workflow-1")
    assert state is not None

    rotated = await store.reconcile_provider_leases(
        created.runtimeBindingRef,
        provider_leases=_provider_leases(generation=8),
        expected_revision=state.revision,
        expected_fencing_generation=state.fencing_generation,
    )
    assert rotated.providerLeases["primary-model"].credentialGeneration == 8

    with pytest.raises(HarnessPlatformError):
        await store.reconcile_provider_leases(
            created.runtimeBindingRef,
            provider_leases=_provider_leases(generation=9),
            expected_revision=state.revision,
            expected_fencing_generation=state.fencing_generation,
        )


# --------------------------------------------------------------------------
# Host-lease repository contract
# --------------------------------------------------------------------------


def _host_lease_repository(session_factory, implementation: str):
    if implementation == "in_memory":
        return InMemoryOmnigentHostLeaseRepository()
    return DbOmnigentHostLeaseRepository(session_factory)


def _acquire_kwargs(plan_ref: str) -> dict:
    return {
        "execution_plan_ref": plan_ref,
        "runtime_binding_id": "runtime-binding-1",
        "host_class_ref": "omnigent-opencode@1",
        "launch_policy_ref": "omnigent-on-demand@1",
        "harness_id": "opencode-native",
        "harness_implementation_ref": "impl",
        "provider_profile_refs": ("opencode-go-primary",),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_host_lease_acquire_is_idempotent_and_cleanup_is_fenced(
    session_factory, implementation: str
) -> None:
    repository = _host_lease_repository(session_factory, implementation)
    plan_ref = "omnigent-execution-plan:sha256:" + "c" * 64

    lease = await repository.acquire(**_acquire_kwargs(plan_ref))
    again = await repository.acquire(**_acquire_kwargs(plan_ref))
    assert again.leaseRef == lease.leaseRef
    assert lease.status == "allocating"
    assert lease.generation == 1

    ready = await repository.mark_ready(
        lease.leaseRef,
        expected_generation=lease.generation,
        omnigent_host_id="host-1",
        cleanup_handle={"containerName": "mm-host-1"},
    )
    assert ready.status == "ready"
    assert ready.omnigentHostId == "host-1"

    claimed = await repository.claim_cleanup(
        ready.leaseRef, expected_generation=ready.generation
    )
    assert claimed.status == "cleanup_pending"
    assert claimed.generation == ready.generation + 1

    # The superseded generation may never claim cleanup again.
    with pytest.raises(HarnessPlatformError):
        await repository.claim_cleanup(
            ready.leaseRef, expected_generation=ready.generation
        )

    cleaned = await repository.mark_cleaned(
        claimed.leaseRef, expected_generation=claimed.generation
    )
    assert cleaned.status == "cleaned"


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_host_lease_heartbeat_requires_the_current_generation(
    session_factory, implementation: str
) -> None:
    repository = _host_lease_repository(session_factory, implementation)
    plan_ref = "omnigent-execution-plan:sha256:" + "d" * 64

    lease = await repository.acquire(**_acquire_kwargs(plan_ref))
    beat = await repository.heartbeat(
        lease.leaseRef, expected_generation=lease.generation
    )
    assert beat.status == "allocating"
    assert beat.expiresAt is not None

    with pytest.raises(HarnessPlatformError):
        await repository.heartbeat(
            lease.leaseRef, expected_generation=lease.generation + 5
        )


# --------------------------------------------------------------------------
# Credential materializer descriptor contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "materializer_ref",
    ("codex-oauth-home@1", "opencode-auth-json@1", "omnigent-provider-config@1"),
)
def test_registered_materializers_share_one_descriptor_contract(
    materializer_ref: str,
) -> None:
    from moonmind.omnigent.harness_platform.materializers import get_materializer

    materializer = get_materializer(materializer_ref)
    assert materializer.materializerId
    assert materializer.version >= 1
    assert f"{materializer.materializerId}@{materializer.version}" == materializer_ref
    assert materializer.target["kind"]
    assert materializer.acceptedAuthModels
    assert materializer.supportedHostModes
    # Every materializer declares whether a host mode is supported without the
    # caller inspecting a harness name.
    for host_mode in ("on-demand", "static-connected"):
        assert isinstance(materializer.supports_host_mode(host_mode), bool)


@pytest.mark.parametrize(
    "harness_id", ("codex-native", "opencode-native", "pi-native")
)
def test_registered_harnesses_bind_a_materializer_that_accepts_them(
    harness_id: str,
) -> None:
    from moonmind.omnigent.harness_platform.harness_registry import (
        harness_registration,
    )
    from moonmind.omnigent.harness_platform.materializers import get_materializer

    registration = harness_registration(harness_id)
    materializer = get_materializer(registration.materializerRef)
    assert registration.authModel in materializer.acceptedAuthModels
    assert (
        not materializer.acceptedHarnessIds
        or harness_id in materializer.acceptedHarnessIds
    )


# --------------------------------------------------------------------------
# Port conformance: every production adapter satisfies its narrow port
# --------------------------------------------------------------------------


def _host_port_pairs():
    from moonmind.omnigent import host_ports
    from moonmind.omnigent import host_services

    return (
        (host_services.DockerOmnigentHostLauncher, host_ports.OmnigentHostLauncherPort),
        (
            host_services.DockerOmnigentHostCleanupService,
            host_ports.OmnigentHostCleanupPort,
        ),
        (
            host_services.OmnigentSkillDeliveryService,
            host_ports.OmnigentSkillDeliveryPort,
        ),
        (
            host_services.OmnigentWorkspaceMaterializer,
            host_ports.OmnigentWorkspaceMaterializationPort,
        ),
        (
            host_services.OmnigentHostRegistrationService,
            host_ports.OmnigentHostRegistrationPort,
        ),
        (
            host_services.DockerOmnigentHostAttestor,
            host_ports.OmnigentHostAttestationPort,
        ),
        (host_services.OmnigentEgressService, host_ports.OmnigentEgressAttestationPort),
        (
            host_services.OmnigentGithubCredentialService,
            host_ports.OmnigentGithubCredentialPort,
        ),
        (host_services.OmnigentMountedToolService, host_ports.OmnigentMountedToolPort),
    )


@pytest.mark.parametrize(
    ("implementation", "port"),
    _host_port_pairs(),
    ids=lambda value: getattr(value, "__name__", str(value)),
)
def test_production_host_adapters_satisfy_their_narrow_port(
    implementation, port
) -> None:
    missing = [
        name for name in port.__protocol_attrs__ if not hasattr(implementation, name)
    ]
    assert not missing, (
        f"{implementation.__name__} no longer satisfies {port.__name__}: {missing}"
    )


def test_execution_adapters_satisfy_the_profile_bound_ports() -> None:
    from moonmind.omnigent.execution_adapters import (
        DbExecutionPolicyAuthority,
        DbProviderProfileAuthority,
        TemporalExecutionAttempt,
    )
    from moonmind.omnigent.execution_ports import (
        ExecutionAttemptPort,
        ExecutionPolicyAuthorityPort,
        ProviderProfileAuthorityPort,
    )

    assert isinstance(DbProviderProfileAuthority(lambda: None), ProviderProfileAuthorityPort)
    assert isinstance(DbExecutionPolicyAuthority(lambda: None), ExecutionPolicyAuthorityPort)
    attempts = TemporalExecutionAttempt()
    assert isinstance(attempts, ExecutionAttemptPort)
    # Outside an Activity the attempt ordinal is one, never zero or negative.
    assert attempts.current_attempt() == 1
