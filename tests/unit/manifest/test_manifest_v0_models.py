"""Unit tests for manifest v0 Pydantic models."""

from __future__ import annotations

import json
import textwrap

import pytest

from moonmind.schemas.manifest_v0_models import (
    DataSourceConfig,
    EmbeddingsConfig,
    IndexConfig,
    ManifestMetadata,
    ManifestV0,
    RetrieverConfig,
    RunConfig,
    SecurityConfig,
    VectorStoreConfig,
    export_v0_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(**overrides) -> ManifestV0:
    """Create a minimal valid ManifestV0 with optional overrides."""
    defaults = dict(
        version="v0",
        metadata=ManifestMetadata(name="test"),
        embeddings=EmbeddingsConfig(provider="openai", model="text-embedding-3-large"),
        vectorStore=VectorStoreConfig(type="qdrant", indexName="test_idx"),
        dataSources=[DataSourceConfig(id="ds1", type="SimpleDirectoryReader")],
        indices=[IndexConfig(id="idx1", sources=["ds1"])],
        retrievers=[RetrieverConfig(id="ret1", type="Vector", indices=["idx1"])],
    )
    defaults.update(overrides)
    return ManifestV0(**defaults)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestManifestV0Construction:
    def test_minimal(self):
        m = _make_manifest()
        assert m.version == "v0"
        assert m.metadata.name == "test"

    def test_version_must_be_v0(self):
        with pytest.raises(Exception):
            _make_manifest(version="v1")

    def test_metadata_name_required(self):
        with pytest.raises(Exception):
            ManifestMetadata(name="")

    def test_default_run_config(self):
        rc = RunConfig()
        assert rc.concurrency == 6
        assert rc.batchSize == 128
        assert rc.errorPolicy == "continue"
        assert rc.dryRun is False

    def test_security_config_defaults(self):
        sc = SecurityConfig()
        assert sc.piiRedaction is False
        assert sc.allowlistMetadata == []


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip(self):
        m = _make_manifest()
        data = m.model_dump()
        m2 = ManifestV0.model_validate(data)
        assert m2.metadata.name == m.metadata.name
        assert len(m2.dataSources) == len(m.dataSources)

    def test_json_schema_generation(self):
        schema = ManifestV0.model_json_schema()
        assert "properties" in schema
        assert "ManifestMetadata" in json.dumps(schema)


# ---------------------------------------------------------------------------
# Cross-field validation
# ---------------------------------------------------------------------------


class TestCrossFieldValidation:
    def test_index_references_valid_datasource(self):
        # Should not raise
        _make_manifest()

    def test_index_references_unknown_datasource_raises(self):
        with pytest.raises(ValueError, match="unknown dataSource"):
            _make_manifest(
                indices=[IndexConfig(id="idx1", sources=["nonexistent"])],
            )

    def test_retriever_references_unknown_index_raises(self):
        with pytest.raises(ValueError, match="unknown index"):
            _make_manifest(
                retrievers=[
                    RetrieverConfig(id="ret1", type="Vector", indices=["missing"])
                ],
            )


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


class TestYamlLoading:
    def test_from_yaml_string(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "yaml-test"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "ds1"
                type: "SimpleDirectoryReader"
            indices:
              - id: "idx1"
                sources: ["ds1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
        """)
        m = ManifestV0.from_yaml_string(yaml_str)
        assert m.metadata.name == "yaml-test"

    def test_from_yaml_file(self, tmp_path):
        f = tmp_path / "manifest.yaml"
        f.write_text(textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "file-test"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "ds1"
                type: "SimpleDirectoryReader"
            indices:
              - id: "idx1"
                sources: ["ds1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
        """))
        m = ManifestV0.from_yaml_file(str(f))
        assert m.metadata.name == "file-test"

    def test_from_yaml_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ManifestV0.from_yaml_file("/nonexistent.yaml")


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------


class TestExportSchema:
    def test_export_creates_file(self, tmp_path):
        out = tmp_path / "schema.json"
        export_v0_schema(out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert "properties" in data

    def test_export_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "schema.json"
        export_v0_schema(out)
        assert out.exists()
