"""Saved results stay listed and downloadable after the source host is gone.

MoonLadderStudios/MoonMind#4020. A real managed capture writes saved work
through the production checkpoint-artifact path into a sqlite-backed artifact
service, next to a report and a non-Git output, and the source workspace is
then deleted. Workflow Detail's Saved Results section reads only the existing
run artifact listing and the authorized download endpoint, so those must still
serve every saved output with the evidence the section presents: completeness
(status, digest, size), the saved-work manifest summary, retention/expiry, and
raw-access restrictions.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    _resolve_principal,
    router,
)
from api_service.db import models as db_models
from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.support.saved_work_capture import capture_saved_work

OPERATOR = str(uuid.UUID(int=4020))
SAVED_WORK_MANIFEST = "application/vnd.moonmind.saved-work-manifest+json;version=1"
BASE_FILES = {"src/app.py": "print('base')\n"}


def _mutate(repo: Path) -> None:
    (repo / "src" / "app.py").write_text("print('saved')\n")
    (repo / "notes.md").write_text("added by the run\n")


@pytest.fixture(autouse=True)
def _enforced_access(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    monkeypatch.setattr(settings.security, "high_security_mode", False)


@asynccontextmanager
async def _artifact_service(tmp_path: Path):
    engine = create_async_engine(
        "sqlite+aiosqlite://", future=True, poolclass=StaticPool
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_maker() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
    finally:
        await engine.dispose()


@asynccontextmanager
async def _client(service: TemporalArtifactService):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
    app.dependency_overrides[_resolve_principal] = lambda: OPERATOR
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _put_output(
    service: TemporalArtifactService,
    payload: bytes,
    *,
    content_type: str,
    link_type: str,
    title: str,
) -> str:
    artifact, _reused = await service.put_content_addressed_payload_complete(
        principal="system",
        payload=payload,
        content_type=content_type,
        scope=link_type,
        link={
            "namespace": settings.temporal.namespace,
            "workflow_id": "mm:source",
            "run_id": "source-run",
            "link_type": link_type,
        },
        metadata_json={"title": title},
    )
    return artifact.artifact_id


def _listing_path() -> str:
    return (
        f"/api/executions/{settings.temporal.namespace}/mm%3Asource/source-run"
        "/artifacts"
    )


@pytest.mark.asyncio
async def test_saved_outputs_list_and_download_after_workspace_removal(tmp_path):
    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path, BASE_FILES, _mutate, artifact_service=service
        )
        report_id = await _put_output(
            service,
            b"# Final report\n",
            content_type="text/markdown",
            link_type="report.summary",
            title="Final report",
        )
        output_id = await _put_output(
            service,
            b'{"answer": 42}',
            content_type="application/json",
            link_type="output.primary",
            title="Answer",
        )
        saved.remove_source()
        assert not saved.source_repo.exists()

        async with _client(service) as client:
            response = await client.get(_listing_path())
            assert response.status_code == 200, response.text
            artifacts = {
                item["artifact_id"]: item for item in response.json()["artifacts"]
            }

            manifest_id = saved.saved_work_ref
            manifest = artifacts[manifest_id]
            assert manifest["content_type"] == SAVED_WORK_MANIFEST
            assert manifest["status"] == "complete"
            assert manifest["sha256"] and manifest["size_bytes"] > 0
            assert manifest["raw_access_allowed"] is True
            assert {link["link_type"] for link in manifest["links"]} == {
                "output.checkpoint"
            }

            downloaded = await client.get(f"/api/artifacts/{manifest_id}/download")
            assert downloaded.status_code == 200
            body = json.loads(downloaded.content)
            assert hashlib.sha256(downloaded.content).hexdigest() == manifest["sha256"]

            # The listing's compact summary is a projection of the committed
            # manifest bytes, so the section never parses raw manifests.
            summary = manifest["metadata"]["saved_work_summary"]
            assert summary["capture_id"] == body["captureId"]
            assert summary["required_formats"] == body["requiredFormats"]
            assert summary["exclusion_count"] == len(body["exclusions"])
            assert summary["retention_ref"] == body["retentionRef"]
            parts = {
                output["format"]: output.get("artifact_id")
                for output in summary["outputs"]
            }
            assert parts["full_snapshot"] in artifacts
            assert parts["exact_baseline_delta"] == body["git"]["deltaRef"]

            # Every part of the saved unit is still served without the host.
            checkpoint = next(
                item
                for item in artifacts.values()
                if item["metadata"].get("artifact_kind") == "checkpoint_manifest"
            )
            assert (
                checkpoint["metadata"]["checkpoint_parts"]["archive_artifact_id"]
                == parts["full_snapshot"]
            )
            for part_id in (
                parts["full_snapshot"],
                parts["exact_baseline_delta"],
                checkpoint["artifact_id"],
            ):
                part = artifacts[part_id]
                assert part["status"] == "complete" and part["sha256"]
                fetched = await client.get(f"/api/artifacts/{part_id}/download")
                assert fetched.status_code == 200
                assert hashlib.sha256(fetched.content).hexdigest() == part["sha256"]

            # Report-only and non-Git outputs remain useful on their own.
            for artifact_id, expected in (
                (report_id, b"# Final report\n"),
                (output_id, b'{"answer": 42}'),
            ):
                assert artifacts[artifact_id]["status"] == "complete"
                fetched = await client.get(f"/api/artifacts/{artifact_id}/download")
                assert fetched.status_code == 200
                assert fetched.content == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded_runtime", ["codex_cli", "codex_cloud"])
async def test_historical_runtime_logs_results_and_patches_remain_readable(
    tmp_path, monkeypatch, recorded_runtime
):
    """#4644: persisted outputs and identity do not need an executable adapter."""
    for name in ("CODEX_CLOUD_ENABLED", "CODEX_CLOUD_API_URL", "CODEX_CLOUD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    async with _artifact_service(tmp_path) as service:
        session = service._repository._session
        parameters = {
            "workflow": {
                "instructions": "Original saved task",
                "runtime": {
                    "mode": recorded_runtime,
                    "model": "recorded-model",
                    "effort": "high",
                },
            },
            "runtimeCapabilities": {"runtimeId": recorded_runtime, "version": "v1"},
        }
        record = db_models.TemporalExecutionCanonicalRecord(
            workflow_id="mm:source",
            run_id="source-run",
            namespace=settings.temporal.namespace,
            workflow_type=db_models.TemporalWorkflowType.USER_WORKFLOW,
            owner_id=OPERATOR,
            owner_type=db_models.TemporalExecutionOwnerType.USER,
            entry="user_workflow",
            state=db_models.MoonMindWorkflowState.COMPLETED,
            parameters=parameters,
        )
        session.add(record)
        await session.commit()
        outputs = [
            (b"original run log\n", "text/plain", "output.log"),
            (b'{"summary":"original result"}', "application/json", "output.primary"),
            (b"diff --git a/file b/file\n", "text/x-diff", "output.patch"),
        ]
        refs = []
        for payload, content_type, link_type in outputs:
            refs.append(
                await _put_output(
                    service,
                    payload,
                    content_type=content_type,
                    link_type=link_type,
                    title=link_type,
                )
            )
        record.artifact_refs = list(refs)
        await session.commit()
        async with _client(service) as client:
            response = await client.get(_listing_path())
            assert response.status_code == 200, response.text
            listed = {item["artifact_id"] for item in response.json()["artifacts"]}
            assert listed == set(refs)
            for ref, (payload, _content_type, _link_type) in zip(refs, outputs):
                downloaded = await client.get(f"/api/artifacts/{ref}/download")
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == payload
        await session.refresh(record)
        assert record.parameters == parameters
        assert record.artifact_refs == refs
        assert record.run_id == "source-run"
        assert record.state == db_models.MoonMindWorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_restricted_and_expired_saved_outputs_stay_accurate(tmp_path):
    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path, BASE_FILES, _mutate, artifact_service=service
        )
        saved.remove_source()
        _artifact, manifest_payload = await service.read(
            artifact_id=saved.saved_work_ref, principal=OPERATOR
        )
        manifest = json.loads(manifest_payload)
        archive_id = next(
            output["ref"]
            for output in manifest["outputs"]
            if output["format"] == "full_snapshot"
        )
        delta_id = manifest["git"]["deltaRef"]
        repository = service._repository
        restricted = await repository.get_artifact(delta_id)
        restricted.redaction_level = db_models.TemporalArtifactRedactionLevel.RESTRICTED
        expired = await repository.get_artifact(archive_id)
        expired_at = datetime.now(UTC) - timedelta(days=1)
        expired.expires_at = expired_at
        await repository.commit()

        async with _client(service) as client:
            response = await client.get(_listing_path())
            assert response.status_code == 200, response.text
            artifacts = {
                item["artifact_id"]: item for item in response.json()["artifacts"]
            }

            # Restricted raw bytes are reported as such and denied on download;
            # metadata visibility never authorizes the raw restore.
            assert artifacts[delta_id]["raw_access_allowed"] is False
            denied = await client.get(f"/api/artifacts/{delta_id}/download")
            assert denied.status_code == 403

            # Expiry is the server's timestamp, not a client guess.
            assert datetime.fromisoformat(
                artifacts[archive_id]["expires_at"].replace("Z", "+00:00")
            ).replace(tzinfo=UTC) <= datetime.now(UTC)
            assert artifacts[saved.saved_work_ref]["raw_access_allowed"] is True


@pytest.mark.parametrize("part_format", ["full_snapshot", "exact_baseline_delta"])
@pytest.mark.parametrize(
    "unavailable", ["restricted", "expired", "incomplete", "identity_absent"]
)
@pytest.mark.asyncio
async def test_publication_admission_requires_raw_readable_saved_closure(
    tmp_path: Path, part_format: str, unavailable: str
):
    from api_service.api.routers.executions import (
        SavedWorkPublicationRequest,
        _admit_saved_work_publication,
    )

    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path, BASE_FILES, _mutate, artifact_service=service
        )
        _artifact, manifest_payload = await service.read(
            artifact_id=saved.saved_work_ref, principal=OPERATOR
        )
        manifest = json.loads(manifest_payload)
        part = next(o for o in manifest["outputs"] if o["format"] == part_format)
        assert "exact_baseline_delta" not in manifest["requiredFormats"]
        manifest_ref = saved.saved_work_ref
        if part_format == "exact_baseline_delta":
            # The publisher still reads git.deltaRef when the delta is optional
            # and is absent from the listing's outputs/format summary.
            manifest["outputs"] = [
                output
                for output in manifest["outputs"]
                if output["format"] != "exact_baseline_delta"
            ]
            manifest_ref = await _put_output(
                service,
                json.dumps(manifest).encode(),
                content_type=SAVED_WORK_MANIFEST,
                link_type="output.checkpoint",
                title="Saved work with optional recorded delta",
            )
        artifact = await service._repository.get_artifact(part["ref"])
        if unavailable == "restricted":
            artifact.redaction_level = (
                db_models.TemporalArtifactRedactionLevel.RESTRICTED
            )
        elif unavailable == "expired":
            artifact.expires_at = datetime.now(UTC) - timedelta(days=1)
        elif unavailable == "incomplete":
            artifact.status = db_models.TemporalArtifactStatus.PENDING_UPLOAD
        else:
            artifact.sha256 = None
            artifact.size_bytes = None
        await service._repository.commit()
        saved.remove_source()

        with pytest.raises(HTTPException) as exc:
            await _admit_saved_work_publication(
                canonical=SimpleNamespace(
                    workflow_id="mm:source",
                    run_id="source-run",
                    created_at=datetime.now(UTC),
                ),
                request=SavedWorkPublicationRequest.model_validate(
                    {
                        "savedWorkRef": manifest_ref,
                        "destination": {
                            "repository": "Dest/Repo",
                            "objective": "pr",
                            "baseBranch": "main",
                            "headBranch": "saved/work",
                            "strategy": "additive_import",
                        },
                    }
                ),
                user=SimpleNamespace(id=uuid.UUID(OPERATOR)),
                artifact_service=service,
            )

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "saved_work_unavailable"


@pytest.mark.asyncio
async def test_publication_admits_selected_saved_closure_without_downloading_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api_service.api.routers.executions import (
        SavedWorkPublicationRequest,
        _admit_saved_work_publication,
    )

    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path, BASE_FILES, _mutate, artifact_service=service
        )
        saved.remove_source()
        reads: list[str] = []
        original_read = service.read

        async def read(**kwargs):
            reads.append(kwargs["artifact_id"])
            return await original_read(**kwargs)

        monkeypatch.setattr(service, "read", read)
        contract = await _admit_saved_work_publication(
            canonical=SimpleNamespace(
                workflow_id="mm:source",
                run_id="source-next",
                created_at=datetime.now(UTC),
            ),
            request=SavedWorkPublicationRequest.model_validate(
                {
                    "savedWorkRef": saved.saved_work_ref,
                    "sourceRunId": "source-run",
                    "destination": {
                        "repository": "Dest/Repo",
                        "objective": "pr",
                        "baseBranch": "main",
                        "headBranch": "saved/work",
                        "strategy": "additive_import",
                    },
                }
            ),
            user=SimpleNamespace(id=uuid.UUID(OPERATOR)),
            artifact_service=service,
        )

        assert contract.source_run_id == "source-run"
        assert contract.saved_work_digest == saved.saved_work_digest
        assert reads == [saved.saved_work_ref]
