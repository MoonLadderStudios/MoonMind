"""Phase 8: Prove architecture with a second harness (Pi).

Acceptance: adding pi-native requires only:
  catalog trust, Host Class or runtime pack, materializer descriptor,
  Agent Profile, support evidence
and NO change to:
  Temporal activity dispatch, generic host realizer, generic session driver

This test proves that planner, realizer registry, and host runtime are
harness-neutral – they handle Pi via data, not branches.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.host_classes import (
    OmnigentHostClassSelector,
    get_launch_policy,
)
from moonmind.omnigent.harness_platform.materializers import get_materializer
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.realizers.registry import OmnigentExecutionRealizerRegistry


def _make_pi_impl():
    return HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "c" * 64,
            "pluginEntryPoint": None,
        }
    )


def _make_catalog(harness_id: str, digest: str):
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": harness_id,
                "aliases": [],
                "label": harness_id,
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": digest,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "omnigent-provider-config",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )


def test_pi_harness_via_generic_host_no_realizer_change():
    """Pi through omnigent-provider-config uses generic-omnigent-host@1 without code change."""
    harness_id = "pi-native"
    impl = _make_pi_impl()
    catalog = _make_catalog(harness_id, "sha256:" + "c" * 64)
    trust = classify_harness_trust(
        harnessId=harness_id, implementation=impl, trustState=TrustState.core_trusted
    )

    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "pi-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": harness_id,
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": ["interrupt"]},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["omnigent-provider-config"],
                    "acceptedProviderIds": ["pi"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
        }
    )
    skills = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test-pi",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    bs = create_binding_set(
        bindingSetId="pi-primary",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "pi-profile",
                "materializerRef": "omnigent-provider-config@1",
            }
        },
    )

    # Host Class: dedicated pi host (not placeholder standard) – requires real digest
    os.environ["OMNIGENT_PI_HOST_IMAGE_REF"] = (
        "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:" + "b" * 64
    )
    harness = catalog.harnesses[0]
    hc = OmnigentHostClassSelector(
        environment={
            "OMNIGENT_PI_HOST_IMAGE_REF": "ghcr.io/moonladderstudios/omnigent-host-pi@sha256:"
            + "b" * 64
        }
    ).select(
        harness=harness,
        omnigent_version=catalog.omnigentVersion,
        omnigent_build_digest=catalog.omnigentBuildDigest,
        integration_mode="native-server",
        materializer_refs=["omnigent-provider-config@1"],
    )
    assert hc.declares_harness(harness_id, impl.implementation_ref())
    assert hc.supports_materializer("omnigent-provider-config@1")

    policy = get_launch_policy("omnigent-on-demand@1")
    # Compile via same planner path as opencode – no harness branch
    envelope = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref=hc.ref,
        host_class=hc,
        launch_policy_ref=policy.ref,
        model_qualified_id="pi/model",
        model_effort=None,
        model_route_ref="pi",
        model_normalized_options={},
    )
    # Must select generic host realizer, not codex
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    assert envelope.payload.harnessId == harness_id

    # Verify realizer registry dispatches without harness branch
    class ReadyGenericRealizer:
        ref = "generic-omnigent-host@1"

        async def execute(self, request, plan):
            raise AssertionError("dispatch-only test")

        async def reconcile(self, plan_ref, runtime_binding_ref):
            return None

    registry = OmnigentExecutionRealizerRegistry()
    registry.register(ReadyGenericRealizer())
    realizer = registry.require(envelope.payload.executionRealizerRef)
    assert realizer.ref == "generic-omnigent-host@1"
    # The realizer's execute must be harness-neutral (no if harness == "pi")
    import inspect

    from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer

    source = inspect.getsource(GenericOmnigentHostRealizer.execute)
    # Must not contain per-harness branches for pi
    assert 'if harness == "pi' not in source
    assert 'elif harness == "opencode' not in source
    assert 'elif harness == "qwen' not in source


def test_host_owned_auth_materializer_for_static_connected():
    """Connected host with host-owned-auth uses static-connected + no secret."""
    mat = get_materializer("host-owned-auth@1")
    assert mat.supports_host_mode("static-connected")
    assert (
        not mat.supports_host_mode("on-demand")
        or mat.supports_host_mode("on-demand") is False
    )
    # host-owned-auth requires no secret roles
    assert mat.requiredSecretRoles == ()
    assert mat.target["kind"] == "host-owned-auth"

    # Validate that a static host can use it with pi-native
    impl_ref = _make_pi_impl().implementation_ref()
    from moonmind.omnigent.harness_platform.materializers import (
        validate_binding_materializer,
    )

    validated = validate_binding_materializer(
        materializer_ref="host-owned-auth@1",
        harness_implementation_ref=impl_ref,
        harness_id="pi-native",
        host_mode="static-connected",
    )
    assert validated.materializerId == "host-owned-auth"

    # on-demand must fail for host-owned-auth
    with pytest.raises(Exception):
        validate_binding_materializer(
            materializer_ref="host-owned-auth@1",
            harness_implementation_ref=impl_ref,
            harness_id="pi-native",
            host_mode="on-demand",
        )


def test_second_harness_does_not_require_realizer_code_change():
    """Prove that adding Pi required only data, not code branches."""
    # Simulate adding a new harness without modifying realizer code:
    # Catalog + HostClass + materializer + Agent Profile are data.
    # The generic realizer and planner handle it via the same code path
    # as opencode-native.

    # Before: registry has generic-omnigent-host@1
    from moonmind.omnigent.realizers.codex_profile_bound import (
        CodexProfileBoundRealizer,
    )

    class ReadyGenericRealizer:
        ref = "generic-omnigent-host@1"

        async def execute(self, request, plan):
            raise AssertionError("dispatch-only test")

        async def reconcile(self, plan_ref, runtime_binding_ref):
            return None

    registry = OmnigentExecutionRealizerRegistry()
    registry.register(CodexProfileBoundRealizer())
    registry.register(ReadyGenericRealizer())
    assert "generic-omnigent-host@1" in registry.list_refs()
    assert "codex-profile-bound@1" in registry.list_refs()

    # Adding pi does not add a new realizer method
    # The count of realizers remains 2
    assert len(registry.list_refs()) == 2

    # Planner without harness branches: check source for harness-specific if
    import inspect

    from moonmind.omnigent.harness_platform import planner as planner_module

    planner_source = inspect.getsource(planner_module.compile_execution_plan)
    assert 'harness == "pi' not in planner_source
    assert 'harness == "opencode' not in planner_source

    from moonmind.omnigent.realizers import generic_host as gh_module

    realizer_source = inspect.getsource(gh_module.GenericOmnigentHostRealizer.execute)
    assert 'harness == "pi' not in realizer_source
    assert 'harness == "opencode' not in realizer_source

    # Host runtime also harness-neutral
    from moonmind.omnigent import host_runtime as hr_module

    hr_source = inspect.getsource(hr_module.GenericOmnigentHostRuntime.realize)
    assert 'harness == "pi' not in hr_source


def test_pi_and_opencode_share_same_generic_session_driver():
    """Both harnesses use same OmnigentHttpClient session driver."""
    from pathlib import Path

    client_path = Path("moonmind/workflows/adapters/omnigent_client.py")
    content = client_path.read_text(encoding="utf-8")
    # Client has generic operations, not harness-specific methods
    assert "def create_session" in content
    assert "def post_event" in content
    assert "def stream_events" in content
    assert "def list_harnesses" in content
    assert "def get_host" in content
    assert "def get_host_model_options" in content

    # Must not have harness-specific methods
    assert "run_opencode_execution" not in content
    assert "run_qwen_execution" not in content
    assert "run_pi_execution" not in content

    # Verify session-create payload is harness-neutral
    lower = content.lower()
    # No harness-specific branching in generic driver
    assert 'harness == "opencode' not in lower
    assert 'harness == "qwen' not in lower


def test_registering_a_synthetic_harness_needs_no_lifecycle_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a harness is registration data (MoonLadderStudios/MoonMind#3711).

    Registering a synthetic harness must make the canonical session lifecycle
    resolve its execution target, aliases, Host Class, and materializer with no
    edit to the activities, the realizer registry, or the generic host runtime.
    """

    from moonmind.omnigent.harness_platform import harness_registry

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(
        harness_registry, "_ALIASES", dict(harness_registry._ALIASES)
    )

    before = harness_registry.approved_harness_ids()
    assert harness_registry.find_harness_registration("qwen-native") is None

    harness_registry.register_harness_product(
        harness_registry.HarnessProductRegistration.model_validate(
            {
                "harnessId": "qwen-native",
                "aliases": ["qwen"],
                "executionTargetRef": "omnigent-qwen@1",
                "hostClassRef": "omnigent-qwen@1",
                "materializerRef": "omnigent-provider-config@1",
                "authModel": "omnigent-provider-config",
            }
        )
    )

    assert harness_registry.canonical_harness_id("qwen") == "qwen-native"
    assert (
        harness_registry.canonical_harness_id({"id": "qwen-native"}) == "qwen-native"
    )
    assert (
        harness_registry.product_execution_target_ref("qwen-native")
        == "omnigent-qwen@1"
    )
    assert set(harness_registry.approved_harness_ids()) == set(before) | {
        "qwen-native"
    }

    # The canonical session lifecycle resolves the new harness through the same
    # registry call it uses for every other approved harness.
    lifecycle = Path(
        "moonmind/workflows/temporal/activities/omnigent_session_activities.py"
    ).read_text(encoding="utf-8")
    assert "find_harness_registration" in lifecycle
    assert "canonical_harness_id" in lifecycle
    for name in (*before, "qwen-native", "qwen"):
        assert f'"{name}"' not in lifecycle


def test_registering_a_conflicting_harness_alias_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.omnigent.harness_platform import harness_registry
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(
        harness_registry, "_ALIASES", dict(harness_registry._ALIASES)
    )

    with pytest.raises(HarnessPlatformError):
        harness_registry.register_harness_product(
            harness_registry.HarnessProductRegistration.model_validate(
                {
                    "harnessId": "shadow-native",
                    "aliases": ["codex"],
                    "executionTargetRef": "omnigent-shadow@1",
                    "hostClassRef": "omnigent-shadow@1",
                    "materializerRef": "omnigent-provider-config@1",
                    "authModel": "omnigent-provider-config",
                }
            )
        )

    with pytest.raises(HarnessPlatformError):
        harness_registry.harness_registration("unregistered-native")


def test_registering_an_alias_that_shadows_a_canonical_harness_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alias may not capture another harness's canonical id.

    ``canonical_harness_id`` resolves aliases before canonical ids, so accepting
    such an alias would silently reroute the shadowed harness to the new
    harness's product authority.
    """

    from moonmind.omnigent.harness_platform import harness_registry
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(
        harness_registry, "_ALIASES", dict(harness_registry._ALIASES)
    )

    with pytest.raises(HarnessPlatformError):
        harness_registry.register_harness_product(
            harness_registry.HarnessProductRegistration.model_validate(
                {
                    "harnessId": "qwen-native",
                    "aliases": ["codex-native"],
                    "executionTargetRef": "omnigent-qwen@1",
                    "hostClassRef": "omnigent-qwen@1",
                    "materializerRef": "omnigent-provider-config@1",
                    "authModel": "omnigent-provider-config",
                }
            )
        )

    # The rejected registration must not have mutated either registry.
    assert harness_registry.canonical_harness_id("codex-native") == "codex-native"
    assert (
        harness_registry.harness_registration("codex-native").executionTargetRef
        == "omnigent-codex@1"
    )
    assert "qwen-native" not in harness_registry.approved_harness_ids()


def test_registering_a_canonical_id_that_shadows_an_alias_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical id may not reuse an existing alias.

    Alias resolution runs first, so such a registration would never be reachable
    through ``harness_registration`` or ``find_harness_registration``.
    """

    from moonmind.omnigent.harness_platform import harness_registry
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(
        harness_registry, "_ALIASES", dict(harness_registry._ALIASES)
    )

    with pytest.raises(HarnessPlatformError):
        harness_registry.register_harness_product(
            harness_registry.HarnessProductRegistration.model_validate(
                {
                    "harnessId": "codex",
                    "aliases": [],
                    "executionTargetRef": "omnigent-shadow@1",
                    "hostClassRef": "omnigent-shadow@1",
                    "materializerRef": "omnigent-provider-config@1",
                    "authModel": "omnigent-provider-config",
                }
            )
        )

    assert "codex" not in harness_registry.approved_harness_ids()
    assert harness_registry.canonical_harness_id("codex") == "codex-native"


def test_registering_identical_harness_data_stays_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collision validation must not break idempotent re-registration."""

    from moonmind.omnigent.harness_platform import harness_registry

    monkeypatch.setattr(
        harness_registry, "_REGISTRATIONS", dict(harness_registry._REGISTRATIONS)
    )
    monkeypatch.setattr(
        harness_registry, "_ALIASES", dict(harness_registry._ALIASES)
    )

    before = harness_registry.approved_harness_ids()
    harness_registry.register_harness_product(
        harness_registry.harness_registration("codex-native")
    )

    assert harness_registry.approved_harness_ids() == before
    assert harness_registry.canonical_harness_id("codex") == "codex-native"
