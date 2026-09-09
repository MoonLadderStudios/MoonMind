"""Retired vector guardrail boundary (MoonLadderStudios/MoonMind#4112).

The native Qdrant vector backend is retired. This module intentionally
performs no vector network access: no Qdrant client construction, no
collection readiness probe, and no retrieval-gateway health check. The
``ensure_rag_ready`` entry point is retained as a successful no-op so
callers migrated by sibling execution children keep a stable import while
the retired component is neither reported as unhealthy nor as verified
healthy.
"""

from __future__ import annotations

from moonmind.rag.settings import RagRuntimeSettings


class GuardrailError(RuntimeError):
    """Raised when a required guardrail fails."""


def ensure_rag_ready(settings: RagRuntimeSettings) -> None:
    """No-op vector retirement boundary.

    Never raises for a deliberately removed vector backend and never
    touches the network. Non-vector prerequisite validation lives with
    the owning callers (worker preflight, deployment diagnostics).
    """

    _ = settings
    return None
