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
    open, attach an artifact to it, and dispatch a recurring definition
    through run-now. Without a provider credential no model-backed step can
    stay in flight, so the deferred start is what the later phases cancel.
``cancel``
    Cancel the deferred execution before its start time and require it to
    reach the canceled terminal state.
``credential``
    Store a synthetic credential and bind it through the GitHub token
    setting. It runs after ``cancel`` so no execution uses the synthetic
    token.
``verify``
    Read back everything saved: credential metadata without plaintext, the
    setting binding and its redacted usage, the recurring definition and its
    dispatched run, each execution's retained terminal state, and the
    attached artifact bytes.
``release``
    Remove the setting override so later work does not use the synthetic
    token.

Stdlib only: it runs on the CI host, outside the application image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


class JourneyFailure(RuntimeError):
    """A journey outcome was not observed."""


class Api:
    def __init__(self, base: str, *, timeout: float = 30.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
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


def cancel(api: Api, state: dict[str, Any], *, timeout: float) -> None:
    executions = [item for item in state.get("executions") or [] if item.get("cancel")]
    if not executions:
        raise JourneyFailure("no executions recorded to cancel")
    for execution in executions:
        scheduled_for = execution.get("scheduledFor")
        if scheduled_for and datetime.fromisoformat(scheduled_for) <= datetime.now(
            timezone.utc
        ):
            raise JourneyFailure(
                f"execution {execution['workflowId']} reached its start time "
                "before cancellation; raise --defer-seconds"
            )
        workflow_id = execution["workflowId"]
        api.json(
            "POST",
            f"/api/executions/{quote(workflow_id)}/cancel",
            body={"action": "cancel", "reason": "single-user journey"},
            expect=(200, 202),
        )
    for execution in executions:
        wait_for_canceled(api, execution["workflowId"], timeout=timeout)
        execution["canceled"] = True
        log(f"execution {execution['workflowId']} canceled")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "phase", choices=("populate", "cancel", "credential", "verify", "release")
    )
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--label", default="fresh")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--defer-seconds", type=float, default=150.0)
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
        elif args.phase == "cancel":
            cancel(api, state, timeout=args.timeout)
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
