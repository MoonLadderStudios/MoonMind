from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.launcher import (
    build_runtime_command_audit_events,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _historical_snapshot() -> dict[str, object]:
    return {
        "snapshotVersion": 1,
        "draft": {
            "taskShape": "skill_only",
            "instructions": "/review\nCheck the branch.",
            "runtime": "codex_cli",
            "runtimeCommand": {
                "kind": "slash_command",
                "sourcePath": "objective.instructions",
                "command": "review",
                "rawCommand": "/review",
                "targetRuntime": "codex_cli",
                "recognitionMode": "hinted_runtime_passthrough",
                "runtimeCapabilityVersion": "2026-05-12",
                "hintCatalogVersion": "2026-05-12",
            },
        },
    }


def test_artifact_backed_snapshot_preserves_edit_and_detail_command_metadata() -> None:
    snapshot = _historical_snapshot()
    draft = snapshot["draft"]
    assert isinstance(draft, dict)

    assert draft["instructions"] == "/review\nCheck the branch."
    command = draft["runtimeCommand"]
    assert isinstance(command, dict)
    assert command["command"] == "review"
    assert command["targetRuntime"] == "codex_cli"
    assert command["recognitionMode"] == "hinted_runtime_passthrough"
    assert command["runtimeCapabilityVersion"] == "2026-05-12"
    assert command["hintCatalogVersion"] == "2026-05-12"


def test_exact_and_edit_for_rerun_keep_source_command_metadata_immutable() -> None:
    snapshot = _historical_snapshot()
    draft = snapshot["draft"]
    assert isinstance(draft, dict)
    command = draft["runtimeCommand"]
    assert isinstance(command, dict)

    editable_copy = {
        **draft,
        "instructions": "/review\nCheck the branch with extra focus.",
        "currentWarnings": [
            "Runtime command capability version changed from 2026-05-12 to 2026-05-13."
        ],
    }

    assert command["runtimeCapabilityVersion"] == "2026-05-12"
    assert command["hintCatalogVersion"] == "2026-05-12"
    assert editable_copy["currentWarnings"]
    assert draft["instructions"] == "/review\nCheck the branch."


def test_runtime_command_audit_events_are_operator_readable_and_secret_safe() -> None:
    events = build_runtime_command_audit_events(
        runtime_id="codex_cli",
        runtime_command={
            "command": "future-command",
            "sourcePath": "objective.instructions",
            "hintStatus": "opaque",
            "recognitionMode": "runtime_passthrough",
            "runtimeCapabilityVersion": "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
            "token": "ghp_1234567890abcdef",
        },
        render_result={
            "status": "passed_through",
            "renderMode": "prompt_prefix",
            "diagnostics": {"secret": "password=super-secret"},
        },
    )

    assert [event["event"] for event in events] == [
        "runtime_command.detected",
        "runtime_command.passthrough",
    ]
    assert "future-command" in repr(events)
    assert "ghp_" not in repr(events)
    assert "password=super-secret" not in repr(events)
