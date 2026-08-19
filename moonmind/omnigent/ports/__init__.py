"""Narrow Omnigent control-plane repository and side-effect ports.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

This package defines one narrow protocol per canonical aggregate -- sessions,
turn attempts, observations, commands, and reconciliation decisions -- instead
of a single all-purpose store interface with dozens of unrelated methods. Each
protocol captures exactly the cohesive surface of its aggregate so application
code and tests can depend on the *interface* rather than the concrete
SQLAlchemy repository.

Both the production SQLAlchemy repositories in
:mod:`moonmind.omnigent.control_plane.repositories` and the in-memory adapters
in :mod:`moonmind.omnigent.adapters.persistence.memory` structurally satisfy
these protocols and are exercised by the shared port-contract suite in
``tests/helpers/omnigent_port_contracts.py``.

Ports speak in the pure canonical record types
(:mod:`moonmind.omnigent.control_plane.records`); they must not depend on
FastAPI, HTTP clients, Docker, or provider implementations. Allowed dependency
directions are documented in ``docs/Omnigent/OmnigentModuleArchitecture.md`` and
enforced by
``tools/check_omnigent_architecture.py``.
"""

from __future__ import annotations

from .commands import CommandRepositoryPort
from .decisions import DecisionRepositoryPort
from .observations import ObservationRepositoryPort
from .sessions import SessionRepositoryPort
from .turns import TurnRepositoryPort

__all__ = [
    "CommandRepositoryPort",
    "DecisionRepositoryPort",
    "ObservationRepositoryPort",
    "SessionRepositoryPort",
    "TurnRepositoryPort",
]
