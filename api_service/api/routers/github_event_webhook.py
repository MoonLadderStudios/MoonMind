"""Unauthenticated GitHub webhook ingress for one opt-in event path (#3967).

Shares the existing API process but not its operator authentication: the
``X-Hub-Signature-256`` signature over the exact bounded raw body is the
credential, resolved against the existing Managed Secrets owner. Default
local-only operation is unchanged — with no webhook secret configured or no
trigger opted in, every delivery is refused before any paid effect and no
new port or public ingress is opened by this router.

Flow per delivery: bound the raw body, verify the signature before parsing,
coerce delivery facts, insert the durable receipt first, recheck current
trigger authority (revocation) before dispatch, then dispatch once through
the existing Temporal execution service. Redeliveries reuse the stored
logical request; changed-body duplicates conflict; dispatch failures stay
``admitted_pending`` so a later redelivery re-attempts under the same
stable identity key instead of minting duplicate work.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.db.models import (
    GitHubEventDeliveryReceipt,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.github_event_dispatch import (
    EventExecutionDispatcher,
    TemporalEventExecutionDispatcher,
    build_dispatch_parameters,
)
from api_service.services.secrets import SecretsService
from moonmind.workflows.adapters.github_event_delivery import (
    MAX_WEBHOOK_BODY_BYTES,
    EventTriggerConfig,
    StoredReceipt,
    decide_delivery,
    delivery_from_webhook_payload,
    delivery_key,
    load_trigger_configs,
    payload_digest,
    resolve_trigger,
    verify_webhook_signature,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["GitHubEvents"])

_SECRET_SLUG_ENV_VAR = "MOONMIND_GITHUB_WEBHOOK_SECRET_SLUG"
_TRIGGER_CONFIG_ENV_VAR = "MOONMIND_GITHUB_EVENT_TRIGGERS"
_DEFAULT_SECRET_SLUG = "github-webhook-secret"


@dataclass
class WebhookSettings:
    """Deployment wiring for the event ingress (no secret material)."""

    secret_slug: str = _DEFAULT_SECRET_SLUG
    trigger_configs: tuple[EventTriggerConfig, ...] = ()
    raw_trigger_configs: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        raw_mappings = list(self.raw_trigger_configs or ())
        inline_configs = [
            item
            for item in (self.trigger_configs or ())
            if isinstance(item, EventTriggerConfig)
        ]
        inline_mappings = [
            item
            for item in (self.trigger_configs or ())
            if not isinstance(item, EventTriggerConfig)
        ]
        self.trigger_configs = tuple(inline_configs) + load_trigger_configs(
            [*raw_mappings, *inline_mappings]
        )

    @classmethod
    def from_env(cls) -> WebhookSettings:
        """Read deployment wiring from the environment (disabled by default)."""
        slug = (
            os.environ.get(_SECRET_SLUG_ENV_VAR, "").strip() or _DEFAULT_SECRET_SLUG
        )
        raw_text = os.environ.get(_TRIGGER_CONFIG_ENV_VAR, "").strip()
        raw_configs: list[dict[str, Any]] = []
        if raw_text:
            try:
                parsed = json.loads(raw_text)
            except ValueError as exc:
                raise ValueError(
                    f"{_TRIGGER_CONFIG_ENV_VAR} is not valid JSON"
                ) from exc
            if not isinstance(parsed, list):
                raise ValueError(f"{_TRIGGER_CONFIG_ENV_VAR} must be a JSON list")
            raw_configs = parsed
        return cls(secret_slug=slug, raw_trigger_configs=tuple(raw_configs))


def get_webhook_settings() -> WebhookSettings:
    """Return current deployment wiring (re-read per request)."""
    return WebhookSettings.from_env()


def get_settings_loader() -> Callable[[], WebhookSettings]:
    """Return the settings loader for pre-dispatch revocation rechecks."""
    return WebhookSettings.from_env


async def resolve_webhook_secrets(
    session: AsyncSession = Depends(get_async_session),
    settings: WebhookSettings = Depends(get_webhook_settings),
) -> dict[str, bytes]:
    """Resolve every candidate webhook secret (global + per-trigger slugs).

    A trigger's documented ``webhook_secret_slug`` is honored: a delivery
    verifies against any configured secret, so trigger-scoped rotation never
    breaks while the global secret still verifies, and a trigger-specific
    secret is never silently ignored. Only slugs are configured here; secret
    material never leaves the Secrets owner except into verification.
    """
    slugs: list[str] = [settings.secret_slug]
    for trigger in settings.trigger_configs or ():
        slug = (trigger.webhook_secret_slug or "").strip()
        if slug and slug not in slugs:
            slugs.append(slug)
    secrets: dict[str, bytes] = {}
    for slug in slugs:
        value = await SecretsService.get_secret(session, slug)
        if not value:
            continue
        secrets[slug] = (
            value.encode("utf-8") if isinstance(value, str) else bytes(value)
        )
    return secrets


def get_event_dispatcher(
    session: AsyncSession = Depends(get_async_session),
) -> EventExecutionDispatcher:
    """Return the Temporal-backed dispatcher (single dispatch identity)."""
    return TemporalEventExecutionDispatcher(session)


def _sanitize_dispatch_diagnostic(message: str, *, limit: int = 40) -> str:
    """Return a bounded log-safe diagnostic fragment for a dispatch failure.

    Single line, restricted alphabet, truncated: enough to tell the operator
    what must be corrected (invalid runtime, unknown preset, unenforceable
    limit) without persisting secret-bearing payloads in the receipt row.
    """
    text = " ".join(str(message or "").split())
    kept = "".join(
        ch for ch in text if ch.isalnum() or ch in " ._-/:"
    ).strip()
    return kept[:limit]


def _dispatch_failure_reason(exc: Exception) -> str:
    """Build a stable, bounded reason code for a dispatch failure."""
    kind = exc.__class__.__name__
    detail = _sanitize_dispatch_diagnostic(str(exc))
    base = f"dispatch_failed_{kind}"
    if detail:
        base = f"{base}_{detail}"
    return base[:64]


async def _read_bounded_body(request: Request) -> bytes | None:
    """Read the request body with an early cutoff before buffering.

    Returns ``None`` when the body exceeds ``MAX_WEBHOOK_BODY_BYTES`` so a
    very large or chunked body on this unauthenticated ingress cannot
    exhaust process memory before the length check runs.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_response(
    status: int,
    *,
    delivery_key_value: str,
    decision: str,
    reason_code: str,
    preset_slug: str = "",
    identity_key: str = "",
    execution_ref: str = "",
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "deliveryKey": delivery_key_value,
            "decision": decision,
            "reasonCode": reason_code,
            "presetSlug": preset_slug,
            "identityKey": identity_key,
            "executionRef": execution_ref or "",
        },
    )


def _stored_from_row(row: GitHubEventDeliveryReceipt) -> StoredReceipt:
    return StoredReceipt(
        delivery_key=row.delivery_key,
        payload_digest=row.payload_digest,
        decision=row.decision,
        execution_ref=row.execution_ref or "",
        received_at_epoch=row.created_at.timestamp()
        if getattr(row, "created_at", None) is not None
        else 0.0,
    )


@router.post("/events")
async def receive_github_event(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    settings: WebhookSettings = Depends(get_webhook_settings),
    secrets: dict[str, bytes] = Depends(resolve_webhook_secrets),
    dispatcher: EventExecutionDispatcher = Depends(get_event_dispatcher),
    settings_loader: Callable[[], WebhookSettings] = Depends(get_settings_loader),
) -> JSONResponse:
    """Receive one GitHub webhook delivery for the opt-in event path."""
    raw = await _read_bounded_body(request)
    if raw is None:
        return _safe_response(
            413,
            delivery_key_value="",
            decision="rejected",
            reason_code="body_too_large",
        )
    signature = request.headers.get("X-Hub-Signature-256", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "").strip()
    event_name = request.headers.get("X-GitHub-Event", "").strip()
    if not delivery_id or not event_name:
        return _safe_response(
            400,
            delivery_key_value="",
            decision="rejected",
            reason_code="missing_delivery_headers",
        )
    if not secrets:
        logger.warning("github_event_webhook_secret_unconfigured")
        return _safe_response(
            503,
            delivery_key_value="",
            decision="rejected",
            reason_code="webhook_secret_unconfigured",
        )
    ok = False
    sig_reason = "signature_mismatch"
    for slug, candidate in secrets.items():
        candidate_ok, candidate_reason = verify_webhook_signature(
            raw_body=raw, signature_header=signature, secret=candidate
        )
        if candidate_ok:
            ok = True
            sig_reason = candidate_reason
            break
        sig_reason = candidate_reason
        logger.debug(
            "github_event_signature_candidate_rejected",
            delivery_key=delivery_id,
            secret_slug=slug,
            reason=candidate_reason,
        )
    if not ok:
        logger.warning("github_event_signature_rejected", reason=sig_reason)
        return _safe_response(
            401, delivery_key_value="", decision="rejected", reason_code=sig_reason
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _safe_response(
            400,
            delivery_key_value="",
            decision="rejected",
            reason_code="malformed_payload",
        )
    if not isinstance(payload, dict):
        return _safe_response(
            400,
            delivery_key_value="",
            decision="rejected",
            reason_code="malformed_payload",
        )
    received_at = time.time()
    try:
        delivery = delivery_from_webhook_payload(
            delivery_id=delivery_id,
            event_name=event_name,
            payload=payload,
            received_at_epoch=received_at,
        )
    except ValueError as exc:
        logger.warning("github_event_malformed_delivery", error=str(exc)[:120])
        return _safe_response(
            400,
            delivery_key_value="",
            decision="rejected",
            reason_code="malformed_delivery",
        )
    digest = payload_digest(raw)
    key = delivery_key(
        installation_id=delivery.installation_id,
        repository=delivery.repository,
        delivery_id=delivery.delivery_id,
    )
    now = time.time()

    stored_row = await session.get(GitHubEventDeliveryReceipt, key)
    stored = _stored_from_row(stored_row) if stored_row is not None else None
    if stored is not None and stored.execution_ref:
        # Reconcile before reusing: a receipt that references a canonical
        # execution which no longer exists is a lost start, not a reuse.
        # Re-attempting under the same stable identity key is idempotent
        # (the execution service reconciles to the existing row), while
        # blindly reusing a dead reference would lose the event forever.
        canonical = await session.get(
            TemporalExecutionCanonicalRecord, stored.execution_ref
        )
        if canonical is None:
            logger.warning(
                "github_event_reuse_without_execution",
                delivery_key=key,
                execution_ref=stored.execution_ref,
            )
            stored = StoredReceipt(
                delivery_key=stored.delivery_key,
                payload_digest=stored.payload_digest,
                decision="admitted_pending",
                execution_ref="",
                received_at_epoch=stored.received_at_epoch,
            )
    decision = decide_delivery(
        delivery=delivery,
        configs=settings.trigger_configs,
        payload_digest_hex=digest,
        now_epoch=now,
        stored=stored,
    )

    if decision.outcome == "ignored":
        if stored_row is None:
            session.add(
                GitHubEventDeliveryReceipt(
                    delivery_key=key,
                    repository=delivery.repository,
                    event_name=delivery.event_name,
                    action=delivery.action,
                    payload_digest=digest,
                    decision="ignored",
                    reason_code=decision.reason_code,
                )
            )
            await session.commit()
        logger.info(
            "github_event_ignored",
            delivery_key=key,
            reason=decision.reason_code,
        )
        return _safe_response(
            202,
            delivery_key_value=key,
            decision="ignored",
            reason_code=decision.reason_code,
        )
    if decision.outcome == "rejected":
        if stored_row is None:
            session.add(
                GitHubEventDeliveryReceipt(
                    delivery_key=key,
                    repository=delivery.repository,
                    event_name=delivery.event_name,
                    action=delivery.action,
                    payload_digest=digest,
                    decision="rejected",
                    reason_code=decision.reason_code,
                )
            )
            await session.commit()
        # Stale deliveries carry an explicit safe disposition: acknowledging
        # avoids pointless redelivery of intent that will never be fresh.
        status = 202 if decision.reason_code == "stale_event" else 403
        logger.info(
            "github_event_rejected",
            delivery_key=key,
            reason=decision.reason_code,
        )
        return _safe_response(
            status,
            delivery_key_value=key,
            decision="rejected",
            reason_code=decision.reason_code,
        )
    if decision.outcome == "conflict":
        logger.warning("github_event_changed_body_conflict", delivery_key=key)
        return _safe_response(
            409,
            delivery_key_value=key,
            decision="conflict",
            reason_code=decision.reason_code,
        )
    if decision.outcome == "redelivery_reuse":
        logger.info(
            "github_event_redelivery_reuse",
            delivery_key=key,
            execution_ref=stored.execution_ref if stored else "",
        )
        return _safe_response(
            202,
            delivery_key_value=key,
            decision="redelivery_reuse",
            reason_code=decision.reason_code,
            execution_ref=stored.execution_ref if stored else "",
        )

    # Admitted (fresh or re-attempting a lost start): durable receipt first.
    if stored_row is None:
        session.add(
            GitHubEventDeliveryReceipt(
                delivery_key=key,
                repository=delivery.repository,
                event_name=delivery.event_name,
                action=delivery.action,
                payload_digest=digest,
                decision="admitted_pending",
                reason_code=decision.reason_code,
                preset_slug=decision.preset_slug,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            # A concurrent duplicate won the insert: reconcile against the
            # stored row instead of dispatching twice. A winner with a
            # different body digest is a changed-body conflict, never fresh
            # work: the loser must not dispatch under its divergent digest.
            await session.rollback()
            winner_row = await session.get(GitHubEventDeliveryReceipt, key)
            winner = (
                _stored_from_row(winner_row)
                if winner_row is not None
                else None
            )
            if winner is not None and winner.payload_digest != digest:
                logger.warning(
                    "github_event_changed_body_conflict", delivery_key=key
                )
                return _safe_response(
                    409,
                    delivery_key_value=key,
                    decision="conflict",
                    reason_code="changed_body_conflict",
                )
            if winner is not None and winner.execution_ref:
                return _safe_response(
                    202,
                    delivery_key_value=key,
                    decision="redelivery_reuse",
                    reason_code="redelivery_reuse",
                    execution_ref=winner.execution_ref,
                )
            # else: same body and still pending: fall through and attempt
            # dispatch under the stable key, refreshing the pending
            # classification the same way the elif branch does for a
            # directly observed pending receipt.
            stored_row = winner_row
            stored = winner
            if stored is not None and stored.decision == "admitted_pending":
                stored_row.decision = "admitted_pending"
                stored_row.reason_code = decision.reason_code
                stored_row.preset_slug = decision.preset_slug
                await session.commit()
    elif stored is not None and stored.decision == "admitted_pending":
        stored_row.decision = "admitted_pending"
        stored_row.reason_code = decision.reason_code
        stored_row.preset_slug = decision.preset_slug
        await session.commit()

    # Revocation recheck before dispatch: current configs own the launch, not
    # the request-time snapshot.
    try:
        fresh_settings = settings_loader()
        fresh_configs = fresh_settings.trigger_configs
    except ValueError:
        fresh_configs = ()
    current = resolve_trigger(
        fresh_configs,
        repository=delivery.repository,
        installation_id=delivery.installation_id,
        event_name=delivery.event_name,
        action=delivery.action,
        actor=delivery.actor,
        label=delivery.label,
    )
    if current is None:
        row = await session.get(GitHubEventDeliveryReceipt, key)
        if row is not None:
            row.decision = "rejected"
            row.reason_code = "revoked_before_dispatch"
            await session.commit()
        logger.warning("github_event_revoked_before_dispatch", delivery_key=key)
        return _safe_response(
            403,
            delivery_key_value=key,
            decision="rejected",
            reason_code="revoked_before_dispatch",
        )

    config = current
    parameters = build_dispatch_parameters(
        preset_slug=config.preset_slug,
        repository=delivery.repository,
        issue_number=delivery.issue_number,
        delivery_key=key,
        identity_key=decision.identity_key,
        execution_limits=dict(config.execution_limits),
    )
    title = (
        f"GitHub event {delivery.event_name}.{delivery.action} "
        f"{delivery.repository}#{delivery.issue_number or ''}".rstrip("#")
        + f" [{config.preset_slug}]"
    )
    try:
        execution_ref = await dispatcher.dispatch(
            preset_slug=config.preset_slug,
            identity_key=decision.identity_key,
            repository=delivery.repository,
            issue_number=delivery.issue_number,
            title=title,
            parameters=parameters,
            execution_limits=dict(config.execution_limits),
            publication_intent=config.publication_intent,
        )
    except Exception as exc:  # noqa: BLE001 - dispatch failure stays pending
        row = await session.get(GitHubEventDeliveryReceipt, key)
        failure_reason = _dispatch_failure_reason(exc)
        if row is not None:
            row.decision = "admitted_pending"
            row.reason_code = failure_reason
            await session.commit()
        logger.warning(
            "github_event_dispatch_failed",
            delivery_key=key,
            error_kind=exc.__class__.__name__,
            reason_code=failure_reason,
        )
        return _safe_response(
            202,
            delivery_key_value=key,
            decision="admitted_pending",
            reason_code="dispatch_failed",
            preset_slug=config.preset_slug,
            identity_key=decision.identity_key,
        )
    if not str(execution_ref or "").strip():
        row = await session.get(GitHubEventDeliveryReceipt, key)
        if row is not None:
            row.decision = "admitted_pending"
            row.reason_code = "dispatch_empty_reference"
            await session.commit()
        return _safe_response(
            202,
            delivery_key_value=key,
            decision="admitted_pending",
            reason_code="dispatch_empty_reference",
            preset_slug=config.preset_slug,
            identity_key=decision.identity_key,
        )
    row = await session.get(GitHubEventDeliveryReceipt, key)
    if row is not None:
        row.decision = "admitted_dispatched"
        row.reason_code = decision.reason_code
        row.execution_ref = str(execution_ref).strip()
        await session.commit()
    logger.info(
        "github_event_dispatched",
        delivery_key=key,
        preset=config.preset_slug,
        execution_ref=str(execution_ref)[:120],
    )
    return _safe_response(
        202,
        delivery_key_value=key,
        decision="admitted_dispatched",
        reason_code=decision.reason_code,
        preset_slug=config.preset_slug,
        identity_key=decision.identity_key,
        execution_ref=str(execution_ref).strip(),
    )
