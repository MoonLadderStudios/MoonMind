"""Purity guard for the lifecycle reconciler package.

Tracks MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

The reconciler must have no side effects or infrastructure imports (no DB,
network, filesystem, Docker, artifact, logging, telemetry, or Temporal calls).
This test statically scans every module in the package's import surface and
fails on any forbidden dependency, so purity cannot silently regress.
"""

from __future__ import annotations

import ast
import pathlib

import moonmind.omnigent.reconciler as reconciler_pkg

# Stdlib modules that are pure value/typing utilities are allowed.
_ALLOWED_STDLIB_ROOTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "typing",
}
# Only the reconciler package may be imported internally.
_ALLOWED_INTERNAL_PREFIX = "moonmind.omnigent.reconciler"

# Roots that would introduce side effects or infrastructure coupling.
_FORBIDDEN_ROOTS = {
    "os",
    "sys",
    "io",
    "socket",
    "subprocess",
    "pathlib",
    "asyncio",
    "threading",
    "logging",
    "json",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    "sqlalchemy",
    "docker",
    "temporalio",
    "boto3",
    "time",
    "random",
}


def _package_module_paths() -> list[pathlib.Path]:
    root = pathlib.Path(reconciler_pkg.__file__).parent
    return sorted(root.glob("*.py"))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            roots.add(module)
    return roots


def test_reconciler_imports_are_pure():
    for path in _package_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for module in _import_roots(tree):
            if not module:
                continue
            if module.startswith(_ALLOWED_INTERNAL_PREFIX):
                continue
            root = module.split(".")[0]
            assert root not in _FORBIDDEN_ROOTS, f"{path.name} imports forbidden {module!r}"
            if root == "moonmind":
                assert module.startswith(_ALLOWED_INTERNAL_PREFIX), (
                    f"{path.name} imports non-reconciler moonmind module {module!r}"
                )
            else:
                assert root in _ALLOWED_STDLIB_ROOTS, (
                    f"{path.name} imports unexpected module {module!r}"
                )


def test_reconciler_has_no_obvious_side_effecting_calls():
    # Guard against clock/randomness/open() creeping in (determinism + purity).
    banned_calls = {"open", "print", "now", "utcnow", "monotonic", "sleep"}
    for path in _package_module_paths():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                assert name not in banned_calls, f"{path.name} calls banned {name!r}()"
