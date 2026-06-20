"""API unit-test setup."""

from __future__ import annotations

from importlib import import_module

import pytest

from tests.support.temporal_guards import install_temporal_client_adapter_guard


import_module("api_service.db.models")


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep API unit tests from starting/signaling/canceling real workflows."""

    install_temporal_client_adapter_guard(monkeypatch)
