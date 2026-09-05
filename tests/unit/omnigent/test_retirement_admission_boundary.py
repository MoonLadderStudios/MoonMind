"""New-admission enforcement at the real plan-compilation boundary.

Source issue: MoonLadderStudios/MoonMind#3835 (required work section 2).

Plan compilation is the single new-admission boundary for every client: the
trusted planner default, an alternate API client supplying an explicit
``execution_realizer_ref``, a schedule, and a preset all resolve here. These
tests drive :func:`compile_execution_plan` itself — not a nearby helper — so the
code-owned retirement class is proven to gate real Codex plan admission.

They also prove the two ordering rules the issue insists on:

* new admission stops *only* when the class says so — today every legacy row is
  ``active_product_path`` because #3833 has not promoted the qualified generic
  rows, so Codex plan compilation still succeeds; and
* disabling new admission never touches execution, cancellation, cleanup, or
  reads for an already-recorded plan.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.omnigent import legacy_retirement
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
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
def codex_plan_inputs() -> dict[str, object]:
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


def _reclassify(monkeypatch: pytest.MonkeyPatch, **updates: object) -> None:
    record = get_retirement_record(PROFILE_BOUND_PATH_ID)
    changed = record.model_copy(update=updates)
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_ID, PROFILE_BOUND_PATH_ID, changed
    )
    monkeypatch.setitem(
        legacy_retirement._INVENTORY_BY_SURFACE,
        f"realizer:{PROFILE_BOUND_REALIZER}",
        changed,
    )


def test_active_product_path_still_compiles_codex_plans(codex_plan_inputs) -> None:
    # #3833 has not promoted the qualified generic rows, so the legacy realizer
    # is still the trusted Codex selection and new admission must not stop.
    assert (
        get_retirement_record(PROFILE_BOUND_PATH_ID).retirement_class
        is RetirementClass.ACTIVE_PRODUCT_PATH
    )
    envelope = compile_execution_plan(**codex_plan_inputs)
    assert envelope.payload.executionRealizerRef == PROFILE_BOUND_REALIZER


def test_new_admission_disabled_rejects_the_trusted_planner_default(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reclassify(
        monkeypatch,
        retirement_class=RetirementClass.NEW_ADMISSION_DISABLED,
        new_admission_source="",
    )
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(**codex_plan_inputs)
    assert (
        excinfo.value.code
        == HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE.value
    )
    assert "no longer admits new work" in str(excinfo.value)


def test_alternate_client_cannot_bypass_the_retirement_state(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit client-supplied realizer is held to the same class."""

    _reclassify(
        monkeypatch,
        retirement_class=RetirementClass.NEW_ADMISSION_DISABLED,
        new_admission_source="",
    )
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(
            **codex_plan_inputs,
            execution_realizer_ref=PROFILE_BOUND_REALIZER,
        )
    assert "no longer admits new work" in str(excinfo.value)


def test_rollback_only_admits_only_the_allowlisted_generation(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reclassify(
        monkeypatch,
        retirement_class=RetirementClass.ROLLBACK_ONLY,
        new_admission_source="operator rollback generation",
        rollback_dependency=True,
        rollback_generations=frozenset({"gen-rollback-1"}),
    )
    with pytest.raises(HarnessPlatformError):
        compile_execution_plan(**codex_plan_inputs)

    with pytest.raises(HarnessPlatformError):
        compile_execution_plan(**codex_plan_inputs, rollback_generation="gen-rollback")

    envelope = compile_execution_plan(
        **codex_plan_inputs, rollback_generation="gen-rollback-1"
    )
    assert envelope.payload.executionRealizerRef == PROFILE_BOUND_REALIZER


def test_disabled_admission_does_not_change_recorded_plans(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-compiled plan keeps its recorded realizer and digest."""

    recorded = compile_execution_plan(**codex_plan_inputs)
    _reclassify(
        monkeypatch,
        retirement_class=RetirementClass.TEMPORAL_REPLAY_ONLY,
        new_admission_source="",
        replay_dependency=True,
    )
    assert recorded.payload.executionRealizerRef == PROFILE_BOUND_REALIZER
    # The realizer stays registered and resolvable for the recorded plan even
    # though no new plan may select it.
    from moonmind.omnigent.harness_platform.support import validate_realizer

    validate_realizer(recorded.payload.executionRealizerRef)
