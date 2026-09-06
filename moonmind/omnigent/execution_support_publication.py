"""Build the published protected execution-support index.

Source issue: MoonLadderStudios/MoonMind#3885.

The protected conformance run is the only authority that may write this index.
This module owns *how* the index is derived from that run's acceptance manifest
so the derivation is a production contract with tests rather than a script
embedded in a workflow file.

Two properties are structural here:

* **A combination that did not pass is recorded, not omitted.** An operator
  reading the index must be able to tell a combination that failed from one
  that was never attempted. :class:`ExecutionSupportRowStatus` makes the
  outcome recordable and
  :func:`~moonmind.omnigent.execution_support_evidence.validate_protected_execution_support_evidence`
  refuses every non-pass row at admission, so widening what is *recorded* never
  widens what is *admitted*.
* **The concurrency dimension travels on the row it qualified.** A concurrency
  record is attached to the entry whose ``supportCombinationKey`` it names and
  to no other, so the combined exact-support matrix is the existing index plus
  one field rather than a second registry.

Which outcomes reach this module is the caller's decision, not this module's.
The protected live-conformance publish job validates the acceptance manifest
first, and that validator refuses a claimed combination that did not pass, so
that caller supplies only passing combinations today. This module records
whatever it is given, so a caller that publishes an outcome index for a matrix
that did not fully pass gets truthful rows without a second schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from moonmind.omnigent.concurrency_qualification import (
    CONCURRENCY_QUALIFICATION_RECORD_VERSION,
    ConcurrencyQualificationRecord,
)
from moonmind.omnigent.execution_support_evidence import (
    EXECUTION_SUPPORT_EVIDENCE_ISSUER,
    EXECUTION_SUPPORT_EVIDENCE_VERSION,
    ExecutionSupportRowStatus,
    ProtectedExecutionSupportEvidence,
)

EXECUTION_SUPPORT_INDEX_VERSION = (
    "moonmind.omnigent-protected-execution-support-index/v1"
)

#: The exact support-identity fields a published row carries. Declared here so
#: the publisher and the schema cannot drift into disagreeing about what an
#: exact combination is.
SUPPORT_IDENTITY_FIELDS: tuple[str, ...] = (
    "omnigentServerBuildRef",
    "omnigentHostBuildRef",
    "harnessImplementationRef",
    "vendorRuntimeRefs",
    "agentSourceRef",
    "materializerRefs",
    "providerCompatibilityClass",
    "hostClassRef",
    "architecture",
    "launchPolicyRef",
    "modelConfigDigest",
    "executionRealizerRef",
    "requiredCapabilitiesDigest",
)


def _row_status(combination: Mapping[str, Any]) -> ExecutionSupportRowStatus:
    """Return the recordable status for one acceptance-manifest combination.

    An unrecognized outcome is recorded as ``failed`` rather than dropped: an
    outcome the publisher cannot name is not evidence that the combination
    worked.
    """

    raw = str(combination.get("status") or "").strip().lower()
    try:
        return ExecutionSupportRowStatus(raw)
    except ValueError:
        return ExecutionSupportRowStatus.failed


def _support_combination_key(combination: Mapping[str, Any]) -> str:
    """Return the exact combination key this entry names, or ``""``.

    A combination the protected run declares ``unsupported`` -- one that never
    claimed the capability -- carries no ``bindingIdentity`` at all
    (``moonmind/omnigent/workflow_chat_acceptance.py`` refuses one that does).
    There is no exact combination to file such an entry under, so it is not a
    row in an index keyed by ``supportCombinationKey``. That is distinct from a
    combination that ran and did not pass, which has a full identity and *is*
    recorded.
    """

    identity = combination.get("bindingIdentity")
    if not isinstance(identity, Mapping):
        return ""
    return str(identity.get("supportCombinationKey") or "").strip()


def load_concurrency_records(
    root: str | Path,
) -> tuple[ConcurrencyQualificationRecord, ...]:
    """Load and merge every concurrency record staged under ``root``.

    Each qualification layer publishes its own record, so the layers reach the
    publisher as several documents for one combination. They are merged into
    one record per ``supportCombinationKey`` here, because
    :attr:`ConcurrencyQualificationRecord.validated_concurrency_level` is a
    cross-layer property: two single-layer records each validate nothing.

    Two records that disagree about the same ``(layer, level)`` pair are a
    conflict, not a merge: the record model refuses duplicate pairs so a failure
    cannot be hidden behind a pass.
    """

    directory = Path(root)
    if not directory.is_dir():
        return ()
    by_key: dict[str, ConcurrencyQualificationRecord] = {}
    for path in sorted(directory.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        if payload.get("schemaVersion") != CONCURRENCY_QUALIFICATION_RECORD_VERSION:
            # The layer's evidence directory is staged alongside its record;
            # only the record itself carries the record schema version.
            continue
        record = ConcurrencyQualificationRecord.model_validate(payload)
        key = record.identity.support_combination_key
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = record
            continue
        if existing.identity != record.identity:
            raise ValueError(
                "concurrency records for one support combination disagree about "
                "the substrate identity that produced them"
            )
        by_key[key] = ConcurrencyQualificationRecord(
            identity=existing.identity,
            generatedAt=max(existing.generated_at, record.generated_at),
            rows=tuple(existing.rows) + tuple(record.rows),
        )
    return tuple(by_key.values())


def build_protected_support_index(
    acceptance_manifest: Mapping[str, Any],
    *,
    protected_run_ref: str,
    evidence_manifest_ref: str,
    evidence_manifest_digest: str,
    policy_gate_ref_prefix: str,
    feature_generation: str,
    replay_compatibility_version: str,
    rollback_policy_version: str,
    concurrency_records: Sequence[ConcurrencyQualificationRecord] = (),
) -> dict[str, Any]:
    """Derive the published index from one validated acceptance manifest.

    Every combination that names an exact identity becomes a row. A passing
    combination carries admission authority; every other outcome is recorded
    with its real status and ``policyQualified=False`` so the index reports what
    happened without granting anything. A combination the run declares
    ``unsupported`` names no identity and is not a row -- see
    :func:`_support_combination_key`.

    A concurrency record is attached to the entry naming its own
    ``supportCombinationKey``, and only when that entry passed: a combination
    this run recorded as failed does not publish a validated peak. A record
    naming a combination this run does not know about at all is an error --
    silently dropping it would publish an index that claims less than the
    program qualified, with nothing to tell the operator why.
    """

    combinations = acceptance_manifest.get("combinations")
    if not isinstance(combinations, Mapping) or not combinations:
        raise ValueError("acceptance manifest declares no combinations")
    source_commit = str(acceptance_manifest.get("sourceCommit") or "").strip()
    if not source_commit:
        raise ValueError("acceptance manifest declares no source commit")

    by_key = {
        record.identity.support_combination_key: record
        for record in concurrency_records
    }
    published_keys: set[str] = set()
    entries: list[dict[str, Any]] = []
    for combination_id, combination in sorted(combinations.items()):
        support_key = _support_combination_key(combination)
        if not support_key:
            # Declared unsupported by the protected run: no exact combination
            # exists to record, and inventing one would publish an identity
            # nothing ran.
            continue
        identity = combination.get("bindingIdentity") or {}
        status = _row_status(combination)
        published_keys.add(support_key)
        concurrency = by_key.get(support_key)
        candidate = {
            "schemaVersion": EXECUTION_SUPPORT_EVIDENCE_VERSION,
            "evidenceIssuer": EXECUTION_SUPPORT_EVIDENCE_ISSUER,
            "status": status.value,
            "sourceCommit": source_commit,
            "protectedRunRef": protected_run_ref,
            "evidenceManifestRef": evidence_manifest_ref,
            "evidenceManifestDigest": evidence_manifest_digest,
            "generatedAt": acceptance_manifest["generatedAt"],
            "expiresAt": acceptance_manifest["expiresAt"],
            "supportClassification": (
                "connected_host"
                if combination.get("hostMode") == "static_compose"
                else "fully_managed"
            ),
            "supportCombinationKey": support_key,
            "supportIdentity": {
                field: identity.get(field) for field in SUPPORT_IDENTITY_FIELDS
            },
            "hostImageRef": combination.get("hostImageRef"),
            "policySnapshotDigest": identity.get("policySnapshotDigest"),
            "effectiveLaunchSnapshotDigest": identity.get(
                "effectiveLaunchSnapshotDigest"
            ),
            "policyGateRef": f"{policy_gate_ref_prefix}/{combination_id}",
            # Only a passing row may claim qualification; the schema refuses a
            # non-pass row that still says it is qualified.
            "policyQualified": status is ExecutionSupportRowStatus.passed,
            "exactArtifactsVerified": True,
            "featureGeneration": feature_generation,
            "replayCompatibilityVersion": replay_compatibility_version,
            "rollbackPolicyVersion": rollback_policy_version,
        }
        # The concurrency dimension is admission-relevant, so it is published
        # only on a row that itself carries admission authority.
        if concurrency is not None and status is ExecutionSupportRowStatus.passed:
            candidate["concurrency"] = concurrency.as_payload()
        entries.append(
            ProtectedExecutionSupportEvidence.model_validate(candidate).model_dump(
                mode="json", by_alias=True
            )
        )

    if not any(
        entry["status"] == ExecutionSupportRowStatus.passed.value for entry in entries
    ):
        raise ValueError("protected execution support has no passing combinations")
    unknown = sorted(set(by_key) - published_keys)
    if unknown:
        raise ValueError(
            "concurrency evidence names support combinations this run did not "
            f"publish: {unknown}"
        )
    return {
        "schemaVersion": EXECUTION_SUPPORT_INDEX_VERSION,
        "sourceCommit": source_commit,
        "entries": entries,
    }


def write_protected_support_index(
    index: Mapping[str, Any], destination: str | Path
) -> Path:
    """Write one published index document deterministically."""

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def published_statuses(index: Mapping[str, Any]) -> dict[str, str]:
    """Return ``supportCombinationKey -> status`` for one published index."""

    entries: Iterable[Any] = index.get("entries") or ()
    return {
        str(entry["supportCombinationKey"]): str(entry["status"])
        for entry in entries
        if isinstance(entry, Mapping)
    }


__all__ = [
    "EXECUTION_SUPPORT_INDEX_VERSION",
    "SUPPORT_IDENTITY_FIELDS",
    "build_protected_support_index",
    "load_concurrency_records",
    "published_statuses",
    "write_protected_support_index",
]
