"""Unit tests for orchestrator artifact storage path resolution."""

from __future__ import annotations

import pytest

from moonmind.workflows.orchestrator.storage import (
    ArtifactPathError,
    resolve_artifact_root,
)


def test_resolve_artifact_root_uses_base_when_override_is_base_path(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    assert (
        resolve_artifact_root("artifacts", "artifacts")
        == (tmp_path / "artifacts").resolve()
    )


def test_resolve_artifact_root_rejects_outside_relative_override(tmp_path):
    base = tmp_path / "artifacts"
    with pytest.raises(ArtifactPathError):
        resolve_artifact_root(str(base), "../outside")


def test_resolve_artifact_root_accepts_relative_nested_override(tmp_path):
    base = tmp_path / "artifacts"
    assert resolve_artifact_root(str(base), "nested") == (base / "nested").resolve()
