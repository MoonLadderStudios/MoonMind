from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.task_compatibility import _get_service, router
from api_service.auth_providers import get_current_user
from moonmind.schemas.task_compatibility_models import (
    TaskCompatibilityDetail,
    TaskCompatibilityListResponse,
    TaskCompatibilityRow,
)
from moonmind.workflows.tasks.source_mapping import TaskResolutionAmbiguousError

CURRENT_USER_DEP = get_current_user()


def _build_test_app(service: AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = lambda: service
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(
        id="owner-1",
        email="task-compatibility@example.com",
        is_superuser=False,
    )
    return TestClient(app)


def test_list_compatibility_tasks_forwards_filters_and_returns_payload() -> None:
    service = AsyncMock()
    service.list_tasks.return_value = TaskCompatibilityListResponse(
        items=[
            TaskCompatibilityRow(
                taskId="mm:run-1",
                source="temporal",
                entry="manifest",
                title="Manifest Task",
                summary="Compatibility summary",
                status="queued",
                rawState="initializing",
                temporalStatus="running",
                workflowId="mm:run-1",
                workflowType="MoonMind.ManifestIngest",
                ownerType="user",
                ownerId="owner-1",
                createdAt="2026-03-06T00:00:00Z",
                startedAt="2026-03-06T00:00:00Z",
                updatedAt="2026-03-06T00:00:00Z",
                closedAt=None,
                artifactsCount=1,
                detailHref="/tasks/mm:run-1",
            )
        ],
        nextCursor="compat-cursor",
        count=1,
        countMode="exact",
    )

    with _build_test_app(service) as client:
        response = client.get(
            "/api/tasks/list",
            params={
                "source": "temporal",
                "entry": "manifest",
                "workflowType": "MoonMind.ManifestIngest",
                "status": "queued",
                "ownerType": "user",
                "ownerId": "owner-1",
                "pageSize": 5,
                "cursor": "compat-cursor-0",
            },
        )

    assert response.status_code == 200
    assert response.json()["countMode"] == "exact"
    service.list_tasks.assert_awaited_once_with(
        user=SimpleNamespace(
            id="owner-1",
            email="task-compatibility@example.com",
            is_superuser=False,
        ),
        source="temporal",
        entry="manifest",
        workflow_type="MoonMind.ManifestIngest",
        status_filter="queued",
        owner_type="user",
        owner_id="owner-1",
        page_size=5,
        cursor="compat-cursor-0",
    )


def test_get_compatibility_task_detail_maps_ambiguity_to_conflict() -> None:
    service = AsyncMock()
    service.get_task_detail.side_effect = TaskResolutionAmbiguousError(
        "legacy-task-id",
        {"queue", "temporal"},
    )

    with _build_test_app(service) as client:
        response = client.get("/api/tasks/legacy-task-id")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "ambiguous_task_source",
        "message": (
            "Task legacy-task-id matches multiple execution sources: "
            "queue, temporal. Retry with an explicit source hint."
        ),
        "sources": ["queue", "temporal"],
    }


def test_get_compatibility_task_detail_accepts_source_hint() -> None:
    service = AsyncMock()
    service.get_task_detail.return_value = TaskCompatibilityDetail(
        taskId="mm:run-2",
        source="temporal",
        entry="run",
        title="Compatibility Detail",
        summary="More detail",
        status="running",
        rawState="planning",
        temporalStatus="running",
        workflowId="mm:run-2",
        workflowType="MoonMind.Run",
        ownerType="user",
        ownerId="owner-1",
        createdAt="2026-03-06T00:00:00Z",
        startedAt="2026-03-06T00:00:00Z",
        updatedAt="2026-03-06T00:00:01Z",
        closedAt=None,
        artifactsCount=0,
        detailHref="/tasks/mm:run-2",
        namespace="moonmind",
        temporalRunId="run-2",
        artifactRefs=[],
        searchAttributes={"mm_entry": "run"},
        memo={"title": "Compatibility Detail"},
        parameterPreview={},
    )

    with _build_test_app(service) as client:
        response = client.get("/api/tasks/mm:run-2?source=temporal")

    assert response.status_code == 200
    assert response.json()["workflowId"] == "mm:run-2"
    service.get_task_detail.assert_awaited_once_with(
        task_id="mm:run-2",
        source_hint="temporal",
        user=SimpleNamespace(
            id="owner-1",
            email="task-compatibility@example.com",
            is_superuser=False,
        ),
    )
