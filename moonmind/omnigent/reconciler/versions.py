"""Versioning and compatibility policy for the Omnigent lifecycle reconciler.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

Every domain object that crosses the reconciler boundary carries an explicit
``schema_version``. The reconciler declares the exact set of versions it
supports and refuses anything else *at construction time* (the "fail" half of
the compatibility policy). Unknown *values* observed at runtime (an unrecognized
provider status or compatibility token) are a separate concern handled inside
:func:`moonmind.omnigent.reconciler.reconcile.reconcile`, which fails **closed**
to observation or quarantine rather than raising or silently succeeding.

This module performs no I/O of any kind.
"""

from __future__ import annotations

INTENT_SCHEMA_VERSION = "moonmind.omnigent.reconciler.intent.v1"
DURABLE_STATE_SCHEMA_VERSION = "moonmind.omnigent.reconciler.durable.v1"
OBSERVATION_SET_SCHEMA_VERSION = "moonmind.omnigent.reconciler.observations.v1"
DECISION_SCHEMA_VERSION = "moonmind.omnigent.reconciler.decision.v1"
REASON_CODE_VERSION = "moonmind.omnigent.reconciler.reason.v1"

# The reconciler intentionally supports exactly one version of each contract.
# MoonMind is pre-release: superseded versions are removed, not aliased. When a
# contract changes, add the new version here and update every caller in the same
# change (see the Compatibility Policy in AGENTS.md).
SUPPORTED_INTENT_VERSIONS = frozenset({INTENT_SCHEMA_VERSION})
SUPPORTED_DURABLE_STATE_VERSIONS = frozenset({DURABLE_STATE_SCHEMA_VERSION})
SUPPORTED_OBSERVATION_SET_VERSIONS = frozenset({OBSERVATION_SET_SCHEMA_VERSION})
SUPPORTED_DECISION_VERSIONS = frozenset({DECISION_SCHEMA_VERSION})


class ReconcilerContractError(Exception):
    """A caller-supplied reconciler input violated the domain contract.

    Raised for structural authority violations that are programming errors, not
    recoverable runtime ambiguity (for example an intent whose canonical session
    identity disagrees with the durable state's identity).
    """


class UnknownSchemaVersionError(ReconcilerContractError):
    """A domain object declared a ``schema_version`` the reconciler cannot serve.

    This is the explicit "fail" policy for unknown envelope versions. Runtime
    ambiguity (unknown provider status, unknown compatibility token) is *not*
    routed here; it is quarantined by the reducer instead.
    """

    def __init__(self, kind: str, version: str, supported: frozenset[str]) -> None:
        self.kind = kind
        self.version = version
        self.supported = supported
        super().__init__(
            f"Unsupported {kind} schema_version {version!r}; "
            f"supported: {sorted(supported)}"
        )


def require_supported_version(
    kind: str, version: str, supported: frozenset[str]
) -> None:
    """Raise :class:`UnknownSchemaVersionError` unless ``version`` is supported."""

    if version not in supported:
        raise UnknownSchemaVersionError(kind, version, supported)


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "DURABLE_STATE_SCHEMA_VERSION",
    "INTENT_SCHEMA_VERSION",
    "OBSERVATION_SET_SCHEMA_VERSION",
    "REASON_CODE_VERSION",
    "ReconcilerContractError",
    "SUPPORTED_DECISION_VERSIONS",
    "SUPPORTED_DURABLE_STATE_VERSIONS",
    "SUPPORTED_INTENT_VERSIONS",
    "SUPPORTED_OBSERVATION_SET_VERSIONS",
    "UnknownSchemaVersionError",
    "require_supported_version",
]
