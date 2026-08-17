"""Omnigent domain layer (MoonLadderStudios/MoonMind#3711).

Pure policy and state for the Omnigent control plane. Modules in this package
are the single canonical source of truth for Omnigent status vocabulary,
provider-native compatibility translation, failure classification, session and
turn state, and lifecycle transitions.

Dependency rule (enforced by ``tools/check_omnigent_architecture.py``): the
domain layer may depend only on the Python standard library and small pure
schema utilities. It must not import FastAPI/Starlette, SQLAlchemy, the Temporal
SDK, HTTP clients, Docker or subprocess launchers, artifact services,
OpenTelemetry exporters, or application settings/environment variables.

This is Phase 1/2 of the incremental decomposition described in the issue:
legacy modules (``bridge_store``, ``bridge_events``, ``failure_classification``)
delegate to these canonical definitions instead of maintaining component-local
copies, eliminating the duplicated provider vocabulary and transition tables the
issue calls out. Existing behavior is preserved.
"""

from moonmind.omnigent.domain.compatibility import (
    PROVIDER_STATUS_ALIASES,
    canonicalize_provider_status,
)
from moonmind.omnigent.domain.failures import (
    OMNIGENT_FAILURE_CLASS_TABLE,
    OmnigentFailureReason,
    classify_omnigent_failure,
    classify_omnigent_http_status,
    failure_class_for_terminal_status,
)
from moonmind.omnigent.domain.observations import (
    RECOGNIZED_EXACT_EVENT_TYPES,
    normalized_status_for_event_type,
)
from moonmind.omnigent.domain.session_state import (
    LIFECYCLE_STATUSES,
    NON_TERMINAL_NORMALIZED_STATUSES,
    TERMINAL_STATUSES,
    SessionStatus,
    coalesce_session_status,
    is_terminal_status,
)
from moonmind.omnigent.domain.turn_state import TurnStatus, is_terminal_turn_status

__all__ = [
    "LIFECYCLE_STATUSES",
    "NON_TERMINAL_NORMALIZED_STATUSES",
    "OMNIGENT_FAILURE_CLASS_TABLE",
    "OmnigentFailureReason",
    "PROVIDER_STATUS_ALIASES",
    "RECOGNIZED_EXACT_EVENT_TYPES",
    "SessionStatus",
    "TERMINAL_STATUSES",
    "TurnStatus",
    "canonicalize_provider_status",
    "classify_omnigent_failure",
    "classify_omnigent_http_status",
    "coalesce_session_status",
    "failure_class_for_terminal_status",
    "is_terminal_status",
    "is_terminal_turn_status",
    "normalized_status_for_event_type",
]
