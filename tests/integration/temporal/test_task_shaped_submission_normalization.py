from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.executions import (
    _get_service,
    get_temporal_client,
    router,
)
from api_service.db.base import get_async_session
from api_service.db.models import TemporalArtifactStatus
from moonmind.config.settings import settings
from tests.unit.api.routers.test_executions import (
    _artifact_session,
    _build_execution_record,
    _override_user_dependencies,
)
from tests.helpers.step_type_payloads import preset_step, skill_step, tool_step

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_temporal_client] = AsyncMock
    _override_user_dependencies(app, is_superuser=False)
    return TestClient(app), service


def test_task_shaped_submission_boundary_exposes_canonical_task_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627INTOBJECTIVE00000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            ),
            SimpleNamespace(
                artifact_id="art_01MM627INTSTEP0000000000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=20,
            ),
        ]
    )

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Preserve MM-627 task shape.",
                        "dependsOn": ["mm:parent"],
                        "runtime": {"mode": "codex", "model": "gpt-5-codex"},
                        "publish": {"mode": "pr"},
                        "git": {"branch": "feature/mm-627"},
                        "inputAttachments": [
                            {
                                "artifactId": "art_01MM627INTOBJECTIVE00000",
                                "filename": "objective.png",
                                "contentType": "image/png",
                                "sizeBytes": 10,
                            }
                        ],
                        "steps": [
                            {
                                "id": "step-1",
                                "instructions": "Run first step.",
                                "jiraOrchestration": {"issueKey": "MM-627"},
                                "inputAttachments": [
                                    {
                                        "artifactId": "art_01MM627INTSTEP0000000000",
                                        "filename": "step.png",
                                        "contentType": "image/png",
                                        "sizeBytes": 20,
                                    }
                                ],
                            }
                        ],
                        "authoredPresets": [{"slug": "jira-orchestrate"}],
                        "appliedStepTemplates": [
                            {"slug": "jira-implementation", "stepIds": ["step-1"]}
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert task["git"] == {"branch": "feature/mm-627"}
    assert "targetBranch" not in task
    assert "targetBranch" not in task["git"]
    assert task["authoredPresets"] == [{"slug": "jira-orchestrate"}]
    assert task["appliedStepTemplates"] == [
        {"slug": "jira-implementation", "stepIds": ["step-1"]}
    ]
    assert task["steps"][0]["jiraOrchestration"] == {"issueKey": "MM-627"}

def test_task_shaped_submission_boundary_preserves_recursive_preset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        []
    )
    authored_presets = [
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
    ]
    composition = {
        "slug": "parent-flow",
        "version": "1.0.0",
        "scope": "global",
        "path": ["parent-flow@1.0.0"],
        "stepIds": ["tpl:parent-flow:1.0.0:01"],
        "includes": [
            {
                "slug": "child-checks",
                "version": "1.0.0",
                "scope": "global",
                "alias": "quality",
                "path": [
                    "parent-flow@1.0.0",
                    "quality:child-checks@1.0.0",
                ],
                "inputMapping": {"target": "preset composition"},
                "stepIds": ["tpl:parent-flow:1.0.0:01"],
                "includes": [],
            }
        ],
    }

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Compile recursive presets.",
                        "runtime": {"mode": "codex"},
                        "publish": {"mode": "none"},
                        "steps": [
                            {
                                "id": "tpl:parent-flow:1.0.0:01",
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
                        "authoredPresets": authored_presets,
                        "appliedStepTemplates": [
                            {
                                "slug": "parent-flow",
                                "version": "1.0.0",
                                "stepIds": ["tpl:parent-flow:1.0.0:01"],
                                "composition": composition,
                                "authoredPresets": authored_presets,
                            }
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert task["authoredPresets"] == authored_presets
    assert task["appliedStepTemplates"][0]["composition"] == composition
    assert task["appliedStepTemplates"][0]["authoredPresets"] == authored_presets
    assert task["steps"][0]["source"]["includePath"] == [
        "parent-flow@1.0.0",
        "quality:child-checks@1.0.0",
    ]
    assert all(step.get("type") != "preset" for step in task["steps"])


def test_mm569_task_shaped_submission_rejects_unresolved_preset_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        []
    )

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Reject unresolved MM-569 preset.",
                        "steps": [preset_step()],
                    },
                },
            },
        )

    assert response.status_code == 422
    assert "task.steps[].type" in response.text
    service.create_execution.assert_not_awaited()


def test_mm569_task_shaped_submission_preserves_flat_tool_skill_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        []
    )
    tool = tool_step()
    skill = skill_step()
    for step in (tool, skill):
        step["source"] = {
            "kind": "preset-derived",
            "presetSlug": "mm569-parent",
            "presetVersion": "1.0.0",
            "includePath": ["mm569-parent@1.0.0"],
        }

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Submit flat MM-569 executable steps.",
                        "steps": [tool, skill],
                    },
                },
            },
        )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert [step["type"] for step in task["steps"]] == ["tool", "skill"]
    assert task["steps"][0]["source"]["presetSlug"] == "mm569-parent"
    assert task["steps"][1]["source"]["presetSlug"] == "mm569-parent"

def test_task_shaped_submission_boundary_does_not_fabricate_preset_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        []
    )

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Manual only.",
                        "steps": [
                            {
                                "id": "manual-1",
                                "type": "skill",
                                "instructions": "Run manual step.",
                                "skill": {"id": "auto"},
                            }
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    task = service.create_execution.await_args.kwargs["initial_parameters"]["task"]
    assert "authoredPresets" not in task
    assert "appliedStepTemplates" not in task
    assert "source" not in task["steps"][0]


def test_task_shaped_submission_boundary_rejects_invalid_alias_and_target_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id="art_01MM627INTDUPLICATE0000",
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )
    attachment = {
        "artifactId": "art_01MM627INTDUPLICATE0000",
        "filename": "same.png",
        "contentType": "image/png",
        "sizeBytes": 10,
    }

    with test_client:
        target_branch_response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Run task.",
                        "git": {"targetBranch": "feature/legacy"},
                    },
                },
            },
        )
        duplicate_target_response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Run task.",
                        "inputAttachments": [attachment],
                        "steps": [
                            {
                                "id": "step-1",
                                "instructions": "Run step.",
                                "inputAttachments": [attachment],
                            }
                        ],
                    },
                },
            },
        )
        duplicate_same_target_response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Run task.",
                        "inputAttachments": [attachment, attachment],
                    },
                },
            },
        )

    assert target_branch_response.status_code == 422
    assert duplicate_target_response.status_code == 422
    assert duplicate_same_target_response.status_code == 422
    service.create_execution.assert_not_awaited()


@pytest.mark.parametrize(
    ("artifact_status", "message_fragment"),
    [
        (TemporalArtifactStatus.PENDING_UPLOAD, "pending_upload"),
        (TemporalArtifactStatus.FAILED, "failed"),
    ],
)
def test_task_shaped_submission_boundary_rejects_unfinalized_binary_ref(
    monkeypatch: pytest.MonkeyPatch,
    artifact_status: TemporalArtifactStatus,
    message_fragment: str,
) -> None:
    """MM-628: unfinalized binary refs fail before execution creation."""

    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    artifact_id = f"art_01MM628INT{artifact_status.value.upper():0<14}"[:30]
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id=artifact_id,
                status=artifact_status,
                content_type="image/png",
                size_bytes=10,
            )
        ]
    )

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Reject unfinalized binary input.",
                        "inputAttachments": [
                            {
                                "artifactId": artifact_id,
                                "filename": "unfinalized.png",
                                "contentType": "image/png",
                                "sizeBytes": 10,
                            }
                        ],
                    },
                },
            },
        )

    assert response.status_code == 422
    assert message_fragment in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()


def test_task_shaped_submission_boundary_rejects_wrong_owner_binary_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MM-628: unauthorized binary refs fail before execution creation."""

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "keycloak")
    monkeypatch.setattr(settings.workflow, "agent_job_attachment_enabled", True)
    test_client, service = _client()
    artifact_id = "art_01MM628INTWRONGOWNER0000"
    test_client.app.dependency_overrides[get_async_session] = lambda: _artifact_session(
        [
            SimpleNamespace(
                artifact_id=artifact_id,
                status=TemporalArtifactStatus.COMPLETE,
                content_type="image/png",
                size_bytes=10,
                created_by_principal="another-user",
            )
        ]
    )

    with test_client:
        response = test_client.post(
            "/api/executions",
            json={
                "type": "task",
                "payload": {
                    "repository": "Moon/Mind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Reject unauthorized binary input.",
                        "inputAttachments": [
                            {
                                "artifactId": artifact_id,
                                "filename": "wrong-owner.png",
                                "contentType": "image/png",
                                "sizeBytes": 10,
                            }
                        ],
                    },
                },
            },
        )

    assert response.status_code == 422
    assert "not authorized" in response.json()["detail"]["message"]
    service.create_execution.assert_not_awaited()
