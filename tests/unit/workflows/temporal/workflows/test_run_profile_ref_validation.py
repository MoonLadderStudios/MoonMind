from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow


def test_validated_execution_profile_ref_rejects_unknown_profile() -> None:
    workflow = MoonMindUserWorkflow()
    workflow._profile_snapshots = {
        "codex-ready": {"profile_id": "codex-ready", "runtime_id": "codex_cli"}
    }

    with pytest.raises(ValueError, match="not a known profile"):
        workflow._validated_execution_profile_ref(
            "missing-profile",
            agent_id="codex_cli",
            source_label="Task",
        )


def test_validated_execution_profile_ref_rejects_runtime_mismatch() -> None:
    workflow = MoonMindUserWorkflow()
    workflow._profile_snapshots = {
        "claude-ready": {"profile_id": "claude-ready", "runtime_id": "claude_code"}
    }

    with pytest.raises(ValueError, match="belongs to runtime 'claude_code'"):
        workflow._validated_execution_profile_ref(
            "claude-ready",
            agent_id="codex_cli",
            source_label="Task",
        )


def test_inherited_execution_profile_ref_rejects_runtime_mismatch() -> None:
    workflow = MoonMindUserWorkflow()
    workflow._profile_snapshots = {
        "claude-ready": {"profile_id": "claude-ready", "runtime_id": "claude_code"}
    }

    with pytest.raises(ValueError, match="targets runtime 'claude_code'"):
        workflow._inherited_execution_profile_ref(
            {
                "task": {
                    "runtime": {
                        "mode": "claude_code",
                        "profileId": "claude-ready",
                    }
                }
            },
            agent_id="codex_cli",
        )
