"""Portable terminal evidence and immutable resolver implementation identity."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

IMPLEMENTATION_CONTRACT = "pr-resolver-core/v1"
RESOLVER_CORE_VERSION = "1.0.0"
# SHA-256 over models.py + normalize.py + classify.py + transition.py in that
# order. It is deliberately embedded in the immutable package so workflow code
# never reads mutable filesystem state during replay.
RESOLVER_CORE_DIGEST = (
    "sha256:2329192ed90d20943e2b2c7fb1181e22d4a3c4fd9df3b2560d9e2158f74b822f"
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
