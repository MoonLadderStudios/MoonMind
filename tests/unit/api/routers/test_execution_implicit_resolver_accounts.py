"""Saved account intent reaches implicit PR resolver agents and Omnigent admission."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.provider_profile_readiness import provider_profile_launch_ready
from moonmind.config.settings import settings
from moonmind.workflows.executions.execution_contract import workflow_requires_agent
from moonmind.workflows.temporal.workflows.merge_gate import build_resolver_run_request
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.api.routers.test_execution_account_intent_matrix import (
    body_for,
    choose,
    matrix_context as matrix_context,
    saved_profile_submission as saved_profile_submission,
    submit,
)
from tests.unit.api.routers.test_execution_saved_profile_boundaries import agent_requests


def _finishing_workflow(finish_mode, *, runtime="codex_cli", explicit=False):
    workflow = {
        "instructions": "Resolve the target then finish the pull request",
        "runtime": {"mode": runtime},
        "steps": [
            {
                "id": "lookup",
                "type": "tool",
                "tool": {
                    "id": "github.resolve_pull_request_target",
                    "inputs": {"pullRequest": "831"},
                },
            }
        ],
        "publish": {
            "mode": "none",
            "mergeAutomation": {"enabled": True, "finishMode": finish_mode},
        },
    }
    if explicit:
        workflow["runtime"]["profileId"] = "saved-codex_cli"
    return workflow


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("finish_mode", ["fix_only", "merge"])
@pytest.mark.parametrize("explicit", [False, True], ids=["saved", "explicit"])
async def test_implicit_resolver_keeps_saved_account(
    matrix_context, monkeypatch, entrypoint, finish_mode, explicit
):
    ctx = matrix_context
    monkeypatch.setattr(settings.feature_flags, "container_jobs_enabled", True)
    task = _finishing_workflow(finish_mode, explicit=explicit)
    ctx.settings_reads.clear()
    recorded = await submit(ctx, entrypoint, task)
    parent = MoonMindRunWorkflow()
    parent._repo = "MoonLadderStudios/MoonMind"
    monkeypatch.setattr(parent, "_workflow_patch_enabled", lambda _patch: True)
    gate = parent._build_merge_gate_start_payload(
        parameters=recorded,
        pull_request_url="https://github.com/MoonLadderStudios/MoonMind/pull/831",
        head_sha="a" * 40,
        parent_workflow_id="mm:review-parent",
        parent_run_id="run-review",
    )
    assert gate is not None
    child = build_resolver_run_request(
        parent_workflow_id="mm:review-parent",
        pull_request=gate["pullRequest"],
        jira_issue_key=None,
        merge_method="squash",
        resolver_template=gate["resolverTemplate"],
        finish_mode=finish_mode,
    )
    assert workflow_requires_agent(
        child["initial_parameters"]["task"], child["initial_parameters"]
    )
    requests = agent_requests(child["initial_parameters"])
    assert len(requests) == 1
    request = next(iter(requests.values()))
    assert request.execution_profile_ref == "saved-codex_cli"
    assert await choose(ctx, request) == "saved-codex_cli"
    assert len(ctx.settings_reads) == (0 if explicit else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fix_only", "merge", "review_only"])
@pytest.mark.parametrize("explicit", [False, True], ids=["saved", "explicit"])
async def test_implicit_resolver_uses_saved_account_before_configuration_fallback(
    saved_profile_submission, monkeypatch, mode, explicit
):
    ctx = saved_profile_submission
    for name in ("saved", "alternative"):
        row = await ctx.session.get(ManagedAgentProviderProfile, f"{name}-codex_cli")
        row.provider_id = "openai"
        row.credential_source = ProviderCredentialSource.OAUTH_VOLUME
        row.runtime_materialization_mode = RuntimeMaterializationMode.OAUTH_HOME
        row.secret_refs = {}
        row.volume_ref = f"{name}-test-codex-oauth"
        row.volume_mount_path = "/root/.codex"
        row.clear_env_keys = [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT",
            "MINIMAX_API_KEY",
        ]
        row.command_behavior = {"auth_readiness": {"launch_ready": True}}
        assert provider_profile_launch_ready(row)
    ctx.session.add_all(
        [
            OmnigentAgentProfile(
                profile_id="codex-configuration",
                display_name="Codex configuration",
                state="active",
                active_version=1,
                default_for_runtime=True,
            ),
            OmnigentAgentProfileVersion(
                profile_id="codex-configuration",
                version=1,
                digest="sha256:" + "a" * 64,
                document={
                    "endpointRef": "default",
                    "bridgeMode": "proxy",
                    "source": {
                        "bundleArtifactRef": "artifact://test-bundle",
                        "bundleDigest": "sha256:" + "b" * 64,
                    },
                    "harness": "codex-native",
                    "execution": {
                        "defaultExecutionProfileRef": "omnigent-codex@1",
                        "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
                    },
                    "providerRequirements": {
                        "runtimeId": "codex_cli",
                        "credentialSource": "oauth_volume",
                        "materializationMode": "oauth_home",
                        "providerIds": ["openai"],
                    },
                    "policyRef": "default@1",
                },
                validation_result={"ready": True},
                rollout_metadata={
                    "bundleImport": {"upstreamAgent": {"id": "test-codex-agent"}}
                },
            ),
        ]
    )
    await ctx.session.commit()
    from api_service.services.omnigent_agent_profile_selection import (
        resolve_default_agent_profile_snapshot,
    )

    snapshots = []

    async def capture(*args, **kwargs):
        snapshots.append(await resolve_default_agent_profile_snapshot(*args, **kwargs))
        # Inspect actual account resolution before unrelated artifact compilation.
        raise HTTPException(418, "Reached real snapshot boundary")

    monkeypatch.setattr(
        "api_service.api.routers.executions.resolve_default_agent_profile_snapshot",
        capture,
    )
    workflow = _finishing_workflow(mode, runtime="omnigent", explicit=explicit)
    if mode == "review_only":
        workflow["publish"]["mergeAutomation"]["reviewLoop"] = {
            "enabled": True,
            "provider": "codex",
            "requireFreshReviewForEveryHead": True,
        }
    ctx.settings_reads.clear()
    response = await ctx.client.post(
        "/api/executions",
        json={
            "type": "workflow",
            "payload": {
                "workflow": workflow,
                "targetRuntime": "omnigent",
                "publishMode": "none",
            },
        },
    )
    assert response.status_code == 418, response.text
    assert len(snapshots) == 1
    expected = (
        "alternative-codex_cli"
        if mode == "review_only" and not explicit
        else "saved-codex_cli"
    )
    assert snapshots[0]["providerProfileRef"] == expected
    assert len(ctx.settings_reads) == (1 if mode != "review_only" and not explicit else 0)
    ctx.service._client_adapter.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["raw", "api", "recurring"])
@pytest.mark.parametrize("finish_mode", ["fix_only", "merge"])
async def test_implicit_resolver_missing_saved_account_fails_before_effects(
    matrix_context, entrypoint, finish_mode
):
    ctx = matrix_context
    saved = await ctx.session.get(ManagedAgentProviderProfile, "saved-codex_cli")
    await ctx.session.delete(saved)
    await ctx.session.commit()
    response = await ctx.client.post(
        "/api/executions", json=body_for(entrypoint, _finishing_workflow(finish_mode))
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["message"] == (
        "Provider profile not found: 'saved-codex_cli'."
    )
    ctx.schedule.assert_not_awaited()
    ctx.service._client_adapter.start_workflow.assert_not_awaited()
    assert (await ctx.session.scalars(select(TemporalExecutionCanonicalRecord))).all() == []
