"""Immutable admission-time binding of execution-sensitive effective settings.

Source issue: MoonLadderStudios/MoonMind#3941 (plan step 4, acceptance REQ-04).

Edits to product settings affect newly admitted work or an explicitly
supported operation — never the replay of an old history. This module freezes
the execution-sensitive effective values (plus their provenance) that were in
force when a unit of work was admitted, so API and worker restarts, UI saves,
and settings reloads cannot silently change the policy an active execution
runs under.

Change classes (one per execution-sensitive key):

* ``live`` – safe to apply to in-flight work without restart or drain.
* ``next_admission`` – applies to newly admitted work only; active executions
  keep the recorded snapshot.
* ``worker_reload`` – needs a worker reload; never a silent in-place switch.
* ``process_restart`` – needs a process restart to take effect.
* ``manual_operation`` – takes effect only through an explicit supported
  operational command, not through a settings save.
* ``credential_lifecycle`` – rotates through the Secrets System / provider
  profile lifecycle, never by editing a generic value.

No execution-sensitive key in this snapshot requires a queue drain
(``requires_drain`` is uniformly ``False``); if a future key does, its entry
must declare it here and the retention check must treat a missed drain as a
mismatch.

The snapshot carries SecretRef *references* (``db://...``/``env://...``),
never plaintext — the same contract as the settings backup surface. The
diagnostic projection (:meth:`EffectivePolicySnapshot.to_diagnostic_dict`)
additionally redacts even the reference for secret-typed keys and reports
presence only, so captured diagnostics never leak credential-adjacent values.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

SNAPSHOT_SCHEMA_VERSION = 1

SnapshotChangeClass = Literal[
    "live",
    "next_admission",
    "worker_reload",
    "process_restart",
    "manual_operation",
    "credential_lifecycle",
]

# Execution-sensitive settings keys bound at admission, with their change
# class. ``requires_drain`` stays False for every current key: none of these
# settings needs a queue drain to change safely.
EXECUTION_SENSITIVE_KEYS: dict[str, dict[str, Any]] = {
    "workflow.default_runtime": {
        "change_class": "next_admission",
        "requires_drain": False,
    },
    "workflow.default_publish_mode": {
        "change_class": "next_admission",
        "requires_drain": False,
    },
    "workflow.moonspec_environment_blocked_publish_action": {
        "change_class": "next_admission",
        "requires_drain": False,
    },
    "skills.policy_mode": {
        "change_class": "worker_reload",
        "requires_drain": False,
    },
    "skills.canary_percent": {
        "change_class": "next_admission",
        "requires_drain": False,
    },
    "integrations.github.token_ref": {
        "change_class": "credential_lifecycle",
        "requires_drain": False,
    },
    "workflow.default_provider_profile_ref": {
        "change_class": "credential_lifecycle",
        "requires_drain": False,
    },
    "workflow.operation_mode": {
        "change_class": "manual_operation",
        "requires_drain": False,
    },
}

# Keys whose values are redacted even as references in diagnostic projections.
REDACTED_DIAGNOSTIC_KEYS = frozenset({"integrations.github.token_ref"})


class SnapshotEntry(BaseModel):
    """One frozen execution-sensitive effective value."""

    model_config = ConfigDict(frozen=True)

    key: str
    value: Any = None
    source: str
    value_version: int = 1
    apply_mode: str
    change_class: SnapshotChangeClass
    requires_drain: bool = False


class EffectivePolicySnapshot(BaseModel):
    """Immutable policy record bound at admission time."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: UUID = Field(default_factory=uuid4)
    taken_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    scope: str
    schema_version: int = SNAPSHOT_SCHEMA_VERSION
    entries: tuple[SnapshotEntry, ...]
    policy_hash: str

    def to_diagnostic_dict(self) -> dict[str, Any]:
        """Secret-free projection for logs, artifacts, and API diagnostics.

        Secret-typed keys report presence only; every other entry reports its
        recorded value, source, and change class.
        """
        projected: dict[str, Any] = {}
        for entry in self.entries:
            if entry.key in REDACTED_DIAGNOSTIC_KEYS:
                projected[entry.key] = {
                    "present": entry.value is not None,
                    "source": entry.source,
                    "change_class": entry.change_class,
                }
            else:
                projected[entry.key] = {
                    "value": entry.value,
                    "source": entry.source,
                    "change_class": entry.change_class,
                }
        return {
            "snapshot_id": str(self.snapshot_id),
            "taken_at": self.taken_at.isoformat(),
            "scope": self.scope,
            "schema_version": self.schema_version,
            "policy_hash": self.policy_hash,
            "entries": projected,
        }


def _canonical_hash(digest_material: Mapping[str, Any]) -> str:
    canonical = json.dumps(digest_material, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bind_effective_policy_snapshot(effective_response: Any) -> EffectivePolicySnapshot:
    """Freeze the execution-sensitive values of a catalog effective response.

    Accepts an ``EffectiveSettingsResponse`` (or any object exposing ``scope``
    and a ``values`` mapping of key -> value object with ``value``,
    ``source``, ``value_version`` and ``apply_mode`` attributes). Raises
    ``ValueError`` when an execution-sensitive key is absent so a partial
    snapshot can never masquerade as a complete admission record.
    """
    scope = getattr(effective_response, "scope")
    values = getattr(effective_response, "values")
    missing = [key for key in EXECUTION_SENSITIVE_KEYS if key not in values]
    if missing:
        raise ValueError(
            "cannot bind an admission policy snapshot without "
            f"execution-sensitive keys: {sorted(missing)}"
        )
    digest_material: dict[str, Any] = {}
    snapshot_entries: list[SnapshotEntry] = []
    for key in sorted(EXECUTION_SENSITIVE_KEYS):
        item = values[key]
        value = getattr(item, "value")
        source = getattr(item, "source")
        value_version = getattr(item, "value_version", 1)
        apply_mode = getattr(item, "apply_mode")
        change_class = EXECUTION_SENSITIVE_KEYS[key]["change_class"]
        digest_material[key] = [value, source, value_version]
        snapshot_entries.append(
            SnapshotEntry(
                key=key,
                value=value,
                source=source,
                value_version=value_version,
                apply_mode=apply_mode,
                change_class=change_class,
                requires_drain=bool(
                    EXECUTION_SENSITIVE_KEYS[key]["requires_drain"]
                ),
            )
        )
    return EffectivePolicySnapshot(
        scope=str(scope),
        entries=tuple(snapshot_entries),
        policy_hash=_canonical_hash(digest_material),
    )


def snapshot_diff(
    snapshot: EffectivePolicySnapshot, effective_response: Any
) -> list[str]:
    """Return the sorted execution-sensitive keys whose current effective
    state (value, source, or version) no longer matches the snapshot."""
    values = getattr(effective_response, "values")
    changed: list[str] = []
    for entry in snapshot.entries:
        item = values.get(entry.key)
        if (
            item is None
            or getattr(item, "value", None) != entry.value
            or getattr(item, "source", None) != entry.source
            or getattr(item, "value_version", 1) != entry.value_version
        ):
            changed.append(entry.key)
    return sorted(changed)


def snapshot_matches(
    snapshot: EffectivePolicySnapshot, effective_response: Any
) -> bool:
    """Whether the current effective state still equals the recorded policy."""
    return not snapshot_diff(snapshot, effective_response)


# Memo keys carrying the admission snapshot with the admitted unit of work.
# The full storable payload (not just the hash) travels in memo so a later
# replay/restart check can diff current effective state against the recorded
# policy without re-resolving history; the hash key keeps the common
# "which policy admitted this run" lookup cheap.
ADMISSION_MEMO_SNAPSHOT_KEY = "effectivePolicySnapshot"
ADMISSION_MEMO_HASH_KEY = "effectivePolicyHash"


def bind_admission_snapshot(
    *,
    scope: str = "workspace",
    settings: Any = None,
    env: Mapping[str, str] | None = None,
) -> EffectivePolicySnapshot:
    """Bind the admission snapshot from ambient catalog effective state.

    Used at work admission (``TemporalExecutionService.create_execution``):
    resolves the current effective values through the existing typed
    catalog/resolver and freezes the execution-sensitive keys. ``settings``
    and ``env`` are test hooks; production callers leave them unset so the
    ambient application settings and deployment environment apply.
    """
    return bind_effective_policy_snapshot(
        current_effective_response(scope=scope, settings=settings, env=env)
    )


def current_effective_response(
    *,
    scope: str = "workspace",
    settings: Any = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    """Resolve the current catalog effective response for drift checks.

    Shares the :func:`bind_admission_snapshot` resolution path so replay /
    restart comparisons observe exactly what a fresh admission would bind.
    """
    from api_service.services.settings_catalog import SettingsCatalogService

    if settings is None and env is None:
        service = SettingsCatalogService()
    else:
        service = SettingsCatalogService(settings=settings, env=env)
    return service.effective_values(scope=scope)  # type: ignore[arg-type]


def snapshot_to_storable_payload(snapshot: EffectivePolicySnapshot) -> dict[str, Any]:
    """Serialize a snapshot into a JSON-safe memo/parameter payload."""
    return snapshot.model_dump(mode="json")


def snapshot_from_storable_payload(payload: Mapping[str, Any]) -> EffectivePolicySnapshot:
    """Restore a snapshot previously stored with :func:`snapshot_to_storable_payload`."""
    return EffectivePolicySnapshot.model_validate(dict(payload))


def admission_snapshot_from_memo(
    memo: Mapping[str, Any] | None,
) -> EffectivePolicySnapshot | None:
    """Restore the admission snapshot carried by an admitted unit, if any.

    Returns ``None`` for executions admitted before snapshot binding existed
    (replay/in-flight compatibility: old records stay readable and keep
    running under whatever policy admitted them; only newly admitted work
    carries a recorded snapshot).
    """
    if not isinstance(memo, Mapping):
        return None
    payload = memo.get(ADMISSION_MEMO_SNAPSHOT_KEY)
    if not isinstance(payload, Mapping) or not payload:
        return None
    try:
        return snapshot_from_storable_payload(payload)
    except Exception:
        return None


def describe_admission_policy_drift(
    snapshot: EffectivePolicySnapshot, effective_response: Any
) -> list[dict[str, Any]]:
    """Describe current-vs-recorded drift with the documented change class.

    Each entry names the drifted key, its change class (live,
    next_admission, worker_reload, process_restart, manual_operation,
    credential_lifecycle) and whether a drain is required. An empty list
    means the active execution still runs under its recorded policy.
    """
    drifted = snapshot_diff(snapshot, effective_response)
    described: list[dict[str, Any]] = []
    for key in drifted:
        spec = EXECUTION_SENSITIVE_KEYS.get(key, {})
        described.append(
            {
                "key": key,
                "change_class": spec.get("change_class", "unknown"),
                "requires_drain": bool(spec.get("requires_drain", False)),
            }
        )
    return described


__all__ = [
    "ADMISSION_MEMO_HASH_KEY",
    "ADMISSION_MEMO_SNAPSHOT_KEY",
    "EXECUTION_SENSITIVE_KEYS",
    "REDACTED_DIAGNOSTIC_KEYS",
    "SNAPSHOT_SCHEMA_VERSION",
    "EffectivePolicySnapshot",
    "SnapshotChangeClass",
    "SnapshotEntry",
    "admission_snapshot_from_memo",
    "bind_admission_snapshot",
    "bind_effective_policy_snapshot",
    "current_effective_response",
    "describe_admission_policy_drift",
    "snapshot_diff",
    "snapshot_from_storable_payload",
    "snapshot_matches",
    "snapshot_to_storable_payload",
]
