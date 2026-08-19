"""Architecture-boundary enforcement for the Omnigent package.

Source: MoonLadderStudios/MoonMind#3711 ([Omnigent control plane 10/11]).

Runs the deterministic ``tools/check_omnigent_architecture`` guard against the
real tree (which must stay clean) and unit-tests each rule against synthetic
fixture trees so a regression in the guard itself is caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKER_PATH = REPO_ROOT / "tools" / "check_omnigent_architecture.py"

_spec = importlib.util.spec_from_file_location(
    "check_omnigent_architecture", _CHECKER_PATH
)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass annotation resolution can find the module.
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def test_real_omnigent_tree_has_no_boundary_violations() -> None:
    violations = checker.check_omnigent_architecture()
    assert violations == [], "\n".join(v.render() for v in violations)


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _clean_tree(root: Path) -> None:
    _write(root, "domain/__init__.py", "")
    _write(
        root,
        "domain/failures.py",
        "from enum import Enum\n\n\nclass OmnigentFailureReason(str, Enum):\n"
        "    AUTH_FAILURE = 'auth_failure'\n",
    )
    _write(root, "ports/__init__.py", "from .sessions import SessionRepositoryPort\n")
    _write(
        root,
        "ports/sessions.py",
        "from typing import Protocol\n\n"
        "from moonmind.omnigent.domain.failures import OmnigentFailureReason\n\n\n"
        "class SessionRepositoryPort(Protocol):\n    ...\n",
    )
    _write(
        root,
        "adapters/__init__.py",
        "from moonmind.omnigent.ports import SessionRepositoryPort\n"
        "import sqlalchemy\n",
    )


def test_clean_fixture_tree_passes(tmp_path) -> None:
    _clean_tree(tmp_path)
    assert checker.check_omnigent_architecture(tmp_path) == []


def test_pure_layer_forbidden_import_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(root=tmp_path, rel="domain/store.py", source="import sqlalchemy\n")
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-forbidden-import" in rules


def test_pure_layer_fastapi_import_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(root=tmp_path, rel="ports/web.py", source="from fastapi import APIRouter\n")
    violations = checker.check_omnigent_architecture(tmp_path)
    assert any(v.rule == "pure-layer-forbidden-import" for v in violations)


def test_pure_layer_environment_read_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="domain/config.py",
        source="import os\n\nVALUE = os.environ['X']\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-env-read" in rules


def test_dependency_direction_back_edge_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    # domain (rank 0) importing ports (rank 1) is a forbidden back-edge.
    _write(
        root=tmp_path,
        rel="domain/leaky.py",
        source="from moonmind.omnigent.ports import SessionRepositoryPort\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "dependency-direction" in rules


def test_duplicate_vocabulary_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/redefine.py",
        source="from enum import Enum\n\n\nclass OmnigentFailureReason(str, Enum):\n"
        "    OTHER = 'other'\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "duplicate-vocabulary" in rules
