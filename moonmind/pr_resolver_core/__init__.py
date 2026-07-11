"""Provider-neutral PR resolver policy core."""

from .models import ResolverAction, ResolverSnapshot, ResolverState, TerminalResult
from .transition import reduce_resolver_state

__all__ = [
    "ResolverAction",
    "ResolverSnapshot",
    "ResolverState",
    "TerminalResult",
    "reduce_resolver_state",
]
