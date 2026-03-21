"""Contract tests for ReaderAdapter wrappers and extensibility tests.

Tests T018 (adapter contract), T025 (extensibility), T026 (PII),
T027 (metadata allowlist).
"""

from __future__ import annotations

import textwrap
from typing import Any, Dict, Iterator, Tuple

import pytest

from moonmind.manifest.reader_adapter import (
    PlanResult,
    ReaderAdapter,
    _reset_registry,
    get_adapter,
    register_adapter,
    registered_types,
)
from moonmind.manifest.adapters import (
    ConfluenceReaderAdapter,
    GitHubReaderAdapter,
    GoogleDriveReaderAdapter,
    SimpleDirectoryReaderAdapter,
    register_builtin_adapters,
)
from moonmind.manifest.validator import validate_manifest_string
from moonmind.manifest.pipeline import ManifestPipeline
from moonmind.schemas.manifest_v0_models import DataSourceConfig, ManifestV0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


MINIMAL_MANIFEST = textwrap.dedent("""\
    version: "v0"
    metadata:
      name: "adapter-test"
    embeddings:
      provider: "openai"
      model: "text-embedding-3-large"
    vectorStore:
      type: "qdrant"
      indexName: "test"
    dataSources:
      - id: "local"
        type: "SimpleDirectoryReader"
        params:
          inputDir: "{input_dir}"
    indices:
      - id: "idx1"
        type: "VectorStoreIndex"
        sources: ["local"]
    retrievers:
      - id: "ret1"
        type: "Vector"
        indices: ["idx1"]
""")


@pytest.fixture(autouse=True)
def _clean_registry():
    """Re-register built-in adapters for each test."""
    _reset_registry()
    register_builtin_adapters()
    yield
    _reset_registry()


# ---------------------------------------------------------------------------
# T018: Adapter contract tests — verify plan/fetch/state interface
# ---------------------------------------------------------------------------


class TestAdapterContracts:
    """Verify each adapter satisfies the ReaderAdapter protocol."""

    def test_github_adapter_is_reader(self):
        ds = DataSourceConfig(
            id="gh", type="GithubRepositoryReader",
            params={"owner": "test", "repo": "test"}
        )
        adapter = GitHubReaderAdapter(ds)
        assert isinstance(adapter, ReaderAdapter)

    def test_google_drive_adapter_is_reader(self):
        ds = DataSourceConfig(
            id="gd", type="GoogleDriveReader",
            params={"folderId": "abc123"}
        )
        adapter = GoogleDriveReaderAdapter(ds)
        assert isinstance(adapter, ReaderAdapter)

    def test_local_adapter_is_reader(self):
        ds = DataSourceConfig(
            id="loc", type="SimpleDirectoryReader",
            params={"inputDir": "."}
        )
        adapter = SimpleDirectoryReaderAdapter(ds)
        assert isinstance(adapter, ReaderAdapter)

    def test_confluence_adapter_is_reader(self):
        ds = DataSourceConfig(
            id="conf", type="ConfluenceReader",
            params={"spaceKey": "TEST"}
        )
        adapter = ConfluenceReaderAdapter(ds)
        assert isinstance(adapter, ReaderAdapter)

    def test_all_adapters_registered(self):
        types = registered_types()
        assert "GithubRepositoryReader" in types
        assert "GoogleDriveReader" in types
        assert "SimpleDirectoryReader" in types
        assert "ConfluenceReader" in types

    def test_github_plan_returns_plan_result(self):
        ds = DataSourceConfig(
            id="gh", type="GithubRepositoryReader",
            params={"owner": "test", "repo": "test", "branch": "main"}
        )
        adapter = GitHubReaderAdapter(ds)
        plan = adapter.plan()
        assert isinstance(plan, PlanResult)
        assert plan.metadata["owner"] == "test"

    def test_local_plan_counts_files(self, tmp_path):
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.py").write_text("world")
        ds = DataSourceConfig(
            id="loc", type="SimpleDirectoryReader",
            params={"inputDir": str(tmp_path)}
        )
        adapter = SimpleDirectoryReaderAdapter(ds)
        plan = adapter.plan()
        assert plan.estimated_docs == 2
        assert plan.estimated_size_bytes > 0

    def test_local_fetch_yields_docs(self, tmp_path):
        (tmp_path / "file.txt").write_text("content here")
        ds = DataSourceConfig(
            id="loc", type="SimpleDirectoryReader",
            params={"inputDir": str(tmp_path)}
        )
        adapter = SimpleDirectoryReaderAdapter(ds)
        docs = list(adapter.fetch())
        assert len(docs) == 1
        assert docs[0][0] == "content here"
        assert docs[0][1]["source_type"] == "SimpleDirectoryReader"

    def test_local_fetch_respects_extensions(self, tmp_path):
        (tmp_path / "file.md").write_text("markdown")
        (tmp_path / "file.py").write_text("python")
        ds = DataSourceConfig(
            id="loc", type="SimpleDirectoryReader",
            params={"inputDir": str(tmp_path), "requiredExts": [".md"]}
        )
        adapter = SimpleDirectoryReaderAdapter(ds)
        docs = list(adapter.fetch())
        assert len(docs) == 1
        assert "markdown" in docs[0][0]

    def test_local_state_returns_cursor(self, tmp_path):
        ds = DataSourceConfig(
            id="loc", type="SimpleDirectoryReader",
            params={"inputDir": str(tmp_path)}
        )
        adapter = SimpleDirectoryReaderAdapter(ds)
        state = adapter.state()
        assert "inputDir" in state

    def test_github_state_returns_branch(self):
        ds = DataSourceConfig(
            id="gh", type="GithubRepositoryReader",
            params={"owner": "o", "repo": "r", "branch": "dev"}
        )
        adapter = GitHubReaderAdapter(ds)
        state = adapter.state()
        assert state["branch"] == "dev"


# ---------------------------------------------------------------------------
# T025: Extensibility test — register custom adapter
# ---------------------------------------------------------------------------


class CustomTestAdapter:
    """A mock custom adapter to verify extensibility."""

    def __init__(self, ds: DataSourceConfig) -> None:
        self.ds = ds

    def plan(self) -> PlanResult:
        return PlanResult(estimated_docs=42, metadata={"custom": True})

    def fetch(self) -> Iterator[Tuple[str, Dict[str, Any]]]:
        yield ("custom doc text", {"source_type": "CustomReader"})

    def state(self) -> Dict[str, Any]:
        return {"custom_cursor": "v1"}


class TestExtensibility:
    def test_register_custom_adapter(self):
        register_adapter("CustomReader", CustomTestAdapter)
        assert "CustomReader" in registered_types()
        cls = get_adapter("CustomReader")
        assert cls is CustomTestAdapter

    def test_custom_adapter_works_in_manifest(self):
        register_adapter("CustomReader", CustomTestAdapter)
        manifest_yaml = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "custom-test"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "custom1"
                type: "CustomReader"
                params:
                  foo: "bar"
            indices:
              - id: "idx1"
                sources: ["custom1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
        """)
        result = validate_manifest_string(manifest_yaml)
        assert result.valid
        assert result.manifest is not None

        pipeline = ManifestPipeline(result.manifest)
        plan = pipeline.plan()
        assert plan.dry_run
        assert plan.total_docs == 42

    def test_custom_adapter_runs(self):
        register_adapter("CustomReader", CustomTestAdapter)
        manifest = ManifestV0.from_yaml_string(textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "custom-run"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "c1"
                type: "CustomReader"
            indices:
              - id: "idx1"
                sources: ["c1"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
        """))
        pipeline = ManifestPipeline(manifest)
        result = pipeline.run()
        assert result.total_docs == 1
        assert result.sources[0].doc_count == 1


# ---------------------------------------------------------------------------
# T026: PII redaction enforcement
# ---------------------------------------------------------------------------


class TestPiiRedaction:
    def test_pii_redaction_flag_accepted(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "pii-test"
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
            security:
              piiRedaction: true
              allowlistMetadata: ["source", "title"]
        """)
        result = validate_manifest_string(yaml_str)
        assert result.valid
        assert result.manifest is not None
        assert result.manifest.security.piiRedaction is True
        assert result.manifest.security.allowlistMetadata == ["source", "title"]

    def test_security_defaults(self):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "no-security"
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
        result = validate_manifest_string(yaml_str)
        assert result.valid
        assert result.manifest.security is None


# ---------------------------------------------------------------------------
# Pipeline integration with local adapter
# ---------------------------------------------------------------------------


class TestPipelineLocalAdapter:
    def test_plan_with_local_files(self, tmp_path):
        (tmp_path / "doc.md").write_text("# Hello")
        manifest = ManifestV0.from_yaml_string(
            MINIMAL_MANIFEST.format(input_dir=str(tmp_path))
        )
        pipeline = ManifestPipeline(manifest)
        plan = pipeline.plan()
        assert plan.dry_run
        assert plan.total_docs == 1

    def test_run_with_local_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("alpha")
        (tmp_path / "b.txt").write_text("beta")
        manifest = ManifestV0.from_yaml_string(
            MINIMAL_MANIFEST.format(input_dir=str(tmp_path))
        )
        pipeline = ManifestPipeline(manifest)
        result = pipeline.run()
        assert not result.dry_run
        assert result.total_docs == 2
        assert result.sources[0].doc_count == 2

    def test_run_unknown_adapter_continues(self, tmp_path):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "unknown-test"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "bad"
                type: "NonExistentReader"
            indices:
              - id: "idx1"
                sources: ["bad"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
        """)
        manifest = ManifestV0.from_yaml_string(yaml_str)
        pipeline = ManifestPipeline(manifest)
        result = pipeline.run()
        assert result.sources[0].error is not None
        assert "No adapter" in result.sources[0].error

    def test_run_stop_on_first_error(self, tmp_path):
        yaml_str = textwrap.dedent("""\
            version: "v0"
            metadata:
              name: "stop-test"
            embeddings:
              provider: "openai"
              model: "text-embedding-3-large"
            vectorStore:
              type: "qdrant"
              indexName: "test"
            dataSources:
              - id: "bad"
                type: "NonExistentReader"
              - id: "good"
                type: "SimpleDirectoryReader"
                params:
                  inputDir: "{input_dir}"
            indices:
              - id: "idx1"
                sources: ["bad", "good"]
            retrievers:
              - id: "ret1"
                type: "Vector"
                indices: ["idx1"]
            run:
              errorPolicy: "stopOnFirstError"
        """.format(input_dir=str(tmp_path)))
        manifest = ManifestV0.from_yaml_string(yaml_str)
        pipeline = ManifestPipeline(manifest)
        result = pipeline.run()
        # Should stop after first error, not process "good"
        assert len(result.sources) == 1
        assert result.sources[0].error is not None
