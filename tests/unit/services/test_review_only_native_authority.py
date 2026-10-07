from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from api_service.services import omnigent_execution_plan_service as service
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _policy_snapshot,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ready_opencode_image_pair as _ready_opencode_image_pair,
)
from tests.unit.services.test_omnigent_execution_plan_service import _snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "graph_kind,selection",
    [
        ("preset", "explicit-default"),
        ("preset", "explicit-other"),
        ("preset", "routed-other"),
        ("schedule", "explicit-other"),
        ("submit", "explicit-default"),
        ("submit", "explicit-other"),
        ("rerun", "explicit-other"),
        ("unassigned", "explicit-default"),
        ("migrated", "explicit-default"),
        ("wrong-target", "explicit-other"),
        ("missing-target", "explicit-other"),
        ("child-agent", "explicit-other"),
        ("agent", "explicit-other"),
        ("mixed", "explicit-other"),
    ],
)
async def test_review_only_default_codex_admits_native_collaboration_authority(
    monkeypatch, tmp_path, graph_kind, selection
) -> None:
    """The shipped closed native graph owns PR authority without an agent grant."""

    generic_admitted = False
    outcome = "reject" if graph_kind in {"agent", "mixed"} else "native"
    from unittest.mock import AsyncMock

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api_service.db.models import Base
    from moonmind.omnigent.harness_platform.stores import InMemoryExecutionPlanStore
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    repository = "MoonLadderStudios/Tactics"
    routed_to_other = selection in {"routed", "routed-other", "explicit-other"}
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "GITHUB_TOKEN"),
        github_pat_connection("codex-repository", "CODEX_REPOSITORY_PAT"),
        assignments=(
            [github_repository_assignment("codex-repository", repository)]
            if routed_to_other
            else (
                [github_repository_assignment(DEFAULT_GIT_CONNECTION_REF, repository)]
                if graph_kind not in {"unassigned", "migrated"}
                else []
            )
        ),
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    if graph_kind == "migrated":
        from api_service.db.models import RepositoryConnectionAuditEvent

        async with sessions() as session:
            session.add(
                RepositoryConnectionAuditEvent(
                    request_id="migration:391:legacy-github-credential",
                    actor_ref="system:migration-391",
                    action="connection.create",
                    connection_id=DEFAULT_GIT_CONNECTION_REF,
                    scope_type="system",
                    policy_revision=1,
                    detail_json={"migration": "391_legacy_github_cred_4023"},
                )
            )
            await session.commit()
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", str(generic_admitted)
    )
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    monkeypatch.setattr(
        service, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        service, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@1")

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    target = (
        {
            "provider": "git",
            "connectionRef": (
                DEFAULT_GIT_CONNECTION_REF
                if selection == "explicit-default"
                else "codex-repository"
            ),
            "repository": {"name": repository},
            "branch": {"name": "main"},
        }
        if selection.startswith("explicit")
        else repository
    )
    if graph_kind == "missing-target":
        target = None
    from tests.unit.api.test_pr_review_resolve_preset import _expand

    expanded = await _expand(
        tmp_path,
        {"pull_request": "350", "review_only": True},
        context={"repository": repository},
    )
    assert expanded["publish"]["mode"] == "none"
    assert expanded["publish"]["mergeAutomation"]["finishMode"] == "review_only"
    assert len(expanded["steps"]) == 1
    assert expanded["steps"][0]["type"] == "tool"
    assert expanded["steps"][0]["tool"]["id"] == "github.resolve_pull_request_target"
    assert expanded["steps"][0]["tool"]["inputs"] == {
        "repository": repository,
        "pullRequest": "350",
    }
    workflow = copy.deepcopy(expanded)
    if graph_kind in {"submit", "schedule"}:
        from api_service.api.routers.executions import _normalize_task_steps
        from api_service.services.presets.catalog import PresetCatalogService
        from moonmind.workflows.executions.preset_expansion import (
            expand_preset_for_child_run,
        )
        from tests.unit.api.test_pr_review_resolve_preset import _catalog_db, _seed_dir

        async with _catalog_db(tmp_path) as maker:
            async with maker() as session:
                catalog = PresetCatalogService(session)
                await catalog.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
                await session.commit()
                submitted = await expand_preset_for_child_run(
                    session=session,
                    initial_parameters={
                        "repository": target,
                        "targetRuntime": "omnigent",
                        "workflow": {
                            "taskTemplate": {
                                "slug": "pr-review-resolve",
                                "scope": "global",
                            },
                            "inputs": {
                                "pull_request": "350",
                                "review_only": True,
                            },
                        },
                    },
                    allow_goal_schedule=False,
                )
        workflow = submitted["workflow"]
        assert submitted["repository"] == target
        assert workflow["steps"][0]["tool"]["inputs"]["repository"] == repository
        workflow["steps"] = _normalize_task_steps(workflow)
    if graph_kind == "wrong-target":
        workflow["steps"][0]["tool"]["inputs"]["repository"] = "MoonLadderStudios/Other"
    if graph_kind == "agent":
        workflow = {"instructions": "Read the repository with an agent."}
    elif graph_kind == "mixed":
        workflow["steps"].append(
            {
                "type": "skill",
                "skill": {"id": "auto"},
                "instructions": "Summarize the review with an agent.",
            }
        )
    if graph_kind == "mixed":
        from api_service.api.routers.executions import _normalize_task_steps

        workflow["steps"] = _normalize_task_steps(workflow)
    store = InMemoryExecutionPlanStore()

    class Artifacts(_ArtifactService):
        async def read(self, *, artifact_id, **_kwargs):
            return SimpleNamespace(), self.payloads[artifact_id]

    artifacts = Artifacts()
    parameters = {
        "model": "example/model",
        "targetRuntime": "omnigent",
        "repository": target,
        "publishMode": "none",
        "requiredCapabilities": ["git", "gh"],
        "workflow": workflow,
    }
    from api_service.api.routers.executions import (
        _build_original_workflow_input_snapshot_payload,
        _snapshot_source_payload_from_parameters,
    )

    source, frozen_workflow = _snapshot_source_payload_from_parameters(parameters)
    snapshot = _build_original_workflow_input_snapshot_payload(
        source_kind="create", payload=source, task_payload=frozen_workflow
    )
    if graph_kind == "schedule":
        snapshot = {
            "snapshotVersion": "original-task-input/v1",
            "source": {"kind": "schedule"},
            "target": {"initialParameters": parameters},
        }
    elif graph_kind == "rerun":
        snapshot["source"]["kind"] = "rerun"
    snapshot_ref, snapshot_digest = await service.persist_json_artifact(
        artifact_service=artifacts,
        principal="user-1",
        artifact_class="original_task_input_snapshot",
        payload=snapshot,
    )

    async def admit(parent_plan=None):
        return await service.compile_and_persist_execution_plan(
            session_factory=sessions,
            execution_plan_store=store,
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="mm:codex-default-repository",
            agent_profile_snapshot=_snapshot(
                harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
            ),
            provider_profile=SimpleNamespace(
                profile_id="codex", runtime_id="codex_cli", provider_id="openai"
            ),
            initial_parameters=parameters,
            parent_repository_plan=parent_plan,
            authored_request_ref=snapshot_ref,
            authored_request_digest=snapshot_digest,
            task_input_snapshot_ref=snapshot_ref,
            task_input_snapshot_digest=snapshot_digest,
        )

    try:
        if graph_kind == "missing-target":
            with pytest.raises(ValueError, match="selected GitHub repository"):
                await admit()
            assert store._plans == {}
            return
        if graph_kind == "wrong-target":
            with pytest.raises(ValueError, match="target conflicts"):
                await admit()
            assert store._plans == {}
            return
        if graph_kind == "unassigned":
            from moonmind.workflows.executions.repository_contract import (
                RepositoryRouteError,
            )

            with pytest.raises(RepositoryRouteError, match="not assigned"):
                await admit()
            assert store._plans == {}
            return
        if outcome == "reject":
            with pytest.raises(ValueError, match="codex-repository.*profile-bound"):
                await admit()
            assert store._plans == {}
            assert all(
                json.loads(payload).get("schemaVersion")
                != "moonmind.omnigent-execution-plan-envelope.v1"
                for payload in artifacts.payloads.values()
            )
            return
        compiled = await admit()
        if graph_kind == "child-agent":
            workflow["steps"] = [
                {
                    "type": "skill",
                    "skill": {"id": "auto"},
                    "instructions": "Reuse parent credentials",
                }
            ]
            with pytest.raises(ValueError, match="native repository authority.*child"):
                await admit(parent_plan=compiled.envelope)
            assert len(store._plans) == 1
    finally:
        await engine.dispose()

    plan = compiled.envelope.payload
    assert "collaboration" in plan.resolvedTools.get("repositoryAccess", {}), (
        graph_kind,
        selection,
        plan.executionRealizerRef,
        plan.resolvedTools,
    )
    binding = plan.credentialBindings["collaboration"]
    assert binding.consumer == "native"
    assert set(plan.resolvedTools["repositoryAccess"]) == {"collaboration"}
    assert "source" not in plan.credentialBindings
    assert "destination" not in plan.credentialBindings
    access = plan.resolvedTools["repositoryAccess"]["collaboration"]
    selection_name = selection
    selection = json.loads(
        artifacts.payloads[access["artifactRef"].removeprefix("artifact:")]
    )["selection"]
    assert set(selection["operations"]) == {"read", "review_request"}
    assert selection["connectionId"] == (
        DEFAULT_GIT_CONNECTION_REF
        if selection_name == "explicit-default"
        else "codex-repository"
    )
    assert plan.executionRealizerRef == "codex-profile-bound@1"
    assert await store.load(compiled.envelope.planRef) == compiled.envelope


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "agent",
        "mixed",
        "write_tool",
        "merge_tool",
        "skill_tool",
        "dynamic",
        "nested",
        "input_artifact",
        "authored_marker",
        "dynamic_input",
        "template_input",
    ],
)
async def test_native_review_proof_rejects_other_execution_graphs(tmp_path, mutation):
    from tests.unit.api.test_pr_review_resolve_preset import _expand

    workflow = await _expand(tmp_path, {"pull_request": "350", "review_only": True})
    parameters = {"repository": "MoonLadderStudios/MoonMind", "workflow": workflow}
    assert service._native_review_graph(parameters) is not None
    step = workflow["steps"][0]
    if mutation == "agent":
        step["type"] = "agent"
    elif mutation == "mixed":
        workflow["steps"].append({"type": "agent", "instructions": "Run an agent"})
    elif mutation in {"write_tool", "merge_tool", "skill_tool"}:
        step["tool"]["id"] = {
            "write_tool": "github.update_issue_status",
            "merge_tool": "github.merge_pull_request",
            "skill_tool": "pr-resolver",
        }[mutation]
    elif mutation == "dynamic":
        step["annotations"] = {"remediationLoop": {"kind": "remediation_loop"}}
    elif mutation == "nested":
        step["steps"] = [{"type": "agent", "instructions": "Run an agent"}]
    elif mutation == "input_artifact":
        workflow["inputArtifactRef"] = "art_other_graph"
    elif mutation == "dynamic_input":
        step["tool"]["inputs"]["pullRequest"] = {"$ref": "inputs.other"}
    elif mutation == "template_input":
        step["tool"]["inputs"]["pullRequest"] = "{{ inputs.other }}"
    elif mutation == "authored_marker":
        workflow["steps"] = [{"type": "agent", "instructions": "Run an agent"}]
        parameters["consumer"] = "native"
        parameters["nativeReview"] = True
    assert service._native_review_graph(parameters) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["digest", "agent", "repository", "review_mode", "selector"]
)
async def test_native_review_proof_checks_frozen_input_bytes(tmp_path, mutation):
    from tests.unit.api.test_pr_review_resolve_preset import _expand

    workflow = await _expand(tmp_path, {"pull_request": "350", "review_only": True})
    parameters = {"repository": "MoonLadderStudios/MoonMind", "workflow": workflow}
    frozen = copy.deepcopy(parameters)
    if mutation == "agent":
        frozen["workflow"]["steps"][0]["type"] = "agent"
    elif mutation == "repository":
        frozen["repository"] = "MoonLadderStudios/Other"
    elif mutation == "review_mode":
        frozen["workflow"]["publish"]["mergeAutomation"]["finishMode"] = "merge"
    elif mutation == "selector":
        frozen["workflow"]["steps"][0]["tool"]["inputs"]["pullRequest"] = "999"
    body = json.dumps({"snapshotVersion": 1, "draft": frozen}).encode()

    class Artifacts:
        async def read(self, *, artifact_id, principal, allow_restricted_raw):
            assert (artifact_id, principal, allow_restricted_raw) == (
                "art_frozen",
                "user-1",
                True,
            )
            return None, body

    digest = "sha256:" + "0" * 64 if mutation == "digest" else service._sha256(body)
    with pytest.raises(ValueError, match="native review graph.*snapshot"):
        await service._verify_native_review_graph(
            initial_parameters=parameters,
            artifact_service=Artifacts(),
            principal="user-1",
            task_input_snapshot_ref="artifact:art_frozen",
            task_input_snapshot_digest=digest,
        )
