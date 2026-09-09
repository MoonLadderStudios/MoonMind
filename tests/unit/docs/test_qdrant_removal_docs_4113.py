"""Vector-free documentation contract (MoonLadderStudios/MoonMind#4113).

Active first-party surfaces must not recommend or assume a MoonMind-managed
vector database. Narrow retired/historical mentions are allowed only with an
explicit non-active-support framing. Behavior/code removal stays with the
sibling execution children of #4103; this module guards prose, navigation,
examples, and UI help.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

README = REPO_ROOT / "README.md"
ARCHITECTURE = REPO_ROOT / "docs" / "MoonMindArchitecture.md"
ROADMAP = REPO_ROOT / "docs" / "MoonMindRoadmap.md"
MEMORY_ARCH = REPO_ROOT / "docs" / "Memory" / "MemoryArchitecture.md"
MEMORY_RESEARCH = REPO_ROOT / "docs" / "Memory" / "MemoryResearch.md"
WORKFLOW_RAG = REPO_ROOT / "docs" / "Rag" / "WorkflowRag.md"
MANIFEST_SYSTEM = REPO_ROOT / "docs" / "Rag" / "LlamaIndexManifestSystem.md"
MANIFEST_DESIGN = REPO_ROOT / "docs" / "Rag" / "ManifestIngestDesign.md"
CREATE_PAGE = REPO_ROOT / "docs" / "UI" / "CreatePage.md"
POLICY_AUTHORITY = REPO_ROOT / "docs" / "Omnigent" / "PolicyAuthority.md"
WORKER_VECTOR_DOC = REPO_ROOT / "docs" / "ManagedAgents" / "WorkerVectorEmbedding.md"
AGENT_GUIDANCE = REPO_ROOT / "memory" / "omnigent-3514-followup-retrieval-authoring.md"
WORKFLOW_START_TSX = REPO_ROOT / "frontend" / "src" / "entrypoints" / "workflow-start.tsx"
SMOKE_JSONL = REPO_ROOT / "examples" / "eval" / "smoke.jsonl"
RESIDUAL_REPORT = REPO_ROOT / "docs" / "tmp" / "QdrantDocsResidual-4113.md"

# Docs with zero tolerance: no case-insensitive `qdrant` at all.
ZERO_TOLERANCE_DOCS = [
    README,
    ARCHITECTURE,
    ROADMAP,
    MEMORY_ARCH,
    MANIFEST_DESIGN,
    CREATE_PAGE,
    POLICY_AUTHORITY,
]

# Docs where narrow retired/historical mentions are allowed, but only when the
# file carries explicit non-active-support framing.
FRAMED_DOCS = {
    MEMORY_RESEARCH: "Historical research note",
    WORKFLOW_RAG: "retired",
    MANIFEST_SYSTEM: "#4113",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_retains_minio_without_vector_service() -> None:
    text = _read(README)
    assert "qdrant" not in text.lower()
    assert "| **MinIO** |" in text
    assert "vector-free" in text
    assert "no MoonMind-managed vector service" in text


def test_obsolete_vector_only_doc_is_deleted() -> None:
    assert not WORKER_VECTOR_DOC.exists()


def test_zero_tolerance_docs_have_no_qdrant() -> None:
    offenders = {
        str(path.relative_to(REPO_ROOT)): [
            line.strip()
            for line in _read(path).splitlines()
            if "qdrant" in line.lower()
        ]
        for path in ZERO_TOLERANCE_DOCS
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, f"Active docs still reference Qdrant: {offenders}"


def test_framed_docs_mark_qdrant_as_non_active() -> None:
    for path, marker in FRAMED_DOCS.items():
        text = _read(path)
        assert "qdrant" in text.lower(), f"{path.name} lost its retired framing?"
        assert marker in text, f"{path.name} mentions Qdrant without framing {marker!r}"


def test_architecture_boundary_is_vector_free() -> None:
    for path in (ARCHITECTURE, ROADMAP, MEMORY_ARCH, WORKFLOW_RAG):
        text = _read(path)
        assert "vector-free" in text, f"{path.name} lacks the vector-free boundary"


def test_no_alternate_vector_product_replaces_qdrant() -> None:
    checked = [README, ARCHITECTURE, ROADMAP, MEMORY_ARCH, WORKFLOW_RAG, MANIFEST_DESIGN]
    offenders = {}
    for path in checked:
        hits = [
            line.strip()
            for line in _read(path).splitlines()
            if re.search(r"pgvector|milvus|vectorStore", line, re.IGNORECASE)
        ]
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    assert not offenders, f"Alternate vector product in active docs: {offenders}"


def test_examples_require_no_vector_configuration() -> None:
    yamls = sorted((REPO_ROOT / "examples").glob("*.yaml"))
    assert yamls, "expected example manifests"
    offenders = {}
    for path in yamls:
        text = _read(path)
        hits = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"qdrant|vectorStore|embeddings\s*:|QDRANT_", line, re.IGNORECASE)
        ]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"Examples still carry vector configuration: {offenders}"


def test_eval_baseline_has_no_live_vector_rows() -> None:
    rows = [json.loads(line) for line in _read(SMOKE_JSONL).splitlines() if line.strip()]
    assert rows, "smoke dataset must not be empty"
    blob = _read(SMOKE_JSONL)
    assert "qdrant" not in blob.lower()
    for row in rows:
        assert row.get("relevant_ids"), "evaluation provenance rows keep relevant_ids"


def test_ui_help_suggests_no_vector_capability() -> None:
    text = _read(WORKFLOW_START_TSX)
    assert "unity, qdrant" not in text


def test_agent_guidance_records_retirement() -> None:
    text = _read(AGENT_GUIDANCE)
    assert "#4113" in text
    assert "Do not restore retired policy fields" in text


def test_residual_report_exists_with_delete_trigger() -> None:
    text = _read(RESIDUAL_REPORT)
    assert "Archive/delete trigger" in text
    assert "does **not** claim a zero-match sweep" in text
