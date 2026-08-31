"""Deployment bootstrap models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BootstrapState(StrEnum):
    not_started = "not_started"
    resolving_images = "resolving_images"
    syncing_catalog = "syncing_catalog"
    awaiting_credentials = "awaiting_credentials"
    validating_credentials = "validating_credentials"
    resolving_model = "resolving_model"
    creating_profiles = "creating_profiles"
    qualifying_runtime = "qualifying_runtime"
    publishing_evidence = "publishing_evidence"
    ready = "ready"
    failed = "failed"


class BootstrapDesired(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    provider: str = Field(default="opencode-go")
    model_display_name: str = Field(alias="modelDisplayName", default="Muse Spark 1.2 Contributor")
    model_id: str | None = Field(default=None, alias="modelId")
    effort: str = Field(default="xhigh")
    accept_contributor_data_use: bool = Field(default=False, alias="acceptContributorDataUse")


class BootstrapResolved(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    model_id: str | None = Field(default=None, alias="modelId")
    qualified_model_id: str | None = Field(default=None, alias="qualifiedModelId")
    provider_model_id: str | None = Field(default=None, alias="providerModelId")
    display_name: str | None = Field(default=None, alias="displayName")
    server_image_ref: str | None = Field(default=None, alias="serverImageRef")
    host_image_ref: str | None = Field(default=None, alias="hostImageRef")
    omnigent_build_digest: str | None = Field(default=None, alias="omnigentBuildDigest")
    architecture: str | None = None
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")


class ResolvedOmnigentDeploymentState(BaseModel):
    """Immutable resolved deployment identities for launch authority."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    server_image_ref: str | None = Field(default=None, alias="serverImageRef")
    opencode_host_image_ref: str | None = Field(default=None, alias="opencodeHostImageRef")
    runtime_host_image_ref: str | None = Field(default=None, alias="runtimeHostImageRef")
    pi_host_image_ref: str | None = Field(default=None, alias="piHostImageRef")
    omnigent_build_digest: str | None = Field(default=None, alias="omnigentBuildDigest")
    architecture: str = Field(default="linux/amd64")
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="resolvedAt")
    source: str = Field(default="auto")
    # Additional metadata
    details: dict[str, Any] = Field(default_factory=dict)


class BootstrapRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    bootstrap_id: str = Field(alias="bootstrapId", default="omnigent-opencode-default")
    revision: int = Field(default=1)
    state: BootstrapState = Field(default=BootstrapState.not_started)
    desired: BootstrapDesired = Field(default_factory=BootstrapDesired)
    resolved: BootstrapResolved = Field(default_factory=BootstrapResolved)
    provider_profile_ref: str | None = Field(default=None, alias="providerProfileRef")
    agent_profile_ref: str | None = Field(default=None, alias="agentProfileRef")
    last_evidence_ref: str | None = Field(default=None, alias="lastEvidenceRef")
    failure: dict[str, Any] | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="updatedAt")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
