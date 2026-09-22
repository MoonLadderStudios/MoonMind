"""Opt-in GitHub event trigger path with durable deduplication (#3967).

Issue MoonLadderStudios/MoonMind#3967 owns the thin transport that turns one
explicitly enabled repository event into an authorized existing preset launch.
This module is the hermetic decision boundary for that transport: opt-in
trigger resolution, ``X-Hub-Signature-256`` verification over the exact
bounded raw body, delivery validation (event/action, installation,
repository, enablement, actor authority, self-loop, staleness, fork
content), and durable-receipt redelivery classification.

It performs no network I/O, reads no secrets, touches no database, and
starts no workflows. The API ingress router
(``api_service.api.routers.github_event_webhook``) owns the webhook secret
(via the existing Managed Secrets owner), the durable receipt row (via the
existing database), and Temporal dispatch (via the existing execution
service); it binds to this contract as its handoff.

First-slice scope: ``issues/labeled`` only. Every other event/action is
ignored safely. Default operation is unchanged: with no trigger configured,
nothing is opted in and every delivery is rejected before any paid effect.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "MAX_WEBHOOK_BODY_BYTES",
    "RECEIPT_RETENTION_SECONDS",
    "STALE_EVENT_MAX_AGE_SECONDS",
    "SUPPORTED_EVENTS",
    "DeliveryDecision",
    "EventTriggerConfig",
    "IncomingDelivery",
    "StoredReceipt",
    "classify_delivery",
    "decide_delivery",
    "delivery_from_webhook_payload",
    "delivery_key",
    "is_receipt_expired",
    "load_trigger_configs",
    "payload_digest",
    "resolve_trigger",
    "sign_webhook_body",
    "trigger_from_mapping",
    "verify_webhook_signature",
]

#: Upper bound for an inbound webhook body. Verification and parsing refuse
#: anything larger before comparing signatures or trusting fields.
MAX_WEBHOOK_BODY_BYTES = 1_000_000

#: How long a delivery receipt is retained for deduplication. Aligned with
#: GitHub's manual redelivery window for failed deliveries: redeliveries
#: inside the window reuse the same logical request; anything older than
#: this is never treated as fresh spending intent.
RECEIPT_RETENTION_SECONDS = 7 * 24 * 3600

#: Default staleness horizon for an event timestamp. A delivery older than
#: this at receipt time gets an explicit safe disposition, never a launch.
STALE_EVENT_MAX_AGE_SECONDS = 600

#: First-slice event/action allowlist. Everything else is ignored safely.
SUPPORTED_EVENTS: tuple[tuple[str, str], ...] = (("issues", "labeled"),)


# ---------------------------------------------------------------------------
# Opt-in trigger configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EventTriggerConfig:
    """One explicitly enabled repository event bound to an existing preset.

    ``webhook_secret_slug`` is a reference into the existing Managed Secrets
    owner, never secret material. ``execution_limits`` carries the bounded
    spend/publication budget the dispatcher enforces; ``publication_intent``
    is ``"none"`` unless the operator explicitly allows follow-up effects.
    """

    name: str
    repository: str
    installation_id: str
    event_name: str = "issues"
    action: str = "labeled"
    permitted_actors: tuple[str, ...] = ()
    label: str = ""
    preset_slug: str = ""
    execution_limits: Mapping[str, Any] = field(default_factory=dict)
    publication_intent: str = "none"
    enabled: bool = True
    webhook_secret_slug: str = "github-webhook-secret"
    max_age_seconds: int = STALE_EVENT_MAX_AGE_SECONDS
    allow_fork_content: bool = False


def normalize_repository(value: Any) -> str:
    """Normalize an ``owner/repo`` reference for comparison."""
    return str(value or "").strip().lower()


def _mapping_str(
    payload: Mapping[str, Any], key: str, *, required: bool = False
) -> str:
    value = payload.get(key, "")
    if value is None:
        value = ""
    if isinstance(value, str):
        text = value.strip()
        if required and not text:
            raise ValueError(f"event trigger field {key!r} must be a non-empty string")
        return text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ValueError(f"event trigger field {key!r} must be a string")


def trigger_from_mapping(payload: Mapping[str, Any]) -> EventTriggerConfig:
    """Coerce one trigger mapping into an :class:`EventTriggerConfig`.

    Raises ``ValueError`` before anything is enabled: a labeled trigger
    without its label, or any trigger without a preset, is a misconfiguration
    that must fail closed, not a silent no-op.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("event trigger must be a mapping")
    repository = normalize_repository(payload.get("repository", ""))
    if not repository or "/" not in repository:
        raise ValueError("event trigger field 'repository' must be 'owner/repo'")
    preset_slug = _mapping_str(payload, "preset_slug", required=True)
    event_name = _mapping_str(payload, "event_name") or "issues"
    action = _mapping_str(payload, "action") or "labeled"
    label = _mapping_str(payload, "label")
    if action == "labeled" and not label:
        raise ValueError("event trigger for action 'labeled' requires 'label'")
    raw_actors = payload.get("permitted_actors", ())
    if raw_actors is None:
        raw_actors = ()
    if isinstance(raw_actors, str) or not isinstance(raw_actors, Sequence):
        raise ValueError("event trigger field 'permitted_actors' must be a sequence")
    permitted_actors = tuple(
        str(actor).strip().lower() for actor in raw_actors if str(actor).strip()
    )
    if not permitted_actors:
        raise ValueError("event trigger requires at least one permitted actor")
    raw_limits = payload.get("execution_limits", {})
    if raw_limits is None:
        raw_limits = {}
    if not isinstance(raw_limits, Mapping):
        raise ValueError("event trigger field 'execution_limits' must be a mapping")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("event trigger field 'enabled' must be a boolean")
    max_age = payload.get("max_age_seconds", STALE_EVENT_MAX_AGE_SECONDS)
    if isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0:
        raise ValueError("event trigger field 'max_age_seconds' must be a positive int")
    allow_fork = payload.get("allow_fork_content", False)
    if not isinstance(allow_fork, bool):
        raise ValueError("event trigger field 'allow_fork_content' must be a boolean")
    return EventTriggerConfig(
        name=_mapping_str(payload, "name") or "github-event-trigger",
        repository=repository,
        installation_id=_mapping_str(payload, "installation_id", required=True),
        event_name=event_name,
        action=action,
        permitted_actors=permitted_actors,
        label=label,
        preset_slug=preset_slug,
        execution_limits=dict(raw_limits),
        publication_intent=_mapping_str(payload, "publication_intent") or "none",
        enabled=enabled,
        webhook_secret_slug=_mapping_str(payload, "webhook_secret_slug")
        or "github-webhook-secret",
        max_age_seconds=max_age,
        allow_fork_content=allow_fork,
    )


def load_trigger_configs(
    raw: Sequence[Mapping[str, Any]] | None,
) -> tuple[EventTriggerConfig, ...]:
    """Load trigger configs; empty input means nothing is opted in."""
    if not raw:
        return ()
    return tuple(trigger_from_mapping(item) for item in raw)


def resolve_trigger(
    configs: Sequence[EventTriggerConfig],
    *,
    repository: str,
    installation_id: str,
    event_name: str,
    action: str,
    actor: str,
    label: str = "",
) -> Optional[EventTriggerConfig]:
    """Return the enabled trigger authorizing this event, if any.

    Every binding fact must match: repository, installation, event/action,
    permitted actor, and (for labeled events) the exact label. Disabled
    configs never match, so revocation takes effect on the next delivery.
    """
    wanted_repo = normalize_repository(repository)
    wanted_actor = str(actor or "").strip().lower()
    wanted_label = str(label or "").strip().lower()
    for config in configs:
        if not config.enabled:
            continue
        if config.repository != wanted_repo:
            continue
        if config.installation_id != str(installation_id or "").strip():
            continue
        if config.event_name != str(event_name or "").strip():
            continue
        if config.action != str(action or "").strip():
            continue
        if wanted_actor not in config.permitted_actors:
            continue
        if config.action == "labeled" and config.label.strip().lower() != wanted_label:
            continue
        return config
    return None


# ---------------------------------------------------------------------------
# Signature verification over the exact bounded raw body
# ---------------------------------------------------------------------------


def sign_webhook_body(raw_body: bytes, secret: bytes) -> str:
    """Return the ``X-Hub-Signature-256`` header value for a body."""
    digest = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str,
    secret: bytes,
    max_bytes: int = MAX_WEBHOOK_BODY_BYTES,
) -> tuple[bool, str]:
    """Verify the exact bounded raw body before trusting any field.

    Returns ``(valid, reason_code)``. Oversized bodies are refused before
    any comparison; comparison is constant-time.
    """
    if not signature_header:
        return False, "signature_missing"
    if len(raw_body) > max_bytes:
        return False, "body_too_large"
    if not secret:
        return False, "secret_unconfigured"
    prefix, sep, presented = str(signature_header).partition("=")
    if not sep or prefix.strip().lower() != "sha256" or not presented.strip():
        return False, "signature_malformed"
    try:
        presented_bytes = bytes.fromhex(presented.strip())
    except ValueError:
        return False, "signature_malformed"
    expected = hmac.new(secret, raw_body, hashlib.sha256).digest()
    if not hmac.compare_digest(presented_bytes, expected):
        return False, "signature_mismatch"
    return True, "signature_valid"


def payload_digest(raw_body: bytes) -> str:
    """Return the stable hex digest identifying this exact delivery body."""
    return hashlib.sha256(raw_body).hexdigest()


# ---------------------------------------------------------------------------
# Incoming delivery facts (coerced only after signature verification)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomingDelivery:
    """Transport-level facts for one verified webhook delivery."""

    delivery_id: str
    event_name: str
    action: str
    installation_id: str
    repository: str
    actor: str
    actor_type: str = "User"
    label: str = ""
    issue_number: int = 0
    received_at_epoch: float = 0.0
    is_fork_content: bool = False
    sender_is_app_bot: bool = False


def _nested_str(payload: Mapping[str, Any], *path: str) -> str:
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key, "")
    if current is None:
        return ""
    if isinstance(current, bool):
        return ""
    if isinstance(current, (str, int, float)):
        return str(current).strip()
    return ""


def delivery_from_webhook_payload(
    *,
    delivery_id: str,
    event_name: str,
    payload: Mapping[str, Any],
    received_at_epoch: float,
) -> IncomingDelivery:
    """Coerce verified webhook fields into an :class:`IncomingDelivery`.

    Call only after :func:`verify_webhook_signature` succeeds. Raises
    ``ValueError`` on malformed payloads before any admission decision.
    """
    if not str(delivery_id or "").strip():
        raise ValueError("webhook delivery requires a delivery id")
    if not isinstance(payload, Mapping):
        raise ValueError("webhook payload must be a mapping")
    action = _nested_str(payload, "action")
    installation_id = _nested_str(payload, "installation", "id")
    repository = normalize_repository(_nested_str(payload, "repository", "full_name"))
    if not installation_id or not repository:
        raise ValueError("webhook payload requires installation id and repository")
    actor = _nested_str(payload, "sender", "login")
    actor_type = _nested_str(payload, "sender", "type") or "User"
    if not actor:
        raise ValueError("webhook payload requires a sender login")
    issue_raw = payload.get("issue", {})
    issue_number = 0
    if isinstance(issue_raw, Mapping):
        number_raw = issue_raw.get("number", 0)
        if isinstance(number_raw, int) and not isinstance(number_raw, bool):
            issue_number = number_raw
    sender_name = actor.lower()
    sender_is_app_bot = actor_type.lower() == "bot" and (
        sender_name.endswith("[bot]") or sender_name.endswith("-bot")
    )
    fork_content = False
    pull_request = payload.get("pull_request", {})
    if isinstance(pull_request, Mapping):
        head = pull_request.get("head", {})
        if isinstance(head, Mapping):
            repo = head.get("repo", {})
            if isinstance(repo, Mapping) and repo.get("fork") is True:
                fork_content = True
    return IncomingDelivery(
        delivery_id=str(delivery_id).strip(),
        event_name=str(event_name or "").strip(),
        action=action,
        installation_id=installation_id,
        repository=repository,
        actor=actor,
        actor_type=actor_type,
        label=_nested_str(payload, "label", "name"),
        issue_number=issue_number,
        received_at_epoch=float(received_at_epoch),
        is_fork_content=fork_content,
        sender_is_app_bot=sender_is_app_bot,
    )


# ---------------------------------------------------------------------------
# Durable receipt identity and redelivery classification
# ---------------------------------------------------------------------------


def delivery_key(*, installation_id: str, repository: str, delivery_id: str) -> str:
    """Return the scoped durable key for one GitHub delivery."""
    return (
        f"github-delivery:v1:{str(installation_id).strip()}:"
        f"{normalize_repository(repository)}:{str(delivery_id).strip()}"
    )


def is_receipt_expired(
    *,
    received_at_epoch: float,
    now_epoch: float,
    retention_seconds: int = RECEIPT_RETENTION_SECONDS,
) -> bool:
    """Return True when a receipt is outside the deduplication lifetime."""
    return (float(now_epoch) - float(received_at_epoch)) > retention_seconds


def classify_delivery(
    *,
    stored_digest: str,
    incoming_digest: str,
    same_delivery: bool,
) -> str:
    """Classify an incoming delivery against a stored receipt.

    Returns ``redelivery_reuse`` (same delivery, same body: reuse the stored
    logical request), ``conflict_changed_body`` (same delivery id, changed
    body: not the original request), or ``new_request``.
    """
    if not same_delivery:
        return "new_request"
    if str(incoming_digest) == str(stored_digest):
        return "redelivery_reuse"
    return "conflict_changed_body"


@dataclass(frozen=True, slots=True)
class StoredReceipt:
    """Minimal durable evidence the receipt owner persisted for a delivery."""

    delivery_key: str
    payload_digest: str
    decision: str
    execution_ref: str = ""
    received_at_epoch: float = 0.0


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """Outcome of deciding one delivery.

    ``outcome`` is ``"admitted"`` (dispatch the bound preset),
    ``"redelivery_reuse"`` (reuse ``execution_ref``, never re-dispatch),
    ``"ignored"`` (unsupported or self-generated: safe no-op),
    ``"rejected"`` (no launch; explicit safe reason), or ``"conflict"``
    (same delivery id with a changed body: not the original request).
    Only ``"admitted"`` carries a ``preset_slug`` and ``identity_key``.
    """

    outcome: str
    reason_code: str
    preset_slug: str = ""
    identity_key: str = ""
    execution_ref: str = ""


def _identity_key(
    *, installation_id: str, repository: str, delivery_id: str, digest_hex: str
) -> str:
    short = str(digest_hex or "")[:16]
    return (
        f"github-event:v1:{str(installation_id).strip()}:"
        f"{normalize_repository(repository)}:{str(delivery_id).strip()}:{short}"
    )


def decide_delivery(
    *,
    delivery: IncomingDelivery,
    configs: Sequence[EventTriggerConfig],
    payload_digest_hex: str,
    now_epoch: float,
    stored: Optional[StoredReceipt] = None,
) -> DeliveryDecision:
    """Decide one verified delivery against current trigger authority.

    Order is load-bearing: unsupported/self/stale events are disposed before
    trigger resolution; stored receipts collapse redeliveries to the same
    logical request and turn changed-body duplicates into conflicts; only a
    fresh delivery matching a currently enabled trigger is admitted.
    """
    if (delivery.event_name, delivery.action) not in SUPPORTED_EVENTS:
        return DeliveryDecision(outcome="ignored", reason_code="unsupported_event")
    if delivery.sender_is_app_bot:
        return DeliveryDecision(outcome="ignored", reason_code="self_event")
    if stored is not None:
        classification = classify_delivery(
            stored_digest=stored.payload_digest,
            incoming_digest=payload_digest_hex,
            same_delivery=True,
        )
        if classification == "redelivery_reuse":
            if stored.decision == "admitted_pending" and not stored.execution_ref:
                # A lost start acknowledgment: the receipt exists but no
                # dispatch evidence was recorded. Re-resolve current authority
                # and re-attempt under the same stable identity key instead of
                # minting a second logical request.
                return _admit_or_reject(
                    delivery,
                    configs,
                    payload_digest_hex,
                    now_epoch,
                    "reattempt_pending_dispatch",
                )
            return DeliveryDecision(
                outcome="redelivery_reuse",
                reason_code="redelivery_reuse",
                execution_ref=stored.execution_ref,
            )
        return DeliveryDecision(outcome="conflict", reason_code="changed_body_conflict")
    if is_receipt_expired(
        received_at_epoch=delivery.received_at_epoch, now_epoch=now_epoch
    ):
        # Old deliveries outside the dedup lifetime need an explicit safe
        # disposition rather than becoming fresh launches.
        return DeliveryDecision(outcome="rejected", reason_code="stale_event")
    return _admit_or_reject(
        delivery, configs, payload_digest_hex, now_epoch, "admitted"
    )


def _admit_or_reject(
    delivery: IncomingDelivery,
    configs: Sequence[EventTriggerConfig],
    payload_digest_hex: str,
    now_epoch: float,
    admit_reason: str,
) -> DeliveryDecision:
    config = resolve_trigger(
        configs,
        repository=delivery.repository,
        installation_id=delivery.installation_id,
        event_name=delivery.event_name,
        action=delivery.action,
        actor=delivery.actor,
        label=delivery.label,
    )
    if config is None:
        return DeliveryDecision(outcome="rejected", reason_code="no_matching_trigger")
    if (float(now_epoch) - float(delivery.received_at_epoch)) > config.max_age_seconds:
        # Stale events never change the admitted target: the operator's
        # configured horizon owns the disposition, not a silent replay.
        return DeliveryDecision(outcome="rejected", reason_code="stale_event")
    if delivery.is_fork_content and not config.allow_fork_content:
        return DeliveryDecision(
            outcome="rejected", reason_code="fork_content_untrusted"
        )
    return DeliveryDecision(
        outcome="admitted",
        reason_code=admit_reason,
        preset_slug=config.preset_slug,
        identity_key=_identity_key(
            installation_id=delivery.installation_id,
            repository=delivery.repository,
            delivery_id=delivery.delivery_id,
            digest_hex=payload_digest_hex,
        ),
    )
