"""Unit tests for worker startup profile validation helpers."""

from __future__ import annotations

import logging

import pytest

from celery_worker.startup_checks import (
    resolve_embedding_runtime_profile,
    validate_embedding_runtime_profile,
    validate_shared_skills_mirror,
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


def test_validate_shared_skills_mirror_strict_requires_existing_skill_root(
    caplog, tmp_path
):
    logger = logging.getLogger("worker-startup-test")
    caplog.set_level(logging.INFO)

    mirror_root = tmp_path / "skills"
    skill = mirror_root / "speckit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: speckit\ndescription: test\n---\n",
        encoding="utf-8",
    )

    resolved = validate_shared_skills_mirror(
        worker_name="codex",
        mirror_root=str(mirror_root),
        strict=True,
        logger=logger,
    )

    assert resolved == mirror_root
    assert "Shared skills mirror validated" in caplog.text


def test_validate_shared_skills_mirror_strict_fails_for_missing_root():
    logger = logging.getLogger("worker-startup-test")

    with pytest.raises(RuntimeError, match="does not exist"):
        validate_shared_skills_mirror(
            worker_name="gemini",
            mirror_root="/tmp/does-not-exist-for-test",
            strict=True,
            logger=logger,
        )


def test_validate_shared_skills_mirror_non_strict_skips_checks():
    logger = logging.getLogger("worker-startup-test")

    resolved = validate_shared_skills_mirror(
        worker_name="gemini",
        mirror_root=None,
        strict=False,
        logger=logger,
    )

    assert resolved is None


def test_validate_shared_skills_mirror_strict_resolves_relative_to_repo_root(
    tmp_path, monkeypatch
):
    logger = logging.getLogger("worker-startup-test")

    repo_root = tmp_path / "repo"
    mirror_root = repo_root / ".agents" / "skills" / "skills"
    skill = mirror_root / "speckit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: speckit\ndescription: test\n---\n",
        encoding="utf-8",
    )

    unrelated_cwd = tmp_path / "unrelated"
    unrelated_cwd.mkdir(parents=True)
    monkeypatch.chdir(unrelated_cwd)

    resolved = validate_shared_skills_mirror(
        worker_name="codex",
        mirror_root=".agents/skills/skills",
        repo_root=str(repo_root),
        strict=True,
        logger=logger,
    )

    assert resolved == mirror_root.resolve()
