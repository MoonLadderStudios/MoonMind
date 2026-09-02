"""API unit-test setup."""

from __future__ import annotations

from importlib import import_module

import pytest

from tests.support.network_guards import (
    install_dns_guard,
    install_settings_backed_artifact_store_guard,
)
from tests.support.temporal_guards import install_temporal_client_adapter_guard


import_module("api_service.db.models")


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep API unit tests from starting/signaling/canceling real workflows."""

    install_temporal_client_adapter_guard(monkeypatch)


@pytest.fixture(autouse=True)
def prevent_network_waits(monkeypatch):
    """Fail Compose-only hostname lookups immediately instead of after backoff.

    Un-overridden request dependencies (the settings-backed S3 artifact store
    and the API Postgres engine) otherwise spend 8 to 30 seconds per request
    failing to reach hosts that only exist in Docker Compose.
    """

    install_settings_backed_artifact_store_guard(monkeypatch)
    install_dns_guard(monkeypatch)
