"""Pure Omnigent domain vocabulary and policy.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

This package holds side-effect-free canonical domain vocabulary and policy for
the Omnigent control plane. Code here may depend only on the Python standard
library and small pure schema utilities; it must never import FastAPI or
Starlette, SQLAlchemy, the Temporal SDK, HTTP clients, Docker or subprocess
launchers, artifact services, OpenTelemetry exporters, or application settings /
environment variables.

The canonical lifecycle reducer, transition table, and reconciliation contracts
already live in the pure :mod:`moonmind.omnigent.reconciler` package
(MoonLadderStudios/MoonMind#3702); this package is its companion for the rest of
the pure domain vocabulary that is being consolidated out of the large,
infrastructure-coupled bridge modules. The allowed dependency directions and
layer roles are documented in ``docs/Omnigent/Architecture.md`` and enforced by
``tools/check_omnigent_architecture.py``.
"""

from __future__ import annotations

from .failures import (
    OMNIGENT_FAILURE_CLASS_TABLE,
    OmnigentFailureReason,
    classify_omnigent_failure,
    classify_omnigent_http_status,
    failure_class_for_terminal_status,
)

__all__ = [
    "OMNIGENT_FAILURE_CLASS_TABLE",
    "OmnigentFailureReason",
    "classify_omnigent_failure",
    "classify_omnigent_http_status",
    "failure_class_for_terminal_status",
]
