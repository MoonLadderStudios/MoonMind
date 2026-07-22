from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest
from temporalio import activity
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.worker_runtime import (
    MoonMindAgentRun,
    MoonMindManifestIngest,
    OpenTelemetryLoggingFilter,
    _OPENTELEMETRY_LOG_FORMAT,
    _build_agent_runtime_deps,
    _build_deployment_update_executor,
    _enforce_codex_config_for_managed_fleet,
    _expand_preset_for_child_run,
    _publish_mode_agent_instructions,
    _persist_child_run_task_input_snapshot,
    _build_runtime_planner,
    _build_runtime_activities,
    _configure_worker_logging,
    _required_capability_blockers,
    main_async,
    resolve_adapter_metadata,
    get_activity_route,
    resolve_external_adapter,
    external_adapter_execution_style,
)
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from moonmind.workflows.temporal.workers import (
    AGENT_RUNTIME_FLEET,
    DEPLOYMENT_FLEET,
    SANDBOX_FLEET,
    WORKFLOW_FLEET,
)

@asynccontextmanager
async def _template_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/child_presets.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


class _FakeTaskInputSnapshotArtifactService:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(artifact_id="art_task_snapshot_1"), SimpleNamespace()

    async def write_complete(self, **kwargs):
        self.write_calls.append(kwargs)
        return SimpleNamespace(artifact_id="art_task_snapshot_1")


def test_publish_mode_agent_instructions_distinguish_auto_none_and_managed() -> None:
    """Publish instruction source: docs/Workflows/WorkflowPublishing.md."""

    auto = _publish_mode_agent_instructions("auto")
    assert "Publishing is in auto mode" in auto
    assert "artifacts/publish_result.json" in auto
    assert "commit, push, or merge only when required by the selected skill" in auto

    none = _publish_mode_agent_instructions("none")
    assert "Do NOT commit or push" in none
    assert "Publishing is disabled" in none

    managed = _publish_mode_agent_instructions("pr")
    assert "commit your work" in managed
    assert "Do NOT push or create a pull request" in managed
    assert _publish_mode_agent_instructions("branch") == managed


def test_opentelemetry_logging_filter_injects_bounded_managed_session_fields(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "agent_runtime")
    monkeypatch.setenv("MOONMIND_WORKER_ID", "worker-otel-1")
    record = logging.LogRecord(
        name="moonmind.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="managed session transition",
        args=(),
        exc_info=None,
    )
    record.managed_session = {
        "agentRunId": "wf-run-1",
        "runtimeId": "codex_cli",
        "sessionId": "sess:wf-run-1:codex_cli",
        "sessionEpoch": 1,
        "sessionStatus": "active",
        "isDegraded": False,
        "activityType": "agent_runtime.send_turn",
        "transition": "active turn running",
        "containerId": "container-1",
        "threadId": "thread-1",
        "turnId": "turn-1",
        "instructions": "Write a private implementation plan",
        "rawLog": "terminal scrollback",
        "token": "ghp_secret_token",
    }

    assert OpenTelemetryLoggingFilter().filter(record) is True

    assert record.service == "temporal-worker-agent-runtime"
    assert record.component == "agent_runtime"
    assert record.worker_fleet == "agent_runtime"
    assert record.worker_id == "worker-otel-1"
    assert record.managed_session_agent_run_id == "wf-run-1"
    assert record.managed_session_runtime_id == "codex_cli"
    assert record.managed_session_id == "sess:wf-run-1:codex_cli"
    assert record.managed_session_epoch == "1"
    assert record.managed_session_status == "active"
    assert record.managed_session_is_degraded == "false"
    assert record.managed_session_activity_type == "agent_runtime.send_turn"
    assert record.managed_session_transition == "active turn running"
    assert record.managed_session_container_id == "container-1"
    assert record.managed_session_thread_id == "thread-1"
    assert record.managed_session_turn_id == "turn-1"
    assert record.managed_session == {
        "agentRunId": "wf-run-1",
        "runtimeId": "codex_cli",
        "sessionId": "sess:wf-run-1:codex_cli",
        "sessionEpoch": "1",
        "sessionStatus": "active",
        "isDegraded": "false",
        "activityType": "agent_runtime.send_turn",
        "transition": "active turn running",
        "containerId": "container-1",
        "threadId": "thread-1",
        "turnId": "turn-1",
    }
    rendered = " ".join(
        str(getattr(record, field))
        for field in record.__dict__
        if field.startswith("managed_session_")
    )
    assert "Write a private implementation plan" not in rendered
    assert "terminal scrollback" not in rendered
    assert "ghp_secret_token" not in rendered
    assert "instructions" not in record.managed_session
    assert "rawLog" not in record.managed_session
    assert "token" not in record.managed_session


def test_required_capability_readiness_blocks_missing_jira(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        worker_runtime.settings.atlassian.jira,
        "jira_tool_enabled",
        False,
    )
    monkeypatch.setattr(
        worker_runtime.settings.atlassian.jira,
        "jira_enabled",
        False,
    )

    blockers = _required_capability_blockers(
        parameters={
            "repository": "MoonLadderStudios/MoonMind",
            "requiredCapabilities": ["git", "jira"],
        },
        task_payload={"instructions": "Verify Jira issue."},
    )

    assert blockers == [
        {
            "capability": "jira",
            "source": "requiredCapabilities",
            "target": "workflow",
            "check": "trusted_jira_readiness",
            "reason": (
                "Trusted Jira tool access or prefetched Jira context is required "
                "before launch."
            ),
            "remediation": (
                "Enable the Jira tool integration or attach a trusted Jira issue artifact."
            ),
        }
    ]


def test_required_capability_readiness_accepts_prefetched_jira_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_runtime.settings.atlassian.jira,
        "jira_tool_enabled",
        False,
    )
    monkeypatch.setattr(
        worker_runtime.settings.atlassian.jira,
        "jira_enabled",
        False,
    )

    blockers = _required_capability_blockers(
        parameters={
            "repository": "MoonLadderStudios/MoonMind",
            "requiredCapabilities": ["jira"],
        },
        task_payload={"jiraIssue": {"key": "MM-1"}},
    )

    assert blockers == []


def test_opentelemetry_logging_filter_caches_default_env_fields(monkeypatch) -> None:
    calls = 0

    def fake_default_fields() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {
            "service": "cached-service",
            "component": "cached-component",
            "worker_fleet": "cached-fleet",
            "worker_id": "cached-worker",
        }

    monkeypatch.setattr(
        "moonmind.workflows.temporal.worker_runtime.default_log_fields_from_env",
        fake_default_fields,
    )
    otel_filter = OpenTelemetryLoggingFilter()

    for _ in range(2):
        record = logging.LogRecord(
            name="moonmind.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="event",
            args=(),
            exc_info=None,
        )
        assert otel_filter.filter(record) is True
        assert record.service == "cached-service"

    assert calls == 1

def test_opentelemetry_log_format_includes_session_locator_fields() -> None:
    assert "service=%(service)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "component=%(component)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "worker_fleet=%(worker_fleet)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "worker_id=%(worker_id)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "container_id=%(managed_session_container_id)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "thread_id=%(managed_session_thread_id)s" in _OPENTELEMETRY_LOG_FORMAT
    assert "turn_id=%(managed_session_turn_id)s" in _OPENTELEMETRY_LOG_FORMAT

def test_configure_worker_logging_applies_otel_filter(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_STRUCTURED_LOGS", "1")
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "sandbox")
    monkeypatch.setenv("MOONMIND_WORKER_ID", "worker-sandbox-1")
    original_handlers = list(logging.root.handlers)

    try:
        _configure_worker_logging(enable_opentelemetry=True)
    finally:
        handlers = list(logging.root.handlers)
        logging.root.handlers = original_handlers

    assert handlers
    assert all(handler.formatter is not None for handler in handlers)
    assert any(
        isinstance(existing_filter, OpenTelemetryLoggingFilter)
        for handler in handlers
        for existing_filter in handler.filters
    )

def test_runtime_planner_preserves_execution_profile_ref():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Use the minimax profile for this Claude run.",
                "runtime": {
                    "mode": "claude",
                    "executionProfileRef": "claude-minimax-oauth",
                },
            }
        },
        parameters={"targetRuntime": "claude"},
        snapshot=snapshot,
    )

    runtime_node = plan["nodes"][0]["inputs"]["runtime"]
    assert runtime_node["mode"] == "claude"
    assert runtime_node["executionProfileRef"] == "claude-minimax-oauth"

def test_runtime_planner_preserves_execution_profile_ref_snake_case():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Anthropic API key profile.",
                "runtime": {
                    "mode": "claude",
                    "execution_profile_ref": "anthropic-work",
                },
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert (
        plan["nodes"][0]["inputs"]["runtime"]["executionProfileRef"]
        == "anthropic-work"
    )

def test_runtime_planner_promotes_profile_id_to_runtime_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Use the pinned Codex provider profile.",
                "runtime": {
                    "mode": "codex",
                    "providerProfile": "codex-provider-profile",
                },
            }
        },
        parameters={"profileId": "codex-provider-profile"},
        snapshot=snapshot,
    )

    runtime_node = plan["nodes"][0]["inputs"]["runtime"]
    assert runtime_node["profileId"] == "codex-provider-profile"
    assert runtime_node["providerProfile"] == "codex-provider-profile"


@pytest.mark.asyncio
async def test_child_run_auto_sequences_jira_goal_through_implement_preset(tmp_path):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "task",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "publishMode": "pr",
                    "task": {
                        "title": "MM-747 goal",
                        "goal": "Implement Jira issue MM-747 using the right preset.",
                    },
                },
            )

    task = expanded_parameters["task"]
    assert task["taskTemplate"] == {
        "slug": "jira-implement",
        "scope": "global",
    }
    assert task["presetSchedule"] == {
        "source": "goal",
        "reason": "jira_issue_goal",
        "presetSlug": "jira-implement",
        "jiraIssueKey": "MM-747",
    }
    assert task["inputs"]["jira_issue_key"] == "MM-747"
    assert task["instructions"] == "Implement Jira issue MM-747 using the right preset."
    assert expanded_parameters["stepCount"] == len(task["steps"])
    assert task["steps"][0]["title"] == "Load Jira preset brief"
    assert task["steps"][1]["title"] == "Assess existing implementation state"
    assert task["steps"][-1]["title"] == "Finalize Jira status"
    assert task["appliedStepTemplates"][0]["slug"] == "jira-implement"
    assert task["authoredPresets"][0]["presetSlug"] == "jira-implement"


@pytest.mark.asyncio
async def test_child_run_task_template_without_version_uses_latest_seeded_preset(
    tmp_path,
):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "task",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "publishMode": "pr",
                    "task": {
                        "title": "Run Jira Implement for MM-747",
                        "instructions": "Implement Jira issue MM-747.",
                        "inputs": {"jira_issue_key": "MM-747"},
                        "taskTemplate": {
                            "slug": "jira-implement",
                            "scope": "global",
                        },
                    },
                },
            )

    task = expanded_parameters["task"]
    assert task["taskTemplate"] == {
        "slug": "jira-implement",
        "scope": "global",
    }
    assert "version" not in task["appliedStepTemplates"][0]
    assert "presetVersion" not in task["authoredPresets"][0]
    assert task["steps"][0]["title"] == "Load Jira preset brief"


@pytest.mark.asyncio
async def test_child_run_goal_scheduled_breakdown_preserves_target_runtime(tmp_path):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "task",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "jules",
                    "publishMode": "pr",
                    "task": {
                        "title": "Break down feature",
                        "goal": "Split docs/Design.md into Jira stories.",
                    },
                },
            )

    downstream_task = expanded_parameters["task"]["steps"][3]["jiraOrchestration"][
        "task"
    ]
    assert downstream_task["repository"] == "MoonLadderStudios/MoonMind"
    assert downstream_task["runtime"] == {"mode": "jules"}


@pytest.mark.asyncio
async def test_child_run_goal_scheduled_breakdown_uses_default_runtime_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        worker_runtime.settings.workflow, "default_runtime", "claude_code"
    )
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "task",
                    "repository": "MoonLadderStudios/MoonMind",
                    "publishMode": "pr",
                    "task": {
                        "title": "Break down feature",
                        "goal": "Split docs/Design.md into Jira stories.",
                    },
                },
            )

    downstream_task = expanded_parameters["task"]["steps"][3]["jiraOrchestration"][
        "task"
    ]
    assert downstream_task["repository"] == "MoonLadderStudios/MoonMind"
    assert downstream_task["runtime"] == {"mode": "claude_code"}


@pytest.mark.asyncio
async def test_child_jira_breakdown_implement_expands_workflow_runtime_context(
    tmp_path,
):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": "MoonLadderStudios/MoonMind",
                    "publishMode": "none",
                    "workflow": {
                        "title": "Break down and implement docs/Design.md",
                        "instructions": "Create Jira stories and implement tasks.",
                        "runtime": {"mode": "codex_cli"},
                        "inputs": {
                            "feature_request": "docs/Designs/RuntimeTypes.md",
                            "jira_project_key": "MM",
                            "jira_issue_type": "Story",
                            "jira_dependency_mode": "linear_blocker_chain",
                            "publish_mode": "pr_with_merge_automation",
                            "source_issue_key": "MM-404",
                        },
                        "taskTemplate": {
                            "slug": "jira-breakdown-implement",
                            "version": "1.0.0",
                        },
                    },
                },
            )

    task = expanded_parameters["workflow"]
    downstream_task = task["steps"][4]["jiraOrchestration"]["task"]
    assert downstream_task["repository"] == "MoonLadderStudios/MoonMind"
    assert downstream_task["runtime"] == {"mode": "codex_cli"}
    assert downstream_task["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }


def test_runtime_planner_maps_explicit_tool_step_to_typed_tool_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run explicit tool step.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "fetch-issue",
                        "type": "tool",
                        "instructions": "Fetch MM-559.",
                        "tool": {
                            "id": "jira.get_issue",
                            "toolVersion": "1.0.0",
                            "inputs": {"issueKey": "MM-559"},
                        },
                        "source": {
                            "kind": "preset-derived",
                            "presetId": "jira-flow",
                            "includePath": ["root", "fetch"],
                            "originalStepId": "fetch-jira-issue",
                        },
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["tool"] == {
        "type": "skill",
        "name": "jira.get_issue",
    }
    assert "selectedSkill" not in plan["nodes"][0]["inputs"]
    assert plan["nodes"][0]["inputs"]["type"] == "tool"
    assert plan["nodes"][0]["inputs"]["issueKey"] == "MM-559"
    assert plan["nodes"][0]["inputs"]["source"] == {
        "kind": "preset-derived",
        "presetId": "jira-flow",
        "includePath": ["root", "fetch"],
        "originalStepId": "fetch-jira-issue",
    }

def test_runtime_planner_maps_explicit_skill_step_with_provenance_to_agent_runtime_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run explicit skill step.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "implement-mm-573",
                        "type": "skill",
                        "instructions": "Implement MM-573.",
                        "skill": {
                            "id": "moonspec-implement",
                            "skillVersion": "1.0.0",
                            "inputs": {"issueKey": "MM-573"},
                        },
                        "source": {
                            "kind": "preset-derived",
                            "presetSlug": "jira-orchestrate",
                        },
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert plan["nodes"][0]["inputs"]["selectedSkill"] == "moonspec-implement"
    assert plan["nodes"][0]["inputs"]["type"] == "skill"
    assert plan["nodes"][0]["inputs"]["issueKey"] == "MM-573"
    assert plan["nodes"][0]["inputs"]["source"] == {
        "kind": "preset-derived",
        "presetSlug": "jira-orchestrate",
    }


def test_runtime_planner_validates_skill_step_contract_and_carries_evidence():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run explicit skill step.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "implement-mm-1057",
                        "type": "skill",
                        "instructions": "Implement MM-1057.",
                        "skill": {
                            "id": "jira-implement",
                            "inputs": {"issueKey": "MM-1057"},
                            "inputSchema": {
                                "type": "object",
                                "required": ["issueKey", "repository"],
                                "properties": {
                                    "issueKey": {"type": "string"},
                                    "repository": {
                                        "type": "string",
                                        "x-moonmind-context-default": "repository",
                                    },
                                },
                            },
                            "inputContractDigest": "sha256:contract",
                            "contentDigest": "sha256:content",
                            "contentRef": "artifact:skill",
                        },
                    }
                ],
            }
        },
        parameters={"repository": "MoonLadderStudios/MoonMind"},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["inputs"] == {
        "issueKey": "MM-1057",
        "repository": "MoonLadderStudios/MoonMind",
    }
    assert node_inputs["issueKey"] == "MM-1057"
    assert node_inputs["repository"] == "MoonLadderStudios/MoonMind"
    assert node_inputs["inputContractDigest"] == "sha256:contract"
    assert node_inputs["contentDigest"] == "sha256:content"
    assert node_inputs["contentRef"] == "artifact:skill"


def test_runtime_planner_validates_primary_skill_contract_and_carries_evidence():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Implement MM-1057.",
                "runtime": {"mode": "codex_cli"},
                "skill": {
                    "id": "jira-implement",
                    "inputs": {"issueKey": "MM-1057"},
                    "inputSchema": {
                        "type": "object",
                        "required": ["issueKey", "repository"],
                        "properties": {
                            "issueKey": {"type": "string"},
                            "repository": {
                                "type": "string",
                                "x-moonmind-context-default": "repository",
                            },
                        },
                    },
                    "inputContractDigest": "sha256:contract",
                    "contentDigest": "sha256:content",
                    "contentRef": "artifact:skill",
                },
            }
        },
        parameters={"repository": "MoonLadderStudios/MoonMind"},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["inputs"] == {
        "issueKey": "MM-1057",
        "repository": "MoonLadderStudios/MoonMind",
    }
    assert node_inputs["inputContractDigest"] == "sha256:contract"
    assert node_inputs["contentDigest"] == "sha256:content"
    assert node_inputs["contentRef"] == "artifact:skill"


def test_runtime_planner_reports_field_addressable_skill_input_errors():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(RuntimeError) as excinfo:
        planner(
            inputs={
                "task": {
                    "instructions": "Run explicit skill step.",
                    "runtime": {"mode": "codex_cli"},
                    "steps": [
                        {
                            "id": "implement-mm-1057",
                            "type": "skill",
                            "instructions": "Implement MM-1057.",
                            "skill": {
                                "id": "jira-implement",
                                "inputs": {},
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["issueKey"],
                                    "properties": {
                                        "issueKey": {"type": "string"},
                                    },
                                },
                            },
                        }
                    ],
                }
            },
            parameters={},
            snapshot=snapshot,
        )

    assert "steps[0].skill.inputs.issueKey" in str(excinfo.value)
    assert "required" in str(excinfo.value)


def test_runtime_planner_orders_flattened_tool_and_skill_steps_with_provenance():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run flattened MM-573 steps.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "fetch-issue",
                        "type": "tool",
                        "instructions": "Fetch MM-573.",
                        "tool": {
                            "id": "jira.get_issue",
                            "inputs": {"issueKey": "MM-573"},
                        },
                        "source": {
                            "kind": "preset-derived",
                            "presetSlug": "jira-orchestrate",
                        },
                    },
                    {
                        "id": "implement-story",
                        "type": "skill",
                        "instructions": "Implement MM-573.",
                        "skill": {
                            "id": "moonspec-implement",
                            "inputs": {"issueKey": "MM-573"},
                        },
                        "source": {
                            "kind": "preset-derived",
                            "presetSlug": "jira-orchestrate",
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert [node["id"] for node in plan["nodes"]] == [
        "fetch-issue",
        "implement-story",
    ]
    assert plan["edges"] == [{"from": "fetch-issue", "to": "implement-story"}]
    assert plan["nodes"][0]["tool"] == {
        "type": "skill",
        "name": "jira.get_issue",
    }
    assert plan["nodes"][1]["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert plan["nodes"][0]["inputs"]["source"]["presetSlug"] == "jira-orchestrate"
    assert plan["nodes"][1]["inputs"]["source"]["presetSlug"] == "jira-orchestrate"


def test_runtime_planner_preserves_authored_task_plan_tool_nodes():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={},
        parameters={
            "failurePolicy": "fail_fast",
            "task": {
                "title": "Update deployment stack moonmind",
                "instructions": (
                    "Run the policy-gated deployment update operation for stack "
                    "'moonmind' using the typed deployment.update_compose_stack "
                    "tool contract."
                ),
                "plan": [
                    {
                        "id": "update-moonmind-deployment",
                        "tool": {
                            "type": "skill",
                            "name": "deployment.update_compose_stack",
                            "version": "1.0.0",
                        },
                        "inputs": {
                            "stack": "moonmind",
                            "image": {
                                "repository": (
                                    "ghcr.io/moonladderstudios/moonmind"
                                ),
                                "reference": "v20260507.2470",
                            },
                            "mode": "changed_services",
                        },
                    }
                ],
            },
        },
        snapshot=snapshot,
    )

    assert plan["nodes"] == [
        {
            "id": "update-moonmind-deployment",
            "tool": {
                "type": "skill",
                "name": "deployment.update_compose_stack",
            },
            "inputs": {
                "stack": "moonmind",
                "image": {
                    "repository": "ghcr.io/moonladderstudios/moonmind",
                    "reference": "v20260507.2470",
                },
                "mode": "changed_services",
            },
        }
    ]
    assert "selectedSkill" not in plan["nodes"][0]["inputs"]
    assert plan["policy"]["failure_mode"] == "FAIL_FAST"
    registry_snapshot = plan["metadata"]["registry_snapshot"]
    assert registry_snapshot["artifact_ref"] == "art_registry_123"


def test_runtime_planner_maps_legacy_task_plan_skill_without_type_to_agent_runtime_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={},
        parameters={
            "task": {
                "instructions": "Run a legacy skill plan entry.",
                "runtime": {"mode": "codex_cli"},
                "plan": [
                    {
                        "id": "implement-story",
                        "skill": {
                            "name": "moonspec-implement",
                        },
                        "inputs": {"story": "MM-573"},
                    }
                ],
            },
        },
        snapshot=snapshot,
    )

    assert plan["nodes"] == [
        {
            "id": "implement-story",
            "tool": {
                "type": "agent_runtime",
                "name": "codex_cli",
            },
            "inputs": {
                "instructions": "Execute skill 'moonspec-implement'",
                "runtime": {"mode": "codex_cli"},
                "selectedSkill": "moonspec-implement",
                "story": "MM-573",
            },
        }
    ]


def test_runtime_planner_preserves_authored_task_plan_node_metadata():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={},
        parameters={
            "task": {
                "instructions": "Run the authored deployment plan.",
                "plan": [
                    {
                        "id": "update-moonmind-deployment",
                        "title": "Update MoonMind deployment",
                        "description": "Use the deployment operations service.",
                        "tool": {
                            "type": "skill",
                            "name": "deployment.update_compose_stack",
                        },
                        "inputs": {
                            "stack": "moonmind",
                            "image": {
                                "repository": (
                                    "ghcr.io/moonladderstudios/moonmind"
                                ),
                                "reference": "v20260507.2470",
                            },
                        },
                    }
                ],
            },
        },
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["title"] == "Update MoonMind deployment"
    assert node["description"] == "Use the deployment operations service."


def test_runtime_planner_maps_deployment_update_step_to_typed_tool_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={},
        parameters={
            "task": {
                "instructions": "Run deployment update.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "update-moonmind-deployment",
                        "type": "tool",
                        "instructions": "Update MoonMind deployment.",
                        "tool": {
                            "type": "skill",
                            "name": "deployment.update_compose_stack",
                            "inputs": {
                                "stack": "moonmind",
                                "image": {
                                    "repository": (
                                        "ghcr.io/moonladderstudios/moonmind"
                                    ),
                                    "reference": "latest",
                                },
                            },
                        },
                    }
                ],
            }
        },
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["tool"] == {
        "type": "skill",
        "name": "deployment.update_compose_stack",
    }
    assert "selectedSkill" not in plan["nodes"][0]["inputs"]
    assert plan["nodes"][0]["inputs"]["image"]["reference"] == "latest"


def test_runtime_planner_rejects_duplicate_authored_task_plan_node_ids():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(RuntimeError, match="task\\.plan duplicate node id: node-2"):
        planner(
            inputs={},
            parameters={
                "task": {
                    "instructions": "Run the authored deployment plan.",
                    "plan": [
                        {
                            "id": "node-2",
                            "tool": {
                                "type": "skill",
                                "name": "deployment.update_compose_stack",
                            },
                            "inputs": {"stack": "moonmind"},
                        },
                        {
                            "tool": {
                                "type": "skill",
                                "name": "deployment.update_compose_stack",
                            },
                            "inputs": {"stack": "moonmind"},
                        },
                    ],
                },
            },
            snapshot=snapshot,
        )


def test_deployment_update_executor_is_enabled_only_with_project_mount(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_COMPOSE_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_LOCK_DIR", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES", raising=False)
    # Point the local mount at a path that doesn't exist so auto-detection
    # cleanly returns None when no override is set.
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", str(tmp_path / "missing-mount")
    )

    assert _build_deployment_update_executor() is None

    compose_file = tmp_path / "docker-compose.yaml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_COMPOSE_FILE", str(compose_file))
    env_file = tmp_path / "deploy" / "state" / ".env.deploy"
    lock_dir = tmp_path / "deploy" / "state" / "locks"
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", str(env_file))
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCK_DIR", str(lock_dir))
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", "moonmind-test")
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES",
        "temporal-worker-deployment-control",
    )

    executor = _build_deployment_update_executor()

    assert executor is not None
    # Explicit project_dir + matching local path collapses to legacy behavior.
    assert executor.runner.project_dir == str(tmp_path)
    assert executor.runner.local_project_dir is None
    assert executor.runner.compose_file == str(compose_file)
    assert executor.runner.project_name == "moonmind-test"
    assert executor.runner.env_file == str(env_file)
    assert executor.runner.excluded_services == ("temporal-worker-deployment-control",)
    assert executor.excluded_services == ("temporal-worker-deployment-control",)
    assert executor.lock_manager.lock_dir == str(lock_dir)


def test_deployment_update_executor_auto_detects_host_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """When the local mount exists and the env var is unset, the worker
    introspects its own container to discover the host path."""

    local_mount = tmp_path / "host_project"
    local_mount.mkdir()
    (local_mount / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_COMPOSE_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_LOCK_DIR", raising=False)
    monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_NAME", raising=False)
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", str(local_mount))

    detected: list[str] = []

    def _fake_detect(path: str) -> str:
        detected.append(path)
        return "/host/abs/MoonMind"

    monkeypatch.setattr(
        "moonmind.workflows.temporal.worker_runtime._detect_host_project_dir",
        _fake_detect,
    )

    executor = _build_deployment_update_executor()

    assert executor is not None
    assert detected == [str(local_mount)]
    assert executor.runner.project_dir == "/host/abs/MoonMind"
    assert executor.runner.local_project_dir == str(local_mount)
    assert executor.runner.compose_file is None


def test_deployment_update_executor_returns_none_when_detection_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    local_mount = tmp_path / "host_project"
    local_mount.mkdir()

    monkeypatch.delenv("MOONMIND_DEPLOYMENT_PROJECT_DIR", raising=False)
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR", str(local_mount))
    monkeypatch.setattr(
        "moonmind.workflows.temporal.worker_runtime._detect_host_project_dir",
        lambda _: None,
    )

    assert _build_deployment_update_executor() is None


def test_detect_host_project_dir_retries_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
):
    """A transient ``docker inspect`` failure must retry before disabling."""

    import subprocess

    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(
        worker_runtime,
        "_read_self_container_id",
        lambda: "abc123",
    )
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda _seconds: None)

    attempts = {"count": 0}

    def _fake_run(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise subprocess.TimeoutExpired(cmd=args[0] if args else "docker", timeout=10)
        mounts = json.dumps(
            [
                {
                    "Source": "/host/repo",
                    "Destination": "/workspace/host_project",
                }
            ]
        )
        return SimpleNamespace(stdout=mounts, stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", _fake_run)

    detected = worker_runtime._detect_host_project_dir("/workspace/host_project")
    assert detected == "/host/repo"
    assert attempts["count"] == 2


def test_detect_host_project_dir_returns_none_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
):
    import subprocess

    from moonmind.workflows.temporal import worker_runtime

    monkeypatch.setattr(
        worker_runtime,
        "_read_self_container_id",
        lambda: "abc123",
    )
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda _seconds: None)

    calls = {"count": 0}

    def _always_fail(*args, **kwargs):
        calls["count"] += 1
        raise FileNotFoundError("docker missing")

    monkeypatch.setattr(subprocess, "run", _always_fail)

    detected = worker_runtime._detect_host_project_dir("/workspace/host_project")
    assert detected is None
    assert calls["count"] == worker_runtime._DETECT_HOST_PROJECT_DIR_RETRIES


@pytest.mark.parametrize(
    "source",
    [
        None,
        {"kind": "detached"},
        {
            "kind": "preset-derived",
            "presetId": "removed-preset",
        },
    ],
)
def test_runtime_planner_materializes_tool_steps_without_source_lookup(
    source: dict[str, object] | None,
):
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )
    step: dict[str, object] = {
        "id": "fetch-issue",
        "type": "tool",
        "instructions": "Fetch MM-579.",
        "tool": {
            "id": "jira.get_issue",
            "inputs": {"issueKey": "MM-579"},
        },
    }
    if source is not None:
        step["source"] = source

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run explicit tool step.",
                "runtime": {"mode": "codex_cli"},
                "steps": [step],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["tool"] == {
        "type": "skill",
        "name": "jira.get_issue",
    }
    assert "selectedSkill" not in plan["nodes"][0]["inputs"]
    if source is None:
        assert "source" not in plan["nodes"][0]["inputs"]
    else:
        assert plan["nodes"][0]["inputs"]["source"] == source

def test_runtime_planner_maps_explicit_skill_step_to_agent_runtime_node():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run explicit skill step.",
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "implement",
                        "type": "skill",
                        "instructions": "Implement MM-559.",
                        "skill": {
                            "id": "moonspec-implement",
                            "args": {"issueKey": "MM-559"},
                        },
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert plan["nodes"][0]["inputs"]["selectedSkill"] == "moonspec-implement"
    assert plan["nodes"][0]["inputs"]["type"] == "skill"

def test_runtime_planner_embeds_skill_inputs_for_generated_skill_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                    "version": "1.0.0",
                    "inputs": {"pr": "123", "repo": "MoonLadderStudios/MoonMind"},
                },
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith(
        "Execute skill 'pr-resolver' with inputs:"
    )
    assert '"pr": "123"' in node_inputs["instructions"]
    assert node_inputs["repo"] == "MoonLadderStudios/MoonMind"
    assert node_inputs["selectedSkill"] == "pr-resolver"

def test_runtime_planner_routes_jira_issue_creator_as_agent_skill_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Break down proposal workflow",
                "instructions": "Break down docs/Workflows/WorkflowProposalSystem.md.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "storyOutput": {
                    "mode": "jira",
                    "jira": {"projectKey": "MM", "issueTypeId": "10001"},
                },
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract the stories.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "jira-issue-creator"},
                        "instructions": "Create Jira issues for each story.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    breakdown = plan["nodes"][0]
    jira = plan["nodes"][1]

    assert breakdown["tool"]["type"] == "agent_runtime"
    assert breakdown["tool"]["name"] == "codex_cli"
    assert breakdown["inputs"]["selectedSkill"] == "moonspec-breakdown"
    assert "Do not create or modify any `spec.md`" in breakdown["inputs"]["instructions"]
    assert breakdown["inputs"]["storyBreakdownPath"].startswith(
        "artifacts/story-breakdowns/"
    )
    assert breakdown["inputs"]["storyBreakdownPath"].endswith("/stories.json")
    assert "commit your work" in breakdown["inputs"]["instructions"]
    assert breakdown["inputs"]["storyOutput"]["handoff"] == "artifact"
    assert (
        breakdown["inputs"]["storyOutput"]["requiresStoryBreakdownArtifactRef"]
        is True
    )

    assert jira["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert jira["inputs"]["selectedSkill"] == "jira-issue-creator"
    assert jira["inputs"]["publishMode"] == "none"
    assert jira["inputs"]["instructions"].startswith("Use $jira-issue-creator.")
    assert "commit your work" not in jira["inputs"]["instructions"]
    assert jira["inputs"]["storyOutput"]["mode"] == "jira"
    assert jira["inputs"]["storyOutput"]["handoff"] == "artifact"
    assert jira["inputs"]["storyOutput"]["requiresStoryBreakdownArtifactRef"] is True
    assert (
        jira["inputs"]["storyBreakdownPath"]
        == breakdown["inputs"]["storyBreakdownPath"]
    )

def test_runtime_planner_shares_story_breakdown_path_for_jira_breakdown_preset():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Breakdown and Jira Create",
                "instructions": "Break down docs/Design.md into Jira stories.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract MoonSpec stories.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues from the generated breakdown.",
                        "storyOutput": {
                            "mode": "jira",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                                "dependencyMode": "linear_blocker_chain",
                            },
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    breakdown = plan["nodes"][0]
    jira = plan["nodes"][1]

    assert breakdown["inputs"]["storyBreakdownPath"].startswith(
        "artifacts/story-breakdowns/"
    )
    assert breakdown["inputs"]["storyOutput"]["mode"] == "jira"
    assert breakdown["inputs"]["storyOutput"]["handoff"] == "artifact"
    assert (
        breakdown["inputs"]["storyOutput"]["requiresStoryBreakdownArtifactRef"]
        is True
    )
    assert breakdown["inputs"]["targetBranch"].startswith(
        "breakdown-and-jira-create-"
    )
    assert (
        jira["inputs"]["storyBreakdownPath"]
        == breakdown["inputs"]["storyBreakdownPath"]
    )
    assert jira["inputs"]["targetBranch"] == breakdown["inputs"]["targetBranch"]
    assert jira["tool"] == {
        "type": "skill",
        "name": "story.create_jira_issues",
    }
    assert "selectedSkill" not in jira["inputs"]
    assert jira["inputs"]["publishMode"] == "none"
    assert jira["inputs"]["storyOutput"]["jira"]["dependencyMode"] == (
        "linear_blocker_chain"
    )
    assert jira["inputs"]["storyOutput"]["handoff"] == "artifact"
    assert jira["inputs"]["storyOutput"]["requiresStoryBreakdownArtifactRef"] is True


def test_runtime_planner_preserves_step_story_output_without_task_story_output():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Breakdown and Jira Create",
                "instructions": "Create Jira stories from a step-local config.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "draft",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Draft the source breakdown.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues.",
                        "storyOutput": {
                            "mode": "jira",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                            },
                        },
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    jira = plan["nodes"][1]

    assert jira["inputs"]["storyOutput"]["mode"] == "jira"
    assert jira["inputs"]["storyOutput"]["jira"] == {
        "projectKey": "MM",
        "issueTypeName": "Story",
    }


def test_runtime_planner_jira_breakdown_treats_branch_as_base_branch():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Docs\\TacticsFrontend\\InitiativeOrderBar.md",
                "instructions": "Break down Docs\\TacticsFrontend\\InitiativeOrderBar.md.",
                "repository": "MoonLadderStudios/Tactics",
                "git": {"branch": "main"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract MoonSpec stories.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues from the generated breakdown.",
                        "storyOutput": {
                            "mode": "jira",
                            "fallback": "fail",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                                "dependencyMode": "linear_blocker_chain",
                            },
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    breakdown = plan["nodes"][0]
    jira = plan["nodes"][1]

    assert breakdown["inputs"]["branch"] == "main"
    assert breakdown["inputs"]["startingBranch"] == "main"
    assert breakdown["inputs"]["targetBranch"].startswith(
        "docs-tacticsfrontend-initiativeorderbar-"
    )
    assert breakdown["inputs"]["targetBranch"] != "main"
    assert breakdown["inputs"]["publishMode"] == "branch"
    assert jira["inputs"]["targetBranch"] == breakdown["inputs"]["targetBranch"]
    assert jira["inputs"]["branch"] == "main"
    assert (
        jira["inputs"]["storyBreakdownPath"]
        == breakdown["inputs"]["storyBreakdownPath"]
    )
    assert jira["inputs"]["startingBranch"] == "main"

def test_runtime_planner_preserves_authored_branch_for_jira_story_import():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Import existing breakdown into Jira",
                "instructions": "Create Jira issues from an existing breakdown.",
                "repository": "MoonLadderStudios/MoonMind",
                "git": {"branch": "feature/authored-breakdown"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "tool": {"type": "skill", "name": "story.create_jira_issues"},
                "storyOutput": {
                    "mode": "jira",
                    "storyBreakdownPath": "artifacts/story-breakdowns/import/stories.json",
                    "jira": {
                        "projectKey": "MM",
                        "issueTypeName": "Story",
                    },
                },
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]

    assert node["tool"] == {
        "type": "skill",
        "name": "story.create_jira_issues",
    }
    assert "selectedSkill" not in node["inputs"]
    assert node["inputs"]["publishMode"] == "none"
    assert node["inputs"]["branch"] == "feature/authored-breakdown"
    assert node["inputs"]["storyBreakdownPath"] == (
        "artifacts/story-breakdowns/import/stories.json"
    )
    assert "targetBranch" not in node["inputs"]
    assert "startingBranch" not in node["inputs"]

def test_runtime_planner_routes_jira_orchestrate_task_creator_as_skill_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Breakdown and Jira Orchestrate",
                "instructions": "Break down docs/Design.md into Jira stories.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract MoonSpec stories.",
                    },
                    {
                        "id": "reconcile",
                        "tool": {
                            "type": "skill",
                            "name": "story-reconcile-implementation",
                        },
                        "instructions": "Reconcile stories with current implementation.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues from the generated breakdown.",
                        "storyOutput": {
                            "mode": "jira",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                                "dependencyMode": "linear_blocker_chain",
                            },
                        },
                    },
                    {
                        "id": "orchestrate",
                        "tool": {
                            "type": "skill",
                            "name": "story.create_jira_orchestrate_tasks",
                        },
                        "instructions": "Create dependent Jira Orchestrate workflow executions.",
                        "jiraOrchestration": {
                            "task": {
                                "repository": "MoonLadderStudios/MoonMind",
                                "runtime": {"mode": "codex_cli"},
                                "publish": {"mode": "none"},
                            },
                            "traceability": {"sourceIssueKey": "MM-404"},
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    breakdown = plan["nodes"][0]
    reconcile = plan["nodes"][1]
    jira = plan["nodes"][2]
    orchestrate = plan["nodes"][3]
    assert reconcile["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert reconcile["inputs"]["selectedSkill"] == "story-reconcile-implementation"
    assert (
        reconcile["inputs"]["storyBreakdownPath"]
        == breakdown["inputs"]["storyBreakdownPath"]
    )
    assert (
        jira["inputs"]["storyBreakdownPath"]
        == breakdown["inputs"]["storyBreakdownPath"]
    )
    assert orchestrate["tool"] == {
        "type": "skill",
        "name": "story.create_jira_orchestrate_tasks",
    }
    assert "selectedSkill" not in orchestrate["inputs"]
    assert orchestrate["inputs"]["publishMode"] == "none"
    assert orchestrate["inputs"]["jiraOrchestration"]["traceability"] == {
        "sourceIssueKey": "MM-404"
    }


def test_runtime_planner_routes_jira_implement_task_creator_as_skill_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Breakdown and Jira Implement",
                "instructions": "Break down docs/Design.md into Jira stories.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract MoonSpec stories.",
                    },
                    {
                        "id": "reconcile",
                        "tool": {
                            "type": "skill",
                            "name": "story-reconcile-implementation",
                        },
                        "instructions": "Reconcile stories with current implementation.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues from the generated breakdown.",
                        "storyOutput": {
                            "mode": "jira",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                                "dependencyMode": "linear_blocker_chain",
                            },
                        },
                    },
                    {
                        "id": "implement",
                        "tool": {
                            "type": "skill",
                            "name": "story.create_jira_implement_tasks",
                        },
                        "instructions": "Create dependent Jira Implement workflow executions.",
                        "jiraOrchestration": {
                            "task": {
                                "repository": "MoonLadderStudios/MoonMind",
                                "runtime": {"mode": "codex_cli"},
                                "publish": {
                                    "mode": "pr",
                                    "mergeAutomation": {"enabled": True},
                                },
                            },
                            "traceability": {"sourceIssueKey": "MM-404"},
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    implement = plan["nodes"][3]

    assert implement["tool"] == {
        "type": "skill",
        "name": "story.create_jira_implement_tasks",
    }
    assert "selectedSkill" not in implement["inputs"]
    assert implement["inputs"]["publishMode"] == "none"
    assert implement["inputs"]["jiraOrchestration"]["task"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }
    assert implement["inputs"]["jiraOrchestration"]["traceability"] == {
        "sourceIssueKey": "MM-404"
    }


def test_runtime_planner_routes_document_update_task_creator_as_tool_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Document Update Orchestrate",
                "instructions": "Discover docs and create update tasks.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "steps": [
                    {
                        "id": "discover",
                        "instructions": "Discover documents.",
                    },
                    {
                        "id": "create-update-tasks",
                        "tool": {
                            "type": "skill",
                            "name": "story.create_document_update_tasks",
                        },
                        "instructions": "Create document update tasks.",
                        "documentUpdateOrchestration": {
                            "task": {
                                "repository": "MoonLadderStudios/MoonMind",
                                "runtime": {"mode": "codex_cli"},
                                "publish": {"mode": "none"},
                            },
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    create_tasks = plan["nodes"][1]

    assert create_tasks["tool"] == {
        "type": "skill",
        "name": "story.create_document_update_tasks",
    }
    assert "selectedSkill" not in create_tasks["inputs"]
    assert create_tasks["inputs"]["publishMode"] == "none"


def test_runtime_planner_routes_single_document_update_task_creator_as_tool_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Create document update tasks",
                "instructions": "Create document update tasks from known paths.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "tool": {
                    "type": "skill",
                    "name": "story.create_document_update_tasks",
                },
                "documentUpdateOrchestration": {
                    "task": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "runtime": {"mode": "codex_cli"},
                        "publish": {"mode": "none"},
                    },
                },
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]

    assert node["tool"] == {
        "type": "skill",
        "name": "story.create_document_update_tasks",
    }
    assert "selectedSkill" not in node["inputs"]
    assert node["inputs"]["publishMode"] == "none"


def test_runtime_planner_dedupes_repeated_identical_preset_steps():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )
    breakdown_step = {
        "id": "tpl:jira-breakdown-orchestrate:1.0.0:01:6bfb1360",
        "title": "Break down preferred source input",
        "type": "skill",
        "skill": {"id": "moonspec-breakdown", "requiredCapabilities": ["git"]},
        "instructions": "Extract MoonSpec stories.",
    }
    jira_step = {
        "id": "tpl:jira-breakdown-orchestrate:1.0.0:02:6bfb1360",
        "title": "Create Jira stories",
        "type": "skill",
        "skill": {"id": "story.create_jira_issues"},
        "instructions": "Create Jira issues from the generated breakdown.",
        "storyOutput": {
            "mode": "jira",
            "fallback": "fail",
            "jira": {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "dependencyMode": "linear_blocker_chain",
            },
        },
    }
    orchestrate_step = {
        "id": "tpl:jira-breakdown-orchestrate:1.0.0:03:6bfb1360",
        "title": "Create dependent Jira Orchestrate workflow executions",
        "type": "skill",
        "skill": {"id": "story.create_jira_orchestrate_tasks"},
        "instructions": "Create dependent Jira Orchestrate workflow executions.",
        "jiraOrchestration": {
            "task": {
                "repository": "MoonLadderStudios/Tactics",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
            },
            "traceability": {"sourceIssueKey": ""},
        },
    }

    plan = planner(
        inputs={
            "task": {
                "title": "docs\\Steps\\StepTypes.md",
                "instructions": "docs\\Steps\\StepTypes.md",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    breakdown_step,
                    jira_step,
                    orchestrate_step,
                    dict(breakdown_step),
                    dict(jira_step),
                    dict(orchestrate_step),
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert [node["id"] for node in plan["nodes"]] == [
        "tpl:jira-breakdown-orchestrate:1.0.0:01:6bfb1360",
        "tpl:jira-breakdown-orchestrate:1.0.0:02:6bfb1360",
        "tpl:jira-breakdown-orchestrate:1.0.0:03:6bfb1360",
    ]
    assert plan["edges"] == [
        {
            "from": "tpl:jira-breakdown-orchestrate:1.0.0:01:6bfb1360",
            "to": "tpl:jira-breakdown-orchestrate:1.0.0:02:6bfb1360",
        },
        {
            "from": "tpl:jira-breakdown-orchestrate:1.0.0:02:6bfb1360",
            "to": "tpl:jira-breakdown-orchestrate:1.0.0:03:6bfb1360",
        },
    ]


def test_runtime_planner_rejects_conflicting_duplicate_step_ids():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(RuntimeError, match="duplicated with different payloads"):
        planner(
            inputs={
                "task": {
                    "title": "Conflicting step ids",
                    "instructions": "Run conflicting steps.",
                    "runtime": {"mode": "codex_cli"},
                    "steps": [
                        {
                            "id": "step-1",
                            "tool": {"type": "skill", "name": "moonspec-breakdown"},
                            "instructions": "Extract MoonSpec stories.",
                        },
                        {
                            "id": "step-1",
                            "tool": {
                                "type": "skill",
                                "name": "story.create_jira_issues",
                            },
                            "instructions": "Create Jira issues.",
                        },
                    ],
                }
            },
            parameters={},
            snapshot=snapshot,
        )

@pytest.mark.asyncio
async def test_child_jira_orchestrate_run_expands_seeded_template_steps(tmp_path):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "task",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "publishMode": "pr",
                    "task": {
                        "title": "Run Jira Orchestrate for MM-501: First",
                        "instructions": "Use the existing Jira Orchestrate workflow.",
                        "inputs": {
                            "jira_issue_key": "MM-501",
                            "source_design_path": "",
                            "constraints": "Preserve source issue MM-404 traceability.",
                        },
                        "taskTemplate": {
                            "slug": "jira-orchestrate",
                            "version": "1.0.0",
                        },
                    },
                },
            )

    task = expanded_parameters["task"]
    assert expanded_parameters["stepCount"] == 26
    assert len(task["steps"]) == 26
    assert task["steps"][0]["title"] == "Check Jira blockers before implementation"
    assert task["steps"][0]["tool"]["id"] == "jira.check_blockers"
    assert task["steps"][1]["title"] == "Load Jira preset brief"
    assert task["steps"][1]["tool"]["id"] == "jira.load_preset_brief"
    assert task["steps"][2]["title"] == "Classify request and resume point"
    assert task["steps"][3]["title"] == "Move Jira issue to In Progress"
    assert task["steps"][3]["type"] == "tool"
    assert task["steps"][3]["tool"]["id"] == "jira.update_issue_status"
    assert "MM-501" in task["steps"][3]["instructions"]
    assert task["steps"][6]["skill"]["id"] == "moonspec-plan"
    assert task["steps"][7]["skill"]["id"] == "moonspec-tasks"
    assert task["steps"][9]["skill"]["id"] == "moonspec-implement"
    assert task["steps"][10]["skill"]["id"] == "moonspec-verify"
    assert task["steps"][10]["skill"]["args"]["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/jira-orchestrate.json"
    )
    assert task["steps"][11]["title"] == "Remediate verification gaps — attempt 1 of 6"
    assert task["steps"][11]["skill"]["id"] == "moonspec-implement"
    assert task["steps"][22]["title"] == "Verify remediation attempt 6 of 6"
    assert task["steps"][22]["skill"]["id"] == "moonspec-verify"
    assert task["steps"][22]["skill"]["args"]["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/jira-orchestrate.json"
    )
    assert task["steps"][23]["title"] == "Reconcile declarative docs"
    assert task["steps"][23]["skill"]["id"] == "moonspec-doc-reconcile"
    assert task["steps"][24]["title"] == "Create pull request"
    assert task["steps"][25]["title"] == "Move Jira issue to Review"
    assert task["appliedStepTemplates"][0]["slug"] == "jira-orchestrate"
    assert len(task["appliedStepTemplates"][0]["stepIds"]) == 26
    assert task["authoredPresets"][0]["presetSlug"] == "jira-orchestrate"
    assert "authoredPresets" not in task["appliedStepTemplates"][0]
    assert task["appliedStepTemplates"][0]["composition"]["slug"] == "jira-orchestrate"
    assert all(step["type"] != "preset" for step in task["steps"])


@pytest.mark.asyncio
async def test_child_jira_orchestrate_workflow_payload_expands_seeded_template_steps(
    tmp_path,
):
    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "publishMode": "pr",
                    "workflow": {
                        "title": "Run Jira Orchestrate for MM-820: Conformance",
                        "instructions": "Use the existing Jira Orchestrate workflow.",
                        "tool": {
                            "type": "skill",
                            "name": "jira-orchestrate",
                        },
                        "skill": {"name": "jira-orchestrate"},
                        "inputs": {
                            "jira_issue_key": "MM-820",
                            "source_design_path": (
                                "docs/Steps/StepExecutionsAndCheckpointing.md"
                            ),
                            "constraints": "Do not run implementation inline.",
                        },
                        "runtime": {"mode": "codex_cli"},
                        "publish": {"mode": "pr"},
                        "taskTemplate": {
                            "slug": "jira-orchestrate",
                            "version": "1.0.0",
                        },
                    },
                },
            )

    task = expanded_parameters["workflow"]
    assert "task" not in expanded_parameters
    assert expanded_parameters["stepCount"] == 26
    assert len(task["steps"]) == 26
    assert task["steps"][0]["tool"]["id"] == "jira.check_blockers"
    assert task["steps"][0]["type"] == "tool"
    assert task["steps"][3]["tool"]["id"] == "jira.update_issue_status"
    assert task["appliedStepTemplates"][0]["slug"] == "jira-orchestrate"
    assert task["authoredPresets"][0]["presetSlug"] == "jira-orchestrate"

    planner = _build_runtime_planner()
    plan = planner(
        inputs={"workflow": task},
        parameters={"targetRuntime": "codex_cli"},
        snapshot=SimpleNamespace(
            digest="reg:sha256:test",
            artifact_ref="art_registry_123",
        ),
    )

    first_node = plan["nodes"][0]
    assert first_node["id"].startswith("tpl:jira-orchestrate:01")
    assert first_node["tool"] == {
        "type": "skill",
        "name": "jira.check_blockers",
    }
    assert first_node["inputs"]["targetIssueKey"] == "MM-820"
    assert first_node["inputs"]["blockerPreflight"] == {
        "targetIssueKey": "MM-820",
        "linkType": "Blocks",
    }
    assert "selectedSkill" not in first_node["inputs"]


@pytest.mark.asyncio
async def test_child_preset_expansion_prefers_workflow_payload_over_legacy_task(
    tmp_path,
):
    legacy_task = {
        "title": "Legacy task should not be used",
        "instructions": "Leave this legacy task alone.",
        "steps": [{"id": "legacy-step", "instructions": "Already authored."}],
    }

    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            expanded_parameters = await _expand_preset_for_child_run(
                session=session,
                initial_parameters={
                    "requestType": "workflow",
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                    "task": legacy_task,
                    "workflow": {
                        "title": "Run Jira Orchestrate for MM-821",
                        "instructions": "Use the existing Jira Orchestrate workflow.",
                        "inputs": {
                            "jira_issue_key": "MM-821",
                            "source_design_path": "",
                            "constraints": "Prefer workflow payload.",
                        },
                        "taskTemplate": {
                            "slug": "jira-orchestrate",
                            "version": "1.0.0",
                        },
                    },
                },
            )

    assert expanded_parameters["task"] == legacy_task
    task = expanded_parameters["workflow"]
    assert expanded_parameters["stepCount"] == 26
    assert task["steps"][0]["tool"]["id"] == "jira.check_blockers"
    assert task["steps"][3]["tool"]["id"] == "jira.update_issue_status"
    assert "MM-821" in task["steps"][3]["instructions"]


@pytest.mark.asyncio
async def test_child_jira_orchestrate_run_persists_original_task_input_snapshot(
    tmp_path,
):
    now = datetime.now(UTC)
    parameters = {
        "requestType": "task",
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex_cli",
        "task": {
            "title": "Run Jira Orchestrate for MM-501",
            "instructions": "Run Jira Orchestrate for MM-501.",
            "runtime": {"mode": "codex_cli", "model": "gpt-5.4"},
            "publish": {"mode": "pr"},
            "git": {
                "repository": "MoonLadderStudios/MoonMind",
                "branch": "feature/mm-501",
            },
            "dependencies": ["MM-500"],
            "appliedStepTemplates": [
                {
                    "slug": "jira-orchestrate",
                    "version": "1.0.0",
                    "stepIds": ["step-1"],
                    "composition": {
                        "slug": "jira-orchestrate",
                        "includes": [
                            {"slug": "jira-fetch", "version": "1.0.0"}
                        ],
                    },
                },
            ],
            "authoredPresets": [
                {
                    "presetSlug": "jira-orchestrate",
                    "inputBindings": {"issueKey": "MM-501"},
                }
            ],
            "steps": [
                {
                    "id": "step-1",
                    "title": "First step",
                    "instructions": "Do the first step.",
                    "presetProvenance": {
                        "presetSlug": "jira-orchestrate",
                    },
                }
            ],
        },
    }

    async with _template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            source = TemporalExecutionCanonicalRecord(
                workflow_id="mm:child-run",
                run_id="run-child",
                namespace="default",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="owner-1",
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                close_status=None,
                entry="run",
                search_attributes={},
                memo={"title": "Child run"},
                artifact_refs=[],
                input_ref=None,
                plan_ref=None,
                manifest_ref=None,
                parameters=parameters,
                integration_state=None,
                pending_parameters_patch=None,
                paused=False,
                awaiting_external=False,
                waiting_reason=None,
                attention_required=False,
                step_count=0,
                wait_cycle_count=0,
                rerun_count=0,
                create_idempotency_key="jira-orchestrate:MM-404:STORY-001:MM-501",
                last_update_idempotency_key=None,
                last_update_response=None,
                created_at=now,
                started_at=None,
                updated_at=now,
                closed_at=None,
            )
            projection = TemporalExecutionRecord(
                workflow_id=source.workflow_id,
                run_id=source.run_id,
                namespace=source.namespace,
                workflow_type=source.workflow_type,
                owner_id=source.owner_id,
                owner_type=source.owner_type,
                state=source.state,
                close_status=source.close_status,
                entry=source.entry,
                search_attributes={},
                memo=dict(source.memo),
                artifact_refs=[],
                input_ref=None,
                plan_ref=None,
                manifest_ref=None,
                parameters=parameters,
                integration_state=None,
                pending_parameters_patch=None,
                paused=False,
                awaiting_external=False,
                waiting_reason=None,
                attention_required=False,
                step_count=0,
                wait_cycle_count=0,
                rerun_count=0,
                create_idempotency_key=source.create_idempotency_key,
                last_update_idempotency_key=None,
                last_update_response=None,
                projection_version=1,
                last_synced_at=now,
                sync_state=TemporalExecutionProjectionSyncState.FRESH,
                sync_error=None,
                source_mode=(
                    TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
                ),
                created_at=now,
                started_at=None,
                updated_at=now,
                closed_at=None,
            )
            session.add_all([source, projection])
            await session.commit()

            artifact_service = _FakeTaskInputSnapshotArtifactService()
            snapshot_ref = await _persist_child_run_task_input_snapshot(
                session=session,
                record=projection,
                parameters=parameters,
                artifact_service=artifact_service,
            )

            assert snapshot_ref == "art_task_snapshot_1"
            refreshed_source = await session.get(
                TemporalExecutionCanonicalRecord,
                "mm:child-run",
            )
            refreshed_projection = await session.get(
                TemporalExecutionRecord,
                "mm:child-run",
            )

    assert refreshed_source.memo["task_input_snapshot_ref"] == "art_task_snapshot_1"
    assert refreshed_projection.memo["task_input_snapshot_ref"] == "art_task_snapshot_1"
    assert refreshed_source.memo["task_input_snapshot_source_kind"] == "create"
    assert refreshed_projection.artifact_refs == ["art_task_snapshot_1"]
    assert artifact_service.create_calls[0]["principal"] == "owner-1"
    assert artifact_service.write_calls[0]["principal"] == "owner-1"
    assert artifact_service.create_calls[0]["link"] == {
        "namespace": "default",
        "workflow_id": "mm:child-run",
        "run_id": "run-child",
        "link_type": "input.original_snapshot",
        "label": "Original workflow input snapshot",
    }
    snapshot_payload = json.loads(artifact_service.write_calls[0]["payload"])
    assert snapshot_payload["draft"]["workflowShape"] == "multi_step"
    assert snapshot_payload["draft"]["repository"] == "MoonLadderStudios/MoonMind"
    assert snapshot_payload["draft"]["targetRuntime"] == "codex_cli"
    assert (
        snapshot_payload["draft"]["workflow"]["title"]
        == "Run Jira Orchestrate for MM-501"
    )
    authored = snapshot_payload["draft"]["authoredWorkflowInput"]
    assert authored["runtime"] == {"mode": "codex_cli", "model": "gpt-5.4"}
    assert authored["publish"] == {"mode": "pr"}
    assert authored["repository"] == "MoonLadderStudios/MoonMind"
    assert authored["branch"] == "feature/mm-501"
    assert authored["dependencyDeclarations"] == ["MM-500"]
    assert authored["finalSubmittedOrder"] == [{"stepId": "step-1", "ordinal": 0}]
    assert authored["pinnedPresetBindings"][0]["presetSlug"] == "jira-orchestrate"
    assert authored["includeTreeSummary"] == [
        {
            "presetSlug": "jira-orchestrate",
            "presetDigest": None,
            "includedSlug": "jira-fetch",
            "includedDigest": None,
        }
    ]
    assert authored["perStepProvenance"] == [
        {
            "stepId": "step-1",
            "ordinal": 0,
            "presetProvenance": {
                "presetSlug": "jira-orchestrate",
            },
        }
    ]


def test_runtime_planner_uses_branch_handoff_for_jira_output_when_task_publish_none():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "title": "Breakdown and Jira Create",
                "instructions": "Break down docs/Design.md into Jira stories.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "breakdown",
                        "tool": {"type": "skill", "name": "moonspec-breakdown"},
                        "instructions": "Extract MoonSpec stories.",
                    },
                    {
                        "id": "jira",
                        "tool": {"type": "skill", "name": "story.create_jira_issues"},
                        "instructions": "Create Jira issues from the generated breakdown.",
                        "storyOutput": {
                            "mode": "jira",
                            "jira": {
                                "projectKey": "MM",
                                "issueTypeName": "Story",
                                "dependencyMode": "linear_blocker_chain",
                            },
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    breakdown = plan["nodes"][0]
    jira = plan["nodes"][1]

    assert breakdown["inputs"]["storyOutput"]["mode"] == "jira"
    assert breakdown["inputs"]["publishMode"] == "branch"
    assert breakdown["inputs"]["targetBranch"].startswith(
        "breakdown-and-jira-create-"
    )
    assert "commit your work" in breakdown["inputs"]["instructions"]
    assert jira["inputs"]["publishMode"] == "none"
    assert jira["inputs"]["storyOutput"]["mode"] == "jira"
    assert jira["inputs"]["targetBranch"] == breakdown["inputs"]["targetBranch"]


def test_github_story_tools_route_as_direct_story_output_tools() -> None:
    assert "story.create_github_issues" in worker_runtime._STORY_OUTPUT_TASK_TOOLS
    assert (
        "story.create_github_issue_orchestrate_workflows"
        in worker_runtime._STORY_OUTPUT_TASK_TOOLS
    )
    assert (
        "story.create_github_issue_implement_workflows"
        in worker_runtime._STORY_OUTPUT_TASK_TOOLS
    )


def test_runtime_planner_preserves_github_orchestration_for_direct_story_tool() -> None:
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Create GitHub issue workflows.",
                "repository": "MoonLadderStudios/MoonMind",
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "type": "skill",
                        "title": "Create dependent GitHub Issue Implement workflow executions",
                        "instructions": "Create downstream workflows.",
                        "githubOrchestration": {
                            "task": {
                                "repository": "MoonLadderStudios/MoonMind",
                                "runtime": {"mode": "codex"},
                                "publish": {
                                    "mode": "pr",
                                    "mergeAutomation": {"enabled": True},
                                },
                            }
                        },
                        "skill": {
                            "id": "story.create_github_issue_implement_workflows"
                        },
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["inputs"]["githubOrchestration"] == {
        "task": {
            "repository": "MoonLadderStudios/MoonMind",
            "runtime": {"mode": "codex"},
            "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
        }
    }


def test_runtime_planner_does_not_require_pr_branch_for_jira_issue_creator():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Create Jira stories from artifacts/story-breakdowns/example.",
                "tool": {"type": "skill", "name": "jira-issue-creator"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert node["inputs"]["selectedSkill"] == "jira-issue-creator"
    assert node["inputs"]["publishMode"] == "none"
    assert node["inputs"]["instructions"].startswith("Use $jira-issue-creator.")
    assert "targetBranch" not in node["inputs"]
    assert "commit your work" not in node["inputs"]["instructions"]

def test_runtime_planner_does_not_require_pr_branch_for_jira_issue_updater():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Transition THOR-352 to In Progress.",
                "tool": {"type": "skill", "name": "jira-issue-updater"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert node["inputs"]["selectedSkill"] == "jira-issue-updater"
    assert node["inputs"]["publishMode"] == "none"
    assert node["inputs"]["instructions"].startswith("Use $jira-issue-updater.")
    assert "targetBranch" not in node["inputs"]
    assert "commit your work" not in node["inputs"]["instructions"]

def test_runtime_planner_does_not_require_pr_branch_for_jira_verify():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Verify KANDY-3607 against this branch.",
                "tool": {"type": "skill", "name": "jira-verify"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert node["inputs"]["selectedSkill"] == "jira-verify"
    assert node["inputs"]["publishMode"] == "none"
    assert node["inputs"]["instructions"].startswith("Use $jira-verify.")
    assert "targetBranch" not in node["inputs"]
    assert "commit your work" not in node["inputs"]["instructions"]

def test_runtime_planner_exposes_jira_verify_inputs_with_explicit_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Verify Jira issue THOR-709.",
                "tool": {"type": "skill", "name": "jira-verify"},
                "skill": {"name": "jira-verify"},
                "inputs": {
                    "jira_issue_key": "THOR-709",
                    "update_status": True,
                },
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith("Use $jira-verify.")
    assert "Selected skill inputs:" in node_inputs["instructions"]
    assert '"update_status": true' in node_inputs["instructions"]
    assert node_inputs["skill"] == {
        "name": "jira-verify",
        "inputs": {
            "jira_issue_key": "THOR-709",
            "update_status": True,
        },
    }

def test_runtime_planner_reads_inputs_from_nested_skill_with_tool_discriminator():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Verify Jira issue THOR-709.",
                "tool": {"type": "skill", "name": "jira-verify"},
                "skill": {
                    "name": "jira-verify",
                    "inputs": {
                        "jira_issue_key": "THOR-709",
                        "update_status": True,
                    },
                },
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert '"update_status": true' in node_inputs["instructions"]
    assert node_inputs["skill"]["inputs"]["update_status"] is True

def test_runtime_planner_appends_inputs_despite_authored_heading_collision():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": (
                    "Discuss the phrase Selected skill inputs:\n"
                    "without treating it as runtime data."
                ),
                "tool": {"type": "skill", "name": "jira-verify"},
                "inputs": {"update_status": True},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    instructions = plan["nodes"][0]["inputs"]["instructions"]
    assert instructions.count("Selected skill inputs:\n") == 2
    assert 'Selected skill inputs:\n{\n  "update_status": true\n}' in instructions

def test_runtime_planner_exposes_inputs_for_expanded_agent_skill_step():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Process the Jira verification steps.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "none"},
                "steps": [
                    {
                        "id": "verify-one",
                        "type": "skill",
                        "instructions": "Verify THOR-709.",
                        "skill": {
                            "name": "jira-verify",
                            "inputs": {
                                "jira_issue_key": "THOR-709",
                                "update_status": True,
                            },
                        },
                    },
                    {
                        "id": "verify-two",
                        "type": "skill",
                        "instructions": "Verify THOR-710.",
                        "skill": {
                            "name": "jira-verify",
                            "inputs": {"jira_issue_key": "THOR-710"},
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["selectedSkill"] == "jira-verify"
    assert '"update_status": true' in node_inputs["instructions"]
    assert node_inputs["skill"] == {
        "name": "jira-verify",
        "inputs": {
            "jira_issue_key": "THOR-709",
            "update_status": True,
        },
    }

def test_runtime_planner_does_not_require_pr_branch_for_jira_pr_verify():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Verify KANDY-3607 against PR #1640.",
                "tool": {"type": "skill", "name": "jira-pr-verify"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["tool"] == {
        "type": "agent_runtime",
        "name": "codex_cli",
    }
    assert node["inputs"]["selectedSkill"] == "jira-pr-verify"
    assert node["inputs"]["publishMode"] == "none"
    assert node["inputs"]["instructions"].startswith("Use $jira-pr-verify.")
    assert "targetBranch" not in node["inputs"]
    assert "commit your work" not in node["inputs"]["instructions"]

def test_runtime_planner_does_not_inherit_top_level_skill_for_blank_multi_step_entries():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "jira-issue-creator",
                    "inputs": {"repository": "MoonLadderStudios/MoonMind"},
                },
                "skill": {
                    "name": "jira-issue-creator",
                    "inputs": {"repository": "MoonLadderStudios/MoonMind"},
                    "inputContractDigest": "sha256:contract",
                    "contentDigest": "sha256:content",
                    "contentRef": "artifact:skill",
                },
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "steps": [
                    {"id": "one", "instructions": "Create the first Jira story."},
                    {
                        "id": "two",
                        "instructions": "Run a generic follow-up.",
                        "skill": {
                            "id": "auto",
                            "inputs": {"scope": "generic"},
                        },
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert len(plan["nodes"]) == 2
    blank_step = plan["nodes"][0]
    auto_step = plan["nodes"][1]
    for node in (blank_step, auto_step):
        assert node["tool"]["type"] == "agent_runtime"
        assert "selectedSkill" not in node["inputs"]
        assert node["inputs"]["publishMode"] == "pr"
        assert "targetBranch" in node["inputs"]
        assert "inputContractDigest" not in node["inputs"]
        assert "contentDigest" not in node["inputs"]
        assert "contentRef" not in node["inputs"]
        assert not node["inputs"]["instructions"].startswith(
            "Use $jira-issue-creator."
        )
    assert "skill" not in blank_step["inputs"]
    assert "inputs" not in blank_step["inputs"]
    assert auto_step["inputs"]["skill"] == {
        "id": "auto",
        "inputs": {"scope": "generic"},
    }
    assert auto_step["inputs"]["inputs"] == {"scope": "generic"}

def test_runtime_planner_single_step_tool_does_not_override_top_level_publish_scope():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "inputs": {"pr": "1434"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "steps": [
                    {
                        "id": "ignored",
                        "tool": {"type": "skill", "name": "jira-issue-creator"},
                        "instructions": "This single step is not expanded.",
                    }
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["inputs"]["selectedSkill"] == "pr-resolver"
    assert node["inputs"]["publishMode"] == "pr"
    assert "targetBranch" in node["inputs"]

def test_runtime_planner_invalid_step_list_keeps_non_jira_publish_scope():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "inputs": {"pr": "1434"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "steps": [
                    {"tool": {"type": "skill", "name": "jira-issue-creator"}},
                    "not-a-step",
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node = plan["nodes"][0]
    assert node["inputs"]["selectedSkill"] == "pr-resolver"
    assert node["inputs"]["publishMode"] == "pr"
    assert "targetBranch" in node["inputs"]

def test_runtime_planner_pr_resolver_injects_branch_selector_into_instruction():
    """When pr-resolver has no inputs.pr but git.startingBranch is set,
    the auto-generated instruction must include the branch as the pr selector
    so the agent knows which PR to target."""
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                },
                "git": {"startingBranch": "fix/my-feature-branch"},
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith(
        "Execute skill 'pr-resolver' with inputs:"
    )
    assert '"pr": "fix/my-feature-branch"' in node_inputs["instructions"]
    assert node_inputs["timeoutPolicy"] == {"timeout_seconds": 9000}
    assert plan["metadata"]["title"] == "fix/my-feature-branch"


def test_runtime_planner_pr_resolver_timeout_reaches_agent_execution_request(
    monkeypatch: pytest.MonkeyPatch,
):
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )
    plan = planner(
        inputs={
            "task": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "inputs": {"pr": "1434"},
                "runtime": {"mode": "codex_cli"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1"},
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        workflow_info,
    )

    node = plan["nodes"][0]
    request = MoonMindUserWorkflow()._build_agent_execution_request(
        node_inputs=node["inputs"],
        node_id=node["id"],
        tool_name=node["tool"]["name"],
    )

    assert request.timeout_policy == {"timeout_seconds": 9000}


def test_runtime_planner_pr_resolver_timeout_is_not_inherited_by_other_steps():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )
    plan = planner(
        inputs={
            "task": {
                "tool": {"type": "skill", "name": "pr-resolver"},
                "inputs": {"pr": "1434"},
                "runtime": {"mode": "codex_cli"},
                "steps": [
                    {
                        "id": "generic",
                        "tool": {"type": "agent_runtime", "name": "codex_cli"},
                        "instructions": "Perform generic work.",
                    },
                    {
                        "id": "jira",
                        "tool": {
                            "type": "agent_runtime",
                            "name": "jira-issue-creator",
                        },
                        "instructions": "Create a Jira issue.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert all("timeoutPolicy" not in node["inputs"] for node in plan["nodes"])


def test_runtime_planner_pr_resolver_uses_non_default_git_branch_as_selector():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                },
                "git": {"branch": "feature/current-pr"},
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert '"pr": "feature/current-pr"' in node_inputs["instructions"]
    assert plan["metadata"]["title"] == "feature/current-pr"


def test_runtime_planner_pr_resolver_includes_git_branch_selector_with_explicit_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Resolve the selected PR.",
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                },
                "git": {"branch": "feature/current-pr"},
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["instructions"].startswith("Resolve the selected PR.")
    assert "Selected skill inputs:" in node_inputs["instructions"]
    assert '"pr": "feature/current-pr"' in node_inputs["instructions"]
    assert plan["metadata"]["title"] == "feature/current-pr"


def test_runtime_planner_pr_resolver_title_uses_case_insensitive_tool_inputs():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "tool": {
                    "type": "skill",
                    "name": "PR-Resolver",
                    "inputs": {"branch": "fix/from-tool-inputs"},
                },
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert '"pr": "fix/from-tool-inputs"' in node_inputs["instructions"]
    assert plan["metadata"]["title"] == "fix/from-tool-inputs"


def test_runtime_planner_pr_resolver_reads_skill_args_when_tool_is_present():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    plan = planner(
        inputs={
            "task": {
                "instructions": "Resolve and merge pull request 2733.",
                "tool": {
                    "type": "skill",
                    "name": "pr-resolver",
                },
                "skill": {
                    "id": "pr-resolver",
                    "args": {"pr": 2733, "branch": "codex/pr-resolver-selector-guard"},
                },
                "runtime": {"mode": "claude_code"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][0]["inputs"]
    assert node_inputs["selectedSkill"] == "pr-resolver"
    assert plan["metadata"]["title"] == "2733"


def test_runtime_planner_requires_selector_for_pr_resolver_without_instructions():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "pr-resolver workflow requires workflow.tool.inputs.pr, "
            "workflow.tool.inputs.branch, workflow.git.startingBranch, "
            "or a non-default workflow.git.branch"
        ),
    ):
        planner(
            inputs={
                "task": {
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "runtime": {"mode": "claude_code"},
                }
            },
            parameters={},
            snapshot=snapshot,
        )

def test_build_agent_runtime_deps_uses_artifacts_env_without_double_nesting(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(artifacts_root))

    (
        store,
        supervisor,
        _launcher,
        _session_controller,
        workload_registry,
        workload_launcher,
        _session_store,
    ) = _build_agent_runtime_deps()

    assert store.store_root == tmp_path / "managed_runs"
    assert supervisor._log_streamer._storage._root == artifacts_root
    assert workload_registry.workspace_root == tmp_path
    assert "unreal-5_3-linux" in workload_registry.profile_ids
    assert workload_launcher is not None
    assert artifacts_root.is_dir()
    assert not (artifacts_root / "artifacts").exists()

def test_build_agent_runtime_deps_reuses_global_session_network(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path))
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_ARTIFACTS", str(artifacts_root))
    monkeypatch.delenv("MOONMIND_MANAGED_SESSION_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("MOONMIND_DOCKER_NETWORK", "shared-moonmind-network")
    monkeypatch.setenv("MOONMIND_URL", "http://moonmind-api:8000")

    (
        _store,
        _supervisor,
        _launcher,
        session_controller,
        _workload_registry,
        _workload_launcher,
        _session_store,
    ) = _build_agent_runtime_deps()

    assert session_controller._network_name == "shared-moonmind-network"
    assert session_controller._moonmind_url == "http://moonmind-api:8000"

def _make_snapshot():
    return SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

def test_runtime_planner_multi_step_generates_multiple_nodes_with_edges():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {"id": "s1", "instructions": "Step one instructions"},
                    {"id": "s2", "instructions": "Step two instructions"},
                    {"id": "s3", "instructions": "Step three instructions"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    nodes = plan["nodes"]
    edges = plan["edges"]

    assert len(nodes) == 3
    assert nodes[0]["id"] == "s1"
    assert nodes[0]["inputs"]["instructions"] == "Step one instructions"
    assert nodes[1]["id"] == "s2"
    assert nodes[1]["inputs"]["instructions"] == "Step two instructions"
    assert nodes[2]["id"] == "s3"
    assert nodes[2]["inputs"]["instructions"] == "Step three instructions"

    # All nodes use agent_runtime tool type
    for node in nodes:
        assert node["tool"]["type"] == "agent_runtime"
        assert node["tool"]["name"] == "jules"

    # Sequential edges
    assert len(edges) == 2
    assert edges[0] == {"from": "s1", "to": "s2"}
    assert edges[1] == {"from": "s2", "to": "s3"}


def test_mm786_runtime_planner_uses_per_step_runtime_selection():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {"id": "s1", "instructions": "Use default runtime."},
                    {
                        "id": "s2",
                        "instructions": "Use lower-cost runtime.",
                        "runtime": {
                            "mode": "claude_code",
                            "model": "gemini-2.5-flash",
                            "effort": "low",
                        },
                    },
                ],
                "runtime": {"mode": "codex_cli", "model": "gpt-5.4"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    nodes = plan["nodes"]
    assert nodes[0]["tool"]["name"] == "codex_cli"
    assert nodes[0]["inputs"]["runtime"]["mode"] == "codex_cli"
    assert nodes[1]["tool"]["name"] == "claude_code"
    assert nodes[1]["inputs"]["runtime"] == {
        "mode": "claude_code",
        "model": "gemini-2.5-flash",
        "effort": "low",
    }


def test_runtime_planner_multi_step_preserves_custom_keys():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {
                        "id": "s1",
                        "instructions": "Step one instructions",
                        "custom_field": "custom_value",
                        "another_field": 42,
                        "tool": {"name": "ignored_in_inputs"},
                    },
                    {
                        "id": "s2",
                        "instructions": "Step two instructions",
                    }
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    nodes = plan["nodes"]
    assert len(nodes) == 2

    s1_inputs = nodes[0]["inputs"]
    assert s1_inputs["instructions"] == "Step one instructions"
    assert s1_inputs["custom_field"] == "custom_value"
    assert s1_inputs["another_field"] == 42
    assert "id" not in s1_inputs
    assert "tool" not in s1_inputs
    assert "skill" not in s1_inputs

    s2_inputs = nodes[1]["inputs"]
    assert s2_inputs["instructions"] == "Step two instructions"
    assert "custom_field" not in s2_inputs

def test_runtime_planner_single_step_falls_back_to_single_node():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do the thing",
                "steps": [
                    {"id": "only", "instructions": "Only step"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    # Single-step array should use the single-node fallback path
    assert len(plan["nodes"]) == 1
    assert plan["edges"] == []
    assert plan["nodes"][0]["inputs"]["instructions"] == "Do the thing"

def test_runtime_planner_no_steps_falls_back_to_single_node():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do the thing",
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert len(plan["nodes"]) == 1
    assert plan["edges"] == []
    assert plan["nodes"][0]["inputs"]["instructions"] == "Do the thing"

def test_runtime_planner_multi_step_step_fallback_instructions():
    """When a step has no instructions, the task-level instructions are used."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Task-level fallback",
                "steps": [
                    {"id": "a", "instructions": "Explicit step A"},
                    {"id": "b"},  # no instructions
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert len(plan["nodes"]) == 2
    assert plan["nodes"][0]["inputs"]["instructions"] == "Explicit step A"
    assert plan["nodes"][1]["inputs"]["instructions"] == "Task-level fallback"

def test_runtime_planner_multi_step_auto_generated_ids():
    """When steps lack explicit IDs, sequential IDs are generated."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Objective",
                "steps": [
                    {"instructions": "First"},
                    {"instructions": "Second"},
                ],
                "runtime": {"mode": "jules"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert plan["nodes"][0]["id"] == "step-1"
    assert plan["nodes"][1]["id"] == "step-2"
    assert plan["edges"] == [{"from": "step-1", "to": "step-2"}]

def test_runtime_planner_publish_pr_appends_gh_suffix_for_cli_runtimes():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "claude_code"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    text = plan["nodes"][-1]["inputs"]["instructions"]
    assert "commit your work" in text
    assert "Do NOT push or create a pull request" in text
    assert "gh pr create" not in text


def test_runtime_planner_preserves_jira_orchestrate_pr_handoff_instructions():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run Jira Orchestrate for THOR-352.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [{"slug": "jira-orchestrate"}],
                "steps": [
                    {
                        "id": "implement",
                        "title": "Implement",
                        "tool": {"type": "skill", "name": "moonspec-implement"},
                        "instructions": "Implement the Jira issue.",
                    },
                    {
                        "id": "create-pr",
                        "title": "Open review request",
                        "annotations": {
                            "jiraOrchestrateRole": "pull-request-handoff",
                        },
                        "instructions": (
                            "Create a pull request and record pull_request_url."
                        ),
                    },
                    {
                        "id": "code-review",
                        "title": "Move Jira issue to Code Review",
                        "tool": {"type": "skill", "name": "jira-issue-updater"},
                        "instructions": "Move the Jira issue to Code Review.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    pr_node = plan["nodes"][1]
    assert pr_node["inputs"]["title"] == "Open review request"
    assert pr_node["inputs"]["publishMode"] == "pr"
    assert "Create a pull request" in pr_node["inputs"]["instructions"]
    assert (
        "Do NOT push or create a pull request"
        not in pr_node["inputs"]["instructions"]
    )
    assert "commit your work" not in pr_node["inputs"]["instructions"]


def test_runtime_planner_preserves_jira_implement_pr_handoff_instructions():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run Jira Implement for THOR-352.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [{"slug": "jira-implement"}],
                "steps": [
                    {
                        "id": "implement",
                        "title": "Implement",
                        "instructions": "Implement the Jira issue.",
                    },
                    {
                        "id": "create-pr",
                        "title": "Create pull request",
                        "annotations": {
                            "jiraImplementRole": "pull-request-handoff",
                        },
                        "instructions": (
                            "Create a pull request and record pull_request_url."
                        ),
                    },
                    {
                        "id": "finalize-jira",
                        "title": "Finalize Jira status",
                        "tool": {"type": "skill", "name": "jira-issue-updater"},
                        "instructions": "Move the Jira issue after PR creation.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    pr_node = plan["nodes"][1]
    assert pr_node["inputs"]["title"] == "Create pull request"
    assert pr_node["inputs"]["publishMode"] == "pr"
    assert "Create a pull request" in pr_node["inputs"]["instructions"]
    assert (
        "Do NOT push or create a pull request"
        not in pr_node["inputs"]["instructions"]
    )
    assert "commit your work" not in pr_node["inputs"]["instructions"]


def test_runtime_planner_preserves_github_issue_pr_handoff_instructions():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Implement GitHub issue org/repo#2231.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [{"slug": "github-issue-implement"}],
                "steps": [
                    {
                        "id": "implement",
                        "title": "Implement",
                        "instructions": "Implement the GitHub issue.",
                    },
                    {
                        "id": "create-pr",
                        "title": "Create pull request",
                        "annotations": {
                            "issueImplementRole": "pull-request-handoff",
                        },
                        "instructions": (
                            "Create a pull request and record pull_request_url."
                        ),
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    pr_node = plan["nodes"][1]
    assert pr_node["inputs"]["publishMode"] == "pr"
    assert "Create a pull request" in pr_node["inputs"]["instructions"]
    assert (
        "Do NOT push or create a pull request"
        not in pr_node["inputs"]["instructions"]
    )
    assert "commit your work" not in pr_node["inputs"]["instructions"]


@pytest.mark.parametrize("step_index", [12, 13])
def test_runtime_planner_preserves_jira_orchestrate_pr_handoff_step_id_fallback(
    step_index,
):
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run Jira Orchestrate for THOR-352.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [{"slug": "jira-orchestrate"}],
                "steps": [
                    {
                        "id": "tpl:jira-orchestrate:1.0.0:11:verify",
                        "title": "Verify completion",
                        "tool": {"type": "skill", "name": "moonspec-verify"},
                        "instructions": "Verify the Jira issue.",
                    },
                    {
                        "id": f"tpl:jira-orchestrate:1.0.0:{step_index:02d}:create-pr",
                        "title": "Create pull request",
                        "instructions": (
                            "Create a pull request and record pull_request_url."
                        ),
                    },
                    {
                        "id": "tpl:jira-orchestrate:1.0.0:14:code-review",
                        "title": "Move Jira issue to Code Review",
                        "tool": {"type": "skill", "name": "jira-issue-updater"},
                        "instructions": "Move the Jira issue to Code Review.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    pr_node = plan["nodes"][1]
    assert pr_node["inputs"]["title"] == "Create pull request"
    assert "Create a pull request" in pr_node["inputs"]["instructions"]
    assert (
        "Do NOT push or create a pull request"
        not in pr_node["inputs"]["instructions"]
    )
    assert "commit your work" not in pr_node["inputs"]["instructions"]


def test_runtime_planner_preserves_jira_implement_pr_handoff_step_id_fallback():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Run Jira Implement for THOR-352.",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
                "appliedStepTemplates": [{"slug": "jira-implement"}],
                "steps": [
                    {
                        "id": "tpl:jira-implement:1.0.0:06:verify",
                        "title": "Verify implementation",
                        "instructions": "Verify the Jira issue.",
                    },
                    {
                        "id": "tpl:jira-implement:1.0.0:07:create-pr",
                        "title": "Create pull request",
                        "instructions": (
                            "Create a pull request and record pull_request_url."
                        ),
                    },
                    {
                        "id": "tpl:jira-implement:1.0.0:08:finalize",
                        "title": "Finalize Jira status",
                        "tool": {"type": "skill", "name": "jira-issue-updater"},
                        "instructions": "Finalize Jira after PR creation.",
                    },
                ],
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    pr_node = plan["nodes"][1]
    assert pr_node["inputs"]["title"] == "Create pull request"
    assert "Create a pull request" in pr_node["inputs"]["instructions"]
    assert (
        "Do NOT push or create a pull request"
        not in pr_node["inputs"]["instructions"]
    )
    assert "commit your work" not in pr_node["inputs"]["instructions"]


def test_runtime_planner_publish_pr_skips_gh_suffix_for_jules():
    """Jules uses API automationMode AUTO_CREATE_PR; do not inject gh CLI text."""
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "jules"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    text = plan["nodes"][-1]["inputs"]["instructions"]
    assert text == "Do work"
    assert "gh pr create" not in text

def test_runtime_planner_publish_pr_uses_task_title_for_target_branch_prefix():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Refine redirect handling.",
                "title": "Fix login redirect",
                "runtime": {"mode": "claude_code"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    target = plan["nodes"][-1]["inputs"]["targetBranch"]
    assert target.startswith("fix-login-redirect-")
    assert re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{8}", target)

def test_runtime_planner_publish_pr_treats_authored_branch_as_base():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "title": "Fix create branch publish",
                "git": {"branch": "main"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][-1]["inputs"]
    assert node_inputs["branch"] == "main"
    assert node_inputs["startingBranch"] == "main"
    assert node_inputs["targetBranch"] != "main"
    assert node_inputs["targetBranch"].startswith("fix-create-branch-publish-")
    assert re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{8}", node_inputs["targetBranch"])


def test_runtime_planner_publish_pr_flattens_publish_base_for_managed_fetch():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Continue the follow-up work",
                "title": "Complete create-first remediation backend",
                "git": {"branch": "use-this-preselected-single-story-reques-e5921fb9"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr", "baseBranch": "main"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][-1]["inputs"]
    assert node_inputs["branch"] == "use-this-preselected-single-story-reques-e5921fb9"
    assert (
        node_inputs["startingBranch"]
        == "use-this-preselected-single-story-reques-e5921fb9"
    )
    assert node_inputs["publishBaseBranch"] == "main"
    assert node_inputs["targetBranch"] != node_inputs["startingBranch"]
    assert node_inputs["targetBranch"].startswith(
        "complete-create-first-remediation-backen-"
    )


@pytest.mark.parametrize(
    ("inputs", "parameters", "expected_branch"),
    [
        (
            {"task": {"git": {"branch": "from-git"}}},
            {"branch": "from-params"},
            "from-git",
        ),
        (
            {"task": {"branch": "from-task", "inputs": {"branch": "from-skill"}}},
            {"branch": "from-params"},
            "from-task",
        ),
        (
            {"task": {"inputs": {"branch": "from-skill"}}},
            {"branch": "from-params"},
            "from-skill",
        ),
        ({}, {"branch": "from-params"}, "from-params"),
        ({"branch": "from-input"}, {}, "from-input"),
        (
            {"task": {"git": {"defaultBranch": "from-default"}}},
            {},
            "from-default",
        ),
    ],
)
def test_runtime_planner_mm669_authored_branch_uses_documented_fallback_chain(
    inputs,
    parameters,
    expected_branch,
):
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()
    task_overrides = dict(inputs.get("task", {}))
    top_level_inputs = {key: value for key, value in inputs.items() if key != "task"}

    task = {
        "instructions": "Do work",
        "title": "MM-669 branch resolution",
        "runtime": {"mode": "codex_cli"},
        "publish": {"mode": "pr"},
        **task_overrides,
    }
    plan = planner(
        inputs={**top_level_inputs, "task": task},
        parameters={"publishMode": "pr", **parameters},
        snapshot=snapshot,
    )

    node_inputs = plan["nodes"][-1]["inputs"]
    assert node_inputs["branch"] == expected_branch
    assert node_inputs["startingBranch"] == expected_branch

def test_runtime_planner_mm669_pr_head_uses_workspace_metadata_before_generated_branch():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "title": "MM-669 work branch",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={
            "publishMode": "pr",
            "targetBranch": "legacy-top-level-head",
            "workspaceSpec": {"targetBranch": "workspace-head"},
        },
        snapshot=snapshot,
    )

    assert plan["nodes"][-1]["inputs"]["targetBranch"] == "workspace-head"

def test_runtime_planner_rejects_pr_resolver_with_only_jira_instructions_and_base_branch():
    planner = _build_runtime_planner()
    snapshot = SimpleNamespace(
        digest="reg:sha256:test",
        artifact_ref="art_registry_123",
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "pr-resolver workflow requires workflow.tool.inputs.pr, "
            "workflow.tool.inputs.branch, workflow.git.startingBranch, "
            "or a non-default workflow.git.branch"
        ),
    ):
        planner(
            inputs={
                "task": {
                    "instructions": "Verify MM-940 and move to DONE if it is a PASS",
                    "tool": {
                        "type": "skill",
                        "name": "pr-resolver",
                    },
                    "git": {"branch": "main"},
                    "runtime": {"mode": "claude_code"},
                }
            },
            parameters={},
            snapshot=snapshot,
        )

def test_runtime_planner_mm669_pr_head_ignores_legacy_top_level_target_branch():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "title": "MM-669 generated head",
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={"publishMode": "pr", "targetBranch": "legacy-top-level-head"},
        snapshot=snapshot,
    )

    target_branch = plan["nodes"][-1]["inputs"]["targetBranch"]
    assert target_branch != "legacy-top-level-head"
    assert target_branch.startswith("mm-669-generated-head-")

def test_runtime_planner_mm669_pr_head_ignores_legacy_git_target_branch():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "title": "MM-669 generated head from git legacy",
                "git": {"targetBranch": "legacy-git-head"},
                "runtime": {"mode": "codex_cli"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={"publishMode": "pr"},
        snapshot=snapshot,
    )

    target_branch = plan["nodes"][-1]["inputs"]["targetBranch"]
    assert target_branch != "legacy-git-head"
    assert target_branch.startswith("mm-669-generated-head-from-git-legacy-")

def test_runtime_planner_mm669_pr_mode_errors_when_head_branch_unresolved(
    monkeypatch: pytest.MonkeyPatch,
):
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.worker_runtime._generate_runtime_pr_branch",
        lambda _prefix: "",
    )

    with pytest.raises(
        RuntimeError,
        match="publishMode 'pr' requested but no PR head branch could be resolved",
    ):
        planner(
            inputs={
                "task": {
                    "instructions": "Do work",
                    "title": "MM-669 unresolved head",
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                }
            },
            parameters={"publishMode": "pr"},
            snapshot=snapshot,
        )

def test_runtime_planner_publish_pr_uses_step_title_for_target_branch_prefix():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "runtime": {"mode": "claude_code"},
                "tool": {"name": "auto", "type": "agent_runtime"},
                "steps": [
                    {"title": "Create PR-friendly branch", "instructions": "Plan step"},
                ],
                "publish": {"mode": "pr"},
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    target = plan["nodes"][0]["inputs"]["targetBranch"]
    assert target.startswith("create-pr-friendly-branch-")
    assert re.fullmatch(r"[a-z0-9-]+-[0-9a-f]{8}", target)

def test_runtime_planner_publish_pr_propagates_commit_message_override():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "claude_code"},
                "publish": {
                    "mode": "pr",
                    "commitMessage": "Use producer commit text",
                },
            }
        },
        parameters={},
        snapshot=snapshot,
    )

    assert (
        plan["nodes"][-1]["inputs"]["commitMessage"]
        == "Use producer commit text"
    )

def test_runtime_planner_publish_pr_falls_back_to_top_level_commit_message():
    planner = _build_runtime_planner()
    snapshot = _make_snapshot()

    plan = planner(
        inputs={
            "task": {
                "instructions": "Do work",
                "runtime": {"mode": "claude_code"},
                "publish": {"mode": "pr"},
            }
        },
        parameters={"publishMode": "pr", "commitMessage": "Top-level commit text"},
        snapshot=snapshot,
    )

    assert plan["nodes"][-1]["inputs"]["commitMessage"] == "Top-level commit text"

def test_enforce_codex_config_skips_non_managed_fleet() -> None:
    with patch("api_service.scripts.ensure_codex_config.ensure_codex_config") as mock_ensure:
        _enforce_codex_config_for_managed_fleet(WORKFLOW_FLEET)
    mock_ensure.assert_not_called()

@pytest.mark.parametrize("fleet", [SANDBOX_FLEET, AGENT_RUNTIME_FLEET])
def test_enforce_codex_config_runs_for_managed_fleets(fleet: str) -> None:
    with patch("api_service.scripts.ensure_codex_config.ensure_codex_config") as mock_ensure:
        mock_ensure.return_value = SimpleNamespace(path="/tmp/codex-config.toml")
        _enforce_codex_config_for_managed_fleet(fleet)
    mock_ensure.assert_called_once_with()

@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.start_healthcheck_server")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_workflow_fleet(
    mock_worker_cls,
    mock_connect,
    mock_describe,
    mock_healthcheck,
):
    # Setup mocks
    mock_healthcheck_server = MagicMock()
    mock_healthcheck_server.wait_closed = AsyncMock()
    mock_healthcheck.return_value = mock_healthcheck_server
    mock_topology = MagicMock()
    mock_topology.fleet = WORKFLOW_FLEET
    mock_topology.task_queues = ["mm.workflow.user.v2", "mm.workflow"]
    mock_topology.concurrency_limit = 7
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker_v2 = MagicMock()
    mock_worker_v2.run = AsyncMock()
    mock_worker_replay = MagicMock()
    mock_worker_replay.run = AsyncMock()
    mock_worker_cls.side_effect = [mock_worker_v2, mock_worker_replay]

    # Run
    await main_async()

    # Verify Worker creation uses the mock workflows
    assert mock_worker_cls.call_count == 2
    assert [call.kwargs["task_queue"] for call in mock_worker_cls.call_args_list] == [
        "mm.workflow.user.v2",
        "mm.workflow",
    ]
    kwargs = mock_worker_cls.call_args_list[0].kwargs
    from moonmind.workflows.temporal.workflows.agent_session import (
        MoonMindAgentSessionWorkflow,
    )
    from moonmind.workflows.temporal.workflows.container_job import (
        MoonMindContainerJobWorkflow,
    )
    from moonmind.workflows.temporal.workflows.provider_profile_manager import MoonMindProviderProfileManagerWorkflow
    from moonmind.workflows.temporal.workflows.oauth_session import MoonMindOAuthSessionWorkflow as MoonMindOAuthSession
    from moonmind.workflows.temporal.workflows.merge_automation import (
        MoonMindMergeAutomationWorkflow,
    )
    from moonmind.workflows.temporal.workflows.pr_resolver import (
        MoonMindPRResolverWorkflow,
    )
    from moonmind.workflows.temporal.workflows.managed_session_reconcile import (
        MoonMindManagedSessionReconcileWorkflow,
    )
    from moonmind.workflows.temporal.workflows.managed_runtime_workspace_cleanup import (
        MoonMindManagedRuntimeWorkspaceCleanupWorkflow,
    )
    from moonmind.workflows.temporal.workflows.omnigent_oauth_host_janitor import (
        MoonMindOmnigentOAuthHostJanitorWorkflow,
    )
    assert kwargs["workflows"] == (
        MoonMindUserWorkflow,
        MoonMindContainerJobWorkflow,
        MoonMindManifestIngest,
        MoonMindProviderProfileManagerWorkflow,
        MoonMindAgentSessionWorkflow,
        MoonMindManagedSessionReconcileWorkflow,
        MoonMindManagedRuntimeWorkspaceCleanupWorkflow,
        MoonMindAgentRun,
        MoonMindOAuthSession,
        MoonMindOmnigentOAuthHostJanitorWorkflow,
        MoonMindMergeAutomationWorkflow,
        MoonMindPRResolverWorkflow,
    )
    assert kwargs["activities"] == (
        resolve_adapter_metadata,
        get_activity_route,
        resolve_external_adapter,
        external_adapter_execution_style,
    )
    assert "deployment_config" not in kwargs
    assert "build_id" not in kwargs
    assert "use_worker_versioning" not in kwargs
    assert kwargs["max_concurrent_workflow_tasks"] == 7
    assert "max_concurrent_activities" not in kwargs

    # Verify worker run is called
    mock_worker_v2.run.assert_awaited_once()
    mock_worker_replay.run.assert_awaited_once()

@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.start_healthcheck_server")
@patch("moonmind.workflows.temporal.worker_runtime._build_runtime_activities")
@patch("moonmind.workflows.temporal.worker_runtime.describe_configured_worker")
@patch("moonmind.workflows.temporal.worker_runtime.Client.connect")
@patch("moonmind.workflows.temporal.worker_runtime.Worker")
async def test_main_async_activity_fleet(
    mock_worker_cls,
    mock_connect,
    mock_describe,
    mock_runtime_activities,
    mock_healthcheck,
):
    # Setup mocks
    mock_healthcheck_server = MagicMock()
    mock_healthcheck_server.wait_closed = AsyncMock()
    mock_healthcheck.return_value = mock_healthcheck_server
    mock_topology = MagicMock()
    mock_topology.fleet = "artifacts"
    mock_topology.task_queues = ["mm.activity.artifacts"]
    mock_topology.concurrency_limit = 3
    mock_describe.return_value = mock_topology

    mock_client = MagicMock()
    mock_connect.return_value = mock_client

    mock_worker = MagicMock()
    mock_worker_cls.return_value = mock_worker
    mock_worker.run = AsyncMock()

    @activity.defn(name="test.handler")
    async def test_handler() -> None:
        return None

    mock_resources = AsyncMock()
    mock_runtime_activities.return_value = (mock_resources, [test_handler])

    # Run
    await main_async()

    # Verify Worker creation uses activities
    mock_worker_cls.assert_called_once()
    kwargs = mock_worker_cls.call_args.kwargs
    assert kwargs["task_queue"] == "mm.activity.artifacts"
    assert kwargs["workflows"] == ()
    assert kwargs["activities"] == (test_handler,)
    assert "deployment_config" not in kwargs
    assert "build_id" not in kwargs
    assert "use_worker_versioning" not in kwargs
    assert kwargs["max_concurrent_activities"] == 3
    assert "max_concurrent_workflow_tasks" not in kwargs

    # Verify worker run is called
    mock_runtime_activities.assert_awaited_once_with(mock_topology)
    mock_worker.run.assert_awaited_once()
    mock_resources.aclose.assert_awaited_once()

@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime._build_agent_runtime_deps")
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalAgentRuntimeActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalIntegrationActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalProposalActivities")
async def test_build_runtime_activities_injects_concrete_handlers(
    mock_proposal_activities_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_dispatcher_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_agent_runtime_activities_cls,
    mock_build_bindings,
    mock_build_deps,
):
    run_store = MagicMock()
    run_supervisor = MagicMock()
    run_supervisor.reconcile = AsyncMock(return_value=[])
    run_launcher = MagicMock()
    session_controller = MagicMock()
    session_controller.reconcile = AsyncMock(return_value=[])
    session_controller.reap_orphan_session_containers = AsyncMock(
        return_value=MagicMock()
    )
    workload_registry = MagicMock()
    workload_launcher = MagicMock()
    session_store = MagicMock()
    mock_build_deps.return_value = (
        run_store,
        run_supervisor,
        run_launcher,
        session_controller,
        workload_registry,
        workload_launcher,
        session_store,
    )
    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = "artifacts"

    mock_binding = MagicMock()
    mock_binding.handler = "artifact_handler"
    mock_build_bindings.return_value = [mock_binding]

    with patch(
        "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
        side_effect=_fake_session_context,
    ):
        resources, handlers = await _build_runtime_activities(topology)

    assert handlers == [
        "artifact_handler",
        resolve_adapter_metadata,
        get_activity_route,
        resolve_external_adapter,
        external_adapter_execution_style,
    ]
    mock_artifact_activities_cls.assert_called_once_with(ANY)
    mock_plan_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        planner=ANY,
    )
    mock_sandbox_activities_cls.assert_called_once_with(
        artifact_service=ANY
    )
    mock_jules_activities_cls.assert_called_once_with(
        artifact_service=ANY
    )
    mock_agent_runtime_activities_cls.assert_not_called()
    mock_build_deps.assert_not_called()
    run_supervisor.reconcile.assert_not_awaited()
    session_controller.reconcile.assert_not_awaited()
    session_controller.reap_orphan_session_containers.assert_not_awaited()
    mock_dispatcher_cls.assert_called_once_with()
    deployment_handler_calls = [
        call
        for call in mock_dispatcher_cls.return_value.register_skill.call_args_list
        if call.kwargs.get("skill_name") == "deployment.update_compose_stack"
    ]
    assert deployment_handler_calls == []
    mock_skill_activities_cls.assert_called_once_with(
        dispatcher=mock_dispatcher_cls.return_value,
        artifact_service=ANY,
    )
    mock_build_bindings.assert_called_once_with(
        fleet="artifacts",
        artifact_activities=mock_artifact_activities_cls.return_value,
        plan_activities=mock_plan_activities_cls.return_value,
        manifest_activities=ANY,
        skill_activities=mock_skill_activities_cls.return_value,
        sandbox_activities=mock_sandbox_activities_cls.return_value,
        integration_activities=mock_jules_activities_cls.return_value,
        agent_runtime_activities=None,
        proposal_activities=mock_proposal_activities_cls.return_value,
        review_activities=ANY,
        agent_skills_activities=ANY,
    )
    mock_proposal_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        proposal_service_factory=ANY,
    )
    proposal_service_factory = mock_proposal_activities_cls.call_args.kwargs[
        "proposal_service_factory"
    ]
    assert callable(proposal_service_factory)
    import typing
    assert isinstance(proposal_service_factory(), typing.AsyncContextManager)
    await resources.aclose()

@pytest.mark.asyncio
async def test_proposal_service_factory_uses_delivery_enabled_service() -> None:
    service = object()

    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    with (
        patch(
            "api_service.db.base.get_async_session_context",
            side_effect=_fake_session_context,
        ),
        patch(
            "moonmind.workflows.get_workflow_proposal_service",
            return_value=service,
        ) as factory,
    ):
        proposal_service_factory = worker_runtime._build_proposal_service_factory()
        async with proposal_service_factory() as resolved:
            assert resolved is service

    factory.assert_called_once_with("session")

@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime._build_agent_runtime_deps")
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalAgentRuntimeActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalIntegrationActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalProposalActivities")
async def test_build_runtime_activities_reconciles_managed_sessions_only_on_agent_runtime_fleet(
    mock_proposal_activities_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_dispatcher_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_agent_runtime_activities_cls,
    mock_build_bindings,
    mock_build_deps,
):
    run_store = MagicMock()
    run_supervisor = MagicMock()
    run_supervisor.reconcile = AsyncMock(return_value=[])
    run_launcher = MagicMock()
    session_controller = MagicMock()
    session_controller.reconcile = AsyncMock(return_value=[])
    session_controller.reap_orphan_session_containers = AsyncMock(
        return_value=MagicMock()
    )
    workload_registry = MagicMock()
    workload_launcher = MagicMock()
    session_store = MagicMock()
    mock_build_deps.return_value = (
        run_store,
        run_supervisor,
        run_launcher,
        session_controller,
        workload_registry,
        workload_launcher,
        session_store,
    )

    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = AGENT_RUNTIME_FLEET

    mock_binding = MagicMock()
    mock_binding.handler = "agent_runtime_handler"
    mock_build_bindings.return_value = [mock_binding]

    with (
        patch("moonmind.workflows.temporal.worker_runtime.settings") as mock_settings,
        patch(
            "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
            side_effect=_fake_session_context,
        ),
        patch(
            "moonmind.workflows.temporal.worker_runtime.DockerContainerJobBackend"
        ) as mock_backend_cls,
    ):
        mock_settings.workflow.workflow_docker_mode = "profiles"
        mock_backend_cls.return_value.check_readiness = AsyncMock()
        mock_backend_cls.return_value.network_ready = AsyncMock(return_value=True)
        resources, handlers = await _build_runtime_activities(topology)

    mock_backend_cls.return_value.check_readiness.assert_awaited_once()

    assert handlers == [
        "agent_runtime_handler",
        resolve_adapter_metadata,
        get_activity_route,
        resolve_external_adapter,
        external_adapter_execution_style,
    ]
    mock_build_deps.assert_called_once_with(artifact_service=ANY)
    run_supervisor.reconcile.assert_awaited_once()
    session_controller.reconcile.assert_awaited_once()
    session_controller.reap_orphan_session_containers.assert_awaited_once()
    deployment_handler_calls = [
        call
        for call in mock_dispatcher_cls.return_value.register_skill.call_args_list
        if call.kwargs.get("skill_name") == "deployment.update_compose_stack"
    ]
    assert deployment_handler_calls == []
    mock_agent_runtime_activities_cls.assert_called_once_with(
        artifact_service=ANY,
        run_store=run_store,
        run_supervisor=run_supervisor,
        run_launcher=run_launcher,
        session_controller=session_controller,
        session_store=session_store,
        workload_registry=workload_registry,
        workload_launcher=workload_launcher,
        workflow_docker_mode="profiles",
        raw_docker_cli_enabled=False,
        container_job_backend=ANY,
    )
    mock_build_bindings.assert_called_once_with(
        fleet=AGENT_RUNTIME_FLEET,
        artifact_activities=mock_artifact_activities_cls.return_value,
        plan_activities=mock_plan_activities_cls.return_value,
        manifest_activities=ANY,
        skill_activities=mock_skill_activities_cls.return_value,
        sandbox_activities=mock_sandbox_activities_cls.return_value,
        integration_activities=mock_jules_activities_cls.return_value,
        agent_runtime_activities=mock_agent_runtime_activities_cls.return_value,
        proposal_activities=mock_proposal_activities_cls.return_value,
        review_activities=ANY,
        agent_skills_activities=ANY,
    )
    await resources.aclose()


@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalAgentRuntimeActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalIntegrationActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.SkillActivityDispatcher")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactService")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactRepository")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalProposalActivities")
async def test_build_runtime_activities_registers_deployment_tool_only_on_deployment_fleet(
    mock_proposal_activities_cls,
    mock_repository_cls,
    mock_service_cls,
    mock_artifact_activities_cls,
    mock_plan_activities_cls,
    mock_dispatcher_cls,
    mock_skill_activities_cls,
    mock_sandbox_activities_cls,
    mock_jules_activities_cls,
    mock_agent_runtime_activities_cls,
    mock_build_bindings,
):
    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = DEPLOYMENT_FLEET

    mock_binding = MagicMock()
    mock_binding.handler = "deployment_handler"
    mock_build_bindings.return_value = [mock_binding]

    with patch(
        "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
        side_effect=_fake_session_context,
    ):
        resources, handlers = await _build_runtime_activities(topology)

    assert handlers[0] == "deployment_handler"
    mock_agent_runtime_activities_cls.assert_not_called()
    deployment_handler_calls = [
        call
        for call in mock_dispatcher_cls.return_value.register_skill.call_args_list
        if call.kwargs.get("skill_name") == "deployment.update_compose_stack"
    ]
    assert len(deployment_handler_calls) == 1
    mock_build_bindings.assert_called_once_with(
        fleet=DEPLOYMENT_FLEET,
        artifact_activities=mock_artifact_activities_cls.return_value,
        plan_activities=mock_plan_activities_cls.return_value,
        manifest_activities=ANY,
        skill_activities=mock_skill_activities_cls.return_value,
        sandbox_activities=mock_sandbox_activities_cls.return_value,
        integration_activities=mock_jules_activities_cls.return_value,
        agent_runtime_activities=None,
        proposal_activities=mock_proposal_activities_cls.return_value,
        review_activities=ANY,
        agent_skills_activities=ANY,
    )
    await resources.aclose()

@pytest.mark.asyncio
@patch("moonmind.workflows.temporal.worker_runtime.settings")
@patch("moonmind.workflows.temporal.worker_runtime.build_worker_activity_bindings")
@patch("moonmind.workflows.temporal.worker_runtime._build_agent_runtime_deps")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalAgentRuntimeActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalReviewActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalProposalActivities")
@patch("moonmind.workflows.temporal.worker_runtime.AgentSkillsActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalArtifactActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalPlanActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalManifestActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSkillActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalSandboxActivities")
@patch("moonmind.workflows.temporal.worker_runtime.TemporalIntegrationActivities")
async def test_build_runtime_activities_registers_unrestricted_mode(
    mock_integration_activities_cls,
    mock_sandbox_activities_cls,
    mock_skill_activities_cls,
    mock_manifest_activities_cls,
    mock_plan_activities_cls,
    mock_artifact_activities_cls,
    mock_agent_skills_activities_cls,
    mock_proposal_activities_cls,
    mock_review_activities_cls,
    mock_agent_runtime_activities_cls,
    mock_build_deps,
    mock_build_bindings,
    mock_settings,
):
    run_store = MagicMock()
    run_supervisor = MagicMock()
    run_supervisor.reconcile = AsyncMock(return_value=[])
    run_launcher = MagicMock()
    session_controller = MagicMock()
    session_controller.reconcile = AsyncMock(return_value=[])
    workload_registry = MagicMock()
    workload_launcher = MagicMock()
    session_store = MagicMock()
    mock_build_deps.return_value = (
        run_store,
        run_supervisor,
        run_launcher,
        session_controller,
        workload_registry,
        workload_launcher,
        session_store,
    )
    mock_settings.workflow.workflow_docker_mode = "unrestricted"

    @asynccontextmanager
    async def _fake_session_context():
        yield "session"

    topology = MagicMock()
    topology.fleet = AGENT_RUNTIME_FLEET

    mock_binding = MagicMock()
    mock_binding.handler = "agent_runtime_handler"
    mock_build_bindings.return_value = [mock_binding]

    with (
        patch(
            "moonmind.workflows.temporal.worker_runtime.get_async_session_context",
            side_effect=_fake_session_context,
        ),
        patch(
            "moonmind.workflows.temporal.worker_runtime.DockerContainerJobBackend"
        ) as mock_backend_cls,
    ):
        mock_backend_cls.return_value.check_readiness = AsyncMock()
        mock_backend_cls.return_value.network_ready = AsyncMock(return_value=True)
        resources, _handlers = await _build_runtime_activities(topology)

    mock_agent_runtime_activities_cls.assert_called_once()
    assert mock_agent_runtime_activities_cls.call_args.kwargs["workflow_docker_mode"] == "unrestricted"
    # Legacy workload handlers are not registered on the agent-facing dispatcher.
    await resources.aclose()
