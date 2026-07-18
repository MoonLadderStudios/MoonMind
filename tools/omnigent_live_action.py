#!/usr/bin/env python3
"""Portable HTTP boundary for the credentialed Omnigent live harness.

The provisioned environment owns credentials and exposes a test-only action
endpoint.  This client performs the action and requires durable evidence refs;
it never converts operator attestations into passing evidence.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: omnigent_live_action.py SCENARIO ACTION INPUTS_JSON", file=sys.stderr)
        return 2
    base = os.environ.get("MOONMIND_OMNIGENT_HARNESS_URL", "").rstrip("/")
    token = os.environ.get("MOONMIND_OMNIGENT_HARNESS_TOKEN", "")
    if not base or not token:
        print("MOONMIND_OMNIGENT_HARNESS_URL and MOONMIND_OMNIGENT_HARNESS_TOKEN are required", file=sys.stderr)
        return 2
    scenario, action, raw = sys.argv[1:]
    inputs = json.loads(raw)
    request = urllib.request.Request(
        f"{base}/v1/omnigent-conformance/{scenario}/{action}",
        data=json.dumps({"inputs": inputs}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"live harness action failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        print("live harness did not report successful observed action", file=sys.stderr)
        return 1
    refs = payload.get("evidenceRefs")
    if not isinstance(refs, list) or not refs:
        print("live harness returned no durable evidence refs", file=sys.stderr)
        return 1
    # The action service is repository-owned and its evidence endpoint is part
    # of the contract.  Opaque artifact identifiers cannot be independently
    # inspected by the live runner and are therefore not accepted here.
    if not all(
        isinstance(ref, str)
        and urllib.parse.urlparse(ref).scheme in {"http", "https", "file"}
        for ref in refs
    ):
        print("live harness returned an unsupported evidence reference", file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
