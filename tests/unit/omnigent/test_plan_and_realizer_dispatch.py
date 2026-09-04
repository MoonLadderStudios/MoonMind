"""Phase 1: Plan persistence and realizer dispatch."""

import os
from datetime import UTC, datetime

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
    HostClass,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.registry import (
    OmnigentExecutionRealizerRegistry,
    reset_default_registry,
)

_SERVER_IMAGE_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "b" * 64
_OPENCODE_IMAGE_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
)


@pytest.fixture(autouse=True)
def _ready_opencode_image_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setattr(
        store,
        "load_resolved_state",
        lambda: SimpleNamespace(
            server_image_ref=_SERVER_IMAGE_REF,
            opencode_host_image_ref=_OPENCODE_IMAGE_REF,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_IMAGE_REF,
                    "hostImageRef": _OPENCODE_IMAGE_REF,
                }
            },
        ),
    )


def _make_catalog(harness_id="opencode-native", digest="sha256:" + "a" * 64):
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
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )


def _select_opencode_host_class(catalog):
    return OmnigentHostClassSelector(
        environment={
            "OMNIGENT_IMAGE_REF": _SERVER_IMAGE_REF,
            "OMNIGENT_OPENCODE_HOST_IMAGE_REF": _OPENCODE_IMAGE_REF,
        }
    ).select(
        harness=catalog.harnesses[0],
        omnigent_version=catalog.omnigentVersion,
        omnigent_build_digest=catalog.omnigentBuildDigest,
        integration_mode="native-server",
        materializer_refs=["opencode-auth-json@1"],
    )


def _test_codex_host_class(
    implementation_ref: str,
    host_class_id: str = "omnigent-codex-current",
    harness_id: str = "codex-native",
    materializer_refs: tuple[str, ...] = ("codex-oauth-home@1",),
):
    return HostClass.model_validate(
        {
            "hostClassId": host_class_id,
            "version": 1,
            "imageRef": "ghcr.io/example/codex-host@sha256:" + "d" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": harness_id,
                    "implementationRef": implementation_ref,
                    "runtimeDependencies": [],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": list(materializer_refs),
            "features": {
                "readOnlyRoot": True,
                "restrictedEgress": True,
                "workspaceBind": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


def test_plan_persisted_and_retries_load_same_plan():

    async def run():
        catalog = _make_catalog()
        impl = HarnessImplementationIdentity.model_validate(
            {
                "sourceKind": "core",
                "package": "omnigent",
                "version": "1.0.0",
                "digest": "sha256:" + "a" * 64,
                "pluginEntryPoint": None,
            }
        )
        trust = classify_harness_trust(
            harnessId="opencode-native",
            implementation=impl,
            trustState=TrustState.core_trusted,
        )
        profile = OmnigentAgentProfileV2.model_validate(
            {
                "schemaVersion": "moonmind.omnigent-agent-profile.v2",
                "endpointRef": "default",
                "source": {
                    "kind": "upstream",
                    "upstreamId": "opencode-native-ui",
                    "upstreamVersion": "1.0.0",
                    "upstreamSnapshotDigest": "sha256:" + "d" * 64,
                },
                "harness": {
                    "id": "opencode-native",
                    "catalogRef": catalog.catalogRef,
                    "implementationRef": impl.implementation_ref(),
                },
                "requirements": {
                    "harness": {"required": [], "preferred": []},
                    "moonmind": {"required": []},
                    "host": {"required": []},
                },
                "credentialSlots": [
                    {
                        "id": "primary-model",
                        "optional": False,
                        "acceptedAuthModels": ["own-auth"],
                        "acceptedProviderIds": ["opencode"],
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
                "resolvedSkillSetRef": "artifact:test",
                "resolvedSkillSetDigest": "sha256:" + "a" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
            }
        )
        bs = create_binding_set(
            bindingSetId="test",
            version=1,
            bindings={
                "primary-model": {
                    "providerProfileRef": "p1",
                    "materializerRef": "opencode-auth-json@1",
                }
            },
        )
        # Need opencode image env for host class
        os.environ["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] = (
            "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "a" * 64
        )
        store = InMemoryExecutionPlanStore()
        host_class = _select_opencode_host_class(catalog)

        envelope1 = compile_execution_plan(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bs,
            host_class_ref="omnigent-opencode@1",
            host_class=host_class,
            launch_policy_ref="omnigent-on-demand@1",
            model_qualified_id="opencode/model",
            model_effort=None,
            model_route_ref="opencode-go",
            model_normalized_options={},
        )
        persisted1 = await store.persist(envelope1)
        # Retry compiles same plan – should load same ref via store (no duplicate persist)
        persisted2 = await store.load_or_compile(
            compile_fn=compile_execution_plan,
            compile_kwargs=dict(
                agent_profile=profile,
                harness_catalog=catalog,
                trust_record=trust,
                resolved_skills=skills,
                credential_binding_set=bs,
                host_class_ref="omnigent-opencode@1",
                host_class=host_class,
                launch_policy_ref="omnigent-on-demand@1",
                model_qualified_id="opencode/model",
                model_effort=None,
                model_route_ref="opencode-go",
                model_normalized_options={},
            ),
        )
        assert persisted1.planRef == persisted2.planRef
        assert persisted1 == persisted2
        # Load by ref
        loaded = await store.load(persisted1.planRef)
        assert loaded == persisted1

    import asyncio as _asyncio

    _asyncio.run(run())
    os.environ.pop("OMNIGENT_OPENCODE_HOST_IMAGE_REF", None)


def test_workflow_cannot_author_realizer():
    catalog = _make_catalog(harness_id="codex-native", digest="sha256:" + "e" * 64)
    impl = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "e" * 64,
            "pluginEntryPoint": None,
        }
    )
    trust = classify_harness_trust(
        harnessId="codex-native",
        implementation=impl,
        trustState=TrustState.core_trusted,
    )
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "codex-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "codex-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["oauth_volume"],
                    "acceptedProviderIds": ["openai"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
        }
    )
    skills = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    bs = create_binding_set(
        bindingSetId="codex",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "codex-oauth-home@1",
            }
        },
    )
    host_class = _test_codex_host_class(impl.implementation_ref())
    # Workflow tries to author generic realizer for codex – must fail closed (trusted planner only)
    # Trusted for codex-native is codex-profile-bound@1, workflow cannot force generic
    with pytest.raises(Exception) as exc:
        compile_execution_plan(
            agent_profile=profile,
            harness_catalog=catalog,
            trust_record=trust,
            resolved_skills=skills,
            credential_binding_set=bs,
            host_class_ref="omnigent-codex-current@1",
            host_class=host_class,
            launch_policy_ref="codex-on-demand@1",
            model_qualified_id="gpt-5",
            model_effort=None,
            model_route_ref="openai",
            model_normalized_options={},
            execution_realizer_ref="generic-omnigent-host@1",  # workflow-authored
        )
    assert "realizer" in str(exc.value).lower()

    # Without workflow authoring, planner selects trusted codex-profile-bound@1
    envelope2 = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref="omnigent-codex-current@1",
        host_class=host_class,
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
    )
    assert envelope2.payload.executionRealizerRef == "codex-profile-bound@1"


def test_realizer_registry_no_fallback():
    reset_default_registry()
    registry = OmnigentExecutionRealizerRegistry()
    registry.register(CodexProfileBoundRealizer())

    class ReadyGenericRealizer:
        ref = GenericOmnigentHostRealizer.ref

        async def execute(self, request, plan):
            raise AssertionError("dispatch-only test")

        async def reconcile(self, plan_ref, runtime_binding_ref):
            return None

    registry.register(ReadyGenericRealizer())

    assert registry.require("codex-profile-bound@1").ref == "codex-profile-bound@1"
    assert registry.require("generic-omnigent-host@1").ref == "generic-omnigent-host@1"

    # Must not fallback: generic failure does not select codex
    with pytest.raises(Exception) as exc:
        registry.require("nonexistent@1")
    assert "unavailable" in str(exc.value).lower()

    # Ensure no if harness branches in registry
    import inspect

    source = inspect.getsource(registry.require)
    assert 'harness == "opencode' not in source
    assert 'harness == "codex' not in source

# ---- #3830/#3831: generic Codex / Claude realizer admission gates ----

_GATE_ENV = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
_CLAUDE_GATE_ENV = "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED"
_SHARED_REF = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "e" * 64


def _compile_claude_plan(**kwargs):
    """Compile a claude-native plan with the shared-image Host Class."""
    from moonmind.omnigent.harness_platform.credential_bindings import (
        create_binding_set as _cbs,
    )
    from moonmind.omnigent.harness_platform.planner import (
        compile_execution_plan as _compile,
    )
    from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet as _rss

    catalog = _make_catalog(harness_id="claude-native", digest="sha256:" + "f" * 64)
    impl = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "f" * 64,
            "pluginEntryPoint": None,
        }
    )
    trust = classify_harness_trust(
        harnessId="claude-native",
        implementation=impl,
        trustState=TrustState.core_trusted,
    )
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "claude-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "claude-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["oauth_volume"],
                    "acceptedProviderIds": ["anthropic"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["claude-on-demand@1"],
        }
    )
    skills = _rss.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    bs = _cbs(
        bindingSetId="claude",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "claude-oauth-home@1",
            }
        },
    )
    host_class = _test_codex_host_class(
        impl.implementation_ref(),
        host_class_id="omnigent-claude",
        harness_id="claude-native",
        materializer_refs=("claude-oauth-home@1",),
    )
    return _compile(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref="omnigent-claude@1",
        host_class=host_class,
        launch_policy_ref="claude-on-demand@1",
        model_qualified_id="claude/sonnet",
        model_effort=None,
        model_route_ref="anthropic",
        model_normalized_options={},
        **kwargs,
    )


def _compile_codex_plan(**kwargs):
    """Compile a codex-native plan with the shared-image Host Class."""
    from moonmind.omnigent.harness_platform.credential_bindings import (
        create_binding_set as _cbs,
    )
    from moonmind.omnigent.harness_platform.planner import (
        compile_execution_plan as _compile,
    )
    from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet as _rss

    catalog = _make_catalog(harness_id="codex-native", digest="sha256:" + "e" * 64)
    impl = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": "sha256:" + "e" * 64,
            "pluginEntryPoint": None,
        }
    )
    trust = classify_harness_trust(
        harnessId="codex-native",
        implementation=impl,
        trustState=TrustState.core_trusted,
    )
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "codex-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "codex-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["oauth_volume"],
                    "acceptedProviderIds": ["openai"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
        }
    )
    skills = _rss.model_validate(
        {
            "resolvedSkillSetRef": "artifact:test",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
    )
    bs = _cbs(
        bindingSetId="codex",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "codex-oauth-home@1",
            }
        },
    )
    host_class = _test_codex_host_class(
        impl.implementation_ref(), host_class_id="omnigent-codex"
    )
    return _compile(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=bs,
        host_class_ref="omnigent-codex@1",
        host_class=host_class,
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
        **kwargs,
    )


def test_select_execution_realizer_codex_gate():
    from moonmind.omnigent.harness_platform.planner import (
        select_execution_realizer,
    )

    # Unqualified (default): codex keeps the retained profile-bound realizer.
    assert (
        select_execution_realizer(harness_id="codex-native", is_codex=True)
        == "codex-profile-bound@1"
    )
    # Claude Code (no legacy lane) owns the generic realizer directly.
    assert (
        select_execution_realizer(harness_id="claude-native", is_codex=False)
        == "generic-omnigent-host@1"
    )
    assert (
        select_execution_realizer(harness_id="opencode-native", is_codex=False)
        == "generic-omnigent-host@1"
    )


def test_codex_generic_plan_fails_closed_until_qualified(monkeypatch):
    from moonmind.omnigent.harness_platform.planner import (
        select_execution_realizer,
    )
    from moonmind.omnigent.settings import generic_codex_qualified

    monkeypatch.delenv(_GATE_ENV, raising=False)
    assert generic_codex_qualified() is False
    # Default trusted selection stays on the retained profile-bound realizer.
    assert (
        select_execution_realizer(harness_id="codex-native", is_codex=True)
        == "codex-profile-bound@1"
    )
    # Explicit generic selection fails closed — never a silent fallback.
    with pytest.raises(Exception) as exc:
        _compile_codex_plan(execution_realizer_ref="generic-omnigent-host@1")
    assert "execution realizer" in str(exc.value).lower()


def test_codex_generic_plan_when_qualified(monkeypatch):
    from moonmind.omnigent.harness_platform.planner import (
        select_execution_realizer,
    )

    monkeypatch.setenv(_GATE_ENV, "1")
    assert select_execution_realizer(harness_id="codex-native", is_codex=True) == (
        "generic-omnigent-host@1"
    )
    envelope = _compile_codex_plan()
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    # Explicit profile-bound remains selectable while the realizer exists.
    envelope2 = _compile_codex_plan(execution_realizer_ref="codex-profile-bound@1")
    assert envelope2.payload.executionRealizerRef == "codex-profile-bound@1"


def test_generic_codex_gate_env_is_explicit_only(monkeypatch):
    from moonmind.omnigent.settings import generic_codex_qualified

    monkeypatch.delenv(_GATE_ENV, raising=False)
    assert generic_codex_qualified() is False
    monkeypatch.setenv(_GATE_ENV, "1")
    assert generic_codex_qualified() is True
    monkeypatch.setenv(_GATE_ENV, "true")
    assert generic_codex_qualified() is True
    monkeypatch.setenv(_GATE_ENV, "0")
    assert generic_codex_qualified() is False


def test_claude_plans_record_generic_realizer(monkeypatch):
    monkeypatch.setenv(_CLAUDE_GATE_ENV, "1")
    envelope = _compile_claude_plan()
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"
    assert envelope.payload.harnessId == "claude-native"
    assert envelope.payload.hostClassRef == "omnigent-claude@1"
