"""Semantic trace convention for the Omnigent control plane.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

This module documents-in-code the Omnigent semantic trace convention and gives
infrastructure/activity boundaries one safe way to emit it. It defines:

* the closed set of ``omnigent.*`` span names (:data:`OMNIGENT_SPANS`);
* the closed set of bounded span attribute keys (:data:`SAFE_SPAN_ATTRIBUTES`);
* a value sanitizer that refuses forbidden content (prompts, transcripts,
  diffs, terminal input, credentials, presigned URLs, host paths, or unbounded
  provider payloads) so a span can never carry a secret or an unbounded payload.

Two hard boundaries from the issue are enforced here:

* **No exporter I/O from deterministic workflow code.** :func:`omnigent_span` is
  meant to wrap the *infrastructure and activity boundaries around* the domain
  decisions, not the pure reducer. It also never raises: a missing tracer or a
  failing exporter is swallowed so telemetry can never change application
  correctness (acceptance criterion "exporter/backend failure cannot alter
  execution correctness").
* **Bounded, secret-free attributes only.** Unknown attribute keys and
  forbidden/oversized values fail closed (dropped, never emitted). The span name
  vocabulary is closed so an ad-hoc span name cannot leak identity through a
  free-text name.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional

logger = logging.getLogger("moonmind.omnigent.control_plane.spans")

# --- Closed span-name vocabulary --------------------------------------------

INTENT_COMPILE = "omnigent.intent.compile"
SESSION_RECONCILE = "omnigent.session.reconcile"
OBSERVATION_LOAD = "omnigent.observation.load"
PROVIDER_OBSERVE_SNAPSHOT = "omnigent.provider.observe_snapshot"
PROVIDER_READ_EVENT_BATCH = "omnigent.provider.read_event_batch"
TURN_SUBMIT = "omnigent.turn.submit"
COMMAND_EXECUTE = "omnigent.command.execute"
PROFILE_LEASE_ENSURE = "omnigent.profile_lease.ensure"
HOST_ENSURE = "omnigent.host.ensure"
SESSION_ENSURE_PROVIDER_ATTACHMENT = "omnigent.session.ensure_provider_attachment"
EVIDENCE_HARVEST = "omnigent.evidence.harvest"
WORKSPACE_PUBLISH = "omnigent.workspace.publish"
CLEANUP_EXECUTE = "omnigent.cleanup.execute"
COMPATIBILITY_VERIFY = "omnigent.compatibility.verify"
STUCK_STATE_INSPECT = "omnigent.stuck_state.inspect"

OMNIGENT_SPANS: frozenset[str] = frozenset(
    {
        INTENT_COMPILE,
        SESSION_RECONCILE,
        OBSERVATION_LOAD,
        PROVIDER_OBSERVE_SNAPSHOT,
        PROVIDER_READ_EVENT_BATCH,
        TURN_SUBMIT,
        COMMAND_EXECUTE,
        PROFILE_LEASE_ENSURE,
        HOST_ENSURE,
        SESSION_ENSURE_PROVIDER_ATTACHMENT,
        EVIDENCE_HARVEST,
        WORKSPACE_PUBLISH,
        CLEANUP_EXECUTE,
        COMPATIBILITY_VERIFY,
        STUCK_STATE_INSPECT,
    }
)


# --- Closed bounded-attribute vocabulary ------------------------------------

# Every attribute key an Omnigent span may carry. The set is closed so an
# instrumentation site cannot introduce an unbounded/identity attribute by
# passing an arbitrary key. Values are additionally range-checked by
# :func:`sanitize_span_attributes`.
SAFE_SPAN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "runtime",
        "harness",
        "host_mode",
        "command_class",
        "decision_class",
        "desired_state",
        "durable_state",
        "observed_state",
        "resulting_state",
        "reason_code",
        "expected_revision",
        "resulting_revision",
        "fencing_generation_class",
        "fencing_generation_ordinal",
        "attempt_ordinal",
        "provider_status_class",
        "observation_source",
        "observation_schema_version",
        "terminal_evidence_kind",
        "compatibility_digest",
        "image_manifest_digest",
        "retry_outcome",
        "delivery_unknown_outcome",
        "cleanup_outcome",
    }
)

# Maximum length of a string attribute value. A digest (sha256 hex) is 64 chars;
# anything materially longer than that is treated as a potential unbounded
# payload and dropped rather than emitted.
MAX_ATTRIBUTE_VALUE_LEN = 96

# Secret-like / forbidden-content markers. A value matching any of these is
# dropped: it may be a credential, a presigned URL, a host path, or an
# unbounded provider payload rather than a bounded classification code.
_FORBIDDEN_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ghp_|github_pat_|AKIA|AIza|ATATT"),  # credential prefixes
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # private key block
    re.compile(r"[?&](?:x-amz-|sig=|signature=)", re.IGNORECASE),  # presigned URL query params
    # Credential assignments and auth headers, matched independently of URL-query
    # punctuation: ``token=secret``, ``password: secret``, ``api_key=...``, an
    # ``Authorization:`` header, etc. are all dropped whether or not they follow
    # a ``?``/``&``. A bounded classification code never contains one of these.
    re.compile(
        r"(?:token|password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|"
        r"client[_-]?secret|private[_-]?key|authorization|session[_-]?id)"
        r"\s*[=:]",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[\s\"'=:])[Bb]earer\s+\S"),  # bearer token value
    re.compile(r"https?://"),  # any URL (links belong in server-authored refs, not span attrs)
    re.compile(r"(?:^|[\s\"'])/(?:home|root|work|var|tmp|etc|mnt|usr)/"),  # host filesystem path
    re.compile(r"\n"),  # multi-line values are transcripts/diffs, not bounded codes
)


def is_omnigent_span(name: str) -> bool:
    """True when ``name`` is a recognized Omnigent span name."""

    return name in OMNIGENT_SPANS


def _safe_attribute_value(value: Any) -> Optional[Any]:
    """Return a bounded, secret-free attribute value, or ``None`` to drop it."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if value is None:
        return None
    text = value.value if hasattr(value, "value") and isinstance(value.value, str) else str(value)
    if len(text) > MAX_ATTRIBUTE_VALUE_LEN:
        return None
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        if pattern.search(text):
            return None
    return text


def sanitize_span_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Filter ``attributes`` to the closed, bounded, secret-free vocabulary.

    Unknown keys are dropped (closed vocabulary); forbidden or oversized values
    are dropped (fail closed). The function never raises so an instrumentation
    site can pass raw values without a correctness risk.
    """

    safe: dict[str, Any] = {}
    for key, raw in attributes.items():
        if key not in SAFE_SPAN_ATTRIBUTES:
            continue
        value = _safe_attribute_value(raw)
        if value is None:
            continue
        safe[key] = value
    return safe


def _tracer() -> Any:
    """Return the OpenTelemetry tracer, or ``None`` when unavailable.

    Importing/using OpenTelemetry is optional: if the package is not installed
    or not initialized, spans degrade to no-ops so telemetry can never be a
    hard dependency of the control plane.
    """

    try:  # pragma: no cover - exercised indirectly
        from opentelemetry import trace  # type: ignore

        return trace.get_tracer("moonmind.omnigent.control_plane")
    except Exception:  # pragma: no cover - defensive; telemetry must not fail hard
        return None


@contextmanager
def omnigent_span(name: str, /, **attributes: Any) -> Iterator[None]:
    """Emit one bounded Omnigent span around an infrastructure/activity boundary.

    ``name`` must be a member of :data:`OMNIGENT_SPANS`; an unknown name fails
    closed to a no-op (and is logged) rather than emitting an ad-hoc span. All
    attributes are sanitized through :func:`sanitize_span_attributes`.

    The context manager never raises from telemetry: a missing tracer or an
    exporter error is swallowed, so wrapping a boundary in ``omnigent_span``
    cannot change the boundary's success or failure. The wrapped block's own
    exceptions still propagate (and are recorded on the span when possible).
    """

    if name not in OMNIGENT_SPANS:
        logger.warning("omnigent.span.unknown_name", extra={"span_name_rejected": True})
        yield
        return

    safe = sanitize_span_attributes(attributes)
    tracer = _tracer()
    if tracer is None:
        # No exporter plane: still yield so the wrapped work runs unchanged.
        yield
        return

    try:  # pragma: no cover - depends on OTel runtime
        span_cm = tracer.start_as_current_span(name)
        span = span_cm.__enter__()
    except Exception:  # pragma: no cover - telemetry must not fail hard
        yield
        return

    exc_info: tuple[Any, Any, Any] = (None, None, None)
    try:
        try:
            for key, value in safe.items():
                span.set_attribute(f"omnigent.{key}", value)
        except Exception:  # pragma: no cover - defensive
            # Setting a bounded attribute must never fail the wrapped work.
            pass
        yield
    except BaseException as exc:  # record then re-raise: the work's error is real
        # Capture the real exception (including cancellations that bypass the
        # ``Exception`` hierarchy) so the span is closed with the actual error
        # tuple below and OpenTelemetry records its error status instead of a
        # normal, successful completion.
        exc_info = (type(exc), exc, exc.__traceback__)
        try:  # pragma: no cover - telemetry must not fail hard
            span.record_exception(exc)
        except Exception:
            # Recording the exception on the span is best-effort telemetry.
            pass
        raise
    finally:
        try:  # pragma: no cover - telemetry must not fail hard
            span_cm.__exit__(*exc_info)
        except Exception:
            # Closing the span/exporter is best-effort and must not fail hard.
            pass


__all__ = [
    "OMNIGENT_SPANS",
    "SAFE_SPAN_ATTRIBUTES",
    "MAX_ATTRIBUTE_VALUE_LEN",
    "INTENT_COMPILE",
    "SESSION_RECONCILE",
    "OBSERVATION_LOAD",
    "PROVIDER_OBSERVE_SNAPSHOT",
    "PROVIDER_READ_EVENT_BATCH",
    "TURN_SUBMIT",
    "COMMAND_EXECUTE",
    "PROFILE_LEASE_ENSURE",
    "HOST_ENSURE",
    "SESSION_ENSURE_PROVIDER_ATTACHMENT",
    "EVIDENCE_HARVEST",
    "WORKSPACE_PUBLISH",
    "CLEANUP_EXECUTE",
    "COMPATIBILITY_VERIFY",
    "STUCK_STATE_INSPECT",
    "is_omnigent_span",
    "sanitize_span_attributes",
    "omnigent_span",
]
