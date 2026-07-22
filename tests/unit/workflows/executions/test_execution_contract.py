from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.workflows.executions import execution_contract as execution_contract_module
from moonmind.workflows.executions.execution_contract import (
    build_canonical_workflow_view,
    build_authoritative_workflow_input_snapshot,
    build_effective_workflow_skill_selectors,
    build_runtime_command_preview_config,
    CanonicalWorkflowExecutionPayload,
    ResumeFromFailedStepRef,
    SUPPORTED_PUBLISH_MODES,
    is_auto_publish_capable_skill,
    reject_workflow_capability_identity_versions,
    resolve_publish_mode_for_skill,
    strip_workflow_capability_identity_versions,
    WorkflowContractError,
    WorkflowExecutionSpec,
    WorkflowRecoveryProvenance,
    WorkflowStepSpec,
)
from tests.helpers.step_type_payloads import (
    mixed_tool_skill_step,
    preset_step,
    skill_step,
    task_payload,
    tool_step,
)

def test_task_skills_accepts_valid_properties() -> None:
    """T001: Ensure workflow.skills structures successfully marshal."""
    raw_payload = {
        "repository": "test/repo",
        "instructions": "execute",
        "skills": {
            "sets": ["default", "python"],
            "include": [
                {"name": "test-skill"},
                {"name": "unversioned"},
            ],
            "exclude": ["legacy"],
            "materializationMode": "hybrid",
        },
    }
    
    spec = WorkflowExecutionSpec.model_validate(raw_payload)
    
    assert spec.skills is not None
    assert spec.skills.sets == ["default", "python"]
    assert len(spec.skills.include) == 2
    assert spec.skills.include[0].name == "test-skill"
    assert spec.skills.include[1].name == "unversioned"
    assert spec.skills.exclude == ["legacy"]
    assert spec.skills.materialization_mode == "hybrid"

def test_task_skills_reject_semantic_version_fields() -> None:
    with pytest.raises(ValidationError, match="semantic versions"):
        WorkflowExecutionSpec.model_validate(
            {
                "repository": "test/repo",
                "instructions": "execute",
                "skills": {"include": [{"name": "test-skill", "version": "1.0.0"}]},
            }
        )

    with pytest.raises(ValidationError, match="semantic versions"):
        WorkflowExecutionSpec.model_validate(
            {
                "repository": "test/repo",
                "instructions": "execute",
                "skills": {"include": [{"name": "test-skill:1.0.0"}]},
            }
        )


def test_workflow_capability_identity_rejector_blocks_nested_skill_versions() -> None:
    payload = {
        "skills": {
            "include": [
                {"name": "pr-resolver", "version": "1.0.0", "skillVersion": "1.0.0"},
            ],
        },
    }

    with pytest.raises(WorkflowContractError, match="semantic versions"):
        reject_workflow_capability_identity_versions(payload)


def test_workflow_capability_identity_rejector_blocks_step_source_versions() -> None:
    payload = {
        "steps": [
            {
                "instructions": "Run preset-derived step.",
                "source": {"slug": "jira-implement", "presetVersion": "1.0.0"},
            },
        ],
    }

    with pytest.raises(
        WorkflowContractError,
        match=r"workflow\.steps\[0\]\.source",
    ):
        reject_workflow_capability_identity_versions(payload)


def test_workflow_capability_identity_rejector_blocks_snake_case_nested_presets() -> None:
    payload = {
        "task_template": {
            "slug": "jira-orchestrate",
            "authored_presets": [
                {"presetSlug": "jira-implement", "presetVersion": "1.0.0"},
            ],
            "applied_step_templates": [
                {"slug": "jira-implement", "version": "1.0.0"},
            ],
        },
    }

    with pytest.raises(
        WorkflowContractError,
        match=r"workflow\.task_template\.authored_presets\[0\]",
    ):
        reject_workflow_capability_identity_versions(payload)


def test_workflow_capability_identity_rejector_blocks_snake_case_applied_templates() -> None:
    payload = {
        "task_template": {
            "slug": "jira-orchestrate",
            "applied_step_templates": [
                {"slug": "jira-implement", "version": "1.0.0"},
            ],
        },
    }

    with pytest.raises(
        WorkflowContractError,
        match=r"workflow\.task_template\.applied_step_templates\[0\]",
    ):
        reject_workflow_capability_identity_versions(payload)


def test_workflow_capability_identity_sanitizer_strips_nested_skill_versions() -> None:
    payload = {
        "skills": {
            "include": [
                {"name": "pr-resolver", "version": "1.0.0", "skillVersion": "1.0.0"},
            ],
            "exclude": [
                {"name": "legacy", "version": "0.1.0"},
                "old-skill",
            ],
        },
        "steps": [
            {
                "instructions": "Run targeted skill.",
                "skills": {
                    "include": [
                        {
                            "name": "fix-ci",
                            "version": "2.0.0",
                            "skillVersion": "2.0.0",
                        },
                    ],
                },
            },
        ],
    }

    sanitized = strip_workflow_capability_identity_versions(payload)

    assert sanitized["skills"]["include"] == [{"name": "pr-resolver"}]
    assert sanitized["skills"]["exclude"] == [{"name": "legacy"}, "old-skill"]
    assert sanitized["steps"][0]["skills"]["include"] == [{"name": "fix-ci"}]


def test_mm842_authoritative_snapshot_omits_authored_step_graph_metadata() -> None:
    snapshot = build_authoritative_workflow_input_snapshot(
        task_payload={
            "instructions": "Run MM-842 as ordered steps.",
            "steps": [
                {
                    "id": "plan",
                    "instructions": "Plan the change.",
                    "dependsOn": [],
                    "dependencies": [],
                },
                {
                    "id": "implement",
                    "instructions": "Implement the change.",
                    "dependsOn": ["plan"],
                    "dependencies": ["plan"],
                },
            ],
        },
        dependency_declarations=["mm:upstream-workflow"],
    )

    assert [step["id"] for step in snapshot["steps"]] == ["plan", "implement"]
    assert [step["instructions"] for step in snapshot["steps"]] == [
        "Plan the change.",
        "Implement the change.",
    ]
    assert snapshot["finalSubmittedOrder"] == [
        {"stepId": "plan", "ordinal": 0},
        {"stepId": "implement", "ordinal": 1},
    ]
    assert "dependencies" not in snapshot["steps"][0]
    assert "dependencies" not in snapshot["steps"][1]
    assert "dependsOn" not in snapshot["steps"][0]
    assert "dependsOn" not in snapshot["steps"][1]
    assert snapshot["dependencyDeclarations"] == ["mm:upstream-workflow"]


def test_authoritative_snapshot_detects_jira_key_without_serializing_metadata() -> None:
    class Unserializable:
        pass

    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
        "hintCatalogVersion": "2026-05-13",
        "detectionPhase": "submit",
    }


def test_authoritative_snapshot_records_step_runtime_command_metadata() -> None:
    snapshot = build_authoritative_workflow_input_snapshot(
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

def test_authoritative_snapshot_records_task_and_step_runtime_command_provenance() -> None:
    snapshot = build_authoritative_workflow_input_snapshot(
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
    assert objective_command["hintCatalogVersion"] == "2026-05-13"
    assert objective_command["detectionPhase"] == "submit"
    assert step_command["hintCatalogVersion"] == "2026-05-13"
    assert step_command["detectionPhase"] == "submit"


def test_mm786_task_steps_accept_runtime_selection_and_snapshot_it() -> None:
    payload = {
        "instructions": "Run MM-786 as a multi-step task.",
        "runtime": {"mode": "codex_cli", "model": "gpt-5.4", "effort": "medium"},
        "steps": [
            {
                "id": "cheap-analysis",
                "instructions": "Analyze with the cheaper runtime.",
                "runtime": {
                    "mode": "claude_code",
                    "model": "claude-haiku-test",
                    "effort": "low",
                },
            }
        ],
    }

    spec = WorkflowExecutionSpec.model_validate(payload)

    assert spec.steps[0].runtime is not None
    assert spec.steps[0].runtime.mode == "claude_code"
    assert spec.steps[0].runtime.model == "claude-haiku-test"
    assert spec.steps[0].runtime.effort == "low"

    snapshot = build_authoritative_workflow_input_snapshot(task_payload=payload)

    assert snapshot["steps"][0]["runtime"] == {
        "mode": "claude_code",
        "model": "claude-haiku-test",
        "effort": "low",
    }


def test_github_3453_canonical_contract_preserves_omnigent_selection() -> None:
    payload = {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "omnigent",
        "omnigent": {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
            "agent": {"harnessOverride": "codex-native"},
            "capture": {"required": True},
        },
        "workflow": {
            "instructions": "Run through the selected Codex OAuth profile.",
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": "codex-oauth-profile",
            },
            "steps": [
                {
                    "id": "direct-review",
                    "instructions": "Review directly.",
                    "runtime": {"mode": "codex_cli"},
                },
                {
                    "id": "omnigent-implement",
                    "instructions": "Implement through Omnigent.",
                    "runtime": {
                        "mode": "omnigent",
                        "executionProfileRef": "codex-oauth-profile",
                        "omnigent": {
                            "executionTargetRef": "omnigent-codex@1",
                            "launchPolicyRef": "codex-on-demand@1",
                        },
                    },
                },
            ],
        },
    }

    canonical = build_canonical_workflow_view(job_type="task", payload=payload)

    assert canonical["targetRuntime"] == "omnigent"
    assert canonical["workflow"]["runtime"]["mode"] == "omnigent"
    assert (
        canonical["workflow"]["runtime"]["executionProfileRef"]
        == "codex-oauth-profile"
    )
    assert canonical["omnigent"] == payload["omnigent"]
    assert canonical["workflow"]["steps"][0]["runtime"]["mode"] == "codex_cli"
    assert canonical["workflow"]["steps"][1]["runtime"]["mode"] == "omnigent"

    snapshot = build_authoritative_workflow_input_snapshot(
        task_payload=canonical["workflow"],
        target_runtime=canonical["targetRuntime"],
    )
    assert snapshot["runtime"]["mode"] == "omnigent"
    assert snapshot["runtime"]["executionProfileRef"] == "codex-oauth-profile"
    assert snapshot["steps"][1]["runtime"]["omnigent"] == {
        "executionTargetRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
    }


def test_runtime_command_unknown_valid_commands_are_opaque_not_rejected() -> None:
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    assert config["runtimes"]["universal"]["slashCommandPassthrough"] is True
    assert config["runtimes"]["codex_cloud"]["slashCommandPassthrough"] is False


def test_runtime_command_leading_whitespace_is_not_detected() -> None:
    snapshot = build_authoritative_workflow_input_snapshot(
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
    snapshot = build_authoritative_workflow_input_snapshot(
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
    with pytest.raises(WorkflowContractError, match="task.runtimeCommand conflicts"):
        build_authoritative_workflow_input_snapshot(
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


def test_runtime_command_rejects_supplied_semantic_capability_version() -> None:
    marker_terms = ("runtime", "capability", "version")
    removed_marker = (
        marker_terms[0]
        + marker_terms[1][:1].upper()
        + marker_terms[1][1:]
        + marker_terms[2][:1].upper()
        + marker_terms[2][1:]
    )
    with pytest.raises(WorkflowContractError, match="remove removed runtime marker"):
        build_authoritative_workflow_input_snapshot(
            task_payload={
                "instructions": "/review\nCheck this branch.",
                "runtime": {"mode": "codex"},
                "runtimeCommand": {
                    "kind": "slash_command",
                    "sourcePath": "objective.instructions",
                    "command": "review",
                    "rawCommand": "/review",
                    "detectionStatus": "detected",
                    "recognitionMode": "hinted_runtime_passthrough",
                    removed_marker: "2026-05-13",
                },
            }
        )


def test_runtime_command_rejects_conflicting_step_metadata() -> None:
    with pytest.raises(WorkflowContractError, match="workflow.steps\\[0\\].runtimeCommand conflicts"):
        build_authoritative_workflow_input_snapshot(
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
    with pytest.raises(ValidationError, match="workflow.skills.sets must be a list"):
        WorkflowExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"sets": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="workflow.skills.include must be a list"):
        WorkflowExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"include": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="workflow.skills.exclude must be a list"):
        WorkflowExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"exclude": "not-a-list"},
        })

    with pytest.raises(ValidationError, match="workflow\\.skills\\.materializationMode must be hybrid"):
        WorkflowExecutionSpec.model_validate({
            "repository": "test/repo",
            "instructions": "foo",
            "skills": {"materializationMode": "invalid"},
        })

def test_task_step_spec_with_step_skills() -> None:
    """T002: Ensure step.skills parses correctly on WorkflowStepSpec."""
    raw_payload = {
        "id": "step1",
        "skills": {
            "exclude": ["bad-skill"],
            "materializationMode": "none",
        }
    }
    
    spec = WorkflowStepSpec.model_validate(raw_payload)
    assert spec.skills is not None
    assert spec.skills.exclude == ["bad-skill"]
    assert spec.skills.materialization_mode == "none"

def test_canonical_task_payload_accepts_legacy_preset_version_keys() -> None:
    payload = CanonicalWorkflowExecutionPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run stored proposal.",
                "authoredPresets": [
                    {
                        "presetId": "runtime-quality-followup",
                        "presetVersion": "1.0.0",
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
                            "version": "1.0.0",
                        },
                    }
                ],
            },
        }
    )

    task = payload.model_dump(by_alias=True, exclude_none=True)["workflow"]
    assert task["authoredPresets"] == [
        {
            "presetId": "runtime-quality-followup",
        }
    ]
    assert task["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetId": "runtime-quality-followup",
    }

def test_task_steps_accept_explicit_tool_and_skill_discriminators() -> None:
    spec = WorkflowExecutionSpec.model_validate(
        {
            "instructions": "Run explicit steps.",
            "steps": [
                {
                    "id": "fetch-issue",
                    "type": "tool",
                    "instructions": "Fetch issue.",
                    "tool": {
                        "id": "jira.get_issue",
                        "inputs": {"issueKey": "MM-559"},
                    },
                    "source": {
                        "kind": "preset-derived",
                        "presetId": "jira-flow",
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
    assert dumped_steps[0]["tool"] == {
        "id": "jira.get_issue",
        "inputs": {"issueKey": "MM-559"},
    }
    assert dumped_steps[0]["source"] == {
        "kind": "preset-derived",
        "presetId": "jira-flow",
        "includePath": ["root", "fetch"],
        "originalStepId": "fetch-jira-issue",
    }
    assert dumped_steps[1]["type"] == "skill"
    assert dumped_steps[1]["skill"] == {
        "id": "moonspec-implement",
        "args": {"issueKey": "MM-559"},
    }

@pytest.mark.parametrize(
    "source",
    [
        None,
        {"kind": "detached"},
        {
            "kind": "preset-derived",
            "presetId": "stale-preset",
        },
        {
            "kind": "preset-include",
            "presetSlug": "parent-flow",
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

    spec = WorkflowExecutionSpec.model_validate(
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
    spec = WorkflowExecutionSpec.model_validate(
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
        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
        "originalStepId": "lint-target",
    }


def test_canonical_task_payload_preserves_recursive_preset_source_original_step_id() -> None:
    payload = CanonicalWorkflowExecutionPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run compiled preset.",
                "authoredPresets": [
                    {
                        "presetSlug": "parent-flow",
                        "scope": "global",
                        "includePath": ["parent-flow@1.0.0"],
                    },
                    {
                        "presetSlug": "child-checks",
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

    task = payload.model_dump(by_alias=True, exclude_none=True)["workflow"]
    assert task["steps"][0]["source"]["originalStepId"] == "lint-target"
    assert task["authoredPresets"][1]["inputMapping"] == {
        "target": "preset composition"
    }


def test_canonical_task_payload_preserves_detached_template_source_provenance() -> None:
    payload = CanonicalWorkflowExecutionPayload.model_validate(
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

    task = payload.model_dump(by_alias=True, exclude_none=True)["workflow"]
    assert task["steps"][0]["source"] == {
        "kind": "detached",
        "presetSlug": "quality-flow",
        "includePath": ["root-flow@1.0.0", "quality-flow@1.0.0"],
        "originalStepId": "lint-target",
    }

def test_task_authored_presets_accept_recursive_bindings() -> None:
    payload = CanonicalWorkflowExecutionPayload.model_validate(
        {
            "repository": "Moon/Repo",
            "task": {
                "instructions": "Run recursive preset.",
                "authoredPresets": [
                    {
                        "presetSlug": "parent-flow",
                        "scope": "global",
                        "includePath": ["parent-flow@1.0.0"],
                    },
                    {
                        "presetSlug": "child-checks",
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

    task = payload.model_dump(by_alias=True, exclude_none=True)["workflow"]
    assert task["authoredPresets"][1] == {
        "presetSlug": "child-checks",
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
        WorkflowExecutionSpec.model_validate(
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
    with pytest.raises(ValidationError, match="workflow\\.steps\\[\\]\\.type"):
        WorkflowExecutionSpec.model_validate(
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
        WorkflowExecutionSpec.model_validate(
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
        WorkflowExecutionSpec.model_validate(
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
        match="workflow\\.steps entries may not define workflow-scoped overrides",
    ):
        WorkflowExecutionSpec.model_validate(
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

def test_task_step_accepts_per_step_runtime_selection() -> None:
    spec = WorkflowStepSpec.model_validate(
        {
            "id": "review",
            "instructions": "Review with a cheaper model.",
            "runtime": {
                "mode": "claude_code",
                "model": "claude-haiku-test",
                "effort": "low",
                "profileId": "claude-default",
            },
        }
    )

    assert spec.runtime is not None
    assert spec.runtime.mode == "claude_code"
    assert spec.runtime.model == "claude-haiku-test"
    assert spec.runtime.effort == "low"
    assert spec.runtime.provider_profile == "claude-default"

def test_mm569_accepts_executable_tool_and_skill_payload_fixtures() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload=task_payload(tool_step(), skill_step()),
    )

    steps = result["workflow"]["steps"]
    assert steps[0]["type"] == "tool"
    assert steps[0]["tool"]["id"] == "jira.get_issue"
    assert steps[1]["type"] == "skill"
    assert steps[1]["skill"]["id"] == "moonspec-implement"


def test_mm569_rejects_mixed_and_unresolved_preset_runtime_steps() -> None:
    with pytest.raises(WorkflowContractError, match="Tool steps must not include a skill payload"):
        build_canonical_workflow_view(
            job_type="task",
            payload=task_payload(mixed_tool_skill_step()),
        )

    with pytest.raises(WorkflowContractError, match="workflow\\.steps\\[\\]\\.type"):
        build_canonical_workflow_view(
            job_type="task",
            payload=task_payload(preset_step()),
        )


def test_mm569_tool_step_required_capabilities_aggregate_into_canonical_required() -> None:
    """MM-569: capability requirements declared on a tool step must propagate into the
    canonical top-level requiredCapabilities so the worker selector can route the job
    to a worker that advertises them (matches existing skill-step behavior)."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=task_payload(tool_step()),  # default fixture sets requiredCapabilities=["jira"]
    )

    assert "jira" in result["requiredCapabilities"]


def test_required_capabilities_do_not_derive_git_without_repository_context() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "targetRuntime": "codex_cli",
            "task": {
                "instructions": "Fetch the Jira issue only.",
                "publish": {"mode": "none"},
                "steps": [tool_step()],
            },
        },
    )

    assert result["repository"] is None
    assert result["requiredCapabilities"] == ["codex_cli", "jira"]


def test_pr_publish_derives_gh_without_implicit_git_for_non_repository_workflow() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "targetRuntime": "codex_cli",
            "task": {
                "instructions": "Prepare a Jira-only report.",
                "publish": {"mode": "pr"},
            },
        },
    )

    assert "gh" in result["requiredCapabilities"]
    assert "git" not in result["requiredCapabilities"]


def test_required_capabilities_derive_git_from_repository_or_git_context() -> None:
    repository_result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "targetRuntime": "codex_cli",
            "task": {
                "instructions": "Run repository-backed work.",
                "publish": {"mode": "none"},
            },
        },
    )
    git_context_result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "targetRuntime": "codex_cli",
            "task": {
                "instructions": "Run branch-backed work.",
                "git": {"branch": "feature/mm-945"},
                "publish": {"mode": "none"},
            },
        },
    )

    assert "git" in repository_result["requiredCapabilities"]
    assert "git" in git_context_result["requiredCapabilities"]


def test_explicit_git_required_capability_is_preserved_without_repository_context() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "targetRuntime": "codex_cli",
            "requiredCapabilities": ["git"],
            "task": {
                "instructions": "Run explicit Git-capability work.",
                "publish": {"mode": "none"},
            },
        },
    )

    assert result["requiredCapabilities"] == ["git", "codex_cli"]


def test_skill_metadata_required_capabilities_aggregate_into_canonical_required() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Verify PR against Jira.",
                "skill": {"id": "jira-pr-verify"},
            },
        },
    )

    assert result["workflow"]["skill"]["id"] == "jira-pr-verify"
    assert set(result["requiredCapabilities"]) >= {"git", "gh", "jira"}


def test_step_skill_metadata_required_capabilities_aggregate_into_canonical_required() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Run a step skill.",
                "steps": [
                    {
                        "instructions": "Verify PR against Jira.",
                        "skill": {"id": "jira-pr-verify"},
                    }
                ],
            },
        },
    )

    assert set(result["requiredCapabilities"]) >= {"git", "gh", "jira"}


def test_mm569_tool_validation_error_identifies_required_field_path() -> None:
    invalid = tool_step()
    invalid["tool"].pop("id")

    with pytest.raises(WorkflowContractError) as excinfo:
        build_canonical_workflow_view(job_type="task", payload=task_payload(invalid))

    assert "workflow.steps[].tool.id" in str(excinfo.value)
    assert "tool.name" in str(excinfo.value)


@pytest.mark.parametrize(
    "skill_id",
    [
        "batch-pr-resolver",
        "batch-dependabot-resolver",
        "batch-workflows",
        "jira-issue-creator",
        "jira-pr-verify",
        "jira-verify",
    ],
)
def test_non_repository_side_effect_skills_reject_repository_publish_modes(
    skill_id: str,
) -> None:
    with pytest.raises(WorkflowContractError, match="non-repository skill"):
        resolve_publish_mode_for_skill(skill_id, "pr")

    assert resolve_publish_mode_for_skill(skill_id, None) == "none"
    assert resolve_publish_mode_for_skill(skill_id, "none") == "none"


@pytest.mark.parametrize(
    "skill_id",
    [
        "pr-resolver",
        "fix-comments",
        "fix-ci",
        "fix-merge-conflicts",
    ],
)
def test_auto_publish_capable_skills_resolve_to_auto(skill_id: str) -> None:
    """Auto publishing contract source: docs/Workflows/WorkflowPublishing.md."""

    assert "auto" in SUPPORTED_PUBLISH_MODES
    assert resolve_publish_mode_for_skill(skill_id, None) == "auto"
    assert resolve_publish_mode_for_skill(skill_id, "auto") == "auto"
    diagnostics: list[dict[str, object]] = []
    assert (
        resolve_publish_mode_for_skill(
            skill_id,
            "none",
            diagnostics=diagnostics,
        )
        == "auto"
    )
    assert diagnostics == [
        {
            "code": "legacy_auto_publish_none_normalized",
            "skillId": skill_id,
            "requestedMode": "none",
            "resolvedMode": "auto",
            "message": (
                f"Legacy publish.mode='none' for auto-publish-capable skill "
                f"'{skill_id}' was normalized to 'auto'."
            ),
        }
    ]
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill(skill_id, "pr")


def test_non_capable_skill_rejects_auto_publish_mode() -> None:
    with pytest.raises(WorkflowContractError, match="auto-publish-capable"):
        resolve_publish_mode_for_skill("ordinary-skill", "auto")


@pytest.mark.parametrize(
    "skill_id",
    ["batch-pr-resolver", "batch-dependabot-resolver", "batch-workflows"],
)
def test_batch_parent_skills_are_side_effect_not_auto_publish(
    skill_id: str,
) -> None:
    assert is_auto_publish_capable_skill(skill_id) is False
    assert resolve_publish_mode_for_skill(skill_id, None) == "none"
    with pytest.raises(WorkflowContractError, match="non-repository skill"):
        resolve_publish_mode_for_skill(skill_id, "auto")


def test_auto_publish_capability_can_come_from_skill_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution_contract_module,
        "_load_skill_publish_metadata",
        lambda skill_id: {
            "mode": "auto",
            "owner": "agent",
            "requiresEvidence": True,
        },
    )

    diagnostics: list[dict[str, object]] = []

    assert is_auto_publish_capable_skill("metadata-only-skill") is True
    assert (
        resolve_publish_mode_for_skill(
            "metadata-only-skill",
            None,
            diagnostics=diagnostics,
        )
        == "auto"
    )
    assert diagnostics == []


def test_auto_publish_metadata_precedes_migration_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution_contract_module,
        "_load_skill_publish_metadata",
        lambda skill_id: {},
    )

    assert is_auto_publish_capable_skill("fix-ci") is False


def test_auto_publish_fallback_emits_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        execution_contract_module,
        "_load_skill_publish_metadata",
        lambda skill_id: None,
    )
    diagnostics: list[dict[str, object]] = []

    assert (
        resolve_publish_mode_for_skill("fix-ci", None, diagnostics=diagnostics)
        == "auto"
    )
    assert diagnostics == [
        {
            "code": "auto_publish_capability_migration_fallback",
            "skillId": "fix-ci",
            "message": (
                "Auto publish capability for skill 'fix-ci' was resolved from "
                "the migration fallback because publish metadata was unavailable."
            ),
        }
    ]


def test_workflow_skill_publish_metadata_enables_auto_for_unknown_skill() -> None:
    payload = {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex",
        "workflow": {
            "instructions": "Run deployment skill.",
            "skill": {
                "id": "deployment-auto-skill",
                "publish": {
                    "mode": "auto",
                    "owner": "agent",
                    "requiresEvidence": True,
                },
            },
            "publish": {"mode": "auto"},
        },
    }

    result = CanonicalWorkflowExecutionPayload.model_validate(payload)

    assert result.task.publish.mode == "auto"


def test_workflow_skill_side_effect_metadata_forces_none_for_unknown_skill() -> None:
    payload = {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex",
        "workflow": {
            "instructions": "Queue children.",
            "skill": {
                "id": "deployment-batch-skill",
                "sideEffect": {
                    "kind": "enqueue_children",
                    "owner": "agent",
                    "outcomeArtifact": "artifacts/result.json",
                },
            },
            "publish": {"mode": "pr"},
        },
    }

    with pytest.raises(ValidationError, match="non-repository skill"):
        CanonicalWorkflowExecutionPayload.model_validate(payload)


@pytest.mark.parametrize(
    "skill_id",
    ["fix-comments", "fix-ci", "fix-merge-conflicts"],
)
def test_codex_skill_payload_defaults_auto_publish_capable_skill_to_auto(
    skill_id: str,
) -> None:
    """codex_skill submissions that omit publishMode must default to ``auto``.

    The legacy payload path must preserve omitted publish mode until the
    selected-skill resolver can apply the auto publishing contract.
    """

    result = build_canonical_workflow_view(
        job_type="codex_skill",
        payload={
            "skillId": skill_id,
            "inputs": {
                "repo": "MoonLadderStudios/MoonMind",
                "instruction": f"Run {skill_id}.",
            },
        },
    )

    assert result["workflow"]["publish"]["mode"] == "auto"


def test_canonical_workflow_view_strips_capability_identity_versions() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Run pr-resolver for PR 2680.",
                "skill": {
                    "id": "pr-resolver",
                    "skillVersion": "1.0.0",
                    "args": {"pr": "2680"},
                },
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0.0",
                    "inputs": {"pr": "2680"},
                },
                "skills": {
                    "include": [
                        {"name": "pr-resolver", "version": "1.0.0"},
                    ],
                },
                "authoredPresets": [
                    {
                        "presetSlug": "jira-implement",
                        "presetVersion": "1.0.0",
                    },
                ],
                "appliedStepTemplates": [
                    {
                        "slug": "jira-implement",
                        "version": "1.0.0",
                    },
                ],
                "steps": [
                    {
                        "type": "tool",
                        "instructions": "Fetch issue.",
                        "tool": {
                            "id": "jira.get_issue",
                            "toolVersion": "1.0.0",
                            "inputs": {"issueKey": "MM-559"},
                        },
                    },
                ],
            },
        },
    )

    workflow = result["workflow"]
    assert workflow["skill"]["id"] == "pr-resolver"
    assert workflow["skill"]["args"] == {"pr": "2680"}
    assert "skillVersion" not in workflow["skill"]
    assert "version" not in workflow["skill"]
    assert workflow["tool"] == {
        "type": "skill",
        "name": "pr-resolver",
        "inputs": {"pr": "2680"},
    }
    assert workflow["skills"]["include"] == [{"name": "pr-resolver"}]
    assert "version" not in workflow["skills"]["include"][0]
    assert workflow["authoredPresets"][0]["presetSlug"] == "jira-implement"
    assert "presetVersion" not in workflow["authoredPresets"][0]
    assert "version" not in workflow["authoredPresets"][0]
    assert workflow["appliedStepTemplates"] == [{"slug": "jira-implement"}]
    assert workflow["steps"][0]["tool"] == {
        "id": "jira.get_issue",
        "inputs": {"issueKey": "MM-559"},
    }


def test_jira_issue_updater_allows_explicit_repository_publish_modes() -> None:
    assert resolve_publish_mode_for_skill("jira-issue-updater", "pr") == "pr"
    assert resolve_publish_mode_for_skill("jira-issue-updater", "branch") == "branch"
    assert resolve_publish_mode_for_skill("jira-issue-updater", None) == "none"


def test_multi_skill_publish_resolution_preserves_auto_over_none() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Resolve PR and update Jira.",
                "skill": {"id": "pr-resolver", "args": {"pr": "2962"}},
                "steps": [
                    {
                        "id": "jira",
                        "title": "Update Jira",
                        "instructions": "Move Jira issue.",
                        "skill": {"id": "jira-issue-updater", "args": {}},
                    }
                ],
            },
        },
    )

    assert result["workflow"]["publish"]["mode"] == "auto"


def test_jira_orchestrate_preset_context_allows_first_step_skill_pr_publish() -> None:
    result = build_canonical_workflow_view(
        job_type="task",
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {
                "instructions": "Run Jira Orchestrate for THOR-352.",
                "skill": {"id": "jira-issue-updater"},
                "publish": {"mode": "pr"},
                "steps": [
                    {
                        "id": "tpl:jira-orchestrate:01",
                        "title": "Move Jira issue",
                        "instructions": "Transition THOR-352 to In Progress.",
                        "skill": {"id": "jira-issue-updater", "args": {}},
                    }
                ],
                "appliedStepTemplates": [
                    {
                        "slug": "jira-orchestrate",
                        "stepIds": ["tpl:jira-orchestrate:01"],
                    }
                ],
            },
        },
    )

    assert result["workflow"]["publish"]["mode"] == "pr"
    assert "gh" in result["requiredCapabilities"]


def test_effective_task_step_skills_apply_exclusions_without_mutating_task() -> None:
    task_skills = WorkflowExecutionSpec.model_validate(
        {
            "repository": "test/repo",
            "instructions": "execute",
            "skills": {
                "sets": ["default"],
                "include": [
                    {"name": "baseline"},
                    {"name": "remove-me"},
                ],
                "exclude": ["legacy"],
                "materializationMode": "hybrid",
            },
        }
    ).skills
    step_skills = WorkflowStepSpec.model_validate(
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

    effective = build_effective_workflow_skill_selectors(task_skills, step_skills)

    assert effective is not None
    assert effective.sets == ["default", "python"]
    assert [item.name for item in effective.include or []] == [
        "baseline",
        "step-only",
    ]
    assert effective.exclude == ["legacy", "remove-me"]
    assert effective.materialization_mode == "none"
    assert [item.name for item in task_skills.include or []] == [
        "baseline",
        "remove-me",
    ]

def test_effective_task_step_skills_returns_none_for_empty_intent() -> None:
    assert build_effective_workflow_skill_selectors(None, None) is None

def test_effective_task_step_skills_ignores_auto_sentinel_include() -> None:
    task_skills = WorkflowExecutionSpec.model_validate(
        {
            "repository": "test/repo",
            "instructions": "execute",
            "skills": {"include": [{"name": "auto"}]},
        }
    ).skills

    assert build_effective_workflow_skill_selectors(task_skills, None) is None

def test_effective_task_step_skills_drops_auto_without_losing_real_includes() -> None:
    task_skills = WorkflowExecutionSpec.model_validate(
        {
            "repository": "test/repo",
            "instructions": "execute",
            "skills": {
                "include": [
                    {"name": "auto"},
                    {"name": "jira-issue-updater"},
                ],
            },
        }
    ).skills

    effective = build_effective_workflow_skill_selectors(task_skills, None)

    assert effective is not None
    assert [item.name for item in effective.include or []] == ["jira-issue-updater"]

def test_task_input_attachments_preserve_objective_and_step_targets() -> None:
    """MM-367: objective and step refs remain distinct canonical fields."""

    canonical = build_canonical_workflow_view(
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

    assert canonical["workflow"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["workflow"]["steps"][0]["inputAttachments"] == [
        {
            "artifactId": "art-step",
            "filename": "same-name.png",
            "contentType": "image/png",
            "sizeBytes": 20,
        }
    ]

def test_task_input_attachments_accept_field_name_keys() -> None:
    """MM-375: pre-validation preserves populate_by_name attachment payloads."""

    canonical = build_canonical_workflow_view(
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

    assert canonical["workflow"]["inputAttachments"] == [
        {
            "artifactId": "art-objective",
            "filename": "objective.png",
            "contentType": "image/png",
            "sizeBytes": 10,
        }
    ]
    assert canonical["workflow"]["steps"][0]["inputAttachments"] == [
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
        WorkflowExecutionSpec.model_validate(
            {
                "instructions": "Inspect image.",
                "inputAttachments": [attachment],
            }
        )

def test_task_input_attachment_validation_error_carries_objective_diagnostic() -> None:
    """MM-375: validation failures expose target-aware diagnostic evidence."""

    with pytest.raises(ValidationError) as exc_info:
        WorkflowExecutionSpec.model_validate(
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
        WorkflowExecutionSpec.model_validate(
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
        WorkflowExecutionSpec.model_validate(
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

    with pytest.raises(ValidationError, match="workflow.inputAttachments must be a list"):
        WorkflowExecutionSpec.model_validate(
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
    "kind": "recover_from_failed_step",
    "sourceWorkflowId": "mm:abc123",
    "sourceRunId": "run-1",
    "failedStepId": "step-3",
    "recoveryCheckpointRef": "art_ckpt_abc",
    "taskInputSnapshotRef": "art_snap_abc",
}

_BASE_TASK_PAYLOAD = {
    "repository": "test/repo",
    "workflow": {"instructions": "Do work"},
}


def _canonical_task_payload(task_overrides: dict) -> dict:
    payload = dict(_BASE_TASK_PAYLOAD)
    payload["workflow"] = {**_BASE_TASK_PAYLOAD["workflow"], **task_overrides}
    return payload


# FR-001: WorkflowRecoveryKind must accept exactly three values

def test_fr001_task_recovery_kind_accepts_valid_literals() -> None:
    """MM-638 FR-001: Each canonical recovery kind is accepted."""
    for kind in ("exact_full_rerun", "edited_full_retry", "recover_from_failed_step"):
        prov = WorkflowRecoveryProvenance.model_validate(
            {"kind": kind, "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
        )
        assert prov.kind == kind


def test_fr001_task_recovery_kind_rejects_invalid_literal() -> None:
    """MM-638 FR-001: Values outside the three canonical literals are rejected."""
    with pytest.raises(ValidationError):
        WorkflowRecoveryProvenance.model_validate(
            {"kind": "unknown_kind", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
        )


# FR-002: WorkflowRecoveryProvenance required/optional fields

def test_fr002_recovery_provenance_requires_non_empty_source_ids() -> None:
    """MM-638 FR-002: sourceWorkflowId and sourceRunId must be non-empty."""
    with pytest.raises(ValidationError):
        WorkflowRecoveryProvenance.model_validate(
            {"kind": "exact_full_rerun", "sourceWorkflowId": "", "sourceRunId": "r1"}
        )
    with pytest.raises(ValidationError):
        WorkflowRecoveryProvenance.model_validate(
            {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": ""}
        )


def test_fr002_recovery_provenance_optional_fields_absent_is_valid() -> None:
    """MM-638 FR-002: requestedBy and requestedAt are optional."""
    prov = WorkflowRecoveryProvenance.model_validate(
        {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"}
    )
    assert prov.requested_by is None
    assert prov.requested_at is None


# FR-003: ResumeFromFailedStepRef required/optional fields

def test_fr003_recovery_ref_requires_non_empty_required_fields() -> None:
    """MM-638 FR-003: All required ResumeFromFailedStepRef fields must be non-empty."""
    for empty_field in ("sourceWorkflowId", "sourceRunId", "failedStepId",
                        "recoveryCheckpointRef", "taskInputSnapshotRef"):
        bad = dict(_VALID_RESUME_BLOCK)
        bad[empty_field] = ""
        with pytest.raises(ValidationError):
            ResumeFromFailedStepRef.model_validate(bad)


def test_fr003_recovery_ref_optional_fields_absent_is_valid() -> None:
    """MM-638 FR-003: failedStepExecution, planRef, planDigest are optional."""
    ref = ResumeFromFailedStepRef.model_validate(_VALID_RESUME_BLOCK)
    assert ref.failed_step_execution is None
    assert ref.plan_ref is None
    assert ref.plan_digest is None


# FR-004/005: WorkflowExecutionSpec accepts recovery and resume as optional fields

def test_fr004_fr005_plain_task_unaffected_by_new_fields() -> None:
    """MM-638 FR-004/005: A plain task without recovery/resume is accepted and unaffected."""
    spec = WorkflowExecutionSpec.model_validate({"instructions": "Do work"})
    assert spec.recovery is None
    assert spec.resume is None
    assert spec.depends_on is None


def test_fr004_recovery_field_accepted_on_task_execution_spec() -> None:
    """MM-638 FR-004: recovery field is accepted on WorkflowExecutionSpec."""
    spec = WorkflowExecutionSpec.model_validate({
        "instructions": "Retry",
        "recovery": {"kind": "exact_full_rerun", "sourceWorkflowId": "mm:x", "sourceRunId": "r1"},
    })
    assert spec.recovery is not None
    assert spec.recovery.kind == "exact_full_rerun"


# FR-006: recover_from_failed_step without resume block → error

def test_fr006_recover_from_failed_step_without_recovery_block_is_rejected() -> None:
    """MM-638 FR-006: Missing resume block with recover_from_failed_step recovery kind raises WorkflowContractError."""
    with pytest.raises(WorkflowContractError, match="task.resume is required"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "recover_from_failed_step",
                    "sourceWorkflowId": "mm:x",
                    "sourceRunId": "r1",
                },
            }),
        )


# FR-007: resume block without matching recovery.kind → error

def test_fr007_recovery_block_without_matching_recovery_kind_is_rejected() -> None:
    """MM-638 FR-007: resume block paired with wrong recovery.kind raises WorkflowContractError."""
    with pytest.raises(WorkflowContractError, match="recover_from_failed_step"):
        build_canonical_workflow_view(
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


def test_fr007_recovery_block_without_any_recovery_is_rejected() -> None:
    """MM-638 FR-007: resume block with no recovery field raises WorkflowContractError."""
    with pytest.raises(WorkflowContractError, match="task.recovery must be present"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_canonical_task_payload({"resume": _VALID_RESUME_BLOCK}),
        )


# FR-008: exact_full_rerun and edited_full_retry with/without source IDs

def test_fr008_exact_full_rerun_accepted_with_source_ids() -> None:
    """MM-638 FR-008: exact_full_rerun with sourceWorkflowId and sourceRunId is accepted."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "exact_full_rerun",
                "sourceWorkflowId": "mm:abc",
                "sourceRunId": "run-2",
            },
        }),
    )
    assert result["workflow"]["recovery"]["kind"] == "exact_full_rerun"
    assert result["workflow"].get("resume") is None


def test_fr008_edited_full_retry_accepted_with_source_ids() -> None:
    """MM-638 FR-008: edited_full_retry with sourceWorkflowId and sourceRunId is accepted."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "edited_full_retry",
                "sourceWorkflowId": "mm:abc",
                "sourceRunId": "run-3",
            },
        }),
    )
    assert result["workflow"]["recovery"]["kind"] == "edited_full_retry"


def test_fr008_exact_full_rerun_with_recovery_is_rejected() -> None:
    """MM-638 FR-008: exact_full_rerun paired with a resume block raises WorkflowContractError."""
    with pytest.raises(WorkflowContractError, match="recover_from_failed_step"):
        build_canonical_workflow_view(
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


def test_fr008_edited_full_retry_with_recovery_is_rejected() -> None:
    """MM-638 FR-008: edited_full_retry paired with a resume block raises WorkflowContractError."""
    with pytest.raises(WorkflowContractError, match="recover_from_failed_step"):
        build_canonical_workflow_view(
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
    result = build_canonical_workflow_view(
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

    recovery = result["workflow"]["recovery"]
    assert recovery["kind"] == "edited_full_retry"
    assert recovery["sourceWorkflowId"] == "mm:failed-source"
    assert recovery["sourceRunId"] == "run-source"


def test_mm644_edited_full_retry_rejects_recovery_pairing() -> None:
    """MM-644 FR-009: edited full retry must not carry failed-step recovery refs."""
    with pytest.raises(WorkflowContractError, match="recover_from_failed_step"):
        build_canonical_workflow_view(
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
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "dependsOn": ["mm:workflow-1", "mm:workflow-2"],
        }),
    )
    assert result["workflow"]["dependsOn"] == ["mm:workflow-1", "mm:workflow-2"]


def test_fr009_empty_depends_on_normalized_to_none() -> None:
    """MM-638 FR-009: Empty dependsOn list is normalized to None."""
    spec = WorkflowExecutionSpec.model_validate({
        "instructions": "Work",
        "dependsOn": [],
    })
    assert spec.depends_on is None


# FR-010/011: task.git.branch is the canonical field; targetBranch is stripped.

def test_fr010_branch_is_canonical_authored_field() -> None:
    """MM-638 FR-010: task.git.branch is accepted and present in canonical output."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({"git": {"branch": "feature/my-branch"}}),
    )
    assert result["workflow"]["git"]["branch"] == "feature/my-branch"


def test_mm668_target_branch_is_not_active_authored_branch_input() -> None:
    """MM-668: targetBranch must not be normalized into active authored branch."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "git": {"targetBranch": "feature/legacy"},
        }),
    )
    assert result["workflow"]["git"]["branch"] is None
    assert "targetBranch" not in result["workflow"]["git"]


# SC-001: Full recover_from_failed_step acceptance scenario

def test_sc001_well_formed_recovery_payload_accepted() -> None:
    """MM-638 SC-001: A complete recover_from_failed_step payload is accepted and preserved."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "recover_from_failed_step",
                "sourceWorkflowId": "mm:abc123",
                "sourceRunId": "run-1",
            },
            "resume": _VALID_RESUME_BLOCK,
        }),
    )
    assert result["workflow"]["recovery"]["kind"] == "recover_from_failed_step"
    assert result["workflow"]["resume"]["failedStepId"] == "step-3"
    assert result["workflow"]["resume"]["recoveryCheckpointRef"] == "art_ckpt_abc"
    assert result["workflow"]["resume"]["taskInputSnapshotRef"] == "art_snap_abc"


def test_mm825_recovery_resume_contract_preserves_checkpoint_restoration_fields() -> None:
    """MM-825: Resume contract carries checkpoint-backed recovery evidence refs."""
    resume = {
        **_VALID_RESUME_BLOCK,
        "failedStepExecution": 2,
        "checkpointBoundary": "before_recovery_restoration",
        "planRef": "artifact://plan/source",
        "planDigest": "sha256:plan",
        "preservedStepRefs": [
            "artifact://workspace/before-plan",
            "artifact://completed/plan",
        ],
        "dependencySignatures": {
            "plan": {
                "logicalStepId": "plan",
                "executionOrdinal": 1,
                "outputDigest": "sha256:output",
            }
        },
        "workspacePolicy": "restore_pre_execution",
    }

    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "recover_from_failed_step",
                "sourceWorkflowId": "mm:abc123",
                "sourceRunId": "run-1",
            },
            "resume": resume,
        }),
    )

    normalized = result["workflow"]["resume"]
    assert normalized["checkpointBoundary"] == "before_recovery_restoration"
    assert normalized["planRef"] == "artifact://plan/source"
    assert normalized["planDigest"] == "sha256:plan"
    assert normalized["preservedStepRefs"] == [
        "artifact://workspace/before-plan",
        "artifact://completed/plan",
    ]
    assert normalized["dependencySignatures"]["plan"]["outputDigest"] == "sha256:output"
    assert normalized["workspacePolicy"] == "restore_pre_execution"


def test_mm825_recovery_resume_contract_preserves_selected_step_fields() -> None:
    """MM-825: Selected-step recovery fields are part of the closed resume contract."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "recovery": {
                "kind": "recover_from_failed_step",
                "sourceWorkflowId": "mm:abc123",
                "sourceRunId": "run-1",
            },
            "resume": {
                **_VALID_RESUME_BLOCK,
                "failedStepId": "design",
                "failedStepExecution": 1,
                "recoveryMode": "selected_step",
                "selectedStartStepId": "design",
                "selectedStartStepExecution": 1,
            },
        }),
    )

    normalized = result["workflow"]["resume"]
    assert normalized["failedStepId"] == "design"
    assert normalized["recoveryMode"] == "selected_step"
    assert normalized["selectedStartStepId"] == "design"
    assert normalized["selectedStartStepExecution"] == 1


def test_mm825_recovery_resume_contract_rejects_unknown_fields() -> None:
    """MM-825: Resume refs are a closed checkpoint-backed recovery contract."""
    with pytest.raises(ValidationError):
        ResumeFromFailedStepRef.model_validate(
            {
                **_VALID_RESUME_BLOCK,
                "inlineCheckpointPayload": {"workspace": "not allowed"},
            }
        )


def test_sc001_recovery_source_workflow_id_must_match_recovery() -> None:
    """MM-638: recovery and resume must pin the same source workflow."""
    with pytest.raises(WorkflowContractError, match="sourceWorkflowId"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "recover_from_failed_step",
                    "sourceWorkflowId": "mm:abc123",
                    "sourceRunId": "run-1",
                },
                "resume": {
                    **_VALID_RESUME_BLOCK,
                    "sourceWorkflowId": "mm:other",
                },
            }),
        )


def test_sc001_recovery_source_run_id_must_match_recovery() -> None:
    """MM-638: recovery and resume must pin the same source run."""
    with pytest.raises(WorkflowContractError, match="sourceRunId"):
        build_canonical_workflow_view(
            job_type="task",
            payload=_canonical_task_payload({
                "recovery": {
                    "kind": "recover_from_failed_step",
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

def test_edge_case_recovery_checkpoint_ref_empty_is_rejected() -> None:
    """MM-638 edge case: recoveryCheckpointRef empty string is rejected."""
    bad_resume = {**_VALID_RESUME_BLOCK, "recoveryCheckpointRef": ""}
    with pytest.raises(ValidationError):
        ResumeFromFailedStepRef.model_validate(bad_resume)


def test_edge_case_branch_and_starting_branch_both_preserved() -> None:
    """MM-638 edge case: branch and startingBranch are distinct fields and both preserved."""
    result = build_canonical_workflow_view(
        job_type="task",
        payload=_canonical_task_payload({
            "git": {"branch": "main", "startingBranch": "sha-abc123"},
        }),
    )
    git = result["workflow"]["git"]
    assert git["branch"] == "main"
    assert git["startingBranch"] == "sha-abc123"
