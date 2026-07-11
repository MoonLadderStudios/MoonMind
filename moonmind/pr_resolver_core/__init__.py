"""Provider-neutral PR resolver policy core."""

from .models import ResolverAction, ResolverSnapshot, ResolverState, TerminalResult
from .transition import classify_github_snapshot, normalize_github_snapshot, reduce_resolver_state
from .retry import (
    FINALIZE_ONLY_RETRY_REASONS,
    FULL_REMEDIATION_REASONS,
    classify_retry_action,
    compute_backoff_seconds,
    normalize_terminal_status,
    normalize_text,
)

__all__ = [
    "ResolverAction",
    "ResolverSnapshot",
    "ResolverState",
    "TerminalResult",
    "classify_github_snapshot",
    "normalize_github_snapshot",
    "reduce_resolver_state",
    "FINALIZE_ONLY_RETRY_REASONS",
    "FULL_REMEDIATION_REASONS",
    "classify_retry_action",
    "compute_backoff_seconds",
    "normalize_terminal_status",
    "normalize_text",
]
