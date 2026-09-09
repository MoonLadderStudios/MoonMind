"""Unit tests for manifest v0 Pydantic models (vector-free, #4108)."""

from __future__ import annotations

import json
import textwrap

import pytest

from moonmind.schemas.manifest_v0_models import (
    DataSourceConfig,
    ManifestMetadata,
    ManifestV0,
    RunConfig,
    SecurityConfig,
    export_v0_schema,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(**overrides) -> ManifestV0:
    """Create a minimal valid vector-free ManifestV0 with optional overrides."""
    defaults = dict(
        version="v0",
        metadata=ManifestMetadata(name="test"),
        dataSources=[DataSourceConfig(id="ds1", type="SimpleDirectoryReader")],
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

    def test_vector_fields_absent_from_model(self):
        m = _make_manifest()
        for retired in ("embeddings", "vectorStore", "indices", "retrievers"):
            assert not hasattr(m, retired), f"retired field {retired} must be removed"

    def test_historical_vector_keys_ignored_not_advertised(self):
        # Historical manifests remain readable via extra="allow" without Qdrant.
        m = ManifestV0.model_validate(
            {
                "version": "v0",
                "metadata": {"name": "hist"},
                "dataSources": [{"id": "ds1", "type": "SimpleDirectoryReader"}],
                "embeddings": {"provider": "openai", "model": "x"},
                "vectorStore": {"type": "qdrant", "indexName": "old"},
                "indices": [{"id": "idx1", "sources": ["ds1"]}],
                "retrievers": [{"id": "ret1", "type": "Vector", "indices": ["idx1"]}],
            }
        )
        assert m.metadata.name == "hist"
        assert len(m.dataSources) == 1

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

    def test_json_schema_has_no_vector_contract(self):
        schema = ManifestV0.model_json_schema()
        props = schema.get("properties", {})
        for retired in ("embeddings", "vectorStore", "indices", "retrievers"):
            assert retired not in props, f"schema must not advertise {retired}"
        blob = json.dumps(schema)
        assert "VectorStoreIndex" not in blob
        assert "qdrant" not in blob.lower()

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

class TestYamlLoading:
    def test_from_yaml_string(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "yaml-test"
            dataSources:
              - id: "ds1"
                type: "SimpleDirectoryReader"
        """)
        m = ManifestV0.from_yaml_string(yaml_str)
        assert m.metadata.name == "yaml-test"

    def test_from_yaml_file(self, tmp_path):
        f = tmp_path / "manifest.yaml"
        f.write_text(textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "file-test"
            dataSources:
              - id: "ds1"
                type: "SimpleDirectoryReader"
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
