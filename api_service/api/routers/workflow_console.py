"""Routes that serve the MoonMind workflow console UI shell."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.api.routers.workflow_console_view_model import (
    build_repository_branch_options,
    build_repository_issue_options,
    resolve_dashboard_runtime_config,
)
from api_service.auth_providers import get_current_user
from api_service.db.models import AgentSkillDefinition, TemporalArtifact, User
from api_service.dashboard_static import DASHBOARD_HTML_CACHE_CONTROL
from api_service.services.settings_catalog import settings_permissions_for_user
from moonmind.config.settings import settings
from moonmind.capabilities.input_contracts import (
    contract_from_artifact_metadata,
    content_digest_for_text,
    parse_skill_capability_input_contract,
)
from moonmind.services.skill_resolution import (
    extract_publish_metadata_from_skill_markdown,
    extract_required_capabilities_from_skill_markdown,
    extract_side_effect_metadata_from_skill_markdown,
)
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    list_available_skill_names,
    resolve_skill_markdown_path,
    resolve_skills_local_mirror_root,
    validate_skill_name,
)

from api_service.ui_boot import generate_boot_payload
from api_service.ui_assets import DashboardUIAssetsError, ui_assets

from moonmind.workflows.temporal import TemporalExecutionService

router = APIRouter(prefix="", tags=["workflow-console"])

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_SAFE_DETAIL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_WORKFLOW_ID_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:{}-]{0,254}$")
_DASHBOARD_UI_ERROR_DETAIL = (
    "Dashboard asset bundle is missing or invalid. Check server logs and rebuild "
    "the UI assets before retrying."
)
_WORKFLOW_DETAIL_TABS = {"chat", "overview", "steps", "artifacts", "runs", "debug"}
_RESERVED_WORKFLOW_ROUTE_SEGMENTS = {
    "manifests",
    "new",
    "proposals",
    "queue",
    "schedules",
    "secrets",
    "settings",
    "skills",
    "system",
    "temporal",
    "workers",
}
_MAX_SKILL_ZIP_BYTES = 50 * 1024 * 1024
_MAX_SKILL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_MAX_SKILL_FILE_BYTES = 25 * 1024 * 1024
_MAX_SKILL_ZIP_ENTRIES = 500
_MAX_INLINE_SKILL_INPUT_SCHEMA_BYTES = 8 * 1024
_IMPORTED_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKILL_INPUT_CONTRACT_CACHE_MAX = 256


@dataclass(frozen=True, slots=True)
class DashboardDestination:
    key: str
    label: str
    icon_key: str
    canonical_path: str
    path_patterns: tuple[str, ...]
    navigation_group: str
    page_classification: str
    capability_key: str
    endpoint_key: str | None = None
    display_mode: str | None = None
    page: str = "dashboard"
    data_wide_panel: bool = True

    def to_ui_info(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "iconKey": self.icon_key,
            "canonicalPath": self.canonical_path,
            "pathPatterns": list(self.path_patterns),
            "navigationGroup": self.navigation_group,
            "pageClassification": self.page_classification,
            "capabilityKey": self.capability_key,
        }
        if self.endpoint_key is not None:
            payload["endpointKey"] = self.endpoint_key
        if self.display_mode is not None:
            payload["displayMode"] = self.display_mode
        return payload


DASHBOARD_DESTINATIONS: tuple[DashboardDestination, ...] = (
    DashboardDestination(
        key="workflows",
        label="Workflows",
        icon_key="scroll-text",
        canonical_path="/workflows",
        path_patterns=(
            "/workflows",
            "/workflows/:workflowId",
            "/workflows/:workflowId/:detailTab",
        ),
        navigation_group="primary",
        page_classification="workspace",
        capability_key="workflowList",
        endpoint_key="workflows",
        display_mode="workflow-list",
        page="workflows-workspace",
    ),
    DashboardDestination(
        key="create",
        label="Create",
        icon_key="rocket",
        canonical_path="/workflows/new",
        path_patterns=("/workflows/new",),
        navigation_group="primary",
        page_classification="create",
        capability_key="workflowActions",
        page="workflows-workspace",
    ),
    DashboardDestination(
        key="recurring",
        label="Recurring",
        icon_key="moon",
        canonical_path="/schedules",
        path_patterns=("/schedules", "/schedules/:definitionId"),
        navigation_group="system",
        page_classification="workspace",
        capability_key="schedules",
        endpoint_key="schedules",
        display_mode="recurring-list",
        page="schedules",
    ),
    DashboardDestination(
        key="skills",
        label="Skills",
        icon_key="sparkles",
        canonical_path="/skills",
        path_patterns=("/skills/*",),
        navigation_group="system",
        page_classification="workspace",
        capability_key="skills",
        endpoint_key="skills",
        display_mode="skills-list",
        page="skills",
    ),
    DashboardDestination(
        key="manifests",
        label="Manifests",
        icon_key="manifest",
        canonical_path="/manifests",
        path_patterns=("/manifests", "/manifests/:manifestName"),
        navigation_group="operations",
        page_classification="collection",
        capability_key="manifests",
        endpoint_key="manifests",
        page="manifests",
    ),
    DashboardDestination(
        key="omnigent-agents",
        label="Agents",
        icon_key="bot",
        canonical_path="/omnigent/agents",
        path_patterns=("/omnigent/agents/*",),
        navigation_group="operations",
        page_classification="collection",
        capability_key="omnigentAgents",
        endpoint_key="omnigentAgents",
        page="omnigent-inventory",
    ),
    DashboardDestination(
        key="omnigent-policies",
        label="Policies",
        icon_key="shield-check",
        canonical_path="/omnigent/policies",
        path_patterns=("/omnigent/policies/*",),
        navigation_group="operations",
        page_classification="collection",
        capability_key="omnigentPolicies",
        endpoint_key="omnigentPolicies",
        page="omnigent-inventory",
    ),
    DashboardDestination(
        key="remediation",
        label="Remediation",
        icon_key="wrench",
        canonical_path="/remediations",
        path_patterns=("/remediations/*",),
        navigation_group="operations",
        page_classification="collection",
        capability_key="remediationCollection",
        endpoint_key="remediations",
        page="remediations",
    ),
    DashboardDestination(
        key="artifacts",
        label="Artifacts",
        icon_key="archive",
        canonical_path="/artifacts",
        path_patterns=("/artifacts/*", "/observability/*"),
        navigation_group="operations",
        page_classification="collection",
        capability_key="artifacts",
        endpoint_key="artifacts",
        page="artifacts",
    ),
    DashboardDestination(
        key="settings",
        label="Settings",
        icon_key="settings",
        canonical_path="/settings",
        path_patterns=("/settings/*",),
        navigation_group="system",
        page_classification="utility",
        capability_key="settings",
        endpoint_key="settings",
        page="settings",
    ),
)


_DASHBOARD_DESTINATIONS_BY_KEY = {
    destination.key: destination for destination in DASHBOARD_DESTINATIONS
}


@dataclass(frozen=True, slots=True)
class _CachedSkillInputContract:
    content_digest: str
    contract: dict[str, object]


_SKILL_INPUT_CONTRACT_CACHE: dict[
    tuple[str, str, int, int],
    _CachedSkillInputContract,
] = {}

_DASHBOARD_ROUTE_NOT_FOUND_DETAIL = {
    "code": "dashboard_route_not_found",
    "message": (
        "Workflow console route was not found. Use /workflows, /workflows/new, "
        "/workflows/{workflowId}, /workflows/{workflowId}/chat, "
        "/workflows/{workflowId}/overview, /workflows/{workflowId}/steps, "
        "/workflows/{workflowId}/artifacts, /workflows/{workflowId}/runs, "
        "or /workflows/{workflowId}/debug."
    ),
}


class CreateSkillRequest(BaseModel):
    """Payload for creating a new skill via the dashboard."""

    name: str = Field(..., description="The name of the new skill")
    markdown: str = Field(..., description="The markdown content of the new skill")


class DashboardSkillOption(BaseModel):
    """Serializable skill option exposed to dashboard clients."""

    id: str = Field(description="Skill identifier")
    kind: Literal["skill"] = Field("skill", description="Capability kind")
    label: str | None = Field(None, description="Human-readable skill label")
    description: str | None = Field(None, description="Skill description")
    required_capabilities: list[str] = Field(
        default_factory=list,
        alias="requiredCapabilities",
        description="Default required capabilities declared by Skill metadata",
    )
    publish: dict[str, Any] | None = Field(
        None,
        description="Skill-declared publish ownership metadata, when present",
    )
    side_effect: dict[str, Any] | None = Field(
        None,
        alias="sideEffect",
        description="Skill-declared non-repository side-effect metadata, when present",
    )
    markdown: str | None = Field(
        None, description="Markdown content of the skill, if requested"
    )
    input_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="inputSchema",
        description="Normalized Skill input JSON Schema, when small enough to inline",
    )
    ui_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="uiSchema",
        description="Presentation-only UI schema for generated input forms",
    )
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Safe normalized input defaults",
    )
    contract_digest: str | None = Field(
        None,
        alias="contractDigest",
        description="Digest of the normalized Skill input contract",
    )
    diagnostics: list[dict[str, str]] = Field(
        default_factory=list,
        description="Non-blocking Skill contract parser diagnostics",
    )
    source: dict[str, Any] | None = Field(
        None,
        description="Skill source and content evidence summary",
    )
    content_digest: str | None = Field(
        None,
        alias="contentDigest",
        description="Digest of the Skill content used to derive the contract",
    )
    has_input_schema: bool = Field(
        False,
        alias="hasInputSchema",
        description="Whether the Skill publishes structured input fields",
    )
    input_contract_ref: str | None = Field(
        None,
        alias="inputContractRef",
        description="Endpoint for the full Skill input contract when not inlined",
    )


class DashboardSkillListResponse(BaseModel):
    """Dashboard response containing available skill options."""

    items: dict[str, list[str]]
    legacy_items: list[DashboardSkillOption] = Field(
        default_factory=list, alias="legacyItems"
    )


class DashboardSkillInputContractResponse(DashboardSkillOption):
    """Detailed Skill contract response with the full schema inlined."""


class DashboardBranchOption(BaseModel):
    """Serializable Git branch option exposed to dashboard clients."""

    value: str = Field(description="Branch name")
    label: str = Field(description="Display label")
    source: str = Field(description="Branch option source")


class DashboardBranchListResponse(BaseModel):
    """Dashboard response containing branch options for one repository."""

    items: list[DashboardBranchOption] = Field(default_factory=list)
    error: str | None = Field(None)
    default_branch: str | None = Field(None, alias="defaultBranch")


class DashboardIssueOption(BaseModel):
    """Serializable GitHub issue option exposed to dashboard clients."""

    repository: str
    number: int
    title: str = ""
    body: str = ""
    url: str = ""
    state: str = ""
    labels: list[str] = Field(default_factory=list)


class DashboardIssueListResponse(BaseModel):
    """Dashboard response containing GitHub issue options for one repository."""

    items: list[DashboardIssueOption] = Field(default_factory=list)
    error: str | None = Field(None)


class DashboardUiInfoResponse(BaseModel):
    """Compact client-discoverable dashboard shell config for the SPA."""

    app: str = "moonmind"
    build_id: str | None = Field(None, alias="buildId")
    api_base: str = Field("/api", alias="apiBase")
    features: dict[str, bool] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    endpoints: dict[str, str] = Field(default_factory=dict)
    destinations: list[dict[str, object]] = Field(default_factory=list)
    dashboard_config: dict = Field(..., alias="dashboardConfig")
    settings_permissions: list[str] = Field(
        default_factory=list,
        alias="settingsPermissions",
    )
    worker_pause: dict = Field(default_factory=dict, alias="workerPause")


async def _read_file_backed_skill_input_contract(
    *,
    skill_id: str,
    label: str,
    skill_file: Path,
    source_kind: str,
) -> tuple[str, dict[str, object]]:
    """Read and parse a SKILL.md contract using content evidence as cache proof."""

    stat_result = await asyncio.to_thread(skill_file.stat)
    cache_key = (
        str(skill_file.resolve()),
        skill_id,
        stat_result.st_mtime_ns,
        stat_result.st_size,
    )
    skill_markdown = await asyncio.to_thread(skill_file.read_text, encoding="utf-8")
    content_digest = content_digest_for_text(skill_markdown)
    cached = _SKILL_INPUT_CONTRACT_CACHE.get(cache_key)
    if cached is not None and cached.content_digest == content_digest:
        return skill_markdown, dict(cached.contract)

    contract = parse_skill_capability_input_contract(
        skill_id=skill_id,
        label=label,
        markdown=skill_markdown,
        source={"kind": source_kind, "path": str(skill_file)},
    )
    _SKILL_INPUT_CONTRACT_CACHE[cache_key] = _CachedSkillInputContract(
        content_digest=content_digest,
        contract=dict(contract),
    )
    if len(_SKILL_INPUT_CONTRACT_CACHE) > _SKILL_INPUT_CONTRACT_CACHE_MAX:
        oldest_key = next(iter(_SKILL_INPUT_CONTRACT_CACHE))
        _SKILL_INPUT_CONTRACT_CACHE.pop(oldest_key, None)
    return skill_markdown, dict(contract)


class _ValidatedSkillZip(BaseModel):
    skill_name: str
    description: str
    root_prefix: str | None = None
    manifest_path: PurePosixPath


class SkillImportResponse(BaseModel):
    """Skill import result returned by the canonical upload contract."""

    import_id: str = Field(..., alias="import_id")
    status: Literal["saved"]
    skill_id: str = Field(..., alias="skill_id")
    version_id: str = Field(..., alias="version_id")
    version_number: int = Field(..., alias="version_number")
    name: str
    description: str
    warnings: list[dict[str, str]] = Field(default_factory=list)


def _is_safe_detail_segment(segment: str) -> bool:
    text = segment.strip()
    if not text:
        return False
    if text in {".", ".."}:
        return False
    return _SAFE_DETAIL_SEGMENT.fullmatch(text) is not None


def _is_safe_workflow_id_segment(segment: str) -> bool:
    text = segment.strip()
    if not text:
        return False
    if text in {".", ".."}:
        return False
    return _SAFE_WORKFLOW_ID_SEGMENT.fullmatch(text) is not None


def _normalize_workflow_detail_path(workflow_path: str) -> str | None:
    normalized = workflow_path.strip("/")
    if not normalized:
        return None
    if normalized != workflow_path or "//" in workflow_path:
        return None
    parts = normalized.split("/")
    if (
        len(parts) == 1
        and _is_safe_workflow_id_segment(parts[0])
        and parts[0].lower() not in _RESERVED_WORKFLOW_ROUTE_SEGMENTS
    ):
        return parts[0]
    if (
        len(parts) == 2
        and _is_safe_workflow_id_segment(parts[0])
        and parts[0].lower() not in _RESERVED_WORKFLOW_ROUTE_SEGMENTS
        and parts[1] in _WORKFLOW_DETAIL_TABS
    ):
        return f"{parts[0]}/{parts[1]}"
    return None


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _is_allowed_path(path: str) -> bool:
    return _normalize_workflow_detail_path(path) is not None


def _raise_dashboard_route_not_found() -> None:
    raise HTTPException(
        status_code=404,
        detail=_DASHBOARD_ROUTE_NOT_FOUND_DETAIL,
    )


def _is_extensionless_dashboard_path(path: str) -> bool:
    normalized = path.strip("/")
    if not normalized or "//" in path:
        return False
    return all(
        "." not in segment and segment not in {"", ".", ".."}
        for segment in normalized.split("/")
    )


def _dashboard_destination(key: str) -> DashboardDestination:
    return _DASHBOARD_DESTINATIONS_BY_KEY[key]


def _dashboard_destination_info() -> list[dict[str, object]]:
    return [destination.to_ui_info() for destination in DASHBOARD_DESTINATIONS]


def _is_extensionless_collection_route(request: Request, destination_keys: set[str]) -> bool:
    path = request.url.path
    if not path.startswith("/"):
        return False
    for key in destination_keys:
        destination = _dashboard_destination(key)
        for pattern in destination.path_patterns:
            if not pattern.endswith("/*"):
                continue
            prefix = pattern[:-2]
            if not path.startswith(f"{prefix}/"):
                continue
            return _is_extensionless_dashboard_path(path[len(prefix) + 1 :])
    return False


def _worker_pause_sources() -> dict[str, str]:
    return {
        "get": "/api/system/worker-pause",
        "post": "/api/system/worker-pause",
        "shardHealth": "/api/v1/operations/codex/shards",
    }


def _skill_input_contract_ref(skill_id: str, contract_digest: str | None) -> str:
    ref = f"/api/workflows/skills/{quote(skill_id, safe='')}/input-contract"
    if contract_digest:
        ref = f"{ref}?digest={quote(contract_digest, safe='')}"
    return ref


def _schema_inline_size(input_schema: dict[str, Any]) -> int:
    return len(
        json.dumps(input_schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _skill_option_from_contract(
    *,
    skill_id: str,
    label: str | None = None,
    description: str | None = None,
    required_capabilities: list[str] | None = None,
    publish: dict[str, Any] | None = None,
    side_effect: dict[str, Any] | None = None,
    markdown: str | None = None,
    contract: dict[str, Any],
    source: dict[str, Any] | None,
    inline_large_schema: bool = False,
) -> DashboardSkillOption:
    input_schema = dict(contract.get("inputSchema") or {})
    contract_digest = contract.get("contractDigest")
    has_input_schema = bool(contract.get("hasInputSchema"))
    input_contract_ref = None
    if (
        has_input_schema
        and not inline_large_schema
        and _schema_inline_size(input_schema) > _MAX_INLINE_SKILL_INPUT_SCHEMA_BYTES
    ):
        input_contract_ref = _skill_input_contract_ref(
            skill_id, str(contract_digest or "")
        )

    return DashboardSkillOption(
        id=skill_id,
        label=label or str(contract.get("label") or skill_id),
        description=(
            description
            if description is not None
            else (
                str(contract.get("description"))
                if isinstance(contract.get("description"), str)
                else None
            )
        ),
        requiredCapabilities=required_capabilities or [],
        publish=publish,
        sideEffect=side_effect,
        markdown=markdown,
        inputSchema=input_schema,
        uiSchema=dict(contract.get("uiSchema") or {}),
        defaults=dict(contract.get("defaults") or {}),
        contractDigest=contract_digest,
        diagnostics=list(contract.get("diagnostics") or []),
        source=source,
        contentDigest=contract.get("contentDigest"),
        hasInputSchema=has_input_schema,
        inputContractRef=input_contract_ref,
    )


async def _file_backed_skill_option(
    skill_id: str,
    *,
    include_content: bool,
    inline_large_schema: bool = False,
) -> DashboardSkillOption:
    markdown_content = None
    required_capabilities: list[str] = []
    publish: dict[str, Any] | None = None
    side_effect: dict[str, Any] | None = None
    contract = {
        "inputSchema": {"type": "object", "properties": {}},
        "uiSchema": {},
        "defaults": {},
        "contractDigest": None,
        "diagnostics": [],
        "contentDigest": None,
        "hasInputSchema": False,
    }
    source: dict[str, Any] | None = None
    skill_file = resolve_skill_markdown_path(skill_id)
    if skill_file is not None:
        skill_markdown, contract = await _read_file_backed_skill_input_contract(
            skill_id=skill_id,
            label=skill_id,
            skill_file=skill_file,
            source_kind="file",
        )
        required_capabilities = list(
            extract_required_capabilities_from_skill_markdown(
                skill_markdown,
                skill_name=skill_id,
                source_label=str(skill_file),
            )
        )
        publish = (
            extract_publish_metadata_from_skill_markdown(
                skill_markdown,
                skill_name=skill_id,
                source_label=str(skill_file),
            )
            or None
        )
        side_effect = (
            extract_side_effect_metadata_from_skill_markdown(
                skill_markdown,
                skill_name=skill_id,
                source_label=str(skill_file),
            )
            or None
        )
        source = {
            "kind": "file",
            "path": str(skill_file),
            "contentDigest": contract.get("contentDigest"),
        }
        if include_content:
            markdown_content = skill_markdown
    return _skill_option_from_contract(
        skill_id=skill_id,
        required_capabilities=required_capabilities,
        publish=publish,
        side_effect=side_effect,
        markdown=markdown_content,
        contract=contract,
        source=source,
        inline_large_schema=inline_large_schema,
    )


async def _deployment_skill_options(
    session: AsyncSession,
) -> list[DashboardSkillOption]:
    stmt = (
        select(AgentSkillDefinition, TemporalArtifact.metadata_json)
        .outerjoin(
            TemporalArtifact,
            TemporalArtifact.artifact_id == AgentSkillDefinition.artifact_ref,
        )
        .order_by(AgentSkillDefinition.slug.asc())
    )
    result = await session.execute(stmt)
    options: list[DashboardSkillOption] = []
    for definition, metadata_json in result.all():
        metadata = metadata_json if isinstance(metadata_json, dict) else {}
        contract = contract_from_artifact_metadata(
            (
                metadata.get("input_contract")
                if isinstance(metadata.get("input_contract"), dict)
                else {}
            ),
            skill_id=definition.slug,
            content_digest=definition.content_digest,
        )
        required_capabilities = [
            str(item)
            for item in metadata.get("required_capabilities") or []
            if str(item).strip()
        ]
        publish = (
            metadata.get("publish")
            if isinstance(metadata.get("publish"), dict)
            else None
        )
        side_effect = (
            metadata.get("sideEffect")
            if isinstance(metadata.get("sideEffect"), dict)
            else (
                metadata.get("side_effect")
                if isinstance(metadata.get("side_effect"), dict)
                else None
            )
        )
        source = {
            "kind": "deployment",
            "artifactRef": definition.artifact_ref,
            "contentDigest": definition.content_digest,
        }
        options.append(
            _skill_option_from_contract(
                skill_id=definition.slug,
                label=definition.title,
                description=definition.description,
                required_capabilities=required_capabilities,
                publish=dict(publish) if publish is not None else None,
                side_effect=dict(side_effect) if side_effect is not None else None,
                contract=contract,
                source=source,
            )
        )
    return options


def _resolve_user_dependency_overrides() -> list[Callable[..., object]]:
    """Return auth dependencies so tests can override them consistently."""

    dependencies: list[Callable[..., object]] = []
    for route in router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if call is None:
                continue
            if call.__name__ == "_current_user_fallback" or call.__name__.startswith(
                "current_"
            ):
                dependencies.append(call)

    if not dependencies:
        dependencies.append(get_current_user())
    return dependencies


async def _get_temporal_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _dashboard_ui_error_response(page: str, detail: str) -> HTMLResponse:
    """503 HTML when Vite assets are missing or incomplete (never a silent blank shell)."""
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MoonMind dashboard UI unavailable</title>
</head>
<body>
  <h1>MoonMind dashboard UI unavailable</h1>
  <p>Missing or incomplete Vite bundle for the shared dashboard entrypoint <code>dashboard</code>.</p>
  <p>While rendering dashboard page <code>{escape(page)}</code>.</p>
  <p>{escape(detail)}</p>
  <p>Rebuild with <code>npm run ui:build</code> or deploy a Docker image that builds the UI from source (see <code>api_service/Dockerfile</code> <code>frontend-builder</code> stage).</p>
</body>
</html>"""
    response = HTMLResponse(status_code=503, content=body, media_type="text/html")
    response.headers["Cache-Control"] = DASHBOARD_HTML_CACHE_CONTROL
    return response


def _vite_assets_or_error(page: str) -> HTMLResponse | str:
    try:
        return ui_assets("dashboard")
    except DashboardUIAssetsError:
        return _dashboard_ui_error_response(page, _DASHBOARD_UI_ERROR_DETAIL)


async def _render_react_page(
    request: Request,
    page: str,
    current_path: str,
    initial_data: dict | None = None,
    *,
    data_wide_panel: bool = False,
    session: AsyncSession | None = None,
    user: User | None = None,
) -> HTMLResponse:
    _ = (current_path, initial_data, data_wide_panel, session, user)
    boot_payload = generate_boot_payload("dashboard")
    assets_html = _vite_assets_or_error(page)
    if isinstance(assets_html, HTMLResponse):
        return assets_html

    response = templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
            "build_id": None,
        },
    )
    response.headers["Cache-Control"] = DASHBOARD_HTML_CACHE_CONTROL
    return response


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _is_unsupported_zip_file_type(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode not in {0, stat.S_IFREG}


def _normalize_zip_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise HTTPException(
            status_code=400,
            detail="Skill zip contains an unsafe path.",
        )
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(
            status_code=400,
            detail="Skill zip contains an unsafe path.",
        )
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise HTTPException(status_code=400, detail="Skill zip contains an empty path.")
    return PurePosixPath(*parts)


def _is_ignored_zip_member(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name == ".DS_Store"


def _validate_imported_skill_name(skill_name: str) -> str:
    try:
        normalized = validate_skill_name(skill_name)
    except SkillResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if _IMPORTED_SKILL_NAME_RE.fullmatch(normalized) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Skill manifest name must use lowercase letters, digits, and single "
                "hyphens only."
            ),
        )
    return normalized


def _parse_skill_manifest_metadata(markdown: str, parent_name: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    if not lines or lines[0] != "---":
        raise HTTPException(
            status_code=400,
            detail="Skill manifest must be Markdown with YAML frontmatter.",
        )

    try:
        end_index = lines[1:].index("---") + 1
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Skill manifest must close YAML frontmatter with '---'.",
        ) from exc

    raw_frontmatter = "\n".join(lines[1:end_index])
    try:
        metadata = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=400,
            detail="Skill manifest YAML frontmatter is invalid.",
        ) from exc
    if not isinstance(metadata, dict):
        raise HTTPException(
            status_code=400,
            detail="Skill manifest YAML frontmatter must be a mapping.",
        )

    raw_name = metadata.get("name")
    raw_description = metadata.get("description")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise HTTPException(status_code=400, detail="Skill manifest name is required.")
    if not isinstance(raw_description, str) or not raw_description.strip():
        raise HTTPException(
            status_code=400,
            detail="Skill manifest description is required.",
        )
    if len(raw_description.strip()) > 1024:
        raise HTTPException(
            status_code=400,
            detail="Skill manifest description must be 1024 characters or fewer.",
        )

    skill_name = _validate_imported_skill_name(raw_name)
    if skill_name != parent_name:
        raise HTTPException(
            status_code=400,
            detail="Skill manifest name must match the parent directory.",
        )
    return skill_name, raw_description.strip()


def _validate_skill_zip(filename: str | None, payload: bytes) -> _ValidatedSkillZip:
    if not payload:
        raise HTTPException(status_code=400, detail="Skill zip file is empty.")
    if len(payload) > _MAX_SKILL_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="Skill zip file is too large.")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid zip archive.",
        ) from exc

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if not infos:
            raise HTTPException(status_code=400, detail="Skill zip must contain files.")
        if len(infos) > _MAX_SKILL_ZIP_ENTRIES:
            raise HTTPException(
                status_code=413,
                detail="Skill zip contains too many files.",
            )

        total_size = 0
        normalized_paths: list[PurePosixPath] = []
        seen_paths: set[PurePosixPath] = set()
        for info in infos:
            if info.flag_bits & 0x1:
                raise HTTPException(
                    status_code=400,
                    detail="Encrypted skill zip entries are not supported.",
                )
            if _is_zip_symlink(info) or _is_unsupported_zip_file_type(info):
                raise HTTPException(
                    status_code=400,
                    detail="Skill zip cannot contain symlinks, hardlinks, or device files.",
                )
            if info.file_size > _MAX_SKILL_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Skill zip contains a file that is too large.",
                )
            total_size += info.file_size
            if total_size > _MAX_SKILL_UNCOMPRESSED_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Skill zip expands to too much data.",
                )
            normalized_path = _normalize_zip_member(info.filename)
            if _is_ignored_zip_member(normalized_path):
                continue
            if normalized_path in seen_paths:
                raise HTTPException(
                    status_code=400,
                    detail="Skill zip contains duplicate file paths.",
                )
            seen_paths.add(normalized_path)
            normalized_paths.append(normalized_path)

        if not normalized_paths:
            raise HTTPException(
                status_code=400, detail="Skill zip must contain skill files."
            )

        skill_files = [
            path for path in normalized_paths if path.name.lower() == "skill.md"
        ]
        top_level_names = {path.parts[0] for path in normalized_paths}
        nested_skill_files = [
            path
            for path in skill_files
            if len(path.parts) == 2 and path.parts[1].lower() == "skill.md"
        ]

        if (
            len(top_level_names) != 1
            or len(nested_skill_files) != 1
            or len(skill_files) != 1
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Skill zip must contain one skill directory with one "
                    "SKILL.md or skill.md file."
                ),
            )

        root_prefix = next(iter(top_level_names))
        skill_name = _validate_imported_skill_name(root_prefix)
        manifest_path = nested_skill_files[0]
        try:
            with archive.open(str(manifest_path)) as manifest_source:
                manifest_markdown = manifest_source.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail="Skill manifest must be UTF-8 Markdown.",
            ) from exc
        manifest_name, description = _parse_skill_manifest_metadata(
            manifest_markdown,
            parent_name=skill_name,
        )
        return _ValidatedSkillZip(
            skill_name=manifest_name,
            description=description,
            root_prefix=root_prefix,
            manifest_path=manifest_path,
        )


def _write_skill_zip(
    skill_dir: Path,
    payload: bytes,
    validated: _ValidatedSkillZip,
    *,
    collision_policy: Literal["reject", "new_version"] = "reject",
) -> None:
    parent = skill_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".skill-upload-", dir=parent))
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                normalized = _normalize_zip_member(info.filename)
                if _is_ignored_zip_member(normalized):
                    continue
                relative_parts = normalized.parts
                if validated.root_prefix is not None:
                    if relative_parts[0] != validated.root_prefix:
                        raise HTTPException(
                            status_code=400,
                            detail="Skill zip root changed during extraction.",
                        )
                    relative_parts = relative_parts[1:]
                if relative_parts and relative_parts[-1].lower() == "skill.md":
                    relative_parts = (*relative_parts[:-1], "SKILL.md")
                target = temp_dir.joinpath(*relative_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

                permissions = (info.external_attr >> 16) & 0o777
                if permissions & 0o111:
                    target.chmod(0o755)

        if not (temp_dir / "SKILL.md").is_file():
            raise HTTPException(
                status_code=400, detail="Skill zip must contain a SKILL.md file."
            )
        if skill_dir.exists():
            detail = f"Skill '{validated.skill_name}' already exists locally."
            if collision_policy == "new_version":
                detail = (
                    f"Skill '{validated.skill_name}' already exists locally; "
                    "new_version requires versioned skill storage."
                )
            raise HTTPException(status_code=409, detail=detail)
        shutil.move(str(temp_dir), str(skill_dir))
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _build_skill_import_response(
    payload: bytes,
    validated: _ValidatedSkillZip,
) -> SkillImportResponse:
    content_hash = hashlib.sha256(payload).hexdigest()
    return SkillImportResponse(
        import_id=f"skill-import-{uuid.uuid4().hex}",
        status="saved",
        skill_id=validated.skill_name,
        version_id=f"{validated.skill_name}-{content_hash[:12]}",
        version_number=1,
        name=validated.skill_name,
        description=validated.description,
        warnings=[],
    )


async def _import_skill_zip(
    file: UploadFile,
    collision_policy: Literal["reject", "new_version"],
) -> SkillImportResponse:
    payload = await file.read()
    validated = _validate_skill_zip(file.filename, payload)
    skills_root = resolve_skills_local_mirror_root()
    skill_dir = skills_root / validated.skill_name
    _write_skill_zip(
        skill_dir,
        payload,
        validated,
        collision_policy=collision_policy,
    )
    return _build_skill_import_response(payload, validated)


@router.get("/secrets")
async def secrets_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy secrets page into unified settings."""
    return RedirectResponse(url="/settings?section=providers-secrets", status_code=307)


@router.get("/workflows", name="workflow_console_root", response_class=HTMLResponse)
async def workflow_console_root(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered workflow list page."""
    destination = _dashboard_destination("workflows")
    return await _render_react_page(
        request,
        "workflow-list",
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/schedules", response_class=HTMLResponse)
async def schedules_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered schedules page."""
    destination = _dashboard_destination("recurring")
    return await _render_react_page(
        request,
        destination.page,
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/schedules/{schedule_id}", response_class=HTMLResponse)
async def schedule_detail_route(
    request: Request,
    schedule_id: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the schedules shell for schedule deep links."""
    if not _is_safe_detail_segment(schedule_id) or schedule_id.lower() == "new":
        raise HTTPException(status_code=404, detail="Not Found")
    destination = _dashboard_destination("recurring")
    return await _render_react_page(
        request,
        destination.page,
        f"/schedules/{schedule_id}",
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/manifests", response_class=HTMLResponse)
async def manifests_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered manifests page."""
    destination = _dashboard_destination("manifests")
    return await _render_react_page(
        request,
        "workflow-start",
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/manifests/new", status_code=307, response_class=RedirectResponse)
async def task_manifest_submit_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy manifest submit route into the unified manifests page."""
    return RedirectResponse(url="/manifests", status_code=307)


@router.get("/manifests/{manifest_name}", response_class=HTMLResponse)
async def task_manifest_detail_route(
    request: Request,
    manifest_name: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the manifests shell for manifest deep links."""
    if not _is_safe_detail_segment(manifest_name):
        _raise_dashboard_route_not_found()
    destination = _dashboard_destination("manifests")
    return await _render_react_page(
        request,
        destination.page,
        f"/manifests/{manifest_name}",
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/index-health", response_class=HTMLResponse)
async def index_health_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered RAG index health page."""
    return await _render_react_page(
        request,
        "index-health",
        "/index-health",
        data_wide_panel=True,
        session=session,
        user=_user,
    )


@router.get("/remediations", response_class=HTMLResponse)
async def remediations_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the capability-gated remediation inventory shell."""
    destination = _dashboard_destination("remediation")
    return await _render_react_page(
        request,
        destination.page,
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/artifacts", response_class=HTMLResponse)
@router.get("/observability", response_class=HTMLResponse)
async def artifact_collection_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the capability-gated artifact and observability collection shell."""
    destination = _dashboard_destination("artifacts")
    current_path = request.url.path
    return await _render_react_page(
        request,
        destination.page,
        current_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/workers")
async def task_workers_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy workers page into unified settings."""
    return RedirectResponse(url="/settings?section=operations", status_code=307)


@router.get("/settings", response_class=HTMLResponse)
async def task_settings_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered settings page."""
    destination = _dashboard_destination("settings")
    return await _render_react_page(
        request,
        destination.page,
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/settings/{dashboard_path:path}", response_class=HTMLResponse)
async def settings_spa_fallback_route(
    request: Request,
    dashboard_path: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the settings SPA shell for extensionless settings sub-routes."""
    if not _is_extensionless_dashboard_path(dashboard_path):
        _raise_dashboard_route_not_found()
    return await task_settings_route(request, session=session, _user=_user)


@router.get("/oauth-terminal", response_class=HTMLResponse)
async def oauth_terminal_route(
    request: Request,
    session_id: str = Query("", alias="session_id"),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the OAuth terminal shell launched from Settings."""
    current_path = "/oauth-terminal"
    return await _render_react_page(
        request,
        "oauth-terminal",
        current_path,
        initial_data={"sessionId": session_id},
        data_wide_panel=True,
        session=session,
        user=_user,
    )


@router.get("/omnigent/agents", response_class=HTMLResponse)
@router.get("/omnigent/policies", response_class=HTMLResponse)
async def omnigent_inventory_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve reloadable capability-gated Omnigent inventory routes."""
    destination = _dashboard_destination(
        "omnigent-policies"
        if request.url.path.startswith("/omnigent/policies")
        else "omnigent-agents"
    )
    return await _render_react_page(
        request,
        destination.page,
        request.url.path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/workflows/new", response_class=HTMLResponse)
async def task_create_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered workflow start page."""
    destination = _dashboard_destination("create")
    return await _render_react_page(
        request,
        destination.page,
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/skills", response_class=HTMLResponse)
async def skills_route(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered skills page."""
    destination = _dashboard_destination("skills")
    return await _render_react_page(
        request,
        destination.page,
        destination.canonical_path,
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/skills/{dashboard_path:path}", response_class=HTMLResponse)
async def skills_spa_fallback_route(
    request: Request,
    dashboard_path: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the skills SPA shell for extensionless skills sub-routes."""
    if not _is_extensionless_dashboard_path(dashboard_path):
        _raise_dashboard_route_not_found()
    destination = _dashboard_destination("skills")
    return await _render_react_page(
        request,
        destination.page,
        f"/skills/{dashboard_path.strip('/')}",
        data_wide_panel=destination.data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/workflows/{workflow_path:path}", response_class=HTMLResponse)
async def workflow_console_route(
    request: Request,
    workflow_path: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve dashboard sub-routes from one HTML shell."""

    normalized = _normalize_workflow_detail_path(workflow_path)
    if normalized is None:
        _raise_dashboard_route_not_found()

    detail_path = f"/workflows/{normalized}"
    return await _render_react_page(
        request,
        "workflow-detail",
        detail_path,
        data_wide_panel=_dashboard_destination("workflows").data_wide_panel,
        session=session,
        user=_user,
    )


@router.get("/artifacts/{dashboard_path:path}", response_class=HTMLResponse)
@router.get("/observability/{dashboard_path:path}", response_class=HTMLResponse)
@router.get("/remediations/{dashboard_path:path}", response_class=HTMLResponse)
@router.get("/omnigent/agents/{dashboard_path:path}", response_class=HTMLResponse)
@router.get("/omnigent/policies/{dashboard_path:path}", response_class=HTMLResponse)
async def collection_spa_fallback_route(
    request: Request,
    dashboard_path: str,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve recognized extensionless collection deep links from the SPA shell."""
    if not _is_extensionless_collection_route(
        request,
        {
            "artifacts",
            "omnigent-agents",
            "omnigent-policies",
            "remediation",
        },
    ):
        _raise_dashboard_route_not_found()
    return await _render_react_page(
        request,
        "dashboard",
        request.url.path,
        data_wide_panel=True,
        session=session,
        user=_user,
    )


@router.get("/api/ui/info", response_model=DashboardUiInfoResponse)
async def get_dashboard_ui_info(
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> DashboardUiInfoResponse:
    """Expose compact shell capabilities and endpoint discovery for the SPA."""
    dashboard_config = await resolve_dashboard_runtime_config(
        "/workflows/new",
        session=session,
        user=_user,
    )
    dashboard_config.pop("initialPath", None)
    system_config = dict(dashboard_config.get("system") or {})
    from api_service.api.routers.omnigent_bridge import (
        OMNIGENT_BRIDGE_MOUNT_PATH,
        get_bridge_config,
    )
    from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_PROXY
    from moonmind.omnigent.settings import build_omnigent_gate

    bridge_config = get_bridge_config()
    omnigent_agents_available = (
        bridge_config.enabled
        and bridge_config.host_protocol_mode == HOST_PROTOCOL_MODE_PROXY
        and build_omnigent_gate().enabled
    )
    return DashboardUiInfoResponse(
        buildId=system_config.get("buildId"),
        features={
            "workflowList": True,
            "workflowActions": True,
            "workflowEditing": True,
            "workflowLiveUpdates": True,
            "artifacts": True,
            "schedules": True,
            "skills": True,
            "settings": True,
            "manifests": True,
            "oauthTerminal": True,
            "remediationCollection": True,
            "omnigentAgents": omnigent_agents_available,
            # No authorized policy inventory read contract exists yet. Advertising
            # this explicitly keeps the rail and route free of dead links.
            "omnigentPolicies": False,
        },
        limits={
            "workflowListDefaultPageSize": 50,
            "workflowListMaxPageSize": 200,
            "artifactMaxUploadBytes": 10 * 1024 * 1024,
        },
        endpoints={
            "workflows": "/api/executions",
            "workflowDetail": "/api/executions/{workflowId}",
            "workflowSteps": "/api/executions/{workflowId}/steps",
            "workflowUpdatesPoll": "/api/executions",
            "workflowUpdatesStream": "/api/workflows/updates/stream",
            "workflowEventsStream": "/api/workflows/{workflowId}/events/stream",
            "artifacts": "/api/artifacts",
            "artifactCollection": "/api/artifacts/collection",
            "skills": "/api/workflows/skills",
            "schedules": "/api/recurring-workflows",
            "settings": "/api/settings",
            "manifests": "/api/manifests",
            "remediations": "/api/executions/remediations",
            **(
                {"omnigentAgents": f"{OMNIGENT_BRIDGE_MOUNT_PATH}/api/agents"}
                if omnigent_agents_available
                else {}
            ),
        },
        destinations=_dashboard_destination_info(),
        dashboardConfig=dashboard_config,
        settingsPermissions=sorted(settings_permissions_for_user(_user)),
        workerPause=_worker_pause_sources(),
    )


@router.get("/api/workflows/skills", response_model=DashboardSkillListResponse)
async def list_dashboard_skills(
    include_content: bool = Query(False, alias="includeContent"),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> DashboardSkillListResponse:
    """List currently available skills for workflow submission forms."""

    worker_skills = list(list_available_skill_names())
    legacy_sorted = sorted(set(worker_skills), key=str)

    legacy_items = await asyncio.gather(
        *(
            _file_backed_skill_option(skill_id, include_content=include_content)
            for skill_id in legacy_sorted
        )
    )
    deployment_items = await _deployment_skill_options(session)
    deployment_skill_ids = [item.id for item in deployment_items]
    merged_items = {
        item.id: item
        for item in [
            *deployment_items,
            *legacy_items,
        ]
    }
    items = {
        "worker": worker_skills,
    }
    if deployment_skill_ids:
        items["deployment"] = deployment_skill_ids

    return DashboardSkillListResponse(
        items=items,
        legacyItems=[merged_items[key] for key in sorted(merged_items)],
    )


@router.get(
    "/api/workflows/skills/{skill_id}/input-contract",
    response_model=DashboardSkillInputContractResponse,
)
async def get_dashboard_skill_input_contract(
    skill_id: str,
    digest: str | None = Query(None),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> DashboardSkillInputContractResponse:
    """Return a Skill catalog item with the full input contract inlined."""

    try:
        validated_skill_id = validate_skill_name(skill_id)
    except SkillResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skill_file = resolve_skill_markdown_path(validated_skill_id)
    if skill_file is not None:
        option = await _file_backed_skill_option(
            validated_skill_id,
            include_content=False,
            inline_large_schema=True,
        )
        if digest and option.contract_digest != digest:
            raise HTTPException(
                status_code=404,
                detail="Skill input contract digest was not found.",
            )
        return DashboardSkillInputContractResponse(**option.model_dump(by_alias=True))

    stmt = (
        select(AgentSkillDefinition, TemporalArtifact.metadata_json)
        .outerjoin(
            TemporalArtifact,
            TemporalArtifact.artifact_id == AgentSkillDefinition.artifact_ref,
        )
        .where(AgentSkillDefinition.slug == validated_skill_id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill was not found.")
    definition, metadata_json = row
    metadata = metadata_json if isinstance(metadata_json, dict) else {}
    contract = contract_from_artifact_metadata(
        (
            metadata.get("input_contract")
            if isinstance(metadata.get("input_contract"), dict)
            else {}
        ),
        skill_id=definition.slug,
        content_digest=definition.content_digest,
    )
    if digest and contract.get("contractDigest") != digest:
        raise HTTPException(
            status_code=404,
            detail="Skill input contract digest was not found.",
        )
    required_capabilities = [
        str(item)
        for item in metadata.get("required_capabilities") or []
        if str(item).strip()
    ]
    publish = (
        metadata.get("publish") if isinstance(metadata.get("publish"), dict) else None
    )
    side_effect = (
        metadata.get("sideEffect")
        if isinstance(metadata.get("sideEffect"), dict)
        else (
            metadata.get("side_effect")
            if isinstance(metadata.get("side_effect"), dict)
            else None
        )
    )
    option = _skill_option_from_contract(
        skill_id=definition.slug,
        label=definition.title,
        description=definition.description,
        required_capabilities=required_capabilities,
        publish=dict(publish) if publish is not None else None,
        side_effect=dict(side_effect) if side_effect is not None else None,
        contract=contract,
        source={
            "kind": "deployment",
            "artifactRef": definition.artifact_ref,
            "contentDigest": definition.content_digest,
        },
        inline_large_schema=True,
    )
    return DashboardSkillInputContractResponse(**option.model_dump(by_alias=True))


@router.get("/api/github/branches", response_model=DashboardBranchListResponse)
async def list_dashboard_github_branches(
    repository: str = Query(..., min_length=1),
    _user: User = Depends(get_current_user()),
) -> DashboardBranchListResponse:
    """List GitHub branches through MoonMind so browsers never call GitHub directly."""

    payload = build_repository_branch_options(repository)
    return DashboardBranchListResponse(**payload)


@router.get("/api/github/issues", response_model=DashboardIssueListResponse)
def list_dashboard_github_issues(
    repository: str = Query(..., min_length=1),
    q: str = Query(""),
    _user: User = Depends(get_current_user()),
) -> DashboardIssueListResponse:
    """List GitHub issues through MoonMind so browsers never call GitHub directly."""

    payload = build_repository_issue_options(repository, q)
    return DashboardIssueListResponse(**payload)


@router.post(
    "/api/workflows/skills",
    status_code=201,
)
async def create_dashboard_skill(
    request: CreateSkillRequest,
    _user: User = Depends(get_current_user()),
) -> dict[str, str]:
    """Create a new local skill from the dashboard."""

    try:
        validated_name = validate_skill_name(request.name)
    except SkillResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skills_root = resolve_skills_local_mirror_root()
    skill_dir = skills_root / validated_name

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{validated_name}' already exists locally.",
        ) from exc
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(request.markdown, encoding="utf-8")

    return {"status": "success"}


@router.post(
    "/api/skills/imports",
    status_code=201,
    response_model=SkillImportResponse,
)
async def create_skill_import(
    file: UploadFile = File(...),
    collision_policy: Literal["reject", "new_version"] = Form("reject"),
    _user: User = Depends(get_current_user()),
) -> SkillImportResponse:
    """Create a new local skill from an uploaded zip bundle."""

    return await _import_skill_zip(file, collision_policy)


@router.post(
    "/api/workflows/skills/upload",
    status_code=201,
)
async def upload_dashboard_skill_zip(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user()),
) -> dict[str, str]:
    """Create a new local skill from an uploaded zip bundle."""

    result = await _import_skill_zip(file, "reject")

    return {"status": "success", "skill": result.name}


__all__ = [
    "router",
    "_is_allowed_path",
    "_resolve_user_dependency_overrides",
]
