"""Provider-neutral PR resolver semantics shared by all execution hosts."""

from .classify import classify_snapshot
from .evidence import (
    IMPLEMENTATION_CONTRACT,
    RESOLVER_CORE_DIGEST,
    RESOLVER_CORE_VERSION,
    portable_terminal_evidence,
)
from .models import (
    CanonicalPullRequestSnapshot,
    ResolverAction,
    ResolverDecision,
    ResolverEvent,
    ResolverPolicy,
    ResolverState,
    ResolverTransition,
)
from .normalize import normalize_portable_snapshot, normalize_temporal_snapshot
from .transition import reduce_resolver_state

__all__ = [
    "CanonicalPullRequestSnapshot",
    "IMPLEMENTATION_CONTRACT",
    "RESOLVER_CORE_DIGEST",
    "RESOLVER_CORE_VERSION",
    "ResolverAction",
    "ResolverDecision",
    "ResolverEvent",
    "ResolverPolicy",
    "ResolverState",
    "ResolverTransition",
    "classify_snapshot",
    "normalize_portable_snapshot",
    "normalize_temporal_snapshot",
    "portable_terminal_evidence",
    "reduce_resolver_state",
]
