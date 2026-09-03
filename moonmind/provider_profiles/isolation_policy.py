"""Single backend authority for Provider Profile launch-safety isolation.

MoonLadderStudios/MoonMind#3821

This module owns the derivation, validation, reconciliation, and
launch-boundary resolution of ``clear_env_keys`` (environment-clearing
policy) from runtime / provider / authentication method / credential source /
materialization mode.

All other layers — creation presets (``api_service``), creation
capabilities, credential enrollment, readiness, persistence/projection, the
launch materializer, and the UI contract — must consume this authority
instead of maintaining separate hard-coded copies.

Only key *names* are ever stored or logged here. Secret values are never
accepted, recorded, or emitted by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

ISOLATION_POLICY_SOURCE = "runtime_provider_isolation_policy"
ISOLATION_LOCK_REASON = (
    "Environment clearing is backend-owned launch security policy."
)
MAX_ISOLATION_KEYS = 32
_ENV_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Keys that must never be cleared because they would break the launch or
# clear unrelated process state.
FORBIDDEN_ISOLATION_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "PWD",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "TZ",
        "MOONMIND_WORKSPACE",
        "MOONMIND_RUNTIME",
    }
)

# Every key the backend may derive or accept as an expert override. Unknown
# keys outside this set are rejected for guided strategies; manual expert
# overrides may carry additional regex-valid keys but they are classified as
# legacy_custom and surfaced in readiness instead of being silently dropped.
KNOWN_ISOLATION_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "MINIMAX_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }
)

_KEY_EXPLANATIONS: dict[str, str] = {
    "OPENAI_API_KEY": "Prevents an ambient OpenAI API key from leaking into a non-OpenAI launch.",
    "OPENAI_BASE_URL": "Prevents a stale OpenAI endpoint override from redirecting provider traffic.",
    "OPENAI_ORG_ID": "Prevents a stale OpenAI organization scope from leaking across profiles.",
    "OPENAI_PROJECT": "Prevents a stale OpenAI project scope from leaking across profiles.",
    "ANTHROPIC_API_KEY": "Prevents an ambient Anthropic API key from leaking into a non-Anthropic launch.",
    "ANTHROPIC_AUTH_TOKEN": "Prevents an ambient Anthropic OAuth token from leaking into API-key launches.",
    "ANTHROPIC_BASE_URL": "Prevents a stale Anthropic endpoint override from redirecting provider traffic.",
    "CLAUDE_API_KEY": "Prevents a legacy Claude API key from leaking across credential paths.",
    "CLAUDE_CODE_OAUTH_TOKEN": "Prevents a Claude OAuth token from leaking into API-key launches.",
    "MINIMAX_API_KEY": "Prevents a MiniMax key from leaking into first-party OpenAI/Anthropic launches.",
    "OPENROUTER_API_KEY": "Prevents an OpenRouter key from leaking into first-party launches.",
    "OPENCODE_API_KEY": "Prevents an OpenCode key from leaking into other runtime launches.",
    "OPENCODE_AUTH_CONTENT": "Prevents ambient OpenCode auth content from leaking across launches.",
    "OPENCODE_CONFIG": "Prevents an ambient OpenCode config path from redirecting auth materialization.",
    "OPENCODE_CONFIG_CONTENT": "Prevents ambient OpenCode config content from leaking across launches.",
    "GEMINI_API_KEY": "Prevents an ambient Gemini key from leaking into unrelated launches.",
    "GOOGLE_API_KEY": "Prevents an ambient Google key from leaking into unrelated launches.",
}

# (runtime_id, provider_id, authentication_method) -> derived keys.
# This is the one canonical table. Presets, capabilities, enrollment,
# readiness, and launch must resolve through derive_isolation_policy().
_STRATEGY_TABLE: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("codex_cli", "openai", "oauth"): (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ),
    ("codex_cli", "openai", "api_key"): (
        "OPENAI_BASE_URL",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT",
        "MINIMAX_API_KEY",
    ),
    ("claude_code", "anthropic", "oauth"): (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ),
    ("claude_code", "anthropic", "api_key"): (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_API_KEY",
        "OPENAI_API_KEY",
    ),
    ("opencode", "opencode-go", "api_key"): (
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ),
    ("opencode", "opencode", "api_key"): (
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ),
    ("codex_cli", "openrouter", "api_key"): (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY",
    ),
    ("codex_cli", "minimax", "api_key"): (
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    ),
    ("claude_code", "minimax", "api_key"): (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "MINIMAX_API_KEY",
    ),
}


class IsolationPolicyError(ValueError):
    """Fail-closed error for unproducible or unsafe isolation policy."""

    code = "provider_profile_isolation_policy_error"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass(frozen=True, slots=True)
class IsolationPolicy:
    """Derived launch-safety policy for one strategy."""

    keys: tuple[str, ...]
    source: str = ISOLATION_POLICY_SOURCE
    editable: bool = False
    lock_reason: str = ISOLATION_LOCK_REASON
    strategy_id: str = ""
    explanations: Mapping[str, str] | None = None

    def as_field(self) -> dict[str, Any]:
        return {
            "value": list(self.keys),
            "source": self.source,
            "editable": self.editable,
            "lock_reason": self.lock_reason,
        }


def _normalized(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip()


def normalize_isolation_keys(keys: Iterable[object] | None) -> list[str]:
    """Deduplicate while preserving order; reject blank entries."""
    seen: dict[str, None] = {}
    for key in keys or []:
        name = str(key or "").strip()
        if not name:
            raise IsolationPolicyError(
                "Isolation policy contains a blank environment key.",
                code="provider_profile_isolation_key_blank",
            )
        if name not in seen:
            seen[name] = None
    return list(seen)


def validate_isolation_key_shape(keys: Iterable[object] | None) -> list[str]:
    """Validate shape only: names, duplicates, bounds, forbidden keys."""
    raw = [str(key or "").strip() for key in (keys or [])]
    if len(raw) != len(set(raw)):
        raise IsolationPolicyError(
            "Isolation policy contains duplicate environment keys.",
            code="provider_profile_isolation_key_duplicate",
        )
    if len(raw) > MAX_ISOLATION_KEYS:
        raise IsolationPolicyError(
            f"Isolation policy exceeds {MAX_ISOLATION_KEYS} keys.",
            code="provider_profile_isolation_key_unbounded",
        )
    for name in raw:
        if not _ENV_KEY_PATTERN.match(name):
            raise IsolationPolicyError(
                f"Invalid environment key name: {name!r}.",
                code="provider_profile_isolation_key_invalid",
                field="clear_env_keys",
            )
        if name in FORBIDDEN_ISOLATION_KEYS:
            raise IsolationPolicyError(
                f"Isolation policy must not clear {name!r}.",
                code="provider_profile_isolation_key_unsafe",
                field="clear_env_keys",
            )
    return raw


def derive_isolation_policy(
    *,
    runtime_id: object,
    provider_id: object,
    authentication_method: object,
    credential_source: object | None = None,
    runtime_materialization_mode: object | None = None,
) -> IsolationPolicy | None:
    """Derive the canonical policy for a known strategy, or None if unknown.

    ``authentication_method`` ``none`` (credential-free) derives an empty
    policy. Unknown runtime/provider/method combinations return None so
    callers fail closed instead of guessing.
    """
    runtime = _normalized(runtime_id)
    provider = _normalized(provider_id)
    method = _normalized(authentication_method)
    if method == "none":
        return IsolationPolicy(
            keys=(),
            strategy_id=f"{runtime}/{provider}/none",
            explanations={},
        )
    # Credential-free materialization without an explicit none method still
    # derives an empty policy when the contract is coherent.
    if not method and _normalized(credential_source) == "none":
        return IsolationPolicy(keys=(), strategy_id=f"{runtime}/{provider}/none", explanations={})
    if not runtime or not provider or not method:
        return None
    keys = _STRATEGY_TABLE.get((runtime, provider, method))
    if keys is None:
        return None
    return IsolationPolicy(
        keys=tuple(keys),
        strategy_id=f"{runtime}/{provider}/{method}",
        explanations={k: _KEY_EXPLANATIONS.get(k, "Backend-owned launch isolation.") for k in keys},
    )


def validate_expert_override_keys(
    keys: Iterable[object] | None,
    *,
    policy: IsolationPolicy | None,
) -> list[str]:
    """Validate an explicit expert override against shape + strategy rules."""
    shaped = validate_isolation_key_shape(keys)
    normalized = normalize_isolation_keys(shaped)
    unknown = [k for k in normalized if k not in KNOWN_ISOLATION_KEYS]
    if unknown:
        raise IsolationPolicyError(
            "Isolation override contains unknown environment keys: "
            + ", ".join(sorted(unknown)),
            code="provider_profile_isolation_key_unknown",
            field="clear_env_keys",
            unknown_keys=sorted(unknown),
        )
    if policy is not None and policy.keys:
        missing = [k for k in policy.keys if k not in normalized]
        if missing:
            raise IsolationPolicyError(
                "Isolation override is incompatible with the selected strategy; "
                "it must preserve required keys: " + ", ".join(missing),
                code="provider_profile_isolation_override_incompatible",
                field="clear_env_keys",
                missing_keys=missing,
            )
    return sorted(normalized)


def classify_existing_policy(
    *,
    stored_keys: Iterable[object] | None,
    derived: IsolationPolicy | None,
    credential_free: bool = False,
) -> str:
    """Classify a persisted value without mutating it.

    Returns one of: current | legacy_custom | unsafe_unknown_incomplete |
    empty_safe_only_credential_free | missing_or_stale.
    """
    stored = normalize_isolation_keys(stored_keys or [])
    if not stored:
        if derived is not None and len(derived.keys) == 0:
            return "current"
        if credential_free:
            return "empty_safe_only_credential_free"
        return "missing_or_stale"
    if derived is None:
        # Unknown strategy: preserve, never silently erase. Shape-valid
        # values (including regex-valid custom keys bound to user secrets)
        # are preserved as legacy custom; only malformed/forbidden values
        # block readiness and launch.
        try:
            validate_isolation_key_shape(stored)
        except IsolationPolicyError:
            return "unsafe_unknown_incomplete"
        return "legacy_custom"
    if sorted(stored) == sorted(derived.keys):
        return "current"
    try:
        validate_isolation_key_shape(stored)
    except IsolationPolicyError:
        return "unsafe_unknown_incomplete"
    missing = [k for k in derived.keys if k not in stored]
    if missing:
        return "unsafe_unknown_incomplete"
    # Superset (known or regex-valid custom extras, e.g. CUSTOM_ENV bound to
    # a user secret): intentional legacy custom behavior, preserved and
    # surfaced as a readiness warning without blocking launch.
    return "legacy_custom"


def merge_enrollment_policy(
    *,
    stored_keys: Iterable[object] | None,
    derived: IsolationPolicy | None,
) -> list[str]:
    """Merge derived policy with preserved unknown legacy custom keys.

    Derived keys always win; regex-valid unknown stored keys are preserved
    (never silently discarded); invalid/forbidden keys are dropped because
    the launch boundary would reject them anyway.
    """
    stored = normalize_isolation_keys(stored_keys or [])
    base = list(derived.keys) if derived is not None else []
    merged = list(base)
    for key in stored:
        if key in merged:
            continue
        if not _ENV_KEY_PATTERN.match(key):
            continue
        if key in FORBIDDEN_ISOLATION_KEYS:
            continue
        merged.append(key)
    # Bound the merged list; derived keys take precedence.
    return merged[:MAX_ISOLATION_KEYS]


def resolve_launch_clear_env_keys(
    profile: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Revalidate/rederive the effective policy at the launch boundary.

    Returns (effective_keys, metadata). Raises IsolationPolicyError when a
    required policy cannot be safely produced. Never touches secret values:
    only key names flow through here.
    """
    runtime_id = profile.get("runtime_id")
    provider_id = profile.get("provider_id")
    method = (
        profile.get("authentication_method")
        or profile.get("authenticationMethod")
        or _infer_method_from_contract(profile)
    )
    credential_source = profile.get("credential_source", profile.get("credentialSource"))
    materialization = profile.get(
        "runtime_materialization_mode", profile.get("runtimeMaterializationMode")
    )
    derived = derive_isolation_policy(
        runtime_id=runtime_id,
        provider_id=provider_id,
        authentication_method=method,
        credential_source=credential_source,
        runtime_materialization_mode=materialization,
    )
    # Explicit expert override marker written by the update path.
    behavior = profile.get("command_behavior", profile.get("commandBehavior"))
    override_keys: list[str] | None = None
    if isinstance(behavior, Mapping):
        marker = behavior.get("_isolation_override")
        if isinstance(marker, Mapping) and isinstance(marker.get("keys"), list):
            override_keys = [str(k) for k in marker["keys"]]

    stored_raw = profile.get("clear_env_keys", profile.get("clearEnvKeys"))
    stored = normalize_isolation_keys(stored_raw or []) if stored_raw else []

    if override_keys is not None:
        validated = validate_expert_override_keys(override_keys, policy=derived)
        # Stored value must match the audited override; otherwise stale.
        if sorted(stored) != sorted(validated):
            raise IsolationPolicyError(
                "Persisted isolation policy is stale relative to the audited "
                "expert override; repair the profile before launch.",
                code="provider_profile_isolation_stale",
            )
        return validated, {
            "source": "expert_override",
            "derived": False,
            "strategy_id": derived.strategy_id if derived else "unknown",
        }

    if derived is None:
        # Unknown strategy: a shape-valid manually-shaped policy (including
        # regex-valid custom keys) may launch as preserved legacy custom;
        # empty, malformed, or forbidden values fail closed.
        if not stored:
            raise IsolationPolicyError(
                "No launch-safety isolation policy can be produced for this "
                "runtime/provider/credential contract.",
                code="provider_profile_isolation_missing",
            )
        try:
            validate_isolation_key_shape(stored)
        except IsolationPolicyError as exc:
            raise IsolationPolicyError(
                f"Stored isolation policy is unsafe: {exc.message}",
                code="provider_profile_isolation_invalid",
            ) from exc
        return sorted(stored), {"source": "legacy_custom", "derived": False, "strategy_id": "unknown"}

    classification = classify_existing_policy(
        stored_keys=stored,
        derived=derived,
        credential_free=len(derived.keys) == 0,
    )
    if classification in {"current", "legacy_custom", "empty_safe_only_credential_free"}:
        effective = sorted(stored) if classification == "legacy_custom" else list(derived.keys)
        # For legacy_custom, stored is a superset of derived with known keys;
        # launch with the stored superset (preserves intentional behavior).
        if classification == "legacy_custom":
            effective = sorted(stored)
        return effective, {
            "source": "runtime_provider_isolation_policy"
            if classification != "legacy_custom"
            else "legacy_custom_preserved",
            "derived": classification != "legacy_custom",
            "strategy_id": derived.strategy_id,
        }
    if classification == "missing_or_stale" and len(derived.keys) == 0:
        return [], {
            "source": ISOLATION_POLICY_SOURCE,
            "derived": True,
            "strategy_id": derived.strategy_id,
        }
    raise IsolationPolicyError(
        "Launch-safety isolation policy is missing, stale, or invalid; "
        "repair the profile before launch.",
        code="provider_profile_isolation_stale"
        if stored
        else "provider_profile_isolation_missing",
        strategy_id=derived.strategy_id,
        classification=classification,
    )


def _infer_method_from_contract(profile: Mapping[str, Any]) -> str | None:
    source = _normalized(profile.get("credential_source", profile.get("credentialSource")))
    materialization = _normalized(
        profile.get("runtime_materialization_mode", profile.get("runtimeMaterializationMode"))
    )
    auth_state = _normalized(profile.get("auth_state", profile.get("authState")))
    if source == "oauth_volume" and materialization == "oauth_home":
        return "oauth"
    if source == "secret_ref" and materialization in {"api_key_env", "env_bundle", "composite"}:
        return "api_key"
    if source == "none" and materialization == "composite":
        return "none"
    if auth_state in {"oauth_pending", "api_key_pending"}:
        return None
    return None


def reconciliation_action(
    *,
    stored_keys: Iterable[object] | None,
    derived: IsolationPolicy | None,
    credential_free: bool = False,
) -> dict[str, Any]:
    """Return the explicit migration/reconciliation action for one profile.

    Never mutates; callers decide whether to normalize, preserve, or block.
    Actions: none (already current) | normalize (equivalent derived value,
    safe to rewrite in stored order) | preserve_custom (intentional legacy
    custom behavior; keep and surface a warning) | repair_required (unsafe,
    unknown-malformed, or incomplete; block until repaired).
    """
    stored = normalize_isolation_keys(stored_keys or [])
    classification = classify_existing_policy(
        stored_keys=stored, derived=derived, credential_free=credential_free
    )
    if classification in {"current", "empty_safe_only_credential_free"}:
        if derived is not None and stored != list(derived.keys):
            return {"action": "normalize", "classification": classification}
        return {"action": "none", "classification": classification}
    if classification in {"legacy_custom"}:
        return {"action": "preserve_custom", "classification": classification}
    return {"action": "repair_required", "classification": classification}


def isolation_explanations(keys: Iterable[str]) -> dict[str, str]:
    return {
        k: _KEY_EXPLANATIONS.get(k, "Backend-owned launch isolation.")
        for k in keys
    }


__all__ = [
    "FORBIDDEN_ISOLATION_KEYS",
    "ISOLATION_LOCK_REASON",
    "ISOLATION_POLICY_SOURCE",
    "KNOWN_ISOLATION_KEYS",
    "MAX_ISOLATION_KEYS",
    "IsolationPolicy",
    "IsolationPolicyError",
    "classify_existing_policy",
    "derive_isolation_policy",
    "isolation_explanations",
    "merge_enrollment_policy",
    "normalize_isolation_keys",
    "reconciliation_action",
    "resolve_launch_clear_env_keys",
    "validate_expert_override_keys",
    "validate_isolation_key_shape",
]
