"""Unit tests for worker startup profile validation helpers."""

from __future__ import annotations

import logging

import pytest

from celery_worker.startup_checks import (
    resolve_embedding_runtime_profile,
    validate_embedding_runtime_profile,
)


def test_resolve_embedding_runtime_profile_prefers_google_key():
    profile = resolve_embedding_runtime_profile(
        default_provider="google",
        default_model="gemini-embedding-001",
        google_api_key="google-key",
        gemini_api_key="gemini-key",
    )

    assert profile.provider == "google"
    assert profile.model == "gemini-embedding-001"
    assert profile.credential_source == "google_api_key"


def test_validate_embedding_runtime_profile_requires_key_for_google(caplog):
    logger = logging.getLogger("worker-startup-test")
    caplog.set_level(logging.CRITICAL)

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY or GEMINI_API_KEY"):
        validate_embedding_runtime_profile(
            worker_name="codex",
            default_provider="google",
            default_model="gemini-embedding-001",
            google_api_key=None,
            gemini_api_key=None,
            logger=logger,
        )

    assert "Google embeddings are configured" in caplog.text


def test_validate_embedding_runtime_profile_allows_non_google_without_key():
    logger = logging.getLogger("worker-startup-test")

    profile = validate_embedding_runtime_profile(
        worker_name="codex",
        default_provider="ollama",
        default_model="nomic-embed-text",
        google_api_key=None,
        gemini_api_key=None,
        logger=logger,
    )

    assert profile.provider == "ollama"
    assert profile.model == "nomic-embed-text"
    assert profile.credential_source is None
