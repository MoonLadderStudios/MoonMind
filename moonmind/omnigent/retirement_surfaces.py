"""Typed retirement surfaces and code-derived legacy dependency discovery.

Source issue: MoonLadderStudios/MoonMind#3835.

The #3835 retirement inventory has to classify more than Python modules: it also
owns execution realizers, direct managed runtime strategies, Compose services and
profiles, provider-specific startup scripts, and legacy image/environment
identities. This module gives all of them one addressable identity — a
scheme-prefixed ``SurfaceRef`` — plus two operations the retirement guard needs:

``surface_exists``
    Whether the surface is still present. A retained (not yet removed) row whose
    surface disappeared must fail CI, because the implementation an active,
    replay, or historical-read dependency still needs was deleted silently.

``discover_legacy_surfaces``
    The set of legacy surfaces the repository *actually* contains right now,
    derived from code and deployment configuration rather than a hand-maintained
    list. The completeness guard compares this against the inventory, so adding a
    new legacy realizer, direct runtime strategy, provider-specific host script,
    duplicate Compose service, or legacy image variable fails CI until it is
    classified with a retirement class.

Discovery is deliberately derived from authorities that already exist:

* ``harness_platform.support.KNOWN_REALIZERS`` — every non-generic realizer.
* ``temporal.runtime.strategies.RUNTIME_STRATEGIES`` — every direct managed
  runtime, and the provider slugs used to recognize provider-specific surfaces.
* ``services/omnigent/scripts`` — provider-specific startup/health scripts.
* ``docker-compose.yaml`` — services that duplicate host startup per runtime or
  still resolve a legacy image variable, plus the profiles that select them.
* ``harness_platform.host_classes`` / ``static_hosts`` — declared image
  environment identities that are not the canonical shared-image variable.
"""

from __future__ import annotations

import ast
import importlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yaml"
OMNIGENT_SCRIPT_DIR = Path("services/omnigent/scripts")

# The canonical destination identities. Everything else in the discovery sources
# below is duplicate runtime architecture that #3835 must classify.
GENERIC_REALIZER_REF = "generic-omnigent-host@1"
CANONICAL_IMAGE_ENV_PREFIX = "OMNIGENT_SHARED_HOST_IMAGE"

# The retirement inventory and this discovery module name every legacy
# environment identity by construction. They are evidence *about* a surface, not
# a consumer *of* it, so they are excluded from environment existence evidence.
_INVENTORY_SELF_REFERENCE_MODULES = frozenset(
    {"legacy_retirement.py", "retirement_surfaces.py"}
)

SURFACE_SCHEMES = (
    "python",
    "file",
    "script",
    "compose-service",
    "compose-profile",
    "realizer",
    "runtime-strategy",
    "env",
)

_ENV_EXPANSION = re.compile(r"\$\{([A-Z0-9_]+)")


class SurfaceRefError(ValueError):
    """Raised when a surface reference is malformed."""


def parse_surface_ref(ref: str) -> tuple[str, str]:
    """Split ``scheme:value`` and reject anything outside the known schemes.

    A bare value with no scheme is rejected rather than guessed, so a weakened
    or mistyped reference can never silently resolve to "present".
    """

    scheme, _, value = str(ref or "").partition(":")
    if scheme not in SURFACE_SCHEMES:
        raise SurfaceRefError(
            f"surface ref {ref!r} must start with one of {SURFACE_SCHEMES}"
        )
    if not value.strip():
        raise SurfaceRefError(f"surface ref {ref!r} has no value")
    return scheme, value


@lru_cache(maxsize=1)
def _compose_document() -> Mapping[str, Any]:
    import yaml

    if not COMPOSE_FILE.is_file():
        return {}
    loaded = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, Mapping) else {}


def _compose_services() -> Mapping[str, Mapping[str, Any]]:
    services = _compose_document().get("services")
    if not isinstance(services, Mapping):
        return {}
    return {
        name: value for name, value in services.items() if isinstance(value, Mapping)
    }


def _service_text(service: Mapping[str, Any]) -> str:
    """Flatten the fields that name images, scripts, and entrypoints."""

    parts: list[str] = []
    for key in ("image", "entrypoint", "command", "profiles"):
        value = service.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value)
    return "\n".join(parts)


def _compose_profiles() -> frozenset[str]:
    profiles: set[str] = set()
    for service in _compose_services().values():
        declared = service.get("profiles")
        if isinstance(declared, (list, tuple)):
            profiles.update(str(item) for item in declared)
    return frozenset(profiles)


@lru_cache(maxsize=1)
def direct_runtime_slugs() -> frozenset[str]:
    """Provider slugs for the registered direct managed runtimes.

    Derived from the strategy registry (``codex_cli`` -> ``codex``) so a newly
    registered direct runtime automatically makes its provider-specific scripts
    and Compose services discoverable.
    """

    from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

    return frozenset(
        runtime_id.split("_", 1)[0] for runtime_id in RUNTIME_STRATEGIES if runtime_id
    )


@lru_cache(maxsize=1)
def declared_image_environment_variables() -> frozenset[str]:
    """Non-canonical image environment identities declared in Host Class code."""

    from moonmind.omnigent.harness_platform import host_classes, static_hosts

    declared: set[str] = set()
    for module in (host_classes, static_hosts):
        for name in dir(module):
            if not name.endswith("_IMAGE_ENV"):
                continue
            value = getattr(module, name)
            if isinstance(value, str) and not value.startswith(
                CANONICAL_IMAGE_ENV_PREFIX
            ):
                declared.add(value)
    return frozenset(declared)


def _provider_specific_scripts() -> frozenset[str]:
    """Startup/health scripts that exist only because a runtime is duplicated."""

    script_dir = REPO_ROOT / OMNIGENT_SCRIPT_DIR
    if not script_dir.is_dir():
        return frozenset()
    slugs = direct_runtime_slugs()
    found: set[str] = set()
    for path in sorted(script_dir.glob("*.sh")):
        stem_tokens = set(path.stem.split("-"))
        if stem_tokens & slugs:
            found.add(path.name)
        elif "projections" in stem_tokens:
            # The pre-consolidation generic entrypoint, kept only for the
            # duplicate ``omnigent-host`` Compose service.
            found.add(path.name)
    return frozenset(found)


def _legacy_compose_services() -> frozenset[str]:
    """Compose services that duplicate host startup or pin a legacy image."""

    scripts = _provider_specific_scripts()
    legacy_env = declared_image_environment_variables()
    matched: set[str] = set()
    for name, service in _compose_services().items():
        text = _service_text(service)
        if any(script in text for script in scripts):
            matched.add(name)
            continue
        referenced = set(_ENV_EXPANSION.findall(text))
        if referenced & legacy_env:
            matched.add(name)
            continue
        # A per-runtime Compose profile is itself duplicate topology even when
        # the service execs the consolidated generic entrypoint.
        declared = service.get("profiles")
        if isinstance(declared, (list, tuple)) and any(
            set(str(profile).split("-")) & direct_runtime_slugs()
            for profile in declared
        ):
            matched.add(name)
    return frozenset(matched)


def _legacy_compose_profiles(services: frozenset[str]) -> frozenset[str]:
    profiles: set[str] = set()
    compose_services = _compose_services()
    for name in services:
        declared = compose_services.get(name, {}).get("profiles")
        if isinstance(declared, (list, tuple)):
            profiles.update(str(item) for item in declared)
    return frozenset(profiles)


def _legacy_compose_image_variables(services: frozenset[str]) -> frozenset[str]:
    """Image variables the duplicate Compose services still expand."""

    compose_services = _compose_services()
    found: set[str] = set()
    for name in services:
        image = compose_services.get(name, {}).get("image")
        if not isinstance(image, str):
            continue
        for variable in _ENV_EXPANSION.findall(image):
            if not variable.startswith(CANONICAL_IMAGE_ENV_PREFIX):
                found.add(variable)
    return frozenset(found)


def discover_legacy_surfaces() -> frozenset[str]:
    """Return every legacy surface the repository currently contains."""

    from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS
    from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

    surfaces: set[str] = set()
    surfaces.update(
        f"realizer:{ref}" for ref in KNOWN_REALIZERS if ref != GENERIC_REALIZER_REF
    )
    surfaces.update(f"runtime-strategy:{ref}" for ref in RUNTIME_STRATEGIES)
    surfaces.update(f"script:{name}" for name in _provider_specific_scripts())

    services = _legacy_compose_services()
    surfaces.update(f"compose-service:{name}" for name in services)
    surfaces.update(
        f"compose-profile:{name}" for name in _legacy_compose_profiles(services)
    )
    surfaces.update(
        f"env:{name}"
        for name in (
            declared_image_environment_variables()
            | _legacy_compose_image_variables(services)
        )
    )
    return frozenset(surfaces)


def _python_symbol_exists(value: str) -> bool:
    module_name, _, symbol = value.rpartition(":")
    if not module_name or not symbol:
        return False
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - any import failure means the ref is gone
        return False
    return hasattr(module, symbol)


_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _executable_python_text(source: str) -> str:
    """Return the module's executable code with comments and docstrings removed.

    A variable named only in a docstring or a comment is documentation, not a
    consumer. Operand strings are kept, because ``os.environ["VAR"]`` is exactly
    how a variable is honored. Round-tripping through the AST drops comments;
    docstrings are removed explicitly. An unparsable module yields no evidence.
    """

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ""
    for node in ast.walk(tree):
        if not isinstance(node, _DOCSTRING_OWNERS):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                node.body = body[1:] or [ast.Pass()]
    try:
        return ast.unparse(ast.fix_missing_locations(tree))
    except (AttributeError, ValueError, RecursionError):
        return ""


@lru_cache(maxsize=1)
def _environment_reference_corpus() -> str:
    """Text of the places a legacy environment identity may still be honored.

    The corpus is built only from *authoritative consumers*: deployment
    configuration plus executable Python that reads or forwards the variable.
    The retirement inventory and surface discovery modules are excluded, because
    every inventoried variable is written into their own rows — including them
    would let the guard prove a variable "exists" from its own retirement row
    after the real Compose and runtime handling was deleted.
    """

    parts: list[str] = []
    if COMPOSE_FILE.is_file():
        parts.append(COMPOSE_FILE.read_text(encoding="utf-8"))
    for path in sorted((REPO_ROOT / "moonmind" / "omnigent").rglob("*.py")):
        if path.name in _INVENTORY_SELF_REFERENCE_MODULES:
            continue
        parts.append(
            _executable_python_text(path.read_text(encoding="utf-8", errors="ignore"))
        )
    return "\n".join(parts)


def surface_exists(ref: str) -> bool:
    """Whether the surface named by ``ref`` is still present in the repository."""

    scheme, value = parse_surface_ref(ref)
    if scheme == "python":
        return _python_symbol_exists(value)
    if scheme == "file":
        return (REPO_ROOT / value).exists()
    if scheme == "script":
        return (REPO_ROOT / OMNIGENT_SCRIPT_DIR / value).is_file()
    if scheme == "compose-service":
        return value in _compose_services()
    if scheme == "compose-profile":
        return value in _compose_profiles()
    if scheme == "realizer":
        from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS

        return value in KNOWN_REALIZERS
    if scheme == "runtime-strategy":
        from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES

        return value in RUNTIME_STRATEGIES
    if scheme == "env":
        return value in _environment_reference_corpus()
    raise SurfaceRefError(f"unhandled surface scheme {scheme!r}")


def reset_surface_caches() -> None:
    """Forget cached repository scans (tests only)."""

    _compose_document.cache_clear()
    _environment_reference_corpus.cache_clear()
    direct_runtime_slugs.cache_clear()
    declared_image_environment_variables.cache_clear()


__all__ = [
    "CANONICAL_IMAGE_ENV_PREFIX",
    "GENERIC_REALIZER_REF",
    "REPO_ROOT",
    "SURFACE_SCHEMES",
    "SurfaceRefError",
    "declared_image_environment_variables",
    "direct_runtime_slugs",
    "discover_legacy_surfaces",
    "parse_surface_ref",
    "reset_surface_caches",
    "surface_exists",
]
