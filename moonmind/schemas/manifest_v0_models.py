"""Manifest v0 Pydantic models.

These models represent the v0 manifest schema described in
``docs/RAG/LlamaIndexManifestSystem.md``.  The legacy ``apiVersion/kind/spec``
format is preserved in the existing ``Manifest`` class for backward
compatibility; v0 manifests are loaded via :class:`ManifestV0`.
"""

from __future__ import annotations

import json
from os import PathLike
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ManifestMetadata(BaseModel):
    """Manifest identity and ownership."""

    name: str = Field(..., min_length=1)
    description: str = ""
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

class LLMConfig(BaseModel):
    """Optional LLM provider for answer generation."""

    provider: str
    model: str
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)

class DataSourceAuth(BaseModel):
    """Secret references for a data source (``${ENV}`` interpolated)."""

    model_config = {"extra": "allow"}

class DataSourceConfig(BaseModel):
    """A single data source reader definition."""

    id: str
    type: str  # GithubRepositoryReader, GoogleDriveReader, etc.
    params: Dict[str, Any] = Field(default_factory=dict)
    auth: Optional[DataSourceAuth] = None
    schedule: Optional[str] = None

class SplitterConfig(BaseModel):
    """Text splitter / chunker settings."""

    type: str = "TokenTextSplitter"
    chunkSize: int = Field(default=1000, ge=1)
    chunkOverlap: int = Field(default=100, ge=0)

class TransformsConfig(BaseModel):
    """Document transforms applied before compilation."""

    htmlToText: bool = False
    splitter: Optional[SplitterConfig] = None
    enrichMetadata: List[Dict[str, Any]] = Field(default_factory=list)

class EvaluationDataset(BaseModel):
    """A golden dataset for retrieval evaluation."""

    name: str
    path: str

class EvaluationMetric(BaseModel):
    """A retrieval metric with optional threshold gate."""

    name: str
    threshold: Optional[float] = None

class EvaluationConfig(BaseModel):
    """Evaluation settings."""

    datasets: List[EvaluationDataset] = Field(default_factory=list)
    metrics: List[EvaluationMetric] = Field(default_factory=list)

class RunConfig(BaseModel):
    """Execution parameters."""

    concurrency: int = Field(default=6, ge=1)
    batchSize: int = Field(default=128, ge=1)
    errorPolicy: str = "continue"  # continue | stopOnFirstError
    dryRun: bool = False

class SecurityConfig(BaseModel):
    """Security and compliance settings."""

    piiRedaction: bool = False
    allowlistMetadata: List[str] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# Top-level v0 manifest
# ---------------------------------------------------------------------------

class ManifestV0(BaseModel):
    """Top-level v0 manifest document (vector-free).

    MoonLadderStudios/MoonMind#4108 retired the managed-vector pipeline:
    ``embeddings``, ``vectorStore``, ``indices`` and ``retrievers`` are no
    longer part of the product contract and are absent from the generated
    schema. Historical manifests carrying those keys remain readable because
    unknown fields are ignored (``extra="allow"``); new submissions carrying
    them are rejected by validator/queue/pipeline gates before any
    fetch/dispatch/side effect.
    """

    model_config = {"extra": "allow"}

    version: Literal["v0"] = "v0"
    metadata: ManifestMetadata
    llm: Optional[LLMConfig] = None
    dataSources: List[DataSourceConfig] = Field(min_length=1)
    transforms: Optional[TransformsConfig] = None
    postprocessors: List[Dict[str, Any]] = Field(default_factory=list)
    evaluation: Optional[EvaluationConfig] = None
    run: Optional[RunConfig] = None
    observability: Optional[Dict[str, Any]] = None
    security: Optional[SecurityConfig] = None
    scheduling: Optional[str] = None

    # ------------------------------------------------------------------
    # Convenience loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml_file(cls, path: PathLike | str) -> "ManifestV0":
        """Load and validate a v0 manifest from a YAML file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Manifest file not found: {p}")
        content = p.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        return cls.model_validate(parsed)

    @classmethod
    def from_yaml_string(cls, content: str) -> "ManifestV0":
        """Load and validate a v0 manifest from a YAML string."""
        parsed = yaml.safe_load(content)
        return cls.model_validate(parsed)

def export_v0_schema(path: PathLike) -> None:
    """Write the v0 JSON Schema to *path*."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    schema = ManifestV0.model_json_schema()
    p.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
