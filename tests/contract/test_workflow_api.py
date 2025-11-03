from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import SpecWorkflowRunModel, WorkflowRunCollectionResponse
from moonmind.workflows.speckit_celery import celery_app, models as workflow_models


@pytest.mark.asyncio
async def test_workflow_endpoints_contract(tmp_path, monkeypatch):
    """Ensure the workflow API adheres to the documented contract."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    feature_key = "001-celery-chain-workflow"
    specs_dir = tmp_path / "specs" / feature_key
    specs_dir.mkdir(parents=True)
    (specs_dir / "tasks.md").write_text(
        """
## Phase 3 – User Story 1
- [ ] T050 Contract test task
""".strip()
        + "\n",
        encoding="utf-8",
    )

    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings.spec_workflow, "test_mode", True, raising=False)
    monkeypatch.setattr(settings.spec_workflow, "repo_root", str(tmp_path), raising=False)
    monkeypatch.setattr(settings.spec_workflow, "tasks_root", "specs", raising=False)
    monkeypatch.setattr(
        settings.spec_workflow, "artifacts_root", str(artifacts_root), raising=False
    )
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

    app.state.settings = settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/workflows/speckit/runs", json={})
        assert response.status_code == 202
        run_model = SpecWorkflowRunModel.model_validate(response.json())
        assert (
            run_model.status
            == workflow_models.SpecWorkflowRunStatus.SUCCEEDED
        )
        assert (
            run_model.phase == workflow_models.SpecWorkflowRunPhase.COMPLETE
        )

        list_response = await client.get("/api/workflows/speckit/runs")
        assert list_response.status_code == 200
        collection = WorkflowRunCollectionResponse.model_validate(list_response.json())
        assert collection.items
        assert collection.items[0].id == run_model.id

        detail_response = await client.get(f"/api/workflows/speckit/runs/{run_model.id}")
        assert detail_response.status_code == 200
        detail_model = SpecWorkflowRunModel.model_validate(detail_response.json())
        assert detail_model.id == run_model.id
        assert detail_model.branch_name is not None
        assert detail_model.pr_url is not None
