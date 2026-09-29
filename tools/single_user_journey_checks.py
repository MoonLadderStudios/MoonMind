#!/usr/bin/env python3
"""Single-user product journey checks against a running default install.

MoonLadderStudios/MoonMind#4356. ``tools/first_run_journey_3938.sh`` boots
``docker-compose.yaml`` and calls this helper against the published API. It
drives the real application over HTTP with no seeded person, dependency
override, or provider credential, and exits non-zero on the first failed,
missing, or unobserved outcome. It never reports a skipped step as passing.

Phases (state is carried in ``--state-file`` so a later phase, possibly on a
recreated stack, can verify what an earlier phase saved):

``populate``
    Read the instance settings and preset catalogs, submit one task with the
    dashboard's default repository as a deferred start, redeliver the same
    submission (lost acknowledgment), observe it durably recorded and still
    open, attach an artifact to it, dispatch a recurring definition through
    run-now, and save a preset. Without a provider credential no model-backed
    step can stay in flight, so the deferred start is what the dashboard
    later cancels (``tools/single_user_journey_browser.mjs ... cancel``).
``vector-free``
    Against the running candidate instance (MoonLadderStudios/MoonMind#4114):
    readiness with no pending migration, a settings catalog that wires no
    retired retrieval backend, and rejection of an explicit retired-retrieval
    submission before any effect. Runs after ``populate`` so its evidence
    accumulates in the same state file.
``canceled``
    Confirm the dashboard cancellation: each execution marked for
    cancellation must have been canceled through the dashboard before its
    start time and must reach the canceled terminal state.
``credential``
    Store a synthetic credential and bind it through the GitHub token
    setting. It runs after ``canceled`` so no execution uses the synthetic
    token.
``verify``
    Read back everything saved: credential metadata without plaintext, the
    setting binding and its redacted usage, the recurring definition and its
    dispatched run, each execution's retained terminal state, the attached
    artifact bytes, and the saved preset version.
``release``
    Remove the setting override so later work does not use the synthetic
    token.
``conversion``
    After an upgrade from an account-era release, read the API startup log
    (``--api-log``) and require the guarded single-user conversion to have
    classified the retained data as one eligible operator. It either
    published, or it refused only because subsystem transforms are not yet
    registered. That refusal is recorded and printed as not published. Any
    other refusal, a deferral, or no observed outcome fails.

Stdlib only: it runs on the CI host, outside the application image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SETTING_KEY = "integrations.github.token_ref"
TERMINAL_FAILURE = frozenset({"failed", "terminated", "timed_out", "timedout"})
CANCELED = frozenset({"canceled", "cancelled"})
COMPLETED = frozenset({"completed", "succeeded"})
# Startup log lines written by api_service.main._run_guarded_single_user_upgrade
# and the read-only conversion guard that follows it.
CONVERSION_OUTCOME = re.compile(
    r"Single-user guarded upgrade (?:"
    r"published \((?P<published>[a-z_]+)\)"
    r"|blocked \((?P<blocked>[a-z_]+)(?:: (?P<detail>[^)]*))?\)"
    r"|deferred: (?P<deferred>\w+))"
)
CONVERSION_GUARD = re.compile(
    r"Single-user conversion guard: disposition=(?P<disposition>[a-z_]+) "
    r"reason=(?P<reason>[a-z_]+)"
)
PENDING_TRANSFORMS = re.compile(r"lack registered transforms: (?P<names>[a-z_,]+)")


class JourneyFailure(RuntimeError):
    """A journey outcome was not observed."""


class Api:
    def __init__(self, base: str, *, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout
        # The disposable journey always targets the local Compose stack
        # derived from its published binding. Container-job and CI hosts
        # export an egress proxy; routing loopback journey traffic through
        # it can only fail, so this helper never honors proxy variables.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> tuple[int, bytes]:
        data = raw
        all_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            all_headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers=all_headers
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        except (urllib.error.URLError, OSError) as exc:
            raise JourneyFailure(f"{method} {path}: transport error {exc}") from exc
        if status not in expect:
            raise JourneyFailure(
                f"{method} {path}: HTTP {status} (expected {expect}): "
                f"{payload[:800].decode(errors='replace')}"
            )
        return status, payload

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        _, payload = self.request(method, path, **kwargs)
        try:
            return json.loads(payload or b"null")
        except json.JSONDecodeError as exc:
            raise JourneyFailure(f"{method} {path}: response is not JSON") from exc


def log(message: str) -> None:
    print(f"single-user-journey: {message}", flush=True)


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def execution_state(execution: dict[str, Any]) -> tuple[str, set[str]]:
    """Return the dashboard status and every status-like value, lowercased."""

    values = {
        str(execution.get(key) or "").strip().lower()
        for key in (
            "status",
            "state",
            "closeStatus",
            "temporalStatus",
            "dashboardStatus",
        )
    }
    values.discard("")
    return str(execution.get("status") or "").lower(), values


def describe(api: Api, workflow_id: str) -> dict[str, Any]:
    return api.json("GET", f"/api/executions/{quote(workflow_id)}")


def wait_for_deferred(api: Api, workflow_id: str, *, timeout: float) -> dict[str, Any]:
    """Wait until the deferred execution is durably recorded and still open."""

    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = describe(api, workflow_id)
        _, values = execution_state(last)
        if values & (TERMINAL_FAILURE | CANCELED | COMPLETED):
            raise JourneyFailure(
                f"deferred execution {workflow_id} closed before cancellation as "
                f"{sorted(values)}: {last.get('summary')!r}"
            )
        if last.get("scheduledFor") and last.get("runId"):
            log(
                f"execution {workflow_id} deferred until {last['scheduledFor']} "
                f"({last.get('status')})"
            )
            return last
        time.sleep(3)
    raise JourneyFailure(
        f"execution {workflow_id} was not recorded as deferred within "
        f"{timeout:.0f}s (last status {last.get('status')!r})"
    )


def wait_for_canceled(api: Api, workflow_id: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = describe(api, workflow_id)
        _, values = execution_state(last)
        if values & CANCELED:
            return last
        if values & (TERMINAL_FAILURE | COMPLETED):
            raise JourneyFailure(
                f"execution {workflow_id} closed as {sorted(values)} instead of "
                f"canceled: {last.get('summary')!r}"
            )
        time.sleep(3)
    raise JourneyFailure(
        f"execution {workflow_id} did not reach canceled within {timeout:.0f}s "
        f"(last status {last.get('status')!r})"
    )


def populate(
    api: Api,
    state: dict[str, Any],
    *,
    label: str,
    timeout: float,
    defer_seconds: float,
) -> None:
    catalog = api.json("GET", "/api/v1/settings/catalog")
    if not catalog:
        raise JourneyFailure("settings catalog is empty")
    presets = api.json("GET", "/api/presets")
    log(f"settings catalog and preset catalog readable ({type(presets).__name__})")
    # Submit with the repository the dashboard applies when the operator
    # leaves it blank, read from the deployment instead of declared here.
    ui_info = api.json("GET", "/api/ui/info")
    repository = str(
        ((ui_info.get("dashboardConfig") or {}).get("system") or {}).get(
            "defaultRepository"
        )
        or ""
    ).strip()
    if not repository:
        raise JourneyFailure("dashboard config exposes no default repository")

    # One task with the dashboard's default selections and no-publication
    # intent, deferred so it stays in flight without a provider credential:
    # Temporal holds the start and the cancellation below is observed on the
    # workflow's first task.
    scheduled_for = datetime.now(timezone.utc) + timedelta(seconds=defer_seconds)
    submission = {
        "workflowType": "MoonMind.UserWorkflow",
        "title": f"single-user journey {label}",
        "initialParameters": {
            "instructions": (
                "Single-user journey check: acknowledge this run in one short "
                "sentence."
            ),
            "repository": repository,
            "publishMode": "none",
        },
        "schedule": {"mode": "once", "scheduledFor": scheduled_for.isoformat()},
        "idempotencyKey": f"single-user-journey-{label}-{uuid.uuid4().hex}",
    }
    created = api.json("POST", "/api/executions", body=submission, expect=(200, 201))
    workflow_id = created.get("workflowId") or ""
    if not workflow_id:
        raise JourneyFailure(f"submission returned no workflowId: {created}")
    redelivered = api.json(
        "POST", "/api/executions", body=submission, expect=(200, 201)
    )
    if redelivered.get("workflowId") != workflow_id:
        raise JourneyFailure(
            "redelivered submission created a second execution "
            f"({redelivered.get('workflowId')!r} != {workflow_id!r})"
        )
    log(f"submitted {workflow_id}; identical redelivery reused it")
    deferred = wait_for_deferred(api, workflow_id, timeout=timeout)
    run_id = deferred.get("runId") or created.get("runId") or ""
    namespace = deferred.get("namespace") or "default"

    # Artifact attached to the execution, read back through its listing.
    content = f"single-user journey artifact {label} {uuid.uuid4().hex}\n".encode()
    created_artifact = api.json(
        "POST",
        "/api/artifacts",
        body={
            "content_type": "text/plain",
            "size_bytes": len(content),
            "link": {
                "namespace": namespace,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "link_type": "input.attachment",
                "label": f"single-user journey {label}",
            },
            "metadata": {"journey": label},
        },
        expect=(201,),
    )
    artifact_id = created_artifact["artifact_ref"]["artifact_id"]
    api.request(
        "PUT",
        f"/api/artifacts/{quote(artifact_id)}/content",
        raw=content,
        headers={"Content-Type": "text/plain"},
    )
    listing = api.json(
        "GET",
        f"/api/executions/{quote(namespace)}/{quote(workflow_id)}/{quote(run_id)}/artifacts",
    )
    if artifact_id not in json.dumps(listing):
        raise JourneyFailure(f"artifact {artifact_id} is not linked to {workflow_id}")
    state["artifact"] = {
        "artifactId": artifact_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "namespace": namespace,
        "workflowId": workflow_id,
        "runId": run_id,
    }
    log(f"artifact {artifact_id} attached to {workflow_id}")

    # Recurring definition dispatched through run-now.
    name = f"single-user journey {label}"
    definition = api.json(
        "POST",
        "/api/recurring-workflows",
        body={
            "name": name,
            "cron": "0 6 * * *",
            "timezone": "UTC",
            "target": {
                "workflowType": "MoonMind.UserWorkflow",
                "title": name,
                "initialParameters": {
                    "task": {"instructions": "Single-user journey recurring check."},
                    "repository": repository,
                    "publishMode": "none",
                },
            },
        },
        expect=(201,),
    )
    definition_id = str(definition["id"])
    request_id = str(uuid.uuid4())
    api.json(
        "POST",
        f"/api/recurring-workflows/{definition_id}/run",
        headers={"Idempotency-Key": request_id},
        expect=(201,),
    )
    dispatched = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not dispatched:
        runs = api.json("GET", f"/api/recurring-workflows/{definition_id}/runs")
        for run in runs.get("items") or []:
            if str(run.get("id")) == request_id and run.get("temporalWorkflowId"):
                dispatched = str(run["temporalWorkflowId"])
        if not dispatched:
            time.sleep(3)
    if not dispatched:
        raise JourneyFailure(
            f"recurring run {request_id} for {definition_id} was never dispatched"
        )
    describe(api, dispatched)
    log(f"recurring definition {definition_id} dispatched {dispatched}")

    state["recurring"] = {
        "definitionId": definition_id,
        "name": name,
        "runId": request_id,
        "workflowId": dispatched,
    }

    preset = api.json(
        "POST",
        "/api/presets",
        body={
            "slug": f"single-user-journey-{label}-{uuid.uuid4().hex[:8]}",
            "title": f"single-user journey preset {label}",
            "description": "Single-user journey preset read back after restarts.",
            "steps": [{"instructions": "Single-user journey preset step."}],
        },
        expect=(201,),
    )
    if not preset.get("slug"):
        raise JourneyFailure(f"preset creation returned no slug: {preset}")
    state["preset"] = preset_identity(preset)
    log(f"preset {preset['slug']} saved ({preset.get('presetDigest')})")
    state["executions"] = [
        {
            "workflowId": workflow_id,
            "title": submission["title"],
            "cancel": True,
            "scheduledFor": scheduled_for.isoformat(),
        },
        # Dispatch is the recurring outcome; without a provider credential the
        # dispatched run cannot complete, so it is observed, not canceled.
        {"workflowId": dispatched, "title": name, "cancel": False},
    ]


def preset_identity(preset: dict[str, Any]) -> dict[str, Any]:
    """The saved preset version the read-back must find unchanged."""

    return {
        "slug": preset.get("slug"),
        "scope": preset.get("scope"),
        "scopeRef": preset.get("scopeRef"),
        "title": preset.get("title"),
        "presetDigest": preset.get("presetDigest"),
        "steps": [step.get("instructions") for step in preset.get("steps") or []],
    }


def verify_preset(api: Api, state: dict[str, Any]) -> None:
    saved = state.get("preset")
    if not saved:
        raise JourneyFailure("no preset recorded to verify")
    query = urllib.parse.urlencode(
        {
            key: value
            for key, value in (
                ("scope", saved["scope"]),
                ("scopeRef", saved["scopeRef"]),
            )
            if value
        }
    )
    found = api.json("GET", f"/api/presets/{quote(saved['slug'])}?{query}")
    if preset_identity(found) != saved:
        raise JourneyFailure(
            f"preset {saved['slug']} changed: saved {saved}, found "
            f"{preset_identity(found)}"
        )
    log(f"preset {saved['slug']} retained at {saved['presetDigest']}")


def credential(api: Api, state: dict[str, Any], *, label: str) -> None:
    """Store a synthetic credential and bind it through an instance setting."""

    slug = f"single-user-journey-{label}"
    plaintext = f"ghp_synthetic_{uuid.uuid4().hex}"
    api.json(
        "POST",
        "/api/v1/secrets",
        body={"slug": slug, "plaintext": plaintext, "details": {"journey": label}},
        expect=(201,),
    )
    api.json(
        "PATCH",
        "/api/v1/settings/workspace",
        body={"changes": {SETTING_KEY: f"db://{slug}"}, "reason": "journey"},
    )
    state["credential"] = {"slug": slug, "plaintext": plaintext}
    log(f"synthetic credential {slug} stored and bound to {SETTING_KEY}")


def release(api: Api, state: dict[str, Any]) -> None:
    """Remove the setting override so later work does not use the synthetic token."""

    slug = state["credential"]["slug"]
    api.request(
        "DELETE",
        f"/api/v1/settings/workspace/{SETTING_KEY}",
        expect=(200, 204),
    )
    _, effective = api.request("GET", "/api/v1/settings/effective?scope=workspace")
    if f"db://{slug}".encode() in effective:
        raise JourneyFailure(f"{SETTING_KEY} still resolves to db://{slug}")
    state["credential"]["released"] = True
    log(f"{SETTING_KEY} override removed")


def canceled(api: Api, state: dict[str, Any], *, timeout: float) -> None:
    """Confirm the dashboard canceled each deferred execution before it started."""

    executions = [item for item in state.get("executions") or [] if item.get("cancel")]
    if not executions:
        raise JourneyFailure("no executions recorded to cancel")
    for execution in executions:
        workflow_id = execution["workflowId"]
        requested = execution.get("cancelRequestedAt")
        if not requested:
            raise JourneyFailure(
                f"execution {workflow_id} was not canceled through the dashboard"
            )
        scheduled_for = execution.get("scheduledFor")
        if scheduled_for and datetime.fromisoformat(
            scheduled_for
        ) <= datetime.fromisoformat(requested):
            raise JourneyFailure(
                f"execution {workflow_id} reached its start time before the "
                "dashboard canceled it; raise --defer-seconds"
            )
    for execution in executions:
        wait_for_canceled(api, execution["workflowId"], timeout=timeout)
        execution["canceled"] = True
        log(f"execution {execution['workflowId']} canceled through the dashboard")


# Vector-free startup posture (MoonLadderStudios/MoonMind#4114): the clean
# default and upgraded candidate instances run docker-compose.yaml with no
# vector configuration. This phase proves it against the running services:
# readiness with no pending migration, a settings catalog that wires no
# retired retrieval backend, and rejection of an explicit retired-retrieval
# submission before any effect. Any failed, missing, or unobserved step
# exits non-zero; admitting retired retrieval fails as a consequential
# delivery.
_RETIRED_CATALOG_TOKENS = (
    "qdrant",
    "followUpRetrieval",
    "follow_up_retrieval",
    "VECTOR_STORE_PROVIDER",
    "RAG_ENABLED",
)
_RETIRED_PROBE_RE = re.compile(r"4105|retired|vector", re.IGNORECASE)


def retired_execution_probe_body() -> dict[str, Any]:
    """Exact submission the live journey uses to probe retired retrieval."""
    return {
        "type": "task",
        "payload": {
            "task": {
                "instructions": (
                    "Vector-free startup probe: ordinary work needs no "
                    "retrieval backend."
                ),
                "rag": {"collections": ["docs"], "required": True},
                "idempotencyKey": f"vector-free-probe-{uuid.uuid4().hex}",
            }
        },
    }


def check_health_ready(health: Any) -> None:
    """Require a ready instance with no pending migration or setup."""
    if not isinstance(health, dict):
        raise JourneyFailure(f"/healthz response is not JSON: {health!r}")
    problems = [
        key
        for key, bad in (
            ("status", health.get("status") != "ok"),
            ("db", health.get("db") != "connected"),
            ("migration_required", bool(health.get("migration_required"))),
            ("setup_required", bool(health.get("setup_required"))),
        )
        if bad
    ]
    if problems:
        raise JourneyFailure(f"/healthz reports {problems}: {health}")


def check_catalog_vector_free(catalog: Any) -> None:
    """Require a settings catalog that wires no retired retrieval backend."""
    text = json.dumps(catalog).lower()
    for token in _RETIRED_CATALOG_TOKENS:
        if token.lower() in text:
            raise JourneyFailure(
                "settings catalog wires retired retrieval backend "
                f"({token})"
            )


def vector_free(api: Api, state: dict[str, Any], *, label: str) -> None:
    """Prove the running candidate instance started and stays vector-free."""
    health = api.json("GET", "/healthz")
    check_health_ready(health)
    log(f"healthz ok without vector wiring (uptime {health.get('uptime_seconds')}s)")
    catalog = api.json("GET", "/api/v1/settings/catalog")
    if not catalog:
        raise JourneyFailure("settings catalog is empty")
    check_catalog_vector_free(catalog)
    log("settings catalog wires no retired retrieval backend")

    try:
        status, payload = api.request(
            "POST",
            "/api/executions",
            body=retired_execution_probe_body(),
            expect=(422,),
        )
    except JourneyFailure as exc:
        raise JourneyFailure(
            f"retired retrieval probe was admitted or lost: {exc}"
        ) from exc
    text = payload.decode(errors="replace")
    if not _RETIRED_PROBE_RE.search(text):
        raise JourneyFailure(
            "retired retrieval probe was not rejected as retired "
            f"(HTTP {status}): {text[:800]}"
        )
    if "workflowId" in text:
        raise JourneyFailure(
            "retired retrieval probe created work before rejection"
        )
    log("explicit retired retrieval rejected before any effect")
    state["vector_free"] = {
        "label": label,
        "health": {
            "status": health.get("status"),
            "db": health.get("db"),
            "migration_required": bool(health.get("migration_required")),
        },
        "catalog": "vector-free",
        "retired_probe": {"status": status, "workflow_created": False},
    }


def conversion(state: dict[str, Any], *, api_log: Path) -> None:
    """Require an eligible guarded conversion outcome after an account-era upgrade."""

    text = api_log.read_text(errors="replace")
    outcomes = list(CONVERSION_OUTCOME.finditer(text))
    if not outcomes:
        raise JourneyFailure(
            f"no guarded single-user upgrade outcome in {api_log.name}"
        )
    latest = outcomes[-1]
    if latest["published"]:
        if latest["published"] != "eligible_conversion":
            raise JourneyFailure(
                "account-era data converted as "
                f"{latest['published']!r}, not as one eligible operator"
            )
        state["conversion"] = {
            "outcome": "published",
            "disposition": latest["published"],
        }
        log("guarded conversion published the eligible single-operator source")
        return
    if latest["deferred"]:
        raise JourneyFailure(f"guarded conversion deferred: {latest['deferred']}")
    reason = latest["blocked"]
    guards = list(CONVERSION_GUARD.finditer(text))
    eligible = bool(guards) and (
        guards[-1]["disposition"],
        guards[-1]["reason"],
    ) == ("eligible_conversion", "single_operator")
    pending = PENDING_TRANSFORMS.search(latest["detail"] or "")
    if reason != "missing_transform_coverage" or not eligible or not pending:
        raise JourneyFailure(
            f"guarded conversion refused ({reason}: {latest['detail'] or 'no detail'})"
        )
    names = sorted(name for name in pending["names"].split(",") if name)
    state["conversion"] = {
        "outcome": "blocked",
        "reason": reason,
        "pendingSubsystems": names,
    }
    log(
        "guarded conversion NOT published: the source is one eligible operator, "
        f"but subsystems {','.join(names)} have no registered transform; "
        "source data and operator access are preserved"
    )


def verify(api: Api, state: dict[str, Any]) -> None:
    credential = state.get("credential")
    if not credential:
        raise JourneyFailure("no credential recorded to verify")
    slug, plaintext = credential["slug"], credential["plaintext"]
    _, listed = api.request("GET", "/api/v1/secrets")
    if slug.encode() not in listed:
        raise JourneyFailure(f"credential {slug} is missing")
    if plaintext.encode() in listed:
        raise JourneyFailure("credential listing exposes plaintext")
    _, effective = api.request("GET", "/api/v1/settings/effective?scope=workspace")
    if f"db://{slug}".encode() not in effective:
        raise JourneyFailure(f"{SETTING_KEY} no longer resolves to db://{slug}")
    _, usage = api.request("GET", f"/api/v1/secrets/{quote(slug)}/usage")
    if SETTING_KEY.encode() not in usage or plaintext.encode() in usage:
        raise JourneyFailure(f"credential {slug} usage is missing or unredacted")
    log(f"credential {slug} and its setting binding retained without plaintext")

    recurring = state["recurring"]
    definition = api.json(
        "GET", f"/api/recurring-workflows/{recurring['definitionId']}"
    )
    if definition.get("name") != recurring["name"]:
        raise JourneyFailure(f"recurring definition changed: {definition}")
    runs = api.json("GET", f"/api/recurring-workflows/{recurring['definitionId']}/runs")
    if not any(
        str(run.get("id")) == recurring["runId"]
        and run.get("temporalWorkflowId") == recurring["workflowId"]
        for run in runs.get("items") or []
    ):
        raise JourneyFailure("recurring run history lost its dispatched execution")
    log(f"recurring definition {recurring['definitionId']} and run retained")

    for execution in state["executions"]:
        found = describe(api, execution["workflowId"])
        _, values = execution_state(found)
        if execution.get("canceled") and not values & CANCELED:
            raise JourneyFailure(
                f"execution {execution['workflowId']} lost its canceled state: "
                f"{sorted(values)}"
            )
    log("executions retained with their terminal state")

    artifact = state["artifact"]
    _, content = api.request(
        "GET", f"/api/artifacts/{quote(artifact['artifactId'])}/download"
    )
    if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
        raise JourneyFailure(f"artifact {artifact['artifactId']} bytes changed")
    log(f"artifact {artifact['artifactId']} bytes retained")
    verify_preset(api, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "phase",
        choices=(
            "populate",
            "vector-free",
            "canceled",
            "credential",
            "verify",
            "release",
            "conversion",
        ),
    )
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--label", default="fresh")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--defer-seconds", type=float, default=150.0)
    parser.add_argument("--api-log", type=Path)
    args = parser.parse_args(argv)

    api = Api(args.api_base)
    state: dict[str, Any] = {}
    if args.phase != "populate":
        state = json.loads(args.state_file.read_text())
    try:
        if args.phase == "populate":
            populate(
                api,
                state,
                label=args.label,
                timeout=args.timeout,
                defer_seconds=args.defer_seconds,
            )
        elif args.phase == "vector-free":
            vector_free(api, state, label=args.label)
        elif args.phase == "canceled":
            canceled(api, state, timeout=args.timeout)
        elif args.phase == "conversion":
            if args.api_log is None:
                raise JourneyFailure("conversion needs --api-log")
            conversion(state, api_log=args.api_log)
        elif args.phase == "credential":
            credential(api, state, label=args.label)
        elif args.phase == "verify":
            verify(api, state)
        else:
            release(api, state)
    except JourneyFailure as exc:
        print(f"single-user-journey: FAILED ({args.phase}): {exc}", file=sys.stderr)
        return 1
    finally:
        if state:
            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            args.state_file.write_text(json.dumps(state, indent=2))
    log(f"{args.phase} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
