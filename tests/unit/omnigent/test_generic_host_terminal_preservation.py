"""Terminal-result preservation across managed publication failures.

Regression coverage for MoonLadderStudios/MoonMind#3825 outcome-4 (recovery
and preservation parity, owned by #4016): the generic host must record the
independently verified compute result as the binding's ``terminalResult``
BEFORE attempting managed repository publication. A publication failure must
not erase verified saved work, and saving must never upgrade failed compute.

These tests drive the production ``GenericOmnigentHostRealizer`` lifecycle
methods (fresh ``_execute_lifecycle`` and ``_resume_attested_host``) with the
production ``InMemoryStableRuntimeBindingStore`` -- which shares the
``evolve_binding`` CAS/immutability contract with the database implementation
-- and the production ``_publish_repository`` decision. Only the process
edge (leases, host runtime, session driver, publisher transport) is faked,
so the record-before-publish ordering under test is the real one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.runtime_bindings import (
    InMemoryStableRuntimeBindingStore,
    RuntimeBindingState,
    stable_binding_id,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult


def _plan():
    digest = compute_model_config_digest(
        qualifiedId="opencode/model",
        effort=None,
        routeRef="opencode-go",
        normalizedOptions={},
    )
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
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
                "qualifiedId": "opencode/model",
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
            "workspaceMutation": "allowed",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
    )


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-terminal-preservation",
        idempotencyKey="idem-terminal-preservation",
        parameters={"publishMode": "branch"},
    )


class _EventLogBindingStore:
    """Proxy the production in-memory store while logging terminal writes."""

    def __init__(self, events: list[str]) -> None:
        self._inner = InMemoryStableRuntimeBindingStore()
        self._events = events

    async def get(self, binding_id: str) -> Any:
        return await self._inner.get(binding_id)

    async def create_initial(self, **kwargs: Any) -> Any:
        return await self._inner.create_initial(**kwargs)

    async def update(self, binding_id: str, **kwargs: Any) -> Any:
        updates = kwargs.get("updates") or {}
        if isinstance(updates, dict) and "terminalResult" in updates:
            self._events.append("terminalResult")
        return await self._inner.update(binding_id, **kwargs)

    async def list_recoverable(self, **kwargs: Any) -> Any:
        return await self._inner.list_recoverable(**kwargs)


def _lease(**overrides: Any) -> SimpleNamespace:
    state = {
        "bindingRef": "omnigent-host-binding:sha256:" + "a" * 64,
        "leaseRef": "omnigent-host-lease:sha256:" + "b" * 64,
        "generation": 1,
        "launchGeneration": 1,
        "status": "ready",
        "cleanupHandle": {"containerName": "mm-host-test", "launchGeneration": 1},
    }
    state.update(overrides)
    return SimpleNamespace(**state)


class _Harness:
    """Fake the process edge the terminal-preservation boundary never touches."""

    def __init__(self, events: list[str], publisher: Any) -> None:
        self._events = events
        self._publisher = publisher

    async def acquire_all(self, **_kwargs: Any) -> tuple[()]:
        return ()

    async def release_all(self, _acquired: Any) -> None:
        return None

    async def materialize_all(self, **_kwargs: Any) -> tuple[()]:
        return ()

    async def load_cleanup_handles(self, *_args: Any) -> tuple[()]:
        return ()

    async def cleanup_all(self, _handles: Any) -> list[Any]:
        return []

    async def acquire(self, **_kwargs: Any) -> SimpleNamespace:
        return _lease()

    async def get(self, _ref: Any) -> None:
        return None

    async def mark_ready(self, lease_ref: str, **kwargs: Any) -> SimpleNamespace:
        return _lease(leaseRef=lease_ref, status="ready")

    async def record_launch(self, lease_ref: str, **kwargs: Any) -> SimpleNamespace:
        return _lease(leaseRef=lease_ref, status="ready")

    async def claim_cleanup(
        self, lease_ref: str, expected_generation: int
    ) -> SimpleNamespace:
        return _lease(leaseRef=lease_ref, generation=expected_generation)

    async def mark_cleaned(
        self, lease_ref: str, expected_generation: int
    ) -> SimpleNamespace:
        return _lease(
            leaseRef=lease_ref, generation=expected_generation, status="cleaned"
        )

    async def heartbeat(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def prepare(self, **_kwargs: Any) -> dict[str, Any]:
        return {"prepared": True}

    async def realize(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "omnigentHostId": "host-test",
            "containerName": "mm-host-test",
            "stateVolumeRef": "volume-test",
            "controlVolumeRef": "control-test",
            "hostHarnessAttestationRef": "attestation-test",
        }

    async def cleanup(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def cleanup_prepared(self, _prepared: Any) -> None:
        return None

    async def cleanup_authorities(self, _refs: Any) -> None:
        return None

    async def drain(self, _session_id: str) -> dict[str, Any]:
        return {}

    async def publish_request_workspace(
        self, **kwargs: Any
    ) -> dict[str, Any]:
        self._events.append("publish")
        return await self._publisher(**kwargs)


def _realizer(
    events: list[str],
    *,
    compute: AgentRunResult,
    publish: Any,
) -> GenericOmnigentHostRealizer:
    edge = _Harness(events, publish)

    async def _driver(request: Any, session_authority_sink: Any = None) -> AgentRunResult:
        return compute

    async def _resolver(plan: Any) -> tuple[Any, Any]:
        return (
            SimpleNamespace(ref="omnigent-opencode@1", imageRef="image-test@sha256:1"),
            SimpleNamespace(
                ref="omnigent-on-demand@1", limits={"timeoutSeconds": 60}
            ),
        )

    return GenericOmnigentHostRealizer(
        runtime_binding_store=_EventLogBindingStore(events),
        provider_lease_coordinator=edge,
        credential_provisioning_service=edge,
        host_lease_repository=edge,
        host_runtime=edge,
        planned_host_resolver=_resolver,
        session_driver=_driver,
        session_cleanup_service=edge,
        workspace_publisher=edge,
        deployment_validator=lambda _payload: None,
    )


async def _successful_publication(**_kwargs: Any) -> dict[str, Any]:
    return {
        "push_status": "pushed",
        "push_branch": "mm/test-branch",
        "push_base_branch": "main",
        "push_head_sha": "f" * 40,
        "push_commit_count": 1,
        "remote_verified": True,
    }


async def _failing_publication(**_kwargs: Any) -> dict[str, Any]:
    raise HarnessPlatformError(
        "generic Omnigent execution produced no publishable repository output",
        code="OMNIGENT_REPOSITORY_OUTPUT_MISSING",
    )


async def _stored_terminal(
    realizer: GenericOmnigentHostRealizer, plan: Any, request: AgentExecutionRequest
) -> dict[str, Any]:
    store = realizer._runtime_bindings._inner
    binding = await store.get(
        stable_binding_id(
            execution_plan_ref=plan.planRef,
            idempotency_key=request.idempotency_key,
        )
    )
    assert binding is not None
    assert binding.terminalResult is not None
    return dict(binding.terminalResult)


@pytest.mark.asyncio
async def test_fresh_lifecycle_records_compute_before_successful_publication() -> None:
    events: list[str] = []
    plan = _plan()
    realizer = _realizer(
        events,
        compute=AgentRunResult(summary="verified compute output"),
        publish=_successful_publication,
    )

    request = _request()
    returned = await realizer._execute_lifecycle(request, plan)

    # The caller still receives the publication-enriched result.
    assert returned.metadata["push_status"] == "pushed"
    assert returned.metadata["acceptedRepositoryEvidence"]["pushStatus"] == "pushed"
    # The durable record precedes publication and stays the compute truth.
    assert events.index("terminalResult") < events.index("publish")
    stored = await _stored_terminal(realizer, plan, request)
    assert stored["summary"] == "verified compute output"
    assert stored["metadata"]["executionPlanRef"] == plan.planRef
    assert "acceptedRepositoryEvidence" not in stored["metadata"]


@pytest.mark.asyncio
async def test_fresh_lifecycle_preserves_compute_when_publication_fails() -> None:
    events: list[str] = []
    plan = _plan()
    realizer = _realizer(
        events,
        compute=AgentRunResult(summary="verified compute output"),
        publish=_failing_publication,
    )

    request = _request()
    with pytest.raises(HarnessPlatformError) as exc:
        await realizer._execute_lifecycle(request, plan)
    assert exc.value.code == "OMNIGENT_REPOSITORY_OUTPUT_MISSING"

    # Failed publication surfaces, but the verified compute result survives.
    stored = await _stored_terminal(realizer, plan, request)
    assert stored["summary"] == "verified compute output"
    assert stored.get("failureClass") is None
    assert stored["metadata"]["executionPlanRef"] == plan.planRef


@pytest.mark.asyncio
async def test_resumed_host_preserves_compute_when_publication_fails() -> None:
    events: list[str] = []
    plan = _plan()
    realizer = _realizer(
        events,
        compute=AgentRunResult(summary="verified resumed compute"),
        publish=_failing_publication,
    )
    store = realizer._runtime_bindings._inner
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key="idem-terminal-preservation",
        provider_leases={},
    )
    for state in (
        RuntimeBindingState.credentials_materialized,
        RuntimeBindingState.host_allocating,
        RuntimeBindingState.host_ready,
    ):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )

    request = _request()
    with pytest.raises(HarnessPlatformError) as exc:
        await realizer._resume_attested_host(
            request=request,
            plan=plan,
            binding=binding,
            host_lease=_lease(),
            host_context={
                "omnigentHostId": "host-test",
                "containerName": "mm-host-test",
            },
            credential_handles=(),
            acquired=(),
        )
    assert exc.value.code == "OMNIGENT_REPOSITORY_OUTPUT_MISSING"

    stored = await _stored_terminal(realizer, plan, request)
    assert stored["summary"] == "verified resumed compute"
    assert stored.get("failureClass") is None


@pytest.mark.asyncio
async def test_resumed_host_never_publishes_failed_compute() -> None:
    events: list[str] = []
    plan = _plan()
    realizer = _realizer(
        events,
        compute=AgentRunResult(
            summary="failed compute stays failed",
            failure_class="execution_error",
        ),
        publish=_successful_publication,
    )
    store = realizer._runtime_bindings._inner
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key="idem-terminal-preservation",
        provider_leases={},
    )
    for state in (
        RuntimeBindingState.credentials_materialized,
        RuntimeBindingState.host_allocating,
        RuntimeBindingState.host_ready,
    ):
        binding = await store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
        )

    request = _request()
    returned = await realizer._resume_attested_host(
        request=request,
        plan=plan,
        binding=binding,
        host_lease=_lease(),
        host_context={
            "omnigentHostId": "host-test",
            "containerName": "mm-host-test",
        },
        credential_handles=(),
        acquired=(),
    )

    # Saving does not upgrade failed compute: no publication, same failure.
    assert "publish" not in events
    assert returned.failure_class == "execution_error"
    stored = await _stored_terminal(realizer, plan, request)
    assert stored["failureClass"] == "execution_error"
