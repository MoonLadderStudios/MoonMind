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
    validate_deployment_evidence,
)
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_deployment_qualification_key,
)


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


def write_deployment_evidence(
    evidence: dict[str, Any], path: Path | None = None
) -> Path:
    """Upsert one signed qualification without discarding other materializers.

    A deployment can expose multiple launch-ready credential classes for the
    same harness. Each class requires exact materializer evidence, while the
    evidence file remains one deployment-owned publication. Preserve valid
    entries for other deployment qualification keys and replace only the
    incoming key.
    """

    dest = path or Path(
        os.getenv(
            "MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE",
            "var/omnigent-evidence/deployment-execution-evidence.json",
        )
    )
    incoming = validate_deployment_evidence(evidence)
    incoming_key = compute_deployment_qualification_key(incoming.support_identity)
    retained: list[dict[str, Any]] = []
    try:
        existing = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = None
    if isinstance(existing, dict):
        raw_entries = existing.get("entries")
        candidates = raw_entries if isinstance(raw_entries, list) else [existing]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            try:
                parsed = validate_deployment_evidence(candidate)
            except ValueError:
                continue
            if (
                compute_deployment_qualification_key(parsed.support_identity)
                == incoming_key
            ):
                continue
            retained.append(parsed.model_dump(mode="json", by_alias=True))

    entries = [
        *retained,
        incoming.model_dump(mode="json", by_alias=True),
    ]
    entries.sort(key=lambda item: str(item["supportCombinationKey"]))
    payload = {"entries": entries}

    def _write(destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(destination)

    _write(dest)
    compose_dest = Path(
        "/workspace/omnigent-evidence/deployment-execution-evidence.json"
    )
    if compose_dest != dest:
        try:
            _write(compose_dest)
        except OSError:
            # Best-effort: compose volume may be unavailable in hermetic tests.
            pass
    return dest
