#!/usr/bin/env python3
"""Deterministic Omnigent architecture-boundary guard.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

Enforces the layer boundaries documented in
``docs/Omnigent/OmnigentModuleArchitecture.md`` so
that reliability changes stay inside one boundary instead of crossing policy,
persistence, transport, and framework concerns at once. The guard is a small
AST-based import scanner (no third-party dependency) with three deterministic
rules:

1. **Forbidden imports in infra-free layers.** ``domain/`` (including the pure
   ``reconciler/`` reducer), ``ports/``, and ``application/`` must not import web
   frameworks, SQLAlchemy, the Temporal SDK, HTTP/Docker/subprocess launchers,
   OpenTelemetry exporters, or application settings, and must not read
   environment variables.

2. **Dependency direction (no cycles/back-edges).** The Omnigent layers form a
   DAG: ``adapters -> application -> ports -> domain``. A lower layer that imports
   a layer above it (for example ``domain`` importing ``ports`` or ``adapters``)
   is a forbidden back-edge.

3. **Single canonical vocabulary.** Canonical domain vocabulary that has one
   authoritative home must not be redefined elsewhere in the package. This covers
   the conflict/failure/fencing enums (``OmnigentFailureReason``,
   ``ControlPlaneOutcome``, ``FencingScope``) *and* the canonical
   status/capability vocabulary -- provider-status normalization
   (``ProviderStatusClass``), session lifecycle (``SessionLifecyclePhase``),
   terminal outcome (``TerminalOutcome``), lease/submission/desired-lifecycle
   state (``LeaseState``, ``SubmissionState``, ``DesiredLifecycle``), and the
   decision/reason tables (``DecisionKind``, ``ReasonCode``) -- so that status and
   transition vocabulary is never duplicated across the large modules.

4. **Web-framework containment.** No decomposed layer (domain, ports,
   application, or adapters) may import FastAPI/Starlette; web transport belongs
   to the API routers and the ``ui_facade`` boundary only.

5. **SQLAlchemy containment.** Direct SQLAlchemy use is confined to persistence
   adapters (``adapters/persistence/``); any other adapter subtree that imports
   the ORM is reaching past the persistence port.

6. **Provider-native vocabulary containment.** The infra-free layers (``domain``,
   ``ports``, ``application``) must not embed provider-native vendor vocabulary --
   runtime/vendor names such as ``codex``, ``claude``, ``jules``, ``gemini``,
   ``anthropic``, or ``openai`` -- in import targets or string-literal constants.
   Provider behaviour belongs behind adapters, which translate provider-native
   vocabulary into canonical domain observations and outcomes; the pure layers
   speak only the canonical vocabulary. Human-readable docstrings are exempt so
   the layers can still document which providers they abstract.

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

# Infra-free layers: no infrastructure, framework, or environment access. The
# domain and ports are pure; the application layer coordinates use cases against
# abstract ports and domain types only, so it carries the same forbidden-infra
# set (it may still import ports/domain, which the dependency-direction rule
# allows). Concrete SQLAlchemy/FastAPI/Docker/provider access belongs in adapters
# and the UI facade, never here.
INFRA_FREE_LAYERS = frozenset({"domain", "ports", "application"})

# Top-level module names (or dotted prefixes) forbidden inside infra-free layers.
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

# Web frameworks belong only to the API routers / ``ui_facade`` boundary. No
# decomposed layer at or below the facade (domain, ports, application, adapters)
# may import them, so an adapter cannot quietly grow HTTP-transport behaviour.
WEB_FRAMEWORK_PREFIXES: tuple[str, ...] = ("fastapi", "starlette")

# Direct SQLAlchemy use is confined to persistence adapters. Any other adapter
# subtree (provider HTTP/stream, docker/compose host, workspace, artifacts) must
# translate through the persistence port, not reach for the ORM directly.
SQLALCHEMY_PREFIXES: tuple[str, ...] = ("sqlalchemy",)
PERSISTENCE_SUBTREE: str = "persistence"

# Canonical domain vocabularies that have exactly one authoritative definition in
# the package. Redefining any of them anywhere else is duplicate vocabulary
# (provider-native or otherwise) that the decomposition exists to eliminate.
#
# Two groups are enforced:
#   * the conflict/failure/fencing enums that own conflict-resolution outcomes;
#   * the canonical status/capability/lifecycle/decision vocabulary that owns the
#     provider-status normalization, session lifecycle, terminal outcome, lease and
#     submission state, desired lifecycle, decision-kind, and reason-code tables.
# The second group is the "status and capability vocabulary" the issue calls out:
# these tables must have exactly one home so provider-status normalization and
# transition/decision vocabulary are never duplicated across the large modules.
SINGLE_DEFINITION_TYPES: frozenset[str] = frozenset(
    {
        # Conflict-resolution / failure / fencing outcomes.
        "OmnigentFailureReason",
        "ControlPlaneOutcome",
        "FencingScope",
        # Canonical status/capability/lifecycle/decision vocabulary.
        "ProviderStatusClass",
        "SessionLifecyclePhase",
        "TerminalOutcome",
        "LeaseState",
        "SubmissionState",
        "DesiredLifecycle",
        "DecisionKind",
        "ReasonCode",
    }
)

# Provider-native vendor vocabulary must stay behind adapters (and their
# compatibility helpers), which translate it into canonical domain observations
# and outcomes. The infra-free layers (domain, ports, application) speak only the
# canonical vocabulary, so a vendor/runtime name leaking into an import target or
# a string-literal constant there is a boundary violation. These tokens are the
# concrete provider/runtime identities MoonMind orchestrates; matching is a
# case-insensitive substring test so provider-native status strings like
# ``codex_completed`` or route fragments like ``/claude/`` are caught too.
PROVIDER_NATIVE_TOKENS: tuple[str, ...] = (
    "codex",
    "claude",
    "jules",
    "gemini",
    "anthropic",
    "openai",
    "opencode",
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


def _docstring_constant_ids(tree: ast.AST) -> frozenset[int]:
    """Return ``id()`` of every module/class/function docstring node.

    Docstrings are human-readable prose that may legitimately name the providers
    a pure layer abstracts, so they are exempt from provider-native vocabulary
    containment. Every other string-literal constant is canonical vocabulary and
    must not embed a vendor/runtime name.
    """

    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return frozenset(ids)


def _provider_native_string_hits(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Return ``(token, literal, lineno)`` for provider vocab in string literals.

    Docstrings are excluded (see :func:`_docstring_constant_ids`). Matching is a
    case-insensitive substring test against :data:`PROVIDER_NATIVE_TOKENS`.
    """

    docstring_ids = _docstring_constant_ids(tree)
    hits: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_ids:
            continue
        lowered = node.value.lower()
        for token in PROVIDER_NATIVE_TOKENS:
            if token in lowered:
                hits.append((token, node.value, node.lineno))
                break
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
    single_def_locations: dict[str, list[tuple[str, int]]] = {}

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

        # Rule 3: single canonical vocabulary (failure/outcome/fencing enums).
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in SINGLE_DEFINITION_TYPES:
                single_def_locations.setdefault(node.name, []).append(
                    (rel_str, node.lineno)
                )

        if layer is None:
            continue

        imports = _imported_modules(tree)

        if layer in INFRA_FREE_LAYERS:
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
            # Rule 6: provider-native vocabulary containment. Vendor/runtime names
            # in an import target or a (non-docstring) string literal mean the
            # pure layer is speaking provider-native vocabulary that belongs behind
            # an adapter/compatibility boundary.
            for module, lineno in imports:
                lowered_module = module.lower()
                token = next(
                    (t for t in PROVIDER_NATIVE_TOKENS if t in lowered_module),
                    None,
                )
                if token is not None:
                    violations.append(
                        Violation(
                            rule="pure-layer-provider-vocabulary",
                            path=rel_str,
                            line=lineno,
                            detail=(
                                f"{layer!r} layer must not import provider-native "
                                f"module {module!r} (vendor token {token!r}); "
                                "provider vocabulary belongs behind an adapter"
                            ),
                        )
                    )
            for token, literal, lineno in _provider_native_string_hits(tree):
                snippet = literal if len(literal) <= 60 else literal[:57] + "..."
                violations.append(
                    Violation(
                        rule="pure-layer-provider-vocabulary",
                        path=rel_str,
                        line=lineno,
                        detail=(
                            f"{layer!r} layer must not embed provider-native "
                            f"vocabulary (vendor token {token!r} in string "
                            f"{snippet!r}); translate it to canonical domain "
                            "vocabulary in an adapter"
                        ),
                    )
                )

        # Rule 4/5: containment inside the adapters layer. Web frameworks belong
        # to the API/ui_facade boundary, and direct ORM use belongs only to the
        # persistence subtree; every other adapter must translate through a port.
        if layer == "adapters":
            under_persistence = (
                len(rel.parts) >= 2 and rel.parts[1] == PERSISTENCE_SUBTREE
            )
            for module, lineno in imports:
                if any(
                    _matches_prefix(module, prefix)
                    for prefix in WEB_FRAMEWORK_PREFIXES
                ):
                    violations.append(
                        Violation(
                            rule="adapters-web-framework",
                            path=rel_str,
                            line=lineno,
                            detail=(
                                f"'adapters' layer must not import {module!r}; web "
                                "transport belongs to the API/ui_facade boundary"
                            ),
                        )
                    )
                if not under_persistence and any(
                    _matches_prefix(module, prefix)
                    for prefix in SQLALCHEMY_PREFIXES
                ):
                    violations.append(
                        Violation(
                            rule="adapters-sqlalchemy-containment",
                            path=rel_str,
                            line=lineno,
                            detail=(
                                f"{module!r} may only be imported under "
                                "adapters/persistence/; other adapters must use "
                                "the persistence port"
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

    for type_name, defs in sorted(single_def_locations.items()):
        if len(defs) <= 1:
            continue
        locations = ", ".join(f"{p}:{ln}" for p, ln in defs)
        for path, line in defs:
            violations.append(
                Violation(
                    rule="duplicate-vocabulary",
                    path=path,
                    line=line,
                    detail=(
                        f"{type_name} must be defined exactly once; "
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
