"""Temporal unit-test setup."""

from __future__ import annotations

import pytest

from tests.support.temporal_guards import install_temporal_client_adapter_guard


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep Temporal unit tests from starting/signaling/canceling real workflows."""

    install_temporal_client_adapter_guard(monkeypatch)
