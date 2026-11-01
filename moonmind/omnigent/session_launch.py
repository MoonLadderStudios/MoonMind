"""Prepare workflow session creation from durable launch authority."""

import hashlib
from collections.abc import Mapping
from typing import Any

import httpx

from moonmind.omnigent.bridge_store import (
    WORKFLOW_LAUNCH_DEFAULTS,
    WORKFLOW_LAUNCH_DEFAULTS_KEY,
    OmnigentBridgeSessionStore,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_agent_adapter import (
    OmnigentExecutionSelection,
    OmnigentResolvedTarget,
    build_omnigent_session_create_payload,
)
from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)

_MAX_LEGACY_SESSION_PAGES = 100


class OmnigentLaunchReconciliationError(OmnigentClientError):
    """An older create may exist; do not recommend a fresh execution key."""

    code = "omnigent_launch_reconciliation_failed"


async def _recover_legacy_session(
    client: OmnigentHttpClient, payload: Mapping[str, Any]
) -> str | None:
    """Find the actual pre-marker effect without replaying a guessed payload.

    Stock Omnigent's GET /v1/sessions exposes labels and cursor pagination.
    Read every page, including archived sessions, before accepting absence.
    Multiple matches, incomplete reads, and mismatched ownership fail closed.
    """
    expected = {
        key: payload["labels"][key]
        for key in ("moonmind.idempotency_key", "moonmind.correlation_id")
    }
    matches: set[str] = set()
    after = None
    seen_cursors: set[str] = set()
    for _ in range(_MAX_LEGACY_SESSION_PAGES):
        page = await client.list_sessions(agent_id=payload["agent_id"], after=after)
        if (
            not isinstance(page, Mapping)
            or not isinstance(page.get("data"), list)
            or not isinstance(page.get("has_more"), bool)
        ):
            raise OmnigentClientError(
                "Legacy session reconciliation returned an incomplete page"
            )
        for item in page["data"]:
            if not isinstance(item, Mapping):
                raise OmnigentClientError(
                    "Legacy session reconciliation returned an invalid session"
                )
            labels = item.get("labels")
            if not isinstance(labels, Mapping):
                raise OmnigentClientError(
                    "Legacy session reconciliation returned invalid labels"
                )
            if (
                labels.get("moonmind.idempotency_key")
                != expected["moonmind.idempotency_key"]
            ):
                continue
            session_id = str(item.get("id") or "").strip()
            if not session_id or any(
                labels.get(key) != value for key, value in expected.items()
            ):
                raise OmnigentClientError(
                    "Legacy session reconciliation found conflicting ownership"
                )
            matches.add(session_id)
        if page["has_more"] is False:
            break
        cursor = str(page.get("last_id") or "").strip()
        if not cursor or cursor in seen_cursors:
            raise OmnigentClientError(
                "Legacy session reconciliation pagination did not advance"
            )
        seen_cursors.add(cursor)
        after = cursor
    else:
        raise OmnigentClientError(
            "Legacy session reconciliation exceeded its page budget"
        )
    if len(matches) > 1:
        raise OmnigentClientError(
            "Legacy session reconciliation found multiple matching sessions"
        )
    if not matches:
        return None
    session_id = next(iter(matches))
    snapshot = await client.get_session(session_id)
    if not isinstance(snapshot, Mapping):
        raise OmnigentClientError("Recovered legacy session snapshot is incomplete")
    labels = snapshot.get("labels")
    if (
        not isinstance(labels, Mapping)
        or any(labels.get(key) != value for key, value in expected.items())
        or snapshot.get("agent_id") != payload["agent_id"]
        or snapshot.get("host_id") != payload.get("host_id")
    ):
        raise OmnigentClientError(
            "Recovered legacy session conflicts with launch authority"
        )
    return session_id


async def prepare_workflow_session_create(
    *,
    request: AgentExecutionRequest,
    selection: OmnigentExecutionSelection,
    target: OmnigentResolvedTarget,
    client: OmnigentHttpClient,
    bridge: Any,
    run_store: OmnigentBridgeSessionStore | None,
    provider_idempotency_key: str,
    session_authority_sink: Any | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Return the exact payload or a recovered provider session to attach."""
    metadata = dict(getattr(bridge, "metadata_", None) or {})
    defaults = (
        metadata.get(WORKFLOW_LAUNCH_DEFAULTS_KEY, {})
        if bridge is not None
        else WORKFLOW_LAUNCH_DEFAULTS
    )
    if not isinstance(defaults, Mapping) or any(
        not isinstance(args, list) or not all(isinstance(arg, str) for arg in args)
        for args in defaults.values()
    ):
        raise OmnigentLaunchReconciliationError(
            "Persisted workflow launch defaults are invalid"
        )
    payload = build_omnigent_session_create_payload(
        request=request, selection=selection, target=target, launch_defaults=defaults
    )
    payload["idempotency_key"] = provider_idempotency_key
    attached_id = str(getattr(bridge, "omnigent_session_id", None) or "").strip()
    phases = (
        getattr(getattr(session_authority_sink, "binding", None), "phaseResults", None)
        or {}
    )
    continuation = phases.get("runtimeContextContinuation")
    if continuation is not None:
        return payload, await _prepare_context_fork(
            payload=payload,
            continuation=continuation,
            client=client,
            sink=session_authority_sink,
            bridge_session_id=attached_id,
            run_store=run_store,
            idempotency_key=request.idempotency_key,
        )
    if attached_id:
        return payload, attached_id
    needs_legacy_reconciliation = (
        bridge is not None
        and WORKFLOW_LAUNCH_DEFAULTS_KEY not in metadata
        and selection.agent.harness_override == "claude-native"
        and not any(
            arg.split("=", 1)[0]
            in {"--permission-mode", "--dangerously-skip-permissions"}
            for arg in selection.session.terminal_launch_args
        )
    )
    if needs_legacy_reconciliation:
        recorded_agent = str(getattr(bridge, "omnigent_agent_id", None) or "").strip()
        if recorded_agent and recorded_agent != target.agent_id:
            raise OmnigentLaunchReconciliationError(
                "Legacy session reconciliation target differs from the saved agent"
            )
        try:
            recovered_id = await _recover_legacy_session(client, payload)
        except (OmnigentClientError, httpx.HTTPError) as exc:
            raise OmnigentLaunchReconciliationError(str(exc)) from exc
        if recovered_id:
            return payload, recovered_id
        if run_store is None:
            raise OmnigentClientError(
                "Legacy launch reconciliation requires a durable bridge store"
            )
        bridge = await run_store.freeze_workflow_launch_defaults(
            request.idempotency_key
        )
        attached_id = str(getattr(bridge, "omnigent_session_id", None) or "").strip()
        if attached_id:
            return payload, attached_id
        payload = build_omnigent_session_create_payload(
            request=request,
            selection=selection,
            target=target,
            launch_defaults=bridge.metadata_[WORKFLOW_LAUNCH_DEFAULTS_KEY],
        )
        payload["idempotency_key"] = provider_idempotency_key
    return payload, None


async def _find_context_fork(client, *, agent_id, title, source_id):
    after, cursors, matches = None, set(), set()
    for _ in range(_MAX_LEGACY_SESSION_PAGES):
        page = await client.list_sessions(agent_id=agent_id, after=after)
        if (
            not isinstance(page, Mapping)
            or not isinstance(page.get("data"), list)
            or not isinstance(page.get("has_more"), bool)
        ):
            raise OmnigentLaunchReconciliationError("Context fork lookup is incomplete")
        for item in page["data"]:
            if not isinstance(item, Mapping):
                raise OmnigentLaunchReconciliationError(
                    "Context fork lookup contains an invalid session"
                )
            if "title" not in item:
                raise OmnigentLaunchReconciliationError(
                    "Context fork lookup omitted session titles"
                )
            if item.get("title") != title:
                continue
            labels = item.get("labels") or {}
            if not isinstance(labels, Mapping):
                raise OmnigentLaunchReconciliationError(
                    "Context fork lookup has invalid lineage"
                )
            if (
                item.get("agent_id") != agent_id
                or labels.get("omnigent.fork.source_id") != source_id
                or not item.get("id")
            ):
                raise OmnigentLaunchReconciliationError(
                    "Context fork lookup conflicts with its source authority"
                )
            matches.add(item["id"])
        if not page["has_more"]:
            break
        cursor = page.get("last_id")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise OmnigentLaunchReconciliationError(
                "Context fork pagination did not advance"
            )
        cursors.add(cursor)
        after = cursor
    else:
        raise OmnigentLaunchReconciliationError(
            "Context fork lookup exceeded its page budget"
        )
    if len(matches) > 1:
        raise OmnigentLaunchReconciliationError(
            "Context fork lookup found multiple effects"
        )
    return next(iter(matches), None)


async def _prepare_context_fork(
    *,
    payload,
    continuation,
    client,
    sink,
    bridge_session_id,
    run_store,
    idempotency_key,
):
    """Reconcile one native fork under the existing immutable phase journal."""
    source_id = continuation["sourceSessionId"]
    title = (
        "MoonMind context "
        + hashlib.sha256(sink.binding.bindingId.encode()).hexdigest()[:40]
    )
    phases = sink.binding.phaseResults or {}
    session_id = bridge_session_id or getattr(sink.binding, "omnigentSessionId", None)
    if not session_id:
        session_id = await _find_context_fork(
            client, agent_id=payload["agent_id"], title=title, source_id=source_id
        )
    if not session_id:
        if "runtimeContextForkIntent" in phases:
            raise OmnigentLaunchReconciliationError(
                "Context fork delivery is unconfirmed; refusing to repeat it"
            )
        await sink.record_phase(
            "runtimeContextForkIntent", {"sourceSessionId": source_id, "title": title}
        )
        fork_payload = {
            key: payload[key]
            for key in (
                "agent_id",
                "model_override",
                "reasoning_effort",
                "terminal_launch_args",
            )
            if key in payload
        }
        fork_payload.update(title=title, host_type="external")
        for setting in ("model_override", "reasoning_effort"):
            fork_payload.setdefault(setting, "default")
        created = await client.fork_session(source_id, fork_payload)
        session_id = created.get("id")
        if not isinstance(session_id, str) or not session_id or session_id == source_id:
            raise OmnigentLaunchReconciliationError(
                "Context fork returned no new session identity"
            )
    # Persist ownership before runner launch, so cleanup and retries know the
    # exact effect even if metadata binding or the launch acknowledgement fails.
    await sink.session_created(session_id)
    if run_store is not None and not bridge_session_id:
        await run_store.attach_session(idempotency_key, session_id)
    snapshot = await client.get_session(session_id)
    labels = snapshot.get("labels") or {}
    if not isinstance(labels, Mapping):
        raise OmnigentLaunchReconciliationError(
            "Context fork has invalid native lineage"
        )
    if (
        snapshot.get("agent_id") != payload["agent_id"]
        or labels.get("omnigent.fork.source_id") != source_id
        or labels.get("omnigent.fork.carry_history") != "1"
    ):
        raise OmnigentLaunchReconciliationError(
            "Context fork has no native history authority"
        )
    changes = {"labels": payload["labels"], "silent": True}
    if "collaboration_mode" in payload:
        changes["collaboration_mode"] = payload["collaboration_mode"]
    await client.update_session(session_id, changes)
    host_id, workspace = payload["host_id"], payload["workspace"]
    bound = (sink.binding.phaseResults or {}).get("runtimeContextForkBound")
    if bound is not None:
        if (bound["sessionId"], bound["hostId"], bound["workspace"]) != (
            session_id,
            host_id,
            workspace,
        ):
            raise OmnigentLaunchReconciliationError(
                "Context fork runner conflicts with its admitted host"
            )
        return session_id
    runner_id = snapshot.get("runner_id")
    if runner_id:
        if snapshot.get("host_id") != host_id:
            raise OmnigentLaunchReconciliationError(
                "Context fork runner ownership is unconfirmed"
            )
    else:
        if "runtimeContextRunnerIntent" in (sink.binding.phaseResults or {}):
            raise OmnigentLaunchReconciliationError(
                "Context runner delivery is unconfirmed; refusing to repeat it"
            )
        await sink.record_phase(
            "runtimeContextRunnerIntent",
            {"sessionId": session_id, "hostId": host_id, "workspace": workspace},
        )
        launched = await client.launch_runner(
            host_id, session_id=session_id, workspace=workspace
        )
        runner_id = launched.get("runner_id")
        if not isinstance(runner_id, str) or not runner_id:
            raise OmnigentLaunchReconciliationError(
                "Context fork runner acknowledgement is incomplete"
            )
    await sink.record_phase(
        "runtimeContextForkBound",
        {
            "sessionId": session_id,
            "hostId": host_id,
            "workspace": workspace,
            "runnerId": runner_id,
        },
    )
    return session_id
