"""Provider-neutral PR resolver policy core."""

from .models import ResolverAction, ResolverSnapshot, ResolverState, TerminalResult
from .transition import classify_github_snapshot, normalize_github_snapshot, reduce_resolver_state

__all__ = [
    "ResolverAction",
    "ResolverSnapshot",
    "ResolverState",
    "TerminalResult",
    "classify_github_snapshot",
    "normalize_github_snapshot",
    "reduce_resolver_state",
]
