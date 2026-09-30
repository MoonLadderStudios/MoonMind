"""Saved-result artifact reads through the real list/download routes.

MoonLadderStudios/MoonMind#4020. Workflow Detail's Saved Results section reads
the execution artifact list and the authorized download route. This exercises
those routes over a real ``TemporalArtifactService`` and local artifact store:
saved report, non-Git, and repository outputs stay listed and downloadable
after the workspace that produced them is gone, restricted raw bytes stay
denied while the redacted preview is served, and incomplete or expired outputs
carry the server evidence the browser classifies.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    _resolve_principal,
    router,
)
from api_service.db.models import Base, TemporalArtifactRedactionLevel
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio]

_WORKFLOW_ID = "mm:saved-source"
_RUN_ID = "saved-run-1"


def _link(link_type: str) -> dict[str, str]:
    return {
        "namespace": "default",
        "workflow_id": _WORKFLOW_ID,
        "run_id": _RUN_ID,
        "link_type": link_type,
    }


async def test_saved_outputs_list_and_download_after_host_workspace_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/saved_results.db", future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = LocalTemporalArtifactStore(tmp_path / "artifact-store")

    # The runtime workspace that produced the outputs.
    workspace = tmp_path / "host-workspace"
    workspace.mkdir()
    (workspace / "report.md").write_text("# Saved report\n")

    async with session_maker() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session), store=store
        )
        report, _ = await service.create(
            principal="workflow-runtime",
            content_type="text/markdown",
            link=_link("report.primary"),
            metadata_json={"title": "Final report"},
        )
        await service.write_complete(
            artifact_id=report.artifact_id,
            principal="workflow-runtime",
            payload=(workspace / "report.md").read_bytes(),
            content_type="text/markdown",
        )
        restricted, _ = await service.create(
            principal="workflow-runtime",
            content_type="text/markdown",
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            link=_link("output.primary"),
            metadata_json={"title": "Restricted notes"},
        )
        await service.write_complete(
            artifact_id=restricted.artifact_id,
            principal="workflow-runtime",
            payload=b"# Notes\ntoken=supersecret\nsafe summary",
            content_type="text/markdown",
        )
        patch, _ = await service.create(
            principal="workflow-runtime",
            content_type="text/x-diff",
            link=_link("patch.diff"),
            metadata_json={"title": "Repository patch"},
        )
        expired, _ = await service.create(
            principal="workflow-runtime",
            content_type="text/plain",
            link=_link("output.summary"),
            metadata_json={"title": "Old summary"},
        )
        await service.write_complete(
            artifact_id=expired.artifact_id,
            principal="workflow-runtime",
            payload=b"summary",
            content_type="text/plain",
        )
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

    # The host is gone: nothing below may depend on the producing workspace.
    shutil.rmtree(workspace)

    async def _service():
        async with session_maker() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session), store=store
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_temporal_artifact_service] = _service
    app.dependency_overrides[_resolve_principal] = lambda: "operator"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://moonmind.test"
    ) as client:
        listed = await client.get(
            f"/api/executions/default/{_WORKFLOW_ID}/{_RUN_ID}/artifacts"
        )
        assert listed.status_code == 200
        by_id = {item["artifact_id"]: item for item in listed.json()["artifacts"]}
        assert set(by_id) == {
            report.artifact_id,
            restricted.artifact_id,
            patch.artifact_id,
            expired.artifact_id,
        }

        saved_report = by_id[report.artifact_id]
        assert saved_report["status"] == "complete"
        assert saved_report["sha256"]
        assert saved_report["size_bytes"] == len(b"# Saved report\n")
        assert saved_report["raw_access_allowed"] is True
        assert saved_report["default_read_ref"]["artifact_id"] == report.artifact_id
        assert saved_report["links"][0]["link_type"] == "report.primary"
        downloaded = await client.get(f"/api/artifacts/{report.artifact_id}/download")
        assert downloaded.status_code == 200
        assert downloaded.content == b"# Saved report\n"

        saved_restricted = by_id[restricted.artifact_id]
        assert saved_restricted["raw_access_allowed"] is False
        preview_id = saved_restricted["preview_artifact_ref"]["artifact_id"]
        assert saved_restricted["default_read_ref"]["artifact_id"] == preview_id
        assert preview_id != restricted.artifact_id
        raw = await client.get(f"/api/artifacts/{restricted.artifact_id}/download")
        assert raw.status_code == 403
        assert raw.json()["detail"]["code"] == "artifact_forbidden"
        preview = await client.get(f"/api/artifacts/{preview_id}/download")
        assert preview.status_code == 200
        assert b"supersecret" not in preview.content

        incomplete = by_id[patch.artifact_id]
        assert incomplete["status"] == "pending_upload"
        assert incomplete["sha256"] is None

        stale = by_id[expired.artifact_id]
        assert datetime.fromisoformat(stale["expires_at"]).replace(
            tzinfo=UTC
        ) < datetime.now(UTC)

    await engine.dispose()
