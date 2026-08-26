"""Tests for the in-image exact-artifact runtime capability probe.

Source issue: MoonLadderStudios/MoonMind#3710.

These run in the repo environment, which installs the same runtime packages
the deployed image ships, so the probe exercises real import/introspection
capabilities rather than a mock.
"""

from __future__ import annotations

import importlib

import pytest

from tools import omnigent_exact_artifact_probe as probe


def _by_name(entries: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(entry["name"]): entry for entry in entries}


def test_server_probe_reports_required_import_capabilities() -> None:
    entries = _by_name(probe.probe_capabilities("server"))
    assert entries["uvicorn_websocket_impl"]["ok"] is True
    assert entries["omnigent_adapters_import"]["ok"] is True
    assert entries["temporal_client_init"]["ok"] is True
    assert entries["database_init"]["ok"] is True


def test_worker_probe_reports_required_import_capabilities() -> None:
    entries = _by_name(probe.probe_capabilities("worker"))
    assert entries["omnigent_adapters_import"]["ok"] is True
    assert entries["temporal_client_init"]["ok"] is True


def test_uvicorn_websocket_probe_ok_with_installed_impl() -> None:
    signal = probe.probe_uvicorn_websocket()
    assert signal["name"] == "uvicorn_websocket_impl"
    assert signal["ok"] is True


def test_uvicorn_websocket_probe_fails_without_impl_3697(monkeypatch) -> None:
    """#3697: if no WebSocket implementation is installed, the probe fails."""
    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args, **kwargs):
        if name in {"websockets", "wsproto"}:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(probe.importlib.util, "find_spec", _fake_find_spec)
    signal = probe.probe_uvicorn_websocket()
    assert signal["ok"] is False
    assert "404" in signal["detail"] or "implementation" in signal["detail"]


def test_unknown_role_raises() -> None:
    with pytest.raises(SystemExit):
        probe.probe_capabilities("does-not-exist")
