"""Scoped GET-only GitHub populations, retained by the assessment artifact owner."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from moonmind.security.assessments import content_key
from moonmind.security.frameworks import FrameworkEvidence
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.artifacts import ArtifactDependency

_PAGE_BYTES = 512 * 1024
_POPULATION_PART_BYTES = 4 * 1024 * 1024
_TRANSIENT = {429, 500, 502, 503, 504}
_PERMISSIONS = {
    "repository": "metadata:read",
    "rules": "metadata:read",
    "protection": "administration:read",
    "pulls": "pull_requests:read",
    "associations": "pull_requests:read",
    "reviews": "pull_requests:read",
    "commits": "contents:read",
    "checks": "checks:read",
    "statuses": "statuses:read",
    "workflows": "actions:read",
    "environments": "actions:read",
    "deployments": "deployments:read",
    "deployment-statuses": "deployments:read",
}


def utcnow():
    return datetime.now(UTC)


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.utcoffset() is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _sha(value):
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
        else None
    )


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("malformed_record")
    return value


def _name(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or re.search(r"[\x00-\x1f\x7f]", value)
        or redact_sensitive_text(value) != value
    ):
        raise ValueError("malformed_record")
    return value


def _timestamp(value, *, required=False):
    if value is None and not required:
        return None
    at = _time(value)
    if at is None:
        raise ValueError("malformed_record")
    return at.isoformat()


def _enum(value, choices):
    if value not in choices:
        raise ValueError("malformed_record")
    return value


def _approvals(value):
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100
    ):
        raise ValueError("malformed_record")
    return value


def _project(category, row):
    """Keep source identities and facts, excluding bodies, logs and credentials."""
    if not isinstance(row, dict):
        raise ValueError("malformed_record")
    if category == "commits":
        sha = _sha(row.get("sha"))
        if not sha:
            raise ValueError("malformed_record")
        return {
            "id": sha,
            "sha": sha,
            "committedAt": _timestamp(
                row.get("commit", {}).get("committer", {}).get("date"), required=True
            ),
        }
    if category == "rules":
        kind = _name(row.get("type"))
        ruleset = row.get("ruleset_id")
        approvals = _approvals(
            row.get("parameters", {}).get("required_approving_review_count")
        )
        if ruleset is not None:
            _identifier(ruleset)
        return {
            "id": content_key(row),
            "type": kind,
            "rulesetId": ruleset,
            "requiredApprovals": approvals,
        }
    if category == "protection":
        reviews = row.get("required_pull_request_reviews")
        checks = row.get("required_status_checks")
        reviews = {} if reviews is None else reviews
        checks = {} if checks is None else checks
        return {
            "id": content_key(row),
            "requiredApprovals": _approvals(
                reviews.get("required_approving_review_count")
            ),
            "requiredStatusChecks": [
                _name(context) for context in checks.get("contexts", [])
            ],
            "requiredChecks": [
                {
                    "context": _name(check.get("context")),
                    "appId": (
                        _identifier(check["app_id"])
                        if check.get("app_id") is not None
                        else None
                    ),
                }
                for check in checks.get("checks", [])
            ],
        }
    result = {"id": _identifier(row.get("id"))}
    if category in {"pulls", "associations"}:
        if "merged_at" not in row:
            raise ValueError("malformed_record")
        result.update(
            number=_identifier(row.get("number")),
            mergedAt=_timestamp(row.get("merged_at")),
            headSha=_sha(row.get("head", {}).get("sha")),
            mergeSha=_sha(row.get("merge_commit_sha")),
            baseRef=_name(row.get("base", {}).get("ref")),
            labels=[_name(label.get("name")) for label in row.get("labels", [])],
        )
    elif category == "repository":
        result.update(
            repository=_name(row.get("full_name")),
            defaultBranch=_name(row.get("default_branch")),
        )
    elif category == "reviews":
        result.update(
            state=_enum(
                row.get("state"),
                {"PENDING", "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"},
            ),
            commitSha=_sha(row.get("commit_id")),
            reviewerId=_identifier(row.get("user", {}).get("id")),
            submittedAt=_timestamp(
                row.get("submitted_at"), required=row.get("state") != "PENDING"
            ),
        )
    elif category == "environments":
        result.update(
            name=_name(row.get("name")),
            protectionRuleTypes=[
                _name(rule.get("type")) for rule in row.get("protection_rules", [])
            ],
        )
    elif category == "deployments":
        if not _sha(row.get("sha")):
            raise ValueError("malformed_record")
        result.update(
            sha=_sha(row.get("sha")),
            environment=_name(row.get("environment")),
            production=row.get("production_environment") is True,
        )
    elif category == "deployment-statuses":
        result.update(
            state=_enum(
                row.get("state"),
                {
                    "error",
                    "failure",
                    "inactive",
                    "in_progress",
                    "queued",
                    "pending",
                    "success",
                },
            )
        )
    elif category == "statuses":
        result.update(
            context=_name(row.get("context")),
            state=_enum(row.get("state"), {"error", "failure", "pending", "success"}),
        )
    else:
        result.update(
            name=_name(row.get("name")),
            status=_enum(
                row.get("status"),
                {
                    "queued",
                    "in_progress",
                    "completed",
                    "waiting",
                    "requested",
                    "pending",
                },
            ),
            conclusion=_enum(
                row.get("conclusion"),
                {
                    None,
                    "success",
                    "failure",
                    "neutral",
                    "cancelled",
                    "skipped",
                    "timed_out",
                    "action_required",
                    "stale",
                    "startup_failure",
                },
            ),
            headSha=_sha(row.get("head_sha")),
        )
        if category == "workflows":
            result["workflowId"] = _identifier(row.get("workflow_id"))
            result["runAttempt"] = _identifier(row.get("run_attempt"))
    for name in ("created_at", "updated_at", "started_at", "completed_at"):
        if name in row:
            result[name] = _timestamp(row[name])
    if category in {"deployments", "deployment-statuses"}:
        result["created_at"] = _timestamp(row.get("created_at"), required=True)
    return result


def _next(response, url, query, page):
    value = response.links.get("next", {}).get("url")
    if not value:
        # An advertised but unparsable continuation cannot establish exhaustion.
        if 'rel="next"' in response.headers.get("link", ""):
            raise ValueError("unsafe_pagination")
        return None
    target, original = urlsplit(value), urlsplit(url)
    if (target.scheme, target.netloc, target.path) != (
        original.scheme,
        original.netloc,
        original.path,
    ) or target.fragment:
        raise ValueError("unsafe_pagination")
    if parse_qs(target.query, keep_blank_values=True) != {
        k: [str(v)] for k, v in {**query, "page": page + 1}.items()
    }:
        raise ValueError("unsafe_pagination")
    return page + 1


class _Reader:
    def __init__(self, execution, selection, key, state):
        self.execution, self.selection, self.key, self.state = (
            execution,
            selection,
            key,
            state,
        )
        self.coverage, self.refs = {}, []
        self.limitations = {
            "provider_snapshot_not_historical_control_proof",
            "commit_time_is_not_push_time",
        }

    async def save(self):
        return await self.execution.write(
            _json(self.state), kind="security.github-change-progress", key=self.key
        )

    def settle(self, pending, page):
        self.state["bytes"] += page["bytes"] - pending["bytes"]
        self.state["items"] += page["items"]
        self.state["receipts"].append(
            {
                **pending,
                "status": page["status"],
                "responseStatus": page["responseStatus"],
                "bytes": page["bytes"],
                "items": page["items"],
                "completedAt": utcnow().isoformat(),
            }
        )

    async def recover(self):
        pending = self.state.pop("pending", None)
        if pending:
            saved = await self.execution.saved(
                kind="security.github-change-page", key=pending["key"]
            )
            if saved:
                self.settle(pending, saved[1])
            else:
                # An interrupted exchange consumed its request and reserves its
                # byte allowance. Neither the wall deadline nor budgets restart.
                self.state["interruptedReads"] += 1
                self.state["receipts"].append(
                    {
                        **pending,
                        "status": "interrupted_response_unknown",
                        "responseStatus": None,
                    }
                )
            await self.save()

    async def endpoint(
        self,
        client,
        base,
        headers,
        repository,
        name,
        path,
        *,
        category,
        params=None,
        envelope=None,
        single=False,
    ):
        query = {} if single else {"per_page": self.selection.per_page}
        query.update(params or {})
        url = f"{base}/repos/{repository}" + (f"/{path}" if path else "")
        records, status, pages, total = {}, "complete", 0, None
        record_gaps = False
        for page_number in range(1, self.selection.max_pages + 1):
            page_key = content_key([self.key, repository, name, page_number])
            saved = await self.execution.saved(
                kind="security.github-change-page", key=page_key
            )
            if saved:
                ref, page = saved
            else:
                page = await self.fetch(
                    client,
                    url,
                    headers,
                    query,
                    page_number,
                    page_key,
                    category,
                    envelope,
                    single,
                )
                ref = await self.execution.write(
                    _json(page), kind="security.github-change-page", key=page_key
                )
                pending = self.state.pop("pending", None)
                if pending:
                    self.settle(pending, page)
                await self.save()
            self.refs.append(ref)
            status = page["status"]
            record_gaps |= status == "partial_records"
            for row in page["records"]:
                identity = str(row["id"])
                if identity in records:
                    prior = {
                        k: v
                        for k, v in records[identity].items()
                        if k != "evidenceRefs"
                    }
                    if prior != row:
                        self.limitations.add(
                            f"{repository}:{name}:conflicting_duplicate"
                        )
                    records[identity]["evidenceRefs"].append(ref)
                else:
                    records[identity] = {**row, "evidenceRefs": [ref]}
            pages += int(page["responseStatus"] == 200)
            if page.get("total") is not None:
                if total is not None and total != page["total"]:
                    self.limitations.add(f"{repository}:{name}:population_changed")
                total = max(total or 0, page["total"])
            if (
                status not in {"complete", "partial_records"}
                or page["nextPage"] is None
            ):
                break
        else:
            status = "page_budget"
        if status == "complete" and record_gaps:
            status = "partial_records"
        if status == "complete" and total is not None and len(records) < total:
            status = "missing_pages"
        if any(s.startswith(f"{repository}:{name}:") for s in self.limitations):
            status = "conflicting_population" if status == "complete" else status
        self.coverage[f"{repository}:{name}"] = {
            "status": status,
            "paginationComplete": status == "complete",
            "pages": pages,
            "records": len(records),
            "sourceUrl": url,
            "query": query,
            "requiredPermission": _PERMISSIONS[category],
        }
        if status != "complete":
            self.limitations.add(f"{repository}:{name}:{status}")
        return list(records.values())

    async def fetch(
        self, client, url, headers, query, number, page_key, category, envelope, single
    ):
        result = {
            "schemaVersion": "github-change-control-page/v1",
            "sourceUrl": url,
            "query": {**query, **({} if single else {"page": number})},
            "page": number,
            "collectedAt": utcnow().isoformat(),
            "status": "upstream_unavailable",
            "responseStatus": None,
            "records": [],
            "nextPage": None,
            "bytes": 0,
            "items": 0,
            "total": None,
        }
        for _ in range(3):
            remaining = (_time(self.state["deadline"]) - utcnow()).total_seconds()
            limit = next(
                (
                    name
                    for name, reached in (
                        ("time_budget", remaining <= 0),
                        (
                            "request_budget",
                            self.state["requests"] >= self.selection.max_requests,
                        ),
                        (
                            "byte_budget",
                            self.state["bytes"] >= self.selection.max_bytes,
                        ),
                        (
                            "item_budget",
                            self.state["items"] >= self.selection.max_items,
                        ),
                        (
                            "retry_exhausted",
                            self.state["attempts"].get(page_key, 0) >= 3,
                        ),
                    )
                    if reached
                ),
                None,
            )
            if limit:
                result["status"] = limit
                return result
            allowance = min(_PAGE_BYTES, self.selection.max_bytes - self.state["bytes"])
            self.state["requests"] += 1
            self.state["attempts"][page_key] = (
                self.state["attempts"].get(page_key, 0) + 1
            )
            self.state["bytes"] += allowance
            self.state["pending"] = {
                "key": page_key,
                "bytes": allowance,
                "request": self.state["requests"],
                "sourceUrl": url,
                "query": result["query"],
                "startedAt": utcnow().isoformat(),
            }
            await self.save()
            body = bytearray()
            try:
                async with asyncio.timeout(remaining):
                    async with client.stream(
                        "GET",
                        url,
                        headers=headers,
                        params=result["query"],
                        timeout=min(remaining, 20),
                    ) as response:
                        result["responseStatus"] = response.status_code
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > allowance:
                                result["status"], result["bytes"] = (
                                    "byte_budget",
                                    allowance,
                                )
                                return result
                            body.extend(chunk)
                        result["bytes"] = len(body)
                        rate_limited = response.status_code == 429 or (
                            response.status_code == 403
                            and (
                                response.headers.get("x-ratelimit-remaining") == "0"
                                or "retry-after" in response.headers
                            )
                        )
                        if response.status_code in _TRANSIENT or rate_limited:
                            result["status"] = (
                                "rate_limited"
                                if rate_limited
                                else "upstream_unavailable"
                            )
                            delay = response.headers.get("retry-after", "0")
                            if rate_limited and (
                                not delay.isdecimal()
                                or int(delay) > 1
                                or int(delay) >= remaining
                            ):
                                result["retryAfter"] = (
                                    delay[:32] if delay.isdecimal() else None
                                )
                                return result
                            self.settle(self.state.pop("pending"), result)
                            await self.save()
                            if rate_limited:
                                await asyncio.sleep(int(delay))
                            continue
                        if response.status_code != 200:
                            result["status"] = {
                                401: "permission_denied",
                                403: "permission_denied",
                                404: "not_found",
                            }.get(response.status_code, "request_rejected")
                            return result
                        payload = json.loads(body)
                        if envelope:
                            total = payload.get("total_count")
                            if (
                                isinstance(total, bool)
                                or not isinstance(total, int)
                                or total < 0
                            ):
                                raise ValueError("malformed_response")
                            result["total"] = total
                            payload = payload[envelope]
                        if single:
                            payload = [payload]
                        if not isinstance(payload, list):
                            raise ValueError("malformed_response")
                        result["items"] = len(payload)
                        if (
                            self.state["items"] + len(payload)
                            > self.selection.max_items
                        ):
                            result["status"] = "item_budget"
                            result["items"] = 0
                            return result
                        result["status"] = "complete"
                        result["rejectedRecords"] = []
                        credential = headers.get("Authorization", "").partition(" ")[2]
                        for index, row in enumerate(payload):
                            try:
                                record = _project(category, row)
                                if credential and credential in json.dumps(record):
                                    raise ValueError("malformed_record")
                                result["records"].append(record)
                            except (ValueError, TypeError, AttributeError):
                                result["rejectedRecords"].append(index)
                                result["status"] = "partial_records"
                        result["nextPage"] = (
                            None if single else _next(response, url, query, number)
                        )
                        return result
            except (httpx.TransportError, TimeoutError):
                result["status"] = "transport_unavailable"
                result["bytes"] = len(body)
                self.settle(self.state.pop("pending"), result)
                await self.save()
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                result["status"] = (
                    "unsafe_pagination"
                    if str(exc) == "unsafe_pagination"
                    else "malformed_response"
                )
                return result
        return result


def _within(selection, value):
    at = _time(value)
    return at is not None and selection.since <= at < selection.until


async def _repository(reader, client, base, headers, repo):
    selection = reader.selection

    async def endpoint(name, path=None, **kwargs):
        return await reader.endpoint(
            client, base, headers, repo, name, name if path is None else path, **kwargs
        )

    metadata = await endpoint("repository", "", category="repository", single=True)
    if not metadata:
        return [], [], [], {}
    if metadata[0]["repository"].lower() != repo.lower():
        reader.coverage[f"{repo}:repository"][
            "status"
        ] = "repository_identity_unavailable"
        reader.coverage[f"{repo}:repository"]["paginationComplete"] = False
        reader.limitations.add(f"{repo}:repository_identity_unavailable")
        return [], [], [], {}
    pulls = await endpoint(
        "pulls",
        category="pulls",
        params={"state": "closed", "sort": "updated", "direction": "desc"},
    )
    for p in pulls:
        if p["mergedAt"] is not None and (
            not _time(p["mergedAt"]) or not p["mergeSha"] or not p["headSha"]
        ):
            reader.coverage[f"{repo}:pulls"]["status"] = "malformed_merge"
            reader.limitations.add(f"{repo}:malformed_merge")
    changes = [
        {**p, "repository": repo, "changeId": f"{repo}#{p['number']}"}
        for p in pulls
        if _within(selection, p["mergedAt"])
    ]
    commits = await endpoint(
        "commits",
        category="commits",
        params={
            "sha": metadata[0]["defaultBranch"],
            "since": selection.since.isoformat(),
            "until": selection.until.isoformat(),
        },
    )
    commits = [
        {**c, "repository": repo}
        for c in commits
        if _within(selection, c["committedAt"])
    ]
    environments = await endpoint(
        "environments", category="environments", envelope="environments"
    )
    missing = set(selection.environments) - {e["name"] for e in environments}
    if missing and reader.coverage[f"{repo}:environments"]["status"] == "complete":
        reader.coverage[f"{repo}:environments"][
            "status"
        ] = "selected_environment_unavailable"
        reader.limitations.add(f"{repo}:selected_environment_unavailable")
    for branch in sorted(
        {metadata[0]["defaultBranch"], *(p["baseRef"] for p in changes)}
    ):
        await endpoint(
            f"rules:{branch}",
            f"rules/branches/{quote(branch, safe='')}",
            category="rules",
        )
        await endpoint(
            f"protection:{branch}",
            f"branches/{quote(branch, safe='')}/protection",
            category="protection",
            single=True,
        )
    deployments = []
    for environment in selection.environments:
        rows = await endpoint(
            f"deployments:{environment}",
            "deployments",
            category="deployments",
            params={"environment": environment},
        )
        for deployment in rows:
            statuses = await endpoint(
                f"deployment-statuses:{deployment['id']}",
                f"deployments/{deployment['id']}/statuses",
                category="deployment-statuses",
            )
            deployment = {**deployment, "repository": repo, "statuses": statuses}
            if _within(selection, deployment.get("created_at")) or any(
                _within(selection, s.get("created_at")) for s in statuses
            ):
                deployments.append(deployment)
    verified = {}
    for sha in sorted(
        {s for p in changes for s in (p["headSha"], p["mergeSha"]) if s}
        | {d["sha"] for d in deployments if d["sha"]}
    ):
        verified[sha] = {
            "checks": await endpoint(
                f"checks:{sha}",
                f"commits/{sha}/check-runs",
                category="checks",
                envelope="check_runs",
                params={"filter": "all"},
            ),
            "statuses": await endpoint(
                f"statuses:{sha}", f"commits/{sha}/statuses", category="statuses"
            ),
            "workflows": await endpoint(
                f"workflows:{sha}",
                "actions/runs",
                category="workflows",
                envelope="workflow_runs",
                params={"head_sha": sha},
            ),
        }
        for category in ("checks", "workflows"):
            if any(row["headSha"] != sha for row in verified[sha][category]):
                reader.coverage[f"{repo}:{category}:{sha}"][
                    "status"
                ] = "identity_mismatch"
                reader.limitations.add(f"{repo}:{category}:{sha}:identity_mismatch")
    for deployment in deployments:
        deployment["verificationRef"] = f"{repo}:{deployment['sha']}"
    for change in changes:
        reviews = await endpoint(
            f"reviews:{change['number']}",
            f"pulls/{change['number']}/reviews",
            category="reviews",
        )
        current = {}
        for r in sorted(
            reviews,
            key=lambda r: (
                _time(r["submittedAt"]) or datetime.min.replace(tzinfo=UTC),
                r["id"],
            ),
        ):
            if (
                r["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
                and _time(r["submittedAt"])
                and _time(r["submittedAt"]) <= _time(change["mergedAt"])
            ):
                current[r["reviewerId"]] = r
        approved = any(
            r["state"] == "APPROVED" and r["commitSha"] == change["headSha"]
            for r in current.values()
        )
        linked = [
            d
            for d in deployments
            if d["sha"] == change["mergeSha"]
            and d["production"]
            and d["environment"] in selection.environments
            and any(
                s["state"] == "success"
                and _within(selection, s.get("created_at"))
                and _time(s["created_at"]) >= _time(change["mergedAt"])
                for s in d["statuses"]
            )
        ]
        change.update(
            reviews=reviews,
            approval="observed" if approved else "unresolved",
            verificationRefs=[
                f"{repo}:{sha}"
                for sha in dict.fromkeys([change["headSha"], change["mergeSha"]])
                if sha in verified
            ],
            deploymentIds=[d["id"] for d in linked],
            productionLink="recorded_success" if linked else "unresolved",
        )
    merge_shas = {p["mergeSha"] for p in changes}
    for commit in commits:
        if commit["sha"] not in merge_shas:
            commit["associatedPulls"] = await endpoint(
                f"associations:{commit['sha']}",
                f"commits/{commit['sha']}/pulls",
                category="associations",
            )
    return (
        changes,
        commits,
        deployments,
        {f"{repo}:{sha}": value for sha, value in verified.items()},
    )


async def _seal(execution, root, refs):
    async def dependencies():
        return [
            ArtifactDependency(
                ref,
                await execution.digest(ref),
                True,
                "security.github-change-evidence",
            )
            for ref in dict.fromkeys(refs)
        ]

    await execution.seal(root, dependencies)


async def _write_population(execution, population, key):
    body = _json(population)
    parts = []
    if len(body) > _POPULATION_PART_BYTES:
        for offset in range(0, len(body), _POPULATION_PART_BYTES):
            parts.append(
                await execution.write(
                    body[offset : offset + _POPULATION_PART_BYTES],
                    kind="security.github-change-population-part",
                    key=content_key([key, offset]),
                    content_type="application/octet-stream",
                )
            )
        document = {
            "schemaVersion": "github-change-control-population-manifest/v1",
            "partRefs": parts,
            **{
                name: population[name]
                for name in (
                    "scope",
                    "coverageStatus",
                    "limitations",
                    "pageRefs",
                    "progressRef",
                    "collectedAt",
                )
            },
        }
        body = _json(document)
    ref = await execution.write(body, kind="security.github-change-population", key=key)
    return ref, parts


async def collect_period(service, execution, selection):
    key = content_key(selection.model_dump(mode="json", by_alias=True))
    # Recheck the existing repository authority before using saved provider work.
    connections, authorization_errors = {}, {}
    for repo in selection.repositories:
        try:
            connections[repo] = await service._repository_reader(
                repo, None, execution.execution["workflow_id"], operation="read"
            )
        except Exception as exc:
            connections[repo] = None
            authorization_errors[repo] = type(exc).__name__
    saved = await execution.saved(kind="security.github-change-report", key=key)
    if saved:
        root, report = saved
        population_ref, sample_ref = report["populationRef"], report["sampleRef"]
        _, body = await execution.read(population_ref)
        document = json.loads(body)
        population_parts = document.get("partRefs", [])
        if population_parts:
            bodies = [(await execution.read(ref))[1] for ref in population_parts]
            population = json.loads(b"".join(bodies))
        else:
            population = document
    else:
        saved_state = await execution.saved(
            kind="security.github-change-progress", key=key, latest=True
        )
        if saved_state:
            _, state = saved_state
        else:
            now = utcnow()
            state = {
                "startedAt": now.isoformat(),
                "deadline": (
                    now + timedelta(seconds=selection.max_seconds)
                ).isoformat(),
                "requests": 0,
                "bytes": 0,
                "items": 0,
                "attempts": {},
                "interruptedReads": 0,
                "receipts": [],
            }
        reader = _Reader(execution, selection, key, state)
        if not saved_state:
            await reader.save()
        await reader.recover()
        changes, commits, deployments, verification = [], [], [], {}
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            for repo, connection in connections.items():
                if connection is None:
                    reader.coverage[f"{repo}:repository"] = {
                        "status": "authorization_unavailable",
                        "paginationComplete": False,
                        "pages": 0,
                        "records": 0,
                        "errorClass": authorization_errors[repo],
                        "requiredOperation": "read",
                    }
                    reader.limitations.add(f"{repo}:authorization_unavailable")
                    continue
                base, headers = connection
                c, m, d, v = await _repository(
                    reader, client, base.rstrip("/"), headers, repo
                )
                changes.extend(c)
                commits.extend(m)
                deployments.extend(d)
                verification.update(v)
        if selection.until > _time(state["startedAt"]):
            reader.limitations.add("period_in_progress")
        if selection.since < _time(state["startedAt"]) - timedelta(days=90):
            reader.limitations.add("deployment_status_retention_gap")
        progress_ref = await reader.save()
        population = {
            "schemaVersion": "github-change-control-population/v1",
            "scope": selection.model_dump(mode="json", by_alias=True),
            "startedAt": state["startedAt"],
            "deadline": state["deadline"],
            "collectedAt": utcnow().isoformat(),
            "coverageStatus": (
                "complete"
                if all(c["status"] == "complete" for c in reader.coverage.values())
                and "period_in_progress" not in reader.limitations
                and "deployment_status_retention_gap" not in reader.limitations
                else "incomplete"
            ),
            "coverage": reader.coverage,
            "limitations": sorted(reader.limitations),
            "usage": {
                k: state[k] for k in ("requests", "bytes", "items", "interruptedReads")
            },
            "progressRef": progress_ref,
            "pageRefs": list(dict.fromkeys(reader.refs)),
            "changes": changes,
            "commits": commits,
            "deployments": deployments,
            "verification": verification,
        }
        population_ref, population_parts = await _write_population(
            execution, population, key
        )
        sample = {
            "schemaVersion": "github-change-control-sample/v1",
            "populationRef": population_ref,
            "selectedChangeIds": [
                c["changeId"]
                for c in sorted(changes, key=lambda c: c["changeId"])[
                    : selection.sample_size
                ]
            ],
            "rationale": "First N merged changes in lexical repository/PR identity order; deterministic, not statistical assurance or a production-change population.",
            "scope": population["scope"],
            "coverageStatus": population["coverageStatus"],
            "limitations": population["limitations"],
        }
        sample_ref = await execution.write(
            _json(sample), kind="security.github-change-sample", key=key
        )
        exceptions = []
        reasons = {
            "emergency_change": "Matched a selected emergency label; review the retained approval and deployment evidence for exception handling.",
            "missing_review": "No retained approval on the observed PR head before merge; unavailable review pages remain coverage gaps.",
            "missing_deployment": "No successful production deployment matching the observed merge commit in the selected period; merge and review alone do not establish production.",
        }
        for change in changes:
            kinds = (
                ["emergency_change"]
                if set(change["labels"]) & set(selection.emergency_labels)
                else []
            )
            if change["approval"] == "unresolved":
                kinds.append("missing_review")
            if change["productionLink"] == "unresolved":
                kinds.append("missing_deployment")
            exceptions.extend(
                {
                    "kind": kind,
                    "reason": reasons[kind],
                    "changeId": change["changeId"],
                    "evidenceRefs": [population_ref, *change["evidenceRefs"]],
                }
                for kind in kinds
            )
        for commit in commits:
            if "associatedPulls" in commit and not commit["associatedPulls"]:
                exceptions.append(
                    {
                        "kind": "possible_direct_push",
                        "commitSha": commit["sha"],
                        "repository": commit["repository"],
                        "reason": "No observed PR association; commit time and an absent association do not prove a direct push.",
                        "evidenceRefs": [population_ref, *commit["evidenceRefs"]],
                    }
                )
        for deployment in deployments:
            if not any(
                c["repository"] == deployment["repository"]
                and c["mergeSha"] == deployment["sha"]
                for c in changes
            ):
                exceptions.append(
                    {
                        "kind": "deployment_without_period_pr",
                        "reason": "No matching merged PR in the selected merge-time population; earlier merges and direct changes remain possible.",
                        "deploymentId": deployment["id"],
                        "evidenceRefs": [population_ref, *deployment["evidenceRefs"]],
                    }
                )
        if population["coverageStatus"] != "complete":
            exceptions.append(
                {
                    "kind": "incomplete_population",
                    "reason": "Source coverage is incomplete; selected samples cannot establish a complete production-change population.",
                    "evidenceRefs": [population_ref],
                }
            )
        report = {
            "schemaVersion": "github-change-control-report/v1",
            "populationRef": population_ref,
            "sampleRef": sample_ref,
            "coverageStatus": population["coverageStatus"],
            "limitations": population["limitations"],
            "exceptions": exceptions,
            "controlOperation": "not_established",
            "counts": {
                "mergedChanges": len(changes),
                "deployments": len(deployments),
                "unresolvedProductionLinks": sum(
                    c["productionLink"] == "unresolved" for c in changes
                ),
                "exceptions": len(exceptions),
            },
        }
        root = await execution.write(
            _json(report), kind="security.github-change-report", key=key
        )
    await _seal(
        execution,
        population_ref,
        [population["progressRef"], *population["pageRefs"], *population_parts],
    )
    await _seal(execution, sample_ref, [population_ref])
    await _seal(execution, root, [population_ref, sample_ref])
    evidence = FrameworkEvidence(
        kind="technical_assessment",
        requirement_ids=selection.requirement_ids,
        systems=selection.repositories,
        period={
            "start": selection.since.astimezone(UTC).date(),
            "end": (selection.until.astimezone(UTC) - timedelta(microseconds=1)).date(),
        },
        method="evidence",
        description=f"GitHub change-control sources for SOC 2 / ISO review. Coverage: {report['coverageStatus']}; unresolved production links: {report['counts']['unresolvedProductionLinks']}. Population, sampling limits and exceptions are retained; historical control operation is not established.",
        provenance={
            "sourceRef": root,
            "issuer": "MoonMind GitHub evidence reader",
            "version": "1",
            "recordedOn": _time(population["collectedAt"]).date(),
        },
        artifact_ref=root,
    )
    saved_review = await execution.saved(kind="security.github-change-review", key=key)
    review_ref = (
        saved_review[0]
        if saved_review
        else await execution.write(
            evidence.model_dump_json(by_alias=True).encode(),
            kind="security.github-change-review",
            key=key,
        )
    )
    await _seal(execution, review_ref, [root])
    return root, review_ref, report


def render_summary(projection):
    return (
        f"\nGitHub change-control evidence: {projection['evidenceRef']}\n"
        f"Population: {projection['populationRef']}; selected sample: {projection['sampleRef']}.\n"
        f"Coverage: {projection['coverageStatus']}; unresolved production links: {projection['counts']['unresolvedProductionLinks']}.\n"
        "Review evidence alone does not establish a production change or compliance. Retained report explains exceptions and source limitations.\n"
    )
