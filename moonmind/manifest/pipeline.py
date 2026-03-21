"""Manifest v0 pipeline runner.

Coordinates the end-to-end ingest pipeline:
    validate → plan → fetch → transform → embed → upsert

Uses the :class:`ReaderAdapter` registry to resolve ``dataSources[].type``
and delegates to per-source adapters for fetch/state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from moonmind.manifest.reader_adapter import PlanResult, get_adapter
from moonmind.schemas.manifest_v0_models import ManifestV0, DataSourceConfig

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
    error: Optional[str] = None


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
                    "error": s.error,
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
    ) -> None:
        self.manifest = manifest
        self.log = logger or logging.getLogger(__name__)

        # Ensure adapters are registered
        try:
            import moonmind.manifest.adapters  # noqa: F401 — side-effect import
        except ImportError:
            self.log.debug("Built-in adapters module not available")

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
        """Full pipeline: fetch all sources, apply transforms, return docs.

        Note: Embedding and upsert to vector store require integration with
        the embedding client and Qdrant, which is wired separately via
        Temporal Activities (T019).
        """
        result = PipelineResult(
            manifest_name=self.manifest.metadata.name,
            dry_run=False,
        )

        run_cfg = self.manifest.run
        _concurrency = run_cfg.concurrency if run_cfg else 6  # noqa: F841 — reserved for T019 async fan-out
        error_policy = run_cfg.errorPolicy if run_cfg else "continue"

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
                doc_count = 0
                for text, metadata in adapter.fetch():
                    doc_count += 1
                    # In full integration, documents would be:
                    # 1. Transformed (htmlToText, splitter, enrichMetadata)
                    # 2. Embedded via embeddings config
                    # 3. Upserted to vectorStore

                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    doc_count=doc_count,
                )
                result.total_docs += doc_count

                # Record state for incremental re-index
                state = adapter.state()
                self.log.info(
                    "Source %s: fetched %d docs, state=%s",
                    ds.id, doc_count, state,
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

        return result
