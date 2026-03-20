"""Unit tests for manifest v0 validator."""

from __future__ import annotations

import textwrap

import pytest

from moonmind.manifest.validator import (
    ValidationResult,
    validate_manifest_file,
    validate_manifest_string,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID = textwrap.dedent("""\
    version: "v0"
    metadata:
      name: "test-manifest"
      description: "Unit test manifest"
    embeddings:
      provider: "openai"
      model: "text-embedding-3-large"
    vectorStore:
      type: "qdrant"
      indexName: "test_idx"
      connection:
        host: "localhost"
    dataSources:
      - id: "src1"
        type: "SimpleDirectoryReader"
        params:
          inputDir: "./data"
    indices:
      - id: "idx1"
        type: "VectorStoreIndex"
        sources: ["src1"]
    retrievers:
      - id: "ret1"
        type: "Vector"
        indices: ["idx1"]
""")


def _result(yaml_str: str) -> ValidationResult:
    return validate_manifest_string(yaml_str)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestValidManifest:
    def test_minimal_valid(self):
        r = _result(MINIMAL_VALID)
        assert r.valid
        assert r.manifest is not None
        assert r.manifest.version == "v0"
        assert r.manifest.metadata.name == "test-manifest"
        assert len(r.errors) == 0

    def test_summary_valid(self):
        r = _result(MINIMAL_VALID)
        assert r.summary().startswith("✓")


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_missing_required_field_embeddings(self):
        bad = MINIMAL_VALID.replace("embeddings:", "# embeddings:")
        # Remove the two lines after embeddings too
        bad = bad.replace('  provider: "openai"\n', "")
        bad = bad.replace('  model: "text-embedding-3-large"\n', "")
        r = _result(bad)
        assert not r.valid
        assert any("embeddings" in e.field for e in r.errors)

    def test_missing_required_field_metadata(self):
        bad = MINIMAL_VALID.replace("metadata:", "# metadata:")
        bad = bad.replace('  name: "test-manifest"\n', "")
        bad = bad.replace('  description: "Unit test manifest"\n', "")
        r = _result(bad)
        assert not r.valid

    def test_wrong_version(self):
        bad = MINIMAL_VALID.replace('version: "v0"', 'version: "v99"')
        r = _result(bad)
        assert not r.valid

    def test_empty_data_sources(self):
        bad = MINIMAL_VALID.replace(
            'dataSources:\n  - id: "src1"\n    type: "SimpleDirectoryReader"\n    params:\n      inputDir: "./data"',
            "dataSources: []",
        )
        r = _result(bad)
        assert not r.valid


# ---------------------------------------------------------------------------
# Cross-field reference validation
# ---------------------------------------------------------------------------


class TestReferenceValidation:
    def test_retriever_references_unknown_index(self):
        bad = MINIMAL_VALID.replace('indices: ["idx1"]', 'indices: ["nonexistent"]')
        r = _result(bad)
        assert not r.valid
        assert any("nonexistent" in e.message for e in r.errors)

    def test_index_references_unknown_datasource(self):
        bad = MINIMAL_VALID.replace('sources: ["src1"]', 'sources: ["missing_ds"]')
        r = _result(bad)
        assert not r.valid
        assert any("missing_ds" in e.message for e in r.errors)


# ---------------------------------------------------------------------------
# Secret leak detection
# ---------------------------------------------------------------------------


class TestSecretDetection:
    def test_github_pat_rejected(self):
        bad = MINIMAL_VALID.replace(
            "SimpleDirectoryReader",
            "GithubRepositoryReader",
        ).replace(
            'params:\n      inputDir: "./data"',
            'params:\n      owner: "test"\n    auth:\n      githubToken: "ghp_aaaBBBcccDDDeeeFF1234567890abcdefghij1234"',
        )
        r = _result(bad)
        assert not r.valid
        assert any("secret" in e.message.lower() for e in r.errors)

    def test_env_ref_accepted(self):
        yaml_str = MINIMAL_VALID.replace(
            "SimpleDirectoryReader",
            "GithubRepositoryReader",
        ).replace(
            'params:\n      inputDir: "./data"',
            'params:\n      owner: "test"\n    auth:\n      githubToken: "${GITHUB_TOKEN}"',
        )
        r = _result(yaml_str)
        # Should not have secret-related errors
        assert not any("secret" in e.message.lower() for e in r.errors)

    def test_openai_key_rejected(self):
        bad = MINIMAL_VALID.replace(
            'host: "localhost"',
            'apiKey: "sk-abcdefghij1234567890abcdefghij1234567890"',
        )
        r = _result(bad)
        # The key pattern should be detected somewhere
        assert any("secret" in i.message.lower() for i in r.issues)


# ---------------------------------------------------------------------------
# Auth warnings
# ---------------------------------------------------------------------------


class TestAuthWarnings:
    def test_github_reader_without_auth_warns(self):
        yaml_str = MINIMAL_VALID.replace(
            "SimpleDirectoryReader",
            "GithubRepositoryReader",
        ).replace(
            'params:\n      inputDir: "./data"',
            'params:\n      owner: "test"',
        )
        r = _result(yaml_str)
        assert any("auth" in w.field for w in r.warnings)

    def test_simple_reader_without_auth_no_warning(self):
        r = _result(MINIMAL_VALID)
        assert len(r.warnings) == 0


# ---------------------------------------------------------------------------
# ID uniqueness
# ---------------------------------------------------------------------------


class TestIdUniqueness:
    def test_duplicate_datasource_id(self):
        yaml_str = MINIMAL_VALID.replace(
            "dataSources:\n"
            '  - id: "src1"\n'
            '    type: "SimpleDirectoryReader"\n'
            "    params:\n"
            '      inputDir: "./data"',
            "dataSources:\n"
            '  - id: "src1"\n'
            '    type: "SimpleDirectoryReader"\n'
            "    params:\n"
            '      inputDir: "./data"\n'
            '  - id: "src1"\n'
            '    type: "SimpleDirectoryReader"\n'
            "    params:\n"
            '      inputDir: "./data2"',
        ).replace(
            'sources: ["src1"]',
            'sources: ["src1"]',
        )
        r = _result(yaml_str)
        assert not r.valid
        assert any("Duplicate" in e.message for e in r.errors)


# ---------------------------------------------------------------------------
# File-based validation
# ---------------------------------------------------------------------------


class TestFileValidation:
    def test_file_not_found(self):
        r = validate_manifest_file("/nonexistent/path/to/manifest.yaml")
        assert not r.valid
        assert any("not found" in e.message for e in r.errors)

    def test_valid_file(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text(MINIMAL_VALID)
        r = validate_manifest_file(str(f))
        assert r.valid


# ---------------------------------------------------------------------------
# YAML parse errors
# ---------------------------------------------------------------------------


class TestYamlErrors:
    def test_invalid_yaml(self):
        r = _result("{{invalid yaml content")
        assert not r.valid

    def test_yaml_list_not_mapping(self):
        r = _result("- item1\n- item2\n")
        assert not r.valid
        assert any("mapping" in e.message for e in r.errors)


# ---------------------------------------------------------------------------
# T026: PII redaction enforcement
# ---------------------------------------------------------------------------


class TestPiiRedactionEnforcement:
    def test_pii_enabled_without_splitter_warns(self):
        """PII redaction enabled but no splitter → WARNING."""
        yaml_str = MINIMAL_VALID.rstrip() + "\nsecurity:\n  piiRedaction: true\n"
        r = _result(yaml_str)
        assert r.valid  # warnings don't fail
        assert any(
            "piiRedaction" in w.field and "splitter" in w.message
            for w in r.warnings
        )

    def test_pii_enabled_with_splitter_no_warning(self):
        """PII redaction enabled WITH splitter → no warning."""
        yaml_str = (
            MINIMAL_VALID.rstrip()
            + "\ntransforms:\n  splitter:\n    type: TokenTextSplitter\n    chunkSize: 500\n"
            + "security:\n  piiRedaction: true\n"
        )
        r = _result(yaml_str)
        assert r.valid
        assert not any("piiRedaction" in w.field for w in r.warnings)

    def test_pii_disabled_no_warning(self):
        """PII redaction disabled → no warning regardless of splitter."""
        yaml_str = MINIMAL_VALID.rstrip() + "\nsecurity:\n  piiRedaction: false\n"
        r = _result(yaml_str)
        assert r.valid
        assert not any("piiRedaction" in w.field for w in r.warnings)


# ---------------------------------------------------------------------------
# T027: Metadata allowlist enforcement
# ---------------------------------------------------------------------------


class TestMetadataAllowlistEnforcement:
    def test_extra_metadata_not_in_allowlist_errors(self):
        """Extra metadata key not in allowlist → ERROR."""
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "test-manifest"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test_idx"
            dataSources:
              - id: "src1"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "./data"
                  extraMetadata:
                    forbidden_key: true
            indices:
              - id: "idx1"
                type: "VectorStoreIndex"
                sources: ["src1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
            security:
              allowlistMetadata:
                - allowed_key
        """)
        r = _result(yaml_str)
        assert not r.valid
        assert any("forbidden_key" in e.message for e in r.errors)

    def test_extra_metadata_in_allowlist_passes(self):
        """Extra metadata key in allowlist → no error."""
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "test-manifest"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test_idx"
            dataSources:
              - id: "src1"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "./data"
                  extraMetadata:
                    allowed_key: true
            indices:
              - id: "idx1"
                type: "VectorStoreIndex"
                sources: ["src1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
            security:
              allowlistMetadata:
                - allowed_key
        """)
        r = _result(yaml_str)
        assert r.valid

    def test_no_extra_metadata_with_allowlist_passes(self):
        """Allowlist set but no extra metadata → no error."""
        yaml_str = (
            MINIMAL_VALID.rstrip()
            + "\nsecurity:\n  allowlistMetadata:\n    - safe_key\n"
        )
        r = _result(yaml_str)
        assert r.valid


# ---------------------------------------------------------------------------
# T010: CI example YAML validation
# ---------------------------------------------------------------------------


class TestCIExampleValidation:
    """Validate all example manifest YAML files (CI gate)."""

    def test_all_example_yamls_validate(self):
        """Every YAML in examples/ must pass v0 schema validation."""
        from pathlib import Path

        examples_dir = Path(__file__).resolve().parents[3] / "examples"
        yaml_files = list(examples_dir.glob("*.yaml")) + list(
            examples_dir.glob("*.yml")
        )

        assert len(yaml_files) > 0, f"No example YAMLs found in {examples_dir}"

        failures = []
        for f in yaml_files:
            r = validate_manifest_file(str(f))
            if not r.valid:
                failures.append(f"{f.name}: {r.summary()}")

        assert not failures, (
            f"{len(failures)} example YAML(s) failed validation:\n"
            + "\n".join(failures)
        )

