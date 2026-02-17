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
    legacy_mirror_root: str | None = None,
    repo_root: str | None = None,
    strict: bool,
    logger: logging.Logger,
) -> Path | None:
    """Validate local shared-skills mirror settings when strict mode is enabled."""

    if not strict:
        return None

    repo_raw = (repo_root or "").strip()
    base_path = Path.cwd().resolve()
    if repo_raw:
        repo_path = Path(repo_raw).expanduser()
        if not repo_path.is_absolute():
            repo_path = (Path.cwd() / repo_path).resolve()
        base_path = repo_path.resolve()

    raw_roots: list[str] = []
    primary_raw = (mirror_root or "").strip()
    legacy_raw = (legacy_mirror_root or "").strip()
    if primary_raw:
        raw_roots.append(primary_raw)
    if legacy_raw and legacy_raw != primary_raw:
        raw_roots.append(legacy_raw)

    if not raw_roots:
        raise RuntimeError("Shared skills mirror root is required in strict mode.")

    resolved_roots: list[Path] = []
    for raw in raw_roots:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (base_path / candidate).resolve()
        resolved_roots.append(candidate.resolve())

    errors: list[str] = []
    for index, mirror_path in enumerate(resolved_roots):
        if not mirror_path.exists():
            errors.append(f"does not exist: {mirror_path}")
            continue
        if not mirror_path.is_dir():
            errors.append(f"is not a directory: {mirror_path}")
            continue

        skill_dirs = [
            child
            for child in mirror_path.iterdir()
            if child.is_dir() and (child / "SKILL.md").is_file()
        ]
        if not skill_dirs:
            errors.append(f"contains no valid skills: {mirror_path}")
            continue

        fallback_used = index > 0
        logger.info(
            "Shared skills mirror validated for %s worker: %s (%d skills)",
            worker_name,
            mirror_path,
            len(skill_dirs),
            extra={
                "worker_name": worker_name,
                "skills_mirror_root": str(mirror_path),
                "skills_mirror_count": len(skill_dirs),
                "skills_mirror_fallback_used": fallback_used,
                "skills_mirror_checked_roots": [str(path) for path in resolved_roots],
            },
        )
        return mirror_path

    if len(resolved_roots) == 1:
        message = errors[0] if errors else f"invalid mirror root: {resolved_roots[0]}"
        raise RuntimeError(f"Shared skills mirror root {message}")

    checked = ", ".join(str(path) for path in resolved_roots)
    details = "; ".join(errors) if errors else "no valid mirror roots found"
    raise RuntimeError(
        f"Shared skills mirror validation failed for roots [{checked}]: {details}"
    )
