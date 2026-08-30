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
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.host_leases import (
    DbOmnigentHostLeaseRepository,
    InMemoryOmnigentHostLeaseRepository,
)
from moonmind.omnigent.host_services.legacy_host_containers import (
    HOST_VOLUME_SUFFIXES,
    LegacyOmnigentHostContainerService,
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
        (
            host_services.LegacyOmnigentHostContainerService,
            host_ports.OmnigentHostContainerInventoryPort,
        ),
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


# ---------------------------------------------------------------------------
# Legacy host container inventory (#3711 required work 3)
#
# Orphan discovery and reclamation is its own narrow port. The deployed adapter
# speaks Docker; the hermetic double speaks a dict. One contract holds both to
# the same ownership, reclamation, and reconciliation rules.
# ---------------------------------------------------------------------------


class _FakeDockerDaemon:
    """A minimal Docker CLI stand-in for the deployed inventory adapter.

    Only the process boundary is faked. The adapter under test still builds
    every real ``docker`` argument vector and applies the real label policy.
    """

    def __init__(self, containers=None, volumes=None, unreconcilable=False) -> None:
        self.containers = dict(containers or {})
        self.volumes = set(volumes or ())
        self._unreconcilable = unreconcilable
        self.commands: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, env=None, check: bool = True):
        self.commands.append(args)
        if args[:2] == ("docker", "inspect"):
            name = args[-1]
            record = self.containers.get(name)
            if record is None:
                return (1, "", "No such object")
            template = args[3]
            if ".State.Running" in template:
                return (0, "true" if record.get("running") else "false", "")
            if ".Id" in template:
                return (0, f"sha256:{name}", "")
            rendered = "|".join(
                str(record.get("labels", {}).get(label, ""))
                for label in ("moonmind.kind", "moonmind.host_lease_id")
                if f'"{label}"' in template
            )
            return (0, rendered, "")
        if args[:3] == ("docker", "volume", "inspect"):
            name = args[-1]
            return (0, name, "") if name in self.volumes else (1, "", "No such volume")
        if args[:2] == ("docker", "ps"):
            return (
                0,
                "\n".join(
                    name
                    for name, record in self.containers.items()
                    if record.get("labels", {}).get("moonmind.kind")
                    == "omnigent-oauth-host"
                ),
                "",
            )
        if args[:3] == ("docker", "rm", "-f"):
            if not self._unreconcilable:
                self.containers.pop(args[-1], None)
            return (0, "", "")
        if args[:4] == ("docker", "volume", "rm", "-f"):
            if not self._unreconcilable:
                self.volumes.discard(args[-1])
            return (0, "", "")
        return (0, "", "")


class _HermeticHostContainerInventory:
    """In-memory implementation of the same ownership and reclamation rules."""

    def __init__(self, containers=None, volumes=None, unreconcilable=False) -> None:
        self.containers = dict(containers or {})
        self.volumes = set(volumes or ())
        self._unreconcilable = unreconcilable

    def _owned(self, container_name: str) -> bool:
        record = self.containers.get(container_name) or {}
        return record.get("labels", {}).get("moonmind.kind") == "omnigent-oauth-host"

    async def container_exists(self, container_name: str) -> bool:
        record = self.containers.get(container_name)
        return bool(record and record.get("running"))

    async def list_managed_containers(self) -> list[str]:
        return [name for name in self.containers if self._owned(name)]

    async def managed_container_host_lease_ref(self, container_name: str) -> str | None:
        if container_name not in self.containers:
            return None
        if not self._owned(container_name):
            raise OmnigentOAuthHostError(
                "refusing to inspect a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        labels = self.containers[container_name].get("labels", {})
        return str(labels.get("moonmind.host_lease_id") or "").strip() or None

    async def remove_container(self, container_name: str) -> None:
        if container_name not in self.containers:
            return
        if not self._owned(container_name):
            raise OmnigentOAuthHostError(
                "refusing to remove a container outside Omnigent ownership",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )
        volume_names = tuple(
            f"{container_name}-{suffix}" for suffix in HOST_VOLUME_SUFFIXES
        )
        if not self._unreconcilable:
            self.containers.pop(container_name, None)
            self.volumes.difference_update(volume_names)
        if container_name in self.containers or (self.volumes & set(volume_names)):
            raise OmnigentOAuthHostError(
                "orphaned Omnigent host cleanup could not be reconciled",
                code="OMNIGENT_HOST_CLEANUP_INCOMPLETE",
            )

    async def assert_container_owned(self, *, container_name: str, lease_id: str) -> None:
        record = self.containers.get(container_name) or {}
        observed = str(record.get("labels", {}).get("moonmind.host_lease_id") or "")
        if not record or observed != lease_id:
            raise OmnigentOAuthHostError(
                "container does not belong to the current host lease",
                code="OMNIGENT_HOST_OWNERSHIP_MISMATCH",
            )


def _host_inventory(implementation: str, **kwargs):
    if implementation == "hermetic":
        return _HermeticHostContainerInventory(**kwargs)
    return LegacyOmnigentHostContainerService(run_command=_FakeDockerDaemon(**kwargs))


_OWNED = {
    "labels": {"moonmind.kind": "omnigent-oauth-host", "moonmind.host_lease_id": "lease-1"},
    "running": True,
}
_FOREIGN = {"labels": {"moonmind.kind": "someone-else"}, "running": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("hermetic", "docker"))
async def test_host_inventory_reports_only_label_owned_containers(
    implementation: str,
) -> None:
    inventory = _host_inventory(
        implementation, containers={"owned": _OWNED, "foreign": _FOREIGN}
    )

    assert await inventory.list_managed_containers() == ["owned"]
    assert await inventory.container_exists("owned") is True
    assert await inventory.container_exists("absent") is False
    assert await inventory.managed_container_host_lease_ref("owned") == "lease-1"
    # An unknown container is absent, not a failure: the janitor races cleanup.
    assert await inventory.managed_container_host_lease_ref("absent") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("hermetic", "docker"))
async def test_host_inventory_refuses_containers_outside_omnigent_ownership(
    implementation: str,
) -> None:
    inventory = _host_inventory(implementation, containers={"foreign": _FOREIGN})

    for operation in (
        inventory.managed_container_host_lease_ref("foreign"),
        inventory.remove_container("foreign"),
    ):
        with pytest.raises(OmnigentOAuthHostError) as excinfo:
            await operation
        assert excinfo.value.code == "OMNIGENT_HOST_OWNERSHIP_MISMATCH"

    # An unknown name is a no-op, never an unscoped removal.
    await inventory.remove_container("absent")


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("hermetic", "docker"))
async def test_host_inventory_reclaims_run_owned_volumes_with_the_container(
    implementation: str,
) -> None:
    volumes = tuple(f"owned-{suffix}" for suffix in HOST_VOLUME_SUFFIXES)
    inventory = _host_inventory(
        implementation, containers={"owned": _OWNED}, volumes=volumes
    )

    await inventory.remove_container("owned")

    assert await inventory.list_managed_containers() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("hermetic", "docker"))
async def test_host_inventory_fails_when_reclamation_cannot_be_reconciled(
    implementation: str,
) -> None:
    inventory = _host_inventory(
        implementation,
        containers={"owned": _OWNED},
        volumes=("owned-state",),
        unreconcilable=True,
    )

    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        await inventory.remove_container("owned")
    assert excinfo.value.code == "OMNIGENT_HOST_CLEANUP_INCOMPLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("hermetic", "docker"))
async def test_host_inventory_binds_a_container_to_its_current_lease(
    implementation: str,
) -> None:
    inventory = _host_inventory(implementation, containers={"owned": _OWNED})

    await inventory.assert_container_owned(container_name="owned", lease_id="lease-1")

    for container_name, lease_id in (("owned", "lease-2"), ("absent", "lease-1")):
        with pytest.raises(OmnigentOAuthHostError) as excinfo:
            await inventory.assert_container_owned(
                container_name=container_name, lease_id=lease_id
            )
        assert excinfo.value.code == "OMNIGENT_HOST_OWNERSHIP_MISMATCH"


# ---------------------------------------------------------------------------
# Profile-bound host ports (#3711 required work 2 and 3)
#
# The legacy Codex coordinator depends on four separate host capabilities, each
# with a typed signature. These contracts fail if a port is reunified into one
# broad runtime interface, reopens an untyped ``**kwargs`` payload, or drifts
# away from the adapter that implements it.
# ---------------------------------------------------------------------------


def _profile_bound_host_ports():
    from moonmind.omnigent import execution_ports, host_ports

    return (
        (execution_ports.OmnigentHostPreparationPort, "prepare_host"),
        (execution_ports.OmnigentWorkspacePublicationPort, "publish_workspace"),
        (
            execution_ports.OmnigentProviderSessionInspectionPort,
            "inspect_session_completion",
        ),
        (host_ports.OmnigentHostReleasePort, "stop_host"),
        (host_ports.OmnigentStaticHostReleasePort, "stop_static_host"),
    )


@pytest.mark.parametrize(
    ("port", "operation"),
    _profile_bound_host_ports(),
    ids=lambda value: getattr(value, "__name__", str(value)),
)
def test_profile_bound_host_ports_own_exactly_one_capability(port, operation) -> None:
    assert set(port.__protocol_attrs__) == {operation}, (
        f"{port.__name__} owns more than one capability; keep host preparation, "
        "workspace publication, session inspection, and host release separate"
    )


@pytest.mark.parametrize(
    ("port", "operation"),
    _profile_bound_host_ports(),
    ids=lambda value: getattr(value, "__name__", str(value)),
)
def test_profile_bound_host_ports_are_typed_not_kwargs_bags(port, operation) -> None:
    import inspect

    signature = inspect.signature(getattr(port, operation))
    kinds = [parameter.kind for parameter in signature.parameters.values()]
    assert inspect.Parameter.VAR_KEYWORD not in kinds, (
        f"{port.__name__}.{operation} accepts **kwargs; declare the parameters "
        "so an adapter cannot silently diverge from the coordinator"
    )
    assert inspect.Parameter.VAR_POSITIONAL not in kinds


@pytest.mark.parametrize(
    ("port", "operation"),
    _profile_bound_host_ports(),
    ids=lambda value: getattr(value, "__name__", str(value)),
)
def test_deployed_legacy_host_runtime_matches_each_port_signature(
    port, operation
) -> None:
    """The deployed Codex host adapter is the paired implementation here."""

    import inspect

    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime

    declared = set(inspect.signature(getattr(port, operation)).parameters) - {"self"}
    implemented = set(
        inspect.signature(getattr(OmnigentOAuthHostRuntime, operation)).parameters
    ) - {"self"}
    assert declared == implemented, (
        f"{port.__name__}.{operation} drifted from OmnigentOAuthHostRuntime: "
        f"port-only={sorted(declared - implemented)}, "
        f"adapter-only={sorted(implemented - declared)}"
    )


def test_legacy_host_runtime_satisfies_every_narrow_host_port() -> None:
    from moonmind.omnigent import execution_ports, host_ports
    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime

    runtime = OmnigentOAuthHostRuntime(client=None)
    for port in (
        execution_ports.OmnigentHostPreparationPort,
        execution_ports.OmnigentWorkspacePublicationPort,
        execution_ports.OmnigentProviderSessionInspectionPort,
        execution_ports.ProfileBoundHostPorts,
        host_ports.OmnigentHostReleasePort,
        host_ports.OmnigentStaticHostReleasePort,
        host_ports.OmnigentHostContainerInventoryPort,
        host_ports.OmnigentHostReclamationPorts,
    ):
        assert isinstance(runtime, port), f"{port.__name__} is unsatisfied"


def test_dependency_set_protocols_add_no_capability_of_their_own() -> None:
    """A composed protocol declares a dependency set, never a fifth capability."""

    from moonmind.omnigent import execution_ports, host_ports

    composed = {
        execution_ports.ProfileBoundHostPorts: (
            execution_ports.OmnigentHostPreparationPort,
            execution_ports.OmnigentWorkspacePublicationPort,
            execution_ports.OmnigentProviderSessionInspectionPort,
            host_ports.OmnigentHostReleasePort,
        ),
        host_ports.OmnigentHostReclamationPorts: (
            host_ports.OmnigentHostContainerInventoryPort,
            host_ports.OmnigentHostReleasePort,
            host_ports.OmnigentStaticHostReleasePort,
        ),
    }
    for port, members in composed.items():
        union: set[str] = set()
        for member in members:
            union |= set(member.__protocol_attrs__)
        assert set(port.__protocol_attrs__) == union, (
            f"{port.__name__} owns behavior beyond its member ports"
        )


# ---------------------------------------------------------------------------
# Stable runtime binding (#3711 required work 2)
#
# The behavior contract runs against the hermetic implementation. The deployed
# implementation is conformance-checked here rather than behavior-paired: its
# snapshot digest is recomputed from the persisted row, and this suite's shared
# SQLite fixture returns ``DateTime(timezone=True)`` columns as naive datetimes,
# so the digest can only be reproduced against PostgreSQL. Recorded as
# conformance-only in docs/Omnigent/OmnigentModuleArchitecture.md §4.
# ---------------------------------------------------------------------------


def _stable_binding_store(implementation: str):
    from moonmind.omnigent.runtime_bindings import InMemoryStableRuntimeBindingStore

    assert implementation == "in_memory"
    return InMemoryStableRuntimeBindingStore()


def test_both_stable_runtime_binding_stores_satisfy_the_declared_port() -> None:
    from moonmind.omnigent.runtime_bindings import (
        DbRuntimeBindingStore as DbStableRuntimeBindingStore,
    )
    from moonmind.omnigent.runtime_bindings import (
        InMemoryStableRuntimeBindingStore,
        StableRuntimeBindingStore,
    )

    assert isinstance(InMemoryStableRuntimeBindingStore(), StableRuntimeBindingStore)
    assert isinstance(
        DbStableRuntimeBindingStore(lambda: None), StableRuntimeBindingStore
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory",))
async def test_stable_runtime_binding_creation_is_idempotent_and_conflict_typed(
    implementation: str,
) -> None:
    plan_ref = "omnigent-execution-plan:sha256:" + "e" * 64
    store = _stable_binding_store(implementation)

    first = await store.create_initial(
        execution_plan_ref=plan_ref,
        idempotency_key="idem-1",
        provider_leases=_provider_leases(),
    )
    replay = await store.create_initial(
        execution_plan_ref=plan_ref,
        idempotency_key="idem-1",
        provider_leases=_provider_leases(),
    )
    assert replay.bindingId == first.bindingId
    assert (await store.get(first.bindingId)).bindingId == first.bindingId

    # Same identity, different authority is a typed conflict, never an overwrite.
    with pytest.raises(HarnessPlatformError):
        await store.create_initial(
            execution_plan_ref=plan_ref,
            idempotency_key="idem-1",
            provider_leases=_provider_leases(generation=9),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory",))
async def test_stable_runtime_binding_update_is_revision_fenced(
    implementation: str,
) -> None:
    from moonmind.omnigent.runtime_bindings import RuntimeBindingState

    plan_ref = "omnigent-execution-plan:sha256:" + "f" * 64
    store = _stable_binding_store(implementation)
    binding = await store.create_initial(
        execution_plan_ref=plan_ref,
        idempotency_key="idem-2",
        provider_leases=_provider_leases(),
    )

    advanced = await store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.credentials_acquired,
    )
    assert advanced.revision > binding.revision
    assert advanced.state is RuntimeBindingState.credentials_acquired

    # The stale writer loses; it never silently overwrites the winner.
    with pytest.raises(HarnessPlatformError):
        await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=RuntimeBindingState.credentials_materialized,
        )
    current = await store.get(binding.bindingId)
    assert current.state is RuntimeBindingState.credentials_acquired


# ---------------------------------------------------------------------------
# Harness catalog observation (#3711 required work 2)
# ---------------------------------------------------------------------------


class _ContractInventoryClient:
    """A bounded upstream inventory used to build one real catalog snapshot."""

    def __init__(self, version: str = "0.11.0") -> None:
        self._version = version

    async def get_version(self) -> str:
        return self._version

    async def list_harnesses(self) -> list[dict]:
        return [
            {
                "id": "opencode-native",
                "label": "OpenCode",
                "package": "omnigent.harness.opencode",
                "version": self._version,
            }
        ]

    async def list_agents(self) -> list[dict]:
        return [{"id": "opencode-native-ui", "version": "7"}]

    async def list_hosts(self) -> list[dict]:
        return [{"host_id": "host-1", "name": "connected", "status": "online"}]


async def _catalog_result(*, version: str = "0.11.0", observed_day: int = 21):
    from datetime import UTC, datetime

    from moonmind.omnigent.harness_platform.catalog_service import (
        OmnigentHarnessCatalogService,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        InMemoryHarnessCatalogRepository,
    )

    service = OmnigentHarnessCatalogService(
        client=_ContractInventoryClient(version),
        repository=InMemoryHarnessCatalogRepository(),
        endpoint_ref="default",
        omnigent_build_digest="sha256:" + "1" * 64,
        clock=lambda: datetime(2026, 8, observed_day, tzinfo=UTC),
    )
    return await service.synchronize()


def _catalog_repository(session_factory, implementation: str):
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        InMemoryHarnessCatalogRepository,
    )

    if implementation == "in_memory":
        return InMemoryHarnessCatalogRepository()
    return DbHarnessCatalogRepository(session_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_catalog_repository_round_trips_and_replays_one_snapshot(
    session_factory, implementation: str
) -> None:
    repository = _catalog_repository(session_factory, implementation)
    result = await _catalog_result()

    assert await repository.load(result.snapshot.catalogRef) is None
    await repository.persist(result)

    loaded = await repository.load(result.snapshot.catalogRef)
    assert loaded is not None
    assert loaded.snapshot.catalogRef == result.snapshot.catalogRef
    assert loaded.snapshot.omnigentBuildDigest == result.snapshot.omnigentBuildDigest
    assert {item.harnessId for item in loaded.trust_records} == {
        item.harnessId for item in result.trust_records
    }

    # Re-observing the same upstream state is a replay, never a conflict.
    await repository.persist(result)
    assert (
        await repository.load(result.snapshot.catalogRef)
    ).snapshot.catalogRef == result.snapshot.catalogRef


@pytest.mark.asyncio
@pytest.mark.parametrize("implementation", ("in_memory", "postgres_style"))
async def test_catalog_repository_latest_tracks_the_newest_observation(
    session_factory, implementation: str
) -> None:
    repository = _catalog_repository(session_factory, implementation)
    older = await _catalog_result(version="0.11.0", observed_day=21)
    newer = await _catalog_result(version="0.12.0", observed_day=22)
    assert older.snapshot.catalogRef != newer.snapshot.catalogRef

    await repository.persist(older)
    await repository.persist(newer)

    latest = await repository.latest("default")
    assert latest is not None
    assert latest.snapshot.catalogRef == newer.snapshot.catalogRef
    assert await repository.latest("unknown-endpoint") is None


# ---------------------------------------------------------------------------
# Ports whose deployed implementation has no second in-repo implementation.
#
# These get machine-checked conformance rather than a paired behavior contract,
# so the port cannot drift away from the adapter that satisfies it. Documented
# as conformance-only in docs/Omnigent/OmnigentModuleArchitecture.md §4.
# ---------------------------------------------------------------------------


def _conformance_only_port_pairs():
    from moonmind.auth.resolvers.base import RootSecretResolver
    from moonmind.omnigent.harness_platform.catalog_service import (
        OmnigentInventoryClient,
    )
    from moonmind.omnigent.provider_leases import ProviderLeaseClient
    from moonmind.omnigent.remediation_workspace import (
        RemediationWorkspaceOwner,
        SandboxRemediationWorkspaceOwner,
    )
    from moonmind.omnigent.secret_resolution import SecretResolver
    from moonmind.provider_profiles.lease_client import ProviderProfileLeaseClient
    from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

    return (
        (OmnigentInventoryClient, OmnigentHttpClient),
        (ProviderLeaseClient, ProviderProfileLeaseClient),
        (RemediationWorkspaceOwner, SandboxRemediationWorkspaceOwner),
        (SecretResolver, RootSecretResolver),
    )


@pytest.mark.parametrize(
    ("port", "implementation"),
    _conformance_only_port_pairs(),
    ids=lambda value: getattr(value, "__name__", str(value)),
)
def test_deployed_adapter_satisfies_its_declared_port(port, implementation) -> None:
    declared = set(port.__protocol_attrs__)
    assert declared, f"{port.__name__} declares no operation"
    missing = [name for name in declared if not hasattr(implementation, name)]
    assert not missing, (
        f"{implementation.__name__} no longer satisfies {port.__name__}: {missing}"
    )
