"""Unit tests for ManifestRunRequest validation behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api_service.api.schemas import ManifestRunRequest


def test_manifest_run_request_defaults_action_to_run() -> None:
    """Missing action should default to run."""

    payload = ManifestRunRequest()
    assert payload.action == "run"


def test_manifest_run_request_normalizes_action_case_and_whitespace() -> None:
    """Valid actions should be normalized to lowercase without surrounding whitespace."""

    payload = ManifestRunRequest(action=" PLAN ")
    assert payload.action == "plan"


def test_manifest_run_request_rejects_unsupported_action() -> None:
    """Unsupported actions should fail schema validation."""

    with pytest.raises(ValidationError, match="action must be one of: plan, run"):
        ManifestRunRequest(action="evaluate")


def test_manifest_run_request_rejects_non_string_action() -> None:
    """Non-string action payloads should fail schema validation."""

    with pytest.raises(
        ValidationError,
        match="action must be a string and one of: plan, run",
    ):
        ManifestRunRequest(action=123)


def test_manifest_run_request_defaults_blank_action_to_run() -> None:
    """Blank or null action values should default to run."""

    assert ManifestRunRequest(action=None).action == "run"
    assert ManifestRunRequest(action="").action == "run"
    assert ManifestRunRequest(action="   ").action == "run"
