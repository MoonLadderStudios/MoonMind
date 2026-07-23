"""Unit tests for preset catalog/save services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import UniqueConstraint, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

from api_service.db.models import (
    Base,
    Preset,
    PresetRecent,
    PresetReleaseStatus,
    PresetScopeType,
)
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetNotFoundError,
    PresetValidationError,
    _validate_moonspec_remediation_topology,
)
from api_service.services.presets.save import PresetSaveService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.helpers.step_type_payloads import preset_step, skill_step, tool_step

pytestmark = [pytest.mark.asyncio]

@asynccontextmanager
async def template_db(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/preset_catalog.db"
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

def _write_seed_template(seed_dir, seed_data: dict) -> None:
    seed_dir.mkdir(exist_ok=True)
    seed_file = seed_dir / f"{seed_data['slug']}.yaml"
    with open(seed_file, "w") as f:
        yaml.dump(seed_data, f)

async def test_create_and_expand_template_deterministic_ids(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug="pr-check",
                title="PR Check",
                description="Template for PR checks",
                scope="personal",
                scope_ref=str(user_id),
                tags=["quality"],
                inputs_schema=[
                    {
                        "name": "summary",
                        "label": "Summary",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Review",
                        "instructions": "Review change: {{ inputs.summary }}",
                        "skill": {
                            "id": "auto",
                            "args": {},
                            "requiredCapabilities": ["docker"],
                        },
                    }
                ],
                annotations={},
                required_capabilities=["codex"],
                created_by=user_id,
            )
            assert created["slug"] == "pr-check"

            expanded = await service.expand_template(
                slug="pr-check",
                scope="personal",
                scope_ref=str(user_id),
                inputs={"summary": "fix failing tests"},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    assert len(expanded["steps"]) == 1
    assert expanded["steps"][0]["id"].startswith("tpl:pr-check:01:")
    assert "fix failing tests" in expanded["steps"][0]["instructions"]
    assert set(expanded["capabilities"]) >= {"codex", "docker"}
    assert expanded["appliedTemplate"]["slug"] == "pr-check"
    assert "version" not in expanded["appliedTemplate"]
    assert "checkpointBranching" not in expanded


async def test_mm1171_preset_runtime_tier_intent_is_validated_and_preserved(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="tiered-workflow",
                title="Tiered Workflow",
                description="Template carrying MM-1168 model tier intent.",
                scope="personal",
                scope_ref=str(user_id),
                tags=["runtime"],
                inputs_schema=[],
                steps=[
                    {
                        "title": "Implement",
                        "instructions": "Implement the issue.",
                        "skill": {
                            "id": "auto",
                            "runtime": {
                                "providerProfileRef": "codex-openai",
                                "profileSelector": {"providerId": "openai"},
                                "modelTier": 2,
                                "tierFallback": "strict",
                            },
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="tiered-workflow",
                scope="personal",
                scope_ref=str(user_id),
                inputs={},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    runtime = expanded["steps"][0]["skill"]["runtime"]
    assert runtime == {
        "providerProfileRef": "codex-openai",
        "profileSelector": {"providerId": "openai"},
        "modelTier": 2,
        "tierFallback": "strict",
    }
    assert expanded["steps"][0]["runtime"] == runtime


async def test_mm1171_preset_runtime_rejects_invalid_tier_values(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            with pytest.raises(PresetValidationError, match="modelTier"):
                await service.create_template(
                    slug="bad-tier",
                    title="Bad Tier",
                    description="Template with invalid tier intent.",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "title": "Run",
                            "instructions": "Run.",
                            "skill": {"runtime": {"modelTier": "2"}},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )

            with pytest.raises(PresetValidationError, match="tierFallback"):
                await service.create_template(
                    slug="bad-fallback",
                    title="Bad Fallback",
                    description="Template with invalid fallback intent.",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "title": "Run",
                            "instructions": "Run.",
                            "skill": {
                                "runtime": {
                                    "modelTier": 1,
                                    "tierFallback": "soft",
                                }
                            },
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )

async def test_checkpoint_branching_policy_expands_only_when_enabled(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="branch-exploration",
                title="Branch Exploration",
                description="Template with bounded branch exploration",
                scope="global",
                scope_ref=None,
                tags=["checkpoint-branch"],
                inputs_schema=[],
                steps=[{"title": "Inspect", "instructions": "Inspect checkpoint"}],
                annotations={
                    "checkpointBranching": {
                        "enabled": True,
                        "triggers": [
                            "failed_step",
                            "failed_step",
                            "operator_requested",
                        ],
                        "maxBranchesPerCheckpoint": 3,
                        "maxTurnsPerBranch": 4,
                        "promotionPolicy": "approval_gated",
                        "defaultWorkspacePolicy": (
                            "apply_previous_execution_diff_to_clean_baseline"
                        ),
                        "branchTemplates": [
                            {
                                "label": "minimal_fix",
                                "instructionsRef": "art_template_minimal_fix",
                            }
                        ],
                        "maxBudgetUsd": "5.50",
                    }
                },
                required_capabilities=["codex"],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="branch-exploration",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )
    assert expanded["checkpointBranching"] == {
        "enabled": True,
        "triggers": ["failed_step", "operator_requested"],
        "maxBranchesPerCheckpoint": 3,
        "maxTurnsPerBranch": 4,
        "promotionPolicy": "approval_gated",
        "defaultWorkspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "publishMode": "none",
        "sideEffectPolicy": "isolated",
        "branchTemplates": [
            {
                "label": "minimal_fix",
                "instructionsRef": "art_template_minimal_fix",
            }
        ],
        "maxBudgetUsd": 5.5,
    }

async def test_disabled_checkpoint_branching_policy_is_explicitly_carried(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="branch-disabled",
                title="Branch Disabled",
                description="Template with disabled branch exploration",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"title": "Run", "instructions": "Run normally"}],
                annotations={"checkpointBranching": {"enabled": False}},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="branch-disabled",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert expanded["checkpointBranching"] == {"enabled": False}

async def test_checkpoint_branching_policy_expands_from_snake_case_annotation(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            session.add(
                Preset(
                    slug="branch-legacy-snake",
                    title="Branch Legacy Snake",
                    description="Template with legacy snake_case annotation",
                    scope_type=PresetScopeType.GLOBAL,
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"title": "Run", "instructions": "Run normally"}],
                    annotations={
                        "checkpoint_branching": {
                            "enabled": True,
                            "triggers": ["failed_step"],
                            "maxBranchesPerCheckpoint": 2,
                            "maxTurnsPerBranch": 3,
                            "promotionPolicy": "approval_gated",
                            "defaultWorkspacePolicy": (
                                "apply_previous_execution_diff_to_clean_baseline"
                            ),
                            "branchTemplates": [
                                {
                                    "label": "minimal_fix",
                                    "instructionsRef": "art_template_minimal_fix",
                                }
                            ],
                        }
                    },
                    required_capabilities=[],
                    created_by=user_id,
                )
            )
            await session.commit()

            service = PresetCatalogService(session)
            expanded = await service.expand_template(
                slug="branch-legacy-snake",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert expanded["checkpointBranching"]["enabled"] is True
    assert expanded["checkpointBranching"]["triggers"] == ["failed_step"]

@pytest.mark.parametrize(
    ("policy_patch", "message"),
    [
        ({"enabled": "true"}, "enabled"),
        ({"triggers": ["unexpected_trigger"]}, "Unsupported checkpointBranching trigger"),
        ({"maxBranchesPerCheckpoint": 0}, "maxBranchesPerCheckpoint"),
        ({"maxTurnsPerBranch": 0}, "maxTurnsPerBranch"),
        ({"promotionPolicy": "auto_promote"}, "promotionPolicy"),
        ({"runtimeContextPolicy": "external_provider_continuation"}, "runtimeContextPolicy"),
        ({"publishMode": "branch"}, "publishMode"),
        ({"sideEffectPolicy": "shared_workspace"}, "sideEffectPolicy"),
        ({"maxBudgetUsd": 0}, "maxBudgetUsd"),
        ({"maxBudgetUsd": "NaN"}, "maxBudgetUsd"),
        ({"maxBudgetUsd": "Infinity"}, "maxBudgetUsd"),
        ({"gitWorkBranch": "main"}, "gitWorkBranch"),
        ({"gitWorkBranch": "production"}, "gitWorkBranch"),
        ({"gitWorkBranch": ".foo/bar"}, "gitWorkBranch"),
        ({"gitWorkBranch": "mm/MM 1087/not-sanitized"}, "gitWorkBranch"),
        ({"branchTemplates": []}, "branchTemplates"),
    ],
)
async def test_checkpoint_branching_policy_fails_closed(
    tmp_path, policy_patch, message
):
    user_id = uuid4()
    policy = {
        "enabled": True,
        "triggers": ["failed_step"],
        "maxBranchesPerCheckpoint": 2,
        "maxTurnsPerBranch": 3,
        "promotionPolicy": "approval_gated",
        "defaultWorkspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "branchTemplates": [
            {"label": "minimal_fix", "instructionsRef": "art_template_minimal_fix"}
        ],
    }
    policy.update(policy_patch)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            with pytest.raises(PresetValidationError, match=message):
                await service.create_template(
                    slug="branch-policy-invalid",
                    title="Branch Policy Invalid",
                    description="Invalid branch policy",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"title": "Run", "instructions": "Run normally"}],
                    annotations={"checkpointBranching": policy},
                    required_capabilities=[],
                    created_by=user_id,
                )

async def test_create_template_strips_tool_version_identity(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug="tool-version",
                title="Tool Version",
                description="Strips semantic tool versions.",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "title": "Fetch issue",
                        "instructions": "Fetch issue.",
                        "tool": {
                            "id": "jira.get_issue",
                            "version": "1.0.0",
                            "inputs": {"issueKey": "MM-916"},
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    assert created["steps"][0]["tool"] == {
        "id": "jira.get_issue",
        "inputs": {"issueKey": "MM-916"},
    }

@pytest.mark.parametrize("slug", ["jira-orchestrate", "moonspec-orchestrate"])
async def test_expand_template_normalizes_legacy_orchestrate_mode_to_runtime(
    tmp_path, slug: str
):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug=slug,
                title=slug,
                description="Legacy orchestrate template",
                scope="global",
                scope_ref=None,
                tags=["moonspec"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    },
                    {
                        "name": "orchestration_mode",
                        "label": "Orchestration Mode",
                        "type": "enum",
                        "required": True,
                        "default": "runtime",
                        "options": ["runtime", "docs"],
                    },
                ],
                steps=[
                    {
                        "title": "Specify",
                        "instructions": "Selected mode: {{ inputs.orchestration_mode }}.",
                        "skill": {"id": "moonspec-specify", "args": {}},
                    }
                ],
                annotations={},
                required_capabilities=["git"],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "Implement MM-600",
                    "orchestration_mode": "docs",
                },
                context={},
            )
    assert expanded["appliedTemplate"]["inputs"]["orchestration_mode"] == "runtime"
    assert "Selected mode: runtime." in expanded["steps"][0]["instructions"]

async def test_template_serializes_normalized_capability_input_contract(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug="schema-capability",
                title="Schema Capability",
                description="Schema-driven preset",
                scope="global",
                scope_ref=None,
                tags=["schema"],
                inputs_schema=[
                    {
                        "name": "jira_issue",
                        "label": "Jira issue",
                        "type": "text",
                        "required": True,
                        "schema": {
                            "type": "object",
                            "required": ["key"],
                            "properties": {
                                "key": {"type": "string", "title": "Issue key"},
                                "summary": {"type": "string"},
                            },
                        },
                        "uiSchema": {
                            "widget": "jira.issue-picker",
                            "allowManualKeyEntry": True,
                        },
                    }
                ],
                steps=[{"title": "Use issue", "instructions": "{{ inputs.jira_issue.key }}"}],
                annotations={},
                required_capabilities=["jira"],
                created_by=user_id,
            )

    assert created["inputSchema"]["type"] == "object"
    assert created["inputSchema"]["required"] == ["jira_issue"]
    assert created["inputSchema"]["properties"]["jira_issue"]["required"] == ["key"]
    assert created["uiSchema"]["jira_issue"]["widget"] == "jira.issue-picker"
    assert created["defaults"] == {}

async def test_expand_template_preserves_literal_placeholders_from_user_input(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="literal-placeholder-input",
                title="Literal Placeholder Input",
                description="Allows user-provided handlebars content.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Use request",
                        "instructions": (
                            "Implement this request:\n\n{{ inputs.feature_request }}"
                        ),
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="literal-placeholder-input",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": (
                        "Preserve the literal {{ downstream.value }} token."
                    )
                },
                context={},
            )

    assert "{{ downstream.value }}" in expanded["steps"][0]["instructions"]

async def test_expand_template_rejects_unknown_template_variable(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="unknown-template-variable",
                title="Unknown Template Variable",
                description="Rejects template authoring mistakes.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "title": "Bad template",
                        "instructions": "Use {{ inputs.missing_value }}.",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            with pytest.raises(PresetValidationError, match="unknown variable"):
                await service.expand_template(
                    slug="unknown-template-variable",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                )

async def test_expand_template_reports_malformed_template_as_validation_error(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="malformed-template",
                title="Malformed Template",
                description="Rejects malformed Jinja templates.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "title": "Bad syntax",
                        "instructions": "Use {{ inputs.missing_value.",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            with pytest.raises(PresetValidationError, match="Template rendering failed"):
                await service.expand_template(
                    slug="malformed-template",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                )

async def test_template_rejects_secret_like_schema_defaults(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            with pytest.raises(PresetValidationError, match="secret-like"):
                await service.create_template(
                    slug="unsafe-default",
                    title="Unsafe Default",
                    description="Schema-driven preset",
                    scope="global",
                    scope_ref=None,
                    tags=["schema"],
                    inputs_schema=[
                        {
                            "name": "jira_issue",
                            "label": "Jira issue",
                            "type": "text",
                            "required": False,
                            "default": "token=raw-secret",
                        }
                    ],
                    steps=[{"title": "Use issue", "instructions": "Use issue"}],
                    annotations={},
                    required_capabilities=["jira"],
                    created_by=user_id,
                )

async def test_expand_schema_capability_reports_field_addressable_errors(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="schema-required",
                title="Schema Required",
                description="Schema-driven preset",
                scope="global",
                scope_ref=None,
                tags=["schema"],
                inputs_schema=[
                    {
                        "name": "jira_issue",
                        "label": "Jira issue",
                        "type": "text",
                        "required": True,
                        "schema": {
                            "type": "object",
                            "required": ["key"],
                            "properties": {"key": {"type": "string"}},
                        },
                    }
                ],
                steps=[{"title": "Use issue", "instructions": "{{ inputs.jira_issue.key }}"}],
                annotations={},
                required_capabilities=["jira"],
                created_by=user_id,
            )

            with pytest.raises(PresetValidationError) as excinfo:
                await service.expand_template(
                    slug="schema-required",
                    scope="global",
                    scope_ref=None,
                    inputs={"jira_issue": {}},
                    context={},
                )

    assert excinfo.value.errors == [
        {
            "path": "preset.inputs.jira_issue.key",
            "message": "Jira issue key is required.",
            "code": "required",
            "recoverable": True,
        }
    ]

async def test_expand_annotated_schema_capability_reports_field_addressable_errors(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="annotated-schema-required",
                title="Annotated Schema Required",
                description="Schema-driven preset",
                scope="global",
                scope_ref=None,
                tags=["schema"],
                inputs_schema=[],
                steps=[{"title": "Use issue", "instructions": "{{ inputs.jira_issue.key }}"}],
                annotations={
                    "inputSchema": {
                        "type": "object",
                        "required": ["jira_issue"],
                        "properties": {
                            "jira_issue": {
                                "type": "object",
                                "title": "Jira issue",
                                "required": ["key"],
                                "properties": {"key": {"type": "string"}},
                            }
                        },
                    },
                    "uiSchema": {
                        "jira_issue": {
                            "widget": "jira.issue-picker",
                            "allowManualKeyEntry": True,
                        }
                    },
                    "defaults": {},
                },
                required_capabilities=["jira"],
                created_by=user_id,
            )

            with pytest.raises(PresetValidationError) as excinfo:
                await service.expand_template(
                    slug="annotated-schema-required",
                    scope="global",
                    scope_ref=None,
                    inputs={"jira_issue": {}},
                    context={},
                )

            expanded = await service.expand_template(
                slug="annotated-schema-required",
                scope="global",
                scope_ref=None,
                inputs={"jira_issue": {"key": "MM-593"}},
                context={},
            )

    assert excinfo.value.errors == [
        {
            "path": "preset.inputs.jira_issue.key",
            "message": "Jira issue key is required.",
            "code": "required",
            "recoverable": True,
        }
    ]
    assert expanded["appliedTemplate"]["inputs"]["jira_issue"] == {"key": "MM-593"}

async def test_expand_template_flattens_pinned_include_with_provenance(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="child-checks",
                title="Child Checks",
                description="Reusable checks",
                scope="global",
                scope_ref=None,
                tags=["checks"],
                inputs_schema=[
                    {
                        "name": "target",
                        "label": "Target",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "id": "lint-target",
                        "title": "Lint target",
                        "instructions": "Lint {{ inputs.target }}",
                        "source": {"kind": "manual"},
                        "skill": {
                            "id": "auto",
                            "args": {},
                            "requiredCapabilities": ["docker"],
                        },
                    },
                    {
                        "id": "test-target",
                        "title": "Test target",
                        "instructions": "Test {{ inputs.target }}",
                    },
                ],
                annotations={},
                required_capabilities=["codex"],
                created_by=None,
            )
            await service.create_template(
                slug="parent-flow",
                title="Parent Flow",
                description="Composed flow",
                scope="global",
                scope_ref=None,
                tags=["composed"],
                inputs_schema=[
                    {
                        "name": "feature",
                        "label": "Feature",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "kind": "include",
                        "slug": "child-checks",
                        "alias": "quality",
                        "scope": "global",
                        "inputMapping": {"target": "{{ inputs.feature }}"},
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="parent-flow",
                scope="global",
                scope_ref=None,
                inputs={"feature": "preset composition"},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    assert [step["title"] for step in expanded["steps"]] == [
        "Lint target",
        "Test target",
    ]
    assert expanded["steps"][0]["id"].startswith("tpl:parent-flow:01:")
    assert expanded["steps"][1]["id"].startswith("tpl:parent-flow:02:")
    assert "preset composition" in expanded["steps"][0]["instructions"]
    assert set(expanded["capabilities"]) >= {"codex", "docker"}
    provenance = expanded["steps"][0]["presetProvenance"]
    assert provenance["root"] == {"slug": "parent-flow"}
    assert provenance["source"]["slug"] == "child-checks"
    assert "version" not in provenance["source"]
    assert provenance["source"]["presetDigest"]
    assert provenance["source"]["originalStepId"] == "lint-target"
    assert provenance["alias"] == "quality"
    assert provenance["path"] == [
        "parent-flow",
        "quality:child-checks",
    ]
    assert expanded["composition"]["includes"][0]["alias"] == "quality"
    assert expanded["composition"]["includes"][0]["stepIds"] == [
        step["id"] for step in expanded["steps"]
    ]
    assert expanded["authoredPresets"] == [
        {
            "presetSlug": "parent-flow",
            "presetDigest": expanded["composition"]["digest"],
            "scope": "global",
            "includePath": ["parent-flow"],
        },
        {
            "presetSlug": "child-checks",
            "presetDigest": expanded["composition"]["includes"][0]["digest"],
            "alias": "quality",
            "scope": "global",
            "includePath": [
                "parent-flow",
                "quality:child-checks",
            ],
            "inputMapping": {"target": "preset composition"},
        },
    ]
    assert expanded["appliedTemplate"]["composition"] == expanded["composition"]
    assert expanded["appliedTemplate"]["authoredPresets"] == expanded[
        "authoredPresets"
    ]
    assert expanded["steps"][0]["source"] == {
        "kind": "preset-derived",
        "presetSlug": "child-checks",
        "presetDigest": expanded["composition"]["includes"][0]["digest"],
        "includePath": [
            "parent-flow",
            "quality:child-checks",
        ],
        "originalStepId": "lint-target",
    }
    assert expanded["steps"][1]["presetProvenance"]["source"][
        "originalStepId"
    ] == "test-target"
    assert expanded["steps"][1]["source"] == {
        "kind": "preset-derived",
        "presetSlug": "child-checks",
        "presetDigest": expanded["composition"]["includes"][0]["digest"],
        "includePath": [
            "parent-flow",
            "quality:child-checks",
        ],
        "originalStepId": "test-target",
    }
    assert expanded["steps"][0]["source"]["kind"] == "preset-derived"
    assert expanded["steps"][0]["source"]["presetSlug"] == "child-checks"
    assert expanded["steps"][0]["source"]["originalStepId"] == "lint-target"
    for step in expanded["steps"]:
        assert "originalStepId" not in step
        assert step["source"]["originalStepId"]
        assert step["presetProvenance"]["source"]["originalStepId"]


async def test_expand_template_preserves_explicit_original_step_id_over_step_id(
    tmp_path,
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="explicit-original-id",
                title="Explicit Original Id",
                description="Preserves source id.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "id": "current-step-id",
                        "originalStepId": "source-step-id",
                        "title": "Run check",
                        "instructions": "Run check",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            expanded = await service.expand_template(
                slug="explicit-original-id",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    step = expanded["steps"][0]
    assert step["source"]["originalStepId"] == "source-step-id"
    assert step["presetProvenance"]["source"]["originalStepId"] == "source-step-id"
    assert "originalStepId" not in step


async def test_expand_template_omits_original_step_id_when_source_step_has_no_id(
    tmp_path,
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="generated-step",
                title="Generated Step",
                description="No source id.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"title": "Generated", "instructions": "Generated"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            expanded = await service.expand_template(
                slug="generated-step",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert "originalStepId" not in expanded["steps"][0]["source"]
    assert "originalStepId" not in expanded["steps"][0]["presetProvenance"]["source"]

async def test_expand_template_repeated_recursive_expansion_is_stable(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="stable-child",
                title="Stable Child",
                description="Reusable child",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {"title": "First child", "instructions": "First child"},
                    {"title": "Second child", "instructions": "Second child"},
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="stable-parent",
                title="Stable Parent",
                description="Composed parent",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {"title": "Manual before", "instructions": "Manual before"},
                    {
                        "kind": "include",
                        "slug": "stable-child",
                        "alias": "child",
                        "scope": "global",
                    },
                    {"title": "Manual after", "instructions": "Manual after"},
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            first = await service.expand_template(
                slug="stable-parent",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )
            second = await service.expand_template(
                slug="stable-parent",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert [step["title"] for step in first["steps"]] == [
        "Manual before",
        "First child",
        "Second child",
        "Manual after",
    ]
    assert [step["id"] for step in first["steps"]] == [
        step["id"] for step in second["steps"]
    ]
    assert first["composition"]["stepIds"] == [
        step["id"] for step in first["steps"]
    ]


async def test_expand_template_skips_steps_with_falsy_enabled_values(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="conditional-steps",
                title="Conditional Steps",
                description="Conditional steps.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "switch",
                        "label": "Switch",
                        "type": "text",
                        "required": False,
                        "default": "null",
                    }
                ],
                steps=[
                    {
                        "title": "Null disabled",
                        "instructions": "Should not run.",
                        "enabled": None,
                    },
                    {
                        "title": "String null disabled",
                        "instructions": "Should not run either.",
                        "enabled": "{{ inputs.switch }}",
                    },
                    {
                        "title": "Enabled step",
                        "instructions": "Should run.",
                    },
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            expanded = await service.expand_template(
                slug="conditional-steps",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert [step["title"] for step in expanded["steps"]] == ["Enabled step"]


async def test_expand_template_flattens_multi_level_include_tree_with_provenance(
    tmp_path,
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="deep-child",
                title="Deep Child",
                description="Deep child.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "target",
                        "label": "Target",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "id": "deep-child-step",
                        "title": "Deep child step",
                        "instructions": "Check {{ inputs.target }}",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="middle-flow",
                title="Middle Flow",
                description="Middle include.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "target",
                        "label": "Target",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Middle manual before",
                        "instructions": "Middle manual before",
                    },
                    {
                        "kind": "include",
                        "slug": "deep-child",
                        "alias": "deep",
                        "scope": "global",
                        "inputMapping": {"target": "{{ inputs.target }}"},
                    },
                    {
                        "title": "Middle manual after",
                        "instructions": "Middle manual after",
                    },
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="parent-flow",
                title="Parent Flow",
                description="Parent include.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "feature",
                        "label": "Feature",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Parent manual before",
                        "instructions": "Parent manual before",
                    },
                    {
                        "kind": "include",
                        "slug": "middle-flow",
                        "alias": "middle",
                        "scope": "global",
                        "inputMapping": {"target": "{{ inputs.feature }}"},
                    },
                    {
                        "title": "Parent manual after",
                        "instructions": "Parent manual after",
                    },
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            expanded = await service.expand_template(
                slug="parent-flow",
                scope="global",
                scope_ref=None,
                inputs={"feature": "recursive provenance"},
                context={},
            )

    assert [step["title"] for step in expanded["steps"]] == [
        "Parent manual before",
        "Middle manual before",
        "Deep child step",
        "Middle manual after",
        "Parent manual after",
    ]
    deep_step = expanded["steps"][2]
    assert deep_step["source"] == {
        "kind": "preset-derived",
        "presetSlug": "deep-child",
        "presetDigest": expanded["composition"]["includes"][0]["includes"][0]["digest"],
        "includePath": [
            "parent-flow",
            "middle:middle-flow",
            "deep:deep-child",
        ],
        "originalStepId": "deep-child-step",
    }
    assert expanded["authoredPresets"] == [
        {
            "presetSlug": "parent-flow",
            "presetDigest": expanded["composition"]["digest"],
            "scope": "global",
            "includePath": ["parent-flow"],
        },
        {
            "presetSlug": "middle-flow",
            "presetDigest": expanded["composition"]["includes"][0]["digest"],
            "alias": "middle",
            "scope": "global",
            "includePath": [
                "parent-flow",
                "middle:middle-flow",
            ],
            "inputMapping": {"target": "recursive provenance"},
        },
        {
            "presetSlug": "deep-child",
            "presetDigest": expanded["composition"]["includes"][0]["includes"][0]["digest"],
            "alias": "deep",
            "scope": "global",
            "includePath": [
                "parent-flow",
                "middle:middle-flow",
                "deep:deep-child",
            ],
            "inputMapping": {"target": "recursive provenance"},
        },
    ]

async def test_expand_template_rejects_duplicate_include_aliases(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="duplicate-child",
                title="Duplicate Child",
                description="Reusable child",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "child"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                PresetValidationError,
                match="include alias 'same' is duplicated",
            ):
                await service.create_template(
                    slug="duplicate-parent",
                    title="Duplicate Parent",
                    description="Invalid includes",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "kind": "include",
                            "slug": "duplicate-child",
                            "alias": "same",
                        },
                        {
                            "kind": "include",
                            "slug": "duplicate-child",
                            "alias": "same",
                        },
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_expand_template_rejects_missing_include_target(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="missing-target-parent",
                title="Missing Target Parent",
                description="Invalid include target",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "missing-child",
                        "alias": "missing",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                PresetValidationError,
                match="missing:missing-child",
            ) as excinfo:
                await service.expand_template(
                    slug="missing-target-parent",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                )

    assert excinfo.value.errors[0]["code"] == "preset_include_missing"
    assert excinfo.value.errors[0]["includePath"] == [
        "missing-target-parent",
        "missing:missing-child",
    ]


async def test_expand_template_strips_include_version(
    tmp_path,
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="child-checks",
                title="Child Checks",
                description="Reusable child.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "child"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="version-field-parent",
                title="Version Field Parent",
                description="Strips versioned preset includes.",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "child-checks",
                        "version": "2",
                        "alias": "child",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            expanded = await service.expand_template(
                slug="version-field-parent",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert len(expanded["steps"]) == 1
    include = expanded["appliedTemplate"]["composition"]["includes"][0]
    assert include["slug"] == "child-checks"
    assert include["alias"] == "child"
    assert include["scope"] == "global"
    assert "version" not in include
    assert "presetVersion" not in include

async def test_expand_template_normalizes_legacy_orchestrate_mode_for_include(
    tmp_path,
):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="moonspec-orchestrate",
                title="MoonSpec Orchestrate",
                description="Runtime-only child preset",
                scope="global",
                scope_ref=None,
                tags=["moonspec"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "title": "Specify",
                        "instructions": "Selected mode: {{ inputs.orchestration_mode }}.",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            await service.create_template(
                slug="parent-flow",
                title="Parent Flow",
                description="Composed flow",
                scope="global",
                scope_ref=None,
                tags=["composed"],
                inputs_schema=[
                    {
                        "name": "feature_request",
                        "label": "Feature Request",
                        "type": "markdown",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "kind": "include",
                        "slug": "moonspec-orchestrate",
                        "alias": "orchestrate",
                        "scope": "global",
                        "inputMapping": {
                            "feature_request": "{{ inputs.feature_request }}",
                            "orchestration_mode": "docs",
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            expanded = await service.expand_template(
                slug="parent-flow",
                scope="global",
                scope_ref=None,
                inputs={"feature_request": "Implement MM-600"},
                context={},
                user_id=user_id,
            )

    assert "Selected mode: runtime." in expanded["steps"][0]["instructions"]

async def test_create_template_strips_templated_include_version(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            created = await service.create_template(
                slug="runtime-selected-parent",
                title="Runtime Selected Parent",
                description="Strips dynamic child version",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "child_version",
                        "label": "Child version",
                        "type": "text",
                    }
                ],
                steps=[
                    {
                        "kind": "include",
                        "slug": "child-checks",
                        "version": "{{ inputs.child_version }}",
                        "alias": "checks",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

    assert created["steps"] == [
        {
            "kind": "include",
            "slug": "child-checks",
            "alias": "checks",
            "scope": "global",
        }
    ]

async def test_create_template_rejects_unsupported_include_fields(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            with pytest.raises(
                PresetValidationError,
                match="include uses unsupported keys: instructions, skill",
            ):
                await service.create_template(
                    slug="override-parent",
                    title="Override Parent",
                    description="Invalid child overrides",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "kind": "include",
                            "slug": "child-checks",
                            "alias": "checks",
                            "scope": "global",
                            "instructions": "Override child instructions",
                            "skill": {"id": "moonspec-verify"},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_expand_template_rejects_global_parent_personal_include(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="global-parent",
                title="Global Parent",
                description="Cannot include personal presets",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "personal-child",
                        "alias": "private",
                        "scope": "personal",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                PresetValidationError,
                match="Global presets cannot include personal presets.*private:personal-child",
            ) as excinfo:
                await service.expand_template(
                    slug="global-parent",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

    assert excinfo.value.errors[0]["code"] == "preset_include_scope_violation"

async def test_expand_template_rejects_include_cycles_with_path(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="preset-a",
                title="Preset A",
                description="Starts cycle",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "preset-b",
                        "alias": "b",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="preset-b",
                title="Preset B",
                description="Completes cycle",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "preset-a",
                        "alias": "a",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                PresetValidationError,
                match="Preset include cycle detected.*preset-a.*b:preset-b.*a:preset-a",
            ) as excinfo:
                await service.expand_template(
                    slug="preset-a",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

    assert excinfo.value.errors[0]["code"] == "preset_include_cycle"

async def test_expand_template_rejects_inactive_and_incompatible_includes(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="inactive-child",
                title="Inactive Child",
                description="Inactive child",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "inactive"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
                release_status=PresetReleaseStatus.INACTIVE,
            )
            await service.create_template(
                slug="input-child",
                title="Input Child",
                description="Requires input",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[
                    {
                        "name": "topic",
                        "label": "Topic",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[{"instructions": "Handle {{ inputs.topic }}"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="bad-parent",
                title="Bad Parent",
                description="Invalid children",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "inactive-child",
                        "alias": "inactive",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="input-parent",
                title="Input Parent",
                description="Missing child input",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "input-child",
                        "alias": "requires-topic",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )

            with pytest.raises(
                PresetValidationError,
                match="inactive.*inactive:inactive-child",
            ) as inactive_excinfo:
                await service.expand_template(
                    slug="bad-parent",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

            with pytest.raises(
                PresetValidationError,
                match="requires-topic:input-child.*Missing required template input 'topic'",
            ) as input_excinfo:
                await service.expand_template(
                    slug="input-parent",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                )

    assert inactive_excinfo.value.errors[0]["code"] == "preset_include_inactive"
    assert input_excinfo.value.errors[0]["code"] == (
        "preset_include_input_mapping_invalid"
    )

async def test_expand_template_enforces_flattened_limit_with_include_path(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="two-step-child",
                title="Two Step Child",
                description="Two concrete steps",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {"instructions": "first child step"},
                    {"instructions": "second child step"},
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            await service.create_template(
                slug="limited-parent",
                title="Limited Parent",
                description="Limit should apply after flattening",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": "two-step-child",
                        "alias": "two",
                        "scope": "global",
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            template = (
                await session.execute(
                    select(Preset)
                    .where(Preset.slug == "limited-parent")
                    
                )
            ).scalar_one()
            template.max_step_count = 1
            await session.commit()

            with pytest.raises(
                PresetValidationError,
                match="max_step_count=1.*two:two-step-child",
            ):
                await service.expand_template(
                    slug="limited-parent",
                    scope="global",
                    scope_ref=None,
                    inputs={},
                    context={},
                    options=ExpandOptions(should_enforce_step_limit=True),
                )

async def test_template_recents_declares_unique_user_template_constraint() -> None:
    constraint_names = {
        constraint.name
        for constraint in PresetRecent.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_preset_recent_user_template" in constraint_names

async def test_save_from_workflow_rejects_secret_patterns(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetSaveService(session)
            with pytest.raises(Exception) as exc:
                await service.save_from_workflow(
                    scope="personal",
                    scope_ref=str(user_id),
                    title="Leaky",
                    description="bad",
                    steps=[
                        {
                            "instructions": "Use token=secret123 for API call",
                            "skill": {"id": "auto", "args": {}},
                        }
                    ],
                    suggested_inputs=[],
                    tags=[],
                    created_by=user_id,
                )

    assert "Potential secrets detected" in str(exc.value)

async def test_list_templates_with_favorites_and_recents(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="first-template",
                title="First Template",
                description="One",
                scope="personal",
                scope_ref=user_str,
                tags=["alpha"],
                inputs_schema=[],
                steps=[{"instructions": "step one"}],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            await service.create_template(
                slug="second-template",
                title="Second Template",
                description="Two",
                scope="personal",
                scope_ref=user_str,
                tags=["beta"],
                inputs_schema=[],
                steps=[{"instructions": "step two"}],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

            await service.set_favorite(
                user_id=user_id,
                slug="second-template",
                scope="personal",
                scope_ref=user_str,
            )
            await service.expand_template(
                slug="second-template",
                scope="personal",
                scope_ref=user_str,
                inputs={},
                context={},
                options=ExpandOptions(),
                user_id=user_id,
            )

            listed = await service.list_templates(
                scope="personal",
                scope_ref=user_str,
                favorites_only=True,
                user_id=user_id,
            )

    assert len(listed) == 1
    assert listed[0]["slug"] == "second-template"
    assert listed[0]["isFavorite"] is True
    assert listed[0]["recentAppliedAt"] is not None
    assert listed[0]["inputSchema"] == {"type": "object", "properties": {}}
    assert "steps" not in listed[0]
    assert "annotations" not in listed[0]

async def test_save_from_workflow_marks_favorite_and_recent(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            saver = PresetSaveService(session)
            await saver.save_from_workflow(
                scope="personal",
                scope_ref=user_str,
                title="Saved Preset",
                description="Saved from task",
                steps=[{"instructions": "run the checks"}],
                suggested_inputs=[],
                tags=["saved"],
                created_by=user_id,
            )

            catalog = PresetCatalogService(session)
            listed = await catalog.list_templates(
                scope="personal",
                scope_ref=user_str,
                favorites_only=True,
                user_id=user_id,
            )

    assert len(listed) == 1
    assert listed[0]["slug"] == "saved-preset"
    assert listed[0]["isFavorite"] is True
    assert listed[0]["recentAppliedAt"] is not None

async def test_recents_trimmed_to_latest_five_rows(tmp_path):
    user_id = uuid4()
    user_str = str(user_id)
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            catalog = PresetCatalogService(session)
            for index in range(1, 7):
                slug = f"preset-{index}"
                await catalog.create_template(
                    slug=slug,
                    title=f"Preset {index}",
                    description="seed",
                    scope="personal",
                    scope_ref=user_str,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"instructions": f"step {index}"}],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )
                await catalog.expand_template(
                    slug=slug,
                    scope="personal",
                    scope_ref=user_str,
                    inputs={},
                    context={},
                    options=ExpandOptions(),
                    user_id=user_id,
                )

            count = await session.scalar(
                select(func.count())
                .select_from(PresetRecent)
                .where(PresetRecent.user_id == user_id)
            )

    assert count == 5

async def test_release_status_sets_reviewer_fields(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            catalog = PresetCatalogService(session)
            await catalog.create_template(
                slug="review-target",
                title="Review Target",
                description="To review",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "review me"}],
                annotations={},
                required_capabilities=[],
                created_by=None,
            )
            reviewed = await catalog.set_release_status(
                slug="review-target",
                scope="global",
                scope_ref=None,
                release_status=PresetReleaseStatus.ACTIVE,
                reviewer_id=user_id,
            )

    assert reviewed["releaseStatus"] == "active"
    assert reviewed["reviewedBy"] == str(user_id)
    assert reviewed["reviewedAt"] is not None

async def test_soft_delete_template_marks_inactive(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="to-be-deleted",
                title="Delete Me",
                description="Template for deletion",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Do nothing"}],
                created_by=user_id,
            )

            await service.soft_delete_template(
                slug="to-be-deleted",
                scope="personal",
                scope_ref=str(user_id),
            )

            with pytest.raises(PresetNotFoundError, match="Template not found."):
                await service._get_template_for_scope(
                    slug="to-be-deleted",
                    scope=PresetScopeType.PERSONAL,
                    scope_ref=str(user_id),
                    include_inactive=False,
                )

            template = await service._get_template_for_scope(
                slug="to-be-deleted",
                scope=PresetScopeType.PERSONAL,
                scope_ref=str(user_id),
                include_inactive=True,
            )
            assert template.is_active is False

async def test_soft_delete_template_not_found(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            with pytest.raises(PresetNotFoundError, match="Template not found."):
                await service.soft_delete_template(
                    slug="does-not-exist",
                    scope="personal",
                    scope_ref=str(user_id),
                )

async def test_deactivate_templates_marks_matching_rows_inactive(tmp_path, monkeypatch):
    increment_calls: list[tuple[str, int]] = []

    class _FakeMetrics:
        def increment(self, metric: str, value: int = 1) -> None:
            increment_calls.append((metric, value))

    monkeypatch.setattr(
        "api_service.services.presets.catalog._METRICS",
        _FakeMetrics(),
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="speckit-orchestrate",
                title="SpecKit Orchestrate",
                description="Legacy preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Legacy orchestration"}],
                created_by=None,
            )
            await service.create_template(
                slug="legacy-checklist",
                title="Legacy Checklist",
                description="Another legacy preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Legacy checklist orchestration"}],
                created_by=None,
            )
            await service.create_template(
                slug="moonspec-orchestrate",
                title="MoonSpec Orchestrate",
                description="Current preset",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=[{"instructions": "Current orchestration"}],
                created_by=None,
            )

            deactivated = await service.deactivate_templates(
                slugs=["speckit-orchestrate", "legacy-checklist"],
                scope="global",
                scope_ref=None,
            )

            assert deactivated == 2
            delete_calls = [call for call in increment_calls if call[0] == "delete"]
            assert delete_calls == [("delete", 2)]

            with pytest.raises(PresetNotFoundError, match="Template not found."):
                await service._get_template_for_scope(
                    slug="speckit-orchestrate",
                    scope=PresetScopeType.GLOBAL,
                    scope_ref=None,
                    include_inactive=False,
                )

            legacy_template = await service._get_template_for_scope(
                slug="speckit-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=True,
            )
            assert legacy_template.is_active is False

            second_legacy_template = await service._get_template_for_scope(
                slug="legacy-checklist",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=True,
            )
            assert second_legacy_template.is_active is False

            current_template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
                include_inactive=False,
            )
            assert current_template.is_active is True

async def test_import_seed_templates_success(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "seed-test",
        "title": "Seed Test",
        "description": "A test seed template",
        "scope": "global",
        "steps": [{"instructions": "seed step"}],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created_count = await service.import_seed_templates(seed_dir=seed_dir)

            assert created_count == 1

            template = await service._get_template_for_scope(
                slug="seed-test",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Seed Test"
            assert template.description == "A test seed template"
            assert template.steps == [
                {
                    "type": "skill",
                        "instructions": "seed step",
                    "skill": {"id": "auto", "args": {}},
                }
            ]

async def test_import_seed_templates_skips_existing(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "seed-test-conflict",
        "title": "Seed Test Conflict",
        "scope": "global",
        "steps": [{"instructions": "seed step"}],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            # First import should create the template
            created_count_first = await service.import_seed_templates(seed_dir=seed_dir)
            assert created_count_first == 1

            # Second import should skip and return 0
            created_count_second = await service.import_seed_templates(
                seed_dir=seed_dir
            )
            assert created_count_second == 0


async def test_sync_seed_templates_preserves_step_original_step_id(tmp_path):
    seed_dir = tmp_path / "seeds"
    _write_seed_template(
        seed_dir,
        {
            "slug": "seeded-preset",
            "title": "Seeded Preset",
            "description": "Seed carries source ids.",
            "scope": "global",
            "steps": [
                {
                    "id": "current-id",
                    "originalStepId": "seed-source-id",
                    "instructions": "Run seeded step",
                }
            ],
        },
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="seeded-preset",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    assert expanded["steps"][0]["source"]["originalStepId"] == "seed-source-id"
    assert "originalStepId" not in expanded["steps"][0]

async def test_seed_catalog_includes_jira_breakdown_preset(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", None)
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="jira-breakdown",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Breakdown and Jira Create"
            assert "jira_board_id" not in {
                item["name"] for item in template.inputs_schema
            }
            assert [
                (step.get("skill") or step.get("tool"))["id"]
                for step in template.steps
            ] == [
                "jira.load_preset_brief",
                "moonspec-breakdown",
                "story.create_jira_issues",
            ]

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                },
                context={},
            )

            assert len(expanded["steps"]) == 2
            assert expanded["steps"][0]["skill"]["id"] == "moonspec-breakdown"
            assert expanded["steps"][0]["title"] == "Break down preferred source input"
            assert "docs/Designs/RuntimeTypes.md" in expanded["steps"][0]["instructions"]
            assert expanded["steps"][1]["skill"]["id"] == "story.create_jira_issues"
            assert "Jira Story issue in project MM" in expanded["steps"][1]["instructions"]
            assert "linear_blocker_chain" in expanded["steps"][1]["instructions"]
            assert "ordered blocker chain" in expanded["steps"][1]["instructions"]
            assert "Source Document path" in expanded["steps"][1]["instructions"]
            assert expanded["steps"][1]["storyOutput"] == {
                "mode": "jira",
                "jira": {
                    "projectKey": "MM",
                    "issueTypeName": "Story",
                    "dependencyMode": "linear_blocker_chain",
                },
            }
            assert "Selected Jira board" not in expanded["steps"][1]["instructions"]
            assert expanded["appliedTemplate"]["inputs"]["jira_dependency_mode"] == (
                "linear_blocker_chain"
            )

            expanded_with_source_issue = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "source_issue_key": "MM-910",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                },
                context={},
            )

            assert len(expanded_with_source_issue["steps"]) == 3
            assert expanded_with_source_issue["steps"][0]["tool"]["id"] == (
                "jira.load_preset_brief"
            )
            assert expanded_with_source_issue["steps"][0]["tool"]["inputs"] == {
                "issueKey": "MM-910",
                "artifactPath": "artifacts/jira-breakdown-brief.json",
            }
            assert expanded_with_source_issue["steps"][1]["skill"]["id"] == (
                "moonspec-breakdown"
            )
            assert "jiraPresetBrief" in expanded_with_source_issue["steps"][1][
                "instructions"
            ]
            assert "resolvedSourceDesignPath" in expanded_with_source_issue["steps"][
                1
            ]["instructions"]
            assert "source-resolution error" in expanded_with_source_issue["steps"][
                1
            ]["instructions"]

            expanded_with_null_optional_sources = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "Inline workflow source.",
                    "source_design_path": None,
                    "source_issue_key": None,
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                },
                context={},
            )

            assert len(expanded_with_null_optional_sources["steps"]) == 2
            assert expanded_with_null_optional_sources["steps"][0]["skill"]["id"] == (
                "moonspec-breakdown"
            )
            assert "Inline workflow source." in expanded_with_null_optional_sources[
                "steps"
            ][0]["instructions"]
            assert "sourceDocumentClass as imperative-input" in (
                expanded_with_null_optional_sources["steps"][0]["instructions"]
            )
            assert "underlying actionable desired system behavior" in (
                expanded_with_null_optional_sources["steps"][0]["instructions"]
            )
            assert "fail fast with the moonspec-breakdown imperative-input error" not in (
                expanded_with_null_optional_sources["steps"][0]["instructions"]
            )
            assert "imperative-override" not in (
                expanded_with_null_optional_sources["steps"][0]["instructions"]
            )

            expanded_with_path_and_issue = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "source_design_path": "docs/Designs/RuntimeTypes.md",
                    "source_issue_key": "MM-910",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                },
                context={},
            )

            assert len(expanded_with_path_and_issue["steps"]) == 2
            assert expanded_with_path_and_issue["steps"][0]["skill"]["id"] == (
                "moonspec-breakdown"
            )
            assert "docs/Designs/RuntimeTypes.md" in expanded_with_path_and_issue[
                "steps"
            ][0]["instructions"]
            assert "sourceDocumentClass as imperative-input" in (
                expanded_with_path_and_issue["steps"][0]["instructions"]
            )
            assert "fail fast with the moonspec-breakdown imperative-input error" not in (
                expanded_with_path_and_issue["steps"][0]["instructions"]
            )

            with pytest.raises(PresetValidationError) as excinfo:
                await service.expand_template(
                    slug="jira-breakdown",
                    scope="global",
                    scope_ref=None,
                    inputs={
                        "jira_project_key": "MM",
                        "jira_issue_type": "Story",
                    },
                    context={},
                )

    assert excinfo.value.errors == [
        {
            "path": "preset.inputs",
            "message": (
                "Provide a Source Document Path, Source Jira Issue Key, "
                "or Workflow Instructions."
            ),
            "code": "required",
            "recoverable": True,
        }
    ]

@pytest.mark.parametrize(
    ("slug", "extra_inputs"),
    [
        ("jira-breakdown", {}),
        (
            "jira-breakdown-orchestrate",
            {"publish_mode": "pr_with_merge_automation"},
        ),
        (
            "jira-breakdown-implement",
            {"publish_mode": "pr_with_merge_automation"},
        ),
    ],
)
async def test_jira_breakdown_presets_allow_inline_instruction_roadmaps(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    extra_inputs: dict[str, str],
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", None)
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": (
                        "Use this recommendation roadmap as breakdown input:\n"
                        "1. Make the workflow list title-first.\n"
                        "2. Move advanced filters into a drawer.\n"
                        "3. Make errors friendly by default."
                    ),
                    "source_design_path": "",
                    "source_issue_key": "",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                    **extra_inputs,
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

    breakdown_instructions = expanded["steps"][0]["instructions"]
    assert "sourceDocumentClass as imperative-input" in breakdown_instructions
    assert "decompose the underlying actionable desired system behavior" in (
        breakdown_instructions
    )
    assert "imperative-override" not in breakdown_instructions
    assert "fail fast with the moonspec-breakdown imperative-input error" not in (
        breakdown_instructions
    )

async def test_seed_catalog_includes_document_health_update_preset(tmp_path):
    """MM-889: a Document Health Update preset runs review then remediate steps."""

    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="document-health-update",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Document Health Update"
            steps = template.steps
            assert [step["title"] for step in steps] == [
                "Document health review",
                "Document health remediate",
            ]
            assert [step["skill"]["id"] for step in steps] == ["auto", "auto"]
            assert [
                step["annotations"]["documentHealthRole"] for step in steps
            ] == ["review", "remediate"]

            expanded = await service.expand_template(
                slug="document-health-update",
                scope="global",
                scope_ref=None,
                inputs={"documentation_scope": "docs/Workflows/"},
                context={},
            )

            assert len(expanded["steps"]) == 2
            review_step, remediate_step = expanded["steps"]
            assert "docs/Workflows/" in review_step["instructions"]
            assert "review-only step" in review_step["instructions"]
            assert (
                "artifacts/document-health-review.json"
                in review_step["instructions"]
            )
            assert "missing metadata" in review_step["instructions"]
            assert "authority ladder" in review_step["instructions"]
            assert "docs/tmp/" in review_step["instructions"]
            assert (
                "artifacts/document-health-review.json"
                in remediate_step["instructions"]
            )
            assert "remediate ONLY" in remediate_step["instructions"]
            assert "missing embedded rationale" in remediate_step["instructions"]
            assert "unverifiable canonical claims" in remediate_step["instructions"]
            assert "docs/tmp/" in remediate_step["instructions"]


async def test_seed_catalog_includes_document_author_preset(tmp_path):
    """MM-931: docs-native authoring chooses docs architecture fields."""

    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="document-author",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Document Author"
            assert template.annotations["sourceIssueKey"] == "MM-931"
            assert template.annotations["sourceReference"] == "MM-927"
            assert template.steps[0]["skill"]["id"] == "document-author"
            assert template.steps[0]["annotations"]["documentAuthoringRole"] == "author"

            expanded = await service.expand_template(
                slug="document-author",
                scope="global",
                scope_ref=None,
                inputs={
                    "documentation_intent": "Document a new runtime contract.",
                    "preferred_area": "docs/Workflows/",
                    "traceability": "MM-931 from MM-927",
                    "constraints": "Canonical only.",
                },
                context={},
            )

            assert len(expanded["steps"]) == 1
            step = expanded["steps"][0]
            assert step["type"] == "skill"
            assert step["skill"]["id"] == "document-author"
            assert "Document a new runtime contract." in step["instructions"]
            assert "docs/Workflows/" in step["instructions"]
            assert "MM-931 from MM-927" in step["instructions"]
            assert "Do not create spec.md" in step["instructions"]
            assert "docs/tmp/" in step["instructions"]

async def test_jira_breakdown_uses_single_allowed_project_as_runtime_default(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM")
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service.get_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
            )
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] == "MM"

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                },
                context={},
            )

            assert "Jira Story issue in project MM" in expanded["steps"][1][
                "instructions"
            ]
            assert "Dependency mode: none." in expanded["steps"][1]["instructions"]
            assert expanded["steps"][1]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "dependencyMode": "none",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "MM"

async def test_jira_breakdown_orchestrate_uses_repository_policy_defaults(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "PLAT,GAME")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        "ExampleOrg/Platform=PLAT,ExampleOrg/Game=GAME",
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service.get_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template["inputs"]
            }
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] == "MM"

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr",
                    "source_issue_key": "GAME-404",
                },
                context={
                    "repository": "ExampleOrg/Game",
                    "targetRuntime": "claude_code",
                },
            )

            jira_step = expanded["steps"][3]
            downstream_step = expanded["steps"][4]

            assert "Jira Story issue in project GAME" in jira_step[
                "instructions"
            ]
            assert jira_step["storyOutput"] == {
                "mode": "jira",
                "fallback": "fail",
                "jira": {
                    "projectKey": "GAME",
                    "issueTypeName": "Story",
                    "sourceIssueKey": "GAME-404",
                    "dependencyMode": "linear_blocker_chain",
                },
            }
            assert downstream_step["jiraOrchestration"]["task"]["repository"] == (
                "ExampleOrg/Game"
            )
            assert downstream_step["jiraOrchestration"]["task"]["runtime"] == {
                "mode": "claude_code"
            }
            assert downstream_step["jiraOrchestration"]["task"]["inputs"] == {
                "run_verify": True
            }
            assert downstream_step["jiraOrchestration"]["task"]["publish"] == {
                "mode": "pr",
                "mergeAutomation": {"enabled": False},
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "GAME"
            assert "repository" not in expanded["appliedTemplate"]["inputs"]
            assert "runtime_mode" not in expanded["appliedTemplate"]["inputs"]

@pytest.mark.parametrize("submitted_project", ["TOOL", "MM"])
async def test_jira_breakdown_replaces_legacy_placeholder_with_single_allowed_project(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    submitted_project: str,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": submitted_project,
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                },
                context={},
            )

            assert expanded["steps"][1]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "dependencyMode": "none",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_project_key"] == "MM"

async def test_jira_breakdown_orchestrate_preserves_explicit_project_input(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "PLAT,GAME")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        "ExampleOrg/Platform=PLAT,ExampleOrg/Game=GAME",
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "PLAT",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr",
                    "source_issue_key": "PLAT-404",
                },
                context={
                    "repository": "ExampleOrg/Game",
                    "targetRuntime": "claude_code",
                },
            )

            assert expanded["steps"][3]["storyOutput"]["jira"]["projectKey"] == "PLAT"
            assert expanded["steps"][4]["jiraOrchestration"]["task"]["repository"] == (
                "ExampleOrg/Game"
            )
            assert expanded["steps"][4]["jiraOrchestration"]["task"]["runtime"] == (
                {"mode": "claude_code"}
            )
            assert expanded["steps"][4]["jiraOrchestration"]["task"]["inputs"] == {
                "run_verify": True
            }

async def test_jira_breakdown_uses_seeded_default_when_multiple_allowed_without_repo_policy(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "MM,OPS")
    monkeypatch.setattr(
        settings.atlassian.jira, "jira_project_defaults_by_repository", None
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service.get_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
            )
            project_input = next(
                item
                for item in template["inputs"]
                if item["name"] == "jira_project_key"
            )
            assert project_input["default"] == "MM"

            expanded = await service.expand_template(
                slug="jira-breakdown",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "none",
                },
                context={},
            )

            assert expanded["steps"][1]["storyOutput"]["jira"]["projectKey"] == "MM"

async def test_seed_catalog_includes_jira_orchestrate_preset(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="jira-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Jira Orchestrate"
            assert template.annotations["jiraWorkflow"] == (
                "implementation-to-code-review"
            )
            template_payload = await service.get_template(
                slug="jira-orchestrate",
                scope="global",
                scope_ref=None,
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template_payload["inputs"]
            }
            assert [
                (step.get("skill") or step.get("tool"))["id"]
                for step in template.steps
            ] == [
                "jira.check_blockers",
                "jira.load_preset_brief",
                "auto",
                "jira.update_issue_status",
                "moonspec-specify",
                "moonspec-assess",
                "moonspec-plan",
                "moonspec-tasks",
                "moonspec-align",
                "moonspec-implement",
                "moonspec-verify",
                *(["moonspec-implement", "moonspec-verify"] * 6),
                "moonspec-doc-reconcile",
                "auto",
                "jira-issue-updater",
            ]
            step_titles = [step["title"] for step in template.steps]
            assert "Return Jira orchestration report" not in step_titles

            expanded = await service.expand_template(
                slug="jira-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "jira_issue_key": "MM-328",
                    "source_design_path": "",
                    "constraints": "Keep the scope narrow.",
                },
                context={},
            )

            assert len(expanded["steps"]) == 26
            assert expanded["steps"][0]["title"] == "Check Jira blockers before implementation"
            assert expanded["steps"][0]["type"] == "tool"
            assert expanded["steps"][0]["tool"]["id"] == "jira.check_blockers"
            assert expanded["steps"][0]["tool"]["inputs"] == {
                "targetIssueKey": "MM-328",
                "blockerPreflight": {
                    "targetIssueKey": "MM-328",
                    "linkType": "Blocks",
                },
            }
            assert expanded["steps"][0]["targetIssueKey"] == "MM-328"
            assert expanded["steps"][0]["blockerPreflight"] == {
                "targetIssueKey": "MM-328",
                "linkType": "Blocks",
            }
            assert "Jira issue MM-328" in expanded["steps"][0]["instructions"]
            assert "deterministic trusted Jira blocker preflight" in expanded["steps"][0]["instructions"]
            assert "other issue as outwardIssue" in expanded["steps"][0]["instructions"]
            assert "other issue as inwardIssue" in expanded["steps"][0]["instructions"]
            assert "MUST NOT block this orchestration" in expanded["steps"][0]["instructions"]
            assert "blocker" in expanded["steps"][0]["instructions"]
            assert "Done" in expanded["steps"][0]["instructions"]
            assert "non-blocker" in expanded["steps"][0]["instructions"]
            assert "status cannot be determined" in expanded["steps"][0][
                "instructions"
            ]
            assert "raw Jira credentials" in expanded["steps"][0]["instructions"]
            assert "web scraping" in expanded["steps"][0]["instructions"]
            assert "stop the orchestration immediately" in expanded["steps"][0][
                "instructions"
            ]
            assert expanded["steps"][1]["type"] == "tool"
            assert expanded["steps"][1]["tool"]["id"] == "jira.load_preset_brief"
            assert expanded["steps"][1]["tool"]["inputs"] == {
                "issueKey": "MM-328",
                "artifactPath": "artifacts/jira-orchestrate-brief.json",
            }
            assert "Jira preset brief" in expanded["steps"][1]["instructions"]
            assert "Keep the scope narrow." in expanded["steps"][2]["instructions"]
            assert "one independently testable story" in expanded["steps"][2]["instructions"]
            assert "upstream breakdown/selector workflow" in expanded["steps"][2]["instructions"]
            assert expanded["steps"][3]["type"] == "tool"
            assert expanded["steps"][3]["tool"]["id"] == "jira.update_issue_status"
            assert expanded["steps"][3]["tool"]["inputs"] == {
                "issueKey": "MM-328",
                "targetStatus": "In Progress",
            }
            assert "MM-328" in expanded["steps"][3]["instructions"]
            assert "In Progress" in expanded["steps"][3]["instructions"]
            assert "one independently testable story" in expanded["steps"][4]["instructions"]
            assert "Do not run moonspec-breakdown from this preset" in expanded["steps"][4]["instructions"]
            assert "Split broad designs when needed" not in [
                step["title"] for step in expanded["steps"]
            ]
            assert expanded["steps"][11]["title"] == "Remediate verification gaps — attempt 1 of 6"
            assert expanded["steps"][11]["skill"]["id"] == "moonspec-implement"
            assert "ADDITIONAL_WORK_NEEDED" in expanded["steps"][11]["instructions"]
            assert "verification report's gaps" in expanded["steps"][11]["instructions"]
            assert expanded["steps"][10]["skill"]["id"] == "moonspec-verify"
            assert "Verification robustness rules" in expanded["steps"][10][
                "instructions"
            ]
            assert "embedding_provider_not_configured" in expanded["steps"][10][
                "instructions"
            ]
            assert "disclosed scope exclusions" in expanded["steps"][10][
                "instructions"
            ]
            assert "Verification robustness rules" in expanded["steps"][12][
                "instructions"
            ]
            assert expanded["steps"][22]["title"] == "Verify remediation attempt 6 of 6"
            assert expanded["steps"][22]["skill"]["id"] == "moonspec-verify"
            assert "controlling verification gate" in expanded["steps"][22][
                "instructions"
            ]
            assert "Verification robustness rules" in expanded["steps"][22][
                "instructions"
            ]
            assert expanded["steps"][23]["title"] == "Reconcile declarative docs"
            assert expanded["steps"][23]["annotations"] == {
                "jiraOrchestrateRole": "doc-reconciliation"
            }
            assert expanded["steps"][23]["skill"]["id"] == "moonspec-doc-reconcile"
            assert "FULLY_IMPLEMENTED" in expanded["steps"][23]["instructions"]
            assert "no_update_required" in expanded["steps"][23]["instructions"]
            assert "Source Document Drift" in expanded["steps"][23]["instructions"]
            assert "artifacts/jira-orchestrate-doc-reconcile.json" in expanded[
                "steps"
            ][23]["instructions"]
            assert "Escalation does not block pull request creation" in expanded[
                "steps"
            ][23]["instructions"]
            assert "Do not commit, push, or create a pull request" in expanded[
                "steps"
            ][23]["instructions"]
            assert expanded["steps"][24]["title"] == "Create pull request"
            assert expanded["steps"][24]["annotations"] == {
                "jiraOrchestrateRole": "pull-request-handoff"
            }
            assert "post-remediation moonspec-verify" in expanded["steps"][24][
                "instructions"
            ]
            assert "pull request title must include MM-328" in expanded["steps"][24][
                "instructions"
            ]
            assert "parent workflow must use the pull request URL" in expanded["steps"][24][
                "instructions"
            ]
            assert "explicit PR-publication step" in expanded["steps"][24][
                "instructions"
            ]
            assert "controlling instruction for this step only" in expanded["steps"][24][
                "instructions"
            ]
            assert "merge automation" in expanded["steps"][24]["instructions"]
            assert "non-draft pull request" in expanded["steps"][24]["instructions"]
            assert "isDraft value is false" in expanded["steps"][24]["instructions"]
            assert "confirmed non-draft" in expanded["steps"][24]["instructions"]
            assert "artifacts/jira-orchestrate-pr.json" in expanded["steps"][24][
                "instructions"
            ]
            assert "doc reconciliation outcome" in expanded["steps"][24][
                "instructions"
            ]
            assert expanded["steps"][25]["skill"]["id"] == "jira-issue-updater"
            assert expanded["steps"][25]["annotations"] == {
                "jiraOrchestrateRole": "code-review-handoff"
            }
            assert "pull_request_url" in expanded["steps"][25]["instructions"]
            assert "stop without changing Jira" in expanded["steps"][25][
                "instructions"
            ]
            assert "status Review" in expanded["steps"][25]["instructions"]
            assert all(
                step["title"] != "Return Jira orchestration report"
                for step in expanded["steps"]
            )

async def test_seed_catalog_jira_implement_flattens_jira_issue_input(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-implement",
                scope="global",
                scope_ref=None,
                inputs={"jira_issue": {"key": "MM-742"}},
                context={},
            )

            assert expanded["steps"][0]["tool"]["id"] == "jira.load_preset_brief"
            assert expanded["steps"][0]["tool"]["inputs"] == {
                "issueKey": "MM-742",
                "artifactPath": "artifacts/jira-implement-brief.json",
            }
            assert expanded["steps"][1]["skill"]["id"] == "auto"
            assert (
                expanded["steps"][1]["title"] == "Assess existing implementation state"
            )
            assert (
                "artifacts/jira-implement-brief.json"
                in expanded["steps"][1]["instructions"]
            )
            assert (
                "artifacts/jira-implement-assessment.json"
                in expanded["steps"][1]["instructions"]
            )
            assert "MM-742" in expanded["steps"][2]["instructions"]
            assert expanded["steps"][2]["tool"]["id"] == "jira.check_blockers"
            assert expanded["steps"][2]["tool"]["inputs"] == {
                "targetIssueKey": "MM-742",
                "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
                "blockerPreflight": {
                    "targetIssueKey": "MM-742",
                    "linkType": "Blocks",
                },
            }
            assert expanded["steps"][3]["type"] == "tool"
            assert expanded["steps"][3]["tool"]["id"] == "jira.update_issue_status"
            assert expanded["steps"][3]["tool"]["inputs"] == {
                "issueKey": "MM-742",
                "targetStatus": "In Progress",
                "assessmentArtifactPath": "artifacts/jira-implement-assessment.json",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_issue_key"] == "MM-742"
            assert [item["presetSlug"] for item in expanded["authoredPresets"]] == [
                "jira-implement",
                "issue-implement-assessment",
                "issue-implement-work-pr",
            ]


@pytest.mark.parametrize(
    "jira_issue_input",
    [
        "MM-742",
        {"issueKey": "MM-742"},
    ],
)
async def test_seed_catalog_jira_implement_accepts_common_jira_issue_shapes(
    tmp_path, jira_issue_input
):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-implement",
                scope="global",
                scope_ref=None,
                inputs={"jira_issue": jira_issue_input},
                context={},
            )

            assert expanded["steps"][0]["tool"]["inputs"] == {
                "issueKey": "MM-742",
                "artifactPath": "artifacts/jira-implement-brief.json",
            }
            assert expanded["appliedTemplate"]["inputs"]["jira_issue_key"] == "MM-742"


async def test_seed_catalog_github_issue_implement_expands_shared_includes(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service.get_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
            )
            expanded = await service.expand_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 123,
                    },
                    "constraints": "",
                },
                context={},
            )
            no_verify = await service.expand_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 123,
                    },
                    "constraints": "",
                    "run_verify": False,
                },
                context={},
            )

    assert template["annotations"]["inputSchema"]["properties"]["run_verify"] == {
        "type": "boolean",
        "title": "Run verification",
        "description": "Run the MoonSpec verification and remediation gate before pull request creation and GitHub issue handoff.",
        "default": True,
    }
    assert template["annotations"]["defaults"]["run_verify"] is True
    assert next(item for item in template["inputs"] if item["name"] == "run_verify")[
        "default"
    ] is True
    assert [step["title"] for step in expanded["steps"]] == [
        "Load GitHub issue brief",
        "Assess existing implementation state",
        "Check GitHub issue blockers before implementation",
        "Mark GitHub issue In Progress",
        "Implement the issue",
        "Verify implementation",
        "Remediation loop controller",
        "Create pull request",
        "Finalize GitHub issue status",
    ]
    assert expanded["steps"][0]["tool"]["id"] == "github.load_issue_preset_brief"
    assert expanded["steps"][0]["tool"]["inputs"]["artifactPath"] == (
        "artifacts/github-issue-implement-brief.json"
    )
    assert (
        "artifacts/github-issue-implement-brief.json"
        in expanded["steps"][1]["instructions"]
    )
    assert expanded["steps"][2]["tool"]["id"] == "github.check_issue_blockers"
    assert expanded["steps"][3]["tool"]["id"] == "github.update_issue_status"
    assert expanded["steps"][5]["skill"]["id"] == "moonspec-verify"
    assert expanded["steps"][5]["skill"]["args"] == {
        "verification_target": "issue_brief",
        "issue_provider": "github",
        "issue_ref": "MoonLadderStudios/MoonMind#123",
        "brief_artifact_path": "artifacts/github-issue-implement-brief.json",
        "assessment_artifact_path": "artifacts/github-issue-implement-assessment.json",
        "verify_artifact_path": "artifacts/github-issue-implement-verify.json",
    }
    assert "verification target issue_brief" in expanded["steps"][5]["instructions"]
    assert "artifacts/github-issue-implement-verify.json" in expanded["steps"][5][
        "instructions"
    ]
    loop_step = expanded["steps"][6]
    assert loop_step["skill"]["id"] == "auto"
    assert loop_step["annotations"]["issueImplementRole"] == (
        "moonspec-remediation-loop"
    )
    loop = loop_step["annotations"]["remediationLoop"]
    assert loop["kind"] == "remediation_loop"
    assert loop["budgets"]["hardMaxAttempts"] == "6"
    assert loop["remediationTool"]["name"] == "auto"
    assert loop["verificationTool"]["name"] == "auto"
    assert loop["verificationTool"]["inputs"]["selectedSkill"] == "moonspec-verify"
    assert not any(
        step.get("annotations", {}).get("moonSpecRemediationAttempt")
        for step in expanded["steps"]
    )
    assert "artifacts/github-issue-implement-verify.json" in expanded["steps"][7][
        "instructions"
    ]
    assert "controlling post-remediation moonspec-verify verdict is FULLY_IMPLEMENTED" in (
        expanded["steps"][7]["instructions"]
    )
    assert "Closes MoonLadderStudios/MoonMind#123" in expanded["steps"][7][
        "instructions"
    ]
    assert (
        "recover that field's full content through the trusted MoonMind tool surface"
        in expanded["steps"][1]["instructions"]
    )
    assert "truncated_fields" in expanded["steps"][1]["instructions"]
    assert "must not become an assessment requirement row" in expanded["steps"][1][
        "instructions"
    ]
    assert "Verification scope rules" in expanded["steps"][5]["instructions"]
    assert "embedding_provider_not_configured" in expanded["steps"][5]["instructions"]
    assert expanded["steps"][8]["tool"]["inputs"]["verificationArtifactPath"] == (
        "artifacts/github-issue-implement-verify.json"
    )
    assert expanded["steps"][8]["tool"]["inputs"]["requireVerification"] is True
    no_verify_titles = [step["title"] for step in no_verify["steps"]]
    assert no_verify_titles == [
        "Load GitHub issue brief",
        "Assess existing implementation state",
        "Check GitHub issue blockers before implementation",
        "Mark GitHub issue In Progress",
        "Implement the issue",
        "Create pull request",
        "Finalize GitHub issue status",
    ]
    assert "Verification was disabled" in no_verify["steps"][-2]["instructions"]
    assert "Verification was disabled" in no_verify["steps"][-1]["instructions"]
    assert no_verify["steps"][-1]["tool"]["inputs"]["requireVerification"] is False
    assert [item["presetSlug"] for item in expanded["authoredPresets"]] == [
        "github-issue-implement",
        "issue-implement-assessment",
        "issue-implement-work-pr",
    ]
    assert expanded["appliedTemplate"]["inputs"]["github_issue_ref"] == (
        "MoonLadderStudios/MoonMind#123"
    )


async def test_seed_catalog_issue_implement_work_pr_renders_remediation_budget(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="issue-implement-work-pr",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_provider": "github",
                    "issue_ref": "MoonLadderStudios/MoonMind#123",
                    "brief_artifact_path": "artifacts/brief.json",
                    "assessment_artifact_path": "artifacts/assessment.json",
                    "pr_artifact_path": "artifacts/pr.json",
                    "verify_artifact_path": "artifacts/verify.json",
                    "verification_target": "issue_brief",
                    "remediation_max_attempts": "2",
                    "constraints": "",
                },
                context={},
            )
            expanded_six = await service.expand_template(
                slug="issue-implement-work-pr",
                scope="global",
                scope_ref=None,
                inputs={
                    "issue_provider": "github",
                    "issue_ref": "MoonLadderStudios/MoonMind#123",
                    "brief_artifact_path": "artifacts/brief.json",
                    "assessment_artifact_path": "artifacts/assessment.json",
                    "pr_artifact_path": "artifacts/pr.json",
                    "verify_artifact_path": "artifacts/verify.json",
                    "verification_target": "issue_brief",
                    "remediation_max_attempts": "6",
                    "constraints": "",
                },
                context={},
            )

    loop_nodes = [
        step
        for step in expanded["steps"]
        if step.get("annotations", {}).get("issueImplementRole")
        == "moonspec-remediation-loop"
    ]
    assert len(loop_nodes) == 1
    assert loop_nodes[0]["annotations"]["remediationLoop"]["budgets"][
        "hardMaxAttempts"
    ] == "2"
    six_loop_nodes = [
        step
        for step in expanded_six["steps"]
        if step.get("annotations", {}).get("issueImplementRole")
        == "moonspec-remediation-loop"
    ]
    assert len(six_loop_nodes) == 1
    assert six_loop_nodes[0]["annotations"]["remediationLoop"]["budgets"][
        "hardMaxAttempts"
    ] == "6"
    assert len(expanded["steps"]) == len(expanded_six["steps"])


async def test_remediation_topology_rejects_partial_active_pair() -> None:
    steps = [
        {
            "annotations": {
                "issueImplementRole": role,
                "moonSpecRemediationAttempt": attempt,
                "moonSpecRemediationMaxAttempts": 2,
                "moonSpecFinalRemediationGate": role
                == "moonspec-verification-gate"
                and attempt == 2,
            }
        }
        for attempt in range(1, 3)
        for role in ("moonspec-remediation", "moonspec-verification-gate")
    ]
    steps.pop(2)

    with pytest.raises(
        PresetValidationError,
        match="complete, unambiguous remediation and verification topology",
    ):
        _validate_moonspec_remediation_topology(steps)


async def test_remediation_topology_rejects_string_final_gate_flag() -> None:
    steps = [
        {
            "annotations": {
                "issueImplementRole": "moonspec-remediation",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 1,
            }
        },
        {
            "annotations": {
                "issueImplementRole": "moonspec-verification-gate",
                "moonSpecRemediationAttempt": 1,
                "moonSpecRemediationMaxAttempts": 1,
                "moonSpecFinalRemediationGate": "true",
            }
        },
    ]

    with pytest.raises(PresetValidationError, match="active final MoonSpec verifier"):
        _validate_moonspec_remediation_topology(steps)


async def test_seed_catalog_github_issue_orchestrate_expands_gated_workflow(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="github-issue-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            template_payload = await service.get_template(
                slug="github-issue-orchestrate",
                scope="global",
                scope_ref=None,
            )
            expanded = await service.expand_template(
                slug="github-issue-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 1067,
                        "url": "https://github.com/MoonLadderStudios/MoonMind/issues/1067",
                    },
                    "constraints": "Keep preset scope bounded.",
                },
                context={},
            )
            no_verify = await service.expand_template(
                slug="github-issue-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 1067,
                        "url": "https://github.com/MoonLadderStudios/MoonMind/issues/1067",
                    },
                    "constraints": "Keep preset scope bounded.",
                    "run_verify": False,
                },
                context={},
            )

    assert template_payload["annotations"]["inputSchema"]["properties"]["run_verify"][
        "type"
    ] == "boolean"
    assert template_payload["annotations"]["inputSchema"]["properties"]["run_verify"][
        "default"
    ] is True
    assert template_payload["annotations"]["defaults"]["run_verify"] is True
    assert next(
        item for item in template_payload["inputs"] if item["name"] == "run_verify"
    )["default"] is True
    assert [step["title"] for step in expanded["steps"]] == [
        "Load GitHub issue brief",
        "Assess existing implementation state",
        "Check GitHub issue blockers before orchestration",
        "Classify request and resume point",
        "Mark GitHub issue In Progress",
        "Create or select MoonSpec",
        "Assess source acceptance coverage",
        "Plan selected spec",
        "Generate TDD task breakdown",
        "Align MoonSpec artifacts",
        "Implement the task breakdown",
        "Verify completion",
        "Remediate verification gaps — attempt 1 of 6",
        "Verify remediation attempt 1 of 6",
        "Remediate remaining gaps — attempt 2 of 6",
        "Verify remediation attempt 2 of 6",
        "Remediate remaining gaps — attempt 3 of 6",
        "Verify remediation attempt 3 of 6",
        "Remediate remaining gaps — attempt 4 of 6",
        "Verify remediation attempt 4 of 6",
        "Remediate remaining gaps — attempt 5 of 6",
        "Verify remediation attempt 5 of 6",
        "Remediate remaining gaps — attempt 6 of 6",
        "Verify remediation attempt 6 of 6",
        "Reconcile declarative docs",
        "Create pull request",
        "Finalize GitHub issue status",
    ]
    assert "one independently testable story" in expanded["steps"][3]["instructions"]
    assert "upstream breakdown/selector workflow" in expanded["steps"][3]["instructions"]
    assert "one independently testable story" in expanded["steps"][5]["instructions"]
    assert "Do not run moonspec-breakdown from this preset" in expanded["steps"][5]["instructions"]
    assert expanded["steps"][0]["tool"]["id"] == "github.load_issue_preset_brief"
    assert expanded["steps"][0]["tool"]["inputs"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": "1067",
        "artifactPath": "artifacts/github-issue-orchestrate-brief.json",
    }
    assert expanded["steps"][2]["tool"]["id"] == "github.check_issue_blockers"
    assert expanded["steps"][4]["tool"]["id"] == "github.update_issue_status"
    assert expanded["steps"][4]["tool"]["inputs"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": "1067",
        "targetStatus": "In Progress",
        "mode": "start",
        "assessmentArtifactPath": "artifacts/github-issue-orchestrate-assessment.json",
    }
    assert expanded["steps"][26]["tool"]["id"] == "github.update_issue_status"
    assert expanded["steps"][26]["tool"]["inputs"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "issueNumber": "1067",
        "mode": "finalize_after_pr_or_done",
        "pullRequestArtifactPath": "artifacts/github-issue-orchestrate-pr.json",
        "verificationArtifactPath": "var/artifacts/moonspec-verify/github-issue-orchestrate.json",
        "requireVerification": True,
    }
    assert expanded["steps"][11]["skill"]["id"] == "moonspec-verify"
    assert expanded["steps"][11]["skill"]["args"]["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/github-issue-orchestrate.json"
    )
    assert expanded["steps"][23]["annotations"] == {
        "jiraOrchestrateRole": "moonspec-verification-gate",
        "moonSpecRemediationAttempt": 6,
        "moonSpecRemediationMaxAttempts": 6,
        "moonSpecFinalRemediationGate": True,
    }
    assert expanded["steps"][23]["skill"]["args"]["verify_artifact_path"] == (
        "var/artifacts/moonspec-verify/github-issue-orchestrate.json"
    )
    assert expanded["steps"][24]["annotations"] == {
        "jiraOrchestrateRole": "doc-reconciliation"
    }
    assert expanded["steps"][25]["annotations"] == {
        "jiraOrchestrateRole": "pull-request-handoff"
    }
    assert expanded["steps"][26]["annotations"] == {
        "jiraOrchestrateRole": "code-review-handoff"
    }
    assert "ADDITIONAL_WORK_NEEDED or NO_DETERMINATION" in expanded["steps"][23][
        "instructions"
    ]
    assert "do not continue to pull request creation" in expanded["steps"][23][
        "instructions"
    ]
    assert "only when the controlling post-remediation verdict is FULLY_IMPLEMENTED" in expanded[
        "steps"
    ][24]["instructions"]
    assert "If the latest verdict is not FULLY_IMPLEMENTED" in expanded["steps"][24][
        "instructions"
    ]
    assert "stop without committing, pushing, creating a pull request" in expanded["steps"][
        25
    ]["instructions"]
    assert "terminal verifier outcomes" in expanded["steps"][26]["instructions"]
    assert "must stop before this status update" in expanded["steps"][26][
        "instructions"
    ]
    assert "FULLY_IMPLEMENTED and no MoonSpec implementation work ran" in expanded[
        "steps"
    ][11]["instructions"]
    assert "skip doc reconciliation" in expanded["steps"][24]["instructions"]
    assert "skip pull request creation entirely" in expanded["steps"][25][
        "instructions"
    ]
    assert "apply the configured Done strategy" in expanded["steps"][26][
        "instructions"
    ]
    assert [item["presetSlug"] for item in expanded["authoredPresets"]] == [
        "github-issue-orchestrate",
        "issue-implement-assessment",
    ]
    assert template.annotations["uiSchema"]["github_issue"] == {
        "widget": "github.issue-picker",
        "dataSource": "github.issues",
        "searchPlaceholder": "Search GitHub issues",
        "allowManualIssueEntry": True,
    }
    assert expanded["appliedTemplate"]["inputs"]["github_issue_ref"] == (
        "MoonLadderStudios/MoonMind#1067"
    )
    no_verify_titles = [step["title"] for step in no_verify["steps"]]
    assert no_verify_titles == [
        "Load GitHub issue brief",
        "Assess existing implementation state",
        "Check GitHub issue blockers before orchestration",
        "Classify request and resume point",
        "Mark GitHub issue In Progress",
        "Create or select MoonSpec",
        "Assess source acceptance coverage",
        "Plan selected spec",
        "Generate TDD task breakdown",
        "Align MoonSpec artifacts",
        "Implement the task breakdown",
        "Create pull request",
        "Finalize GitHub issue status",
    ]
    assert all("moonspec-verify" != step["skill"]["id"] for step in no_verify["steps"] if "skill" in step)
    assert "Verification was disabled" in no_verify["steps"][-2]["instructions"]
    assert "Verification was disabled" in no_verify["steps"][-1]["instructions"]
    assert no_verify["steps"][-1]["tool"]["inputs"]["requireVerification"] is False


async def test_seed_catalog_includes_jira_breakdown_orchestrate_preset(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="jira-breakdown-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Breakdown and Jira Orchestrate"
            assert "jira_board_id" not in {
                item["name"] for item in template.inputs_schema
            }
            assert template.annotations["sourceSkill"] == (
                "jira-breakdown"
            )
            assert template.annotations["output"] == (
                "dependent-jira-orchestrate-workflows"
            )
            assert [
                (step.get("skill") or step.get("tool"))["id"]
                for step in template.steps
            ] == [
                "jira.load_preset_brief",
                "moonspec-breakdown",
                "story-reconcile-implementation",
                "story.create_jira_issues",
                "story.create_jira_orchestrate_tasks",
            ]

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr_with_merge_automation",
                    "source_issue_key": "MM-404",
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

            assert len(expanded["steps"]) == 5
            assert expanded["steps"][0]["tool"]["id"] == "jira.load_preset_brief"
            assert expanded["steps"][0]["tool"]["inputs"] == {
                "issueKey": "MM-404",
                "artifactPath": "artifacts/jira-breakdown-orchestrate-brief.json",
            }
            assert expanded["steps"][1]["skill"]["id"] == "moonspec-breakdown"
            assert "input preference chain" in expanded["steps"][1]["instructions"]
            assert expanded["steps"][2]["skill"]["id"] == (
                "story-reconcile-implementation"
            )
            assert "fully implemented stories" in expanded["steps"][2]["instructions"]
            assert expanded["steps"][3]["skill"]["id"] == "story.create_jira_issues"
            assert expanded["steps"][3]["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Story",
                "sourceIssueKey": "MM-404",
                "dependencyMode": "linear_blocker_chain",
            }
            downstream = expanded["steps"][4]
            assert downstream["skill"]["id"] == "story.create_jira_orchestrate_tasks"
            assert (
                "Create one Jira Orchestrate workflow execution"
                in downstream["instructions"]
            )
            assert "dependsOn" in downstream["instructions"]
            assert "MM-404" in downstream["instructions"]
            assert "Selected Jira board" not in expanded["steps"][3]["instructions"]
            assert downstream["jiraOrchestration"]["task"] == {
                "repository": "MoonLadderStudios/MoonMind",
                "runtime": {"mode": "codex_cli"},
                "inputs": {"run_verify": True},
                "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
            }
            assert downstream["jiraOrchestration"]["traceability"]["sourceIssueKey"] == (
                "MM-404"
            )

async def test_jira_breakdown_orchestrate_can_create_source_subtasks(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings.atlassian.jira, "jira_allowed_projects", "")
    monkeypatch.setattr(
        settings.atlassian.jira,
        "jira_project_defaults_by_repository",
        None,
    )
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-breakdown-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Sub-task",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr",
                    "source_issue_key": "MM-404",
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

            jira_step = expanded["steps"][3]
            assert "Create each generated Jira issue as a sub-task of MM-404" in (
                jira_step["instructions"]
            )
            assert jira_step["storyOutput"]["jira"] == {
                "projectKey": "MM",
                "issueTypeName": "Sub-task",
                "sourceIssueKey": "MM-404",
                "dependencyMode": "linear_blocker_chain",
            }

async def test_seed_catalog_includes_jira_breakdown_implement_preset(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            expanded = await service.expand_template(
                slug="jira-breakdown-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "docs/Designs/RuntimeTypes.md",
                    "jira_project_key": "MM",
                    "jira_issue_type": "Story",
                    "jira_dependency_mode": "linear_blocker_chain",
                    "publish_mode": "pr_with_merge_automation",
                    "source_issue_key": "MM-404",
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

            template = await service.get_template(
                slug="jira-breakdown-implement",
                scope="global",
                scope_ref=None,
            )

    assert template["title"] == "Breakdown and Jira Implement"
    assert "jira_board_id" not in {item["name"] for item in template["inputs"]}
    assert len(expanded["steps"]) == 5
    assert expanded["steps"][0]["tool"]["id"] == "jira.load_preset_brief"
    assert expanded["steps"][0]["tool"]["inputs"] == {
        "issueKey": "MM-404",
        "artifactPath": "artifacts/jira-breakdown-implement-brief.json",
    }
    assert expanded["steps"][1]["skill"]["id"] == "moonspec-breakdown"
    assert expanded["steps"][2]["skill"]["id"] == "story-reconcile-implementation"
    assert expanded["steps"][3]["skill"]["id"] == "story.create_jira_issues"
    assert "Selected Jira board ID" not in expanded["steps"][3]["instructions"]
    assert expanded["steps"][3]["storyOutput"]["jira"] == {
        "projectKey": "MM",
        "issueTypeName": "Story",
        "sourceIssueKey": "MM-404",
        "dependencyMode": "linear_blocker_chain",
    }
    downstream = expanded["steps"][4]
    assert downstream["skill"]["id"] == "story.create_jira_implement_tasks"
    assert downstream["jiraOrchestration"]["task"] == {
        "repository": "MoonLadderStudios/MoonMind",
        "runtime": {"mode": "codex_cli"},
        "inputs": {"run_verify": True},
        "publish": {"mode": "pr", "mergeAutomation": {"enabled": True}},
    }
    assert downstream["jiraOrchestration"]["traceability"] == {
        "sourceIssueKey": "MM-404"
    }

async def test_seed_catalog_includes_document_update_orchestrate_preset(tmp_path):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="document-update-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "Document Update Orchestrate"
            assert template.annotations["sourceSkill"] == (
                "document-update-orchestrate"
            )
            assert template.annotations["output"] == (
                "document-update-tasks"
            )
            assert [
                step.get("tool", step.get("skill", {})).get("id")
                for step in template.steps
            ] == [
                "document.discover",
                "story.create_document_update_tasks",
            ]

            expanded = await service.expand_template(
                slug="document-update-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "document_directory": "docs",
                    "publish_mode": "pr_with_merge_automation",
                },
                context={
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex_cli",
                },
            )

            assert len(expanded["steps"]) == 2
            assert expanded["steps"][0]["tool"]["id"] == "document.discover"
            assert expanded["steps"][0]["tool"]["inputs"]["repository"] == (
                "MoonLadderStudios/MoonMind"
            )
            assert expanded["steps"][1]["tool"]["id"] == "story.create_document_update_tasks"
            assert expanded["steps"][1]["documentUpdateOrchestration"]["task"]["publish"] == {
                "mode": "pr",
                "mergeAutomation": {"enabled": True},
            }
            assert expanded["steps"][1]["documentUpdateOrchestration"]["traceability"][
                "sourceDirectory"
            ] == "docs"

async def test_seed_catalog_includes_moonspec_orchestrate_without_report_step(
    tmp_path,
):
    seed_dir = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "data"
        / "presets"
    )

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "MoonSpec Orchestrate"
            assert [step["skill"]["id"] for step in template.steps] == [
                "moonspec-specify",
                "moonspec-assess",
                "moonspec-plan",
                "moonspec-tasks",
                "moonspec-align",
                "moonspec-implement",
                "moonspec-verify",
                "moonspec-doc-reconcile",
            ]
            step_titles = [step["title"] for step in template.steps]
            assert (
                "Return orchestration report and defer publish actions"
                not in step_titles
            )
            assert step_titles[-2] == "Verify completion"
            assert step_titles[-1] == "Reconcile declarative docs"
            template_payload = await service.get_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
            )
            assert "orchestration_mode" not in {
                item["name"] for item in template_payload["inputs"]
            }
            assert next(
                item
                for item in template_payload["inputs"]
                if item["name"] == "run_verify"
            )["default"] is True

            expanded = await service.expand_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "MM-366: Simplify Orchestrate Summary",
                    "orchestration_mode": "docs",
                    "source_design_path": "",
                    "constraints": "Keep the scope narrow.",
                },
                context={},
            )
            no_verify = await service.expand_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "MM-366: Simplify Orchestrate Summary",
                    "source_design_path": "",
                    "constraints": "Keep the scope narrow.",
                    "run_verify": False,
                },
                context={},
            )

            assert len(expanded["steps"]) == 8
            assert (
                expanded["appliedTemplate"]["inputs"]["orchestration_mode"]
                == "runtime"
            )
            assert "Selected mode" not in expanded["steps"][0]["instructions"]
            assert "runtime implementation workflow" in expanded["steps"][0][
                "instructions"
            ]
            assert expanded["steps"][-2]["title"] == "Verify completion"
            assert "moonspec-verify" == expanded["steps"][-2]["skill"]["id"]
            assert expanded["steps"][-1]["title"] == "Reconcile declarative docs"
            assert "moonspec-doc-reconcile" == expanded["steps"][-1]["skill"]["id"]
            assert "at least one canonical source candidate exists" in expanded[
                "steps"
            ][-1]["instructions"]
            assert "orchestration step provides a source design path under docs/" in expanded[
                "steps"
            ][-1]["instructions"]
            assert "no_update_required" in expanded["steps"][-1]["instructions"]
            assert all(
                step["title"] != "Return orchestration report and defer publish actions"
                for step in expanded["steps"]
            )
            assert [step["title"] for step in no_verify["steps"]] == [
                "Create or select MoonSpec",
                "Assess source acceptance coverage",
                "Plan selected spec",
                "Generate TDD task breakdown",
                "Align MoonSpec artifacts",
                "Implement the task breakdown",
            ]

            expanded_with_source_design = await service.expand_template(
                slug="moonspec-orchestrate",
                scope="global",
                scope_ref=None,
                inputs={
                    "feature_request": "MM-366: Simplify Orchestrate Summary",
                    "source_design_path": "docs/Designs/RuntimeTypes.md",
                    "constraints": "Keep the scope narrow.",
                },
                context={},
            )
            assert "source design path: docs/Designs/RuntimeTypes.md" in expanded_with_source_design[
                "steps"
            ][-1]["instructions"]

async def test_sync_seed_templates_creates_missing_seed(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "moonspec-orchestrate",
        "title": "MoonSpec Orchestrate",
        "description": "Seeded preset",
        "scope": "global",
        "steps": [
            {
                "title": "Specify",
                "instructions": "Use {{ inputs.feature_request }}",
                "skill": {"id": "moonspec-specify", "args": {}},
            }
        ],
        "inputs": [
            {
                "name": "feature_request",
                "label": "Feature Request",
                "type": "markdown",
                "required": True,
            }
        ],
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            result = await service.sync_seed_templates(seed_dir=seed_dir)

            assert result.created == 1
            assert result.updated == 0

            template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.title == "MoonSpec Orchestrate"
            assert template.steps[0]["skill"]["id"] == "moonspec-specify"

async def test_sync_seed_templates_preserves_default_expansion_limit_for_includes(
    tmp_path,
):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "composed-seed",
        "title": "Composed Seed",
        "description": "Seeded preset with child include",
        "scope": "global",
        "steps": [
            {
                "kind": "include",
                "slug": "shared-child",
                "alias": "shared",
                "scope": "global",
            }
        ],
        "inputs": [],
        "annotations": {"sourceSkill": "composed-seed"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )

    assert template.max_step_count == 25

async def test_sync_seed_templates_updates_existing_include_limit_default(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "composed-seed",
        "title": "Composed Seed",
        "description": "Seeded preset with child include",
        "scope": "global",
        "steps": [
            {
                "kind": "include",
                "slug": "shared-child",
                "alias": "shared",
                "scope": "global",
            }
        ],
        "inputs": [],
        "annotations": {"sourceSkill": "composed-seed"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="composed-seed",
                title="Composed Seed",
                description="Seeded preset with child include",
                scope="global",
                scope_ref=None,
                tags=[],
                inputs_schema=[],
                steps=seed_data["steps"],
                annotations={"sourceSkill": "composed-seed"},
                required_capabilities=[],
                created_by=None,
            )
            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            template.max_step_count = 1
            await session.commit()

            result = await service.sync_seed_templates(seed_dir=seed_dir)

            template = await service._get_template_for_scope(
                slug="composed-seed",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )

    assert result.updated == 1
    assert template.max_step_count == 25

async def test_sync_seed_templates_updates_existing_seed(tmp_path):
    seed_dir = tmp_path / "seeds"
    seed_data = {
        "slug": "moonspec-orchestrate",
        "title": "MoonSpec Orchestrate",
        "description": "Updated seeded preset",
        "scope": "global",
        "steps": [
            {
                "title": "Specify",
                "instructions": "Translate {{ inputs.feature_request }} into spec artifacts.",
                "skill": {"id": "moonspec-specify", "args": {}},
            },
            {
                "title": "Plan",
                "instructions": "Plan the implementation.",
                "skill": {"id": "moonspec-plan", "args": {}},
            },
        ],
        "inputs": [
            {
                "name": "feature_request",
                "label": "Feature Request",
                "type": "markdown",
                "required": True,
            }
        ],
        "annotations": {"sourceSkill": "moonspec-orchestrate"},
    }
    _write_seed_template(seed_dir, seed_data)

    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="moonspec-orchestrate",
                title="Legacy Preset",
                description="Old preset",
                scope="global",
                scope_ref=None,
                tags=["legacy"],
                inputs_schema=[],
                steps=[{"instructions": "legacy step"}],
                annotations={"sourceSkill": "legacy-orchestrate"},
                required_capabilities=[],
                created_by=None,
                release_status=PresetReleaseStatus.ACTIVE,
            )

            result = await service.sync_seed_templates(seed_dir=seed_dir)

            assert result.created == 0
            assert result.updated == 1

            template = await service._get_template_for_scope(
                slug="moonspec-orchestrate",
                scope=PresetScopeType.GLOBAL,
                scope_ref=None,
            )
            assert template.description == "Updated seeded preset"
            assert len(template.steps) == 2
            assert template.steps[0]["skill"]["id"] == "moonspec-specify"
            assert template.annotations["sourceSkill"] == "moonspec-orchestrate"

async def test_mm557_accepts_and_expands_jira_transition_tool_step(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug="jira-transition-tool",
                title="Jira Transition Tool",
                description="Typed Jira transition",
                scope="personal",
                scope_ref=str(user_id),
                tags=["jira"],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "title": "Move Jira issue",
                        "instructions": "Move MM-557 to In Progress.",
                        "tool": {
                            "id": "jira.transition_issue",
                            "inputs": {
                                "issueKey": "MM-557",
                                "targetStatus": "In Progress",
                            },
                            "requiredAuthorization": "jira",
                            "requiredCapabilities": ["jira"],
                            "sideEffectPolicy": "idempotent-by-transition-target",
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            expanded = await service.expand_template(
                slug="jira-transition-tool",
                scope="personal",
                scope_ref=str(user_id),
                inputs={},
                context={},
                options=ExpandOptions(should_enforce_step_limit=True),
                user_id=user_id,
            )

    step = created["steps"][0]
    assert step["type"] == "tool"
    assert step["tool"]["id"] == "jira.transition_issue"
    assert step["tool"]["inputs"]["issueKey"] == "MM-557"
    assert step["tool"]["requiredCapabilities"] == ["jira"]
    assert expanded["steps"][0]["type"] == "tool"
    assert expanded["steps"][0]["tool"]["id"] == "jira.transition_issue"

async def test_mm557_accepts_explicit_and_legacy_skill_steps(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            created = await service.create_template(
                slug="jira-skill-steps",
                title="Jira Skill Steps",
                description="Agentic Jira steps",
                scope="personal",
                scope_ref=str(user_id),
                tags=["jira"],
                inputs_schema=[],
                steps=[
                    {
                        "type": "skill",
                        "title": "Triage Jira issue",
                        "instructions": "Read MM-557 and decide next action.",
                        "skill": {
                            "id": "jira-triage",
                            "args": {"issueKey": "MM-557"},
                            "requiredCapabilities": ["jira"],
                            "context": {"repository": "MoonLadderStudios/MoonMind"},
                            "permissions": {"jira": "read"},
                            "autonomy": {"mode": "bounded"},
                        },
                    },
                    {
                        "title": "Legacy implementation step",
                        "instructions": "Implement MM-557.",
                        "skill": {"id": "jira-implement", "args": {"issueKey": "MM-557"}},
                    },
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    explicit, legacy = created["steps"]
    assert explicit["type"] == "skill"
    assert explicit["skill"]["id"] == "jira-triage"
    assert explicit["skill"]["context"]["repository"] == "MoonLadderStudios/MoonMind"
    assert explicit["skill"]["permissions"] == {"jira": "read"}
    assert explicit["skill"]["autonomy"] == {"mode": "bounded"}
    assert legacy["type"] == "skill"
    assert legacy["skill"]["id"] == "jira-implement"

async def test_mm557_rejects_unsupported_or_mixed_step_type_payloads(tmp_path):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            with pytest.raises(PresetValidationError, match="Step 1 type must be one of: tool, skill"):
                await service.create_template(
                    slug="bad-type",
                    title="Bad Type",
                    description="Bad type",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[{"type": "command", "instructions": "Run something"}],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

            with pytest.raises(PresetValidationError, match="Step 1 Tool step must not include a skill payload"):
                await service.create_template(
                    slug="tool-with-skill",
                    title="Tool With Skill",
                    description="Bad tool",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "tool",
                            "instructions": "Implement MM-557.",
                            "skill": {"id": "jira-implement", "args": {"issueKey": "MM-557"}},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

            with pytest.raises(PresetValidationError, match="Step 1 Skill step must not include a tool payload"):
                await service.create_template(
                    slug="skill-with-tool",
                    title="Skill With Tool",
                    description="Bad skill",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "skill",
                            "instructions": "Move MM-557.",
                            "tool": {
                                "id": "jira.transition_issue",
                                "inputs": {"issueKey": "MM-557"},
                            },
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

async def test_mm557_rejects_shell_snippets_unless_bounded_typed_tool(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            with pytest.raises(PresetValidationError, match="forbidden keys: command"):
                await service.create_template(
                    slug="shell-snippet",
                    title="Shell Snippet",
                    description="Bad shell",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "skill",
                            "instructions": "Run a shell snippet.",
                            "command": "bash deploy.sh",
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )

            created = await service.create_template(
                slug="bounded-command-tool",
                title="Bounded Command Tool",
                description="Approved typed command",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "instructions": "Run a bounded test command.",
                        "tool": {
                            "name": "command.run_typed",
                            "inputs": {"commandId": "unit-tests"},
                            "requiredCapabilities": ["command-runner"],
                            "sideEffectPolicy": "bounded",
                            "validation": {"schema": "registered"},
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    assert created["steps"][0]["tool"]["id"] == "command.run_typed"
    assert created["steps"][0]["tool"]["inputs"] == {"commandId": "unit-tests"}


async def test_mm557_tool_args_survive_empty_schema_inputs(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            created = await service.create_template(
                slug="tool-args-fallback",
                title="Tool Args Fallback",
                description="Tool args from API request payload",
                scope="personal",
                scope_ref=str(user_id),
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "type": "tool",
                        "instructions": "Fetch Jira issue.",
                        "tool": {
                            "name": "jira.get_issue",
                            "inputs": {},
                            "args": {"issueKey": "MM-557"},
                        },
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )

    assert created["steps"][0]["tool"]["inputs"] == {"issueKey": "MM-557"}


async def test_mm557_command_tool_rejects_empty_policy_metadata(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)

            with pytest.raises(
                PresetValidationError,
                match="command-like Tool steps require bounded inputs and policy metadata",
            ):
                await service.create_template(
                    slug="empty-command-policy",
                    title="Empty Command Policy",
                    description="Command tool with default schema metadata",
                    scope="personal",
                    scope_ref=str(user_id),
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "type": "tool",
                            "instructions": "Run a bounded test command.",
                            "tool": {
                                "name": "command.run_typed",
                                "inputs": {"commandId": "unit-tests"},
                                "validation": {},
                            },
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=user_id,
                )


async def test_mm569_accepts_valid_tool_skill_and_preset_draft_steps(tmp_path):
    user_id = uuid4()
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            await service.create_template(
                slug="mm569-child-preset",
                title="MM-569 Child Preset",
                description="Child preset for explicit Step Type validation.",
                scope="global",
                scope_ref=None,
                tags=["mm-569"],
                inputs_schema=[
                    {
                        "name": "issue_key",
                        "label": "Issue key",
                        "type": "text",
                        "required": True,
                    }
                ],
                steps=[
                    {
                        "type": "skill",
                        "title": "Child skill",
                        "instructions": "Implement {{ inputs.issue_key }}.",
                        "skill": {"id": "moonspec-implement", "args": {}},
                    }
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            created = await service.create_template(
                slug="mm569-explicit-step-types",
                title="MM-569 Explicit Step Types",
                description="Draft Tool, Skill, and Preset examples.",
                scope="global",
                scope_ref=None,
                tags=["mm-569"],
                inputs_schema=[],
                steps=[
                    tool_step(),
                    skill_step(),
                    preset_step(slug="mm569-child-preset"),
                ],
                annotations={},
                required_capabilities=[],
                created_by=user_id,
            )
            expanded = await service.expand_template(
                slug="mm569-explicit-step-types",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
            )

    tool, skill, preset = created["steps"]
    assert tool["id"] == "fetch-issue"
    assert tool["title"] == "Fetch issue"
    assert tool["type"] == "tool"
    assert tool["tool"]["id"] == "jira.get_issue"
    assert skill["id"] == "implement"
    assert skill["title"] == "Implement story"
    assert skill["type"] == "skill"
    assert skill["skill"]["id"] == "moonspec-implement"
    assert preset["id"] == "run-preset"
    assert preset["title"] == "Run preset"
    assert preset["type"] == "preset"
    assert preset["preset"] == {
        "slug": "mm569-child-preset",
        "inputs": {"issue_key": "MM-569"},
    }
    assert [step["type"] for step in expanded["steps"]] == ["tool", "skill", "skill"]
    assert expanded["steps"][2]["source"]["presetSlug"] == "mm569-child-preset"


async def test_mm569_preset_draft_validation_reports_field_addressable_error(
    tmp_path,
):
    async with template_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = PresetCatalogService(session)
            with pytest.raises(PresetValidationError) as excinfo:
                await service.create_template(
                    slug="mm569-bad-preset",
                    title="MM-569 Bad Preset",
                    description="Invalid preset draft.",
                    scope="global",
                    scope_ref=None,
                    tags=[],
                    inputs_schema=[],
                    steps=[
                        {
                            "id": "bad-preset",
                            "title": "Bad preset",
                            "type": "preset",
                            "instructions": "Apply a preset.",
                            "preset": {"inputs": {"issue_key": "MM-569"}},
                        }
                    ],
                    annotations={},
                    required_capabilities=[],
                    created_by=None,
                )

    assert excinfo.value.errors == [
        {
            "path": "steps[0].preset.slug",
            "message": "Preset steps require preset.slug or preset.id.",
            "code": "required",
            "recoverable": True,
        }
    ]
