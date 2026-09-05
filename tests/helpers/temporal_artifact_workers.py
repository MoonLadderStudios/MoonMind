"""SQLite/local-store substrate for real worker-bound artifact Activities."""

import json
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity

from api_service.db import base as db_base
from api_service.db.models import Base
from moonmind.workflows.temporal.activity_runtime import _bind_activity_handler
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)


class ArtifactWorkers:
    def __init__(self, sessions, store):
        self.sessions = sessions
        self.store = store

    def service(self, session):
        return TemporalArtifactService(
            TemporalArtifactRepository(session), store=self.store
        )

    async def put(self, payload, *, principal="system"):
        async with self.sessions() as session:
            record, _ = await self.service(
                session
            ).put_content_addressed_payload_complete(
                principal=principal,
                payload=(
                    payload
                    if isinstance(payload, bytes)
                    else json.dumps(payload).encode()
                ),
                content_type="application/json",
                scope="boundary-fixture",
            )
            return record.artifact_id

    async def read(self, ref, *, principal="system"):
        async with self.sessions() as session:
            _, content = await self.service(session).read(
                artifact_id=ref.removeprefix("artifact://"),
                principal=principal,
                allow_restricted_raw=True,
            )
            return content

    def bind(self, name, implementation_class=TemporalArtifactActivities, method=None):
        method = method or name.replace(".", "_")

        @activity.defn(name=name)
        async def handler(payload: dict):
            async with self.sessions() as session:
                service = self.service(session)
                implementation = (
                    implementation_class(service)
                    if implementation_class is TemporalArtifactActivities
                    else implementation_class(artifact_service=service)
                )
                bound = _bind_activity_handler(
                    implementation,
                    func=getattr(implementation_class, method),
                    activity_type=name,
                )
                return await bound(payload)

        return handler


@asynccontextmanager
async def artifact_workers(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/boundary.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    try:
        yield ArtifactWorkers(
            sessions, LocalTemporalArtifactStore(tmp_path / "artifact-bytes")
        )
    finally:
        await engine.dispose()
