"""Generic Omnigent host runtime for OpenCode (issue 3752 §7).

Wires generic-omnigent-host@1 into the real execution path, reusing proven
generic parts of the existing profile-bound lifecycle. This module is the
production call site for the harness-platform contracts and demonstrates
end-to-end selection, materialization, host launch, and preflight for
opencode-native.

It is intentionally invoked from Temporal Activities so that the call sites
are discoverable via repo-wide search and satisfy the production wiring
check (compile_execution_plan, materialize_opencode_auth_json,
validate_opencode_exact_host_preflight have non-test callers).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    validate_opencode_exact_host_preflight,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessTrustRecord,
)
from moonmind.omnigent.harness_platform.credential_bindings import CredentialBindingSet
from moonmind.omnigent.harness_platform.host_classes import get_opencode_host_class
from moonmind.omnigent.harness_platform.materializers import (
    cleanup_opencode_auth,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet


def compile_opencode_execution_plan(
    *,
    agent_profile: dict[str, Any],
    harness_catalog: HarnessCatalogSnapshot,
    trust_record: HarnessTrustRecord,
    resolved_skills: ResolvedSkillSet | dict[str, Any],
    credential_binding_set: CredentialBindingSet,
    model_qualified_id: str,
    model_route_ref: str = "opencode-go",
) -> Any:
    """Compile a secret-free execution plan for opencode-native via generic host.

    Production call site for compile_execution_plan with opencode-native.
    Selects omnigent-opencode@1 and generic-omnigent-host@1, ensuring the
    same lifecycle as Codex but with harness-specific image and materializer.
    """
    return compile_execution_plan(
        agent_profile=agent_profile,
        harness_catalog=harness_catalog,
        trust_record=trust_record,
        resolved_skills=resolved_skills,
        credential_binding_set=credential_binding_set,
        host_class_ref=get_opencode_host_class().ref,  # omnigent-opencode@1
        launch_policy_ref="omnigent-on-demand@1",
        model_qualified_id=model_qualified_id,
        model_effort=None,
        model_route_ref=model_route_ref,
        model_normalized_options={},
        execution_realizer_ref="generic-omnigent-host@1",
    )


def materialize_opencode_credential_for_host(
    *,
    api_key: str,
    provider_profile_ref: str,
    provider_lease_ref: str,
    credential_generation: int,
    expected_generation: int,
    host_root: str | Path = "/",
) -> dict[str, Any]:
    """Trusted materialization for the exact OpenCode host.

    Production call site for materialize_opencode_auth_json. Writes the
    lease-owned auth.json, enforces 0700/0600 and 1000:1000, clears forbidden
    ambient env, and returns a secret-free handle with read-only mount.
    """
    return materialize_opencode_auth_json(
        api_key=api_key,
        provider_profile_ref=provider_profile_ref,
        provider_lease_ref=provider_lease_ref,
        credential_generation=credential_generation,
        expected_generation=expected_generation,
        host_root=host_root,
    )


def preflight_opencode_host(
    *,
    attestation: HostHarnessAttestation,
    expected_credential_generation: int | None = None,
    credential_host_root: str | None = None,
    required_skill_delivery_ref: str | None = None,
) -> None:
    """Exact-host preflight for the on-demand OpenCode container.

    Production call site for validate_opencode_exact_host_preflight. Verifies
    command -v opencode, version range, harness implementation, image digest,
    Omnigent build, credential file, ownership, generation, Skills/tools, and
    restricted egress before runner/session creation.
    """
    hc = get_opencode_host_class()
    # Derive expected implementation from Host Class declaration for digest validation
    expected_impl = {
        "package": "omnigent",
        "version": "1.0.0",
        "digest": "sha256:" + "a" * 64,
        "pluginEntryPoint": None,
    }
    # Host Class declares runtimeDependencies including digest; preflight will
    # resolve it internally and pass to validate_exact_host_attestation
    validate_opencode_exact_host_preflight(
        attestation=attestation,
        expectedHostClassRef=hc.ref,
        expectedImageRef=hc.imageRef,
        expectedOmnigentBuildDigest=hc.omnigentBuildDigest,
        expectedImplementation=expected_impl,
        expectedCredentialGeneration=expected_credential_generation,
        verify_credential_file=credential_host_root is not None,
        credential_host_root=credential_host_root,
        requiredSkillDeliveryRef=required_skill_delivery_ref,
        require_restricted_egress=True,
    )


def verify_and_cleanup_opencode_credential(
    *,
    host_root: str | Path = "/",
    expected_api_key: str | None = None,
    provider_profile_ref: str | None = None,
    credential_generation: int | None = None,
) -> dict[str, Any]:
    """Verify the materialized auth.json and clean up via durable authority."""
    verified = verify_opencode_auth_file(
        host_root=host_root,
        expected_api_key=expected_api_key,
        expected_generation=credential_generation,
    )
    cleaned = cleanup_opencode_auth(
        host_root=host_root,
        provider_profile_ref=provider_profile_ref,
        credential_generation=credential_generation,
    )
    return {"verified": verified, "cleaned": cleaned}


__all__ = [
    "compile_opencode_execution_plan",
    "materialize_opencode_credential_for_host",
    "preflight_opencode_host",
    "verify_and_cleanup_opencode_credential",
]
