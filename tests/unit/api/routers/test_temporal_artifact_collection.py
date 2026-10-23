"""Route-to-database tests for the dashboard artifact collection.

The collection endpoint must cost a bounded number of SQL statements per
page however many artifacts exist, while keeping category exclusion,
search, newest-first ordering, link de-duplication and the operator vs
machine-principal read boundary.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    _resolve_principal,
    router,
)
from api_service.db import models as db_models
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio]

_BASE_TIME = datetime(2026, 10, 1, tzinfo=UTC)
_OBSERVABILITY_PREFIXES = (
    "output.logs",
    "debug.",
    "observability.",
    "runtime.stderr",
    "runtime.diagnostics",
)


@dataclass
class _Row:
    artifact_id: str
    created_at: datetime
    content_type: str
    owner: str | None = None
    deleted: bool = False
    # (link_type, label, workflow_id)
    links: list[tuple[str, str, str]] = field(default_factory=list)

    def category(self) -> set[str]:
        types = [link_type for link_type, _label, _wf in self.links]
        found = set()
        if any(t.startswith("report.") for t in types):
            found.add("reports")
        if any(t.startswith(_OBSERVABILITY_PREFIXES) for t in types):
            found.add("observability")
        return found or {"artifacts"}

    def matches(self, query: str) -> bool:
        needle = query.casefold()
        fields = [self.artifact_id, self.content_type]
        for link_type, label, _wf in self.links:
            fields.extend([link_type, label])
        return any(needle in value.casefold() for value in fields)


def _mixed_rows(count: int) -> list[_Row]:
    """Artifacts spanning every category; index order is creation order."""

    rows: list[_Row] = []
    for index in range(count):
        created_at = _BASE_TIME + timedelta(seconds=index)
        if index == 62:
            # Shares 61's timestamp, so artifact_id breaks the tie.
            created_at = _BASE_TIME + timedelta(seconds=61)
        row = _Row(
            artifact_id=f"art_{index:04d}",
            created_at=created_at,
            content_type="image/png" if index == 99 else (
                "text/plain" if index % 2 else "application/json"
            ),
            deleted=index % 17 == 3,
        )
        if index % 10 == 0:
            row.links.append(("report.primary", f"Report {index}", "wf-1"))
        if index % 7 == 0:
            row.links.append(("output.logs", f"Log {index}", "wf-1"))
        if not row.links and index % 13 != 0:
            label = "50% done" if index == 33 else f"Output {index}"
            row.links.append(("output.primary", label, "wf-1"))
            if index % 5 == 0:
                # A second artifact-category link must not duplicate the row.
                row.links.append(("input.plan", f"Plan {index}", "wf-2"))
        rows.append(row)
    return rows


def _expected(rows: list[_Row], category: str, query: str | None = None) -> list[str]:
    selected = [
        row
        for row in rows
        if not row.deleted
        and category in row.category()
        and (not query or row.matches(query))
    ]
    selected.sort(key=lambda row: (row.created_at, row.artifact_id), reverse=True)
    return [row.artifact_id for row in selected]


@asynccontextmanager
async def _collection_client(tmp_path: Path, rows: list[_Row], principal: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/collection.db", future=True
    )
    statements: list[str] = []
    event.listen(
        engine.sync_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, *_args: statements.append(statement),
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(db_models.Base.metadata.create_all)
    async with session_maker() as session:
        for row in rows:
            session.add(
                db_models.TemporalArtifact(
                    artifact_id=row.artifact_id,
                    created_at=row.created_at,
                    created_by_principal=row.owner,
                    content_type=row.content_type,
                    storage_key=f"collection/{row.artifact_id}",
                    status=db_models.TemporalArtifactStatus.COMPLETE,
                    deleted_at=row.created_at if row.deleted else None,
                )
            )
        await session.flush()
        for row in rows:
            for offset, (link_type, label, workflow_id) in enumerate(row.links):
                session.add(
                    db_models.TemporalArtifactLink(
                        artifact_id=row.artifact_id,
                        namespace="moonmind",
                        workflow_id=workflow_id,
                        run_id=f"run-{workflow_id}",
                        link_type=link_type,
                        label=label,
                        created_at=row.created_at + timedelta(milliseconds=offset),
                    )
                )
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    async with session_maker() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        app.dependency_overrides[_get_temporal_artifact_service] = lambda: service
        app.dependency_overrides[_resolve_principal] = lambda: principal
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client, statements
    await engine.dispose()


async def _all_pages(client, statements, *, category, limit, query=None):
    """Walk every page; return ids, has_more flags and statements per page."""

    ids: list[str] = []
    flags: list[bool] = []
    costs: list[int] = []
    offset = 0
    while True:
        params = {"category": category, "offset": offset, "limit": limit}
        if query is not None:
            params["q"] = query
        statements.clear()
        response = await client.get("/api/artifacts/collection", params=params)
        costs.append(len(statements))
        assert response.status_code == 200, response.text
        body = response.json()
        assert "total" not in body
        ids.extend(item["artifact_id"] for item in body["items"])
        flags.append(body["has_more"])
        if not body["has_more"]:
            return ids, flags, costs
        assert len(body["items"]) == limit
        offset += limit


async def test_operator_collection_pages_cost_bounded_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    # More rows than one internal candidate batch of the previous scan.
    rows = _mixed_rows(260)
    async with _collection_client(tmp_path, rows, "operator") as (client, statements):
        for category in ("artifacts", "reports", "observability"):
            expected = _expected(rows, category)
            assert expected, category
            ids, flags, costs = await _all_pages(
                client, statements, category=category, limit=25
            )
            assert ids == expected, category
            assert flags[-1] is False and all(flags[:-1])
            # One page query plus one link lookup, independent of row count.
            assert max(costs) <= 2, (category, costs)

        # Report and log links exclude an artifact from the artifacts surface
        # but list it under both of its own categories.
        assert "art_0070" in _expected(rows, "reports")
        assert "art_0070" in _expected(rows, "observability")
        assert "art_0070" not in _expected(rows, "artifacts")

        for category, query in (
            ("reports", "REPORT 1"),  # label, case-insensitive
            ("artifacts", "input.pl"),  # link type
            ("artifacts", "image/"),  # content type
            ("artifacts", "art_0044"),  # artifact id
            ("artifacts", "%"),  # LIKE wildcards match literally
            ("observability", "no-such-evidence"),
        ):
            ids, _flags, costs = await _all_pages(
                client, statements, category=category, limit=10, query=query
            )
            assert ids == _expected(rows, category, query), (category, query)
            assert max(costs) <= 2, (category, query, costs)
        assert await _all_pages(
            client, statements, category="artifacts", limit=10, query="%"
        ) == (["art_0033"], [False], [2])

        response = await client.get(
            "/api/artifacts/collection", params={"category": "reports", "limit": 1}
        )
        row = response.json()["items"][0]
        assert row["link_type"] == "report.primary"
        assert row["download_url"] == f"/api/artifacts/{row['artifact_id']}/download"


def _machine_rows() -> list[_Row]:
    """Restricted rows are newest, so visible ones sit past the first batch."""

    rows: list[_Row] = []
    clock = iter(range(1_000))

    def add(prefix: str, count: int, owner: str | None, workflow_id: str) -> None:
        for index in range(count):
            rows.append(
                _Row(
                    artifact_id=f"art_{prefix}_{index:03d}",
                    created_at=_BASE_TIME - timedelta(seconds=next(clock)),
                    content_type="text/plain",
                    owner=owner,
                    links=[("output.primary", f"{prefix} {index}", workflow_id)],
                )
            )

    add("hidden", 230, "workflow:wf-other", "wf-other")
    add("linked", 5, "workflow:wf-other", "wf-mine")
    add("owned", 3, "workflow:wf-mine", "wf-other")
    add("unowned", 2, None, "wf-other")
    return rows


async def test_machine_principal_collection_keeps_execution_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    rows = _machine_rows()
    async with _collection_client(tmp_path, rows, "workflow:wf-mine") as (
        client,
        statements,
    ):
        ids, flags, _costs = await _all_pages(
            client, statements, category="artifacts", limit=4
        )
    visible = [
        row.artifact_id
        for row in rows
        if not row.artifact_id.startswith("art_hidden")
    ]
    assert ids == visible
    assert flags == [True, True, False]

    (tmp_path / "service").mkdir()
    async with _collection_client(tmp_path / "service", rows, "service:collector") as (
        client,
        statements,
    ):
        ids, _flags, _costs = await _all_pages(
            client, statements, category="artifacts", limit=25
        )
    assert ids == ["art_unowned_000", "art_unowned_001"]
