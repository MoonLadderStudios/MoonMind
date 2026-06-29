from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def test_runtime_and_skill_visibility_read_canonical_workflow_payload() -> None:
    workflow = MoonMindRunWorkflow()
    parameters = {
        "workflow": {
            "instructions": "Resolve the issue.",
            "runtime": {"mode": "codex_cli"},
            "tool": {"type": "skill", "name": "fix-ci"},
        }
    }

    assert workflow._runtime_visibility_from_parameters(parameters) == "codex_cli"
    assert workflow._skill_visibility_from_parameters(parameters) == "fix-ci"
