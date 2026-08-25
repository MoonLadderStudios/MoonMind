"""The merge-automation gate must survive the trip through a client.

Preset *steps* are expanded, edited, and resubmitted by the client. Workflow
level publish policy is not: it is resolved again from the stored template at
submission time. The gate decides whether MoonMind may push, request reviews,
and merge on the operator's behalf, so a submission may not quietly rewrite it.
"""

from __future__ import annotations

from typing import Any

import pytest

from api_service.api.routers import executions as executions_router

pytestmark = [pytest.mark.asyncio]

_TEMPLATE_PUBLISH: dict[str, Any] = {
    "mode": "none",
    "mergeAutomation": {
        "enabled": True,
        "mergeMethod": "squash",
        "reviewLoop": {"enabled": True, "provider": "codex"},
    },
}


def _task_payload(publish: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "appliedStepTemplates": [
            {"slug": "pr-review-resolve", "scope": "global", "inputs": {"pull_request": "3771"}}
        ],
        "taskTemplate": {"slug": "pr-review-resolve", "scope": "global"},
    }
    if publish is not None:
        payload["publish"] = publish
    return payload


@pytest.fixture
def stub_catalog(monkeypatch: pytest.MonkeyPatch):
    """Return the stored template's workflow publish policy."""

    class _Service:
        def __init__(self, _session: Any) -> None:
            pass

        async def resolve_workflow_publish(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "mode": _TEMPLATE_PUBLISH["mode"],
                "mergeAutomation": dict(_TEMPLATE_PUBLISH["mergeAutomation"]),
            }

    import api_service.services.presets.catalog as catalog

    monkeypatch.setattr(catalog, "PresetCatalogService", _Service)
    monkeypatch.setattr(
        executions_router,
        "resolve_template_scope_for_user",
        lambda **_kwargs: ("global", None),
    )
    return _Service


async def _apply(task_payload: dict[str, Any]) -> dict[str, Any]:
    await executions_router._apply_preset_workflow_publish(
        task_payload=task_payload,
        request_payload={"repository": "MoonLadderStudios/MoonMind"},
        session=object(),
        user=object(),
    )
    return task_payload


async def test_missing_publish_takes_the_template_policy(stub_catalog) -> None:
    payload = await _apply(_task_payload())

    assert payload["publish"]["mode"] == "none"
    assert payload["publish"]["mergeAutomation"]["enabled"] is True


async def test_a_stripped_gate_is_restored(stub_catalog) -> None:
    """Submitting publish without the gate must not disable merge automation."""

    payload = await _apply(_task_payload({"mode": "none"}))

    assert payload["publish"]["mergeAutomation"]["enabled"] is True
    assert payload["publish"]["mergeAutomation"]["reviewLoop"]["provider"] == "codex"


async def test_a_rewritten_gate_is_restored(stub_catalog) -> None:
    """A submission cannot turn the gate off or downgrade its review loop."""

    payload = await _apply(
        _task_payload(
            {
                "mode": "none",
                "mergeAutomation": {
                    "enabled": False,
                    "mergeMethod": "merge",
                    "reviewLoop": {"enabled": False},
                },
            }
        )
    )

    gate = payload["publish"]["mergeAutomation"]
    assert gate["enabled"] is True
    assert gate["mergeMethod"] == "squash"
    assert gate["reviewLoop"]["enabled"] is True


async def test_a_snake_case_gate_alias_cannot_shadow_the_restored_gate(
    stub_catalog,
) -> None:
    payload = await _apply(
        _task_payload({"mode": "none", "merge_automation": {"enabled": False}})
    )

    assert "merge_automation" not in payload["publish"]
    assert payload["publish"]["mergeAutomation"]["enabled"] is True


async def test_other_publish_fields_survive(stub_catalog) -> None:
    """Only the gate is server-owned; the rest of publish stays the client's."""

    payload = await _apply(
        _task_payload({"mode": "none", "draft": True, "labels": ["automation"]})
    )

    assert payload["publish"]["draft"] is True
    assert payload["publish"]["labels"] == ["automation"]
    assert payload["publish"]["mergeAutomation"]["enabled"] is True


async def test_a_submission_without_an_applied_preset_is_untouched(
    stub_catalog,
) -> None:
    payload = {"publish": {"mode": "pr"}}
    await executions_router._apply_preset_workflow_publish(
        task_payload=payload,
        request_payload={},
        session=object(),
        user=object(),
    )

    assert payload == {"publish": {"mode": "pr"}}


async def test_a_template_without_a_gate_leaves_publish_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Service:
        def __init__(self, _session: Any) -> None:
            pass

        async def resolve_workflow_publish(self, **_kwargs: Any) -> dict[str, Any]:
            return {"mode": "pr"}

    import api_service.services.presets.catalog as catalog

    monkeypatch.setattr(catalog, "PresetCatalogService", _Service)
    monkeypatch.setattr(
        executions_router,
        "resolve_template_scope_for_user",
        lambda **_kwargs: ("global", None),
    )

    payload = await _apply(_task_payload({"mode": "none", "draft": True}))

    assert payload["publish"] == {"mode": "none", "draft": True}
