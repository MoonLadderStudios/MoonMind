#!/usr/bin/env python3
"""Generate frontend OpenAPI types without dirtying the repo root."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_EXPORT = REPO_ROOT / "tools" / "export_openapi.py"
DEFAULT_OUTPUT = REPO_ROOT / "frontend" / "src" / "generated" / "openapi.ts"
OPENAPI_TYPESCRIPT_CLI = (
    REPO_ROOT / "node_modules" / "openapi-typescript" / "bin" / "cli.js"
)

def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)

def _forward_streams(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)

def main() -> int:
    with tempfile.TemporaryDirectory(prefix="moonmind-openapi-") as temp_dir:
        openapi_path = Path(temp_dir) / "openapi.json"
        export_result = _run([sys.executable, str(OPENAPI_EXPORT)], cwd=REPO_ROOT)
        if export_result.returncode != 0:
            _forward_streams(export_result)
            return export_result.returncode
        openapi_path.write_text(export_result.stdout, encoding="utf-8")

        generate_result = _run(
            [
                "node",
                str(OPENAPI_TYPESCRIPT_CLI),
                str(openapi_path),
                "-o",
                str(DEFAULT_OUTPUT),
            ],
            cwd=REPO_ROOT,
        )
        _forward_streams(generate_result)
        return generate_result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
