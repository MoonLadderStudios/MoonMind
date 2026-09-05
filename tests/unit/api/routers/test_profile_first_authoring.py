"""Shared renderer-request replay through Profile resolution and real plan admission.

The frontend pins the selection in profile-first-authoring.json with the
production Create renderer. This half replays that request through the real API,
configuration resolver, snapshot resolver and plan compiler. Only persistence,
credential availability, deployment evidence and Temporal transport are controlled.
"""

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from api_service.api.routers import executions
from api_service.db.base import get_async_session
from api_service.db.models import ManagedAgentProviderProfile
from api_service.services import omnigent_agent_profile_selection as selection
from api_service.services import omnigent_execution_plan_service as plans
from api_service.services.profile_execution_selection import (
    select_execution_configuration,
)
from tests.unit.api.routers.test_executions import _build_execution_record
from tests.unit.api.routers.test_executions import (
    client as client,  # noqa: PLC0414 - pytest fixture
)
from tests.unit.services.test_omnigent_agent_profile_selection import _Session
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _PlanStore,
    _policy_snapshot,
    _protected_support_evidence,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ready_opencode_image_pair as _ready_opencode_image_pair,  # noqa: PLC0414 - pytest fixture
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "frontend/src/runtime/fixtures/profile-first-authoring.json"
    ).read_text()
)


@pytest.mark.parametrize(
    "authored, policy_override",
    [(None, None), (False, None), (True, None), (True, "opencode-on-demand@1")],
)
@pytest.mark.parametrize(
    "change",
    [None, "configuration", "configuration_removed", "unavailable", "target", "profile"],
)
def test_rendered_profile_request_reaches_matching_immutable_plan(
    client, monkeypatch, authored, change, policy_override
):
    test_client, temporal, user = client
    session = _Session()
    fixture = copy.deepcopy(FIXTURE)
    reference = fixture["provider"]["execution_selection"]
    session.profile.profile_id = reference["profileId"]
    session.profile.active_version = reference["version"]
    session.version.version = reference["version"]
    session.version.digest = reference["digest"]
    session.version.document = fixture["configuration"]["versions"][0]["document"]
    session.version.upstream_snapshot = {
        "importReceiptRef": session.version.document["source"]["importReceiptRef"]
    }
    session.provider.profile_id = fixture["provider"]["profile_id"]
    session.provider.runtime_id = "opencode"
    session.provider.provider_id = "opencode-go"
    session.provider.credential_source = "secret_ref"
    session.provider.runtime_materialization_mode = "config_bundle"
    session.provider.default_model = "example/model"
    session.provider.default_effort = None
    session.provider.execution_configuration = None
    session.provider.model_tiers = None
    session.provider.account_label = "OpenCode Go"
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    old_get = session.get

    async def get(model, key):
        if model is ManagedAgentProviderProfile:
            return session.provider
        return await old_get(model, key)

    session.get = get
    test_client.app.dependency_overrides[get_async_session] = lambda: session
    artifacts = _ArtifactService()
    store = _PlanStore(object())
    store.persist = AsyncMock(side_effect=store.persist)
    compiled = []
    original_compile = plans.compile_and_persist_execution_plan

    async def compile_plan(**kwargs):
        kwargs.update(session_factory=object(), execution_plan_store=store)
        result = await original_compile(**kwargs)
        compiled.append(result)
        return result

    monkeypatch.setattr(plans, "compile_and_persist_execution_plan", compile_plan)
    monkeypatch.setattr(
        plans, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )

    async def resolve_policy(**kwargs):
        return _policy_snapshot(harness="opencode-native", policy=kwargs["policy_ref"])

    monkeypatch.setattr(plans, "_resolve_runtime_policy_snapshot", resolve_policy)
    monkeypatch.setattr(
        plans,
        "resolve_execution_evidence",
        lambda payload: (_protected_support_evidence(payload), "supported"),
    )
    monkeypatch.setattr(
        selection, "provider_profile_launch_ready", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        executions, "get_temporal_artifact_service", lambda _: artifacts
    )
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
    )
    # Competing promoted Codex and OpenCode rows reproduce the escaped UI case.
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "true")
    temporal.create_execution.return_value = _build_execution_record()

    assert (
        select_execution_configuration(
            session.provider, [(session.profile, session.version)]
        )
        == reference
    )
    request = fixture["request"]
    runtime = request["payload"]["task"]["runtime"]
    if authored is not None:
        runtime["authored"] = authored
    if policy_override:
        request["payload"]["omnigent"] = {"launchPolicyRef": policy_override}
        runtime.update(model="example/override", effort="xhigh")
    if change == "configuration":
        session.version.version += 1
        session.profile.active_version += 1
    elif change == "configuration_removed":
        # A Codex Profile can support a direct path, but a vanished Omnigent
        # configuration must not turn the displayed selection into that path.
        session.provider.runtime_id = "codex_cli"
        from api_service.services import profile_execution_selection

        monkeypatch.setattr(
            profile_execution_selection,
            "load_execution_configurations",
            AsyncMock(return_value=[]),
        )
    elif change == "unavailable":
        session.version.validation_result = {"ready": False}
    elif change == "target":
        request["payload"]["requestedTargetId"] = "codex.legacy-profile-bound-omnigent"
    elif change == "profile":
        request["payload"]["agentProfile"] = {
            **runtime["executionConfiguration"],
            "providerProfileRef": "different-account",
        }

        # The resolver can only return the selected account from storage.
        # An explicit snapshot naming another account is rejected at the API.
        async def conflicting_snapshot(*args, **kwargs):
            resolved = await selection.resolve_default_agent_profile_snapshot(
                session,
                provider_profile_ref=session.provider.profile_id,
                launch_policy_ref=None,
                consumer_type="workflow",
                consumer_id="conflict",
                user=user,
            )
            return {**resolved, "providerProfileRef": "different-account"}

        monkeypatch.setattr(
            executions, "resolve_agent_profile_snapshot", conflicting_snapshot
        )

    response = test_client.post("/api/executions", json=request)
    if change:
        assert response.status_code in {409, 422}, response.text
        if change == "configuration":
            assert (
                response.json()["detail"]["code"]
                == "profile_execution_configuration_changed"
            )
        elif change == "configuration_removed":
            if authored is False:
                assert "requires targetRuntime='omnigent'" in response.text
            else:
                assert (
                    response.json()["detail"]["code"]
                    == "profile_execution_configuration_required"
                )
        elif change == "unavailable":
            assert (
                response.json()["detail"]["code"]
                == "profile_execution_configuration_required"
            )
        elif change == "target":
            assert "Requested runtime target does not match" in response.text
        elif change == "profile":
            assert "must use the selected Profile" in response.text
        temporal.create_execution.assert_not_awaited()
        assert compiled == []
        store.persist.assert_not_awaited()
        return
    assert response.status_code == 201, response.text
    store.persist.assert_awaited_once()
    plan = compiled[0].envelope.payload
    assert plan.harnessId == fixture["expectedPlan"]["harnessId"]
    assert plan.executionRealizerRef == fixture["expectedPlan"]["executionRealizerRef"]
    assert plan.launchPolicyRef == (policy_override or reference["launchPolicyRef"])
    assert plan.runtimeProviderRollout.targetId == fixture["expectedPlan"]["targetId"]
    parameters = temporal.create_execution.await_args.kwargs["initial_parameters"]
    assert parameters["profileId"] == runtime["profileId"]
    if policy_override:
        assert parameters["model"] == runtime["model"]
        assert parameters["effort"] == runtime["effort"]
    assert parameters["agentProfile"] == runtime["executionConfiguration"]
    assert (
        parameters["runtimeProviderTarget"]["targetId"]
        == plan.runtimeProviderRollout.targetId
    )
    assert (
        parameters["omnigentExecutionPlan"]["planRef"] == compiled[0].envelope.planRef
    )
