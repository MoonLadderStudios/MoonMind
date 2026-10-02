"""MoonLadderStudios/MoonMind#4636: persisted intent through preview and launch.

External RPC and artifact access use isolated adapters. JSON persistence, preset
expansion, input artifacts, planning, resolution and command construction are real.
"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from api_service.db.models import (
    ManagedAgentProviderProfile,
    Preset,
    ProviderProfileAuthState,
    RecurringWorkflowDefinition,
)
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetValidationError,
)
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.runtime_intent import model_selection_fields
from moonmind.workflows.executions.execution_contract import (
    build_canonical_workflow_view,
)
from moonmind.workflows.executions.model_resolver import resolve_model_effort
from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore
from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
from moonmind.workflows.temporal.runtime.strategies.codex_cli import CodexCliStrategy
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.api.test_presets_service import template_db

pytestmark = pytest.mark.asyncio


def provider_profile():
    return ManagedAgentProviderProfile(
        profile_id="issue-4636",
        runtime_id="codex_cli",
        provider_id="openai",
        enabled=True,
        auth_state=ProviderProfileAuthState.CONNECTED,
        default_model="obsolete-model",
        default_effort="obsolete-effort",
        model_tiers=[
            {"model": "tier-one", "effort": "low"},
            {"model": "tier-two", "effort": "high"},
        ],
        default_model_tier=2,
    )


async def create_preset(catalog, selection):
    return await catalog.create_template(
        slug="issue-4636",
        title="Issue #4636",
        description="Selection round trip",
        scope="global",
        scope_ref=None,
        tags=[],
        inputs_schema=[],
        annotations={},
        steps=[
            {
                "id": "work",
                "type": "skill",
                "title": "Work",
                "instructions": "Implement #4636",
                "skill": {"id": "auto", "runtime": selection},
            }
        ],
        required_capabilities=[],
        created_by=None,
    )


def launch_from_input(workflow_input, profile):
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched", return_value=True
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="issue-4636",
            run_id="test",
            task_queue="test",
            namespace="moonmind",
        ),
    ):
        plan = _build_runtime_planner()(
            inputs={},
            parameters={"workflow": workflow_input},
            snapshot=SimpleNamespace(
                version="test", digest="sha256:test", artifact_ref="artifact://input"
            ),
        )
        request = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs=plan["nodes"][0]["inputs"],
            node_id="work",
            tool_name="codex_cli",
            workflow_parameters={"workflow": workflow_input},
        )
    authored = deepcopy(request.parameters["runtime"])
    from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile

    profile = ManagedRuntimeProfile(
        runtime_id=profile.runtime_id,
        profile_id=profile.profile_id,
        provider_id=profile.provider_id,
        enabled=profile.enabled,
        auth_state=profile.auth_state,
        default_model=profile.default_model,
        default_effort=profile.default_effort,
        default_model_tier=profile.default_model_tier,
        model_tiers=profile.model_tiers,
        command_template=["codex", "exec"],
    )
    strategy = CodexCliStrategy()
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=request, profile=profile, strategy=strategy
    )
    return authored, request, strategy.build_command(profile, request)


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"modelTier": 2},
        {"model": None, "effort": None},
        {"model": None, "effort": "max"},
        {"modelTier": 2, "effort": "max"},
    ],
)
async def test_4636_preset_database_artifact_plan_preview_and_command_round_trip(
    tmp_path, monkeypatch, selection
):
    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            session.add(provider_profile())
            await create_preset(PresetCatalogService(session), selection)
            await session.commit()
        # Reload from a separate DB session, as after a process restart.
        async with sessions() as session:
            profile = await session.get(ManagedAgentProviderProfile, "issue-4636")
            expanded = await PresetCatalogService(session).expand_template(
                slug="issue-4636",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
                options=ExpandOptions(),
            )
            step = expanded["steps"][0]
            assert model_selection_fields(step.get("runtime", {})) == selection
            view = build_canonical_workflow_view(
                job_type="task",
                payload={
                    "workflow": {
                        "instructions": "Implement #4636",
                        "runtime": {"mode": "codex_cli"},
                        "steps": [step],
                    }
                },
            )
            workflow = view["workflow"]
            from api_service.api.routers.executions import (
                _build_original_workflow_input_snapshot_payload,
                _snapshot_workflow_from_artifact_payload,
            )

            snapshot = _build_original_workflow_input_snapshot_payload(
                source_kind="create",
                payload={"targetRuntime": "codex_cli"},
                task_payload=workflow,
            )
            store = LocalTemporalArtifactStore(tmp_path / "artifacts")
            store.write_bytes(
                "issue-4636/input.json",
                json.dumps(snapshot).encode(),
                content_type="application/json",
            )
            _payload, restored = _snapshot_workflow_from_artifact_payload(
                json.loads(store.read_bytes("issue-4636/input.json"))
            )
            assert (
                model_selection_fields(restored["steps"][0].get("runtime", {}))
                == selection
            )
            authored, request, command = launch_from_input(restored, profile)
            assert model_selection_fields(authored) == selection
            preview = resolve_model_effort(
                runtime_id="codex_cli", profile=profile, authored_runtime=selection
            )
            assert request.parameters["model"] == preview.model
            assert request.parameters["effort"] == preview.effort
            assert command[command.index("-m") + 1] == preview.model
            assert "--effort" not in command
            assert (
                request.parameters["metadata"]["moonmind"]["modelEffortResolution"][
                    "effortApplicationStatus"
                ]
                == "not_supported"
            )


async def test_4636_new_preset_strict_rejected_but_persisted_strict_expands_after_restart(
    tmp_path,
):
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            catalog = PresetCatalogService(session)
            with pytest.raises(PresetValidationError, match="new strict"):
                await create_preset(catalog, {"modelTier": 2, "tierFallback": "strict"})
            await create_preset(catalog, {"modelTier": 2})
            stored = (await session.execute(select(Preset))).scalar_one()
            steps = deepcopy(stored.steps)
            steps[0]["skill"]["runtime"]["tierFallback"] = "strict"
            stored.steps = steps
            await session.commit()
        async with sessions() as session:
            expanded = await PresetCatalogService(session).expand_template(
                slug="issue-4636",
                scope="global",
                scope_ref=None,
                inputs={},
                context={},
                options=ExpandOptions(),
            )
            assert model_selection_fields(expanded["steps"][0]["runtime"]) == {
                "modelTier": 2,
                "tierFallback": "strict",
            }
            profile = provider_profile()
            profile.model_tiers = profile.model_tiers[:1]
            profile.default_model_tier = 1
            with pytest.raises(
                ValueError, match="Requested model tier 2 is unavailable"
            ):
                launch_from_input(
                    {
                        "instructions": "Work",
                        "runtime": {"mode": "codex_cli"},
                        "steps": expanded["steps"],
                    },
                    profile,
                )


def temporal_adapter():
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="issue-4636-schedule")
    adapter.update_schedule = AsyncMock()
    adapter.resolve_workflow_task_queue.return_value = "mm.workflow.user.v2"
    return adapter


async def create_schedule(service, target):
    return await service.create_definition(
        name="Issue #4636",
        description="Round trip",
        enabled=True,
        schedule_type="cron",
        cron="0 6 * * *",
        timezone="UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=None,
        target=target,
        policy={},
    )


def schedule_target(selection, *, snake=False):
    return {
        "workflow_type" if snake else "workflowType": "MoonMind.UserWorkflow",
        "initial_parameters" if snake else "initialParameters": {
            "targetRuntime": "codex_cli",
            "workflow": {
                "instructions": "Work on #4636",
                "publish": {"mode": "none"},
                "runtime": {
                    "mode": "codex_cli",
                    "profileId": "issue-4636",
                    **selection,
                },
            },
        },
    }


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"modelTier": 2},
        {"model": None, "effort": None},
        {"modelTier": 2, "effort": "max"},
    ],
)
async def test_4636_schedule_save_reload_and_default_changes_preserve_authored_intent(
    tmp_path, monkeypatch, selection
):
    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
    adapter = temporal_adapter()
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            session.add(provider_profile())
            await session.flush()
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            definition = await create_schedule(service, schedule_target(selection))
            definition_id = definition.id
            await session.commit()
        async with sessions() as session:
            definition = await session.get(RecurringWorkflowDefinition, definition_id)
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            await service.update_definition(definition, name="Unrelated rename #4636")
            runtime = definition.target["initialParameters"]["workflow"]["runtime"]
            assert model_selection_fields(runtime) == selection
            profile = await session.get(ManagedAgentProviderProfile, "issue-4636")
            profile.default_model_tier = 1
            await session.commit()
            preview = resolve_model_effort(
                runtime_id="codex_cli", profile=profile, authored_runtime=runtime
            )
            assert preview.model == (
                "runtime-model"
                if "model" in selection and "modelTier" not in selection
                else "tier-two" if "modelTier" in selection else "tier-one"
            )
            authored, request, _command = launch_from_input(
                definition.target["initialParameters"]["workflow"], profile
            )
            assert model_selection_fields(authored) == selection
            assert request.parameters["model"] == preview.model


@pytest.mark.parametrize("snake", [False, True])
async def test_4636_schedule_strict_cutoff_compares_server_record_and_field_presence(
    tmp_path, snake
):
    async with template_db(tmp_path) as sessions, sessions() as session:
        session.add(provider_profile())
        await session.flush()
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=temporal_adapter()
        )
        with pytest.raises(RecurringWorkflowValidationError, match="new strict"):
            await create_schedule(
                service,
                schedule_target(
                    {"modelTier": 2, "tierFallback": "strict"}, snake=snake
                ),
            )
        definition = await create_schedule(service, schedule_target({"modelTier": 2}))
        legacy = deepcopy(definition.target)
        legacy["initialParameters"]["workflow"]["runtime"]["tierFallback"] = "strict"
        definition.target = legacy
        await session.commit()
        await service.update_definition(
            definition, name="Rename saved strict", target=deepcopy(legacy)
        )
        invalid = deepcopy(legacy)
        invalid["initialParameters"]["workflow"]["runtime"]["effort"] = None
        with pytest.raises(RecurringWorkflowValidationError, match="new strict"):
            await service.update_definition(definition, target=invalid)
        assert model_selection_fields(
            definition.target["initialParameters"]["workflow"]["runtime"]
        ) == {"modelTier": 2, "tierFallback": "strict"}


async def test_4636_seed_refresh_cannot_introduce_strict_into_existing_preset(tmp_path):
    import yaml

    async with template_db(tmp_path) as sessions, sessions() as session:
        catalog = PresetCatalogService(session)
        await create_preset(catalog, {"modelTier": 2})
        seed = {
            "slug": "issue-4636",
            "title": "Issue #4636",
            "scope": "global",
            "description": "Updated seed",
            "steps": [
                {
                    "title": "Work",
                    "instructions": "Work on #4636",
                    "skill": {
                        "id": "auto",
                        "runtime": {"modelTier": 2, "tierFallback": "strict"},
                    },
                }
            ],
        }
        seed_dir = tmp_path / "seeds"
        seed_dir.mkdir()
        (seed_dir / "selection.yaml").write_text(yaml.safe_dump(seed))
        with pytest.raises(PresetValidationError, match="new strict"):
            await catalog.sync_seed_templates(seed_dir=seed_dir)
        stored = (await session.execute(select(Preset))).scalar_one()
        assert "tierFallback" not in stored.steps[0]["skill"]["runtime"]


async def test_4636_custom_launch_keeps_non_model_parameters_across_preset_storage_and_step_inheritance(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    async with template_db(tmp_path) as sessions, sessions() as session:
        profile = provider_profile()
        await create_preset(
            PresetCatalogService(session),
            {"model": None, "effort": None, "parameters": {"seed": 7}},
        )
        await session.commit()
        expanded = await PresetCatalogService(session).expand_template(
            slug="issue-4636",
            scope="global",
            scope_ref=None,
            inputs={},
            context={},
            options=ExpandOptions(),
        )
        workflow = {
            "instructions": "Work on #4636",
            "runtime": {
                "mode": "codex_cli",
                "modelTier": 2,
                "parameters": {"seed": 42, "temperature": 0},
            },
            "steps": expanded["steps"],
        }
        authored, request, command = launch_from_input(workflow, profile)
        assert model_selection_fields(authored) == {"model": None, "effort": None}
        assert authored["parameters"] == {"seed": 7, "temperature": 0}
        assert request.parameters["seed"] == 7
        assert request.parameters["temperature"] == 0
        assert request.parameters["model"] == "runtime-model"
        assert command[command.index("-m") + 1] == "runtime-model"


async def test_4636_schedule_artifact_cutoff_and_saved_reference_survive_restart(
    tmp_path,
):
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    store = LocalTemporalArtifactStore(tmp_path / "schedule-artifacts")
    adapter = temporal_adapter()
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            session.add(provider_profile())
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            artifact, _upload = await artifacts.create(
                principal="system", content_type="application/json"
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system",
                payload=json.dumps(
                    {
                        "workflow": {
                            "instructions": "Saved strict #4636",
                            "runtime": {"modelTier": 2, "tierFallback": "strict"},
                        }
                    }
                ).encode(),
                content_type="application/json",
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter, artifact_service=artifacts
            )
            new_target = {
                **schedule_target({}),
                "inputArtifactRef": artifact.artifact_id,
            }
            with pytest.raises(RecurringWorkflowValidationError, match="new strict"):
                await create_schedule(service, new_target)
            adapter.create_schedule.assert_not_awaited()
            assert (
                not (await session.execute(select(RecurringWorkflowDefinition)))
                .scalars()
                .all()
            )
            definition = await create_schedule(service, schedule_target({}))
            # Represent accepted saved provenance from before the authoring cutoff.
            definition.target = deepcopy(new_target)
            definition_id = definition.id
            await session.commit()
        async with sessions() as session:
            definition = await session.get(RecurringWorkflowDefinition, definition_id)
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter, artifact_service=artifacts
            )
            target = deepcopy(definition.target)
            target["initialParameters"]["workflow"]["title"] = "Unrelated saved edit"
            await service.update_definition(
                definition, name="Unrelated rename", target=target
            )
            assert definition.target["inputArtifactRef"] == artifact.artifact_id
            _artifact, body = await artifacts.read(
                artifact_id=artifact.artifact_id,
                principal="system",
                allow_restricted_raw=True,
            )
            saved = json.loads(body)
            profile = await session.get(ManagedAgentProviderProfile, "issue-4636")
            profile.model_tiers = profile.model_tiers[:1]
            profile.default_model_tier = 1
            with pytest.raises(
                ValueError, match="Requested model tier 2 is unavailable"
            ):
                resolve_model_effort(
                    runtime_id="codex_cli",
                    profile=profile,
                    authored_runtime=saved["workflow"]["runtime"],
                )


async def test_4636_saved_artifact_step_reset_reaches_snapshot_preview_and_launch(
    tmp_path,
    monkeypatch,
):
    from api_service.api.routers import executions

    original = {
        "instructions": "Preserve #4636",
        "runtime": {"mode": "codex_cli", "modelTier": 2},
        "steps": [
            {
                "id": "work",
                "type": "skill",
                "instructions": "Saved work",
                "skill": {"id": "auto"},
                "runtime": {
                    "modelTier": 3,
                    "tierFallback": "strict",
                    "parameters": {"seed": 42},
                },
            }
        ],
    }
    store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    store.write_bytes(
        "issue-4636/input.json",
        json.dumps({"workflow": original}).encode(),
        content_type="application/json",
    )

    async def read_artifact(**kwargs):
        return SimpleNamespace(), store.read_bytes("issue-4636/input.json")

    monkeypatch.setattr(
        executions,
        "get_temporal_artifact_service",
        lambda _session: SimpleNamespace(read=read_artifact),
    )
    _payload, restored = (
        await executions._snapshot_source_payload_from_parameters_and_artifact(
            session=None,
            user=SimpleNamespace(id="operator"),
            record=SimpleNamespace(input_ref="issue-4636-input"),
            parameters={
                "workflow": {"steps": [{"id": "work", "instructions": "Saved work"}]}
            },
        )
    )
    assert model_selection_fields(restored["steps"][0]["runtime"]) == {}
    assert restored["steps"][0]["runtime"]["parameters"] == {"seed": 42}
    snapshot = executions._build_original_workflow_input_snapshot_payload(
        source_kind="edit",
        payload={"targetRuntime": "codex_cli"},
        task_payload=restored,
    )
    _payload, reloaded = executions._snapshot_workflow_from_artifact_payload(
        json.loads(json.dumps(snapshot))
    )
    authored, request, command = launch_from_input(reloaded, provider_profile())
    assert model_selection_fields(authored) == {"modelTier": 2}
    preview = resolve_model_effort(
        runtime_id="codex_cli", profile=provider_profile(), authored_runtime=authored
    )
    assert command[command.index("-m") + 1] == preview.model == "tier-two"
    assert request.parameters["seed"] == 42
    assert model_selection_fields(
        json.loads(store.read_bytes("issue-4636/input.json"))["workflow"]["steps"][0][
            "runtime"
        ]
    ) == {"modelTier": 3, "tierFallback": "strict"}


@pytest.mark.parametrize(
    "scenario",
    json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "frontend/src/runtime/fixtures/model-selection-preset-save.json"
        ).read_text()
    ),
    ids=lambda scenario: scenario["name"],
)
async def test_4636_save_emitted_client_preset_through_reload_preview_and_launch(
    tmp_path, monkeypatch, scenario
):
    from api_service.api.routers.presets import save_from_workflow
    from api_service.api.schemas import PresetSaveFromWorkflowRequestSchema
    from api_service.auth_providers import _transient_disabled_operator
    from api_service.services.presets.save import PresetSaveService

    monkeypatch.setenv("MOONMIND_CODEX_MODEL", "runtime-model")
    monkeypatch.setenv("MOONMIND_CODEX_EFFORT", "medium")
    operator = _transient_disabled_operator()
    # workflow-start.test.tsx proves the page emits this exact request for the
    # named user interaction. Exercise its actual API schema and save owner.
    payload = PresetSaveFromWorkflowRequestSchema.model_validate(scenario["request"])
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            session.add(provider_profile())
            saved = await save_from_workflow(
                payload=payload, service=PresetSaveService(session), user=operator
            )
            await session.commit()
        async with sessions() as session:
            stored = (await session.execute(select(Preset))).scalar_one()
            stored_runtime = (stored.steps[0].get("skill") or {}).get("runtime", {})
            assert model_selection_fields(stored_runtime) == scenario["selection"]
            assert stored_runtime.get("parameters", {}) == scenario["parameters"]
            profile = await session.get(ManagedAgentProviderProfile, "issue-4636")
            profile.default_model_tier = 1
            await session.commit()
            expanded = await PresetCatalogService(session).expand_template(
                slug=saved.slug,
                scope="personal",
                scope_ref=str(operator.id),
                inputs={},
                context={},
                options=ExpandOptions(),
                user_id=operator.id,
            )
            runtime = expanded["steps"][0].get("runtime", {})
            assert model_selection_fields(runtime) == scenario["selection"]
            view = build_canonical_workflow_view(
                job_type="task",
                payload={
                    "workflow": {
                        "instructions": "Save preset round trip",
                        "runtime": {"mode": "codex_cli"},
                        "steps": expanded["steps"],
                    }
                },
            )
            from api_service.api.routers.executions import (
                _build_original_workflow_input_snapshot_payload,
                _snapshot_workflow_from_artifact_payload,
            )

            snapshot = _build_original_workflow_input_snapshot_payload(
                source_kind="create",
                payload={"targetRuntime": "codex_cli"},
                task_payload=view["workflow"],
            )
            store = LocalTemporalArtifactStore(tmp_path / "saved-input")
            store.write_bytes(
                "input.json",
                json.dumps(snapshot).encode(),
                content_type="application/json",
            )
            _, restored = _snapshot_workflow_from_artifact_payload(
                json.loads(store.read_bytes("input.json"))
            )
            authored, request, command = launch_from_input(restored, profile)
            assert model_selection_fields(authored) == scenario["selection"]
            preview = resolve_model_effort(
                runtime_id="codex_cli", profile=profile, authored_runtime=runtime
            )
            assert request.parameters["model"] == preview.model
            assert request.parameters["effort"] == preview.effort
            assert command[command.index("-m") + 1] == preview.model
            for key, value in scenario["parameters"].items():
                assert authored["parameters"][key] == value
                assert request.parameters[key] == value
            if scenario["name"] == "omitted":
                assert preview.effective_model_tier == 1


async def test_4636_save_client_preset_cannot_introduce_strict(tmp_path):
    from fastapi import HTTPException

    from api_service.api.routers.presets import save_from_workflow
    from api_service.api.schemas import PresetSaveFromWorkflowRequestSchema
    from api_service.auth_providers import _transient_disabled_operator
    from api_service.services.presets.save import PresetSaveService

    payload = PresetSaveFromWorkflowRequestSchema.model_validate(
        {
            "title": "Strict copy",
            "description": "Client copied a legacy selection",
            "steps": [
                {
                    "instructions": "Work",
                    "skill": {
                        "id": "auto",
                        "runtime": {
                            "modelTier": 2,
                            "tierFallback": "strict",
                        },
                    },
                }
            ],
        }
    )
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            with pytest.raises(HTTPException) as error:
                await save_from_workflow(
                    payload=payload,
                    service=PresetSaveService(session),
                    user=_transient_disabled_operator(),
                )
            assert error.value.status_code == 422
            assert "new strict" in error.value.detail["message"]
            assert (await session.execute(select(Preset))).scalars().all() == []


async def test_4636_schedule_replaces_missing_artifact_and_reloads_new_launch_intent(
    tmp_path,
):
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    store = LocalTemporalArtifactStore(tmp_path / "replacement-artifacts")
    adapter = temporal_adapter()
    replacement = {
        "workflow": {
            "instructions": "Replacement workflow",
            "runtime": {"mode": "codex_cli", "modelTier": 1},
            "steps": [
                {
                    "id": "work",
                    "type": "skill",
                    "instructions": "Work",
                    "skill": {"id": "auto"},
                }
            ],
        }
    }
    async with template_db(tmp_path) as sessions:
        async with sessions() as session:
            session.add(provider_profile())
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            artifact, _upload = await artifacts.create(
                principal="system", content_type="application/json"
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="system",
                payload=json.dumps(replacement).encode(),
                content_type="application/json",
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter, artifact_service=artifacts
            )
            definition = await create_schedule(service, schedule_target({}))
            definition.target = {
                **definition.target,
                "inputArtifactRef": "art-missing-old",
            }
            definition_id = definition.id
            await session.commit()
        async with sessions() as session:
            definition = await session.get(RecurringWorkflowDefinition, definition_id)
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter, artifact_service=artifacts
            )
            await service.update_definition(
                definition,
                target={**definition.target, "inputArtifactRef": artifact.artifact_id},
            )
            await session.commit()
        async with sessions() as session:
            definition = await session.get(RecurringWorkflowDefinition, definition_id)
            assert definition.target["inputArtifactRef"] == artifact.artifact_id
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )
            _artifact, body = await artifacts.read(
                artifact_id=definition.target["inputArtifactRef"],
                principal="system",
                allow_restricted_raw=True,
            )
            selected = await session.get(ManagedAgentProviderProfile, "issue-4636")
            authored, request, command = launch_from_input(
                json.loads(body)["workflow"], selected
            )
            assert model_selection_fields(authored) == {"modelTier": 1}
            assert request.parameters["model"] == "tier-one"
            assert command[command.index("-m") + 1] == "tier-one"
