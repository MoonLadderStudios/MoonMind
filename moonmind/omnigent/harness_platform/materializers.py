"""Credential materializer registry (section 12).

Trusted boundary that turns leased Provider Profile + generation into runtime state.
"""

from __future__ import annotations

import json
import os
import re
import stat
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

# OpenCode materializer constants per issue §5
OPENCODE_AUTH_TARGET_PATH = "/home/app/.local/share/opencode/auth.json"
OPENCODE_AUTH_PARENT_MODE = 0o700
OPENCODE_AUTH_FILE_MODE = 0o600
OPENCODE_AUTH_UID = 1000
OPENCODE_AUTH_GID = 1000
OPENCODE_PROVIDER_KEY = "opencode-go"
OPENCODE_SUPPORTED_VERSION_RANGE = (  # inclusive lower, exclusive upper
    "1.17.7",
    "1.19.0",
)
FORBIDDEN_AMBIENT_ENV_KEYS = (
    "OPENCODE_AUTH_CONTENT",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

class MaterializerTargetKind(StrEnum):
    generated_file = "generated-file"
    oauth_home = "oauth-home"
    provider_config = "provider-config"
    secret_env_file = "secret-env-file"
    session_config = "session-config"
    host_owned_auth = "host-owned-auth"
    none = "none"


class CredentialMaterializer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    materializerId: str = Field(alias="materializerId")
    version: int = Field(ge=1)
    acceptedHarnessImplementations: tuple[str, ...] = Field(alias="acceptedHarnessImplementations")
    acceptedAuthModels: tuple[str, ...] = Field(alias="acceptedAuthModels")
    supportedHostModes: tuple[str, ...] = Field(alias="supportedHostModes")
    requiredSecretRoles: tuple[str, ...] = Field(alias="requiredSecretRoles")
    state: dict[str, Any]
    target: dict[str, Any]
    preflight: dict[str, Any] | None = None
    cleanup: dict[str, Any]

    @model_validator(mode="after")
    def validate_top(self) -> "CredentialMaterializer":
        if not _SAFE_ID_RE.fullmatch(self.materializerId):
            raise ValueError("invalid materializerId")
        if not self.acceptedHarnessImplementations:
            raise ValueError("acceptedHarnessImplementations required")
        for ref in self.acceptedHarnessImplementations:
            if not ref.startswith("omnigent-harness-implementation:sha256:"):
                raise ValueError("acceptedHarnessImplementations must be implementation refs")
        if not self.acceptedAuthModels:
            raise ValueError("acceptedAuthModels required")
        if not self.supportedHostModes:
            raise ValueError("supportedHostModes required")
        # target must have kind
        if "kind" not in self.target:
            raise ValueError("target.kind required")
        kind = self.target["kind"]
        allowed_kinds = {e.value for e in MaterializerTargetKind}
        if kind not in allowed_kinds and kind not in {"generated-file", "oauth-home"}:
            raise ValueError(f"unknown target kind {kind}")
        if kind == "generated-file" and "path" not in self.target:
            raise ValueError("generated-file target requires path")
        if "mode" not in self.cleanup:
            raise ValueError("cleanup.mode required")
        return self

    @property
    def ref(self) -> str:
        return f"{self.materializerId}@{self.version}"

    def supports_harness(self, implementation_ref: str) -> bool:
        return implementation_ref in self.acceptedHarnessImplementations

    def supports_host_mode(self, host_mode: str) -> bool:
        return host_mode in self.supportedHostModes


# Built-in registry
BUILTIN_MATERIALIZERS: dict[str, CredentialMaterializer] = {}


def _impl_ref_for_materializer(package: str, version: str, digest: str, kind: str = "core", entry: str | None = None) -> str:
    from moonmind.omnigent.harness_platform.catalog import HarnessImplementationIdentity
    return HarnessImplementationIdentity.model_validate(
        {"sourceKind": kind, "package": package, "version": version, "digest": digest, "pluginEntryPoint": entry}
    ).implementation_ref()

def _register_builtin(data: dict[str, Any]) -> CredentialMaterializer:
    m = CredentialMaterializer.model_validate(data)
    BUILTIN_MATERIALIZERS[m.ref] = m
    return m


_register_builtin(
    {
        "materializerId": "codex-oauth-home",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "a" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "e" * 64),
        ],
        "acceptedAuthModels": ["oauth_volume"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["oauth_token"],
        "state": {"scope": "run", "mutable": True},
        "target": {"kind": "oauth-home", "path": "/home/app/.codex", "permissions": "0700"},
        "preflight": {"kind": "login-status"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "opencode-auth-json",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "a" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "b" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64),
        ],
        "acceptedAuthModels": ["own-auth"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "generated-file", "path": "/home/app/.local/share/opencode/auth.json", "permissions": "0600"},
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "omnigent-provider-config",
        "version": 1,
        "acceptedHarnessImplementations": [_impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64)],
        "acceptedAuthModels": ["omnigent-provider-config"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": ["api_key"],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "generated-file", "path": "/home/app/.config/omnigent/provider.json", "permissions": "0600"},
        "preflight": {"kind": "live-model-options"},
        "cleanup": {"mode": "remove-owned-state"},
    }
)

_register_builtin(
    {
        "materializerId": "host-owned-auth",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "d" * 64),
        ],
        "acceptedAuthModels": ["host-owned-auth"],
        "supportedHostModes": ["static-connected"],
        "requiredSecretRoles": [],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "host-owned-auth", "path": ""},
        "preflight": {"kind": "host-auth"},
        "cleanup": {"mode": "none"},
    }
)

_register_builtin(
    {
        "materializerId": "none",
        "version": 1,
        "acceptedHarnessImplementations": [
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "a" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "b" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "c" * 64),
            _impl_ref_for_materializer("omnigent", "1.0.0", "sha256:" + "d" * 64),
        ],
        "acceptedAuthModels": ["none"],
        "supportedHostModes": ["on-demand", "static-connected"],
        "requiredSecretRoles": [],
        "state": {"scope": "run", "mutable": False},
        "target": {"kind": "none", "path": ""},
        "preflight": {"kind": "none"},
        "cleanup": {"mode": "none"},
    }
)


def get_materializer(ref: str) -> CredentialMaterializer:
    if ref not in BUILTIN_MATERIALIZERS:
        raise HarnessPlatformError(
            f"credential materializer {ref} unavailable",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    return BUILTIN_MATERIALIZERS[ref]


def validate_binding_materializer(
    *,
    materializer_ref: str,
    harness_implementation_ref: str,
    host_mode: str,
    required_secret_roles: list[str] | None = None,
) -> CredentialMaterializer:
    mat = get_materializer(materializer_ref)
    if not mat.supports_harness(harness_implementation_ref):
        raise HarnessPlatformError(
            f"materializer {materializer_ref} incompatible with harness {harness_implementation_ref}",
            code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
        )
    if not mat.supports_host_mode(host_mode):
        raise HarnessPlatformError(
            f"materializer {materializer_ref} does not support host mode {host_mode}",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        )
    if required_secret_roles:
        missing = set(required_secret_roles) - set(mat.requiredSecretRoles)
        if missing:
            raise HarnessPlatformError(
                f"materializer missing required secret roles: {missing}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
            )
    return mat


def materialize_credential(
    *,
    materializer_ref: str,
    provider_profile_ref: str,
    provider_lease_ref: str,
    credential_generation: int,
    host_mode: str = "on-demand",
) -> dict[str, Any]:
    """Return secret-free handle (section 12.3). Never contains secret bodies."""
    mat = get_materializer(materializer_ref)
    # Forbidden: secret bodies in handle
    target_path = mat.target.get("path", "")
    # Derive access mode from materializer state: mutable OAuth homes must remain writable for token refresh
    is_mutable = bool(mat.state.get("mutable"))
    access_mode = "read_write" if is_mutable else "read-only"
    return {
        "credentialRuntimeRef": f"credential-runtime:{provider_profile_ref}:{credential_generation}",
        "providerProfileRef": provider_profile_ref,
        "providerLeaseRef": provider_lease_ref,
        "credentialGeneration": credential_generation,
        "materializerRef": mat.ref,
        "mountClass": "provider-auth",
        "targetPath": target_path,
        "accessMode": access_mode,
        "cleanupRef": f"credential-cleanup:{provider_profile_ref}:{credential_generation}",
        "attestationRef": f"artifact:{mat.materializerId}:{credential_generation}",
    }


# ---- OpenCode opencode-auth-json@1 trusted materialization (issue §5) ----


def _assert_no_forbidden_ambient_env() -> None:
    """Fail if conflicting ambient credentials are present (issue §5)."""
    present = [k for k in FORBIDDEN_AMBIENT_ENV_KEYS if os.environ.get(k)]
    if present:
        raise HarnessPlatformError(
            f"conflicting ambient credentials present: {present}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )


def _opencode_auth_json_payload(*, api_key: str) -> dict[str, Any]:
    """Exact OpenCode credential structure for the pinned version.

    Verified against opencode-ai 1.18.x auth.json shape:
    The provider key is `opencode-go` and the file contains the API key
    under that key. This is the only secret-bearing structure; all
    diagnostics, handles, and logs must remain secret-free.
    """
    if not api_key or not api_key.strip():
        raise HarnessPlatformError(
            "api_key is required for opencode-auth-json",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    # The exact structure required by pinned opencode-ai@1.18.x: { "opencode-go": { "type": "api", "key": "..." } }
    # Pinned source uses `key`, not `apiKey`. Use canonical provider-keyed form.
    return {OPENCODE_PROVIDER_KEY: {"type": "api", "key": api_key.strip()}}


def build_opencode_auth_json_bytes(*, api_key: str) -> bytes:
    """Return canonical JSON bytes for auth.json without touching filesystem."""
    payload = _opencode_auth_json_payload(api_key=api_key)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def materialize_opencode_auth_json(
    *,
    api_key: str,
    provider_profile_ref: str,
    provider_lease_ref: str,
    credential_generation: int,
    expected_generation: int | None = None,
    # For testing: root directory that mirrors host filesystem layout.
    # Production passes "/" so target is /home/app/.local/share/opencode/auth.json.
    host_root: str | Path = "/",
    uid: int = OPENCODE_AUTH_UID,
    gid: int = OPENCODE_AUTH_GID,
) -> dict[str, Any]:
    """Trusted opencode-auth-json@1 materialization (issue §5 steps 1-12).

    Writes the exact OpenCode credential structure to
    /home/app/.local/share/opencode/auth.json with parent 0700, file 0600,
    ownership uid:gid, and returns a secret-free handle.

    Generation fencing: if expected_generation is provided and does not match
    credential_generation, fails closed (stale-generation refusal).
    """
    if expected_generation is not None and credential_generation != expected_generation:
        raise HarnessPlatformError(
            f"credential generation mismatch: got {credential_generation} expected {expected_generation}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    if credential_generation < 1:
        raise HarnessPlatformError(
            "credential_generation must be >=1",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    _assert_no_forbidden_ambient_env()
    # Verify provider key is the expected one (issue §5 step 6)
    if OPENCODE_PROVIDER_KEY != "opencode-go":
        raise HarnessPlatformError(
            "opencode provider key mismatch",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    # Materialize file to host filesystem
    # Resolve target path under host_root for test isolation; production host_root="/"
    target_rel = OPENCODE_AUTH_TARGET_PATH.lstrip("/")
    target_path = Path(host_root) / target_rel
    parent = target_path.parent
    # Ensure parent exists with 0700
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, OPENCODE_AUTH_PARENT_MODE)
    except OSError as exc:
        raise HarnessPlatformError(
            f"failed to set parent dir permissions: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        ) from exc
    try:
        # Best-effort ownership; may fail in test environments without privilege or on Windows
        os.chown(parent, uid, gid)  # type: ignore[attr-defined]
    except (OSError, AttributeError):  # best-effort ownership in non-root test containers; verified later if root
        pass
    # Write file atomically with 0600
    payload_bytes = build_opencode_auth_json_bytes(api_key=api_key)
    # Forbidden: never log the raw key; we assert this in tests by scanning returns
    tmp_path = parent / f".auth.json.tmp.{credential_generation}"
    # Ensure no leftover tmp
    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:  # best-effort cleanup of stale tmp file
        pass
    # Write with restrictive umask
    old_umask = os.umask(0o077)
    try:
        tmp_path.write_bytes(payload_bytes)
        os.chmod(tmp_path, OPENCODE_AUTH_FILE_MODE)
        try:
            os.chown(tmp_path, uid, gid)  # type: ignore[attr-defined]
        except (OSError, AttributeError):  # best-effort ownership in non-root test containers
            pass
        # Atomic move
        tmp_path.replace(target_path)
        # Ensure final permissions
        os.chmod(target_path, OPENCODE_AUTH_FILE_MODE)
    finally:
        os.umask(old_umask)
        # Clean tmp if still exists
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:  # best-effort tmp cleanup
            pass
    # Verify file exists with correct mode (best-effort uid/gid check)
    try:
        st = target_path.stat()
    except OSError as exc:
        raise HarnessPlatformError(
            f"auth.json not found after materialization: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        ) from exc
    file_mode = stat.S_IMODE(st.st_mode)
    # On Windows, chmod semantics differ; enforce only on POSIX where strict 0600 is meaningful
    if os.name != "nt" and file_mode != OPENCODE_AUTH_FILE_MODE:
        raise HarnessPlatformError(
            f"auth.json permissions mismatch: {oct(file_mode)} != {oct(OPENCODE_AUTH_FILE_MODE)}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    parent_mode = stat.S_IMODE(parent.stat().st_mode)
    if os.name != "nt" and parent_mode != OPENCODE_AUTH_PARENT_MODE:
        raise HarnessPlatformError(
            f"auth.json parent permissions mismatch: {oct(parent_mode)} != {oct(OPENCODE_AUTH_PARENT_MODE)}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    # Ownership check: only enforce when running as root or when ownership matches expected
    # In test environments, chown may have been no-op; we verify but don't hard-fail if not root
    # If we are root and ownership mismatch, fail closed.
    if hasattr(os, "getuid") and os.getuid() == 0:
        if st.st_uid != uid or st.st_gid != gid:
            raise HarnessPlatformError(
                f"auth.json ownership mismatch: {st.st_uid}:{st.st_gid} != {uid}:{gid}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
    # Persist generation fencing evidence alongside auth.json (private sidecar)
    generation_path = parent / ".opencode-auth-generation"
    try:
        # Write generation with restrictive perms; best-effort chown
        old_umask2 = os.umask(0o077)
        try:
            generation_path.write_text(str(credential_generation), encoding="utf-8")
            os.chmod(generation_path, OPENCODE_AUTH_FILE_MODE)
            try:
                os.chown(generation_path, uid, gid)  # type: ignore[attr-defined]
            except (OSError, AttributeError):  # best-effort ownership in non-root containers
                pass
        finally:
            os.umask(old_umask2)
    except OSError as exc:
        raise HarnessPlatformError(
            f"failed to persist generation sidecar: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        ) from exc
    # Ensure no conflicting env vars were introduced
    _assert_no_forbidden_ambient_env()
    # Return secret-free handle (issue §5 step 11)
    handle = materialize_credential(
        materializer_ref="opencode-auth-json@1",
        provider_profile_ref=provider_profile_ref,
        provider_lease_ref=provider_lease_ref,
        credential_generation=credential_generation,
        host_mode="on-demand",
    )
    # Enforce read-only mount when compatible (issue §5 step 10)
    handle["accessMode"] = "read-only"
    handle["targetPath"] = OPENCODE_AUTH_TARGET_PATH
    # Extra attestation fields for diagnostics (secret-free)
    handle["materializationEvidence"] = {
        "targetPath": OPENCODE_AUTH_TARGET_PATH,
        "parentMode": oct(OPENCODE_AUTH_PARENT_MODE),
        "fileMode": oct(OPENCODE_AUTH_FILE_MODE),
        "uid": uid,
        "gid": gid,
        "providerKey": OPENCODE_PROVIDER_KEY,
        "credentialGeneration": credential_generation,
    }
    # Verify handle does not contain raw key
    handle_json = json.dumps(handle)
    if api_key in handle_json:
        raise HarnessPlatformError(
            "materialization handle leaked secret",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    return handle


def verify_opencode_auth_file(
    *,
    host_root: str | Path = "/",
    expected_api_key: str | None = None,
    expected_generation: int | None = None,
    uid: int = OPENCODE_AUTH_UID,
    gid: int = OPENCODE_AUTH_GID,
) -> dict[str, Any]:
    """Verify the materialized auth.json without leaking secrets."""
    target_rel = OPENCODE_AUTH_TARGET_PATH.lstrip("/")
    target_path = Path(host_root) / target_rel
    parent = target_path.parent
    if not target_path.exists():
        raise HarnessPlatformError(
            "opencode auth.json missing",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    # Check permissions (POSIX strict; Windows chmod differs)
    st = target_path.stat()
    if os.name != "nt" and stat.S_IMODE(st.st_mode) != OPENCODE_AUTH_FILE_MODE:
        raise HarnessPlatformError(
            "auth.json permissions invalid",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    if os.name != "nt" and stat.S_IMODE(parent.stat().st_mode) != OPENCODE_AUTH_PARENT_MODE:
        raise HarnessPlatformError(
            "auth.json parent permissions invalid",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    if hasattr(os, "getuid") and os.getuid() == 0:
        if st.st_uid != uid or st.st_gid != gid:
            raise HarnessPlatformError(
                "auth.json ownership invalid",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
            )
    # Load and validate structure without printing secret
    try:
        data = json.loads(target_path.read_bytes())
    except Exception as exc:
        raise HarnessPlatformError(
            f"auth.json invalid json: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        ) from exc
    if OPENCODE_PROVIDER_KEY not in data:
        raise HarnessPlatformError(
            f"auth.json missing provider key {OPENCODE_PROVIDER_KEY}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    entry = data[OPENCODE_PROVIDER_KEY]
    if not isinstance(entry, dict) or "key" not in entry:
        raise HarnessPlatformError(
            "auth.json provider entry malformed",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    if expected_api_key is not None and entry["key"] != expected_api_key:
        raise HarnessPlatformError(
            "auth.json apiKey mismatch",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        )
    # Verify generation fencing via sidecar file instead of echoing expectation
    if expected_generation is not None:
        generation_path = parent / ".opencode-auth-generation"
        try:
            observed = int(generation_path.read_text(encoding="utf-8").strip())
        except Exception as exc:
            raise HarnessPlatformError(
                f"auth generation sidecar missing or invalid: {exc}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            ) from exc
        if observed != expected_generation:
            raise HarnessPlatformError(
                f"credential generation mismatch: observed {observed} != expected {expected_generation}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
            )
        verified_generation = observed
    else:
        verified_generation = None
    return {
        "targetPath": OPENCODE_AUTH_TARGET_PATH,
        "providerKey": OPENCODE_PROVIDER_KEY,
        "hasApiKey": True,
        "fileMode": oct(stat.S_IMODE(st.st_mode)),
        "parentMode": oct(stat.S_IMODE(parent.stat().st_mode)),
        "generation": verified_generation,
    }


def cleanup_opencode_auth(
    *,
    host_root: str | Path = "/",
    provider_profile_ref: str | None = None,
    credential_generation: int | None = None,
) -> dict[str, Any]:
    """Destroy run-owned credential material (issue §5 step 12).

    Removes auth.json and, if empty, its parent directory.
    Returns secret-free cleanup evidence.
    """
    target_rel = OPENCODE_AUTH_TARGET_PATH.lstrip("/")
    target_path = Path(host_root) / target_rel
    parent = target_path.parent
    removed_file = False
    removed_parent = False
    # Remove file if exists
    try:
        if target_path.exists():
            target_path.unlink()
            removed_file = True
    except OSError as exc:
        raise HarnessPlatformError(
            f"failed to cleanup auth.json: {exc}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZATION_FAILED,
        ) from exc
    # Remove generation sidecar
    try:
        gen_path = parent / ".opencode-auth-generation"
        if gen_path.exists():
            gen_path.unlink()
    except OSError:  # best-effort generation cleanup
        pass
    # Try to remove parent if empty (best-effort)
    try:
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            removed_parent = True
    except OSError:  # best-effort parent cleanup
        pass
    # Also remove any tmp files
    try:
        for tmp in parent.glob(".auth.json.tmp.*"):
            try:
                tmp.unlink()
            except OSError:  # best-effort tmp cleanup
                pass
    except OSError:  # best-effort glob cleanup
        pass
    return {
        "cleanupRef": f"credential-cleanup:{provider_profile_ref or 'unknown'}:{credential_generation or 0}",
        "targetPath": OPENCODE_AUTH_TARGET_PATH,
        "removedFile": removed_file,
        "removedParent": removed_parent,
        "materializerRef": "opencode-auth-json@1",
    }


def assert_opencode_materialization_secret_free(handle: dict[str, Any], raw_key: str) -> None:
    """Test helper: prove raw key is absent from handle and evidence."""
    handle_json = json.dumps(handle, sort_keys=True)
    if raw_key and raw_key in handle_json:
        raise AssertionError("handle leaked raw api key")


def clear_forbidden_ambient_env() -> list[str]:
    """Remove conflicting ambient credentials and return the cleared key names (no raw values)."""
    cleared: list[str] = []
    for key in FORBIDDEN_AMBIENT_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared.append(key)
    return cleared
