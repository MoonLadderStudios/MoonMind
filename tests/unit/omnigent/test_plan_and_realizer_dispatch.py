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


def test_select_execution_realizer_codex_gate(monkeypatch):
    from moonmind.omnigent.harness_platform.planner import (
        select_execution_realizer,
    )

    # Unqualified (default): codex keeps the retained profile-bound realizer.
    monkeypatch.delenv(_CLAUDE_GATE_ENV, raising=False)
    assert (
        select_execution_realizer(harness_id="codex-native", is_codex=True)
        == "codex-profile-bound@1"
    )
    # Claude Code is fail-closed until qualified: no legacy lane to fall back to.
    with pytest.raises(Exception) as exc:
        select_execution_realizer(harness_id="claude-native", is_codex=False)
    assert "execution realizer" in str(exc.value).lower()
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


def test_claude_generic_plan_fails_closed_until_qualified(monkeypatch):
    from moonmind.omnigent.settings import generic_claude_qualified

    monkeypatch.delenv(_CLAUDE_GATE_ENV, raising=False)
    assert generic_claude_qualified() is False
    with pytest.raises(Exception) as exc:
        _compile_claude_plan()
    assert "execution realizer" in str(exc.value).lower()
    with pytest.raises(Exception) as exc2:
        _compile_claude_plan(execution_realizer_ref="generic-omnigent-host@1")
    assert "execution realizer" in str(exc2.value).lower()

# ---- #3833: rollout decision frozen into immutable execution authority ----


_EVIDENCE_HOST_IMAGE_REF = (
    "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "f" * 64
)


def _deployment_evidence_payload(support_identity):
    """Build one real signed deployment qualification for a compiled plan."""

    from moonmind.omnigent.bootstrap.evidence import build_deployment_evidence
    from moonmind.omnigent.harness_platform.support import (
        compute_support_combination_key,
    )

    return build_deployment_evidence(
        support_identity=support_identity,
        support_combination_key=compute_support_combination_key(support_identity),
        host_image_ref=_EVIDENCE_HOST_IMAGE_REF,
        policy_snapshot_digest="sha256:" + "1" * 64,
        effective_launch_snapshot_digest="sha256:" + "2" * 64,
        provider_profile_ref="p1",
        credential_generation=1,
        qualified_model_id="gpt-5",
        effort="medium",
        results={"readQualification": "passed"},
        evidence_refs={"readRun": "artifact:read-run"},
        resolved_state=None,
    )


def _publish_deployment_evidence(
    tmp_path, monkeypatch, support_identity, *, age_days=0
):
    """Publish deployment evidence for an exact combination and select it.

    ``age_days`` back-dates the document so the planner boundary can be
    exercised against evidence that has lapsed, which is the case an operator
    actually hits when a qualification is not refreshed.
    """

    import json
    from datetime import timedelta

    from moonmind.omnigent.bootstrap.evidence import write_deployment_evidence
    from moonmind.omnigent.deployment_evidence import sign_deployment_evidence

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    destination = tmp_path / "deployment-execution-evidence.json"
    monkeypatch.setenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", str(destination))
    evidence = _deployment_evidence_payload(support_identity)
    if not age_days:
        write_deployment_evidence(evidence, path=destination)
        return destination
    shifted = {
        key: value for key, value in evidence.items() if key != "signature"
    }
    generated_at = datetime.fromisoformat(shifted["generatedAt"]) - timedelta(
        days=age_days
    )
    shifted["generatedAt"] = generated_at.isoformat()
    shifted["expiresAt"] = (generated_at + timedelta(days=1)).isoformat()
    destination.write_text(
        json.dumps({"entries": [sign_deployment_evidence(shifted)]}),
        encoding="utf-8",
    )
    return destination


def _select_missing_deployment_evidence(tmp_path, monkeypatch):
    """Point admission at a deployment that published no qualification."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "deployment")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE",
        str(tmp_path / "no-deployment-execution-evidence.json"),
    )


def test_plan_freezes_the_runtime_provider_rollout_decision(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.runtime_provider_rollout import (
        RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION,
    )

    monkeypatch.setenv(_GATE_ENV, "1")
    _select_missing_deployment_evidence(tmp_path, monkeypatch)
    _publish_deployment_evidence(
        tmp_path, monkeypatch, _compile_codex_plan().payload.supportIdentity
    )
    payload = _compile_codex_plan().payload
    record = payload.runtimeProviderRollout
    assert record is not None
    assert record.policyVersion == RUNTIME_PROVIDER_ROLLOUT_POLICY_VERSION
    assert record.targetId == "codex.generic-omnigent"
    assert record.pathClass == "generic_omnigent"
    assert record.state == "new_work_default"
    assert record.ruleGeneration >= 1
    assert record.combinationKey.startswith(
        "omnigent-runtime-provider-combination:sha256:"
    )
    # The realizer the plan records and the rollout row it was admitted under
    # agree, so audit evidence shows one truthful selected path.
    assert payload.executionRealizerRef == "generic-omnigent-host@1"


def test_plan_records_the_legacy_row_before_codex_promotion(monkeypatch):
    monkeypatch.delenv(_GATE_ENV, raising=False)
    payload = _compile_codex_plan().payload
    record = payload.runtimeProviderRollout
    assert record is not None
    assert record.targetId == "codex.legacy-profile-bound-omnigent"
    assert record.pathClass == "legacy_profile_bound_omnigent"
    assert payload.executionRealizerRef == "codex-profile-bound@1"


def test_frozen_rollout_record_is_not_reinterpreted_by_a_later_policy(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.harness_platform.execution_plan import compute_plan_ref

    monkeypatch.setenv(_GATE_ENV, "1")
    _select_missing_deployment_evidence(tmp_path, monkeypatch)
    _publish_deployment_evidence(
        tmp_path, monkeypatch, _compile_codex_plan().payload.supportIdentity
    )
    admitted = _compile_codex_plan()
    admitted_ref = compute_plan_ref(admitted.payload)
    # Rolling the deployment back cannot change the admitted plan's identity or
    # its recorded rollout authority.
    monkeypatch.delenv(_GATE_ENV, raising=False)
    assert compute_plan_ref(admitted.payload) == admitted_ref
    assert admitted.payload.runtimeProviderRollout.state == "new_work_default"


@pytest.mark.parametrize(
    "age_days,expected_reason",
    [(None, "support_evidence_missing"), (60, "support_evidence_stale")],
)
def test_support_evidence_gaps_demote_a_promoted_row_at_the_planner_boundary(
    tmp_path, monkeypatch, age_days, expected_reason
):
    """A promoted row is frozen as promoted only while evidence backs it."""

    from moonmind.omnigent.control_plane import metrics as control_plane_metrics

    monkeypatch.setenv(_GATE_ENV, "1")
    _select_missing_deployment_evidence(tmp_path, monkeypatch)
    if age_days is not None:
        _publish_deployment_evidence(
            tmp_path,
            monkeypatch,
            _compile_codex_plan().payload.supportIdentity,
            age_days=age_days,
        )

    control_plane_metrics.reset()
    record = _compile_codex_plan().payload.runtimeProviderRollout

    assert record is not None
    # The promoted row is still the matched target; only its state is demoted.
    assert record.targetId == "codex.generic-omnigent"
    assert record.state == "explicit_only"
    assert record.reasonCode == expected_reason
    # The same decision reports the denial through the migration telemetry the
    # operator status view reads.
    assert [
        (labels, count)
        for name, labels, count in control_plane_metrics.counter_series()
        if name == control_plane_metrics.MIGRATION_SUPPORT_EVIDENCE_DENIAL
    ] == [({"harness_class": "codex", "denial_reason": expected_reason}, 1)]


def test_qualified_support_evidence_keeps_a_promoted_row_promoted(
    tmp_path, monkeypatch
):
    """The same boundary promotes when current evidence backs the row."""

    monkeypatch.setenv(_GATE_ENV, "1")
    _select_missing_deployment_evidence(tmp_path, monkeypatch)
    demoted = _compile_codex_plan().payload
    assert demoted.runtimeProviderRollout.state == "explicit_only"

    _publish_deployment_evidence(tmp_path, monkeypatch, demoted.supportIdentity)
    promoted = _compile_codex_plan().payload.runtimeProviderRollout
    assert promoted.state == "new_work_default"
    assert promoted.reasonCode == "rollout_new_work_default"


def test_plan_payload_without_a_rollout_record_stays_replayable():
    """Plans admitted before #3833 keep their canonical bytes and digest."""

    from moonmind.omnigent.harness_platform.execution_plan import (
        OmnigentExecutionPlanPayload,
        canonical_payload_bytes,
        compute_plan_ref,
    )

    payload = _compile_codex_plan().payload
    historical = payload.model_dump(by_alias=True, mode="json")
    historical.pop("runtimeProviderRollout", None)
    replayed = OmnigentExecutionPlanPayload.model_validate(historical)
    assert replayed.runtimeProviderRollout is None
    assert compute_plan_ref(replayed) == compute_plan_ref(historical)
    assert b"runtimeProviderRollout" not in canonical_payload_bytes(replayed)


def test_rollback_control_blocks_new_generic_codex_admission(monkeypatch):
    from moonmind.omnigent.runtime_provider_rollout import (
        RUNTIME_PROVIDER_ROLLBACK_ENV,
    )

    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(
        RUNTIME_PROVIDER_ROLLBACK_ENV, "stop_new_generic_codex_admission"
    )
    # Stopping generic Codex admission does not silently restore the legacy
    # realizer: that is a separate, explicit control. New admission fails
    # closed instead of substituting a path the operator did not restore.
    with pytest.raises(Exception) as exc:
        _compile_codex_plan()
    assert "realizer" in str(exc.value).lower()
    with pytest.raises(Exception) as explicit_exc:
        _compile_codex_plan(execution_realizer_ref="generic-omnigent-host@1")
    assert "execution realizer" in str(explicit_exc.value).lower()

    # Restoring the explicitly supported legacy default re-admits it, and the
    # plan records that truthful path.
    monkeypatch.setenv(
        RUNTIME_PROVIDER_ROLLBACK_ENV,
        "stop_new_generic_codex_admission,restore_legacy_or_direct_default",
    )
    payload = _compile_codex_plan().payload
    assert payload.executionRealizerRef == "codex-profile-bound@1"
    assert (
        payload.runtimeProviderRollout.targetId
        == "codex.legacy-profile-bound-omnigent"
    )


def test_stop_all_new_omnigent_work_never_substitutes_another_runtime(monkeypatch):
    from moonmind.omnigent.runtime_provider_rollout import (
        RUNTIME_PROVIDER_ROLLBACK_ENV,
    )

    monkeypatch.setenv(_GATE_ENV, "1")
    monkeypatch.setenv(RUNTIME_PROVIDER_ROLLBACK_ENV, "stop_all_new_omnigent_work")
    with pytest.raises(Exception) as exc:
        _compile_codex_plan()
    message = str(exc.value).lower()
    assert "realizer" in message
    # The denial names no replacement runtime.
    assert "codex_cli" not in message
    assert "claude_code" not in message
