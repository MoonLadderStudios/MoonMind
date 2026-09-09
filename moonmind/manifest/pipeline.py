"""Manifest v0 pipeline runner (vector-free).

MoonLadderStudios/MoonMind#4108 retired managed vector indexing:
validate → plan → fetch → transform compiles to an executable plan with no
embedding, upsert, collection lifecycle, overlay handling, or incremental
index-state persistence. There is no successful no-writer index path: new
manifests carrying retired vector blocks are rejected before reader fetch.

Uses the :class:`ReaderAdapter` registry to resolve ``dataSources[].type``
and delegates to per-source adapters for fetch/state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from moonmind.manifest.reader_adapter import get_adapter
from moonmind.schemas.manifest_v0_models import ManifestV0

logger = logging.getLogger(__name__)

RETIRED_VECTOR_FIELDS = ("embeddings", "vectorStore", "indices", "retrievers")


class ManifestRetiredError(RuntimeError):
    """Raised when a retired vector-ingest manifest is submitted."""


def _reject_retired_vector_manifest(manifest: ManifestV0) -> None:
    """Fail fast for retired vector-ingest manifests before fetch/dispatch.

    Historical artifacts remain readable via ``ManifestV0`` (extra="allow")
    without Qdrant; new submissions are rejected here, in the validator, and
    in the queue contract.
    """
    extra = dict(getattr(manifest, "model_extra", None) or {})
    for field_name in RETIRED_VECTOR_FIELDS:
        # Active model no longer declares these fields; historical payloads
        # surface them via model_extra. Defensive getattr covers any legacy
        # constructed object still carrying them as attributes.
        raw = extra.get(field_name, getattr(manifest, field_name, None))
        if raw not in (None, [], {}):
            raise ManifestRetiredError(
                f"manifest block '{field_name}' was retired in "
                "MoonLadderStudios/MoonMind#4108: MoonMind ships no managed "
                "vector indexing, embedding, collection or retrieval "
                "pipeline. Remove this block; new vector-ingest manifests "
                "are not fetched, dispatched, or ingested."
            )


# ---------------------------------------------------------------------------
# Pipeline result types
# ---------------------------------------------------------------------------


@dataclass
class SourceResult:
    """Result from processing a single data source."""

    source_id: str
    source_type: str
    doc_count: int = 0
    skipped: bool = False
    error: Optional[str] = None


@dataclass
class PipelineResult:
    """Aggregated result from a pipeline run (fetch/transform only)."""

    manifest_name: str
    sources: List[SourceResult] = field(default_factory=list)
    total_docs: int = 0
    dry_run: bool = False
    outcome: str = (
        "fetch/transform estimate only; no embedding, indexing, collection "
        "lifecycle, overlay handling, or live ingestion occurred"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest_name,
            "total_docs": self.total_docs,
            "dry_run": self.dry_run,
            "outcome": self.outcome,
            "sources": [
                {
                    "id": s.source_id,
                    "type": s.source_type,
                    "docs": s.doc_count,
                    "skipped": s.skipped,
                    "error": s.error,
                }
                for s in self.sources
            ],
        }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class ManifestPipeline:
    """Execute a vector-free manifest v0 pipeline.

    Usage::

        pipeline = ManifestPipeline(manifest)
        # Dry-run plan (fetch estimate only, no writes)
        plan_result = pipeline.plan()
        # Full execution (fetch only, no indexing/state persistence)
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
        _reject_retired_vector_manifest(self.manifest)
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
        """Full pipeline: fetch sources without indexing or state persistence."""
        _reject_retired_vector_manifest(self.manifest)
        result = PipelineResult(
            manifest_name=self.manifest.metadata.name,
            dry_run=False,
        )

        run_cfg = self.manifest.run
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
                raw_documents = list(adapter.fetch())
                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    doc_count=len(raw_documents),
                )
                result.total_docs += len(raw_documents)
                self.log.info(
                    "Source %s: fetched %d docs (no indexing performed)",
                    ds.id,
                    len(raw_documents),
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
