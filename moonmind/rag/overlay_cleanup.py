"""Cleanup helpers for overlay collections."""

from __future__ import annotations

from moonmind.rag.qdrant_client import RagQdrantClient
from moonmind.rag.settings import RagRuntimeSettings


def clean_overlay_run(*, run_id: str, settings: RagRuntimeSettings, qdrant: RagQdrantClient) -> None:
    collection_name = settings.overlay_collection_name(run_id)
    qdrant.delete_overlay_collection(collection_name)
