"""Pure identity helpers for the merge-automation automated review loop.

These helpers are deliberately free of Temporal, HTTP, and persistence imports
so the deterministic workflow, the integration activity, and the durable ledger
all derive the same request identity from the same code.
"""

from __future__ import annotations

from hashlib import sha256

REVIEW_REQUEST_POSTED_STATUSES = frozenset({"requested", "reconciled", "recorded"})
REVIEW_REQUEST_RETRY_GATE_STATUSES = frozenset(
    {"stale_head", "pull_request_closed"}
)


def build_review_request_key(
    *,
    parent_workflow_id: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    provider: str,
) -> str:
    """Return the deterministic identity of one automated review request.

    One key per (owning workflow, repository, pull request, head SHA, provider)
    is what makes "exactly one request per head SHA" enforceable across activity
    retries, workflow replay, and lost GitHub responses.
    """

    identity = "|".join(
        [
            str(parent_workflow_id or "").strip(),
            str(repository or "").strip(),
            str(pr_number or 0),
            str(head_sha or "").strip().lower(),
            str(provider or "").strip().lower(),
        ]
    )
    return sha256(identity.encode("utf-8")).hexdigest()


__all__ = [
    "REVIEW_REQUEST_POSTED_STATUSES",
    "REVIEW_REQUEST_RETRY_GATE_STATUSES",
    "build_review_request_key",
]
