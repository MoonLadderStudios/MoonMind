"""Manifest CLI helpers for ``moonmind manifest`` commands.

These are thin wrappers that the Typer CLI commands in ``moonmind/cli.py``
call. They follow the same pattern as ``moonmind/rag/cli.py``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

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
    """Dry-run: parse manifest and estimate scope without writes.

    Returns a summary dict with estimated doc/chunk counts.
    """
    result = validate_manifest_file(manifest_path)
    if not result.valid:
        raise ManifestCliError(
            f"Manifest validation failed: {result.summary()}"
        )

    manifest = result.manifest
    assert manifest is not None

    # Estimate scope
    plan_summary = {
        "manifest": manifest.metadata.name,
        "dataSources": len(manifest.dataSources),
        "indices": len(manifest.indices),
        "retrievers": len(manifest.retrievers),
        "dryRun": True,
        "sources": [],
    }

    for ds in manifest.dataSources:
        plan_summary["sources"].append(  # type: ignore[union-attr]
            {
                "id": ds.id,
                "type": ds.type,
                "status": "planned",
            }
        )

    return plan_summary


def run_manifest(*, manifest_path: str) -> dict:
    """Full pipeline: validate, fetch, transform, embed, upsert.

    Returns a summary dict with execution results.
    """
    result = validate_manifest_file(manifest_path)
    if not result.valid:
        raise ManifestCliError(
            f"Manifest validation failed: {result.summary()}"
        )

    manifest = result.manifest
    assert manifest is not None

    # Placeholder: actual pipeline execution will be wired in Phase 4
    return {
        "manifest": manifest.metadata.name,
        "status": "not_implemented",
        "message": "Full pipeline execution requires Phase 4 implementation (ReaderAdapter wiring).",
    }


def run_evaluate(
    *,
    manifest_path: str,
    dataset: Optional[str] = None,
) -> dict:
    """Evaluate retrieval quality against a golden dataset.

    Returns a summary dict with metric scores.
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

    return evaluate_manifest(manifest=manifest, dataset_filter=dataset)
