#!/usr/bin/env python3
"""Deterministic Omnigent architecture-boundary guard.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

Enforces the layer boundaries documented in ``docs/Omnigent/Architecture.md`` so
that reliability changes stay inside one boundary instead of crossing policy,
persistence, transport, and framework concerns at once. The guard is a small
AST-based import scanner (no third-party dependency) with three deterministic
rules:

1. **Forbidden imports in pure layers.** ``domain/`` (including the pure
   ``reconciler/`` reducer) and ``ports/`` must not import web frameworks,
   SQLAlchemy, the Temporal SDK, HTTP/Docker/subprocess launchers, OpenTelemetry
   exporters, or application settings, and must not read environment variables.

2. **Dependency direction (no cycles/back-edges).** The Omnigent layers form a
   DAG: ``adapters -> application -> ports -> domain``. A pure layer that imports
   a layer above it (for example ``domain`` importing ``ports`` or ``adapters``)
   is a forbidden back-edge.

3. **Single canonical vocabulary.** Canonical domain vocabulary that has one
   authoritative home (currently the ``OmnigentFailureReason`` failure table)
   must not be redefined elsewhere in the package.

The checker intentionally does not fail on file length; it measures
responsibility through dependency and ownership rules, not line counts. Legacy
Omnigent modules that predate the decomposition are not classified into an
enforced layer, so this guard passes on the current tree while preventing the
new boundaries from regressing.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
OMNIGENT_ROOT = REPO_ROOT / "moonmind" / "omnigent"

# Layer name -> path prefixes (relative to moonmind/omnigent) that belong to it.
# Legacy monolithic modules are deliberately unclassified until they are
# extracted; only decomposed layers are enforced.
LAYER_DIRS: dict[str, tuple[str, ...]] = {
    "domain": ("domain", "reconciler"),
    "ports": ("ports",),
    "application": ("application",),
    "adapters": ("adapters",),
}

# Pure layers: no infrastructure, framework, or environment access.
PURE_LAYERS = frozenset({"domain", "ports"})

# Top-level module names (or dotted prefixes) forbidden inside pure layers.
FORBIDDEN_IN_PURE: tuple[str, ...] = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "temporalio",
    "httpx",
    "aiohttp",
    "requests",
    "docker",
    "subprocess",
    "opentelemetry.sdk",
    "opentelemetry.exporter",
    "moonmind.config",
    "api_service",
)

# Allowed dependency direction: a layer may only import layers with a rank at or
# below its own. adapters(3) -> application(2) -> ports(1) -> domain(0).
LAYER_RANK: dict[str, int] = {
    "domain": 0,
    "ports": 1,
    "application": 2,
    "adapters": 3,
}


@dataclass(frozen=True)
class Violation:
    """One architecture-boundary violation."""

    rule: str
    path: str
    line: int
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.detail}"


def _layer_for(rel_parts: tuple[str, ...]) -> Optional[str]:
    if not rel_parts:
        return None
    head = rel_parts[0]
    for layer, prefixes in LAYER_DIRS.items():
        if head in prefixes:
            return layer
    return None


def _imported_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """Return ``(dotted_module, lineno)`` for every import statement."""

    modules: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import: stays within the same layer, never crosses a
                # forbidden boundary, so it is not a module we need to classify.
                continue
            if node.module:
                modules.append((node.module, node.lineno))
    return modules


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _reads_environment(tree: ast.AST) -> list[int]:
    """Return line numbers of ``os.environ`` / ``os.getenv`` reads."""

    hits: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id == "os":
                hits.append(node.lineno)
    return hits


def _module_target_layer(module: str) -> Optional[str]:
    prefix = "moonmind.omnigent."
    if not module.startswith(prefix):
        return None
    remainder = module[len(prefix):]
    head = remainder.split(".", 1)[0]
    for layer, prefixes in LAYER_DIRS.items():
        if head in prefixes:
            return layer
    return None


def _iter_python_files(root: Path) -> Iterable[Path]:
    yield from sorted(root.rglob("*.py"))


def check_omnigent_architecture(
    omnigent_root: Path = OMNIGENT_ROOT,
) -> list[Violation]:
    """Return every architecture-boundary violation under ``omnigent_root``."""

    violations: list[Violation] = []
    failure_reason_defs: list[tuple[str, int]] = []

    for path in _iter_python_files(omnigent_root):
        rel = path.relative_to(omnigent_root)
        try:
            rel_str = str(path.relative_to(REPO_ROOT))
        except ValueError:
            # Scanning a tree outside the repo (for example a test fixture):
            # render paths relative to the scanned root instead.
            rel_str = str(rel)
        layer = _layer_for(rel.parts)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        # Rule 3: single canonical vocabulary (failure reason enum).
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "OmnigentFailureReason":
                failure_reason_defs.append((rel_str, node.lineno))

        if layer is None:
            continue

        imports = _imported_modules(tree)

        if layer in PURE_LAYERS:
            for module, lineno in imports:
                for forbidden in FORBIDDEN_IN_PURE:
                    if _matches_prefix(module, forbidden):
                        violations.append(
                            Violation(
                                rule="pure-layer-forbidden-import",
                                path=rel_str,
                                line=lineno,
                                detail=(
                                    f"{layer!r} layer must not import {module!r} "
                                    f"(forbidden infrastructure/framework/settings)"
                                ),
                            )
                        )
                        break
            for lineno in _reads_environment(tree):
                violations.append(
                    Violation(
                        rule="pure-layer-env-read",
                        path=rel_str,
                        line=lineno,
                        detail=(
                            f"{layer!r} layer must not read environment variables"
                        ),
                    )
                )

        # Rule 2: dependency direction (no back-edges / cycles across layers).
        own_rank = LAYER_RANK[layer]
        for module, lineno in imports:
            target_layer = _module_target_layer(module)
            if target_layer is None or target_layer == layer:
                continue
            if LAYER_RANK[target_layer] > own_rank:
                violations.append(
                    Violation(
                        rule="dependency-direction",
                        path=rel_str,
                        line=lineno,
                        detail=(
                            f"{layer!r} layer must not depend on higher layer "
                            f"{target_layer!r} (import {module!r})"
                        ),
                    )
                )

    if len(failure_reason_defs) > 1:
        locations = ", ".join(f"{p}:{ln}" for p, ln in failure_reason_defs)
        for path, line in failure_reason_defs:
            violations.append(
                Violation(
                    rule="duplicate-vocabulary",
                    path=path,
                    line=line,
                    detail=(
                        "OmnigentFailureReason must be defined exactly once; "
                        f"found duplicates at {locations}"
                    ),
                )
            )

    return violations


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=OMNIGENT_ROOT,
        help="Omnigent package root to scan (defaults to moonmind/omnigent).",
    )
    args = parser.parse_args(argv)

    violations = check_omnigent_architecture(args.root)
    if violations:
        print("Omnigent architecture-boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation.render()}", file=sys.stderr)
        return 1
    print("Omnigent architecture boundaries OK.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
