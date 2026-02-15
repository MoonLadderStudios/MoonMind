"""Shared startup checks for Celery worker entrypoints."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


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


def validate_shared_skills_mirror(
    *,
    worker_name: str,
    mirror_root: str | None,
    strict: bool,
    logger: logging.Logger,
) -> Path | None:
    """Validate local shared-skills mirror settings when strict mode is enabled."""

    if not strict:
        return None

    raw = (mirror_root or "").strip()
    if not raw:
        raise RuntimeError("Shared skills mirror root is required in strict mode.")

    mirror_path = Path(raw).expanduser()
    if not mirror_path.is_absolute():
        mirror_path = (Path.cwd() / mirror_path).resolve()

    if not mirror_path.exists():
        raise RuntimeError(f"Shared skills mirror root does not exist: {mirror_path}")
    if not mirror_path.is_dir():
        raise RuntimeError(
            f"Shared skills mirror root is not a directory: {mirror_path}"
        )

    skill_dirs = [
        child
        for child in mirror_path.iterdir()
        if child.is_dir() and (child / "SKILL.md").is_file()
    ]
    if not skill_dirs:
        raise RuntimeError(
            f"Shared skills mirror root contains no valid skills: {mirror_path}"
        )

    logger.info(
        "Shared skills mirror validated for %s worker: %s (%d skills)",
        worker_name,
        mirror_path,
        len(skill_dirs),
        extra={
            "worker_name": worker_name,
            "skills_mirror_root": str(mirror_path),
            "skills_mirror_count": len(skill_dirs),
        },
    )
    return mirror_path
