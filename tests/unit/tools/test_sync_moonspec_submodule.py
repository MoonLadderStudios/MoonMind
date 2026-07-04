from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "sync_moonspec_submodule.py"
)
SPEC = importlib.util.spec_from_file_location("sync_moonspec_submodule", MODULE_PATH)
assert SPEC is not None
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_deprecated_sync_write_fails_before_touching_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    source = repo / "moonspec"
    bundle = source / "bundle"
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    monkeypatch.setattr(mod, "DEFAULT_SOURCE", source)
    _write(
        bundle / "moonspec.bundle.yaml",
        "schemaVersion: 1\nprojections:\n  moonmind:\n    path: projections/moonmind.yaml\n",
    )
    _write(
        bundle / "projections/moonmind.yaml",
        "schemaVersion: 1\nconsumer: moonmind\nmappings:\n"
        "  - from: source.md\n"
        "    to: target.md\n"
        "    mode: file\n",
    )
    _write(bundle / "source.md", "# Source\n")

    result = mod.main(["--source", str(source), "--write"])

    assert result == 1
    assert not (repo / "target.md").exists()
    assert "--write is no longer supported" in capsys.readouterr().err
