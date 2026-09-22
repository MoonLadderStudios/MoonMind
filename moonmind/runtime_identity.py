"""Single canonical runtime-identity boundary for legacy alias migration.

Source issue: MoonLadderStudios/MoonMind#3934.

New producers write canonical runtime ids (``codex_cli``, ``claude_code``,
``jules``, ``omnigent``). This module owns the one legacy-alias map shared
by settings ingress, submission admission, and the runtime selection
boundary, so canonical writes never depend on several independently
maintained maps.

It deliberately imports only the standard library: low-level config
modules share it without importing the workflow package (which would be a
dependency cycle via ``prepared_context``).

Runtime ids, harness ids, provider ids, and executable/display names are
different domains. ``codex`` as a runtime normalizes to ``codex_cli``,
while ``codex`` as a harness still resolves to ``codex-native`` through
``harness_registry`` and vendor commands are untouched. This map must never
be applied to those other domains, and unknown values are returned as-is
(lowercased) so admission layers — not this helper — reject them with a
precise correction before resource effects.
"""

from __future__ import annotations

DEFAULT_WORKFLOW_RUNTIME = "omnigent"

#: The single legacy-alias map. Canonical ids map to themselves implicitly.
RUNTIME_ALIASES: dict[str, str] = {
    "codex": "codex_cli",
    "claude": "claude_code",
    "jules_api": "jules",
}


def _clean_optional_string(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def normalize_runtime_id(runtime: object) -> str:
    """Return the canonical managed runtime id for *runtime*.

    Applies the single legacy-alias map and lowercases the result. Unknown
    values are returned as-is (lowercased) so callers can handle them
    gracefully; admission boundaries reject unknown explicit input instead
    of falling back to another runtime.
    """
    key = (_clean_optional_string(runtime) or DEFAULT_WORKFLOW_RUNTIME).lower()
    return RUNTIME_ALIASES.get(key, key)


__all__ = [
    "DEFAULT_WORKFLOW_RUNTIME",
    "RUNTIME_ALIASES",
    "normalize_runtime_id",
]
