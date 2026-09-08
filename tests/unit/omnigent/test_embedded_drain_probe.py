"""Live durable-count wiring for the retired embedded host transport.

MoonLadderStudios/MoonMind#3955 (remediation): Stage 1 disabled new admission
while in-flight sessions drain. The side-effect-free decoder/summary in
:mod:`moonmind.omnigent.embedded_drain` is only half of the disposition story;
this suite pins the other half — the production-boundary wiring that feeds
durable counts from the owning store readers
(``active_host_protocol_modes`` for sessions,
``list_embedded_host_readiness`` for host leases) into
:func:`summarize_embedded_drain` via :func:`probe_embedded_drain`, and the
composition entrypoint that owns that wiring for routes, workflows, and the
janitor.

A store failure propagates instead of implying drain: missing evidence is a
blocker, never an implicit drain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    HOST_PROTOCOL_MODE_PROXY,
)
from moonmind.omnigent.embedded_drain import (
    EmbeddedDrainError,
    probe_embedded_drain,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DRAIN_MODULE = REPO_ROOT / "moonmind/omnigent/embedded_drain.py"
COMPOSITION_MODULE = (
    REPO_ROOT / "api_service/api/routers/omnigent_bridge_composition.py"
)


class _FakeBridgeSessionStore:
    """Duck-typed stand-in for the two canonical durable readers."""

    def __init__(
        self,
        modes: object,
        leases: list[dict[str, object]] | BaseException,
    ) -> None:
        self._modes = modes
        self._leases = leases

    async def active_host_protocol_modes(self) -> object:
        if isinstance(self._modes, BaseException):
            raise self._modes
        return dict(self._modes)  # type: ignore[arg-type]

    async def list_embedded_host_readiness(self) -> list[dict[str, object]]:
        if isinstance(self._leases, BaseException):
            raise self._leases
        return list(self._leases)


@pytest.mark.asyncio
async def test_probe_counts_embedded_sessions_and_leases() -> None:
    disposition = await probe_embedded_drain(
        _FakeBridgeSessionStore(
            {HOST_PROTOCOL_MODE_EMBEDDED: 2, HOST_PROTOCOL_MODE_PROXY: 5},
            [{"id": "lease-a"}],
        )
    )

    assert disposition["drained"] is False
    assert disposition["activeEmbeddedSessions"] == 2
    assert disposition["activeEmbeddedLeases"] == 1
    assert disposition["blockers"] == (
        "active_embedded_sessions:2",
        "active_embedded_leases:1",
    )


@pytest.mark.asyncio
async def test_probe_reports_drained_only_when_counts_are_zero() -> None:
    disposition = await probe_embedded_drain(
        _FakeBridgeSessionStore({HOST_PROTOCOL_MODE_PROXY: 3, "unknown": 1}, [])
    )

    assert disposition["drained"] is True
    assert disposition["blockers"] == ()
    assert disposition["activeEmbeddedSessions"] == 0
    assert disposition["activeEmbeddedLeases"] == 0


@pytest.mark.asyncio
async def test_probe_rejects_non_mapping_modes() -> None:
    with pytest.raises(EmbeddedDrainError):
        await probe_embedded_drain(_FakeBridgeSessionStore("not-a-mapping", []))


@pytest.mark.asyncio
async def test_probe_failure_propagates_and_never_implies_drain() -> None:
    with pytest.raises(RuntimeError, match="durable store is unavailable"):
        await probe_embedded_drain(
            _FakeBridgeSessionStore(
                RuntimeError("durable store is unavailable"),
                [],
            )
        )
    with pytest.raises(RuntimeError, match="lease read failed"):
        await probe_embedded_drain(
            _FakeBridgeSessionStore(
                {HOST_PROTOCOL_MODE_PROXY: 1},
                RuntimeError("lease read failed"),
            )
        )


def test_composition_owns_the_live_drain_probe() -> None:
    """The composition boundary must wire the store into the drain summary."""

    tree = ast.parse(COMPOSITION_MODULE.read_text(encoding="utf-8"))
    probe_fn = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "probe_embedded_transport_drain"
        ),
        None,
    )
    assert probe_fn is not None, (
        "composition must expose probe_embedded_transport_drain so a route, "
        "workflow, or janitor can report live drain disposition"
    )
    referenced = {node.id for node in ast.walk(probe_fn) if isinstance(node, ast.Name)}
    assert "probe_embedded_drain" in referenced
    assert "build_bridge_session_store" in referenced


def test_drain_probe_adds_no_launch_store_or_db_imports() -> None:
    tree = ast.parse(DRAIN_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
    for forbidden in (
        "moonmind.omnigent.bridge_embedded",
        "moonmind.omnigent.embedded_host_channel",
        "moonmind.omnigent.embedded_evidence",
        "moonmind.omnigent.bridge_store",
        "api_service.db.models",
        "sqlalchemy",
    ):
        assert forbidden not in imported, forbidden
