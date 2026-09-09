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
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from moonmind.manifest.reader_adapter import get_adapter
from moonmind.schemas.manifest_v0_models import (
    ManifestV0,
    RETIRED_VECTOR_FIELDS,
    is_retired_vector_key,
)

logger = logging.getLogger(__name__)


class ManifestRetiredError(RuntimeError):
    """Raised when a retired vector-ingest manifest is submitted."""


def _reject_retired_vector_manifest(manifest: ManifestV0) -> None:
    """Fail fast for retired vector-ingest manifests before fetch/dispatch.

    Historical artifacts remain readable via ``ManifestV0`` (extra="allow")
    without Qdrant; new submissions are rejected here, in the validator, and
    in the queue contract.
    """
    extra = dict(getattr(manifest, "model_extra", None) or {})
    for key, raw in extra.items():
        # Normalized match: 'vectorstore', 'VectorStore' and padded
        # variants reject exactly like the canonical spellings.
        if is_retired_vector_key(key) and raw not in (None, [], {}):
            raise ManifestRetiredError(
                f"manifest block '{key}' was retired in "
                "MoonLadderStudios/MoonMind#4108: MoonMind ships no managed "
                "vector indexing, embedding, collection or retrieval "
                "pipeline. Remove this block; new vector-ingest manifests "
                "are not fetched, dispatched, or ingested."
            )
    for field_name in RETIRED_VECTOR_FIELDS:
        # Active model no longer declares these fields; historical payloads
        # surface them via model_extra. Defensive getattr covers any legacy
        # constructed object still carrying them as attributes.
        raw = getattr(manifest, field_name, None)
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
    transform_applied: bool = False
    output_chunks: int = 0


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
                    "transform_applied": s.transform_applied,
                    "output_chunks": s.output_chunks,
                }
                for s in self.sources
            ],
        }


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Best-effort HTML-to-text conversion for the retained transform contract."""
    return _HTML_TAG_RE.sub("", text)


def _chunk_text(text: str, *, chunk_size: int, chunk_overlap: int) -> List[str]:
    """Split text into character windows (pre-#4108 splitter semantics).

    ``chunk_size``/``chunk_overlap`` come from the retained
    ``transforms.splitter`` contract; overlap is clamped below the window so
    the cursor always advances.
    """
    size = max(1, int(chunk_size))
    overlap = max(0, min(int(chunk_overlap), size - 1))
    if not text:
        return []
    chunks: List[str] = []
    cursor = 0
    end = len(text)
    while cursor < end:
        chunk_end = min(end, cursor + size)
        chunks.append(text[cursor:chunk_end])
        if chunk_end >= end:
            break
        cursor = max(chunk_end - overlap, cursor + 1)
    return chunks


def apply_declared_transforms(
    raw_documents: Sequence[Tuple[str, Mapping[str, Any]]],
    transforms: Any,
) -> Tuple[List[Tuple[str, Dict[str, Any]]], bool, int]:
    """Apply the retained ``transforms`` contract to fetched documents.

    Executes ``htmlToText``, ``splitter`` chunking, and ``enrichMetadata``
    in memory (no embedding, indexing, or state persistence). Returns the
    transformed ``(text, metadata)`` pairs, whether any transform was
    declared, and the resulting output-chunk count.
    """
    html_to_text = bool(getattr(transforms, "htmlToText", False))
    splitter = getattr(transforms, "splitter", None)
    enrich = list(getattr(transforms, "enrichMetadata", None) or [])
    declared = bool(html_to_text or splitter is not None or len(enrich) > 0)
    if not declared:
        return [(text, dict(meta)) for text, meta in raw_documents], False, 0

    chunk_size = getattr(splitter, "chunkSize", 1000) if splitter else 1000
    chunk_overlap = getattr(splitter, "chunkOverlap", 100) if splitter else 100
    transformed: List[Tuple[str, Dict[str, Any]]] = []
    for text, meta in raw_documents:
        working = _strip_html(text) if html_to_text else text
        metadata = dict(meta)
        for entry in enrich:
            if isinstance(entry, Mapping):
                metadata.update(entry)
        if splitter is not None:
            for chunk in _chunk_text(
                working, chunk_size=chunk_size, chunk_overlap=chunk_overlap
            ):
                transformed.append((chunk, dict(metadata)))
        else:
            transformed.append((working, metadata))
    return transformed, True, len(transformed)


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
        """Full pipeline: fetch sources, apply transforms, no indexing/state."""
        _reject_retired_vector_manifest(self.manifest)
        result = PipelineResult(
            manifest_name=self.manifest.metadata.name,
            dry_run=False,
        )

        run_cfg = self.manifest.run
        error_policy = run_cfg.errorPolicy if run_cfg else "continue"
        transforms = self.manifest.transforms
        transformed_sources = 0
        transformed_chunks = 0

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
                # Declared transforms execute in memory (no downstream
                # writer persists them in the vector-free pipeline); the
                # applied/chunk evidence below is the terminal record.
                _, applied, chunk_count = apply_declared_transforms(
                    raw_documents, transforms
                )
                src_result = SourceResult(
                    source_id=ds.id,
                    source_type=ds.type,
                    doc_count=len(raw_documents),
                    transform_applied=applied,
                    output_chunks=chunk_count,
                )
                result.total_docs += len(raw_documents)
                if applied:
                    transformed_sources += 1
                    transformed_chunks += chunk_count
                self.log.info(
                    "Source %s: fetched %d docs, transform_applied=%s "
                    "output_chunks=%d (no indexing performed)",
                    ds.id,
                    len(raw_documents),
                    applied,
                    chunk_count,
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

        if transformed_sources:
            result.outcome = (
                f"{result.outcome}; transforms applied to "
                f"{transformed_sources} source(s), {transformed_chunks} "
                "output chunk(s)"
            )
        return result
