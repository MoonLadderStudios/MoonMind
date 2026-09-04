"""Omnigent Harness Platform - generic harness execution platform.

Implements docs/Omnigent/OmnigentHarnessPlatformDesign.md
- One top-level Omnigent identity (external/omnigent)
- Catalog-driven harness support with exact implementation identity
- Two-stage capability proof (class-level admission + exact-host attestation)
- Discriminated agent source, immutable Skills, versioned credential-binding sets
- Host Classes + materializer registry + launch policies
- Canonical secret-free execution plan + fenced runtime binding
- Support classification keyed by exact model config + realizer version
- Preserves Codex lane via codex-profile-bound@1 realizer coexistence
"""

from moonmind.omnigent.harness_platform.agent_profile import (
    BundleSource,
    OmnigentAgentProfileV2,
    UpstreamSource,
    decode_v1_profile_to_v2_inputs,
    validate_agent_profile,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    validate_exact_host_attestation,
    validate_runtime_pack_preflight,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessImplementationIdentity,
    HarnessTrustRecord,
    TrustState,
    assert_catalog_fresh,
    assert_catalog_refresh_attests,
    classify_harness_trust,
    compute_catalog_ref,
    create_catalog_snapshot,
    is_launchable_trust,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBindingSet,
    create_binding_set,
    parse_binding_set_ref,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
    OmnigentExecutionPlanPayload,
    compute_model_config_digest,
    compute_plan_ref,
    create_execution_plan_envelope,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    OMNIGENT_OPENCODE_HOST_IMAGE_ENV,
    OMNIGENT_SHARED_HOST_IMAGE_ENV,
    OPENCODE_PINNED_VERSION,
    OPENCODE_SUPPORTED_RANGE,
    HostClass,
    LaunchPolicy,
    get_host_class,
    get_launch_policy,
    get_opencode_host_class,
    get_opencode_host_image_ref,
    get_shared_host_image_ref,
    register_host_class,
    register_launch_policy,
)
from moonmind.omnigent.harness_platform.materializers import (
    FORBIDDEN_AMBIENT_ENV_KEYS,
    OPENCODE_AUTH_FILE_MODE,
    OPENCODE_AUTH_PARENT_MODE,
    OPENCODE_AUTH_TARGET_PATH,
    OPENCODE_PROVIDER_KEY,
    OPENCODE_SUPPORTED_VERSION_RANGE,
    CredentialMaterializer,
    assert_opencode_materialization_secret_free,
    build_opencode_auth_json_bytes,
    clear_forbidden_ambient_env,
    get_materializer,
    materialize_credential,
)
from moonmind.omnigent.harness_platform.planner import (
    compile_execution_plan,
    select_execution_realizer,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
    assert_runtime_binding_generation_sticky,
    create_runtime_binding,
)
from moonmind.omnigent.harness_platform.runtime_packs import (
    RUNTIME_PACK_SCHEMA_VERSION,
    RuntimePackDescriptor,
    get_runtime_pack,
    is_vendor_version_supported,
    pack_ref_for_harness,
    register_runtime_pack,
    runtime_dependencies_for_pack,
)
from moonmind.omnigent.harness_platform.shared_host_conformance import (
    SHARED_HOST_CONFORMANCE_CATALOG_VERSION,
    RequiredRow,
    SharedHostImageInventory,
    assert_credential_isolation,
    assert_failure_isolation,
    assert_lifecycle_order,
    assert_one_harness_admission,
    assert_ownership_cleanup,
    assert_runtime_readiness,
    build_conformance_evidence,
    build_shared_host_image_inventory,
)
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.support import (
    SupportClassification,
    SupportKeyPayload,
    compute_required_capabilities_digest,
    compute_support_combination_key,
)

__all__ = [
    "FORBIDDEN_AMBIENT_ENV_KEYS",
    "OMNIGENT_OPENCODE_HOST_IMAGE_ENV",
    "OMNIGENT_SHARED_HOST_IMAGE_ENV",
    "OPENCODE_AUTH_FILE_MODE",
    "OPENCODE_AUTH_PARENT_MODE",
    "OPENCODE_AUTH_TARGET_PATH",
    "OPENCODE_PINNED_VERSION",
    "OPENCODE_PROVIDER_KEY",
    "OPENCODE_SUPPORTED_RANGE",
    "OPENCODE_SUPPORTED_VERSION_RANGE",
    "RUNTIME_PACK_SCHEMA_VERSION",
    "BundleSource",
    "CredentialBindingSet",
    "CredentialMaterializer",
    "HarnessCatalogSnapshot",
    "HarnessImplementationIdentity",
    "HarnessPlatformError",
    "HarnessPlatformFailure",
    "HarnessTrustRecord",
    "HostClass",
    "HostHarnessAttestation",
    "LaunchPolicy",
    "OmnigentAgentProfileV2",
    "OmnigentExecutionPlanEnvelope",
    "OmnigentExecutionPlanPayload",
    "OmnigentRuntimeBinding",
    "ResolvedSkillSet",
    "RuntimePackDescriptor",
    "SHARED_HOST_CONFORMANCE_CATALOG_VERSION",
    "SharedHostImageInventory",
    "RequiredRow",
    "SupportClassification",
    "SupportKeyPayload",
    "TrustState",
    "UpstreamSource",
    "assert_catalog_fresh",
    "assert_catalog_refresh_attests",
    "assert_credential_isolation",
    "assert_failure_isolation",
    "assert_lifecycle_order",
    "assert_one_harness_admission",
    "assert_opencode_materialization_secret_free",
    "assert_ownership_cleanup",
    "assert_runtime_binding_generation_sticky",
    "assert_runtime_readiness",
    "build_conformance_evidence",
    "build_opencode_auth_json_bytes",
    "build_shared_host_image_inventory",
    "classify_harness_trust",
    "clear_forbidden_ambient_env",
    "compile_execution_plan",
    "compute_catalog_ref",
    "compute_model_config_digest",
    "compute_plan_ref",
    "compute_required_capabilities_digest",
    "compute_support_combination_key",
    "create_binding_set",
    "create_catalog_snapshot",
    "create_execution_plan_envelope",
    "create_runtime_binding",
    "decode_v1_profile_to_v2_inputs",
    "get_host_class",
    "get_launch_policy",
    "get_materializer",
    "get_opencode_host_class",
    "get_opencode_host_image_ref",
    "get_runtime_pack",
    "get_shared_host_image_ref",
    "is_launchable_trust",
    "is_vendor_version_supported",
    "materialize_credential",
    "pack_ref_for_harness",
    "parse_binding_set_ref",
    "register_host_class",
    "register_launch_policy",
    "register_runtime_pack",
    "runtime_dependencies_for_pack",
    "select_execution_realizer",
    "validate_agent_profile",
    "validate_exact_host_attestation",
    "validate_runtime_pack_preflight",
    "verify_execution_plan_envelope",
]
