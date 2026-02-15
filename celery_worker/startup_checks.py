"""Shared startup checks for Celery worker entrypoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingRuntimeProfile:
    """Resolved embedding configuration for startup diagnostics."""

    provider: str
    model: str
    credential_source: str | None


def resolve_embedding_runtime_profile(
    *,
    default_provider: str | None,
    default_model: str | None,
    google_api_key: str | None,
    gemini_api_key: str | None,
) -> EmbeddingRuntimeProfile:
    """Normalize embedding provider/model and key source for startup checks."""

    provider = (default_provider or "").strip().lower() or "unknown"
    model = (default_model or "").strip() or "unknown"
    credential_source: str | None = None
    if google_api_key:
        credential_source = "google_api_key"
    elif gemini_api_key:
        credential_source = "gemini_api_key"
    return EmbeddingRuntimeProfile(
        provider=provider,
        model=model,
        credential_source=credential_source,
    )


def validate_embedding_runtime_profile(
    *,
    worker_name: str,
    default_provider: str | None,
    default_model: str | None,
    google_api_key: str | None,
    gemini_api_key: str | None,
    logger: logging.Logger,
) -> EmbeddingRuntimeProfile:
    """Validate embedding prerequisites and emit startup diagnostics."""

    profile = resolve_embedding_runtime_profile(
        default_provider=default_provider,
        default_model=default_model,
        google_api_key=google_api_key,
        gemini_api_key=gemini_api_key,
    )

    if profile.provider == "google" and profile.credential_source is None:
        logger.critical(
            "Google embeddings are configured but no API credential is available.",
            extra={
                "worker_name": worker_name,
                "embedding_provider": profile.provider,
                "embedding_model": profile.model,
                "credential_source": profile.credential_source,
            },
        )
        raise RuntimeError(
            "Google embeddings require GOOGLE_API_KEY or GEMINI_API_KEY."
        )

    logger.info(
        "Embedding runtime profile resolved for %s worker: provider=%s model=%s",
        worker_name,
        profile.provider,
        profile.model,
        extra={
            "worker_name": worker_name,
            "embedding_provider": profile.provider,
            "embedding_model": profile.model,
            "credential_source": profile.credential_source,
        },
    )

    return profile
