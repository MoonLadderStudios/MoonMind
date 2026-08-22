"""One-action OpenCode bootstrap API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.settings_catalog import has_settings_permission

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
    from moonmind.omnigent.bootstrap.controller import BootstrapController
    from api_service.db.base import async_session_maker

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
    from moonmind.omnigent.bootstrap.controller import BootstrapController
    from api_service.db.base import async_session_maker

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
    from moonmind.omnigent.bootstrap.controller import BootstrapController
    from moonmind.omnigent.bootstrap.store import load_bootstrap_record
    from api_service.db.base import async_session_maker

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
    from moonmind.omnigent.settings import generic_host_enabled, opencode_support_enabled
    from moonmind.omnigent.bootstrap.store import load_bootstrap_record, load_resolved_state
    from pathlib import Path
    import json, os

    record = load_bootstrap_record()
    resolved = load_resolved_state()
    # Check deployment evidence
    evidence_path = Path(os.getenv("MOONMIND_OMNIGENT_DEPLOYMENT_EVIDENCE", "var/omnigent-evidence/deployment-execution-evidence.json"))
    has_evidence = evidence_path.exists()
    # Also check compose path
    compose_evidence = Path("/workspace/omnigent-evidence/deployment-execution-evidence.json")
    if not has_evidence and compose_evidence.exists():
        has_evidence = True
    enabled = generic_host_enabled() and opencode_support_enabled()
    state = record.state.value if record else "not_started"
    if not enabled:
        readiness = "disabled"
    elif has_evidence and state == "ready":
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
        "hasDeploymentEvidence": has_evidence,
        "resolvedImages": resolved.model_dump(mode="json", by_alias=True) if resolved else None,
        "bootstrap": record.model_dump(mode="json", by_alias=True) if record else None,
    }
