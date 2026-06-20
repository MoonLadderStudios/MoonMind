from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def test_mm852_contract_job_uses_minimal_generator_setup() -> None:
    """MM-852 / MM-846: contract checks install only contract-generation tools."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    contract_job_match = re.search(
        r"(?ms)^  check-generated-contracts:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert contract_job_match is not None
    contract_job = contract_job_match.group("body")

    assert "bash tools/install_openapi_typescript.sh" in contract_job
    assert "uv pip install --system -e ." in contract_job
    assert "uv pip install --system -e .[tests]" not in contract_job
    assert "npm ci" not in contract_job


def test_mm852_openapi_export_avoids_startup_only_llamaindex_imports() -> None:
    """MM-852 / MM-846: exporting OpenAPI should not import RAG/indexer runtime."""

    script = """
import json
import sys

import tools.export_openapi as export_openapi

schema = export_openapi.app.openapi()
llama_modules = [
    name for name in sys.modules
    if name == "llama_index" or name.startswith("llama_index.")
]
print(json.dumps({"paths": len(schema["paths"]), "llama_modules": llama_modules}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["paths"] > 0
    assert payload["llama_modules"] == []
