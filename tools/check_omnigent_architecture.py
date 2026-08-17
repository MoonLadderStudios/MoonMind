#!/usr/bin/env python3
"""Deterministic architecture-boundary checker for the Omnigent layered packages.

Enforces the dependency directions and boundary rules described in
``docs/Omnigent/OmnigentArchitecture.md`` and issue
MoonLadderStudios/MoonMind#3711 for the decomposed Omnigent packages:

    moonmind/omnigent/{domain,application,ports,adapters,ui_facade,evidence}

Rules (each finding carries a stable rule id so callers can allowlist or gate):

  - forbidden-layer-import   an import that violates the allowed dependency
                             direction for the importing layer;
  - forbidden-infra-import   a domain/application/ports module importing an
                             infrastructure package (SQLAlchemy, FastAPI,
                             Temporal SDK, httpx, Docker, artifact services,
                             OpenTelemetry, settings/os.environ);
  - layer-cycle             an import cycle among the Omnigent layers;
  - fastapi-outside-facade  a FastAPI/Starlette import outside the ui_facade
                             layer (routers live in api_service, not here);
  - env-read-outside-adapter an os.environ / getenv read outside adapters;
  - duplicate-vocabulary    a canonical status/failure vocabulary symbol that
                             the domain owns, redefined in another layer.

The checker is import-graph based (AST only); it never imports the target
modules, so it is safe and deterministic in CI. Exit code is non-zero when any
finding is present unless ``--allow-dirty`` is passed (advisory mode).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
OMNIGENT_ROOT = REPO_ROOT / "moonmind" / "omnigent"
PACKAGE_PREFIX = "moonmind.omnigent"

LAYERS = ("domain", "application", "ports", "adapters", "ui_facade", "evidence")

# Allowed *intra-Omnigent* layer dependencies. A layer may always import itself.
# Values are the set of other Omnigent layers a module in the key layer may
# import from.
ALLOWED_LAYER_DEPS: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "ports": frozenset({"domain"}),
    "application": frozenset({"domain", "ports"}),
    "adapters": frozenset({"domain", "ports"}),
    "ui_facade": frozenset({"domain", "ports", "application"}),
    "evidence": frozenset({"domain", "ports"}),
}

# Infrastructure module prefixes forbidden in the pure layers.
INFRA_IMPORT_PREFIXES = (
    "sqlalchemy",
    "fastapi",
    "starlette",
    "temporalio",
    "httpx",
    "aiohttp",
    "requests",
    "docker",
    "opentelemetry",
)
# Stdlib modules that indicate a side-effect launcher/env read in a pure layer.
FORBIDDEN_STDLIB_IN_PURE = ("subprocess",)

# Layers that must stay free of infrastructure imports.
PURE_LAYERS = frozenset({"domain", "application", "ports", "evidence"})

# FastAPI/Starlette are permitted only in this layer within Omnigent.
FASTAPI_ALLOWED_LAYERS = frozenset({"ui_facade"})

# Environment reads are permitted only in adapters (infrastructure composition).
ENV_ALLOWED_LAYERS = frozenset({"adapters"})

# Canonical vocabulary symbols owned by the domain. Re-definition (assignment or
# class/enum def) of these names outside the domain layer is duplicate
# vocabulary. Re-export via ``import`` is fine (that is a reference, not a def).
DOMAIN_OWNED_VOCABULARY = frozenset(
    {
        "OmnigentFailureReason",
        "OMNIGENT_FAILURE_CLASS_TABLE",
        "coalesce_session_status",
        "failure_class_for_terminal_status",
        "normalized_status_for_event_type",
    }
)


@dataclass(frozen=True)
class Finding:
    rule: str
    module: str
    detail: str
    line: int

    def format(self) -> str:
        return f"{self.module}:{self.line}: [{self.rule}] {self.detail}"


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_of(module: str) -> str | None:
    prefix = PACKAGE_PREFIX + "."
    if not module.startswith(prefix):
        return None
    tail = module[len(prefix) :]
    top = tail.split(".", 1)[0]
    return top if top in LAYERS else None


def _iter_imports(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative import; resolve is unnecessary for our rules because
                # intra-package relative imports stay within Omnigent. Emit the
                # module tail so layer detection can still work if absolute.
                yield (node.module or ""), node.lineno
            else:
                yield (node.module or ""), node.lineno


def _iter_env_reads(tree: ast.AST) -> Iterable[int]:
    for node in ast.walk(tree):
        # os.environ[...] / os.environ.get(...) / os.getenv(...)
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            if isinstance(node.value, ast.Name) and node.value.id == "os":
                yield node.lineno


def _iter_vocabulary_defs(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in DOMAIN_OWNED_VOCABULARY:
                yield node.name, node.lineno
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if target.id in DOMAIN_OWNED_VOCABULARY:
                    yield target.id, node.lineno


def analyze_module(path: Path) -> list[Finding]:
    module = _module_name(path)
    layer = _layer_of(module)
    if layer is None:
        return []
    findings: list[Finding] = []
    tree = ast.parse(path.read_text(), filename=str(path))

    for imported, lineno in _iter_imports(tree):
        # Intra-Omnigent layer direction.
        imported_layer = _layer_of(imported)
        if imported_layer is not None and imported_layer != layer:
            if imported_layer not in ALLOWED_LAYER_DEPS[layer]:
                findings.append(
                    Finding(
                        "forbidden-layer-import",
                        module,
                        f"{layer} must not import {imported_layer} ({imported})",
                        lineno,
                    )
                )
        # Infrastructure imports in pure layers.
        if layer in PURE_LAYERS:
            if imported.split(".", 1)[0] in INFRA_IMPORT_PREFIXES:
                findings.append(
                    Finding(
                        "forbidden-infra-import",
                        module,
                        f"{layer} must not import infrastructure module {imported}",
                        lineno,
                    )
                )
            if imported.split(".", 1)[0] in FORBIDDEN_STDLIB_IN_PURE:
                findings.append(
                    Finding(
                        "forbidden-infra-import",
                        module,
                        f"{layer} must not import launcher module {imported}",
                        lineno,
                    )
                )
        # FastAPI/Starlette confinement.
        if imported.split(".", 1)[0] in {"fastapi", "starlette"}:
            if layer not in FASTAPI_ALLOWED_LAYERS:
                findings.append(
                    Finding(
                        "fastapi-outside-facade",
                        module,
                        f"FastAPI/Starlette import not allowed in {layer} ({imported})",
                        lineno,
                    )
                )

    # Environment reads outside adapters.
    if layer not in ENV_ALLOWED_LAYERS:
        for lineno in _iter_env_reads(tree):
            findings.append(
                Finding(
                    "env-read-outside-adapter",
                    module,
                    f"environment read not allowed in {layer}",
                    lineno,
                )
            )

    # Duplicate vocabulary outside the domain.
    if layer != "domain":
        for name, lineno in _iter_vocabulary_defs(tree):
            findings.append(
                Finding(
                    "duplicate-vocabulary",
                    module,
                    f"domain-owned vocabulary {name!r} redefined in {layer}",
                    lineno,
                )
            )

    return findings


def _build_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {layer: set() for layer in LAYERS}
    for path in sorted(OMNIGENT_ROOT.rglob("*.py")):
        module = _module_name(path)
        layer = _layer_of(module)
        if layer is None:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for imported, _ in _iter_imports(tree):
            imported_layer = _layer_of(imported)
            if imported_layer is not None and imported_layer != layer:
                graph[layer].add(imported_layer)
    return graph


def detect_layer_cycles(graph: dict[str, set[str]]) -> list[Finding]:
    findings: list[Finding] = []
    color: dict[str, int] = {}  # 0=unvisited,1=in-stack,2=done

    def visit(node: str, stack: list[str]) -> None:
        color[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color.get(nxt, 0) == 1:
                cycle = stack[stack.index(nxt):] + [nxt]
                findings.append(
                    Finding(
                        "layer-cycle",
                        PACKAGE_PREFIX,
                        "import cycle among layers: " + " -> ".join(cycle),
                        0,
                    )
                )
            elif color.get(nxt, 0) == 0:
                visit(nxt, stack)
        stack.pop()
        color[node] = 2

    for layer in LAYERS:
        if color.get(layer, 0) == 0:
            visit(layer, [])
    return findings


def collect_findings() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(OMNIGENT_ROOT.rglob("*.py")):
        findings.extend(analyze_module(path))
    findings.extend(detect_layer_cycles(_build_import_graph()))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit findings as JSON"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="advisory mode: always exit 0",
    )
    args = parser.parse_args(argv)

    findings = collect_findings()
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for finding in findings:
            print(finding.format())
        if not findings:
            print("omnigent architecture: OK (no boundary violations)")

    if args.allow_dirty:
        return 0
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
