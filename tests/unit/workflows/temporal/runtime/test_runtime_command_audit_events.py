from __future__ import annotations

from moonmind.workflows.temporal.runtime.launcher import (
    build_runtime_command_audit_events,
)


def test_runtime_command_audit_events_include_detected_rendered_and_passthrough() -> None:
    events = build_runtime_command_audit_events(
        runtime_id="codex_cli",
        runtime_command={
            "command": "review",
            "rawCommand": "/review",
            "sourcePath": "objective.instructions",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "runtimeCapabilityVersion": "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
        },
        render_result={
            "status": "rendered",
            "renderMode": "prompt_prefix",
        },
    )

    assert events == [
        {
            "event": "runtime_command.detected",
            "runtimeId": "codex_cli",
            "command": "review",
            "sourcePath": "objective.instructions",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "runtimeCapabilityVersion": "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
        },
        {
            "event": "runtime_command.rendered",
            "runtimeId": "codex_cli",
            "command": "review",
            "renderMode": "prompt_prefix",
        },
    ]

    passthrough_events = build_runtime_command_audit_events(
        runtime_id="codex_cli",
        runtime_command={
            "command": "future-command",
            "sourcePath": "objective.instructions",
            "hintStatus": "opaque",
            "recognitionMode": "runtime_passthrough",
        },
        render_result={
            "status": "passed_through",
            "renderMode": "prompt_prefix",
        },
    )

    assert passthrough_events[-1] == {
        "event": "runtime_command.passthrough",
        "runtimeId": "codex_cli",
        "command": "future-command",
        "hintStatus": "opaque",
        "renderMode": "prompt_prefix",
    }


def test_runtime_command_audit_events_redact_secret_like_values() -> None:
    events = build_runtime_command_audit_events(
        runtime_id="codex_cli",
        runtime_command={
            "command": "review",
            "sourcePath": "objective.instructions",
            "hintStatus": "hinted",
            "recognitionMode": "hinted_runtime_passthrough",
            "runtimeCapabilityVersion": "2026-05-13",
            "hintCatalogVersion": "2026-05-13",
            "token": "github_pat_1234567890abcdef",
            "password": "password=super-secret",
        },
        render_result={
            "status": "rendered",
            "renderMode": "prompt_prefix",
            "diagnostics": {
                "auth": "Bearer ghp_1234567890abcdef",
                "private": "-----BEGIN PRIVATE KEY-----abc",
            },
        },
    )

    serialized = repr(events)
    assert "github_pat_" not in serialized
    assert "ghp_" not in serialized
    assert "password=super-secret" not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized
    assert events[0]["command"] == "review"
