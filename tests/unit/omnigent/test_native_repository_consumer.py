"""Native-only repository grants stay outside agent execution authority."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.omnigent.harness_platform import credential_bindings as cb
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError


def _repository(**extra):
    return {
        "authorityKind": "repository_connection",
        "connectionRef": "selected-repository",
        "repositoryAccessSnapshotRef": "repository-access-snapshot:sha256:" + "c" * 64,
        "materializerRef": "repository-broker@1",
        "repositoryRole": "collaboration",
        **extra,
    }


def _bindings(**extra):
    return cb.create_binding_set(
        bindingSetId="native-review",
        version=1,
        schema_version=cb.SCHEMA_V2,
        bindings={"collaboration": _repository(**extra)},
    )


def test_consumer_default_preserves_historical_v2_bytes_and_digest():
    legacy = _bindings()
    explicit = _bindings(consumer="agent")
    expected = {
        "schemaDomain": cb.SCHEMA_DOMAIN,
        "schemaVersion": cb.SCHEMA_V2,
        "bindingSetId": "native-review",
        "version": 1,
        "bindings": {"collaboration": _repository()},
    }
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert legacy.digest == explicit.digest == digest
    assert explicit.model_dump(mode="json")["bindings"] == expected["bindings"]
    native = _bindings(consumer="native")
    assert native.digest != digest
    assert (
        native.model_dump(mode="json")["bindings"]["collaboration"]["consumer"]
        == "native"
    )


def test_native_consumer_requires_trusted_declaration():
    native = _bindings(consumer="native")
    options = dict(binding_set=native, required_slots=[], declared_slots=[])
    with pytest.raises(HarnessPlatformError, match="consumer"):
        cb.validate_binding_set_for_plan(
            **options, declared_repository_slots={"collaboration": {}}
        )
    declarations = cb.derive_repository_slot_requirements(
        trusted_repository_declarations={
            "collaboration": {"allowedConsumers": ("native",)}
        }
    )
    assert declarations["collaboration"]["allowedConsumers"] == ("native",)
    cb.validate_binding_set_for_plan(**options, declared_repository_slots=declarations)
    with pytest.raises(HarnessPlatformError, match="consumer"):
        cb.derive_repository_slot_requirements(
            trusted_repository_declarations={
                "collaboration": {"allowedConsumers": ("any",)}
            }
        )


def test_native_consumer_does_not_require_repository_capable_agent():
    native = _bindings(consumer="native")
    assert cb.required_worker_authority_kinds(native) == ("model",)
    cb.assert_worker_supports_binding_set(("model",), native)
    agent = _bindings()
    assert cb.required_worker_authority_kinds(agent) == ("model", "repository")
    with pytest.raises(HarnessPlatformError):
        cb.assert_worker_supports_binding_set(("model",), agent)


@pytest.mark.parametrize("role", ["source_read", "destination_write"])
def test_native_binding_is_only_collaboration_authority(role):
    with pytest.raises(HarnessPlatformError, match="native.*collaboration"):
        _bindings(consumer="native", repositoryRole=role)


def test_child_attenuation_preserves_native_consumer():
    parent = _bindings(consumer="native").bindings["collaboration"]
    child = cb.attenuate_repository_binding_for_child(
        parent_binding=parent,
        child_target_ref="child",
        child_attempt_ref="attempt",
        child_snapshot_ref="repository-access-snapshot:sha256:" + "d" * 64,
    )
    assert child.binding.consumer == "native"


def test_planner_admits_declared_native_binding_without_agent_repository_capability():
    from moonmind.omnigent.harness_platform.execution_plan import (
        verify_execution_plan_envelope,
    )
    from moonmind.omnigent.harness_platform.host_classes import HOST_CLASSES
    from moonmind.omnigent.harness_platform.planner import compile_execution_plan
    from tests.unit.omnigent.test_repository_authority_bindings import (
        V1_MODEL_SLOT,
        _remediation_planner_fixture,
    )

    saved, catalog, profile, skills, trust = _remediation_planner_fixture()
    try:
        bindings = cb.create_binding_set(
            bindingSetId="native-review",
            version=1,
            schema_version=cb.SCHEMA_V2,
            bindings={
                "primary-model": V1_MODEL_SLOT,
                "collaboration": _repository(consumer="native"),
            },
        )
        plan = compile_execution_plan(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bindings,
            repository_slot_requirements={
                "collaboration": {"allowedConsumers": ("native",)}
            },
            worker_authority_kinds=("model",),
            host_class_ref="repo-test-standard@1",
            launch_policy_ref="omnigent-on-demand@1",
            model_qualified_id="opencode/test-model",
            model_effort=None,
            model_route_ref="opencode-go",
            model_normalized_options={},
        )
        restored = verify_execution_plan_envelope(
            plan.model_dump(by_alias=True, mode="json")
        )
        assert restored == plan
        assert restored.payload.credentialBindings["collaboration"].consumer == "native"
        with pytest.raises(HarnessPlatformError, match="native-only.*agent"):
            cb.assert_agent_execution_authority(restored.payload.credentialBindings)
    finally:
        HOST_CLASSES.clear()
        HOST_CLASSES.update(saved)


@pytest.mark.parametrize("realizer_name", ["codex", "generic"])
@pytest.mark.asyncio
async def test_native_plan_cannot_enter_any_agent_realizer(realizer_name):
    from moonmind.omnigent.realizers.codex_profile_bound import (
        CodexProfileBoundRealizer,
    )
    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

    realizer_type = (
        CodexProfileBoundRealizer
        if realizer_name == "codex"
        else GenericOmnigentHostRealizer
    )
    # Reject before touching coordinator, runtime bindings, turn claims, or leases.
    realizer = object.__new__(realizer_type)
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings=_bindings(consumer="native").bindings
        )
    )
    with pytest.raises(HarnessPlatformError, match="native-only.*agent"):
        await realizer.execute(SimpleNamespace(), plan)


def test_native_plan_cannot_enter_session_supervisor():
    from moonmind.workflows.temporal.activities.omnigent_session_activities import (
        _enforce_session_worker_authority_barrier,
    )

    native = _bindings(consumer="native")
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings=native.bindings, credentialBindingSetRef=native.ref
        )
    )
    with pytest.raises(HarnessPlatformError, match="native-only.*agent"):
        _enforce_session_worker_authority_barrier(plan)


@pytest.mark.parametrize(
    "consumer,has_request", [("agent", True), ("agent", False), ("native", True)]
)
@pytest.mark.asyncio
async def test_native_binding_rejects_agent_and_implicit_acquisition(
    consumer, has_request
):
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )

    artifacts = SimpleNamespace(
        read_bytes=AsyncMock(), read_repository_access_snapshot=AsyncMock()
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings=_bindings(consumer="native").bindings,
            resolvedTools={
                "repositoryAccess": {"collaboration": {"artifactRef": "artifact:test"}}
            },
        )
    )
    service = OmnigentGithubCredentialService(None, artifact_gateway=artifacts)
    with pytest.raises(ValueError, match="consumer|native"):
        await service.acquire_repository_use(
            plan=plan,
            request=SimpleNamespace(idempotency_key="agent") if has_request else None,
            role="collaboration",
            operation="read",
            repository="owner/repo",
            execution_owner="owner",
            consumer=consumer,
        )
    artifacts.read_bytes.assert_not_awaited()
    artifacts.read_repository_access_snapshot.assert_not_awaited()


@pytest.mark.parametrize("consumer", ["agent", "native"])
@pytest.mark.asyncio
async def test_native_grant_rejects_broader_snapshot_operations(consumer):
    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
    )

    connection = github_pat_connection("selected-repository", "SELECTED_PAT")
    assignment = github_repository_assignment(connection.id, "owner/repo")
    snapshot = select_repository_authority(
        access_mode=AccessMode.EXPLICIT,
        principal_ref="user-1",
        principal_scope=("system", None),
        identity=assignment.identity,
        role="collaboration",
        requested_operations=("read", "write"),
        policy_revision=connection.policy_revision,
        explicit_connection=connection,
        explicit_assignment=assignment,
    )
    body = json.dumps(
        {
            "selection": snapshot.model_dump(by_alias=True, mode="json"),
            "repositoryIdentity": assignment.identity.model_dump(
                by_alias=True, mode="json"
            ),
        }
    ).encode()
    snapshot_ref = (
        "repository-access-snapshot:sha256:" + hashlib.sha256(body).hexdigest()
    )
    binding = cb.RepositoryAuthorityBinding.model_validate(
        _repository(consumer=consumer, repositoryAccessSnapshotRef=snapshot_ref)
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={"collaboration": binding},
            resolvedTools={
                "repositoryAccess": {
                    "collaboration": {
                        "artifactRef": "artifact:test",
                        "snapshotRef": snapshot_ref,
                    }
                }
            },
        )
    )
    service = OmnigentGithubCredentialService(
        None, artifact_gateway=SimpleNamespace(read_bytes=AsyncMock(return_value=body))
    )
    call = service.admitted_repository_identity(
        plan=plan,
        request=None,
        role="collaboration",
        operation="read",
        repository="owner/repo",
        consumer="native",
    )
    if consumer == "native":
        with pytest.raises(ValueError, match="native.*operations"):
            await call
    else:
        # Native readers can still consume retained agent collaboration grants.
        assert (await call).display_name == "owner/repo"
