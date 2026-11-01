"""New-admission enforcement at the real plan-compilation boundary.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 2).

Plan compilation is the single new-admission boundary for every client: the
trusted planner default, a schedule, and a preset all resolve here. Stores
strip caller-authored realizer fields before compilation. These
tests drive :func:`compile_execution_plan` itself — not a nearby helper — so the
code-owned retirement class is proven to gate real Codex plan admission.

New Codex plans use the enabled shared owner. The retired profile-bound owner
cannot be restored by a stale selector or caller field, while its recorded
plans retain their realizer and digest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.omnigent.harness_platform import planner
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    compute_plan_ref,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
from moonmind.omnigent.legacy_retirement import (
    RetirementClass,
    get_retirement_record,
)

PROFILE_BOUND_REALIZER = "codex-profile-bound@1"
PROFILE_BOUND_PATH_ID = "omnigent.legacy.profile_bound_realizer"
_IMPL_DIGEST = "sha256:" + "e" * 64


def _catalog():
    return create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "codex-native",
                "aliases": [],
                "label": "codex-native",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": _IMPL_DIGEST,
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


def _host_class(implementation_ref: str) -> HostClass:
    return HostClass.model_validate(
        {
            "hostClassId": "omnigent-codex-current",
            "version": 1,
            "imageRef": "ghcr.io/example/codex-host@sha256:" + "d" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "codex-native",
                    "implementationRef": implementation_ref,
                    "runtimeDependencies": [],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["codex-oauth-home@1"],
            "features": {
                "readOnlyRoot": True,
                "restrictedEgress": True,
                "workspaceBind": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )


@pytest.fixture()
def codex_plan_inputs(monkeypatch) -> dict[str, object]:
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "true")
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    catalog = _catalog()
    impl = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": _IMPL_DIGEST,
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
    binding_set = create_binding_set(
        bindingSetId="codex",
        version=1,
        bindings={
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "codex-oauth-home@1",
            }
        },
    )
    return dict(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=skills,
        credential_binding_set=binding_set,
        host_class_ref="omnigent-codex-current@1",
        host_class=_host_class(impl.implementation_ref()),
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
    )


def test_retired_profile_bound_admission_preserves_new_shared_plans(
    codex_plan_inputs,
) -> None:
    assert (
        get_retirement_record(PROFILE_BOUND_PATH_ID).retirement_class
        is RetirementClass.NEW_ADMISSION_DISABLED
    )
    envelope = compile_execution_plan(**codex_plan_inputs)
    assert envelope.payload.executionRealizerRef == "generic-omnigent-host@1"


def test_retirement_guard_rejects_a_stale_trusted_planner_default(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate the retirement guard from the earlier shared-only selection guard.
    monkeypatch.setattr(
        planner, "select_execution_realizer", lambda **_kwargs: PROFILE_BOUND_REALIZER
    )
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(**codex_plan_inputs)
    assert (
        excinfo.value.code
        == HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE.value
    )
    assert "no longer admits new work" in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["execution_realizer_ref", "executionRealizerRef"])
@pytest.mark.parametrize(
    "realizer", ["generic-omnigent-host@1", PROFILE_BOUND_REALIZER]
)
async def test_caller_realizer_cannot_bypass_the_retirement_state(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch, field: str, realizer: str
) -> None:
    """A caller field cannot bypass a disabled shared owner through retirement."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "false")
    store = InMemoryExecutionPlanStore()
    codex_plan_inputs[field] = realizer
    with pytest.raises(HarnessPlatformError) as excinfo:
        await store.load_or_compile(
            compile_fn=compile_execution_plan,
            compile_kwargs=codex_plan_inputs,
        )
    assert (
        excinfo.value.code
        == HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE.value
    )
    assert "shared Omnigent path is disabled" in str(excinfo.value)
    assert store._plans == {}


def test_plan_compilation_takes_no_rollback_generation(codex_plan_inputs) -> None:
    """Plan admission follows the code-owned class alone; no generation re-admits."""

    with pytest.raises(TypeError):
        compile_execution_plan(**codex_plan_inputs, rollback_generation="gen-1")


@pytest.mark.asyncio
async def test_disabled_admission_does_not_change_recorded_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-compiled plan keeps its recorded realizer and digest."""

    fixture = json.loads(
        (
            Path(__file__).parents[2]
            / "fixtures/omnigent/retained_profile_bound_plans/codex-on-demand-1.json"
        ).read_text()
    )
    binding = fixture["request"]["omnigentExecutionPlan"]
    recorded = OmnigentExecutionPlanEnvelope.model_validate_json(
        fixture["artifacts"][binding["planArtifactRef"]]
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "false")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", "stop_all_new_omnigent_work"
    )
    store = InMemoryExecutionPlanStore()
    await store.persist(recorded)
    assert await store.load(binding["planRef"]) == recorded
    assert compute_plan_ref(recorded.payload) == binding["planRef"]
    assert recorded.payload.executionRealizerRef == PROFILE_BOUND_REALIZER
    # The realizer stays registered and resolvable for the recorded plan even
    # though no new plan may select it.
    from moonmind.omnigent.harness_platform.support import validate_realizer

    validate_realizer(recorded.payload.executionRealizerRef)
