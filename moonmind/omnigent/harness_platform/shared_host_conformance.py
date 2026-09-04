"""Cross-harness isolation and exact support on the shared host image.

Deterministic exact-artifact conformance for
MoonLadderStudios/MoonMind#3832 (design source #3823 and
``docs/Omnigent/PrimaryRuntimeProviderStrategy.md`` sections 5.7, 5.8, 7,
and 11).

Scope: this module is pure, secret-free, and credentialless. It owns:

- the versioned required-row catalog for every supported combination
  MoonMind claims (Codex, Claude Code, OpenCode on
  ``generic-omnigent-host@1``),
- the shared-image inventory model (contents only, never launch readiness),
- hermetic validators for one-harness admission, credential isolation,
  ownership-aware cleanup, runtime/auth readiness, common lifecycle ordering,
  and failure isolation.

Protected-live rows (real ``/api/executions`` journeys against enrolled
OAuth state) are explicitly out of scope here: rows carry
``liveEvidence: "pending"`` until the protected provider-verification runner
qualifies the exact digest. Nothing in this module reads or prints credential
contents; isolation assertions inspect only path/env-key *names* and
secret-free metadata digests.

Source: MoonLadderStudios/MoonMind#3832.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

SHARED_HOST_CONFORMANCE_CATALOG_VERSION = "moonmind.omnigent-shared-host-rows.v1"
SHARED_HOST_INVENTORY_SCHEMA_VERSION = "moonmind.omnigent-shared-host-inventory.v1"
SHARED_HOST_CONFORMANCE_EVIDENCE_VERSION = (
    "moonmind.omnigent-shared-host-conformance.v1"
)

GENERIC_REALIZER_REF = "generic-omnigent-host@1"
AGENT_PROFILE_VERSION = "moonmind.omnigent-agent-profile.v2"

_SAFE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*@\d+$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")

OwnershipClass = Literal["run_owned", "profile_owned", "host_owned", "none"]

# Secret-safe observed credential state: callers pass *names only* (mounted
# paths, present env-key names, metadata digests). Values are never accepted.
CODEX_HOME = "/home/app/.codex"
CLAUDE_HOME = "/home/app/.claude"
OPENCODE_AUTH_FILE = "/home/app/.local/share/opencode/auth.json"
OPENCODE_STAGING_DIR = "/home/app/.local/share/opencode"

# Competing ambient selectors per harness family. Mirrors the pack
# ``forbiddenAmbientEnvKeys`` descriptors without duplicating secret values.
_CODEX_DENIED_ENV = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
    }
)
_CLAUDE_DENIED_ENV = frozenset(
    {
        "OPENAI_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
    }
)
_OPENCODE_DENIED_ENV = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
    }
)

_CODEX_DENIED_PATHS = frozenset(
    {CLAUDE_HOME, OPENCODE_AUTH_FILE, OPENCODE_STAGING_DIR + "/auth.json.staging"}
)
_CLAUDE_DENIED_PATHS = frozenset(
    {CODEX_HOME, OPENCODE_AUTH_FILE, OPENCODE_STAGING_DIR + "/auth.json.staging"}
)
_OPENCODE_DENIED_PATHS = frozenset({CODEX_HOME, CLAUDE_HOME})


class RequiredRow(BaseModel):
    """One supported combination MoonMind claims on the shared image."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rowId: str = Field(alias="rowId")
    harnessId: str = Field(alias="harnessId")
    materializerRef: str = Field(alias="materializerRef")
    ownershipClass: OwnershipClass = Field(alias="ownershipClass")
    executionRealizerRef: str = Field(alias="executionRealizerRef")
    hostClassRef: str = Field(alias="hostClassRef")
    runtimePackRef: str = Field(alias="runtimePackRef")
    providerCompatibilityClass: str = Field(alias="providerCompatibilityClass")
    agentProfileVersion: str = Field(alias="agentProfileVersion")
    hostMode: str = Field(alias="hostMode")
    launchPolicyRef: str = Field(alias="launchPolicyRef")
    architectures: tuple[str, ...] = ()
    liveEvidence: Literal["pending", "qualified"] = "pending"

    @model_validator(mode="after")
    def validate_top(self) -> RequiredRow:
        if not self.rowId.strip():
            raise ValueError("rowId required")
        for ref in (self.materializerRef, self.hostClassRef, self.runtimePackRef):
            if not _SAFE_REF_RE.fullmatch(ref):
                raise ValueError(f"invalid versioned ref {ref!r}")
        if self.executionRealizerRef != GENERIC_REALIZER_REF:
            raise ValueError(
                f"required rows must use {GENERIC_REALIZER_REF} "
                f"(got {self.executionRealizerRef!r}); legacy/direct rows are "
                "comparison-only and never qualify generic rows"
            )
        if not self.architectures:
            raise ValueError("architectures required")
        return self


REQUIRED_ROWS: tuple[RequiredRow, ...] = (
    RequiredRow.model_validate(
        {
            "rowId": "opencode-shared-generic-v1",
            "harnessId": "opencode-native",
            "materializerRef": "opencode-auth-json@1",
            "ownershipClass": "run_owned",
            "executionRealizerRef": GENERIC_REALIZER_REF,
            "hostClassRef": "omnigent-opencode@1",
            "runtimePackRef": "opencode-native-pack@1",
            "providerCompatibilityClass": "omnigent-provider-binding-set@1",
            "agentProfileVersion": AGENT_PROFILE_VERSION,
            "hostMode": "on-demand",
            "launchPolicyRef": "opencode-on-demand@2",
            "architectures": ["linux/amd64", "linux/arm64"],
        }
    ),
    RequiredRow.model_validate(
        {
            "rowId": "codex-shared-generic-v1",
            "harnessId": "codex-native",
            "materializerRef": "codex-oauth-home@1",
            "ownershipClass": "profile_owned",
            "executionRealizerRef": GENERIC_REALIZER_REF,
            "hostClassRef": "omnigent-codex@1",
            "runtimePackRef": "codex-native-pack@1",
            "providerCompatibilityClass": "omnigent-provider-binding-set@1",
            "agentProfileVersion": AGENT_PROFILE_VERSION,
            "hostMode": "on-demand",
            "launchPolicyRef": "codex-on-demand@1",
            "architectures": ["linux/amd64", "linux/arm64"],
        }
    ),
    RequiredRow.model_validate(
        {
            "rowId": "claude-shared-generic-v1",
            "harnessId": "claude-native",
            "materializerRef": "claude-oauth-home@1",
            "ownershipClass": "profile_owned",
            "executionRealizerRef": GENERIC_REALIZER_REF,
            "hostClassRef": "omnigent-claude@1",
            "runtimePackRef": "claude-native-pack@1",
            "providerCompatibilityClass": "omnigent-provider-binding-set@1",
            "agentProfileVersion": AGENT_PROFILE_VERSION,
            "hostMode": "on-demand",
            "launchPolicyRef": "claude-on-demand@1",
            "architectures": ["linux/amd64", "linux/arm64"],
        }
    ),
)


def get_required_row(row_id: str) -> RequiredRow:
    for row in REQUIRED_ROWS:
        if row.rowId == row_id:
            return row
    raise HarnessPlatformError(
        f"shared-host required row {row_id} unknown",
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
    )


def required_row_for_harness(harness_id: str) -> RequiredRow:
    matches = [row for row in REQUIRED_ROWS if row.harnessId == harness_id]
    if not matches:
        raise HarnessPlatformError(
            f"no shared-host required row for harness {harness_id}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )
    if len(matches) > 1:
        raise HarnessPlatformError(
            f"shared-host required rows ambiguous for {harness_id}",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    return matches[0]


class SharedHostImageInventory(BaseModel):
    """Secret-free inventory of the exact shared image (contents only)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schemaVersion: str = Field(SHARED_HOST_INVENTORY_SCHEMA_VERSION)
    imageRef: str = Field(alias="imageRef")
    architecture: str
    runtimeBinaries: tuple[dict[str, Any], ...] = Field(alias="runtimeBinaries")
    harnessImplementations: tuple[str, ...] = Field(alias="harnessImplementations")
    uid: int = 1000
    gid: int = 1000
    home: str = "/home/app"
    entrypoint: str = "/home/app"
    omnigentBuildDigest: str = Field(alias="omnigentBuildDigest")
    sbomRef: str = Field(alias="sbomRef")
    provenanceRef: str = Field(alias="provenanceRef")

    @model_validator(mode="after")
    def validate_top(self) -> SharedHostImageInventory:
        if not _IMAGE_RE.fullmatch(self.imageRef):
            raise ValueError("imageRef must be digest-pinned")
        if not _DIGEST_RE.fullmatch(self.omnigentBuildDigest):
            raise ValueError("omnigentBuildDigest must be sha256")
        if not self.runtimeBinaries:
            raise ValueError("runtimeBinaries required")
        names = sorted(str(item.get("name") or "") for item in self.runtimeBinaries)
        if names != ["claude", "codex", "opencode"]:
            raise ValueError(
                f"shared image must carry exactly codex/claude/opencode "
                f"(got {names})"
            )
        for item in self.runtimeBinaries:
            if not str(item.get("version") or "").strip():
                raise ValueError("runtime binary version required")
        if not self.harnessImplementations:
            raise ValueError("harnessImplementations required")
        if not self.sbomRef.strip() or not self.provenanceRef.strip():
            raise ValueError("sbomRef and provenanceRef required")
        return self


def build_shared_host_image_inventory(
    *,
    image_ref: str,
    architecture: str,
    omnigent_build_digest: str,
    sbom_ref: str,
    provenance_ref: str,
) -> SharedHostImageInventory:
    """Build the contents-only inventory from trusted pack pins.

    Vendor versions come from the registered runtime-pack descriptors so the
    inventory cannot drift from pack authority; callers supply only the exact
    image identity and SBOM/provenance refs. Proves contents only, never
    launch or credential readiness.
    """

    from moonmind.omnigent.harness_platform.runtime_packs import get_runtime_pack

    binaries: list[dict[str, Any]] = []
    for pack_ref in (
        "codex-native-pack@1",
        "claude-native-pack@1",
        "opencode-native-pack@1",
    ):
        pack = get_runtime_pack(pack_ref)
        binaries.append(
            {
                "name": pack.vendorRuntime.name,
                "version": pack.vendorRuntime.pinnedVersion,
                "supportedRange": pack.vendorRuntime.supportedRange,
            }
        )
    binaries.sort(key=lambda item: str(item["name"]))
    return SharedHostImageInventory.model_validate(
        {
            "schemaVersion": SHARED_HOST_INVENTORY_SCHEMA_VERSION,
            "imageRef": image_ref,
            "architecture": architecture,
            "runtimeBinaries": binaries,
            "harnessImplementations": [
                "codex-native",
                "claude-native",
                "opencode-native",
            ],
            "uid": 1000,
            "gid": 1000,
            "home": "/home/app",
            "entrypoint": "/home/app",
            "omnigentBuildDigest": omnigent_build_digest,
            "sbomRef": sbom_ref,
            "provenanceRef": provenance_ref,
        }
    )


def assert_one_harness_admission(
    *,
    row: RequiredRow,
    plan_harness_id: str,
    plan_realizer_ref: str,
    host_class_ref: str,
    declared_harness_ids: tuple[str, ...],
    runtime_pack_ref: str,
    materializer_refs: tuple[str, ...],
    support_combination_key: str,
    expected_support_combination_key: str,
) -> None:
    """Prove one-harness admission for an exact host launch.

    Fails before provider work when another installed harness could be
    substituted through request, catalog, host, or runtime metadata.
    """

    if plan_harness_id != row.harnessId:
        raise HarnessPlatformError(
            f"plan harness {plan_harness_id} does not select required row "
            f"{row.rowId} ({row.harnessId})",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    if plan_realizer_ref != row.executionRealizerRef:
        raise HarnessPlatformError(
            f"plan realizer {plan_realizer_ref} is not the row realizer "
            f"{row.executionRealizerRef}; legacy/direct realizers never "
            "qualify generic rows",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_REALIZER_UNAVAILABLE,
        )
    if host_class_ref != row.hostClassRef:
        raise HarnessPlatformError(
            f"host class {host_class_ref} does not match row {row.hostClassRef}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    if tuple(declared_harness_ids) != (row.harnessId,):
        raise HarnessPlatformError(
            f"host class {host_class_ref} must declare exactly one harness "
            f"({row.harnessId}); got {list(declared_harness_ids)}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    if runtime_pack_ref != row.runtimePackRef:
        raise HarnessPlatformError(
            f"runtime pack {runtime_pack_ref} does not match row "
            f"{row.runtimePackRef}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    if row.materializerRef not in materializer_refs:
        raise HarnessPlatformError(
            f"row materializer {row.materializerRef} not selected",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    # Cross-harness substitution fails here, before provider work: the Host
    # Class allowlist is exact, so any foreign materializer is a substitution
    # attempt even when the plan harness itself is correct.
    from moonmind.omnigent.harness_platform.host_classes import (
        DEFAULT_HOST_CLASS_TEMPLATES,
    )

    allowed: set[str] = set()
    for template in DEFAULT_HOST_CLASS_TEMPLATES:
        if template.ref == row.hostClassRef:
            allowed = set(template.materializer_refs)
    foreign = sorted(set(materializer_refs) - allowed - {row.materializerRef})
    if foreign:
        raise HarnessPlatformError(
            f"harness substitution via materializers {foreign} rejected for "
            f"row {row.rowId}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    if support_combination_key != expected_support_combination_key:
        raise HarnessPlatformError(
            f"support key mismatch for row {row.rowId}: evidence for one row "
            "cannot qualify another",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_DIGEST_MISMATCH,
        )


def assert_credential_isolation(
    *,
    row: RequiredRow,
    present_paths: tuple[str, ...],
    present_env_keys: tuple[str, ...],
) -> None:
    """Prove per-row credential isolation from names only (never values).

    ``present_paths`` are mounted credential paths; ``present_env_keys`` are
    env-key *names* only. Any competing harness state or ambient selector
    fails closed.
    """

    paths = set(present_paths)
    env_keys = set(present_env_keys)
    if row.harnessId == "codex-native":
        if CODEX_HOME not in paths:
            raise HarnessPlatformError(
                "codex row must attach exactly the selected Codex OAuth home",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
            )
        unexpected = paths & _CODEX_DENIED_PATHS
        if unexpected:
            raise HarnessPlatformError(
                f"codex row must not attach competing state {sorted(unexpected)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
        denied = env_keys & (_CODEX_DENIED_ENV | {"OPENAI_API_KEY"})
        # OPENAI_API_KEY is denied unless the selected contract explicitly
        # requires it; no shared-image row does.
        if denied:
            raise HarnessPlatformError(
                f"codex row must not carry competing selectors {sorted(denied)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    elif row.harnessId == "claude-native":
        if CLAUDE_HOME not in paths:
            raise HarnessPlatformError(
                "claude row must attach exactly the selected Claude bundle",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
            )
        unexpected = paths & _CLAUDE_DENIED_PATHS
        if unexpected:
            raise HarnessPlatformError(
                f"claude row must not attach competing state {sorted(unexpected)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
        denied = env_keys & (_CLAUDE_DENIED_ENV | {"ANTHROPIC_API_KEY"})
        if denied:
            raise HarnessPlatformError(
                f"claude row must not carry competing selectors {sorted(denied)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    elif row.harnessId == "opencode-native":
        if OPENCODE_AUTH_FILE not in paths:
            raise HarnessPlatformError(
                "opencode row must attach exactly the run-owned auth.json",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_SLOT_UNBOUND,
            )
        unexpected = paths & _OPENCODE_DENIED_PATHS
        if unexpected:
            raise HarnessPlatformError(
                f"opencode row must not attach competing state {sorted(unexpected)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
        denied = env_keys & _OPENCODE_DENIED_ENV
        if denied:
            raise HarnessPlatformError(
                f"opencode row must not carry competing selectors {sorted(denied)}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    else:  # pragma: no cover - catalog is closed
        raise HarnessPlatformError(
            f"unknown harness {row.harnessId}",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
        )


def assert_ownership_cleanup(
    *,
    ownership: OwnershipClass,
    run_state_present_after_cleanup: bool,
    enrollment_state_present_after_cleanup: bool,
    host_state_copied_or_deleted: bool,
    stale_cleanup_affected_replacement: bool,
    provider_profile_released_last: bool,
    image_layers_present_after_cleanup: bool,
) -> None:
    """Prove ownership-aware cleanup from safe metadata (no bodies)."""

    if ownership == "run_owned" and run_state_present_after_cleanup:
        raise HarnessPlatformError(
            "run_owned state must be removed after terminal cleanup",
            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
    if ownership == "profile_owned" and not enrollment_state_present_after_cleanup:
        raise HarnessPlatformError(
            "profile_owned state must be detached but preserved",
            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
    if ownership == "host_owned" and host_state_copied_or_deleted:
        raise HarnessPlatformError(
            "host_owned state must be neither copied nor deleted",
            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
    if stale_cleanup_affected_replacement:
        raise HarnessPlatformError(
            "stale cleanup must not affect a replacement generation",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    if not provider_profile_released_last:
        raise HarnessPlatformError(
            "Provider Profile release must be last",
            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )
    if not image_layers_present_after_cleanup:
        raise HarnessPlatformError(
            "shared image layers must remain present after run cleanup",
            code=HarnessPlatformFailure.OMNIGENT_CLEANUP_DEFERRED,
        )


def assert_runtime_readiness(
    *,
    row: RequiredRow,
    observed_vendor_version: str,
    probe_kinds_passed: tuple[str, ...],
    required_env_present: tuple[str, ...],
    required_env_expected: tuple[str, ...],
    unselected_credential_state_absent: bool,
    credential_path_mode_ok: bool,
) -> None:
    """Prove exact runtime/auth readiness via pack-selected probes."""

    from moonmind.omnigent.harness_platform.runtime_packs import (
        get_runtime_pack,
        is_vendor_version_supported,
    )

    pack = get_runtime_pack(row.runtimePackRef)
    if not is_vendor_version_supported(pack, observed_vendor_version):
        raise HarnessPlatformError(
            f"vendor version {observed_vendor_version} outside pack range "
            f"{pack.vendorRuntime.supportedRange}",
            code=HarnessPlatformFailure.OMNIGENT_VENDOR_RUNTIME_MISMATCH,
        )
    if pack.readiness.kind != "none" and pack.readiness.kind not in probe_kinds_passed:
        raise HarnessPlatformError(
            f"pack-selected probe {pack.readiness.kind} did not pass",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
    if tuple(sorted(required_env_present)) != tuple(sorted(required_env_expected)):
        raise HarnessPlatformError(
            "required environment passthrough mismatch",
            code=HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY,
        )
    if not unselected_credential_state_absent:
        raise HarnessPlatformError(
            "unselected runtime credential state must be absent",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
        )
    if not credential_path_mode_ok:
        raise HarnessPlatformError(
            "credential file/mount permissions mismatch",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )


# Common lifecycle ordering for exact-artifact evidence. Protected-live
# journeys prove the same order against real /api/executions evidence; this
# hermetic check proves candidate event streams preserve the authority order
# (plan before side effects, lease before realization, release last).
LIFECYCLE_ORDER: tuple[str, ...] = (
    "plan_persisted",
    "provider_lease_acquired",
    "host_realized",
    "session_started",
    "first_turn",
    "terminal_harvest",
    "cleanup",
    "provider_released",
)


def assert_lifecycle_order(events: tuple[str, ...]) -> None:
    positions = {name: index for index, name in enumerate(events)}
    previous = -1
    for required in LIFECYCLE_ORDER:
        if required not in positions:
            raise HarnessPlatformError(
                f"lifecycle evidence missing {required}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        if positions[required] <= previous:
            raise HarnessPlatformError(
                f"lifecycle order violated at {required}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        previous = positions[required]


def assert_failure_isolation(
    *,
    failed_harness_id: str,
    other_harness_id: str,
    other_state_before: str,
    other_state_after: str,
) -> None:
    """Prove a failure in one harness cannot alter another harness."""

    if failed_harness_id == other_harness_id:
        raise HarnessPlatformError(
            "failure isolation requires two distinct harnesses",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    if other_state_before != other_state_after:
        raise HarnessPlatformError(
            f"failure in {failed_harness_id} altered {other_harness_id}",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )


def build_conformance_evidence(
    *,
    moonmind_commit: str,
    server_image_ref: str,
    shared_host_image_ref: str,
    architecture: str,
    row_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the secret-free exact-artifact evidence document (#3832)."""

    if not _IMAGE_RE.fullmatch(shared_host_image_ref):
        raise HarnessPlatformError(
            "shared host image ref must be digest-pinned",
            code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
        )
    row_ids = sorted(item.get("rowId", "") for item in row_results)
    expected = sorted(row.rowId for row in REQUIRED_ROWS)
    if row_ids != expected:
        raise HarnessPlatformError(
            f"evidence must cover exactly the required rows {expected} "
            f"(got {row_ids})",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    canonical = json.dumps(
        {
            "catalogVersion": SHARED_HOST_CONFORMANCE_CATALOG_VERSION,
            "commit": moonmind_commit,
            "serverImage": server_image_ref,
            "hostImage": shared_host_image_ref,
            "rows": row_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schemaVersion": SHARED_HOST_CONFORMANCE_EVIDENCE_VERSION,
        "catalogVersion": SHARED_HOST_CONFORMANCE_CATALOG_VERSION,
        "moonmindCommit": moonmind_commit,
        "serverImageRef": server_image_ref,
        "sharedHostImageRef": shared_host_image_ref,
        "architecture": architecture,
        "rowResults": row_results,
        "evidenceDigest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
    }


__all__ = [
    "AGENT_PROFILE_VERSION",
    "GENERIC_REALIZER_REF",
    "LIFECYCLE_ORDER",
    "REQUIRED_ROWS",
    "SHARED_HOST_CONFORMANCE_CATALOG_VERSION",
    "SHARED_HOST_CONFORMANCE_EVIDENCE_VERSION",
    "SHARED_HOST_INVENTORY_SCHEMA_VERSION",
    "RequiredRow",
    "SharedHostImageInventory",
    "assert_credential_isolation",
    "assert_failure_isolation",
    "assert_lifecycle_order",
    "assert_one_harness_admission",
    "assert_ownership_cleanup",
    "assert_runtime_readiness",
    "build_conformance_evidence",
    "build_shared_host_image_inventory",
    "get_required_row",
    "required_row_for_harness",
]
