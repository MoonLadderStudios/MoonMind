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
from .review_providers import (
    AUTOMATED_REVIEW_PROVIDERS,
    DEFAULT_AUTOMATED_REVIEW_PROVIDER,
    AutomatedReviewProvider,
    automated_review_provider_or_raise,
    is_automated_review_provider_login,
    normalize_provider_name,
    normalize_reviewer_login,
    resolve_automated_review_provider,
)
from .transition import reduce_resolver_state

__all__ = [
    "AUTOMATED_REVIEW_PROVIDERS",
    "AutomatedReviewProvider",
    "CanonicalPullRequestSnapshot",
    "DEFAULT_AUTOMATED_REVIEW_PROVIDER",
    "IMPLEMENTATION_CONTRACT",
    "RESOLVER_CORE_DIGEST",
    "RESOLVER_CORE_VERSION",
    "ResolverAction",
    "ResolverDecision",
    "ResolverEvent",
    "ResolverPolicy",
    "ResolverState",
    "ResolverTransition",
    "automated_review_provider_or_raise",
    "classify_snapshot",
    "is_automated_review_provider_login",
    "normalize_portable_snapshot",
    "normalize_provider_name",
    "normalize_reviewer_login",
    "normalize_temporal_snapshot",
    "portable_terminal_evidence",
    "reduce_resolver_state",
    "resolve_automated_review_provider",
]
