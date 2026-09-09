"""Unit tests for manifest v0 validator (vector-free, #4108)."""

from __future__ import annotations

import textwrap

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
    dataSources:
      - id: "src1"
        type: "SimpleDirectoryReader"
        params:
          inputDir: "./data"
""")

RETIRED_VECTOR_MANIFEST = textwrap.dedent("""\
    version: "v0"
    metadata:
      name: "retired-vector"
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
    indices:
      - id: "idx1"
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
# Retired vector contract (#4108)
# ---------------------------------------------------------------------------

class TestRetiredVectorRejection:
    def test_retired_vector_manifest_rejected_before_fetch(self):
        r = _result(RETIRED_VECTOR_MANIFEST)
        assert not r.valid
        assert any(e.field in {"embeddings", "vectorStore", "indices", "retrievers"} for e in r.errors)
        assert any("4108" in e.message for e in r.errors)

    def test_single_retired_field_rejected(self):
        bad = MINIMAL_VALID.rstrip() + '\nembeddings:\n  provider: "openai"\n  model: "x"\n'
        r = _result(bad)
        assert not r.valid
        assert any(e.field == "embeddings" for e in r.errors)

    def test_normalized_retired_spellings_rejected(self):
        # Case/whitespace variants reject like the canonical spellings.
        bad = (
            MINIMAL_VALID.rstrip()
            + '\nvectorstore:\n  type: "qdrant"\n  indexName: "old"\n'
        )
        r = _result(bad)
        assert not r.valid
        assert any("4108" in e.message for e in r.errors)

        bad_padded = (
            MINIMAL_VALID.rstrip()
            + '\n" VectorStore ":\n  type: "qdrant"\n  indexName: "old"\n'
        )
        r = _result(bad_padded)
        assert not r.valid
        assert any("4108" in e.message for e in r.errors)

    def test_historical_model_still_readable_without_validator(self):
        # Historical artifacts remain readable via ManifestV0 directly.
        from moonmind.schemas.manifest_v0_models import ManifestV0

        m = ManifestV0.model_validate(
            {
                "version": "v0",
                "metadata": {"name": "hist"},
                "dataSources": [{"id": "ds1", "type": "SimpleDirectoryReader"}],
                "vectorStore": {"type": "qdrant", "indexName": "old"},
            }
        )
        assert m.metadata.name == "hist"

# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestSchemaValidation:
    def test_vector_free_manifest_valid_4113(self):
        vector_free = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "vector-free-manifest"
              description: "No managed-vector configuration"
            dataSources:
              - id: "src1"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "./data"
            transforms:
              splitter:
                type: "TokenTextSplitter"
                chunkSize: 1000
                chunkOverlap: 100
            run:
              concurrency: 4
        """)
        r = _result(vector_free)
        assert r.valid, r.summary()
        assert r.manifest is not None

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
# Secret leak detection
# ---------------------------------------------------------------------------

class TestSecretDetection:
    def test_github_pat_rejected(self):
        bad = MINIMAL_VALID.replace(
            "SimpleDirectoryReader",
            "GithubRepositoryReader",
        ).replace(
            'params:\n      inputDir: "./data"',
            'params:\n      owner: "test"\n    auth:\n      githubToken: "ghp_FAKE_TEST_VALUE_DO_NOT_USE_00000000000"',
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
        assert not any("secret" in e.message.lower() for e in r.errors)

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
        yaml_str = MINIMAL_VALID.rstrip() + "\nsecurity:\n  piiRedaction: true\n"
        r = _result(yaml_str)
        assert r.valid
        assert any(
            "piiRedaction" in w.field and "splitter" in w.message
            for w in r.warnings
        )

    def test_pii_enabled_with_splitter_no_warning(self):
        yaml_str = (
            MINIMAL_VALID.rstrip()
            + "\ntransforms:\n  splitter:\n    type: TokenTextSplitter\n    chunkSize: 500\n"
            + "security:\n  piiRedaction: true\n"
        )
        r = _result(yaml_str)
        assert r.valid
        assert not any("piiRedaction" in w.field for w in r.warnings)

    def test_pii_disabled_no_warning(self):
        yaml_str = MINIMAL_VALID.rstrip() + "\nsecurity:\n  piiRedaction: false\n"
        r = _result(yaml_str)
        assert r.valid
        assert not any("piiRedaction" in w.field for w in r.warnings)

# ---------------------------------------------------------------------------
# T027: Metadata allowlist enforcement
# ---------------------------------------------------------------------------

class TestMetadataAllowlistEnforcement:
    def test_extra_metadata_not_in_allowlist_errors(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "test-manifest"
            dataSources:
              - id: "src1"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "./data"
                  extraMetadata:
                    forbidden_key: true
            security:
              allowlistMetadata:
                - allowed_key
        """)
        r = _result(yaml_str)
        assert not r.valid
        assert any("forbidden_key" in e.message for e in r.errors)

    def test_extra_metadata_in_allowlist_passes(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "test-manifest"
            dataSources:
              - id: "src1"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "./data"
                  extraMetadata:
                    allowed_key: true
            security:
              allowlistMetadata:
                - allowed_key
        """)
        r = _result(yaml_str)
        assert r.valid

    def test_no_extra_metadata_with_allowlist_passes(self):
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
    def test_all_example_yamls_validate(self):
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
