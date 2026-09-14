"""Temporal unit-test setup."""

from __future__ import annotations

import pytest

from tests.support.temporal_guards import install_temporal_client_adapter_guard


@pytest.fixture(autouse=True)
def prevent_live_temporal_lifecycle_calls(monkeypatch):
    """Keep Temporal unit tests from starting/signaling/canceling real workflows."""

    install_temporal_client_adapter_guard(monkeypatch)


@pytest.fixture(autouse=True)
def default_noop_deprecate_patch(monkeypatch):
    """Default deprecated patch markers to a no-op outside workflow runs.

    Production workflow code calls the real ``workflow.deprecate_patch``,
    which raises outside a workflow event loop just like ``workflow.patched``
    does. Tests that drive workflow methods directly already mock ``patched``
    per test; this default covers ``deprecate_patch`` the same way so a
    retirement (patched -> deprecate_patch) does not require touching every
    caller test. Tests asserting specific deprecation calls override this
    with their own mock.
    """

    import temporalio.workflow as temporal_workflow

    monkeypatch.setattr(
        temporal_workflow, "deprecate_patch", lambda _patch_id: None
    )
