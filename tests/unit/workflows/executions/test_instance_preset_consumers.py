"""Instance preset consumers preserve authored snapshots and explicit values."""

from copy import deepcopy

import pytest

from moonmind.workflows.executions.preset_expansion import expand_preset_for_child_run
from moonmind.workflows.executions.preset_readiness import saved_preset_capability_check


def test_saved_preset_readiness_projects_identity_without_person_partition():
    parameters = {
        "system": {"recurrence": {"definitionId": "scheduled"}},
        "requiredCapabilities": ["repository.read"],
        "workflow": {
            "appliedStepTemplates": [
                {
                    "slug": "root",
                    "inputs": {"enabled": False, "suffix": ""},
                    "composition": {
                        "slug": "root",
                        "includes": [
                            {"slug": "child"},
                        ],
                    },
                }
            ]
        },
    }
    before = deepcopy(parameters)
    check = saved_preset_capability_check(parameters)
    assert check is not None
    assert check.model_dump(exclude_none=True) == {
        "definition_id": "scheduled",
        "presets": [{"slug": "root"}, {"slug": "child"}],
        "required_capabilities": ["repository.read"],
    }
    assert parameters == before


@pytest.mark.asyncio
async def test_child_expansion_uses_instance_contract_and_preserves_explicit_inputs(
    monkeypatch,
):
    from api_service.services.presets import catalog

    calls = []

    class InstanceCatalog:
        def __init__(self, session):
            pass

        async def expand_template(self, *, slug, inputs, context, options):
            calls.append((slug, deepcopy(inputs)))
            return {
                "steps": [{"instructions": "Keep false and empty"}],
                "appliedTemplate": {
                    "slug": slug,
                    "inputs": inputs,
                    "stepIds": ["one"],
                    "appliedAt": "now",
                },
            }

    monkeypatch.setattr(catalog, "PresetCatalogService", InstanceCatalog)
    original = {
        "workflow": {
            "taskTemplate": {"slug": "custom"},
            "inputs": {"enabled": False, "suffix": ""},
        }
    }
    expanded = await expand_preset_for_child_run(
        session=object(),
        initial_parameters=original,
        allow_goal_schedule=False,
    )
    assert calls == [("custom", {"enabled": False, "suffix": ""})]
    assert expanded["workflow"]["taskTemplate"] == {"slug": "custom"}
    assert (
        expanded["workflow"]["appliedStepTemplates"][0]["inputs"]
        == original["workflow"]["inputs"]
    )


@pytest.mark.asyncio
async def test_recorded_steps_and_legacy_provenance_are_not_reresolved(monkeypatch):
    snapshot = {
        "workflow": {
            "taskTemplate": {
                "slug": "old",
                "scope": "personal",
                "scopeRef": "old-owner",
            },
            "steps": [{"instructions": "Recorded contents", "enabled": False}],
        }
    }
    before = deepcopy(snapshot)
    assert (
        await expand_preset_for_child_run(session=None, initial_parameters=snapshot)
        == before
    )
    assert snapshot == before


@pytest.mark.asyncio
async def test_unexpanded_legacy_scope_requires_refresh_before_lookup():
    from api_service.services.presets.catalog import PresetValidationError

    with pytest.raises(PresetValidationError, match="reapply the preset"):
        await expand_preset_for_child_run(
            session=None,
            allow_goal_schedule=False,
            initial_parameters={
                "workflow": {"taskTemplate": {"slug": "same", "scope": "personal"}},
            },
        )


def test_legacy_projection_preserves_scope_evidence_and_replay_payload():
    from moonmind.workflows.executions.preset_readiness import (
        legacy_saved_preset_capability_check,
    )

    saved = {
        "system": {"recurrence": {"definitionId": "saved"}},
        "task": {
            "appliedStepTemplates": [
                {"slug": "old", "scope": "personal", "scopeRef": "owner"}
            ]
        },
    }
    check = saved_preset_capability_check(saved)
    assert check.presets == [{"slug": "old", "scope": "personal", "scopeRef": "owner"}]
    assert "principal" not in check.model_dump(exclude_none=True)
    legacy = legacy_saved_preset_capability_check(saved, principal="owner")
    assert legacy.model_dump() == {
        "principal": "owner",
        "definition_id": "saved",
        "presets": [{"slug": "old", "scope": "personal", "scopeRef": "owner"}],
        "required_capabilities": [],
    }


@pytest.mark.parametrize(
    "legacy_fields", [{"scope": "personal"}, {"scope_ref": "owner"}]
)
def test_current_projection_retains_legacy_wrapper_evidence(legacy_fields):
    from moonmind.workflows.executions.preset_readiness import (
        legacy_saved_preset_capability_check,
    )

    saved = {
        "system": {"recurrence": {"definitionId": "saved"}},
        "workflow": {
            "appliedStepTemplates": [
                {"slug": "old", **legacy_fields, "composition": {"slug": "old"}}
            ]
        },
    }
    before = deepcopy(saved)
    check = saved_preset_capability_check(saved)
    assert any("scope" in item or "scopeRef" in item for item in check.presets)
    assert saved == before
    # Retained commands selected composition rather than wrapper. Replaying
    # those commands must not acquire new fields or change their scope.
    legacy = legacy_saved_preset_capability_check(saved, principal="owner")
    assert legacy.presets == [{"slug": "old", "scope": "global"}]
