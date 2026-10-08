"""Portable terminal evidence and immutable resolver implementation identity."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

IMPLEMENTATION_CONTRACT = "pr-resolver-core/v1"
RESOLVER_CORE_VERSION = "1.0.0"
# SHA-256 over models.py + normalize.py + classify.py + transition.py +
# review_providers.py + github_checks.py in that order. It is deliberately embedded in the
# immutable package so workflow code never reads mutable filesystem state
# during replay.
RESOLVER_CORE_DIGEST = (
    "sha256:af14fdc54c7696e0839fc01b888c3e7b82739a31890d7cfa2778ef9e890d0553"
)


def portable_terminal_evidence(
    *,
    status: str,
    reason_code: str,
    repository: str,
    pr_number: int,
    pr_url: str,
    verified_head_sha: str | None,
    verified_merge_sha: str | None,
    extensions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schemaVersion": 4,
        "implementationContract": IMPLEMENTATION_CONTRACT,
        "resolverCoreVersion": RESOLVER_CORE_VERSION,
        "resolverCoreDigest": RESOLVER_CORE_DIGEST,
        "status": status,
        "reasonCode": reason_code,
        "repository": repository,
        "prNumber": pr_number,
        "prUrl": pr_url,
        "verifiedHeadSha": verified_head_sha,
        "verifiedMergeSha": verified_merge_sha,
    }
    if extensions:
        payload["extensions"] = json.loads(json.dumps(dict(extensions), default=str))
    return payload
