"""Pure Omnigent application-layer use cases.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

This package holds the application-layer coordinators that drive control-plane
use cases through explicit ports and the pure domain reducer, rather than
against concrete SQLAlchemy, HTTP, Docker, or FastAPI implementations. Code here
may depend on the pure ``domain``/``reconciler`` vocabulary and the narrow
``ports`` protocols only; it must never import a web framework, SQLAlchemy, the
Temporal SDK, HTTP/Docker/subprocess launchers, provider clients, or application
settings/environment. The concrete adapter -- the in-memory reference adapter or
the production SQLAlchemy repository -- is injected by the caller/composition
layer, never chosen here.

The allowed dependency directions (``adapters -> application -> ports ->
domain``) and layer roles are documented in
``docs/Omnigent/OmnigentModuleArchitecture.md`` and enforced by
``tools/check_omnigent_architecture.py``.
"""

from __future__ import annotations

from .errors import (
    MaxTurnAttemptsExceededError,
    OmnigentApplicationError,
    SessionNotFoundError,
    SessionTerminalError,
)
from .reconcile_session import ReconcileSessionResult, ReconcileSessionUseCase
from .turn_admission import OpenTurnAttemptUseCase

__all__ = [
    "MaxTurnAttemptsExceededError",
    "OmnigentApplicationError",
    "OpenTurnAttemptUseCase",
    "ReconcileSessionResult",
    "ReconcileSessionUseCase",
    "SessionNotFoundError",
    "SessionTerminalError",
]
