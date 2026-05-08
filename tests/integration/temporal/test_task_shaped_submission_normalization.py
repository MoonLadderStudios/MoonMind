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

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    service = AsyncMock()
    service.create_execution.return_value = _build_execution_record()
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[get_temporal_client] = lambda: AsyncMock()
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
        duplicate_response = test_client.post(
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

    assert target_branch_response.status_code == 422
    assert duplicate_response.status_code == 422
    service.create_execution.assert_not_awaited()
