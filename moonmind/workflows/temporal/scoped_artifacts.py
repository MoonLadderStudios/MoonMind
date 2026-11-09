"""Session-per-operation adapter over the canonical Temporal artifact owner."""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal.artifacts import ArtifactTransactionOwnership


class ScopedTemporalArtifactService:
    """Open one DB session per artifact operation at an Activity boundary."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def _invoke(self, method: str, **kwargs: Any) -> Any:
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        async with self._session_factory() as artifact_session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(artifact_session),
                transaction_ownership=ArtifactTransactionOwnership.STANDALONE,
            )
            return await getattr(service, method)(**kwargs)

    async def record_assessment_occurrence_evidence(self, **kwargs: Any) -> Any:
        return await self._invoke("record_assessment_occurrence_evidence", **kwargs)

    async def assessment_occurrence(self, **kwargs: Any) -> Any:
        return await self._invoke("assessment_occurrence", **kwargs)

    async def create(self, **kwargs: Any) -> Any:
        return await self._invoke("create", **kwargs)

    async def read(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("read", artifact_id=artifact_id, **kwargs)

    async def get_metadata(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("get_metadata", artifact_id=artifact_id, **kwargs)

    async def read_chunks(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("read_chunks", artifact_id=artifact_id, **kwargs)

    async def write_complete(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("write_complete", artifact_id=artifact_id, **kwargs)

    async def soft_delete(self, *, artifact_id: str, **kwargs: Any) -> Any:
        return await self._invoke("soft_delete", artifact_id=artifact_id, **kwargs)

    async def dependency_proof(self, **kwargs: Any) -> Any:
        # Metadata/integrity proof only, never a child read grant. Preserve
        # this adapter's existing standalone session per operation contract.
        return await self._invoke("dependency_proof", **kwargs)

    async def record_verified_dependencies(self, **kwargs: Any) -> Any:
        return await self._invoke("record_verified_dependencies", **kwargs)

    async def verified_dependencies(self, **kwargs: Any) -> Any:
        return await self._invoke("verified_dependencies", **kwargs)
