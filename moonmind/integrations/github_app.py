"""Authorized, durable GitHub App event-to-workflow integration core.

Pure, dependency-free policy and protocol logic for the inbound GitHub App
webhook path (MoonLadderStudios/MoonMind#3967). This module owns:

- the App manifest contract (minimum permissions and event subscriptions),
- ``X-Hub-Signature-256`` verification against exact raw request bytes,
- bounded receipt validation (size, content type, event/action allowlist),
- delivery identity (scoped delivery ID + payload digest) with
  same-body-duplicate vs changed-body-conflict semantics,
- repository opt-in and actor authorization (signature proves provenance,
  never spending authority),
- vertical-slice event semantics (``issues.labeled`` and
  ``issue_comment.created`` with an ``@mm`` command), including bot/self-loop
  guards, fork-content isolation, and stale-event handling,
- stable dispatch identity (idempotency key) and delivery lifecycle states.

Design notes:

- Issue/PR/review content is untrusted input. This module never executes it;
  ``extract_mm_command`` returns inert text for downstream policy matching.
- Installation tokens and webhook secrets must never flow through here.
  Diagnostics and reason strings are safe to persist and display.
- Persistence (inbox rows) and preset-catalog admission live in
  ``api_service`` service boundaries, which call into this module.

Source issue: MoonLadderStudios/MoonMind#3967.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# App manifest contract (minimum permissions / event subscriptions)
# ---------------------------------------------------------------------------

# Minimum GitHub App permissions for the implemented vertical slice
# (label-gated and @mm-command-gated issue triggers). Request nothing broader:
# no contents write, no administration, no secrets, no deployments.
GITHUB_APP_MIN_PERMISSIONS: dict[str, str] = {
    "issues": "read",
    "pull_requests": "read",
    "metadata": "read",
}

# Webhook event subscriptions for the implemented vertical slice. ``issues``
# carries label/unlabel actions; ``issue_comment`` carries @mm commands.
# Review, review-comment, check-run/suite, and pull-request events are
# intentionally not subscribed until their mappings are implemented.
GITHUB_APP_EVENT_SUBSCRIPTIONS: tuple[str, ...] = ("issues", "issue_comment")

# Supported (event, action) combinations mapped to trigger evaluation.
SUPPORTED_EVENT_ACTIONS: dict[str, frozenset[str]] = {
    "issues": frozenset({"labeled", "unlabeled"}),
    "issue_comment": frozenset({"created"}),
}

MAX_PAYLOAD_BYTES_DEFAULT = 1_000_000
JSON_CONTENT_TYPE = "application/json"
MM_COMMAND_PREFIX = "@mm"
SIGNATURE_HEADER_PREFIX = "sha256="

_BOT_LOGIN_SUFFIX = "[bot]"
_MM_COMMAND_RE = re.compile(r"(?m)^\s*@mm\s+(?P<command>[^\r\n]{1,200})")


class DeliveryState(str, Enum):
    """Lifecycle states for one inbox delivery record."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    IGNORED = "ignored"
    REJECTED = "rejected"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"


class DeliveryOutcome(str, Enum):
    """Policy outcome for one evaluated delivery."""

    ACCEPT_DISPATCH = "accept_dispatch"
    DUPLICATE_SAME_BODY = "duplicate_same_body"
    IGNORE = "ignore"
    REJECT = "reject"
    CONFLICT_CHANGED_BODY = "conflict_changed_body"


# Outcomes that must never launch or spend (no paid side effect).
NO_LAUNCH_OUTCOMES = frozenset(
    {
        DeliveryOutcome.DUPLICATE_SAME_BODY,
        DeliveryOutcome.IGNORE,
        DeliveryOutcome.REJECT,
        DeliveryOutcome.CONFLICT_CHANGED_BODY,
    }
)


@dataclass(frozen=True, slots=True)
class RepositoryBinding:
    """One authorized installation repository binding.

    ``installation_id`` scopes the GitHub App installation; ``repository``
    is the ``owner/repo`` full name explicitly opted in by the operator.
    ``revoked`` marks uninstall/suspension/removal: future dispatch and
    credential use stop, while historical rows keep their evidence.
    """

    installation_id: int
    repository: str
    owner: str = ""
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class TriggerMapping:
    """One authorized event/action to versioned-preset mapping."""

    event: str
    action: str
    # For ``issues``: the opt-in label that gates dispatch (``unlabeled``
    # of the same label cancels/ignores rather than dispatches).
    # For ``issue_comment``: the @mm command word that gates dispatch.
    gate: str
    preset_slug: str
    # Explicitly authorized GitHub logins allowed to spend operator budget
    # through this trigger. Empty means nobody may dispatch.
    allowed_actor_logins: tuple[str, ...] = ()
    # Repositories this trigger is enabled for. Empty means none.
    enabled_repositories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """Authoritative policy verdict for one delivery."""

    outcome: DeliveryOutcome
    state: DeliveryState
    reason_code: str
    # Safe to persist, log, and display. Never contains secrets or tokens.
    safe_summary: str
    preset_slug: str | None = None
    dispatch_key: str | None = None


@dataclass
class EvaluationContext:
    """Inputs for one delivery evaluation (all untrusted except bindings)."""

    event: str
    action: str
    installation_id: int | None
    repository: str
    actor_login: str
    actor_is_bot: bool
    payload_digest: str
    scoped_delivery_id: str
    payload: dict = field(default_factory=dict)
    is_duplicate_same_body: bool = False
    is_duplicate_changed_body: bool = False
    bindings: tuple[RepositoryBinding, ...] = ()
    triggers: tuple[TriggerMapping, ...] = ()


# ---------------------------------------------------------------------------
# Protocol primitives
# ---------------------------------------------------------------------------


def verify_webhook_signature(
    *, raw_body: bytes, signature_header: str, webhook_secret: bytes
) -> bool:
    """Verify ``X-Hub-Signature-256`` against the exact raw request bytes.

    Uses constant-time comparison. Returns ``False`` for missing, malformed,
    or mismatched signatures instead of raising, so callers can reject
    safely with a stable reason code.
    """

    header = (signature_header or "").strip()
    if not header.startswith(SIGNATURE_HEADER_PREFIX):
        return False
    hex_digest = header[len(SIGNATURE_HEADER_PREFIX) :].strip()
    if not hex_digest or len(hex_digest) != 64:
        return False
    try:
        provided = bytes.fromhex(hex_digest)
    except ValueError:
        return False
    if not webhook_secret:
        return False
    expected = hmac.new(webhook_secret, raw_body, hashlib.sha256).digest()
    return hmac.compare_digest(provided, expected)


def sign_webhook_payload(*, raw_body: bytes, webhook_secret: bytes) -> str:
    """Build an ``X-Hub-Signature-256`` header value (tests/fixtures)."""

    digest = hmac.new(webhook_secret, raw_body, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_HEADER_PREFIX}{digest}"


def payload_digest(raw_body: bytes) -> str:
    """Stable SHA-256 hex digest of the exact raw request bytes."""

    return hashlib.sha256(raw_body).hexdigest()


def scoped_delivery_id(*, installation_id: int | None, delivery_guid: str) -> str:
    """Unique delivery identity scoped to the sending installation.

    GitHub delivery GUIDs are unique per App, so scoping by installation
    keeps redeliveries and cross-installation collisions distinct.
    """

    installation = str(installation_id) if installation_id is not None else "unknown"
    return f"github-installation-{installation}:{delivery_guid.strip()}"


def dispatch_idempotency_key(*, scoped_id: str, digest: str) -> str:
    """Stable workflow/update identity for one authorized delivery.

    Reconciles the crash between Temporal acceptance and marking the inbox
    dispatched: re-processing the same scoped delivery reuses this key, so
    the authorized workflow mutation cannot duplicate.
    """

    return f"github-delivery:{scoped_id}:{digest}"


def extract_mm_command(comment_body: str) -> str | None:
    """Extract the inert ``@mm`` command word from untrusted comment text.

    Returns the lowercased command word (e.g. ``"review"``) or ``None``.
    The returned text is data for policy matching only; it is never
    executed and must never be interpolated into prompts or shell.
    """

    match = _MM_COMMAND_RE.search(comment_body or "")
    if not match:
        return None
    word = match.group("command").strip().split()[0] if match.group("command").strip() else ""
    return word.lower() or None


def is_bot_login(login: str) -> bool:
    """True for GitHub bot/self-generated actors (feedback-loop guard)."""

    candidate = (login or "").strip().lower()
    return not candidate or candidate.endswith(_BOT_LOGIN_SUFFIX) or candidate == "moonmind-bot"


def redact_sensitive_text(value: str) -> str:
    """Remove secret-like patterns from text destined for diagnostics."""

    redacted = re.sub(
        r"(?i)\b(github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+|ghs_[A-Za-z0-9_]+)",
        "[redacted-token]",
        value or "",
    )
    redacted = re.sub(r"(?i)\b(token|secret|password)\s*=\s*\S+", r"\1=[redacted]", redacted)
    return redacted


# ---------------------------------------------------------------------------
# Policy evaluation
# ---------------------------------------------------------------------------


def _reject(reason_code: str, safe_summary: str) -> DeliveryDecision:
    return DeliveryDecision(
        outcome=DeliveryOutcome.REJECT,
        state=DeliveryState.REJECTED,
        reason_code=reason_code,
        safe_summary=safe_summary,
    )


def _ignore(reason_code: str, safe_summary: str) -> DeliveryDecision:
    return DeliveryDecision(
        outcome=DeliveryOutcome.IGNORE,
        state=DeliveryState.IGNORED,
        reason_code=reason_code,
        safe_summary=safe_summary,
    )


def binding_for(
    bindings: tuple[RepositoryBinding, ...],
    *,
    installation_id: int | None,
    repository: str,
) -> RepositoryBinding | None:
    """Find the binding for one installation/repository pair (case-insensitive)."""

    wanted = (repository or "").strip().lower()
    for binding in bindings:
        if binding.installation_id == installation_id and binding.repository.strip().lower() == wanted:
            return binding
    return None


def evaluate_delivery(ctx: EvaluationContext) -> DeliveryDecision:
    """Evaluate one received delivery against opt-in policy.

    Ordering is load-bearing: dedup/conflict first (no re-spend), then
    event/action allowlist, then installation/repository binding and
    revocation, then trigger opt-in and actor authorization. A valid
    signature (checked by the caller before evaluation) proves provenance
    only; only this policy grants spending authority.
    """

    if ctx.is_duplicate_changed_body:
        return DeliveryDecision(
            outcome=DeliveryOutcome.CONFLICT_CHANGED_BODY,
            state=DeliveryState.REJECTED,
            reason_code="conflict_changed_body",
            safe_summary=(
                f"Duplicate delivery {ctx.scoped_delivery_id} arrived with changed "
                "content; held as a conflict for operator review."
            ),
        )
    if ctx.is_duplicate_same_body:
        return DeliveryDecision(
            outcome=DeliveryOutcome.DUPLICATE_SAME_BODY,
            state=DeliveryState.IGNORED,
            reason_code="duplicate_same_body",
            safe_summary=(
                f"Duplicate delivery {ctx.scoped_delivery_id} with identical "
                "content; already recorded."
            ),
        )

    allowed_actions = SUPPORTED_EVENT_ACTIONS.get(ctx.event)
    if allowed_actions is None:
        return _ignore(
            "ignored_unknown_event",
            f"Event {ctx.event!r} is not subscribed; ignored without side effects.",
        )
    if ctx.action not in allowed_actions:
        return _ignore(
            "ignored_unsupported_action",
            f"Action {ctx.event}.{ctx.action} is not mapped; ignored without side effects.",
        )

    binding = binding_for(
        ctx.bindings, installation_id=ctx.installation_id, repository=ctx.repository
    )
    if binding is None:
        return _reject(
            "rejected_unknown_installation_or_repository",
            "No authorized installation/repository binding for this delivery; rejected.",
        )
    if binding.revoked:
        return _reject(
            "rejected_revoked_binding",
            "The installation/repository binding is revoked; future dispatch is blocked.",
        )

    if is_bot_login(ctx.actor_login) or ctx.payload.get("sender_is_bot"):
        return _ignore(
            "ignored_bot_actor",
            "Bot/self-generated event ignored to prevent feedback loops.",
        )

    trigger = _matching_trigger(ctx)
    if trigger is None:
        return _ignore(
            "ignored_no_trigger_match",
            f"No enabled trigger matches {ctx.event}.{ctx.action} for {ctx.repository}.",
        )

    if ctx.repository.strip().lower() not in {
        repo.strip().lower() for repo in trigger.enabled_repositories
    }:
        return _reject(
            "rejected_repository_not_opted_in",
            f"Repository {ctx.repository} is not opted in for trigger {trigger.gate!r}.",
        )

    # Fork/PR content isolation: hostile fork content must not reach
    # installation or provider credentials. Fork heads are rejected for the
    # vertical slice until a fork-aware mapping exists.
    if bool(ctx.payload.get("head_is_fork")):
        return _reject(
            "rejected_fork_content",
            "Fork-head content is kept away from installation credentials; rejected.",
        )

    actor = (ctx.actor_login or "").strip().lower()
    if not actor or actor not in {login.strip().lower() for login in trigger.allowed_actor_logins}:
        return _reject(
            "rejected_unauthorized_actor",
            f"Actor is not authorized for trigger {trigger.gate!r}; no launch or paid side effect.",
        )

    if not trigger.preset_slug.strip():
        return _reject(
            "rejected_unknown_preset",
            "Trigger preset is not configured; rejected without side effects.",
        )

    # Stale-event guard: an older issue/comment revision must not override a
    # newer pinned evaluation. Callers supply monotonic ``event_sequence``
    # (comment ID / issue updated sequence) and ``last_sequence``.
    sequence = ctx.payload.get("event_sequence")
    last_sequence = ctx.payload.get("last_sequence")
    if (
        isinstance(sequence, int)
        and isinstance(last_sequence, int)
        and sequence <= last_sequence
    ):
        return _ignore(
            "ignored_stale_event",
            "Out-of-order or stale event ignored; newer state already observed.",
        )

    return DeliveryDecision(
        outcome=DeliveryOutcome.ACCEPT_DISPATCH,
        state=DeliveryState.PENDING,
        reason_code="accepted_dispatch",
        safe_summary=(
            f"Authorized {ctx.event}.{ctx.action} for {ctx.repository} "
            f"matches trigger {trigger.gate!r}; dispatch requested."
        ),
        preset_slug=trigger.preset_slug.strip(),
        dispatch_key=dispatch_idempotency_key(
            scoped_id=ctx.scoped_delivery_id, digest=ctx.payload_digest
        ),
    )


def _matching_trigger(ctx: EvaluationContext) -> TriggerMapping | None:
    if ctx.event == "issues" and ctx.action == "labeled":
        label = str(ctx.payload.get("label") or "").strip().lower()
        for trigger in ctx.triggers:
            if (
                trigger.event == "issues"
                and trigger.action == "labeled"
                and trigger.gate.strip().lower() == label
                and label
            ):
                return trigger
        return None
    if ctx.event == "issues" and ctx.action == "unlabeled":
        # Label removal cancels intent: never dispatch; the dispatcher maps
        # this to ignored so repeat label churn cannot re-spend.
        return None
    if ctx.event == "issue_comment" and ctx.action == "created":
        command = extract_mm_command(str(ctx.payload.get("comment_body") or ""))
        if command is None:
            return None
        for trigger in ctx.triggers:
            if (
                trigger.event == "issue_comment"
                and trigger.action == "created"
                and trigger.gate.strip().lower() == command
            ):
                return trigger
        return None
    return None
