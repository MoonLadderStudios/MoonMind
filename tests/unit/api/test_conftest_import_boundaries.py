"""Regression coverage for MM-848 API unit-test setup."""

from __future__ import annotations

import sys


def test_api_unit_conftest_preloads_api_db_models_and_temporal_guard() -> None:
    assert "api_service.db.models" in sys.modules
    assert "moonmind.workflows.temporal.client" in sys.modules
