"""Trusted Jira auth resolution for managed-agent tool execution."""

from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass

from moonmind.config.settings import AtlassianSettings, settings
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
    resolve_managed_api_key_reference,
)

_VALID_AUTH_MODES = frozenset({"service_account_scoped", "basic"})


@dataclass(frozen=True, slots=True)
class ResolvedJiraConnection:
    """Resolved Jira connection details for one trusted tool call."""

    auth_mode: str
    base_url: str
    headers: dict[str, str]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    retry_attempts: int
    redaction_values: tuple[str, ...]


def _normalize_url(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if normalized.startswith("https://https://"):
        normalized = normalized[8:]
    if not normalized:
        return None
    return normalized.rstrip("/")


async def _resolve_binding_value(
    *,
    binding_name: str,
    raw_value: str | None,
    secret_ref: str | None,
) -> str | None:
    ref = str(secret_ref or "").strip()
    if ref:
        try:
            return str(await resolve_managed_api_key_reference(ref)).strip() or None
        except Exception as exc:
            raise JiraToolError(
                f"Jira binding '{binding_name}' could not be resolved.",
                code="jira_not_configured",
                status_code=503,
            ) from exc

    raw = str(raw_value or "").strip()
    return raw or None


async def resolve_jira_connection(
    atlassian_settings: AtlassianSettings | None = None,
) -> ResolvedJiraConnection:
    """Resolve one Jira tool binding from settings and SecretRefs."""

    cfg = atlassian_settings or settings.atlassian
    jira_cfg = cfg.jira

    auth_mode = await _resolve_binding_value(
        binding_name="auth_mode",
        raw_value=cfg.atlassian_auth_mode,
        secret_ref=cfg.atlassian_auth_mode_secret_ref,
    )
    if auth_mode is None:
        raise JiraToolError(
            "Jira auth mode is not configured.",
            code="jira_not_configured",
            status_code=503,
        )

    auth_mode = auth_mode.strip().lower()
    if auth_mode not in _VALID_AUTH_MODES:
        raise JiraToolError(
            "Jira auth mode must be 'service_account_scoped' or 'basic'.",
            code="jira_invalid_configuration",
            status_code=503,
        )

    api_key = await _resolve_binding_value(
        binding_name="api_key",
        raw_value=cfg.atlassian_api_key,
        secret_ref=cfg.atlassian_api_key_secret_ref,
    )
    if api_key is None:
        raise JiraToolError(
            "Jira API token is not configured.",
            code="jira_not_configured",
            status_code=503,
        )

    if auth_mode == "service_account_scoped":
        cloud_id = await _resolve_binding_value(
            binding_name="cloud_id",
            raw_value=cfg.atlassian_cloud_id,
            secret_ref=cfg.atlassian_cloud_id_secret_ref,
        )
        service_account_email = await _resolve_binding_value(
            binding_name="service_account_email",
            raw_value=cfg.atlassian_service_account_email,
            secret_ref=cfg.atlassian_service_account_email_secret_ref,
        )
        if cloud_id is None or service_account_email is None:
            raise JiraToolError(
                "Service-account Jira mode requires a cloud ID and service-account email.",
                code="jira_invalid_configuration",
                status_code=503,
            )
        base_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3"
        authorization = f"Bearer {api_key}"
    else:
        email = await _resolve_binding_value(
            binding_name="email",
            raw_value=cfg.atlassian_email or cfg.atlassian_username,
            secret_ref=cfg.atlassian_email_secret_ref,
        )
        site_url = await _resolve_binding_value(
            binding_name="site_url",
            raw_value=cfg.atlassian_site_url or cfg.atlassian_url,
            secret_ref=cfg.atlassian_site_url_secret_ref,
        )
        site_url = _normalize_url(site_url)
        if email is None or site_url is None:
            raise JiraToolError(
                "Basic Jira mode requires a site URL and account email.",
                code="jira_invalid_configuration",
                status_code=503,
            )
        basic_token = b64encode(f"{email}:{api_key}".encode("utf-8")).decode("ascii")
        base_url = f"{site_url}/rest/api/3"
        authorization = f"Basic {basic_token}"
        basic_material = f"{email}:{api_key}"
    if auth_mode == "service_account_scoped":
        basic_material = None

    redaction_values = (
        api_key,
        basic_material,
        authorization,
        f"Authorization: {authorization}",
        f"authorization: {authorization}",
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": authorization,
    }
    return ResolvedJiraConnection(
        auth_mode=auth_mode,
        base_url=base_url,
        headers=headers,
        connect_timeout_seconds=float(jira_cfg.jira_connect_timeout_seconds),
        read_timeout_seconds=float(jira_cfg.jira_read_timeout_seconds),
        retry_attempts=int(jira_cfg.jira_retry_attempts),
        redaction_values=tuple(value for value in redaction_values if value),
    )
