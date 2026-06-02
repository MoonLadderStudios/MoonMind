from __future__ import annotations

import json
import textwrap

import pytest

from moonmind.manifest.evaluation import evaluate_manifest
from moonmind.manifest.pipeline import ManifestPipeline
from moonmind.schemas.manifest_v0_models import ManifestV0

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_mm754_manifest_pipeline_local_source_e2e_with_evaluation(tmp_path):
    source_dir = tmp_path / "docs"
    source_dir.mkdir()
    rag_doc = source_dir / "rag.md"
    rag_doc.write_text("Workflow RAG assembles context packs.", encoding="utf-8")
    source_dir.joinpath("jira.md").write_text(
        "Jira issues track implementation scope.", encoding="utf-8"
    )
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text(
        json.dumps({"query": "context packs", "relevant_ids": [str(rag_doc)]})
        + "\n",
        encoding="utf-8",
    )
    manifest = ManifestV0.from_yaml_string(
        textwrap.dedent(
            f"""\
            version: "v0"
            metadata:
              name: "mm754-local-e2e"
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
                  inputDir: "{source_dir}"
                  requiredExts: [".md"]
            indices:
              - id: "idx"
                sources: ["local"]
            retrievers:
              - id: "ret"
                type: "Vector"
                indices: ["idx"]
                params:
                  topK: 1
            evaluation:
              datasets:
                - name: "smoke"
                  path: "{dataset}"
              metrics:
                - name: "hitRate@1"
                  threshold: 1.0
            """
        )
    )

    run = ManifestPipeline(manifest).run()
    fetched_sources = {source.source_id: source for source in run.sources}
    assert run.total_docs == 2
    assert fetched_sources["local"].state["files"]["rag.md"]["sha256"]

    eval_result = evaluate_manifest(
        manifest,
        retriever=lambda _query, _top_k: [str(rag_doc)],
    )

    assert eval_result["passed"] is True
    assert eval_result["datasets"][0]["metrics"][0]["score"] == 1.0
