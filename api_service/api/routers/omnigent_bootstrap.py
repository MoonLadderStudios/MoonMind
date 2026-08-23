"""One-action OpenCode bootstrap API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User

router = APIRouter(prefix="/api/omnigent/bootstrap", tags=["Omnigent Bootstrap"])


class BootstrapOpencodeRequest(BaseModel):
    api_key: str = Field(alias="apiKey", min_length=1, max_length=8192)
    model_display_name: str = Field(
        default="Muse Spark 1.2 Contributor",
        alias="modelDisplayName",
        max_length=255,
    )
    effort: str = Field(default="xhigh", max_length=64)
    accept_contributor_data_use: bool = Field(
        default=False, alias="acceptContributorDataUse"
    )

    model_config = {"populate_by_name": True}


class BootstrapOpencodeResponse(BaseModel):
    bootstrap_id: str = Field(alias="bootstrapId")
    state: str
    provider_profile_ref: str | None = Field(default=None, alias="providerProfileRef")
    requested_model: str = Field(alias="requestedModel")
    requested_effort: str = Field(alias="requestedEffort")
    failure: dict[str, Any] | None = None
    resolved: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


def _require_bootstrap_permission(user: User) -> None:
    # Reuse provider_profiles.write or settings
    from api_service.api.routers.provider_profiles import (
        _require_provider_profile_permission,
    )

    _require_provider_profile_permission(user, "provider_profiles.write")


@router.get("/opencode", response_model=BootstrapOpencodeResponse)
async def get_bootstrap_state(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_bootstrap_permission(current_user)
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bootstrap.controller import BootstrapController

    controller = BootstrapController(session_factory=async_session_maker)
    record = await controller.get_state()
    return {
        "bootstrapId": record.bootstrap_id,
        "state": record.state.value,
        "providerProfileRef": record.provider_profile_ref,
        "requestedModel": record.desired.model_display_name,
        "requestedEffort": record.desired.effort,
        "failure": record.failure,
        "resolved": record.resolved.model_dump(mode="json", by_alias=True) if record.resolved else None,
    }


@router.post("/opencode", response_model=BootstrapOpencodeResponse)
async def bootstrap_opencode(
    body: BootstrapOpencodeRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_bootstrap_permission(current_user)
    from api_service.db.base import async_session_maker
    from moonmind.omnigent.bootstrap.controller import BootstrapController

    controller = BootstrapController(session_factory=async_session_maker)
    try:
        record = await controller.configure_opencode(
            api_key=body.api_key,
            model_display_name=body.model_display_name,
            effort=body.effort,
            accept_contributor_data_use=body.accept_contributor_data_use,
            principal=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc

    if record.state.value == "failed":
        # Return 409 with state but not throw? For UX, return 200 with failed state so UI can show failure
        # But also surface failure message
        pass
    return {
        "bootstrapId": record.bootstrap_id,
        "state": record.state.value,
        "providerProfileRef": record.provider_profile_ref,
        "requestedModel": record.desired.model_display_name,
        "requestedEffort": record.desired.effort,
        "failure": record.failure,
        "resolved": record.resolved.model_dump(mode="json", by_alias=True) if record.resolved else None,
    }


@router.post("/opencode/retry", response_model=BootstrapOpencodeResponse)
async def retry_bootstrap(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    _require_bootstrap_permission(current_user)
    from moonmind.omnigent.bootstrap.store import load_bootstrap_record

    record = load_bootstrap_record()
    if record is None:
        raise HTTPException(status_code=404, detail="no bootstrap state to retry")
    # Need api key? We don't store it. So retry requires re-providing key.
    # For now, fail with clear message
    raise HTTPException(
        status_code=422,
        detail="Retry requires re-submitting the API key via POST /opencode",
    )


@router.get("/readiness")
async def bootstrap_readiness(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
) -> dict[str, Any]:
    """Return computed readiness for OpenCode via Omnigent."""
    import json
    import os
    from pathlib import Path

    from moonmind.omnigent.bootstrap.store import (
        load_bootstrap_record,
        load_resolved_state,
    )
    from moonmind.omnigent.deployment_evidence import (
        validate_deployment_evidence,
    )
    from moonmind.omnigent.settings import (
        generic_host_enabled,
        opencode_support_enabled,
    )

    record = load_bootstrap_record()
    resolved = load_resolved_state()
    # Check deployment evidence with validation
    evidence_path = Path(os.getenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "var/omnigent-evidence/deployment-execution-evidence.json"))
    compose_evidence = Path("/workspace/omnigent-evidence/deployment-execution-evidence.json")
    # Resolve actual path to check
    actual_path = evidence_path if evidence_path.exists() else (compose_evidence if compose_evidence.exists() else None)
    has_evidence = False
    evidence_valid = False
    if actual_path is not None and actual_path.exists():
        try:
            raw = json.loads(actual_path.read_text(encoding="utf-8"))
            from collections.abc import Mapping

            if not isinstance(raw, Mapping):
                raise ValueError("deployment evidence must be an object")
            entries = raw.get("entries")
            candidates = list(entries) if isinstance(entries, list) else [raw]
            # If record has a last evidence ref, filter to that combination
            target_key = record.last_evidence_ref if record and record.last_evidence_ref else None
            if target_key:
                matching = [
                    v for v in candidates if isinstance(v, Mapping) and v.get("supportCombinationKey") == target_key
                ]
                if len(matching) != 1:
                    raise ValueError("exact deployment execution evidence is unavailable for recorded combination")
                candidate = matching[0]
            else:
                # No recorded key: require exactly one entry
                if len(candidates) != 1:
                    raise ValueError("exact deployment execution evidence is unavailable")
                candidate = candidates[0]
            # Validate evidence (checks HMAC, expiry, future-dated, support key recompute, secret-free)
            validate_deployment_evidence(candidate)
            has_evidence = True
            # If we have resolved state, additionally verify host image matches evidence where possible
            # For full plan match, we would need to reconstruct plan payload; at minimum ensure evidence is not stale
            evidence_valid = True
        except Exception:
            has_evidence = False
            evidence_valid = False
    # Also check alternative path existence for has_evidence flag without validation? No, we already validated.
    enabled = generic_host_enabled() and opencode_support_enabled()
    state = record.state.value if record else "not_started"
    if not enabled:
        readiness = "disabled"
    elif evidence_valid and has_evidence and state == "ready":
        readiness = "ready"
    elif state in {"resolving_images", "syncing_catalog", "validating_credentials", "qualifying_runtime", "publishing_evidence"}:
        readiness = "preparing" if state in {"resolving_images", "syncing_catalog"} else "qualifying"
    elif state == "failed":
        readiness = "blocked"
    else:
        readiness = "setup_required"
    return {
        "enabled": enabled,
        "state": state,
        "readiness": readiness,
        "hasDeploymentEvidence": has_evidence and evidence_valid,
        "resolvedImages": resolved.model_dump(mode="json", by_alias=True) if resolved else None,
        "bootstrap": record.model_dump(mode="json", by_alias=True) if record else None,
    }
