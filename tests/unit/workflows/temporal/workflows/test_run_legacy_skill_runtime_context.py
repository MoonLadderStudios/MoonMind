"""Runtime/profile context propagation for activity-backed plan tools."""

from __future__ import annotations

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def test_legacy_skill_runtime_context_includes_selected_provider_snapshot() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._profile_snapshots = {
        "codex_openrouter_qwen36_plus": {
            "profile_id": "codex_openrouter_qwen36_plus",
            "runtime_id": "codex_cli",
            "provider_id": "openrouter",
            "credential_source": "secret_ref",
            "runtime_materialization_mode": "composite",
            "enabled": True,
            "launch_ready": True,
            "auth_state": "connected",
            "secret_refs": {"provider_api_key": "env://OPENROUTER_API_KEY"},
            "env_template": {
                "OPENROUTER_API_KEY": {"from_secret_ref": "provider_api_key"}
            },
        }
    }

    context = workflow._legacy_skill_runtime_context(
        {
            "targetRuntime": "codex_cli",
            "profileId": "codex_openrouter_qwen36_plus",
            "task": {
                "runtime": {
                    "mode": "codex_cli",
                    "profileId": "codex_openrouter_qwen36_plus",
                }
            },
        }
    )

    assert context["targetRuntime"] == "codex_cli"
    assert context["executionProfileRef"] == "codex_openrouter_qwen36_plus"
    assert context["providerProfile"]["runtime_id"] == "codex_cli"
    assert context["providerProfile"]["provider_id"] == "openrouter"
