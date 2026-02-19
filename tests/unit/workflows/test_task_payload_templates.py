"""Tests for task payload template metadata compiler."""

from __future__ import annotations

from moonmind.workflows.tasks.payload import compile_task_payload_templates


def test_compile_task_payload_templates_merges_capabilities_and_metadata() -> None:
    payload = {
        "repository": "Moon/Repo",
        "requiredCapabilities": ["codex", "git"],
        "task": {
            "instructions": "Run work",
            "appliedStepTemplates": [
                {
                    "slug": "pr-code-change",
                    "version": "1.0.0",
                    "inputs": {"summary": "fix"},
                    "stepIds": ["tpl:pr-code-change:1.0.0:01:abcd1234"],
                    "capabilities": ["gh", "docker"],
                }
            ],
        },
    }

    compiled = compile_task_payload_templates(payload)

    assert sorted(compiled["requiredCapabilities"]) == ["codex", "docker", "gh", "git"]
    assert compiled["task"]["appliedStepTemplates"][0]["slug"] == "pr-code-change"
    assert "appliedAt" in compiled["task"]["appliedStepTemplates"][0]
