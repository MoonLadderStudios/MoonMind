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
    Read the instance settings and preset catalogs, submit one scratch task
    (the old release in an upgrade gets its dashboard's default repository)
    as a deferred start, redeliver the same
    submission (lost acknowledgment), observe it durably recorded and still
    open, attach an artifact to it, dispatch a recurring definition through
    run-now, and save a preset. Without a provider credential no model-backed
    step can stay in flight, so the deferred start is what the dashboard
    later cancels (``tools/single_user_journey_browser.mjs ... cancel``).
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
``vector_free``
    MoonLadderStudios/MoonMind#4114: prove the running candidate starts and
    stays vector-free with no vector configuration. Read live ``/healthz``
    (no key at any depth naming a retired vector backend), read live
    ``/openapi.json`` (no vector-backend path), and submit one uniquely
    marked task-envelope request carrying an explicit retired vector
    requirement (see ``vector_free_retired_probe``). That submission must be
    rejected with 422 carrying the structured #4105 retirement diagnostic,
    return no execution identity, and leave no new marked execution in the
    execution list. The ordinary vector-free work
    itself is exercised by ``populate`` on the same instance; this phase
    adds only the retirement-boundary proof.
``conversion``
    After an upgrade from an account-era release, read the API startup log
    (``--api-log``) and require the guarded single-user conversion to have
    classified the retained data as one eligible operator. It either
    published, or it refused only because subsystem transforms are not yet
    registered. That refusal is recorded and printed as not published. Any
    other refusal, a deferral, or no observed outcome fails.
``controller_absent``
    MoonLadderStudios/MoonMind#4502, before a deployment controller is
    installed: Settings Operations reports it not installed, and one update
    submission is refused with the host repair route instead of starting
    another updater. No workflow-backed update is created; existing
    workflow-backed history is recorded as read-only.
``controller_ready``
    After the journey starts the standalone controller: Settings Operations
    must report it installed and reachable, with no controller operation yet
    and no new workflow-backed update.
``controller``
    After the dashboard submitted, reloaded, and retried
    (``tools/single_user_journey_browser.mjs ... controller``): the dashboard's
    submission and retry were answered by the controller with no workflow,
    the reload reconnected to the same operation, exactly one controller
    operation owns the stack, its retry started a fresh attempt group while
    keeping the first failure, and no new workflow-backed update exists.

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
DEPLOYMENT_STACK = "moonmind"
DEPLOYMENT_STACK_PATH = f"/api/v1/operations/deployment/stacks/{DEPLOYMENT_STACK}"


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


def journey_submission(
    *,
    title: str,
    scheduled_for: datetime,
    idempotency_key: str,
) -> dict[str, Any]:
    """The deferred task ``populate`` submits.

    MoonLadderStudios/MoonMind#3935: it uses the Workflow Create task envelope
    and leaves the runtime and profiles to the deployment defaults, so the
    server resolves them and persists the Omnigent execution plan before
    scheduling, as it does for the dashboard. A clean install has no
    repository connection, and routed repository access never downgrades to
    anonymous, so the task saves scratch results in MoonMind. The unit suite
    posts this exact payload through the production create route.
    """

    return {
        "type": "task",
        "payload": {
            "publishMode": "none",
            "task": {
                "title": title,
                "instructions": (
                    "Single-user journey check: acknowledge this run in one "
                    "short sentence."
                ),
            },
            "schedule": {"mode": "once", "scheduledFor": scheduled_for.isoformat()},
            "idempotencyKey": idempotency_key,
        },
    }


def pre_upgrade_submission(
    *,
    title: str,
    repository: str,
    scheduled_for: datetime,
    idempotency_key: str,
) -> dict[str, Any]:
    """The deferred task ``populate`` saves on the release being upgraded.

    The upgrade journey proves that work saved by the old release survives the
    update, so it uses the UserWorkflow request that release accepts.
    """

    return {
        "workflowType": "MoonMind.UserWorkflow",
        "title": title,
        "initialParameters": {
            "instructions": (
                "Single-user journey check: acknowledge this run in one short "
                "sentence."
            ),
            "repository": repository,
            "publishMode": "none",
        },
        "schedule": {"mode": "once", "scheduledFor": scheduled_for.isoformat()},
        "idempotencyKey": idempotency_key,
    }


def populate(
    api: Api,
    state: dict[str, Any],
    *,
    label: str,
    timeout: float,
    defer_seconds: float,
    pre_upgrade_release: bool = False,
) -> None:
    catalog = api.json("GET", "/api/v1/settings/catalog")
    if not catalog:
        raise JourneyFailure("settings catalog is empty")
    presets = api.json("GET", "/api/presets")
    log(f"settings catalog and preset catalog readable ({type(presets).__name__})")
    repository: str | None = None
    if pre_upgrade_release:
        # The old release accepted the repository its dashboard applies when
        # the operator leaves it blank, read from the deployment.
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
    title = f"single-user journey {label}"
    idempotency_key = f"single-user-journey-{label}-{uuid.uuid4().hex}"
    if repository:
        submission = pre_upgrade_submission(
            title=title,
            repository=repository,
            scheduled_for=scheduled_for,
            idempotency_key=idempotency_key,
        )
    else:
        submission = journey_submission(
            title=title,
            scheduled_for=scheduled_for,
            idempotency_key=idempotency_key,
        )
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
                    **({"repository": repository} if repository else {}),
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
            "title": title,
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


_VECTOR_SERVICE_NAME_RE = re.compile(
    r"qdrant|milvus|vector[-_ ]?(db|store|service|index)|embeddings?|pgvector",
    re.IGNORECASE,
)


#: The #4105 retirement diagnostic raised by ``reject_retired_vector_fields``
#: for the probe's ``payload.rag`` requirement. The probe carries none of this
#: text, so an echoed-input validation error cannot satisfy it.
RETIRED_VECTOR_DIAGNOSTIC = (
    "payload.rag has been retired (MoonLadderStudios/MoonMind#4105)"
)
EXECUTION_IDENTITY_KEYS = frozenset(
    {"workflowId", "workflow_id", "workflowid", "runId", "run_id"}
)


def vector_free_retired_probe(marker: str = "") -> dict[str, Any]:
    """Task-envelope payload carrying an explicit retired vector requirement.

    MoonLadderStudios/MoonMind#4114: the shape mirrors the hermetic
    rejection contract (``reject_retired_vector_fields`` from #4105) so the
    live ``POST /api/executions`` request boundary must reject it with 422
    before scheduling. The unit suite imports this exact payload and asserts
    the production admission path raises (4105); the live phase below
    asserts the same over HTTP with no consequential execution identity.
    ``marker`` makes the probe's instructions unique so a persisted
    execution can be found afterwards.
    """

    instructions = "vector-free journey retired probe"
    if marker:
        instructions = f"{instructions} {marker}"
    return {
        "type": "workflow",
        "payload": {
            "rag": {"collections": ["docs"], "required": True},
            "workflow": {
                "instructions": instructions,
                "steps": [
                    {
                        "id": "step-1",
                        "title": "Step",
                        "type": "skill",
                        "skill": {
                            "id": "noop",
                            "inputs": {},
                            "inputContractDigest": "sha256:saved",
                        },
                    }
                ],
            },
        },
    }


def _retired_backend_keys(value: Any, path: str = "") -> list[str]:
    """Return every mapping key, at any depth, naming a retired backend."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if _VECTOR_SERVICE_NAME_RE.search(str(key)):
                found.append(key_path)
            found.extend(_retired_backend_keys(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_retired_backend_keys(item, f"{path}[{index}]"))
    return found


def _execution_identities(value: Any) -> list[str]:
    """Return execution identity values at any depth of an error response."""

    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in EXECUTION_IDENTITY_KEYS and item:
                found.append(f"{key}={item!r}")
            else:
                found.extend(_execution_identities(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_execution_identities(item))
    return found


def list_executions(api: Api) -> dict[str, dict[str, Any]]:
    """Return every listed execution keyed by workflow id, all pages."""

    executions: dict[str, dict[str, Any]] = {}
    token = ""
    for _ in range(50):
        path = "/api/executions?pageSize=200"
        if token:
            path += f"&nextPageToken={quote(token)}"
        page = api.json("GET", path)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise JourneyFailure(f"execution list is not readable: {page!r}")
        for item in page["items"]:
            if isinstance(item, dict) and item.get("workflowId"):
                executions[str(item["workflowId"])] = item
        token = str(page.get("nextPageToken") or "")
        if not token:
            return executions
    raise JourneyFailure("execution list did not finish paginating")


def vector_free(api: Api, state: dict[str, Any]) -> None:
    """Prove the running instance is vector-free at its live boundaries."""

    _, raw_health = api.request("GET", "/healthz", expect=(200,))
    try:
        health = json.loads(raw_health or b"null")
    except json.JSONDecodeError as exc:
        raise JourneyFailure("/healthz response is not JSON") from exc
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise JourneyFailure(f"/healthz is not healthy: {health!r}")
    retired_keys = _retired_backend_keys(health)
    if retired_keys or any(
        key in json.dumps(health).lower()
        for key in ("qdrant", "vector_store", "vector-store", "vectorstore")
    ):
        raise JourneyFailure(
            f"/healthz wires a retired vector backend {retired_keys}: {health!r}"
        )
    log("healthz ok with no vector backend")

    _, raw_spec = api.request("GET", "/openapi.json", expect=(200,))
    try:
        spec = json.loads(raw_spec or b"null")
    except json.JSONDecodeError as exc:
        raise JourneyFailure("/openapi.json response is not JSON") from exc
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    if not isinstance(paths, dict) or not paths:
        raise JourneyFailure("/openapi.json exposes no paths")
    offenders = [path for path in paths if _VECTOR_SERVICE_NAME_RE.search(str(path))]
    if offenders:
        raise JourneyFailure(
            f"served API contract exposes vector-backend paths: {offenders}"
        )
    log(f"openapi ok with no vector-backend path ({len(paths)} paths)")

    marker = f"vector-free-probe-{uuid.uuid4().hex}"
    before = list_executions(api)
    _, raw_error = api.request(
        "POST",
        "/api/executions",
        body=vector_free_retired_probe(marker),
        expect=(422,),
    )
    try:
        error = json.loads(raw_error or b"null")
    except json.JSONDecodeError:
        error = None
    detail = error.get("detail") if isinstance(error, dict) else None
    if not (
        isinstance(detail, dict)
        and detail.get("code") == "invalid_execution_request"
        and RETIRED_VECTOR_DIAGNOSTIC in str(detail.get("message") or "")
    ):
        raise JourneyFailure(
            "retired vector submission was rejected without the retirement "
            f"diagnostic: {(raw_error or b'').decode(errors='replace')[:800]!r}"
        )
    identities = _execution_identities(error)
    if identities:
        raise JourneyFailure(
            "retired vector submission created an execution identity "
            f"despite rejection: {identities}"
        )
    after = list_executions(api)
    persisted = [
        workflow_id
        for workflow_id, item in after.items()
        if workflow_id not in before
        and marker in json.dumps(item) + json.dumps(describe(api, workflow_id))
    ]
    if persisted:
        raise JourneyFailure(
            f"retired vector submission persisted executions despite rejection: "
            f"{persisted}"
        )
    state["vector_free"] = {
        "healthz": "ok",
        "openapiPaths": len(paths),
        "retiredRejected": True,
    }
    log("retired vector submission rejected with no execution created")


def _stack_actions(api: Api) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stack = api.json("GET", DEPLOYMENT_STACK_PATH)
    actions = stack.get("recentActions") if isinstance(stack, dict) else None
    if not isinstance(actions, list):
        raise JourneyFailure(f"Settings Operations stack state is not readable: {stack!r}")
    return stack, [action for action in actions if isinstance(action, dict)]


def _workflow_rows(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [action for action in actions if action.get("owner") != "controller"]


def _workflow_history(actions: list[dict[str, Any]]) -> list[str]:
    return sorted(str(row.get("runDetailUrl")) for row in _workflow_rows(actions))


def _require_no_new_workflow_updates(
    state: dict[str, Any], actions: list[dict[str, Any]]
) -> None:
    """Workflow-backed rows stay read-only history, never a revived engine."""

    recorded = state.get("workflowHistory")
    if not isinstance(recorded, list):
        raise JourneyFailure("no controller_absent observation was recorded")
    urls = _workflow_history(actions)
    if urls != sorted(recorded):
        raise JourneyFailure(
            "Settings Operations lists workflow-backed updates other than the "
            f"recorded history ({urls} != {sorted(recorded)}); a workflow was created"
        )


def controller_absent(api: Api, state: dict[str, Any], *, label: str) -> None:
    """Without a controller the dashboard reports the host repair route."""

    stack, actions = _stack_actions(api)
    controller_state = stack.get("controller") or {}
    if controller_state.get("installed"):
        raise JourneyFailure(
            f"the controller is already installed before the journey installs it: {controller_state!r}"
        )
    history = _workflow_history(actions)
    targets = api.json(
        "GET", f"/api/v1/operations/deployment/image-targets?stack={DEPLOYMENT_STACK}"
    )
    repositories = targets.get("repositories") if isinstance(targets, dict) else None
    if not repositories:
        raise JourneyFailure(f"Settings Operations offers no image target: {targets!r}")
    repository = str(repositories[0]["repository"])
    status, payload = api.request(
        "POST",
        "/api/v1/operations/deployment/update",
        body={
            "stack": DEPLOYMENT_STACK,
            "image": {
                "repository": repository,
                "reference": f"journey-absent-{label}-{uuid.uuid4().hex[:8]}",
            },
            "mode": "changed_services",
            "reason": f"single-user journey {label}: no controller installed",
        },
        expect=(202, 503),
    )
    answer = json.loads(payload or b"null")
    if status == 202:
        raise JourneyFailure(
            "without an installed controller Settings Operations accepted an "
            f"update through another updater (workflow fallback): {answer!r}"
        )
    detail = answer.get("detail") if isinstance(answer, dict) else None
    if not isinstance(detail, dict) or (
        detail.get("code") != "deployment_controller_not_installed"
        or detail.get("repairCommand") != "./tools/update-moonmind.sh"
    ):
        raise JourneyFailure(
            f"the refused update did not name the host repair route: {answer!r}"
        )
    _, after = _stack_actions(api)
    state["workflowHistory"] = history
    _require_no_new_workflow_updates(state, after)
    # The dashboard later submits this unpublished tag to the controller.
    state["controller"] = {
        "repository": repository,
        "reference": f"journey-controller-{label}-{uuid.uuid4().hex[:8]}",
    }
    log("Settings Operations refused the update with the host repair route")


def controller_ready(api: Api, state: dict[str, Any], *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    controller: dict[str, Any] = {}
    while time.monotonic() < deadline:
        stack, actions = _stack_actions(api)
        controller = stack.get("controller") or {}
        if controller.get("installed") and controller.get("reachable"):
            if any(action.get("owner") == "controller" for action in actions):
                raise JourneyFailure(
                    "the controller already owns an operation before the "
                    "dashboard submitted one"
                )
            _require_no_new_workflow_updates(state, actions)
            log("Settings Operations reaches the installed controller")
            return
        time.sleep(3)
    raise JourneyFailure(
        "Settings Operations never reported the installed controller as "
        f"reachable: {controller!r}"
    )


def _require_controller_answer(name: str, answer: Any, operation_id: str) -> None:
    if not isinstance(answer, dict) or answer.get("owner") != "controller":
        raise JourneyFailure(f"the dashboard {name} was not answered by the controller: {answer!r}")
    if answer.get("operationId") != operation_id:
        raise JourneyFailure(
            f"the dashboard {name} named operation {answer.get('operationId')!r}, "
            f"not {operation_id!r}"
        )
    if answer.get("workflowId") or answer.get("taskId"):
        raise JourneyFailure(
            f"the dashboard {name} created workflow {answer.get('workflowId')!r} "
            "for a controller operation"
        )


def controller(api: Api, state: dict[str, Any]) -> None:
    """Confirm the dashboard journey left one controller-owned operation."""

    record = state.get("controller") or {}
    operation_id = str(record.get("operationId") or "")
    if not operation_id.startswith("ui-"):
        raise JourneyFailure(
            f"the dashboard recorded no Settings Operations controller operation: {record!r}"
        )
    _require_controller_answer("submission", record.get("submission"), operation_id)
    _require_controller_answer("retry", record.get("retry"), operation_id)
    if record.get("reloadedOperationId") != operation_id:
        raise JourneyFailure(
            f"the dashboard reload did not reconnect to operation {operation_id}"
        )
    if record.get("dashboardSubmissions") != 1:
        raise JourneyFailure(
            f"the dashboard submitted {record.get('dashboardSubmissions')} updates; "
            "reconnecting must not submit again"
        )
    stack, actions = _stack_actions(api)
    controller_state = stack.get("controller") or {}
    if not (controller_state.get("installed") and controller_state.get("reachable")):
        raise JourneyFailure(f"the controller is no longer reachable: {controller_state!r}")
    owned = [action for action in actions if action.get("owner") == "controller"]
    if [action.get("operationId") for action in owned] != [operation_id]:
        raise JourneyFailure(
            "expected one mutation owner, the dashboard's operation "
            f"{operation_id}; the controller lists "
            f"{[action.get('operationId') for action in owned]}"
        )
    operation = owned[0]
    requested = f"{record['repository']}:{record['reference']}"
    if operation.get("requestedImage") != requested:
        raise JourneyFailure(
            f"operation {operation_id} targets {operation.get('requestedImage')!r}, "
            f"not the dashboard's {requested!r}"
        )
    if operation.get("status") != "FAILED" or operation.get("installedImage"):
        raise JourneyFailure(
            f"operation {operation_id} reports {operation.get('status')} with "
            f"installed {operation.get('installedImage')!r}; a controller without "
            "Docker cannot have installed anything"
        )
    if not str(operation.get("errorSummary") or "").startswith("attempt 1:"):
        raise JourneyFailure(
            f"the retry lost the first failure: {operation.get('errorSummary')!r}"
        )
    if int(operation.get("attemptGroup") or 0) < 2:
        raise JourneyFailure(
            f"the retry of {operation_id} started no fresh attempt group "
            f"(attempt group {operation.get('attemptGroup')!r})"
        )
    _require_no_new_workflow_updates(state, actions)
    log(
        f"controller operation {operation_id} is the only mutation owner; retry "
        "kept the first failure and no workflow-backed update was created"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "phase",
        choices=(
            "populate",
            "canceled",
            "credential",
            "verify",
            "release",
            "vector_free",
            "conversion",
            "controller_absent",
            "controller_ready",
            "controller",
        ),
    )
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--label", default="fresh")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--defer-seconds", type=float, default=150.0)
    parser.add_argument("--api-log", type=Path)
    parser.add_argument(
        "--pre-upgrade-release",
        action="store_true",
        help="populate the release an upgrade starts from with its own request",
    )
    args = parser.parse_args(argv)

    api = Api(args.api_base)
    state: dict[str, Any] = {}
    # These phases start a journey's state; every later phase reads it.
    if args.phase not in {"populate", "controller_absent"}:
        state = json.loads(args.state_file.read_text())
    try:
        if args.phase == "populate":
            populate(
                api,
                state,
                label=args.label,
                timeout=args.timeout,
                defer_seconds=args.defer_seconds,
                pre_upgrade_release=args.pre_upgrade_release,
            )
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
        elif args.phase == "vector_free":
            vector_free(api, state)
        elif args.phase == "controller_absent":
            controller_absent(api, state, label=args.label)
        elif args.phase == "controller_ready":
            controller_ready(api, state, timeout=args.timeout)
        elif args.phase == "controller":
            controller(api, state)
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
