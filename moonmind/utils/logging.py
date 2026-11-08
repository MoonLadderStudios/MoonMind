"""Logging utilities including simple secret redaction helpers."""

from __future__ import annotations

import math
import os
import re
from base64 import b64encode
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote_plus

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:token|secret|password|key|credential|auth|authorization)(?:$|[^a-z0-9])"
)
_GITHUB_TOKEN_PATTERN = re.compile(
    r"(?:ghp|gho|ghu|ghs|ghr|github_pat)[_-][A-Za-z0-9_-]{20,}",
    re.IGNORECASE,
)
_CLOUD_TOKEN_PATTERN = re.compile(
    r"\b(?:AIza[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b"
)
_SLACK_TOKEN_PATTERN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+\b", re.IGNORECASE)
_COOKIE_HEADER_PATTERN = re.compile(r"(?im)\b((?:set-)?cookie\s*:)\s*[^\r\n]+")
_SECRET_ASSIGNMENT_HEAD = (
    r"(?i)(?<![A-Z0-9_.-])(?P<quote>\\*[\"'])?"
    r"(?P<key>[A-Z0-9_.-]{0,80}(?:token|password|passwd|session|cookie|authorization|secret|(?:api|access|private|secret)[_-]?key|credential)"
    r"[A-Z0-9_.-]{0,80})(?(quote)(?P=quote))(?P<separator>\s*[:=]\s*)"
)
_QUOTED_SECRET_VALUE = r"\\*\"(?:\\.|[^\"\\])*\"?|'(?:\\.|[^'\\])*'?"
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    _SECRET_ASSIGNMENT_HEAD
    + r"(?P<value>"
    + _QUOTED_SECRET_VALUE
    + r"|(?(quote)[^\s,;}]+|\S+))"
)
_URL_SECRET_ASSIGNMENT_PATTERN = re.compile(
    _SECRET_ASSIGNMENT_HEAD
    + r"(?P<value>[^\s&#]+)"
)
_SECRET_URL_CONTEXT_PATTERN = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]{0,31}://|(?<![a-z0-9_.-])/)(?=\S)"
)
# The portable retry record carries a public audit marker, not a credential.
# Accept only its exact JSON string grammar; poster/account/history checks
# remain owned by retry reconstruction. Known credential scans run first.
_RETRY_RESET_MARKER_PATTERN = re.compile(
    r'"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?(?:\[bot\])? at '
    r'(?P<instant>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)?)"'
)

_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(?:(?:proxy-)?authorization\s*[:=]\s*(?:basic|token|bearer)\s+|bearer\s+)"
    r"[A-Za-z0-9._~+/=-]+"
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://)[^\s/?#\"'<>`]+@"
)
_PRIVATE_KEY_END = "-----END "
_PRIVATE_KEY_BEGIN_PATTERN = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)\b(?:-----)?", re.IGNORECASE
)
_AUTH_PATH_MARKERS = (
    ".codex",
    ".claude",
    "codex-auth",
    "claude-auth",
    "auth-volume",
)
_NON_SECRET_SENTINEL_VALUES = frozenset(
    {"true", "false", "none", "null", "yes", "no", "on", "off"}
)
# Values shorter than this are never registered as redaction targets. The
# redactor scrubs secrets by naive substring replacement, so a very short
# value carries no meaningful secret entropy yet corrupts unrelated content:
# a boolean-flag env var with a sensitively-named key (for example
# ``OMNIGENT_AUTH_ENABLED=0`` or ``MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION=1``)
# would otherwise turn every ``0``/``1`` in logs, diagnostics, agent summaries,
# issue keys, and commit shas into the placeholder.
_MIN_REDACTABLE_SECRET_LENGTH = 4
_NON_SECRET_REF_KEYS = frozenset(
    {
        "public_key",
        "publickey",
        "public_key_fingerprint",
        "publickeyfingerprint",
        "key_fingerprint",
        "keyfingerprint",
        "auth_actions",
        "authactions",
        "auth_readiness",
        "authreadiness",
        "auth_status_label",
        "authstatuslabel",
        "auth_state",
        "authstate",
        "auth_strategy",
        "authstrategy",
        "credential_source",
        "credentialsource",
        "runtime_materialization_mode",
        "runtimematerializationmode",
    }
)

def _is_sensitive_key(key: str) -> bool:
    return bool(
        _SENSITIVE_KEY_PATTERN.search(key)
        or re.search(r"(?:Token|Secret|Password|Credential|Auth|Authorization)(?:$|[A-Z])", key)
        or re.search(r"(?i)(?:api|access|private|secret)[_-]?key$", key)
    )

def _is_non_secret_sentinel(value: str) -> bool:
    return value.strip().casefold() in _NON_SECRET_SENTINEL_VALUES

def _secret_variants(secret: str) -> set[str]:
    variants = {secret}
    try:
        variants.add(b64encode(secret.encode()).decode())
    except Exception:  # pragma: no cover - defensive
        pass
    try:
        variants.add(quote_plus(secret))
    except Exception:  # pragma: no cover - defensive
        pass
    return variants

def scrub_github_tokens(text: str) -> str:
    """Scrub common provider token-like values from diagnostic text."""

    if not text:
        return ""
    redacted = _GITHUB_TOKEN_PATTERN.sub("[REDACTED]", text)
    return _CLOUD_TOKEN_PATTERN.sub("[REDACTED]", redacted)

def _redact_private_key_blocks(text: str) -> str:
    redacted = text
    while True:
        match = _PRIVATE_KEY_BEGIN_PATTERN.search(redacted)
        if match is None:
            return redacted
        begin = match.start()
        footer = _PRIVATE_KEY_END + match.group("label").upper() + "-----"
        end_marker = redacted.upper().find(footer, match.end())
        if end_marker == -1:
            return redacted[:begin] + "[REDACTED_PRIVATE_KEY]"
        end = end_marker + len(footer)
        redacted = (
            redacted[:begin]
            + "[REDACTED_PRIVATE_KEY]"
            + redacted[end:]
        )

def _redact_auth_paths(text: str) -> str:
    output: list[str] = []
    index = 0
    length = len(text)
    delimiters = set(" \t\r\n\"'")
    while index < length:
        if text[index] in delimiters:
            output.append(text[index])
            index += 1
            continue
        end = index
        while end < length and text[end] not in delimiters:
            end += 1
        token = text[index:end]
        token_lower = token.lower()
        is_path = (
            "/" in token
            or "\\" in token
            or any(
                token_lower.startswith(marker)
                or (not marker.startswith(".") and marker in token_lower)
                for marker in _AUTH_PATH_MARKERS
            )
        )
        if is_path and any(marker in token_lower for marker in _AUTH_PATH_MARKERS):
            output.append("[REDACTED_AUTH_PATH]")
        else:
            output.append(token)
        index = end
    return "".join(output)


def _is_retry_reset_marker(match: re.Match[str]) -> bool:
    value = match.group("value")
    if (
        match.group("key") != "resetAuthorization"
        or match.group("quote") != '"'
        or match.group("separator").strip() != ":"
        or len(value) > 128
    ):
        return False
    if value == '""':
        return True
    marker = _RETRY_RESET_MARKER_PATTERN.fullmatch(value)
    if marker is None:
        return False
    try:
        # Match the audit owner's ISO parser, including naive historical
        # instants and offsets. This also rejects impossible calendar dates.
        datetime.fromisoformat(marker.group("instant"))
    except ValueError:
        return False
    return True


def _redact_secret_assignment(match: re.Match[str]) -> str:
    if _is_retry_reset_marker(match):
        return match.group(0)
    key = match.group("key")
    normalized = key.lower().replace("_", "")
    value = match.group("value")
    quote = match.group("quote") or ""
    compact = re.fullmatch(r"[A-Za-z0-9_./:-]+", value.strip("\"'"))
    if compact and (
        normalized in _NON_SECRET_REF_KEYS
        or normalized.endswith("ref")
        or value.strip("\"'").startswith(("env://", "secret://", "vault://", "ref://"))
        # A compact reference URI is not a credential assignment. Quoted
        # JSON keys and colon-space fields still denote credential values.
        or (normalized == "credential" and not quote and match.group("separator") == ":")
    ):
        return match.group(0)
    replacement = '"[REDACTED]"' if quote and value.startswith('"') else "[REDACTED]"
    return f"{quote}{key}{quote}{match.group('separator')}{replacement}"



def _advance_quoted_context(
    text: str, start: int, end: int, state: tuple[str, str, bool]
) -> tuple[str, str, bool]:
    quote, previous, escaped = state
    for char in text[start:end]:
        if char in "\r\n":
            quote, previous, escaped = "", "", False
        elif escaped:
            escaped = False
        elif quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {"\"", "'"} and (
            previous in {"{", "[", ":", ",", "=", "("}
            or (not previous and char == "\"")
        ):
            quote = char
        if not char.isspace():
            previous = char
    return quote, previous, escaped


def _redact_secret_assignments(text: str) -> str:
    # Plaintext punctuation belongs to the credential. Only actual URL
    # contexts use query delimiters, and a URL used as a secret stays secret.
    output = []
    cursor = scan = quote_scan = 0
    quote = ""
    previous = ""
    escaped = False
    cursor_state = (quote, previous, escaped)
    while (context := _SECRET_URL_CONTEXT_PATTERN.search(text, scan)) is not None:
        # A URI can occur after prose inside a JSON/markup string. Carry its
        # enclosing quote across the original text once, including escapes.
        quote, previous, escaped = _advance_quoted_context(
            text, quote_scan, context.start(), (quote, previous, escaped)
        )
        quote_scan = context.start()
        end = context.end()
        adjacent = text[context.start() - 1] if context.start() else ""
        enclosing = quote or (adjacent if adjacent in {"\"", "'", "`"} else "")
        slashes = 0
        while end < len(text) and not text[end].isspace():
            # Quotes inside query values are credential data. Only a closing
            # enclosing string/markup delimiter bounds this URL context.
            if (
                enclosing in {"\"", "'", "`"}
                and text[end] == enclosing
                and slashes % 2 == 0
            ):
                following = text[end + 1:end + 2]
                if not following or following.isspace() or following in ">,;}]":
                    break
            slashes = slashes + 1 if text[end] == "\\" else 0
            end += 1
        scan = end
        value = text[context.start():end]
        if value.startswith("/") and "?" not in value:
            quote_scan, escaped = end, False
            previous = value[-1]
            continue
        enclosing_assignment = next(
            (
                match
                for match in _SECRET_ASSIGNMENT_PATTERN.finditer(
                    text, cursor, context.end()
                )
                if match.start() < context.start() < match.end()
            ),
            None,
        )
        if enclosing_assignment is not None:
            # Resolve the full value only after identifying its owner. This
            # avoids rescanning later URL fields as part of an earlier value.
            complete = _SECRET_ASSIGNMENT_PATTERN.match(
                text, enclosing_assignment.start()
            )
            end = complete.end()
            output.append(
                _SECRET_ASSIGNMENT_PATTERN.sub(
                    _redact_secret_assignment, text[cursor:end]
                )
            )
            # A consumed secret's own quotes cannot open later containers.
            # Recover the enclosing state just before its complete assignment;
            # each prefix is visited at most twice before `cursor` advances.
            quote, previous, escaped = _advance_quoted_context(
                text, cursor, complete.start(), cursor_state
            )
            previous, escaped = "]", False
            cursor_state = (quote, previous, escaped)
            cursor = scan = quote_scan = end
            continue
        # Query quotes belong to this URI's data, not to the containing
        # record. The actual closing delimiter is still at `end` if present.
        quote_scan, escaped = end, False
        previous = value[-1]
        output.append(
            _SECRET_ASSIGNMENT_PATTERN.sub(
                _redact_secret_assignment, text[cursor:context.start()]
            )
        )
        output.append(
            _URL_SECRET_ASSIGNMENT_PATTERN.sub(_redact_secret_assignment, value)
        )
        cursor = end
        cursor_state = (quote, previous, escaped)
    output.append(
        _SECRET_ASSIGNMENT_PATTERN.sub(_redact_secret_assignment, text[cursor:])
    )
    return "".join(output)

def redact_sensitive_text(text: str | None) -> str:
    """Scrub common credential-shaped values from browser/artifact text."""

    if not text:
        return ""
    redacted = scrub_github_tokens(str(text))
    redacted = _SLACK_TOKEN_PATTERN.sub("[REDACTED]", redacted)
    redacted = _redact_private_key_blocks(redacted)
    redacted = _AUTHORIZATION_PATTERN.sub("[REDACTED_AUTHORIZATION]", redacted)
    redacted = _COOKIE_HEADER_PATTERN.sub(r"\1 [REDACTED]", redacted)
    redacted = _URL_USERINFO_PATTERN.sub(r"\1[REDACTED_USERINFO]@", redacted)
    redacted = _redact_secret_assignments(redacted)
    redacted = _redact_auth_paths(redacted)
    return redacted

def redact_sensitive_payload(payload: Any, *, key: str | None = None) -> Any:
    """Recursively redact sensitive values while preserving compact refs."""

    if payload is None:
        return None
    if isinstance(payload, str):
        raw_key = str(key or "").strip()
        normalized_key = raw_key.lower()
        normalized_key_compact = normalized_key.replace("_", "")
        if (
            normalized_key in _NON_SECRET_REF_KEYS
            or normalized_key_compact in _NON_SECRET_REF_KEYS
        ):
            return payload
        if normalized_key.endswith("_ref") or normalized_key.endswith("ref"):
            return payload
        if _is_sensitive_key(raw_key):
            if payload.startswith(("env://", "secret://", "vault://", "ref://")):
                return payload
            return "[REDACTED]"
        return redact_sensitive_text(payload)
    if isinstance(payload, Mapping):
        return {
            str(nested_key): redact_sensitive_payload(
                nested_value,
                key=str(nested_key),
            )
            for nested_key, nested_value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_sensitive_payload(item, key=key) for item in payload]
    if isinstance(payload, tuple):
        return tuple(redact_sensitive_payload(item, key=key) for item in payload)
    return payload


EVIDENCE_TRANSFORMATION_VERSION = "evidence-redaction/v1"
EVIDENCE_TRANSFORMATION_LIMITATIONS = (
    "Credential patterns, email addresses and explicitly named personal-data fields are redacted.",
    "This is not full anonymization: arbitrary names, private business details and unrecognized personal data may remain.",
    "Only selected authorized evidence should be delivered; redaction grants no access or publication authority.",
)
_EVIDENCE_MAX_TEXT_CHARS = 4 * 1024 * 1024
_EVIDENCE_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    # A valid email local part is at most 64 characters. Bound the scan even
    # when untrusted evidence contains long punctuation runs without an @.
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?![A-Za-z0-9-])"
)
_EVIDENCE_PERSONAL_FIELDS = frozenset(
    {
        "email",
        "emailaddress",
        "phonenumber",
        "phone",
        "telephone",
        "fullname",
        "firstname",
        "lastname",
        "customername",
        "personname",
        "dateofbirth",
        "birthdate",
        "dob",
        "streetaddress",
        "postaladdress",
        "homeaddress",
        "ssn",
        "socialsecuritynumber",
        "passportnumber",
        "driverslicensenumber",
        "driverlicensenumber",
        "nationalid",
    }
)


def redact_evidence_text(
    text: str | None, *, max_chars: int = _EVIDENCE_MAX_TEXT_CHARS
) -> str:
    """Transform bounded evidence text, without claiming general anonymization."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("Evidence text has an unsupported type")  # noqa: TRY004 - stable transformation error contract
    if len(text) > max_chars:
        raise ValueError("Evidence text exceeds the transformation bound")
    return _EVIDENCE_EMAIL_PATTERN.sub("[REDACTED_EMAIL]", redact_sensitive_text(text))


def redact_evidence_payload(
    payload: Any,
    *,
    max_depth: int = 32,
    max_items: int = 10000,
    max_text_chars: int = _EVIDENCE_MAX_TEXT_CHARS,
) -> Any:
    """Copy bounded JSON-like evidence using the existing credential owner.

    Explicit personal-data fields are withheld; generic identifiers such as
    name, path, target and address are not guessed to be personal information.
    Bounds include keys and original text, before any redaction reduces size.
    """
    items = 0
    text_chars = 0

    def text(value):
        nonlocal text_chars
        text_chars += len(value)
        if text_chars > max_text_chars:
            raise ValueError("Evidence text exceeds the transformation bound")
        return redact_evidence_text(value, max_chars=max_text_chars)

    def visit(value, depth=0, key=None):
        nonlocal items
        items += 1
        if depth > max_depth or items > max_items:
            raise ValueError("Evidence structure exceeds the transformation bound")
        personal = (
            key is not None
            and re.sub(r"[^a-z0-9]", "", key.lower()) in _EVIDENCE_PERSONAL_FIELDS
        )
        if isinstance(value, Mapping):
            result = {}
            for raw_key, item in value.items():
                if not isinstance(raw_key, str):
                    raise ValueError("Evidence object has an unsupported key")  # noqa: TRY004 - stable transformation error contract
                safe_key = text(raw_key)
                if safe_key in result:
                    raise ValueError("Evidence keys collide after transformation")
                result[safe_key] = visit(item, depth + 1, raw_key)
        elif isinstance(value, (list, tuple)):
            result = [visit(item, depth + 1, key) for item in value]
        elif isinstance(value, str):
            # Check the original before the credential owner replaces it.
            text(value)
            result = redact_evidence_text(
                redact_sensitive_payload(value, key=key), max_chars=max_text_chars
            )
        elif value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Evidence value is not a finite number")
            result = value
        else:
            raise ValueError("Evidence value has an unsupported type")
        # The credential owner knows value fields versus references. Apply
        # the same decision to non-string values after traversing their bounds.
        if (
            value is not None
            and not isinstance(value, str)
            and key is not None
            and redact_sensitive_payload("evidence-value", key=key) == "[REDACTED]"
        ):
            return "[REDACTED]"
        return "[REDACTED_PERSONAL_DATA]" if personal and value is not None else result

    return visit(payload)


def redact_profile_file_templates(value: list[Any]) -> list[Any]:
    """Redact provider-profile file templates without exposing template bodies."""

    redacted = redact_sensitive_payload(value)
    if not isinstance(redacted, list):
        return []

    templates: list[Any] = []
    for item in redacted:
        if not isinstance(item, Mapping):
            templates.append(item)
            continue
        template = dict(item)
        for content_key in ("content", "contentTemplate", "content_template"):
            if content_key in template:
                template[content_key] = redact_sensitive_payload(
                    template[content_key], key="secret"
                )
        templates.append(template)
    return templates

class SecretRedactor:
    """Utility to scrub sensitive values from log output.

    The redactor performs straightforward string replacement against known secret
    values; it is intentionally lightweight so it can be applied to temporal worker workflows
    artifacts without adding heavy dependencies.
    """

    def __init__(
        self, secrets: Iterable[str] | None = None, placeholder: str = "***"
    ) -> None:
        self._placeholder = placeholder
        seen: set[str] = set()
        unique_secrets: list[str] = []
        for value in secrets or []:
            if not value:
                continue
            if _is_non_secret_sentinel(value):
                continue
            if len(value.strip()) < _MIN_REDACTABLE_SECRET_LENGTH:
                continue
            for variant in _secret_variants(value):
                if variant and variant not in seen:
                    seen.add(variant)
                    unique_secrets.append(variant)
        self._secrets: list[str] = sorted(unique_secrets, key=len, reverse=True)

    @classmethod
    def from_environ(
        cls, *, placeholder: str = "***", extra_secrets: Iterable[str] | None = None
    ) -> "SecretRedactor":
        secrets = []
        for key, value in os.environ.items():
            if _is_sensitive_key(key) and value:
                secrets.append(value)
        if extra_secrets:
            secrets.extend(secret for secret in extra_secrets if secret)
        return cls(secrets=secrets, placeholder=placeholder)

    def scrub(self, text: str | None) -> str:
        if not text:
            return ""
        scrubbed = text
        for secret in self._secrets:
            scrubbed = scrubbed.replace(secret, self._placeholder)
        return scrubbed

    def scrub_sequence(self, values: Sequence[str]) -> list[str]:
        return [self.scrub(value) for value in values]

__all__ = [
    "EVIDENCE_TRANSFORMATION_LIMITATIONS",
    "EVIDENCE_TRANSFORMATION_VERSION",
    "SecretRedactor",
    "redact_evidence_payload",
    "redact_evidence_text",
    "redact_profile_file_templates",
    "redact_sensitive_payload",
    "redact_sensitive_text",
    "scrub_github_tokens",
]
