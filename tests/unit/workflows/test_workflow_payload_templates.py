"""Tests for task payload template metadata compiler."""

from __future__ import annotations

from moonmind.workflows.executions.payload import compile_workflow_payload_templates

def test_compile_workflow_payload_templates_merges_capabilities_and_metadata() -> None:
    payload = {
        "repository": "Moon/Repo",
        "requiredCapabilities": ["codex", "git"],
        "task": {
            "instructions": "Run work",
            "appliedStepTemplates": [
                {
                    "slug": "pr-code-change",
                    "inputs": {"summary": "fix"},
                    "stepIds": ["tpl:pr-code-change:01:abcd1234"],
                    "capabilities": ["gh", "docker"],
                }
            ],
        },
    }

    compiled = compile_workflow_payload_templates(payload)

    assert sorted(compiled["requiredCapabilities"]) == ["codex", "docker", "gh", "git"]
    assert compiled["task"]["appliedStepTemplates"][0]["slug"] == "pr-code-change"
    assert "version" not in compiled["task"]["appliedStepTemplates"][0]
    assert "appliedAt" in compiled["task"]["appliedStepTemplates"][0]

def test_compile_workflow_payload_templates_strips_template_versions() -> None:
    payload = {
        "task": {
            "appliedStepTemplates": [
                {
                    "slug": "pr-code-change",
                    "version": "1.0.0",
                }
            ],
        },
    }

    compiled = compile_workflow_payload_templates(payload)

    assert compiled["task"]["appliedStepTemplates"][0]["slug"] == "pr-code-change"
    assert "version" not in compiled["task"]["appliedStepTemplates"][0]
