"""Selected assessment prerequisites fail before provider or plan side effects."""

import copy

import pytest

from api_service.services import omnigent_execution_plan_service as plans
from tests.artifact_access import access_for_test


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", ["workflow", "task"])
@pytest.mark.parametrize("kind", ["repository-scan", "web-assessment"])
async def test_unready_assessment_fails_before_provider_compilation(
    monkeypatch, envelope, kind
):
    monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_ENABLED", "false")
    assessment = (
        {"scanner": "gitleaks", "outputDirectory": "reports"}
        if kind == "repository-scan"
        else {
            "mode": "baseline",
            "origin": "https://fixture.example.test",
            "routes": [{"path": "/", "methods": ["GET"]}],
            "egressPolicyRef": "fixture-web",
        }
    )
    parameters = {
        "targetRuntime": "omnigent",
        envelope: {
            "steps": [
                {
                    "id": "selected-assessment",
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": assessment},
                    },
                    "annotations": {
                        "securityAssessment": {
                            "kind": kind,
                            "admission": copy.deepcopy(assessment),
                            "phase": "collect",
                        }
                    },
                }
            ],
        },
    }
    original = copy.deepcopy(parameters)
    started = []

    async def profile_work(**kwargs):
        started.append(True)
        raise AssertionError(
            "Profile/provider work started before assessment readiness"
        )

    monkeypatch.setattr(plans, "_try_load_real_harness_config", profile_work)
    with pytest.raises(ValueError):
        await plans.compile_and_persist_execution_plan(
            session_factory=None,
            artifact_service=object(),
            principal="operator",
            workflow_id="readiness-workflow",
            agent_profile_snapshot={"document": {"harness": "codex-native"}},
            provider_profile=None,
            initial_parameters=parameters,
            authored_request_ref="art_authored",
            authored_request_digest="a" * 64,
            task_input_snapshot_ref="art_input",
            task_input_snapshot_digest="b" * 64,
            access=access_for_test("operator"),
        )
    assert not started
    assert parameters == original


@pytest.mark.asyncio
@pytest.mark.parametrize("has_grant", [False, True])
async def test_cloud_plan_uses_current_grant_before_provider_work(
    monkeypatch, has_grant
):
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from moonmind.config import container_backend_settings as backend
    from tests.unit.security import test_cloud_assessment as cloud
    from tests.unit.services.test_repository_scanner_readiness import configured

    settings = replace(
        configured(),
        image_sources=(
            backend.RegistryImageSource(
                "cloud-posture", "registry.example/cloud:fixture"
            ),
        ),
        egress_policies=(cloud.policy(("sts.amazonaws.com", "provider-api")),),
    )
    monkeypatch.setattr(
        backend, "resolve_container_backend_settings", lambda env=None: settings
    )
    admission = cloud.admission().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    scope = cloud.scope().model_dump(mode="json", by_alias=True)
    if has_grant:
        scope["grants"][0]["expiresAt"] = (
            datetime.now(UTC) + timedelta(hours=2)
        ).isoformat()
    else:
        scope["grants"] = []
    parameters = {
        "targetRuntime": "omnigent",
        "assessmentScope": scope,
        "workflow": {
            "steps": [
                {
                    "id": "cloud-selection",
                    "skill": {
                        "id": "security-review",
                        "args": {"assessment": admission},
                    },
                    "annotations": {
                        "securityAssessment": {
                            "kind": "cloud-assessment",
                            "admission": copy.deepcopy(admission),
                            "phase": "collect",
                        }
                    },
                }
            ]
        },
    }
    original = copy.deepcopy(parameters)
    reached = []

    class ProfileStarted(RuntimeError):
        pass

    async def profile_work(**kwargs):
        reached.append(True)
        raise ProfileStarted

    monkeypatch.setattr(plans, "_try_load_real_harness_config", profile_work)
    with pytest.raises(ProfileStarted if has_grant else ValueError):
        await plans.compile_and_persist_execution_plan(
            session_factory=None,
            artifact_service=object(),
            principal="operator",
            workflow_id="cloud-readiness-workflow",
            agent_profile_snapshot={"document": {"harness": "codex-native"}},
            provider_profile=None,
            initial_parameters=parameters,
            authored_request_ref="art_authored",
            authored_request_digest="a" * 64,
            task_input_snapshot_ref="art_input",
            task_input_snapshot_digest="b" * 64,
            access=access_for_test("operator"),
        )
    assert reached == ([True] if has_grant else [])
    assert parameters == original
