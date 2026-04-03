"""Regression tests for tools/verify_vite_manifest.py."""

from __future__ import annotations

import textwrap
from pathlib import Path

import importlib.util

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_verify_module():
    path = REPO_ROOT / "tools" / "verify_vite_manifest.py"
    spec = importlib.util.spec_from_file_location("verify_vite_manifest", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_expected_entrypoints_matches_vite_config() -> None:
    mod = _load_verify_module()
    names = mod.expected_entrypoints()
    assert "tasks-list" in names
    assert "task-detail" in names
    assert "task-create" in names
    assert "skills" in names


def test_verify_vite_manifest_script_succeeds_on_synthetic_repo(
    tmp_path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    frontend_dir = repo_root / "frontend"
    assets_dir = repo_root / "api_service" / "static" / "task_dashboard" / "dist" / "assets"
    manifest_dir = assets_dir.parent / ".vite"

    frontend_dir.mkdir(parents=True)
    manifest_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)

    (frontend_dir / "vite.config.ts").write_text(
        textwrap.dedent(
            """
            import { resolve } from 'path';

            export default {
              build: {
                rollupOptions: {
                  input: {
                    'tasks-list': resolve(__dirname, 'src/entrypoints/tasks-list.tsx'),
                    'task-detail': resolve(__dirname, 'src/entrypoints/task-detail.tsx'),
                  },
                },
              },
            };
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    (manifest_dir / "manifest.json").write_text(
        textwrap.dedent(
            """
            {
              "entrypoints/tasks-list.tsx": {
                "file": "assets/tasks-list.js",
                "css": ["assets/shared.css"]
              },
              "entrypoints/task-detail.tsx": {
                "file": "assets/task-detail.js"
              }
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    (assets_dir / "tasks-list.js").write_text("console.log('tasks-list');\n", encoding="utf-8")
    (assets_dir / "task-detail.js").write_text("console.log('task-detail');\n", encoding="utf-8")
    (assets_dir / "shared.css").write_text("body { color: black; }\n", encoding="utf-8")

    mod = _load_verify_module()
    monkeypatch.setattr(mod, "ROOT", repo_root)

    assert mod.main() == 0
