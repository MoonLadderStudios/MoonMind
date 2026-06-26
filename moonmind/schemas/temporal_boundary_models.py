"""Typed inventory models for public Temporal boundary contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TemporalBoundaryKind = Literal[
    "workflow",
    "activity",
    "signal",
    "update",
    "query",
    "continue_as_new",
]
TemporalBoundaryModelRole = Literal[
    "request",
    "response",
    "snapshot",
    "continuation",
    "metadata",
]
TemporalBoundaryStatus = Literal[
    "modeled",
    "compatibility_shim",
    "tracking_only",
]

def _require_non_blank(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized

class TemporalBoundaryModelRef(BaseModel):
    """Reference to a named model that owns a Temporal wire shape."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    module: str = Field(..., alias="module")
    name: str = Field(..., alias="name")
    role: TemporalBoundaryModelRole = Field(..., alias="role")
    schema_home: str = Field(..., alias="schemaHome")

    @model_validator(mode="after")
    def _normalize(self) -> "TemporalBoundaryModelRef":
        self.module = _require_non_blank(self.module, field_name="module")
        self.name = _require_non_blank(self.name, field_name="name")
        self.schema_home = _require_non_blank(
            self.schema_home,
            field_name="schemaHome",
        )
        return self

class TemporalBoundaryContract(BaseModel):
    """One public Temporal boundary and its named typed contract ownership."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: TemporalBoundaryKind = Field(..., alias="kind")
    name: str = Field(..., alias="name")
    owner: str = Field(..., alias="owner")
    request_model: TemporalBoundaryModelRef = Field(..., alias="requestModel")
    response_model: TemporalBoundaryModelRef | None = Field(
        default=None,
        alias="responseModel",
    )
    status: TemporalBoundaryStatus = Field(..., alias="status")
    schema_home: str = Field(..., alias="schemaHome")
    coverage_ids: tuple[str, ...] = Field(..., alias="coverageIds", min_length=1)
    rationale: str | None = Field(default=None, alias="rationale")
    metadata_fields: tuple[str, ...] = Field(default=(), alias="metadataFields")

    @model_validator(mode="after")
    def _normalize(self) -> "TemporalBoundaryContract":
        self.name = _require_non_blank(self.name, field_name="name")
        self.owner = _require_non_blank(self.owner, field_name="owner")
        self.schema_home = _require_non_blank(
            self.schema_home,
            field_name="schemaHome",
        )
        self.coverage_ids = tuple(
            _require_non_blank(item, field_name="coverageIds")
            for item in self.coverage_ids
        )
        self.metadata_fields = tuple(
            _require_non_blank(item, field_name="metadataFields")
            for item in self.metadata_fields
        )
        if self.rationale is not None:
            self.rationale = _require_non_blank(
                self.rationale,
                field_name="rationale",
            )
        if self.status != "modeled" and not self.rationale:
            raise ValueError("rationale is required for non-modeled boundaries")
        if self.response_model is None and not self.rationale:
            raise ValueError("rationale is required when responseModel is omitted")
        return self

class TemporalBoundaryInventory(BaseModel):
    """Deterministic collection of Temporal boundary contracts for review gates."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_issue_key: str = Field(..., alias="sourceIssueKey")
    board_scope: str = Field(..., alias="boardScope")
    contracts: tuple[TemporalBoundaryContract, ...] = Field(
        ...,
        alias="contracts",
        min_length=1,
    )

    @model_validator(mode="after")
    def _normalize(self) -> "TemporalBoundaryInventory":
        self.source_issue_key = _require_non_blank(
            self.source_issue_key,
            field_name="sourceIssueKey",
        )
        self.board_scope = _require_non_blank(
            self.board_scope,
            field_name="boardScope",
        )
        seen: set[tuple[str, str, str]] = set()
        for contract in self.contracts:
            key = (contract.kind, contract.name, contract.owner)
            if key in seen:
                raise ValueError(
                    "duplicate boundary contract "
                    f"{contract.kind}:{contract.owner}:{contract.name}"
                )
            seen.add(key)
        return self

__all__ = [
    "TemporalBoundaryContract",
    "TemporalBoundaryInventory",
    "TemporalBoundaryKind",
    "TemporalBoundaryModelRef",
    "TemporalBoundaryModelRole",
    "TemporalBoundaryStatus",
]
