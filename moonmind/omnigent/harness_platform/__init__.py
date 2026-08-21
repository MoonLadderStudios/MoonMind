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
    validate_agent_profile,
    decode_v1_profile_to_v2_inputs,
)
from moonmind.omnigent.harness_platform.attestation import (
    HostHarnessAttestation,
    validate_exact_host_attestation,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessCatalogSnapshot,
    HarnessImplementationIdentity,
    HarnessTrustRecord,
    TrustState,
    create_catalog_snapshot,
    compute_catalog_ref,
    assert_catalog_fresh,
    classify_harness_trust,
    is_launchable_trust,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBindingSet,
    create_binding_set,
    parse_binding_set_ref,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanPayload,
    OmnigentExecutionPlanEnvelope,
    create_execution_plan_envelope,
    compute_plan_ref,
    compute_model_config_digest,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.host_classes import (
    OPENCODE_PINNED_VERSION,
    OPENCODE_SUPPORTED_RANGE,
    OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT,
    OMNIGENT_OPENCODE_HOST_IMAGE_ENV,
    HostClass,
    LaunchPolicy,
    get_host_class,
    get_harness_implementation,
    get_launch_policy,
    get_opencode_host_class,
    get_opencode_host_image_ref,
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
    cleanup_opencode_auth,
    clear_forbidden_ambient_env,
    get_materializer,
    materialize_credential,
    materialize_opencode_auth_json,
    verify_opencode_auth_file,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    OmnigentRuntimeBinding,
    create_runtime_binding,
    assert_runtime_binding_generation_sticky,
)
from moonmind.omnigent.harness_platform.skills import (
    ResolvedSkillSet,
)
from moonmind.omnigent.harness_platform.support import (
    SupportClassification,
    SupportKeyPayload,
    compute_support_combination_key,
    compute_required_capabilities_digest,
)
from moonmind.omnigent.harness_platform.planner import (
    compile_execution_plan,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

__all__ = [
    "BundleSource",
    "UpstreamSource",
    "OmnigentAgentProfileV2",
    "validate_agent_profile",
    "decode_v1_profile_to_v2_inputs",
    "HostHarnessAttestation",
    "validate_exact_host_attestation",
    "HarnessCatalogSnapshot",
    "HarnessImplementationIdentity",
    "HarnessTrustRecord",
    "TrustState",
    "create_catalog_snapshot",
    "compute_catalog_ref",
    "assert_catalog_fresh",
    "classify_harness_trust",
    "is_launchable_trust",
    "CredentialBindingSet",
    "create_binding_set",
    "parse_binding_set_ref",
    "OmnigentExecutionPlanPayload",
    "OmnigentExecutionPlanEnvelope",
    "create_execution_plan_envelope",
    "compute_plan_ref",
    "compute_model_config_digest",
    "verify_execution_plan_envelope",
    "HostClass",
    "LaunchPolicy",
    "get_host_class",
    "get_harness_implementation",
    "get_launch_policy",
    "get_opencode_host_class",
    "get_opencode_host_image_ref",
    "register_host_class",
    "register_launch_policy",
    "OMNIGENT_OPENCODE_HOST_IMAGE_ENV",
    "OMNIGENT_OPENCODE_HOST_IMAGE_DEFAULT",
    "OPENCODE_PINNED_VERSION",
    "OPENCODE_SUPPORTED_RANGE",
    "CredentialMaterializer",
    "get_materializer",
    "materialize_credential",
    "materialize_opencode_auth_json",
    "verify_opencode_auth_file",
    "cleanup_opencode_auth",
    "build_opencode_auth_json_bytes",
    "assert_opencode_materialization_secret_free",
    "clear_forbidden_ambient_env",
    "OPENCODE_AUTH_TARGET_PATH",
    "OPENCODE_AUTH_PARENT_MODE",
    "OPENCODE_AUTH_FILE_MODE",
    "OPENCODE_PROVIDER_KEY",
    "OPENCODE_SUPPORTED_VERSION_RANGE",
    "FORBIDDEN_AMBIENT_ENV_KEYS",
    "OmnigentRuntimeBinding",
    "create_runtime_binding",
    "assert_runtime_binding_generation_sticky",
    "ResolvedSkillSet",
    "SupportClassification",
    "SupportKeyPayload",
    "compute_support_combination_key",
    "compute_required_capabilities_digest",
    "compile_execution_plan",
    "HarnessPlatformError",
    "HarnessPlatformFailure",
]
