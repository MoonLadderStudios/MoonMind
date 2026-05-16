from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.tasks.task_contract import (
    build_canonical_task_view,
    build_authoritative_task_input_snapshot,
    build_effective_task_skill_selectors,
    build_runtime_command_preview_config,
    CanonicalTaskPayload,
    ResumeFromFailedStepRef,
    resolve_publish_mode_for_skill,
    TaskContractError,
    TaskExecutionSpec,
    TaskRecoveryProvenance,
    TaskStepSpec,
)
from tests.helpers.step_type_payloads import (
    mixed_tool_skill_step,
    preset_step,
    skill_step,
    task_payload,
    tool_step,
)

def test_task_skills_accepts_valid_properties() -> None:
    """T001: Ensure task.skills structures successfully marshal."""
    raw_payload = {
        "repository": "test/repo",
        "instructions": "execute",
        "skills": {
            "sets": ["default", "python"],
            "include": [
                {"name": "test-skill", "version": "1.0.0"},
                {"name": "unversioned"},
            ],
            "exclude": ["legacy"],
            "materializationMode": "hybrid",
        },
    }
    
    spec = TaskExecutionSpec.model_validate(raw_payload)
    
    assert spec.skills is not None
    assert spec.skills.sets == ["default", "python"]
    assert len(spec.skills.include) == 2
    assert spec.skills.include[0].name == "test-skill"
    assert spec.skills.include[0].version == "1.0.0"
    assert spec.skills.include[1].name == "unversioned"
    assert spec.skills.include[1].version is None
    assert spec.skills.exclude == ["legacy"]
    assert spec.skills.materialization_mode == "hybrid"


def test_authoritative_snapshot_preserves_explicit_empty_dependencies() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "Run MM-639 without dependencies.",
            "dependencies": [],
            "steps": [
                {
                    "id": "step-1",
                    "instructions": "Use the explicit empty dependency list.",
                    "dependsOn": [],
                }
            ],
        },
        dependency_declarations=["MM-638"],
    )

    assert snapshot["dependencyDeclarations"] == []
    assert snapshot["steps"][0]["dependencies"] == []


def test_authoritative_snapshot_detects_jira_key_without_serializing_metadata() -> None:
    class Unserializable:
        pass

    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "Implement MM-639.",
            "metadata": {"opaque": Unserializable()},
            "steps": [
                {
                    "id": "step-1",
                    "instructions": "Preserve authored fields.",
                }
            ],
        }
    )

    assert snapshot["traceability"]["jiraIssueKey"] == "MM-639"


def test_authoritative_snapshot_records_task_runtime_command_metadata() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/review\nCheck this branch for regressions.",
            "runtime": {"mode": "codex"},
        }
    )

    assert snapshot["objective"]["instructions"] == (
        "/review\nCheck this branch for regressions."
    )
    assert snapshot["objective"]["runtimeCommand"] == {
        "kind": "slash_command",
        "source": "leading_slash",
        "sourcePath": "objective.instructions",
        "command": "review",
        "rawCommand": "/review",
        "args": "",
        "instructionBody": "Check this branch for regressions.",
        "targetRuntime": "codex",
        "detectionStatus": "detected",
        "hintStatus": "hinted",
        "recognitionMode": "hinted_runtime_passthrough",
        "requiresRuntimeRecognition": True,
        "runtimeCapabilityVersion": "2026-05-13",
        "hintCatalogVersion": "2026-05-13",
        "detectionPhase": "submit",
    }


def test_authoritative_snapshot_records_step_runtime_command_metadata() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "Run task.",
            "runtime": {"mode": "claude"},
            "steps": [
                {
                    "id": "simplify-step",
                    "instructions": "/simplify\nReduce duplication.",
                }
            ],
        }
    )

    assert snapshot["steps"][0]["instructions"] == "/simplify\nReduce duplication."
    assert snapshot["steps"][0]["runtimeCommand"]["command"] == "simplify"
    assert snapshot["steps"][0]["runtimeCommand"]["sourcePath"] == (
        "steps[0].instructions"
    )
    assert snapshot["steps"][0]["runtimeCommand"]["targetStepId"] == "simplify-step"
    assert snapshot["steps"][0]["runtimeCommand"]["targetRuntime"] == "claude"
    assert snapshot["steps"][0]["runtimeCommand"]["recognitionMode"] == (
        "hinted_runtime_passthrough"
    )


def test_authoritative_snapshot_preserves_task_and_step_runtime_command_versions() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/review\nCheck this branch for regressions.",
            "runtime": {"mode": "codex"},
            "steps": [
                {
                    "id": "simplify-step",
                    "instructions": "/simplify\nReduce duplication.",
                }
            ],
        }
    )

    objective_command = snapshot["objective"]["runtimeCommand"]
    step_command = snapshot["steps"][0]["runtimeCommand"]

    assert objective_command["runtimeCapabilityVersion"] == "2026-05-13"
    assert objective_command["hintCatalogVersion"] == "2026-05-13"
    assert objective_command["detectionPhase"] == "submit"
    assert step_command["runtimeCapabilityVersion"] == "2026-05-13"
    assert step_command["hintCatalogVersion"] == "2026-05-13"
    assert step_command["detectionPhase"] == "submit"


def test_runtime_command_unknown_valid_commands_are_opaque_not_rejected() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/future-command now\nUse the provider command.",
            "runtime": {"mode": "codex"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["command"] == "future-command"
    assert command["args"] == "now"
    assert command["instructionBody"] == "Use the provider command."
    assert command["hintStatus"] == "opaque"
    assert command["recognitionMode"] == "runtime_passthrough"
    assert command["requiresRuntimeRecognition"] is True


def test_runtime_command_does_not_create_workflow_action_steps() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/future-command --dangerous-looking\nUse provider behavior.",
            "runtime": {"mode": "codex"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["command"] == "future-command"
    assert command["args"] == "--dangerous-looking"
    assert command["hintStatus"] == "opaque"
    assert command["recognitionMode"] == "runtime_passthrough"
    assert snapshot["steps"] == []
    # Runtime passthrough commands must stay provider-owned and must not be
    # promoted into workflow action fields if similar command names are added.
    assert "workflowAction" not in snapshot["objective"]
    assert "action" not in snapshot["objective"]


def test_runtime_command_preserves_opaque_provider_command_lines() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/provider.command now\nOpaque provider body.",
            "runtime": {"mode": "codex"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["command"] == "provider.command"
    assert command["rawCommand"] == "/provider.command now"
    assert command["args"] == ""
    assert command["instructionBody"] == "Opaque provider body."
    assert command["hintStatus"] == "opaque"


@pytest.mark.parametrize(
    "instructions", ["/ review\nKeep literal.", "/review!\nKeep literal."]
)
def test_runtime_command_malformed_token_input_is_literal(instructions: str) -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": instructions,
            "runtime": {"mode": "codex"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["detectionStatus"] == "malformed"
    assert command["recognitionMode"] == "escaped_literal"
    assert command["requiresRuntimeRecognition"] is False
    assert command["command"] == ""
    assert command["instructionBody"] == instructions


def test_runtime_command_escaped_slash_records_literal_metadata() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "\\/review\nTreat this as ordinary text.",
            "runtime": {"mode": "codex"},
        }
    )

    assert snapshot["objective"]["instructions"] == (
        "\\/review\nTreat this as ordinary text."
    )
    command = snapshot["objective"]["runtimeCommand"]
    assert command["detectionStatus"] == "escaped"
    assert command["recognitionMode"] == "escaped_literal"
    assert command["requiresRuntimeRecognition"] is False
    assert command["rawCommand"] == "\\/review"
    assert command["instructionBody"] == "/review\nTreat this as ordinary text."


def test_runtime_command_path_like_input_is_malformed_literal() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/src/app.ts is broken",
            "runtime": {"mode": "codex"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["detectionStatus"] == "malformed"
    assert command["recognitionMode"] == "escaped_literal"
    assert command["requiresRuntimeRecognition"] is False
    assert command["command"] == ""


@pytest.mark.parametrize(
    ("instructions", "raw_command", "instruction_body"),
    [
        ("/", "/", "/"),
        ("/   ", "/", "/   "),
        ("/\nContinue as literal text.", "/", "/\nContinue as literal text."),
    ],
)
def test_runtime_command_slash_only_input_is_malformed_literal(
    instructions: str, raw_command: str, instruction_body: str
) -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": instructions,
            "runtime": {"mode": "codex"},
        }
    )

    assert snapshot["objective"]["instructions"] == instructions
    command = snapshot["objective"]["runtimeCommand"]
    assert command["detectionStatus"] == "malformed"
    assert command["recognitionMode"] == "escaped_literal"
    assert command["requiresRuntimeRecognition"] is False
    assert command["command"] == ""
    assert command["rawCommand"] == raw_command
    assert command["instructionBody"] == instruction_body


def test_runtime_command_unsupported_runtime_does_not_require_recognition() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "/review\nCheck external runtime behavior.",
            "runtime": {"mode": "jules"},
        }
    )

    command = snapshot["objective"]["runtimeCommand"]
    assert command["command"] == "review"
    assert command["detectionStatus"] == "detected"
    assert command["recognitionMode"] == "runtime_does_not_support_slash_commands"
    assert command["requiresRuntimeRecognition"] is False


def test_runtime_command_preview_config_includes_all_passthrough_runtime_ids() -> None:
    config = build_runtime_command_preview_config()

    assert config["runtimes"]["codex"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["codex_cli"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["claude"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["claude_code"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["gemini_cli"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["universal"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["codex_cloud"]["slashCommandPassthrough"] is False


def test_runtime_command_leading_whitespace_is_not_detected() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": " /review\nLeading whitespace keeps this literal.",
            "runtime": {"mode": "codex"},
        }
    )

    assert snapshot["objective"]["instructions"] == (
        " /review\nLeading whitespace keeps this literal."
    )
    assert "runtimeCommand" not in snapshot["objective"]


def test_runtime_command_step_leading_whitespace_preserves_literal_text() -> None:
    snapshot = build_authoritative_task_input_snapshot(
        task_payload={
            "instructions": "Run task.",
            "runtime": {"mode": "codex"},
            "steps": [
                {
                    "id": "step-1",
                    "instructions": " /simplify\nLeading whitespace keeps this literal.",
                }
            ],
        }
    )

    assert snapshot["steps"][0]["instructions"] == (
        " /simplify\nLeading whitespace keeps this literal."
    )
    assert "runtimeCommand" not in snapshot["steps"][0]


def test_runtime_command_rejects_conflicting_objective_metadata() -> None:
    with pytest.raises(TaskContractError, match="task.runtimeCommand conflicts"):
        build_authoritative_task_input_snapshot(
            task_payload={
                "instructions": "/review\nCheck this branch.",
                "runtime": {"mode": "codex"},
                "runtimeCommand": {
                    "kind": "slash_command",
                    "sourcePath": "objective.instructions",
                    "command": "simplify",
                    "detectionStatus": "detected",
                },
            }
        )


def test_runtime_command_rejects_conflicting_step_metadata() -> None:
    with pytest.raises(TaskContractError, match="task.steps\\[0\\].runtimeCommand conflicts"):
        build_authoritative_task_input_snapshot(
            task_payload={
                "instructions": "Run task.",
                "runtime": {"mode": "codex"},
                "steps": [
                    {
                        "id": "step-1",
                        "instructions": "/simplify\nReduce duplication.",
                        "runtimeCommand": {
                            "kind": "slash_command",
                            "sourcePath": "steps[0].instructions",
                            "targetStepId": "different",
                            "command": "simplify",
                            "detectionStatus": "detected",
                        },
                    }
                ],
            }
        )

def test_task_skills_rejects_invalid_values() -> None:
    """T001: Assert structure validation handles edge cases for skills."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="task.skills.sets must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"sets": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task.skills.include must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"include": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task.skills.exclude must be a list"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"exclude": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="task\\.skills\\.materializationMode must be hybrid"):
        TaskExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"materializationMode": "invalid"},
        })

def test_task_step_spec_with_step_skills() -> None:
    """T002: Ensure step.skills parses correctly on TaskStepSpec."""
    raw_payload = {
        "id": "step1",
        "skills": {
            "exclude": ["bad-skill"],
            "materializationMode": "none",
        }
    }
    
    spec = TaskStepSpec.model_validate(raw_payload)
    assert spec.skills is not None
    assert spec.skills.exclude == ["bad-skill"]
    assert spec.skills.materialization_mode == "none"

def test_canonical_task_payload_accepts_legacy_preset_version_keys() -> None:
    payload = CanonicalTaskPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run stored proposal.",
                "authoredPresets": [
                    {
                        "presetId": "runtime-quality-followup",
                        "version": "2026-04-17",
                    }
                ],
                "steps": [
                    {
                        "type": "skill",
                        "instructions": "Implement issue.",
                        "skill": {"id": "moonspec-implement"},
                        "source": {
                            "kind": "preset-derived",
                            "presetId": "runtime-quality-followup",
                            "version": "2026-04-17",
                        },
                    }
                ],
            },
        }
    )

    task = payload.model_dump(by_alias=True, exclude_none=True)["task"]
    assert task["authoredPresets"] == [
        {
            "presetId": "runtime-quality-followup",
            "presetVersion": "2026-04-17",
        }
    ]
    assert task["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetId": "runtime-quality-followup",
        "presetVersion": "2026-04-17",
    }

def test_task_steps_accept_explicit_tool_and_skill_discriminators() -> None:
    spec = TaskExecutionSpec.model_validate(
        {
            "instructions": "Run explicit steps.",
            "steps": [
                {
                    "id": "fetch-issue",
                    "type": "tool",
                    "instructions": "Fetch issue.",
                    "tool": {
                        "id": "jira.get_issue",
                        "version": "1.0.0",
                        "inputs": {"issueKey": "MM-559"},
                    },
                    "source": {
                        "kind": "preset-derived",
                        "presetId": "jira-flow",
                        "presetVersion": "2026-04-24",
                        "includePath": ["root", "fetch"],
                        "originalStepId": "fetch-jira-issue",
                    },
                },
                {
                    "id": "implement",
                    "type": "skill",
                    "instructions": "Implement issue.",
                    "skill": {
                        "id": "moonspec-implement",
                        "args": {"issueKey": "MM-559"},
                    },
                },
            ],
        }
    )

    dumped_steps = [
        step.model_dump(by_alias=True, exclude_none=True) for step in spec.steps
    ]
    assert dumped_steps[0]["type"] == "tool"
    assert dumped_steps[0]["tool"]["id"] == "jira.get_issue"
    assert dumped_steps[0]["source"] == {
        "kind": "preset-derived",
        "presetId": "jira-flow",
        "presetVersion": "2026-04-24",
        "includePath": ["root", "fetch"],
        "originalStepId": "fetch-jira-issue",
    }
    assert dumped_steps[1]["type"] == "skill"
    assert dumped_steps[1]["skill"]["id"] == "moonspec-implement"

@pytest.mark.parametrize(
    "source",
    [
        None,
        {"kind": "detached"},
        {
            "kind": "preset-derived",
            "presetId": "stale-preset",
            "presetVersion": "missing-from-catalog",
        },
        {
            "kind": "preset-include",
            "presetSlug": "parent-flow",
            "presetVersion": "2026-04-24",
            "includePath": ["root", "child"],
            "originalStepId": "child-step",
        },
    ],
)
def test_task_steps_validate_without_resolving_source_provenance(
    source: dict[str, object] | None,
) -> None:
    step: dict[str, object] = {
        "id": "fetch-issue",
        "type": "tool",
        "instructions": "Fetch issue.",
        "tool": {"id": "jira.get_issue", "inputs": {"issueKey": "MM-579"}},
    }
    if source is not None:
        step["source"] = source

    spec = TaskExecutionSpec.model_validate(
        {"instructions": "Run explicit steps.", "steps": [step]}
    )

    dumped = spec.model_dump(by_alias=True, exclude_none=True)["steps"][0]
    assert dumped["type"] == "tool"
    assert dumped["tool"]["id"] == "jira.get_issue"
    if source is None:
        assert "source" not in dumped or dumped["source"] is None
    else:
        assert dumped["source"] == source


def test_task_steps_preserve_detached_template_source_provenance() -> None:
    spec = TaskExecutionSpec.model_validate(
        {
            "instructions": "Run detached preset step.",
            "steps": [
                {
                    "id": "detached-step",
                    "type": "skill",
                    "instructions": "Run the edited preset step.",
                    "skill": {"id": "auto"},
                    "source": {
                        "kind": "detached",
                        "presetSlug": "quality-flow",
                        "presetVersion": "1.0.0",
                        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
                        "originalStepId": "lint-target",
                    },
                }
            ],
        }
    )

    dumped = spec.model_dump(by_alias=True, exclude_none=True)["steps"][0]
    assert dumped["source"] == {
        "kind": "detached",
        "presetSlug": "quality-flow",
        "presetVersion": "1.0.0",
        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
        "originalStepId": "lint-target",
    }


def test_canonical_task_payload_preserves_recursive_preset_source_original_step_id() -> None:
    payload = CanonicalTaskPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run compiled preset.",
                "authoredPresets": [
                    {
                        "presetSlug": "parent-flow",
                        "presetVersion": "1.0.0",
                        "scope": "global",
                        "includePath": ["parent-flow@1.0.0"],
                    },
                    {
                        "presetSlug": "child-checks",
                        "presetVersion": "1.0.0",
                        "alias": "quality",
                        "scope": "global",
                        "includePath": [
                            "parent-flow@1.0.0",
                            "quality:child-checks@1.0.0",
                        ],
                        "inputMapping": {"target": "preset composition"},
                    },
                ],
                "steps": [
                    {
                        "id": "tpl:parent-flow:1.0.0:01:abcdef12",
                        "type": "skill",
                        "instructions": "Lint target.",
                        "skill": {"id": "auto"},
                        "source": {
                            "kind": "preset-derived",
                            "presetSlug": "child-checks",
                            "presetVersion": "1.0.0",
                            "includePath": [
                                "parent-flow@1.0.0",
                                "quality:child-checks@1.0.0",
                            ],
                            "originalStepId": "lint-target",
                        },
                    }
                ],
            },
        }
    )

    task = payload.model_dump(by_alias=True, exclude_none=True)["task"]
    assert task["steps"][0]["source"]["originalStepId"] == "lint-target"
    assert task["authoredPresets"][1]["inputMapping"] == {
        "target": "preset composition"
    }


def test_canonical_task_payload_preserves_detached_template_source_provenance() -> None:
    payload = CanonicalTaskPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run edited preset step.",
                "steps": [
                    {
                        "id": "edited-lint-step",
                        "type": "skill",
                        "instructions": "Edited lint instructions.",
                        "skill": {"id": "auto"},
                        "source": {
                            "kind": "detached",
                            "presetSlug": "quality-flow",
                            "presetVersion": "1.0.0",
                            "includePath": [
                                "root-flow@1.0.0",
                                "quality-flow@1.0.0",
                            ],
                            "originalStepId": "lint-target",
                        },
                    }
                ],
            },
        }
    )

    task = payload.model_dump(by_alias=True, exclude_none=True)["task"]
    assert task["steps"][0]["source"] == {
        "kind": "detached",
        "presetSlug": "quality-flow",
        "presetVersion": "1.0.0",
        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
        "originalStepId": "lint-target",
    }

def test_task_authored_presets_accept_recursive_bindings() -> None:
    payload = CanonicalTaskPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run recursive preset.",
                "authoredPresets": [
                    {
                        "presetSlug": "parent-flow",
                        "presetVersion": "1.0.0",
                        "scope": "global",
                        "includePath": ["parent-flow@1.0.0"],
                    },
                    {
                        "presetSlug": "child-checks",
                        "presetVersion": "1.0.0",
                        "alias": "quality",
                        "scope": "global",
                        "includePath": [
                            "parent-flow@1.0.0",
                            "quality:child-checks@1.0.0",
                        ],
                        "inputMapping": {"target": "preset composition"},
                    },
                ],
                "steps": [
                    {
                        "type": "skill",
                        "instructions": "Run child check.",
                        "skill": {"id": "auto"},
                        "source": {
                            "kind": "preset-derived",
                            "presetSlug": "child-checks",
                            "presetVersion": "1.0.0",
                            "includePath": [
                                "parent-flow@1.0.0",
                                "quality:child-checks@1.0.0",
                            ],
                        },
                    }
                ],
            },
        }
    )

    task = payload.model_dump(by_alias=True, exclude_none=True)["task"]
    assert task["authoredPresets"][1] == {
        "presetSlug": "child-checks",
        "presetVersion": "1.0.0",
        "alias": "quality",
        "includePath": [
            "parent-flow@1.0.0",
            "quality:child-checks@1.0.0",
        ],
        "inputMapping": {"target": "preset composition"},
        "scope": "global",
    }

def test_task_steps_reject_unresolved_preset_include_work() -> None:
    with pytest.raises(ValidationError, match="unresolved preset include"):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Invalid worker payload.",
                "steps": [
                    {
                        "kind": "include",
                        "slug": "child-checks",
                        "version": "1.0.0",
                        "alias": "quality",
                    }
                ],
            }
        )

@pytest.mark.parametrize("step_type", ["preset", "activity", "Activity"])
def test_task_steps_reject_non_executable_step_types(step_type: str) -> None:
    with pytest.raises(ValidationError, match="task\\.steps\\[\\]\\.type"):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Run explicit steps.",
                "steps": [
                    {
                        "type": step_type,
                        "instructions": "This must not execute.",
                        "preset": {
                            "id": "jira.implementation_flow",
                            "inputs": {"issueKey": "MM-559"},
                        },
                    }
                ],
            }
        )

def test_task_steps_reject_conflicting_executable_payloads() -> None:
    with pytest.raises(
        ValidationError,
        match="Tool steps must not include a skill payload",
    ):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Run explicit steps.",
                "steps": [
                    {
                        "type": "tool",
                        "instructions": "Fetch issue.",
                        "tool": {
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-559"},
                        },
                        "skill": {"id": "moonspec-implement", "args": {}},
                    }
                ],
            }
        )

def test_task_steps_reject_skill_step_with_non_skill_tool_payload() -> None:
    with pytest.raises(
        ValidationError,
        match="Skill steps must not include a non-skill tool payload",
    ):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Run explicit steps.",
                "steps": [
                    {
                        "type": "skill",
                        "instructions": "Implement issue.",
                        "tool": {
                            "type": "tool",
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-559"},
                        },
                        "skill": {"id": "moonspec-implement", "args": {}},
                    }
                ],
            }
        )

@pytest.mark.parametrize("field", ["command", "cmd", "script", "shell", "bash"])
def test_task_steps_reject_shell_like_executable_fields(field: str) -> None:
    with pytest.raises(
        ValidationError,
        match="task\\.steps entries may not define task-scoped overrides",
    ):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Run explicit steps.",
                "steps": [
                    {
                        "type": "tool",
                        "instructions": "Run shell-like work.",
                        "tool": {
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-563"},
                        },
                        field: "bash deploy.sh",
                    }
                ],
            }
        )

def test_mm569_accepts_executable_tool_and_skill_payload_fixtures() -> None:
    result = build_canonical_task_view(
        job_type="task",
        payload=task_payload(tool_step(), skill_step()),
    )

    steps = result["task"]["steps"]
    assert steps[0]["type"] == "tool"
    assert steps[0]["tool"]["id"] == "jira.get_issue"
    assert steps[1]["type"] == "skill"
    assert steps[1]["skill"]["id"] == "moonspec-implement"


def test_mm569_rejects_mixed_and_unresolved_preset_runtime_steps() -> None:
    with pytest.raises(TaskContractError, match="Tool steps must not include a skill payload"):
        build_canonical_task_view(
            job_type="task",
            payload=task_payload(mixed_tool_skill_step()),
        )

    with pytest.raises(TaskContractError, match="task\\.steps\\[\\]\\.type"):
        build_canonical_task_view(
            job_type="task",
            payload=task_payload(preset_step()),
        )


def test_mm569_tool_step_required_capabilities_aggregate_into_canonical_required() -> None:
    """MM-569: capability requirements declared on a tool step must propagate into the
    canonical top-level requiredCapabilities so the worker selector can route the job
    to a worker that advertises them (matches existing skill-step behavior)."""
    result = build_canonical_task_view(
        job_type="task",
        payload=task_payload(tool_step()),  # default fixture sets requiredCapabilities=["jira"]
    )

    assert "jira" in result["requiredCapabilities"]


def test_mm569_tool_validation_error_identifies_required_field_path() -> None:
    invalid = tool_step()
    invalid["tool"].pop("id")

    with pytest.raises(TaskContractError) as excinfo:
        build_canonical_task_view(job_type="task", payload=task_payload(invalid))

    assert "task.steps[].tool.id" in str(excinfo.value)
    assert "tool.name" in str(excinfo.value)


@pytest.mark.parametrize(
    "skill_id",
    ["jira-issue-creator", "jira-issue-updater", "jira-pr-verify", "jira-verify"],
)
def test_jira_side_effect_skills_reject_repository_publish_modes(
    skill_id: str,
) -> None:
    with pytest.raises(TaskContractError, match="non-repository skill"):
        resolve_publish_mode_for_skill(skill_id, "pr")

    assert resolve_publish_mode_for_skill(skill_id, None) == "none"
    assert resolve_publish_mode_for_skill(skill_id, "none") == "none"


def test_jira_orchestrate_preset_context_allows_first_step_skill_pr_publish() -> None:
    result = build_canonical_task_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Run Jira Orchestrate for THOR-352.",
                "skill": {"id": "jira-issue-updater"},
                "publish": {"mode": "pr"},
                "steps": [
                    {
                        "id": "tpl:jira-orchestrate:1.0.0:01",
                        "title": "Move Jira issue",
                        "instructions": "Transition THOR-352 to In Progress.",
                        "skill": {"id": "jira-issue-updater", "args": {}},
                    }
                ],
                "appliedStepTemplates": [
                    {
                        "slug": "jira-orchestrate",
                        "version": "1.0.0",
                        "stepIds": ["tpl:jira-orchestrate:1.0.0:01"],
                    }
                ],
            },
        },
    )

    assert result["task"]["publish"]["mode"] == "pr"
    assert "gh" in result["requiredCapabilities"]


def test_effective_task_step_skills_apply_exclusions_without_mutating_task() -> None:
    task_skills = TaskExecutionSpec.model_validate(
        {
            "repository": "test/repo",
            "instructions": "execute",
            "skills": {
                "sets": ["default"],
                "include": [
                    {"name": "baseline", "version": "1.0.0"},
                    {"name": "remove-me", "version": "2.0.0"},
                ],
                "exclude": ["legacy"],
                "materializationMode": "hybrid",
            },
        }
    ).skills
    step_skills = TaskStepSpec.model_validate(
        {
            "id": "step1",
            "skills": {
                "sets": ["python"],
                "include": [{"name": "step-only"}],
                "exclude": ["remove-me"],
                "materializationMode": "none",
            },
        }
    ).skills

    effective = build_effective_task_skill_selectors(task_skills, step_skills)

    assert effective is not None
    assert effective.sets == ["default", "python"]
    assert [(item.name, item.version) for item in effective.include or []] == [
        ("baseline", "1.0.0"),
        ("step-only", None),
    ]
    assert effective.exclude == ["legacy", "remove-me"]
    assert effective.materialization_mode == "none"
    assert [item.name for item in task_skills.include or []] == [
        "baseline",
        "remove-me",
    ]

def test_effective_task_step_skills_returns_none_for_empty_intent() -> None:
    assert build_effective_task_skill_selectors(None, None) is None

def test_task_input_attachments_preserve_objective_and_step_targets() -> None:
    """MM-367: objective and step refs remain distinct canonical fields."""

    canonical = build_canonical_task_view(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Inspect the images.",
                "inputAttachments": [
                    {
                        "artifactId": "art-objective",
                        "filename": "same-name.png",
                        "contentType": "image/png",
                        "sizeBytes": 10,
                    }
                ],
                "steps": [
                    {
                        "instructions": "Inspect the step image.",
                        "inputAttachments": [
                            {
                                "artifactId": "art-step",
                                "filename": "same-name.png",
                                "contentType": "image/png",
                                "sizeBytes": 20,
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert canonical["task"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["task"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art-step",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]

def test_task_input_attachments_accept_field_name_keys() -> None:
    """MM-375: pre-validation preserves populate_by_name attachment payloads."""

    canonical = build_canonical_task_view(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "targetRuntime": "codex",
            "task": {
                "instructions": "Inspect the images.",
                "input_attachments": [
                    {
                        "artifact_id": "art-objective",
                        "filename": "objective.png",
                        "content_type": "image/png",
                        "size_bytes": 10,
                    }
                ],
                "steps": [
                    {
                        "id": "review-step",
                        "instructions": "Inspect the step image.",
                        "input_attachments": [
                            {
                                "artifact_id": "art-step",
                                "filename": "step.png",
                                "content_type": "image/png",
                                "size_bytes": 20,
                            }
                        ],
                    }
                ],
            },
        },
    )

    assert canonical["task"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["task"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art-step",
            "filename": "step.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]

@pytest.mark.parametrize(
    "attachment",
    [
        {"filename": "missing-id.png", "contentType": "image/png", "sizeBytes": 10},
        {
            "artifactId": "art-inline",
            "filename": "inline.png",
            "contentType": "image/png",
            "sizeBytes": 10,
            "dataUrl": "data:image/png;base64,AAAA",
        },
        {
            "artifactId": "art-data-filename",
            "filename": "data:image/png;base64,AAAA",
            "contentType": "image/png",
            "sizeBytes": 10,
        },
    ],
)
def test_task_input_attachments_reject_incomplete_or_embedded_data(
    attachment: dict[str, object],
) -> None:
    """MM-367: refs stay compact and cannot carry inline image payloads."""

    with pytest.raises(ValidationError):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": [attachment],
            }
        )

def test_task_input_attachment_validation_error_carries_objective_diagnostic() -> None:
    """MM-375: validation failures expose target-aware diagnostic evidence."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": [
                    {
                        "artifactId": "art-inline",
                        "filename": "inline.png",
                        "contentType": "image/png",
                        "sizeBytes": 10,
                        "dataUrl": "data:image/png;base64,AAAA",
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "objective",
        "artifactId": "art-inline",
        "filename": "inline.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachment_validation_diagnostic_accepts_field_names() -> None:
    """MM-375: field-name payloads still produce canonical diagnostic keys."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "input_attachments": [
                    {
                        "artifact_id": "art-inline",
                        "filename": "inline.png",
                        "content_type": "image/png",
                        "size_bytes": 10,
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "objective",
        "artifactId": "art-inline",
        "filename": "inline.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachment_validation_error_carries_step_diagnostic() -> None:
    """MM-375: step validation failures identify the affected step target."""

    with pytest.raises(ValidationError) as exc_info:
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "steps": [
                    {
                        "id": "review-step",
                        "instructions": "Inspect step image.",
                        "inputAttachments": [
                            {
                                "artifactId": "art-step",
                                "filename": "step.png",
                                "contentType": "image/png",
                                "sizeBytes": 10,
                                "dataUrl": "data:image/png;base64,AAAA",
                            }
                        ],
                    }
                ],
            }
        )

    error = exc_info.value.errors()[0]["ctx"]["error"]
    assert error.diagnostic == {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": "step",
        "stepRef": "review-step",
        "artifactId": "art-step",
        "filename": "step.png",
        "contentType": "image/png",
        "sizeBytes": 10,
        "error": "inputAttachments entries must not include embedded image data",
    }

def test_task_input_attachments_must_be_lists() -> None:
    """MM-367: canonical attachment fields are arrays."""

    with pytest.raises(ValidationError, match="task.inputAttachments must be a list"):
        TaskExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": {
                    "artifactId": "art-objective",
                    "filename": "objective.png",
                    "contentType": "image/png",
                    "sizeBytes": 10,
                },
            }
        )


# --- MM-638: Recovery / Resume contract type tests ---

_VALID_RESUME_BLOCK = {
    "kind": "resume_from_failed_step",
    "sourceWorkflowId": "mm:abc123",
    "sourceRunId": "run-1",
    "failedStepId": "step-3",
    "resumeCheckpointRef": "art_ckpt_abc",
    "taskInputSnapshotRef": "art_snap_abc",
}

_BASE_TASK_PAYLOAD = {
    "repository": "test/repo",
    "task": {"instructions": "Do work"},
}


def _canonical_task_payload(task_overrides: dict) -> dict:
    payload = dict(_BASE_TASK_PAYLOAD)
    payload["task"] = {**_BASE_TASK_PAYLOAD["task"], **task_overrides}
    return payload


# FR-001: TaskRecoveryKind must accept exactly three values

def test_fr001_task_recovery_kind_accepts_valid_literals() -> None:
    """MM-638 FR-001: Each canonical recovery kind is accepted."""
    for kind in ("exact_full_rerun", "edited_full_retry", "resume_from_failed_step"):
        prov = TaskRecoveryProvenance.model_validate(
            {"kind": kind, "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
        )
        assert prov.kind == kind


def test_fr001_task_recovery_kind_rejects_invalid_literal() -> None:
    """MM-638 FR-001: Values outside the three canonical literals are rejected."""
    with pytest.raises(ValidationError):
        TaskRecoveryProvenance.model_validate(
            {"kind": "unknown_kind", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
        )


# FR-002: TaskRecoveryProvenance required/optional fields

def test_fr002_recovery_provenance_requires_non_empty_source_ids() -> None:
    """MM-638 FR-002: sourceWorkflowId and sourceRunId must be non-empty."""
    with pytest.raises(ValidationError):
        TaskRecoveryProvenance.model_validate(
            {"kind": "exact_full_rerun", "sourceWorkflowId": "", "sourceRunId": "r1"}
        )
    with pytest.raises(ValidationError):
        TaskRecoveryProvenance.model_validate(
            {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": ""}
        )


def test_fr002_recovery_provenance_optional_fields_absent_is_valid() -> None:
    """MM-638 FR-002: requestedBy and requestedAt are optional."""
    prov = TaskRecoveryProvenance.model_validate(
        {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
    )
    assert prov.requested_by is None
    assert prov.requested_at is None


# FR-003: ResumeFromFailedStepRef required/optional fields

def test_fr003_resume_ref_requires_non_empty_required_fields() -> None:
    """MM-638 FR-003: All required ResumeFromFailedStepRef fields must be non-empty."""
    for empty_field in ("sourceWorkflowId", "sourceRunId", "failedStepId",
                        "resumeCheckpointRef", "taskInputSnapshotRef"):
        bad = dict(_VALID_RESUME_BLOCK)
        bad[empty_field] = ""
        with pytest.raises(ValidationError):
            ResumeFromFailedStepRef.model_validate(bad)


def test_fr003_resume_ref_optional_fields_absent_is_valid() -> None:
    """MM-638 FR-003: failedStepAttempt, planRef, planDigest are optional."""
    ref = ResumeFromFailedStepRef.model_validate(_VALID_RESUME_BLOCK)
    assert ref.failed_step_attempt is None
    assert ref.plan_ref is None
    assert ref.plan_digest is None


# FR-004/005: TaskExecutionSpec accepts recovery and resume as optional fields

def test_fr004_fr005_plain_task_unaffected_by_new_fields() -> None:
    """MM-638 FR-004/005: A plain task without recovery/resume is accepted and unaffected."""
    spec = TaskExecutionSpec.model_validate({"instructions": "Do work"})
    assert spec.recovery is None
    assert spec.resume is None
    assert spec.depends_on is None


def test_fr004_recovery_field_accepted_on_task_execution_spec() -> None:
    """MM-638 FR-004: recovery field is accepted on TaskExecutionSpec."""
    spec = TaskExecutionSpec.model_validate({
        "instructions": "Retry",
        "recovery": {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"},
    })
    assert spec.recovery is not None
    assert spec.recovery.kind == "exact_full_rerun"


# FR-006: resume_from_failed_step without resume block → error

def test_fr006_resume_from_failed_step_without_resume_block_is_rejected() -> None:
    """MM-638 FR-006: Missing resume block with resume_from_failed_step recovery kind raises TaskContractError."""
    with pytest.raises(TaskContractError, match="task.resume is required"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "resume_from_failed_step",
                    "sourceWorkflowId": "mm:x",
                    "sourceRunId": "r1",
                },
            }),
        )


# FR-007: resume block without matching recovery.kind → error

def test_fr007_resume_block_without_matching_recovery_kind_is_rejected() -> None:
    """MM-638 FR-007: resume block paired with wrong recovery.kind raises TaskContractError."""
    with pytest.raises(TaskContractError, match="resume_from_failed_step"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "exact_full_rerun",
                    "sourceWorkflowId": "mm:x",
                    "sourceRunId": "r1",
                },
                "resume": _VALID_RESUME_BLOCK,
            }),
        )


def test_fr007_resume_block_without_any_recovery_is_rejected() -> None:
    """MM-638 FR-007: resume block with no recovery field raises TaskContractError."""
    with pytest.raises(TaskContractError, match="task.recovery must be present"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({"resume": _VALID_RESUME_BLOCK}),
        )


# FR-008: exact_full_rerun and edited_full_retry with/without source IDs

def test_fr008_exact_full_rerun_accepted_with_source_ids() -> None:
    """MM-638 FR-008: exact_full_rerun with sourceWorkflowId and sourceRunId is accepted."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "exact_full_rerun",
                "sourceWorkflowId": "mm:abc",
                "sourceRunId": "run-2",
            },
        }),
    )
    assert result["task"]["recovery"]["kind"] == "exact_full_rerun"
    assert result["task"].get("resume") is None


def test_fr008_edited_full_retry_accepted_with_source_ids() -> None:
    """MM-638 FR-008: edited_full_retry with sourceWorkflowId and sourceRunId is accepted."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "edited_full_retry",
                "sourceWorkflowId": "mm:abc",
                "sourceRunId": "run-3",
            },
        }),
    )
    assert result["task"]["recovery"]["kind"] == "edited_full_retry"


def test_fr008_exact_full_rerun_with_resume_is_rejected() -> None:
    """MM-638 FR-008: exact_full_rerun paired with a resume block raises TaskContractError."""
    with pytest.raises(TaskContractError, match="resume_from_failed_step"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "exact_full_rerun",
                    "sourceWorkflowId": "mm:x",
                    "sourceRunId": "r1",
                },
                "resume": _VALID_RESUME_BLOCK,
            }),
        )


def test_fr008_edited_full_retry_with_resume_is_rejected() -> None:
    """MM-638 FR-008: edited_full_retry paired with a resume block raises TaskContractError."""
    with pytest.raises(TaskContractError, match="resume_from_failed_step"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "edited_full_retry",
                    "sourceWorkflowId": "mm:x",
                    "sourceRunId": "r1",
                },
                "resume": _VALID_RESUME_BLOCK,
            }),
        )


def test_mm644_edited_full_retry_requires_pinned_source_run_ids() -> None:
    """MM-644 FR-010: edited full retry provenance pins the source workflow and run."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "instructions": "MM-644 edited retry instructions.",
            "recovery": {
                "kind": "edited_full_retry",
                "sourceWorkflowId": "mm:failed-source",
                "sourceRunId": "run-source",
            },
        }),
    )

    recovery = result["task"]["recovery"]
    assert recovery["kind"] == "edited_full_retry"
    assert recovery["sourceWorkflowId"] == "mm:failed-source"
    assert recovery["sourceRunId"] == "run-source"


def test_mm644_edited_full_retry_rejects_resume_pairing() -> None:
    """MM-644 FR-009: edited full retry must not carry failed-step Resume refs."""
    with pytest.raises(TaskContractError, match="resume_from_failed_step"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "instructions": "MM-644 edited retry instructions.",
                "recovery": {
                    "kind": "edited_full_retry",
                    "sourceWorkflowId": "mm:failed-source",
                    "sourceRunId": "run-source",
                },
                "resume": _VALID_RESUME_BLOCK,
            }),
        )


# FR-009: dependsOn list preserved verbatim; empty list normalized to None

def test_fr009_depends_on_preserved_verbatim() -> None:
    """MM-638 FR-009: dependsOn list is accepted and preserved verbatim."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "dependsOn": ["mm:workflow-1", "mm:workflow-2"],
        }),
    )
    assert result["task"]["dependsOn"] == ["mm:workflow-1", "mm:workflow-2"]


def test_fr009_empty_depends_on_normalized_to_none() -> None:
    """MM-638 FR-009: Empty dependsOn list is normalized to None."""
    spec = TaskExecutionSpec.model_validate({
        "instructions": "Work",
        "dependsOn": [],
    })
    assert spec.depends_on is None


# FR-010/011: task.git.branch is the canonical field; targetBranch is stripped.

def test_fr010_branch_is_canonical_authored_field() -> None:
    """MM-638 FR-010: task.git.branch is accepted and present in canonical output."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({"git": {"branch": "feature/my-branch"}}),
    )
    assert result["task"]["git"]["branch"] == "feature/my-branch"


def test_mm668_target_branch_is_not_active_authored_branch_input() -> None:
    """MM-668: targetBranch must not be normalized into active authored branch."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "git": {"targetBranch": "feature/legacy"},
        }),
    )
    assert result["task"]["git"]["branch"] is None
    assert "targetBranch" not in result["task"]["git"]


# SC-001: Full resume_from_failed_step acceptance scenario

def test_sc001_well_formed_resume_payload_accepted() -> None:
    """MM-638 SC-001: A complete resume_from_failed_step payload is accepted and preserved."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "resume_from_failed_step",
                "sourceWorkflowId": "mm:abc123",
                "sourceRunId": "run-1",
            },
            "resume": _VALID_RESUME_BLOCK,
        }),
    )
    assert result["task"]["recovery"]["kind"] == "resume_from_failed_step"
    assert result["task"]["resume"]["failedStepId"] == "step-3"
    assert result["task"]["resume"]["resumeCheckpointRef"] == "art_ckpt_abc"
    assert result["task"]["resume"]["taskInputSnapshotRef"] == "art_snap_abc"


def test_sc001_resume_source_workflow_id_must_match_recovery() -> None:
    """MM-638: recovery and resume must pin the same source workflow."""
    with pytest.raises(TaskContractError, match="sourceWorkflowId"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "resume_from_failed_step",
                    "sourceWorkflowId": "mm:abc123",
                    "sourceRunId": "run-1",
                },
                "resume": {
                    **_VALID_RESUME_BLOCK,
                    "sourceWorkflowId": "mm:other",
                },
            }),
        )


def test_sc001_resume_source_run_id_must_match_recovery() -> None:
    """MM-638: recovery and resume must pin the same source run."""
    with pytest.raises(TaskContractError, match="sourceRunId"):
        build_canonical_task_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "resume_from_failed_step",
                    "sourceWorkflowId": "mm:abc123",
                    "sourceRunId": "run-1",
                },
                "resume": {
                    **_VALID_RESUME_BLOCK,
                    "sourceRunId": "run-2",
                },
            }),
        )


# Edge cases

def test_edge_case_resume_checkpoint_ref_empty_is_rejected() -> None:
    """MM-638 edge case: resumeCheckpointRef empty string is rejected."""
    bad_resume = {**_VALID_RESUME_BLOCK, "resumeCheckpointRef": ""}
    with pytest.raises(ValidationError):
        ResumeFromFailedStepRef.model_validate(bad_resume)


def test_edge_case_branch_and_starting_branch_both_preserved() -> None:
    """MM-638 edge case: branch and startingBranch are distinct fields and both preserved."""
    result = build_canonical_task_view(
        job_type="task",
        payload=_canonical_task_payload({
            "git": {"branch": "main", "startingBranch": "sha-abc123"},
        }),
    )
    git = result["task"]["git"]
    assert git["branch"] == "main"
    assert git["startingBranch"] == "sha-abc123"
