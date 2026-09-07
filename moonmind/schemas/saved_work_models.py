"""Compact saved-work manifest contracts (GitHub issue #4015).

Implements ``CONTRACT-012`` / ``QUALITY-004`` from
``docs/RepositoryAccessAndWorkspaceDesign.md`` (plan slice 4).

The manifest is a compact index of verified artifact/checkpoint evidence,
not a second storage service. It extends -- but does not replace -- the
narrower managed-checkpoint contracts in
:mod:`moonmind.schemas.managed_checkpoint_models` (which capture one
``worktree_archive`` only).

Design notes honoring the issue brief:

- One compact manifest records schema/logical identity/capture identity,
  source provenance, content/file-manifest digests, capture policy/version,
  required/optional output formats, exclusions, scan disposition, and
  artifact dependencies. Git baseline/head/patch/bundle fields stay
  optional so report-only or non-Git tasks never synthesize commits.
- ACL/retention resolve through artifact ownership (``owner_scope`` /
  ``retention_class`` references), never as editable copied policy text in
  the manifest.
- Qualifying checkpoint bytes and logical references may be reused via
  ``reused_checkpoint_ref`` / ``reused_checkpoint_digest``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

SAVED_WORK_MANIFEST_SCHEMA_VERSION: Literal["v1"] = "v1"
SAVED_WORK_MANIFEST_CONTENT_TYPE = (
    "application/vnd.moonmind.saved-work-manifest+json;version=1"
)

SavedWorkOutputState = Literal[
    "self-contained",
    "requires-dependencies",
    "inapplicable",
    "incomplete",
    "failed",
]
"""Per-output terminal taxonomy (req 2).

- ``self-contained``: restorable without further authorization or objects.
- ``requires-dependencies``: usable only with the named immutable
  dependencies (baseline commit, external object store, LFS, submodules).
- ``inapplicable``: the format was not requested for this result kind
  (e.g. no bundle for a report-only task) -- never a broken download.
- ``incomplete``: requested but missing inputs/objects; blocks save when
  required.
- ``failed``: attempted but did not verify; blocks save when required.
"""

SavedWorkFormatKind = Literal[
    "full_snapshot",
    "baseline_delta",
    "selected_history",
    "worktree_archive",
    "report",
]
"""Small supported format profile (req 2).

``full_snapshot`` is a complete export, ``baseline_delta`` is an
exact-baseline binary-safe delta, ``selected_history`` is a scoped
history/bundle export, ``worktree_archive`` reuses checkpoint bytes, and
``report`` is a non-workspace execution summary.
"""

SavedWorkSourceKind = Literal[
    "managed-workspace",
    "scratch",
    "anonymous-repository",
    "connection-repository",
    "unrelated-repository-import",
]


class SavedWorkFormatEntry(BaseModel):
    """One output in the small format profile derived from the request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: SavedWorkFormatKind
    state: SavedWorkOutputState
    required: bool = False
    artifact_ref: str | None = Field(None, alias="artifactRef", max_length=500)
    digest: str | None = Field(None, min_length=1, max_length=200)
    size_bytes: int | None = Field(None, alias="sizeBytes", ge=0)
    dependencies: list[str] = Field(default_factory=list, max_length=25)
    reason: str | None = Field(None, max_length=500)

    @field_validator("artifact_ref", "digest", "reason")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SavedWorkFormatProfile(BaseModel):
    """The requested result's small format profile."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    formats: list[SavedWorkFormatEntry] = Field(min_length=1, max_length=10)

    def entry_for(self, kind: SavedWorkFormatKind) -> SavedWorkFormatEntry | None:
        for entry in self.formats:
            if entry.kind == kind:
                return entry
        return None

    def required_blockers(self) -> list[str]:
        """Names of required formats that are not usable self-contained output."""
        blockers: list[str] = []
        for entry in self.formats:
            if not entry.required:
                continue
            if entry.state in {"incomplete", "failed"}:
                blockers.append(entry.kind)
            elif entry.state == "requires-dependencies" and not entry.dependencies:
                blockers.append(entry.kind)
        return blockers

    def is_savable(self) -> bool:
        return not self.required_blockers()


class SavedWorkGitRefs(BaseModel):
    """Optional Git baseline/head/patch/bundle references."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    baseline_commit: str | None = Field(None, alias="baselineCommit", max_length=100)
    head_commit: str | None = Field(None, alias="headCommit", max_length=100)
    patch_ref: str | None = Field(None, alias="patchRef", max_length=500)
    patch_digest: str | None = Field(None, alias="patchDigest", max_length=200)
    bundle_ref: str | None = Field(None, alias="bundleRef", max_length=500)
    bundle_digest: str | None = Field(None, alias="bundleDigest", max_length=200)
    bundle_self_contained: bool | None = Field(None, alias="bundleSelfContained")


class SavedWorkScanDisposition(BaseModel):
    """Scan evidence bound to the export digest with coverage/limitations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    disposition: Literal["clean", "blocked", "unsupported", "not-applicable"]
    policy_ref: str = Field(alias="policyRef", min_length=1, max_length=300)
    export_digest: str = Field(alias="exportDigest", min_length=1, max_length=200)
    scanned_bytes: int = Field(alias="scannedBytes", ge=0)
    unscanned_bytes: int = Field(alias="unscannedBytes", ge=0)
    limitations: list[str] = Field(default_factory=list, max_length=10)


class SavedWorkManifest(BaseModel):
    """Compact immutable saved-work manifest (req 1)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    schema_digest: str = Field(alias="schemaDigest", min_length=1, max_length=200)
    capture_id: str = Field(alias="captureId", min_length=1, max_length=500)
    identity: dict[str, Any] = Field(min_length=1)
    source_kind: SavedWorkSourceKind = Field(alias="sourceKind")
    source_identity_digest: str = Field(
        alias="sourceIdentityDigest", min_length=1, max_length=200
    )
    content_digest: str = Field(alias="contentDigest", min_length=1, max_length=200)
    file_manifest_digest: str = Field(
        alias="fileManifestDigest", min_length=1, max_length=200
    )
    capture_policy: str = Field(alias="capturePolicy", min_length=1, max_length=300)
    capture_policy_version: str = Field(
        alias="capturePolicyVersion", min_length=1, max_length=100
    )
    capture_generation: str = Field(
        alias="captureGeneration", min_length=1, max_length=200
    )
    formats: SavedWorkFormatProfile
    exclusions: list[str] = Field(default_factory=list, max_length=100)
    scan: SavedWorkScanDisposition
    artifact_dependencies: list[str] = Field(
        default_factory=list, alias="artifactDependencies", max_length=50
    )
    git: SavedWorkGitRefs | None = None
    reused_checkpoint_ref: str | None = Field(
        None, alias="reusedCheckpointRef", max_length=500
    )
    reused_checkpoint_digest: str | None = Field(
        None, alias="reusedCheckpointDigest", max_length=200
    )
    owner_scope: str = Field(alias="ownerScope", min_length=1, max_length=500)
    retention_class: str = Field(alias="retentionClass", min_length=1, max_length=100)

    @field_validator(
        "capture_id",
        "capture_policy",
        "capture_policy_version",
        "capture_generation",
        "owner_scope",
        "retention_class",
        mode="after",
    )
    @classmethod
    def _non_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


def saved_work_manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Deterministic identity of a saved-work manifest payload."""
    payload = json.dumps(
        dict(manifest), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()
