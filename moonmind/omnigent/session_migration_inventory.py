"""Safe, machine-readable migration inventory for the Omnigent control plane.

Source issue: MoonLadderStudios/MoonMind#3712.

The inventory classifies every legacy ``OmnigentBridgeSession`` / session record
into exactly one :class:`InventoryClass` from bounded, non-sensitive per-record
evidence, and produces a bounded aggregate report of counts and safe diagnostic
refs.

Safety rule (issue #3712 "Migration inventory"): operator-facing aggregate
reports must never expose provider session IDs or credentials. The
:class:`RecordInventoryView` deliberately carries only booleans, bounded scalar
signals, and an operator-safe ``diagnostic_ref`` — it has no field for a raw
provider session ID, credential, token, or host secret. Callers project the
sensitive persistence rows down to this view at the trusted boundary before
classification.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

INVENTORY_CONTRACT_VERSION = "moonmind.omnigent-migration-inventory/v1"
DEFAULT_DIAGNOSTIC_SAMPLE_LIMIT = 20

# Operator-safe diagnostic references must be bounded, validated artifact refs in
# the canonical ``artifact://`` scheme — never a raw provider session id, token,
# or credential. The bounded charset and length keep the value safe to surface
# verbatim in operator-facing inventory reports.
DIAGNOSTIC_REF_MAX_LENGTH = 256
_DIAGNOSTIC_REF_PATTERN = re.compile(
    r"^artifact://[A-Za-z0-9][A-Za-z0-9._/\-]{0,244}$"
)


class InventoryClass(str, Enum):
    """The eight mutually-exclusive migration-inventory classes (issue #3712)."""

    NEW_MODEL_READY = "new_model_ready"
    LEGACY_ACTIVE = "legacy_active"
    LEGACY_TERMINAL_READABLE = "legacy_terminal_readable"
    CANONICALIZABLE = "canonicalizable"
    ALIAS_REQUIRED = "alias_required"
    AMBIGUOUS_AUTHORITY = "ambiguous_authority"
    CLEANUP_REQUIRED = "cleanup_required"
    UNSUPPORTED_OR_CORRUPT = "unsupported_or_corrupt"


class RecordInventoryView(BaseModel):
    """Bounded, non-sensitive projection of one record for classification.

    This view intentionally excludes provider session IDs, credentials, tokens,
    and host secrets. ``diagnostic_ref`` is an operator-safe artifact reference,
    never a provider identifier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    record_id: str = Field(alias="recordId")
    has_canonical_session: bool = Field(False, alias="hasCanonicalSession")
    is_active: bool = Field(False, alias="isActive")
    is_terminal: bool = Field(False, alias="isTerminal")
    authority_provable: bool = Field(False, alias="authorityProvable")
    immutable_evidence_complete: bool = Field(
        False, alias="immutableEvidenceComplete"
    )
    duplicate_group: bool = Field(False, alias="duplicateGroup")
    conflicting_authority: bool = Field(False, alias="conflictingAuthority")
    requires_chat_alias: bool = Field(False, alias="requiresChatAlias")
    cleanup_pending: bool = Field(False, alias="cleanupPending")
    corrupt: bool = Field(False, alias="corrupt")
    diagnostic_ref: str | None = Field(None, alias="diagnosticRef")

    @field_validator("diagnostic_ref")
    @classmethod
    def _validate_diagnostic_ref(cls, value: str | None) -> str | None:
        """Require a bounded, validated ``artifact://`` reference or ``None``.

        ``extra="forbid"`` only blocks *additional* sensitive fields; it does not
        make the contents of this one safe. A raw provider session id, token, or
        credential accidentally supplied here would otherwise be copied verbatim
        into operator-facing reports, so the value is constrained to a bounded
        artifact-reference format before it is ever accepted or emitted.
        """

        if value is None:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if len(candidate) > DIAGNOSTIC_REF_MAX_LENGTH:
            raise ValueError(
                "diagnostic_ref exceeds the bounded artifact-reference length"
            )
        if not _DIAGNOSTIC_REF_PATTERN.match(candidate):
            raise ValueError(
                "diagnostic_ref must be a bounded 'artifact://' reference, never "
                "a raw provider session id, token, or credential"
            )
        return candidate


class InventoryClassCount(BaseModel):
    """Bounded count plus a bounded sample of safe diagnostic refs for a class."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    inventory_class: InventoryClass = Field(alias="inventoryClass")
    count: int = Field(ge=0)
    diagnostic_refs: tuple[str, ...] = Field(
        default_factory=tuple, alias="diagnosticRefs"
    )


class InventoryReport(BaseModel):
    """Operator-safe aggregate report: bounded counts and safe diagnostic refs."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    contract_version: str = Field(
        INVENTORY_CONTRACT_VERSION, alias="contractVersion"
    )
    total: int = Field(ge=0)
    counts: tuple[InventoryClassCount, ...] = Field(default_factory=tuple)
    quarantined: int = Field(0, ge=0)

    def count_for(self, inventory_class: InventoryClass) -> int:
        for entry in self.counts:
            if entry.inventory_class is inventory_class:
                return entry.count
        return 0

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


def classify_record(view: RecordInventoryView) -> InventoryClass:
    """Classify one record into exactly one class, fail-closed and deterministic.

    Precedence (first match wins):

    1. corrupt / invalid → ``unsupported_or_corrupt``
    2. conflicting authority, or a duplicate group whose authority is not
       provable → ``ambiguous_authority`` (quarantine; never newest-wins)
    3. already on the canonical model with an outstanding chat-binding alias →
       ``alias_required`` (the canonical session exists, but the alias must
       still be created before the record is a no-op)
    4. already on the canonical model with no outstanding alias →
       ``new_model_ready``
    5. active legacy session → ``legacy_active`` (keep on recorded owner; an
       active session is never canonicalized by ownership transfer)
    6. pending cleanup / janitor authority → ``cleanup_required``
    7. terminal with provable authority → ``canonicalizable`` (its planned
       action also creates a safe chat-binding alias when one is needed)
    8. needs a safe chat-binding alias but is not canonicalizable →
       ``alias_required``
    9. terminal without provable authority → ``legacy_terminal_readable``
    10. otherwise (unknown state / missing immutable evidence) →
        ``unsupported_or_corrupt``
    """

    if view.corrupt or not view.immutable_evidence_complete:
        return InventoryClass.UNSUPPORTED_OR_CORRUPT
    if view.conflicting_authority or (
        view.duplicate_group and not view.authority_provable
    ):
        return InventoryClass.AMBIGUOUS_AUTHORITY
    if view.has_canonical_session:
        # A canonical session already exists, but a still-outstanding chat-binding
        # alias must be produced before the record is truly "ready"; otherwise the
        # previously issued chat handle stays unresolved. Only a record with no
        # outstanding alias is new_model_ready (a no-op).
        if view.requires_chat_alias:
            return InventoryClass.ALIAS_REQUIRED
        return InventoryClass.NEW_MODEL_READY
    if view.is_active:
        return InventoryClass.LEGACY_ACTIVE
    if view.cleanup_pending:
        return InventoryClass.CLEANUP_REQUIRED
    if view.is_terminal and view.authority_provable:
        return InventoryClass.CANONICALIZABLE
    if view.requires_chat_alias:
        return InventoryClass.ALIAS_REQUIRED
    if view.is_terminal:
        return InventoryClass.LEGACY_TERMINAL_READABLE
    return InventoryClass.UNSUPPORTED_OR_CORRUPT


def build_inventory_report(
    views: list[RecordInventoryView],
    *,
    sample_limit: int = DEFAULT_DIAGNOSTIC_SAMPLE_LIMIT,
) -> InventoryReport:
    """Aggregate per-record classifications into a bounded, safe report.

    Per-class diagnostic-ref samples are capped at ``sample_limit`` so the
    report stays bounded regardless of input size. Only operator-safe
    ``diagnostic_ref`` values are surfaced — never provider session IDs or
    credentials (the view has no such field).
    """

    if sample_limit < 0:
        raise ValueError("sample_limit must be non-negative")

    counts: dict[InventoryClass, int] = {cls: 0 for cls in InventoryClass}
    samples: dict[InventoryClass, list[str]] = {cls: [] for cls in InventoryClass}

    for view in views:
        cls = classify_record(view)
        counts[cls] += 1
        ref = (view.diagnostic_ref or "").strip()
        if ref and len(samples[cls]) < sample_limit:
            samples[cls].append(ref)

    ordered = [
        InventoryClassCount(
            inventoryClass=cls,
            count=counts[cls],
            diagnosticRefs=tuple(samples[cls]),
        )
        for cls in InventoryClass
    ]
    return InventoryReport(
        contractVersion=INVENTORY_CONTRACT_VERSION,
        total=len(views),
        counts=tuple(ordered),
        quarantined=counts[InventoryClass.AMBIGUOUS_AUTHORITY],
    )


__all__ = [
    "INVENTORY_CONTRACT_VERSION",
    "DEFAULT_DIAGNOSTIC_SAMPLE_LIMIT",
    "InventoryClass",
    "RecordInventoryView",
    "InventoryClassCount",
    "InventoryReport",
    "classify_record",
    "build_inventory_report",
]
