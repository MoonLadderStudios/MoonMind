"""Manifest CLI helpers for ``moonmind manifest`` commands.

These are thin wrappers that the Typer CLI commands in ``moonmind/cli.py``
call. They follow the same pattern as ``moonmind/rag/cli.py``.
"""

from __future__ import annotations

import logging

from moonmind.manifest.validator import (
    ValidationResult,
    validate_manifest_file,
)

logger = logging.getLogger(__name__)

class ManifestCliError(RuntimeError):
    """Raised for manifest CLI usage errors."""

def run_validate(*, manifest_path: str) -> ValidationResult:
    """Validate a manifest YAML file and return the result."""
    result = validate_manifest_file(manifest_path)
    return result

def run_plan(*, manifest_path: str) -> dict:
    """Dry-run: parse manifest and estimate fetch scope without writes.

    Returns a fetch/transform estimate; explicitly not indexing or live
    ingestion (MoonLadderStudios/MoonMind#4108 vector-free).
    """
    result = validate_manifest_file(manifest_path)
    if not result.valid:
        raise ManifestCliError(
            f"Manifest validation failed: {result.summary()}"
        )

    manifest = result.manifest
    assert manifest is not None

    from moonmind.manifest.pipeline import ManifestPipeline

    pipeline = ManifestPipeline(manifest)
    plan_result = pipeline.plan()
    return plan_result.to_dict()

def run_manifest(*, manifest_path: str) -> dict:
    """Full pipeline (vector-free): validate, fetch, transform.

    No embedding, upsert, collection lifecycle, overlay handling, or index
    state persistence occurs (MoonLadderStudios/MoonMind#4108). Retired
    vector-ingest manifests fail validation before fetch.

    Returns a summary dict with fetch execution results.
    """
    result = validate_manifest_file(manifest_path)
    if not result.valid:
        raise ManifestCliError(
            f"Manifest validation failed: {result.summary()}"
        )

    manifest = result.manifest
    assert manifest is not None

    from moonmind.manifest.pipeline import ManifestPipeline

    pipeline = ManifestPipeline(manifest)
    run_result = pipeline.run()
    return run_result.to_dict()

def run_evaluate(
    *,
    manifest_path: str,
    dataset: str | None = None,
) -> dict:
    """Evaluate recorded/injected retrieval results (no live retrieval).

    MoonLadderStudios/MoonMind#4108 retired live-vector evaluation wiring.
    """
    result = validate_manifest_file(manifest_path)
    if not result.valid:
        raise ManifestCliError(
            f"Manifest validation failed: {result.summary()}"
        )

    manifest = result.manifest
    assert manifest is not None

    if manifest.evaluation is None:
        raise ManifestCliError(
            "Manifest has no 'evaluation' block. Add datasets and metrics to evaluate."
        )

    # Import evaluation module
    from moonmind.manifest.evaluation import evaluate_manifest

    try:
        return evaluate_manifest(manifest=manifest, dataset_filter=dataset)
    except ManifestCliError:
        raise
    except RuntimeError as exc:
        # Retired live-retrieval evaluation raises actionably inside the
        # domain layer; surface it as a CLI usage error (exit 1) instead of
        # an unhandled traceback.
        raise ManifestCliError(str(exc)) from exc
