"""api_service unit-test setup."""

from __future__ import annotations

import pytest

import api_service.db.models  # noqa: F401
from tests.support.temporal_guards import install_temporal_client_adapter_guard


@pytest.fixture(autouse=True)
def api_service_unit_settings(monkeypatch):
    """Apply api_service-only settings defaults without touching unrelated tests."""

    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.workflow, "test_mode", True)
    monkeypatch.setattr(settings.workflow, "enable_proposals", False)


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep api_service unit tests from starting/signaling/canceling real workflows."""

    install_temporal_client_adapter_guard(monkeypatch)
