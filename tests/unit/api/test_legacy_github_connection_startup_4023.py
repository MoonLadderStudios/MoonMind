"""API startup runs the one legacy GitHub migration without blocking (#4023)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import api_service.main as api_main
from api_service.db.models import (
    Base,
    RepositoryConnectionAuditEvent,
    RepositoryConnectionRecord,
    SettingsOverride,
)
from api_service.services.legacy_github_connection import (
    LegacyGitHubMigrationOutcome,
)
from moonmind.workflows.executions.repository_contract import RepositoryClientPolicy

_TOKEN = "ghp_startupTokenValueAAAAAAAAAAAAAAAAAAAA"


@pytest_asyncio.fixture
async def sessions(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/startup.db")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    RepositoryConnectionRecord.__table__,
                    RepositoryConnectionAuditEvent.__table__,
                    SettingsOverride.__table__,
                ],
            )
        )

    @asynccontextmanager
    async def _session_context():
        async with factory() as session:
            yield session

    monkeypatch.setattr(api_main, "get_async_session_context", _session_context)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.launcher.resolve_deployment_git_client_policy",
        lambda: RepositoryClientPolicy(
            pinnedVersion="2.46.0",
            toolBundleRef="repository-client:git-system",
            executableSha256="sha256:api-host-git",
        ),
    )
    for name in (
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api_main.settings.github, "github_token_secret_ref", None)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_startup_maps_env_token_once_and_logs_references_only(
    sessions, monkeypatch, caplog
):
    monkeypatch.setenv("GITHUB_TOKEN", _TOKEN)
    caplog.set_level(logging.INFO, logger=api_main.logger.name)

    first = await api_main._migrate_legacy_github_connection()
    second = await api_main._migrate_legacy_github_connection()

    assert first is not None and second is not None
    assert first.outcome is LegacyGitHubMigrationOutcome.MIGRATED
    assert second.outcome is LegacyGitHubMigrationOutcome.ALREADY_PRESENT
    assert "env://GITHUB_TOKEN" in caplog.text
    assert _TOKEN not in caplog.text


@pytest.mark.asyncio
async def test_startup_reports_missing_evidence_without_blocking(
    sessions, monkeypatch, caplog
):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    caplog.set_level(logging.INFO, logger=api_main.logger.name)

    result = await api_main._migrate_legacy_github_connection()

    assert result is not None
    assert result.outcome is LegacyGitHubMigrationOutcome.ABSENT
    assert "repository-connection:git-default" in caplog.text
    assert "GITHUB_TOKEN" in caplog.text


@pytest.mark.asyncio
async def test_startup_survives_database_failure(monkeypatch, caplog):
    monkeypatch.setenv("GITHUB_TOKEN", _TOKEN)

    @asynccontextmanager
    async def _unavailable():
        raise ConnectionRefusedError("database is starting")
        yield  # pragma: no cover

    monkeypatch.setattr(api_main, "get_async_session_context", _unavailable)
    caplog.set_level(logging.WARNING, logger=api_main.logger.name)

    assert await api_main._migrate_legacy_github_connection() is None
    assert "legacy GitHub credential migration" in caplog.text
    assert _TOKEN not in caplog.text
