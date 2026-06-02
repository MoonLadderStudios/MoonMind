"""Manifest v0 pipeline runner.

Coordinates the end-to-end ingest pipeline:
    validate → plan → fetch → transform → embed → upsert

Uses the :class:`ReaderAdapter` registry to resolve ``dataSources[].type``
and delegates to per-source adapters for fetch/state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from moonmind.manifest.incremental import (
    IncrementalIndexWriter,
    IndexState,
    build_changeset,
    default_state_path,
    source_documents,
    splitter_hash,
    state_hash,
)
from moonmind.manifest.reader_adapter import get_adapter
from moonmind.schemas.manifest_v0_models import ManifestV0

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline result types
# ---------------------------------------------------------------------------

@dataclass
class SourceResult:
    """Result from processing a single data source."""

    source_id: str
    source_type: str
    doc_count: int = 0
    indexed_doc_count: int = 0
    deleted_doc_count: int = 0
    chunk_count: int = 0
    skipped: bool = False
    error: Optional[str] = None
    state: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PipelineResult:
    """Aggregated result from a pipeline run."""

    manifest_name: str
    sources: List[SourceResult] = field(default_factory=list)
    total_docs: int = 0
    total_chunks: int = 0
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest_name,
            "total_docs": self.total_docs,
            "total_chunks": self.total_chunks,
            "dry_run": self.dry_run,
            "sources": [
                {
                    "id": s.source_id,
                    "type": s.source_type,
                    "docs": s.doc_count,
                    "indexed_docs": s.indexed_doc_count,
                    "deleted_docs": s.deleted_doc_count,
                    "chunks": s.chunk_count,
                    "skipped": s.skipped,
                    "error": s.error,
                    "state": s.state,
                }
                for s in self.sources
            ],
        }

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class ManifestPipeline:
    """Execute a manifest v0 pipeline.

    Usage::

        pipeline = ManifestPipeline(manifest)
        # Dry-run plan
        plan_result = pipeline.plan()
        # Full execution
        result = pipeline.run()
    """

    def __init__(
        self,
        manifest: ManifestV0,
        logger: Optional[logging.Logger] = None,
        state_path: Path | str | None = None,
        index_writer: IncrementalIndexWriter | None = None,
    ) -> None:
        self.manifest = manifest
        self.log = logger or logging.getLogger(__name__)
        self._state_path = Path(state_path) if state_path is not None else None
        self._index_writer = index_writer

        # Ensure adapters are registered
        try:
            import moonmind.manifest.adapters  # noqa: F401 — side-effect import
        except ImportError:
            self.log.debug("Built-in adapters module not available")

    def _index_name(self) -> str:
        if self.manifest.vectorStore.indexName:
            return self.manifest.vectorStore.indexName
        return self.manifest.indices[0].id

    def _resolve_state_path(self) -> Path:
        if self._state_path is not None:
            return self._state_path
        connection = self.manifest.vectorStore.connection
        raw = getattr(connection, "incrementalStatePath", None)
        if isinstance(raw, str) and raw.strip():
            return Path(raw)
        for index in self.manifest.indices:
            if index.persist and index.persist.path:
                return Path(index.persist.path).with_suffix(".incremental_state.json")
        return default_state_path(
            manifest_name=self.manifest.metadata.name,
            index_name=self._index_name(),
        )

    @staticmethod
    def _cursor_supports_fetch_skip(cursor: dict[str, Any]) -> bool:
        for key in ("files", "commit", "commit_sha", "revision", "etag"):
            if key in cursor:
                return True
        return False

    def plan(self) -> PipelineResult:
        """Dry-run: enumerate sources and estimate scope without writes."""
        result = PipelineResult(
            manifest_name=self.manifest.metadata.name,
            dry_run=True,
        )

        for ds in self.manifest.dataSources:
            try:
                adapter_cls = get_adapter(ds.type)
                adapter = adapter_cls(ds)
                plan = adapter.plan()
                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    doc_count=plan.estimated_docs,
                )
                result.total_docs += plan.estimated_docs
            except KeyError:
                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    error=f"No adapter registered for type '{ds.type}'",
                )
            except Exception as exc:
                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    error=str(exc),
                )

            result.sources.append(src_result)

        return result

    def run(self) -> PipelineResult:
        """Full pipeline: fetch changed sources, apply deltas, and persist state."""
        result = PipelineResult(
            manifest_name=self.manifest.metadata.name,
            dry_run=False,
        )

        run_cfg = self.manifest.run
        _concurrency = run_cfg.concurrency if run_cfg else 6  # noqa: F841 — reserved for T019 async fan-out
        error_policy = run_cfg.errorPolicy if run_cfg else "continue"
        state_path = self._resolve_state_path()
        index_state = IndexState.load(
            state_path,
            manifest_name=self.manifest.metadata.name,
            index_name=self._index_name(),
        )
        splitter = self.manifest.transforms.splitter if self.manifest.transforms else None

        for ds in self.manifest.dataSources:
            self.log.info("Processing data source: %s (%s)", ds.id, ds.type)

            try:
                adapter_cls = get_adapter(ds.type)
                adapter = adapter_cls(ds)
            except KeyError:
                msg = f"No adapter registered for type '{ds.type}'"
                self.log.error(msg)
                result.sources.append(
                    SourceResult(source_id=ds.id, source_type=ds.type, error=msg)
                )
                if error_policy == "stopOnFirstError":
                    break
                continue

            try:
                source_cursor = dict(adapter.state())
                source_cursor["splitter_hash"] = splitter_hash(splitter)
                previous_snapshot = index_state.sources.get(ds.id)
                current_state_hash = state_hash(source_cursor)
                if (
                    previous_snapshot is not None
                    and previous_snapshot.state_hash == current_state_hash
                    and self._cursor_supports_fetch_skip(source_cursor)
                ):
                    self.log.info(
                        "Source %s unchanged; skipping fetch and re-index",
                        ds.id,
                    )
                    result.sources.append(
                        SourceResult(
                            source_id=ds.id,
                            source_type=ds.type,
                            skipped=True,
                            state=source_cursor,
                        )
                    )
                    continue

                raw_documents = list(adapter.fetch())
                documents = source_documents(
                    source_id=ds.id,
                    raw_documents=raw_documents,
                )
                changeset = build_changeset(
                    source_id=ds.id,
                    cursor=source_cursor,
                    documents=documents,
                    previous=previous_snapshot,
                    splitter=splitter,
                )

                if self._index_writer is not None:
                    self._index_writer.delete_points(changeset.stale_point_ids)
                    self._index_writer.upsert_chunks(changeset.chunks)

                index_state.sources[ds.id] = changeset.next_snapshot
                index_state.save(state_path)

                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    doc_count=len(documents),
                    indexed_doc_count=len(changeset.changed_documents),
                    deleted_doc_count=len(changeset.deleted_document_ids),
                    chunk_count=len(changeset.chunks),
                    state=source_cursor,
                )
                result.total_docs += len(documents)
                result.total_chunks += len(changeset.chunks)
                self.log.info(
                    "Source %s: fetched %d docs, indexed %d docs, deleted %d docs",
                    ds.id,
                    len(documents),
                    len(changeset.changed_documents),
                    len(changeset.deleted_document_ids),
                )

            except Exception as exc:
                self.log.exception(
                    "Error processing source %s: %s", ds.id, exc
                )
                result.sources.append(
                    SourceResult(
                        source_id=ds.id,
                        source_type=ds.type,
                        error=str(exc),
                    )
                )
                if error_policy == "stopOnFirstError":
                    break
                continue

            result.sources.append(src_result)

        index_state.save(state_path)
        return result
