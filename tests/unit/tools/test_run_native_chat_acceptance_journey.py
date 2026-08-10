from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from moonmind.omnigent.conformance import ConformanceContractError


_SCRIPT = Path(__file__).parents[3] / "tools" / "run_native_chat_acceptance_journey.py"
_SPEC = importlib.util.spec_from_file_location("run_native_chat_acceptance_journey", _SCRIPT)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_protected_producer_owns_action_order_and_carries_observed_state(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_run(command, **_kwargs):
        action = command[-2]
        prior = json.loads(command[-1])
        calls.append((action, prior))
        required = runner._REQUIRED_RESULT_KEYS[action]
        observation = {key: True for key in required}
        if action == "create_workflow":
            observation.update(
                workflowRef="wf-safe", runRef="run-safe", stepRef="step-safe",
                agentRunRef="agent-safe",
            )
        if action == "open_workflow_detail_chat":
            observation.update(
                bindingRef="binding-safe", profileRef="profile-safe",
                providerProfileRef="provider-profile-safe",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "ok": True,
                "observation": observation,
                "evidenceRefs": [f"artifact://observations/{action}"],
            }),
            stderr="",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    state, refs = runner._run_protected_actions(
        {"MOONMIND_NATIVE_CHAT_ACTION_COMMAND": "product-adapter"}
    )

    assert [action for action, _ in calls] == list(runner._PROTECTED_ACTIONS)
    assert calls[1][1]["workflowRef"] == "wf-safe"
    assert state["sourceUnmodified"] is True
    assert len(refs) == len(runner._PROTECTED_ACTIONS)


def test_protected_producer_rejects_missing_adapter_and_prebuilt_input() -> None:
    with pytest.raises(ConformanceContractError, match="must name"):
        runner._run_protected_actions({})
    # The producer has no evidence/matrix input argument: callers may select
    # immutable images, but cannot submit their own passing result.
    parser_flags = _SCRIPT.read_text(encoding="utf-8")
    assert 'parser.add_argument("--evidence' not in parser_flags
