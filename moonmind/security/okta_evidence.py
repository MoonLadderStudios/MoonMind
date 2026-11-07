"""GET-only Okta evidence, checkpointed by the existing assessment artifact owner."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx

from moonmind.security import identity_lifecycle
from moonmind.security.assessments import content_key
from moonmind.security.egress_policy import (
    EGRESS_POLICIES_ENV_KEY,
    parse_egress_policies,
    tool_proxy_options,
)
from moonmind.security.frameworks import FrameworkEvidence
from moonmind.security.identity_lifecycle import Workforce, reconcile
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
from moonmind.workflows.temporal.artifacts import ArtifactDependency

_LIFECYCLE = {
    "user.lifecycle.create",
    "user.lifecycle.activate",
    "user.lifecycle.deactivate",
    "user.lifecycle.suspend",
    "user.lifecycle.unsuspend",
    "user.lifecycle.delete.completed",
    "group.user_membership.add",
    "group.user_membership.remove",
    "user.account.privilege.grant",
    "user.account.privilege.revoke",
}
_STATUSES = {
    "STAGED",
    "PROVISIONED",
    "ACTIVE",
    "RECOVERY",
    "LOCKED_OUT",
    "PASSWORD_EXPIRED",
    "SUSPENDED",
    "DEPROVISIONED",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.isoformat() if result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _id(value, secret):
    return (
        value
        if isinstance(value, str)
        and secret not in value
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
        else None
    )


def _project(category, rows, secret):
    records, limitations = [], set()
    for item in rows:
        if not isinstance(item, dict):
            limitations.add("malformed_record")
            continue
        identifier = _id(item.get("uuid" if category == "logs" else "id"), secret)
        if not identifier:
            limitations.add("malformed_record")
            continue
        if category == "logs":
            if item.get("eventType") not in _LIFECYCLE:
                continue
            targets = item.get("target")
            if not isinstance(targets, list):
                limitations.add("malformed_record")
                continue
            at = _time(item.get("published"))
            if at is None:
                limitations.add("unknown_audit_timestamp")
            records.append(
                {
                    "id": identifier,
                    "type": item["eventType"],
                    "published": at,
                    "accountIds": [
                        t["id"]
                        for t in targets
                        if isinstance(t, dict)
                        and t.get("type") == "User"
                        and _id(t.get("id"), secret)
                    ],
                    "groupIds": [
                        t["id"]
                        for t in targets
                        if isinstance(t, dict)
                        and t.get("type") == "UserGroup"
                        and _id(t.get("id"), secret)
                    ],
                    "successful": isinstance(item.get("outcome"), dict)
                    and item["outcome"].get("result") == "SUCCESS",
                }
            )
        elif category in {"users", "deprovisioned"}:
            status = item.get("status")
            if status not in _STATUSES:
                limitations.add("unknown_account_status")
            records.append(
                {
                    "id": identifier,
                    "status": status if status in _STATUSES else "UNKNOWN",
                }
            )
        elif category == "roles":
            records.append({"id": identifier, "type": _id(item.get("type"), secret)})
        else:
            records.append({"id": identifier})
    return records, sorted(limitations)


def _next(response, *, origin, path, params, secret):
    """Validate the cursor; System Log continuations may replace since."""
    value = response.links.get("next", {}).get("url")
    if not value:
        return None
    url = urlsplit(value)
    query = parse_qs(url.query, keep_blank_values=True)
    cursor = query.pop("after", [])
    expected = {k: [str(v)] for k, v in params.items()}
    if path == "/api/v1/logs" and "since" not in query:
        expected.pop("since", None)
    if (
        url.scheme != "https"
        or url.netloc != origin
        or url.path != path
        or url.fragment
        or query != expected
        or len(cursor) != 1
        or not 1 <= len(cursor[0]) <= 2048
        or re.search(r"[\x00-\x1f\x7f]", cursor[0])
        or secret in cursor[0]
    ):
        raise ValueError("unsafe_pagination")
    return cursor[0]


def _transport(selection, workflow_id):
    policies = parse_egress_policies(os.environ.get(EGRESS_POLICIES_ENV_KEY))
    policy = next(
        (p for p in policies if p.policy_ref == selection.egress_policy_ref), None
    )
    if (
        policy is None
        or workflow_id not in policy.allowed_workflow_ids
        or not any(
            d.host == selection.organisation
            and d.protocol == "https"
            and d.port == 443
            and d.purpose == "provider-api"
            and d.target_network_ref is None
            for d in policy.destinations
        )
    ):
        raise ValueError("identity egress is not admitted")
    return tool_proxy_options("httpx", environ=os.environ)


class _Reader:
    def __init__(self, execution, selection, key, anchor):
        self.execution, self.selection, self.key, self.anchor = (
            execution,
            selection,
            key,
            anchor,
        )
        self.refs, self.coverage, self.limitations = [], {}, set(anchor["limitations"])
        self.requests = self.bytes = self.items = 0

    async def endpoint(self, client, secret, name, path, params):
        category = name.split(":")[0]
        records, cursor, seen = [], None, set()
        status = "pagination_limit"
        pages = 0
        record_limits = set()
        for index in range(self.selection.max_pages):
            page_key = content_key([self.key, name, index])
            saved = await self.execution.saved(
                kind="security.identity-page", key=page_key
            )
            if saved:
                ref, page = saved
            else:
                page = await self.fetch(client, secret, category, path, params, cursor)
                page["endpoint"] = name
                page["page"] = index
                page["completedAt"] = identity_lifecycle.utcnow().isoformat()
                ref = await self.execution.write(
                    _json(page), kind="security.identity-page", key=page_key
                )
            self.refs.append(ref)
            self.requests += page["requests"]
            self.bytes += page["bytes"]
            self.items += page["items"]
            self.limitations.update(page["limitations"])
            record_limits.update(page["limitations"])
            records.extend({**r, "evidenceRefs": [ref]} for r in page["records"])
            pages += int(page["status"] == "complete")
            status = page["status"]
            cursor = page["nextAfter"]
            if status != "complete" or cursor is None:
                break
            if cursor in seen:
                status = "pagination_cycle"
                break
            seen.add(cursor)
        else:
            status = "pagination_limit"
        self.coverage[name] = {
            "status": (
                "partial_records" if status == "complete" and record_limits else status
            ),
            "pages": pages,
            "records": len(records),
            "paginationComplete": status == "complete" and cursor is None,
            "requiredScope": (
                "okta.logs.read"
                if category == "logs"
                else "okta.roles.read" if category == "roles" else "okta.users.read"
            ),
        }
        if status != "complete":
            self.limitations.add(status)
            self.limitations.add("partial_pagination")
        return records

    async def fetch(self, client, secret, category, path, params, cursor):
        page = {
            "status": "upstream_unavailable",
            "records": [],
            "limitations": [],
            "requests": 0,
            "bytes": 0,
            "items": 0,
            "nextAfter": None,
            "observedAt": identity_lifecycle.utcnow().isoformat(),
        }
        for _ in range(3):
            if self.bytes + page["bytes"] >= self.selection.max_bytes:
                page["status"] = "byte_budget"
                break
            if self.items >= self.selection.max_items:
                page["status"] = "item_budget"
                break
            remaining = (
                datetime.fromisoformat(self.anchor["deadline"])
                - identity_lifecycle.utcnow()
            ).total_seconds()
            if (
                self.requests + page["requests"] >= self.selection.max_requests
                or remaining <= 0
            ):
                page["status"] = "request_budget" if remaining > 0 else "time_budget"
                break
            page["requests"] += 1
            body = bytearray()
            query = {**params, **({"after": cursor} if cursor else {})}
            if category == "logs" and cursor:
                # Okta's opaque continuation replaces since. Keep the admitted
                # until/order/limit; the saved request still owns period coverage.
                query.pop("since", None)
            try:
                async with asyncio.timeout(remaining):
                    async with client.stream(
                        "GET",
                        f"https://{self.selection.organisation}{path}",
                        params=query,
                        headers={
                            "Authorization": "Bearer " + secret,
                            "Accept": "application/json",
                        },
                        timeout=min(remaining, 15),
                    ) as response:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            body.extend(chunk)
                            if (
                                self.bytes + page["bytes"] + len(body)
                                > self.selection.max_bytes
                            ):
                                page["status"] = "byte_budget"
                                page["bytes"] += len(body)
                                return page
                        page["bytes"] += len(body)
                        status = response.status_code
                        if status in {500, 502, 503, 504}:
                            continue
                        if status != 200:
                            page["status"] = (
                                "permission_denied"
                                if status in {401, 403}
                                else (
                                    "rate_limited"
                                    if status == 429
                                    else "request_rejected"
                                )
                            )
                            return page
                        payload = json.loads(body)
                        if not isinstance(payload, list):
                            raise ValueError("malformed_response")
                        page["items"] = len(payload)
                        if self.items + page["items"] > self.selection.max_items:
                            page["status"] = "item_budget"
                            return page
                        page["records"], page["limitations"] = _project(
                            category, payload, secret
                        )
                        page["status"] = "complete"
                        try:
                            page["nextAfter"] = _next(
                                response,
                                origin=self.selection.organisation,
                                path=path,
                                params=params,
                                secret=secret,
                            )
                        except ValueError:
                            page["status"] = "unsafe_pagination"
                        return page
            except (httpx.TransportError, TimeoutError):
                page["status"] = "transport_unavailable"
            except (ValueError, TypeError):
                page["status"] = "malformed_response"
                return page
        return page


async def collect_identity(execution, selection):
    """Read admitted inputs before reuse; retain pages before reconciliation."""
    _, body = await execution.read(selection.workforce_ref)
    workforce = Workforce.model_validate_json(body)
    if workforce.organisation != selection.organisation or (
        workforce.since,
        workforce.until,
    ) != (selection.since, selection.until):
        raise ValueError(
            "workforce source differs from admitted organisation or period"
        )
    key = content_key(
        {
            "request": selection.model_dump(mode="json"),
            "workforce": execution.digests[selection.workforce_ref],
        }
    )
    saved = await execution.saved(kind="security.identity", key=key)
    if saved:
        root, report = saved
    else:
        options = _transport(selection, execution.execution["workflow_id"])
        from moonmind.auth.resolvers import DbEncryptedSecretResolver
        from moonmind.auth.secret_refs import parse_secret_ref

        try:
            secret = await DbEncryptedSecretResolver().resolve(
                parse_secret_ref(selection.secret_ref)
            )
            if not secret or "\r" in secret or "\n" in secret:
                raise ValueError("invalid credential")
        except Exception:
            raise ToolFailure(
                "POLICY_VIOLATION",
                "Identity provider credential is unavailable.",
                False,
            ) from None
        start = await execution.saved(kind="security.identity-start", key=key)
        if start:
            anchor_ref, anchor = start
        else:
            now = identity_lifecycle.utcnow()
            limitations = [
                "snapshot_cannot_prove_history",
                "deleted_accounts_unavailable",
                "downstream_access_unverified",
            ]
            if selection.since < now - timedelta(days=90):
                limitations.append("audit_retention_gap")
            if selection.until > now:
                limitations.append("period_in_progress")
            anchor = {
                "observedAt": now.isoformat(),
                "deadline": (
                    now + timedelta(seconds=selection.max_seconds)
                ).isoformat(),
                "retentionEarliest": (now - timedelta(days=90)).isoformat(),
                "limitations": limitations,
            }
            anchor_ref = await execution.write(
                _json(anchor), kind="security.identity-start", key=key
            )
        reader = _Reader(execution, selection, key, anchor)
        async with httpx.AsyncClient(**options, follow_redirects=False) as client:
            users = await reader.endpoint(
                client,
                secret,
                "users",
                "/api/v1/users",
                {"limit": 200, "fields": "id,status"},
            )
            users += await reader.endpoint(
                client,
                secret,
                "deprovisioned",
                "/api/v1/users",
                {
                    "limit": 200,
                    "fields": "id,status",
                    "filter": 'status eq "DEPROVISIONED"',
                },
            )
            events = await reader.endpoint(
                client,
                secret,
                "logs",
                "/api/v1/logs",
                {
                    "limit": 200,
                    "since": selection.since.isoformat(),
                    "until": selection.until.isoformat(),
                    "sortOrder": "ASCENDING",
                },
            )
            accounts = {}
            duplicates = 0
            for user in users:
                if user["id"] in accounts:
                    duplicates += 1
                    if user["status"] != accounts[user["id"]]["status"]:
                        reader.limitations.add("conflicting_account_records")
                    accounts[user["id"]]["evidenceRefs"].extend(user["evidenceRefs"])
                else:
                    accounts[user["id"]] = user
            for user in accounts.values():
                user["groups"], user["roles"], user["privileged"] = [], [], None
                if reader.requests >= selection.max_requests:
                    reader.limitations.add("assignments_not_collected")
                    continue
                for category in ("groups", "roles"):
                    rows = await reader.endpoint(
                        client,
                        secret,
                        f'{category}:{user["id"]}',
                        f'/api/v1/users/{user["id"]}/{category}',
                        {} if category == "roles" else {"limit": 200},
                    )
                    user[category] = [r["id"] for r in rows]
                    user["evidenceRefs"].extend(
                        ref for r in rows for ref in r["evidenceRefs"]
                    )
                positive = bool(
                    user["roles"]
                    or set(user["groups"]) & set(selection.privileged_group_ids)
                )
                complete = all(
                    reader.coverage[f'{category}:{user["id"]}']["paginationComplete"]
                    and reader.coverage[f'{category}:{user["id"]}']["status"]
                    == "complete"
                    for category in ("groups", "roles")
                )
                user["privileged"] = True if positive else False if complete else None
        # Conflicting duplicates must remain ambiguous even though the report's
        # current-account projection has one row per source account.
        reconciled = reconcile(
            selection,
            workforce,
            users,
            events,
            observed_at=datetime.fromisoformat(anchor["observedAt"]),
        )
        reader.limitations.update(
            row["reason"]
            for row in reconciled["lifecycle"]
            if row["status"] == "review_required"
        )
        for event in events:
            if (
                event["published"]
                and not selection.since
                <= datetime.fromisoformat(event["published"])
                <= selection.until
            ):
                reader.limitations.add("audit_event_outside_period")
        report = {
            "schemaVersion": "identity-lifecycle-evidence/v1",
            "provider": "okta",
            "organisation": selection.organisation,
            "requestedPeriod": {
                "since": selection.since.isoformat(),
                "until": selection.until.isoformat(),
            },
            "observedAt": anchor["observedAt"],
            "collectionInterval": {
                "startedAt": anchor["observedAt"],
                "completedAt": identity_lifecycle.utcnow().isoformat(),
            },
            "anchorRef": anchor_ref,
            "retention": {
                "days": 90,
                "earliestAvailableEstimate": anchor["retentionEarliest"],
                "basis": "provider_documented_limit_not_completeness_attestation",
            },
            "timestampSemantics": {
                "accounts": "collection_snapshot",
                "audit": "published_event_time_not_effective_removal",
                "workforce": "source_declared_effective_time_or_unknown",
            },
            "capabilities": {
                "accountStatus": True,
                "groupMembership": True,
                "assignedAdminRoles": True,
                "lifecycleAudit": True,
                "deletedAccountHistory": False,
                "downstreamApplications": False,
                "effectiveRemovalTime": False,
            },
            "policy": {
                "removalHours": selection.removal_hours,
                "clock": "audit_publication",
                "privilegedGroupIds": selection.privileged_group_ids,
            },
            "accounts": list(accounts.values()),
            "events": events,
            "duplicateAccountRecords": duplicates,
            "coverage": reader.coverage,
            "limitations": sorted(reader.limitations),
            "pageRefs": reader.refs,
            "usage": {
                "requests": reader.requests,
                "bytes": reader.bytes,
                "items": reader.items,
            },
            **reconciled,
            "reviewRequests": [
                {
                    "method": "enquiry_evidence_observation",
                    "sample": "joiner_mover_leaver_privileged_service_and_exceptions",
                    "evidenceRefs": [selection.workforce_ref, *reader.refs],
                    "reason": "Review matching, coverage, policy and representative samples; collector output is not a compliance verdict.",
                }
            ],
        }
        root = await execution.write(_json(report), kind="security.identity", key=key)

    async def dependencies():
        return [
            ArtifactDependency(
                ref, await execution.digest(ref), True, "security.identity-input"
            )
            for ref in [
                selection.workforce_ref,
                report["anchorRef"],
                *report["pageRefs"],
            ]
        ]

    await execution.seal(root, dependencies)
    evidence = FrameworkEvidence(
        kind="technical_assessment",
        requirement_ids=selection.requirement_ids,
        systems=[selection.organisation],
        period={"start": selection.since.date(), "end": selection.until.date()},
        method="evidence",
        description="Identity lifecycle collection for review and sampling. Historical control operation is not established.",
        provenance={
            "sourceRef": root,
            "issuer": "MoonMind Okta evidence reader",
            "version": "1",
            "recordedOn": datetime.fromisoformat(report["observedAt"]).date(),
        },
        artifact_ref=root,
    )
    review_ref = await execution.write(
        evidence.model_dump_json(by_alias=True).encode(),
        kind="security.identity-review",
        key=key,
    )

    async def review_dependencies():
        return [
            ArtifactDependency(
                root, await execution.digest(root), True, "security.identity-evidence"
            )
        ]

    await execution.seal(review_ref, review_dependencies)
    return root, review_ref, report


def render_identity_summary(identity):
    """No personnel, provider error bodies, account IDs or credentials."""
    return (
        "\nIdentity lifecycle evidence: "
        + identity["evidenceRef"]
        + ".\n"
        + "\n".join(f"- {name}: {count}" for name, count in identity["counts"].items())
        + "\nHistorical control operation is not established. Review and sample the restricted evidence.\n"
    )
