"""Generate deployment evidence from qualification results."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.deployment_evidence import (
    DEPLOYMENT_EVIDENCE_DEFAULT_TTL,
    DEPLOYMENT_EVIDENCE_ISSUER,
    DEPLOYMENT_EVIDENCE_VERSION,
    sign_deployment_evidence,
)
from moonmind.omnigent.harness_platform.support import SupportKeyPayload


def build_deployment_evidence(
    *,
    support_identity: SupportKeyPayload,
    support_combination_key: str,
    host_image_ref: str,
    policy_snapshot_digest: str,
    effective_launch_snapshot_digest: str,
    provider_profile_ref: str,
    credential_generation: int,
    qualified_model_id: str,
    effort: str,
    results: dict[str, str],
    evidence_refs: dict[str, str],
    resolved_state: ResolvedOmnigentDeploymentState | None,
    deployment_id: str = "local-default",
    compatibility_generation: str = "omnigent-generic-host/1",
) -> dict[str, Any]:
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    now = datetime.now(UTC)
    expires = now + DEPLOYMENT_EVIDENCE_DEFAULT_TTL
    payload = {
        "schemaVersion": DEPLOYMENT_EVIDENCE_VERSION,
        "evidenceIssuer": DEPLOYMENT_EVIDENCE_ISSUER,
        "deploymentId": deployment_id,
        "compatibilityGeneration": compatibility_generation,
        "generatedAt": now.isoformat(),
        "expiresAt": expires.isoformat(),
        "supportCombinationKey": support_combination_key,
        "supportIdentity": support_identity.model_dump(mode="json", by_alias=True),
        "hostImageRef": host_image_ref,
        "policySnapshotDigest": policy_snapshot_digest,
        "effectiveLaunchSnapshotDigest": effective_launch_snapshot_digest,
        "provider": {
            "profileRef": provider_profile_ref,
            "credentialGeneration": credential_generation,
        },
        "model": {
            "qualifiedId": qualified_model_id,
            "effort": effort,
        },
        "results": results,
        "evidenceRefs": evidence_refs,
        "featureGeneration": OMNIGENT_SESSION_FEATURE_GENERATION,
        "replayCompatibilityVersion": OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        "rollbackPolicyVersion": SUPERVISOR_ROLLBACK_POLICY_VERSION,
    }
    signed = sign_deployment_evidence(payload)
    return signed


def write_deployment_evidence(evidence: dict[str, Any], path: Path | None = None) -> Path:
    dest = path or Path(
        os.getenv(
            "MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE",
            "var/omnigent-evidence/deployment-execution-evidence.json",
        )
    )
    # Also handle compose mount
    compose_dest = Path("/workspace/omnigent-evidence/deployment-execution-evidence.json")
    # Ensure parent
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        compose_dest.parent.mkdir(parents=True, exist_ok=True)
        compose_dest.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        # Best-effort: compose volume may be unavailable in hermetic tests
        pass
    # If evidence is single entry, also support entries array form for loader compatibility
    # The loader handles both raw and entries list, so single object is fine
    return dest
