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

from datetime import UTC, datetime, timedelta

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
from moonmind.omnigent.session_supervisor_rollback import (
    DEFAULT_ROLLBACK_EXERCISE_MAX_AGE,
    RollbackExerciseRecord,
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


# ``host_architecture`` is deliberately omitted: it belongs to the atomic
# launch-authority set, and the rollback scope must resolve the effective
# architecture the plan actually runs on (the Host Class default here).
ROLLBACK_SCOPE_INPUTS: dict[str, object] = {
    "agent_profile_snapshot_ref": "agent-profile-snapshot:rollback",
    "rollback_owner_cohort": "internal",
}


def _exercise_record(**overrides: object) -> RollbackExerciseRecord:
    """A fresh, successful exercise for exactly the compiled plan's scope."""

    scope = {
        "agentProfileRef": ROLLBACK_SCOPE_INPUTS["agent_profile_snapshot_ref"],
        "hostClassRef": "omnigent-codex-current@1",
        "materializerRef": "codex-oauth-home@1",
        "executionRealizerRef": PROFILE_BOUND_REALIZER,
        "modelQualifiedId": "gpt-5",
        "launchPolicyRef": "codex-on-demand@1",
        "hostMode": "on-demand",
        "architecture": "linux/amd64",
        "ownerCohort": ROLLBACK_SCOPE_INPUTS["rollback_owner_cohort"],
    }
    scope.update(overrides.pop("scope", {}))  # type: ignore[arg-type]
    payload: dict[str, object] = {
        "retirementPathId": PROFILE_BOUND_PATH_ID,
        "scope": scope,
        "exercisedAt": datetime.now(UTC),
        "evidenceRef": "artifact://rollback-exercise",
        "succeeded": True,
        "futureAdmissionOnly": True,
    }
    payload.update(overrides)
    return RollbackExerciseRecord.model_validate(payload)


def _reclassify_rollback_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _reclassify(
        monkeypatch,
        retirement_class=RetirementClass.ROLLBACK_ONLY,
        new_admission_source="operator rollback generation",
        rollback_dependency=True,
        rollback_generations=frozenset({"gen-rollback-1"}),
    )


def test_rollback_only_admits_only_the_allowlisted_generation(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reclassify_rollback_only(monkeypatch)
    with pytest.raises(HarnessPlatformError):
        compile_execution_plan(**codex_plan_inputs)

    with pytest.raises(HarnessPlatformError):
        compile_execution_plan(
            **codex_plan_inputs,
            **ROLLBACK_SCOPE_INPUTS,
            rollback_generation="gen-rollback",
            rollback_exercise_records=[_exercise_record()],
        )

    envelope = compile_execution_plan(
        **codex_plan_inputs,
        **ROLLBACK_SCOPE_INPUTS,
        rollback_generation="gen-rollback-1",
        rollback_exercise_records=[_exercise_record()],
    )
    assert envelope.payload.executionRealizerRef == PROFILE_BOUND_REALIZER


def test_rollback_only_refuses_a_generation_with_no_exercise_evidence(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlisted generation alone is a global string, not scoped evidence."""

    _reclassify_rollback_only(monkeypatch)
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(
            **codex_plan_inputs,
            **ROLLBACK_SCOPE_INPUTS,
            rollback_generation="gen-rollback-1",
        )
    assert "rollback_exercise" in str(excinfo.value)


@pytest.mark.parametrize(
    "dimension, value",
    [
        ("hostClassRef", "omnigent-codex-other@1"),
        ("materializerRef", "codex-api-key@1"),
        ("modelQualifiedId", "gpt-5-codex"),
        ("launchPolicyRef", "codex-static@1"),
        ("hostMode", "static-connected"),
        ("architecture", "linux/arm64"),
        ("ownerCohort", "external"),
        ("agentProfileRef", "agent-profile-snapshot:other"),
    ],
)
def test_rollback_exercise_does_not_widen_beyond_its_exact_scope(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch, dimension: str, value: str
) -> None:
    """One exercised combination never re-admits a different one."""

    _reclassify_rollback_only(monkeypatch)
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(
            **codex_plan_inputs,
            **ROLLBACK_SCOPE_INPUTS,
            rollback_generation="gen-rollback-1",
            rollback_exercise_records=[_exercise_record(scope={dimension: value})],
        )
    assert "rollback_exercise" in str(excinfo.value)


def test_rollback_exercise_expires_with_its_window(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence older than the exercise window no longer re-admits new work."""

    _reclassify_rollback_only(monkeypatch)
    expired = _exercise_record(
        exercisedAt=datetime.now(UTC)
        - (DEFAULT_ROLLBACK_EXERCISE_MAX_AGE + timedelta(minutes=1))
    )
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(
            **codex_plan_inputs,
            **ROLLBACK_SCOPE_INPUTS,
            rollback_generation="gen-rollback-1",
            rollback_exercise_records=[expired],
        )
    assert "rollback_exercise" in str(excinfo.value)


def test_rollback_only_requires_every_scope_dimension(
    codex_plan_inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan that cannot name its exact scope fails closed."""

    _reclassify_rollback_only(monkeypatch)
    incomplete = dict(ROLLBACK_SCOPE_INPUTS)
    incomplete["rollback_owner_cohort"] = None
    with pytest.raises(HarnessPlatformError) as excinfo:
        compile_execution_plan(
            **codex_plan_inputs,
            **incomplete,
            rollback_generation="gen-rollback-1",
            rollback_exercise_records=[_exercise_record()],
        )
    assert "rollback_scope_incomplete" in str(excinfo.value)


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
