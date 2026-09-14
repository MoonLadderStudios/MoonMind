"""Generate the machine-readable configuration consumer inventory.

Source issue: MoonLadderStudios/MoonMind#3941 (plan step 1, acceptance REQ-01).

Every variable assigned in ``.env-template`` must resolve to at least one real
consumer with an owning boundary. A comment, a self-listed inventory entry, or
a documentation mention alone is not proof of use; the generator therefore
records evidence by kind and location:

* ``compose``      – ``${NAME}``/``$NAME`` interpolation in docker-compose files.
* ``compose-native`` – variables the Compose tool itself consumes (profiles).
* ``docker``       – ``ARG``/``ENV``/interpolation in Dockerfiles.
* ``shell``        – ``${NAME}``/``$NAME`` reads in shell entrypoints/scripts.
* ``python``       – quoted env-alias reads, ``os.environ``/``getenv`` access,
  or nested-model prefix reads in ``moonmind/``, ``api_service/``,
  ``services/``, ``tools/``. Python sources are comment- and
  docstring-stripped through ``ast``/``tokenize`` so documentation is never
  counted as a consumer.
* ``settings-catalog`` – the variable is an ``env_alias`` of a typed
  ``SettingRegistryEntry`` in ``api_service/services/settings_catalog.py``.
* ``docs-contract`` – operator contract text enforced by a docs test (used
  only where a canonical document plus an executable docs test pins the
  variable; flagged in the record notes).

Classification precedence: secret > product_preference (catalog alias) >
operation > infrastructure_bootstrap (compose/docker/shell consumer) >
deployment_override (python-only) > dead (no consumer of any kind).

Usage: ``python3 tools/config_consumer_inventory.py [--check]``. The default
writes ``config/consumer_inventory.json``; ``--check`` regenerates to a
temporary file and diffs it against the checked-in artifact.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import os
import re
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / ".env-template"
OUTPUT = REPO_ROOT / "config" / "consumer_inventory.json"

COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yaml",
    REPO_ROOT / "docker-compose.test.yaml",
    REPO_ROOT / "docker-compose.development.yaml",
]
PYTHON_ROOTS = ["moonmind", "api_service", "services", "tools", "pr_resolver_core"]
CATALOG_FILE = "api_service/services/settings_catalog.py"

# Variables consumed by the Compose tool itself rather than by our services
# (compose-native identities, not MoonMind configuration).
COMPOSE_NATIVE_VARIABLES = frozenset({"COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME"})

_SECRET_TOKENS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "API_KEY",
    "COOKIE_SECRET",
    "SESSION_SECRET",
    "SIGNING_SECRET",
)

_OPERATION_SUBSTRINGS = (
    "JANITOR",
    "DRAIN",
    "OPERATION_MODE",
    "MAINTENANCE",
    "MIGRATION_DECISION",
    "PAUSE",
    "RESUME",
    "QUIESCE",
)

# Owner per classification. Deployment authority (ports, database
# connectivity, root-key custody, image/network/mount authority, pre-database
# startup) stays at the deployment boundary; product preferences stay with the
# settings system; secrets stay with the secrets system; provider credential
# identity stays with provider profiles; explicit commands stay with ops.
_CLASSIFICATION_OWNER = {
    "secret": "secrets-system",
    "product_preference": "settings-system",
    "operation": "operations",
    "infrastructure_bootstrap": "deployment",
    "deployment_override": "deployment",
    "dead": "retirement",
}


def _parse_template() -> list[dict]:
    """Parse uncommented assignments from .env-template in file order."""
    entries: list[dict] = []
    for lineno, line in enumerate(
        TEMPLATE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match:
            continue
        name, raw_default = match.group(1), match.group(2).strip()
        entries.append(
            {"name": name, "default": raw_default, "template_line": lineno}
        )
    return entries


def _strip_python_noise(source: str) -> str:
    """Remove comments and docstrings so docs are never counted as consumers."""
    try:
        tokens = [
            tok
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type != tokenize.COMMENT
        ]
        text = tokenize.untokenize(tokens)
        if isinstance(text, bytes):
            text = text.decode("utf-8")
    except Exception:
        text = source
    try:
        tree = ast.parse(text)
    except Exception:
        return text
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_lines.update(
                    range(first.lineno, (first.end_lineno or first.lineno) + 1)
                )
    if not docstring_lines:
        return text
    return "\n".join(
        line
        for lineno, line in enumerate(text.splitlines(), start=1)
        if lineno not in docstring_lines
    )


def _collect_text_files() -> dict[str, str]:
    """Load searchable corpora keyed by consumer kind."""
    corpora = {"compose": "", "docker": "", "shell": "", "python": ""}
    for path in COMPOSE_FILES:
        if path.is_file():
            corpora["compose"] += path.read_text(
                encoding="utf-8", errors="replace"
            ) + "\n"
    for pattern in ("Dockerfile*", "docker/Dockerfile*", "docker/*.dockerfile"):
        for match in sorted(REPO_ROOT.glob(pattern)):
            if match.is_file():
                corpora["docker"] += match.read_text(
                    encoding="utf-8", errors="replace"
                ) + "\n"
    for match in sorted(REPO_ROOT.rglob("*.sh")):
        if _excluded(match):
            continue
        try:
            corpora["shell"] += match.read_text(
                encoding="utf-8", errors="replace"
            ) + "\n"
        except OSError:
            continue
    docker_entrypoints = REPO_ROOT / "docker"
    if docker_entrypoints.is_dir():
        for match in sorted(docker_entrypoints.rglob("*")):
            if match.is_file() and not match.suffix:
                try:
                    head = match.read_bytes()[:2]
                except OSError:
                    continue
                if head == b"#!":
                    corpora["shell"] += match.read_text(
                        encoding="utf-8", errors="replace"
                    ) + "\n"
    python_parts: list[str] = []
    for root in PYTHON_ROOTS:
        root_path = REPO_ROOT / root
        if not root_path.is_dir():
            continue
        for path in sorted(root_path.rglob("*.py")):
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cleaned = _strip_python_noise(source)
            python_parts.append(f"\n# ==== {path.relative_to(REPO_ROOT)} ====\n{cleaned}")
    corpora["python"] = "\n".join(python_parts)
    return corpora


_EXCLUDED_PATH_PARTS = (
    "/.git/",
    "/node_modules/",
    "/moonspec/",
    "/omnigent/",
    "/__pycache__/",
    "/.venv/",
)


def _excluded(path: Path) -> bool:
    text = "/" + path.relative_to(REPO_ROOT).as_posix() + "/"
    return any(part in text for part in _EXCLUDED_PATH_PARTS)


def _scan_corpus_once(
    names: list[str],
    corpus: str,
    *,
    quoted_only: bool = False,
    limit_per_name: int = 5,
) -> dict[str, list[str]]:
    """Single-pass scan mapping each variable name to evidence locations."""
    alternation = "|".join(re.escape(name) for name in names)
    if quoted_only:
        pattern = re.compile(r"[\"'](" + alternation + r")[\"']")
    else:
        pattern = re.compile(r"\b(" + alternation + r")\b")
    hits: dict[str, list[str]] = {}
    current = "unknown"
    for line in corpus.splitlines():
        marker = re.match(r"^# ==== (\S+) ====$", line)
        if marker:
            current = marker.group(1)
            continue
        for match in pattern.finditer(line):
            locations = hits.setdefault(match.group(1), [])
            if current not in locations and len(locations) < limit_per_name:
                locations.append(current)
    return hits


def _locate(pattern: str, corpus: str, limit: int = 5) -> list[str]:
    """Return up to ``limit`` file markers preceding pattern matches."""
    locations: list[str] = []
    current = "unknown"
    compiled = re.compile(pattern)
    for line in corpus.splitlines():
        marker = re.match(r"^# ==== (\S+) ====$", line)
        if marker:
            current = marker.group(1)
            continue
        if compiled.search(line):
            if current not in locations:
                locations.append(current)
            if len(locations) >= limit:
                break
    return locations


def _catalog_aliases(python_corpus: str) -> dict[str, dict]:
    """Map env alias -> {key, scopes} from typed settings-catalog entries."""
    catalog_text = ""
    capturing = False
    for line in python_corpus.splitlines():
        if line.startswith("# ==== "):
            capturing = line.endswith(f"{CATALOG_FILE} ====")
            continue
        if capturing:
            catalog_text += line + "\n"
    result: dict[str, dict] = {}
    # Extract SettingRegistryEntry(...) blocks with paren-depth counting
    # (entries contain nested tuples such as settings_path and options).
    blocks: list[str] = []
    for match in re.finditer(r"SettingRegistryEntry\(", catalog_text):
        depth = 0
        in_string: str | None = None
        escaped = False
        body_chars: list[str] = []
        for char in catalog_text[match.end():]:
            if in_string is not None:
                body_chars.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == in_string:
                    in_string = None
                continue
            if char in ("'", '"'):
                in_string = char
                body_chars.append(char)
            elif char == "(":
                depth += 1
                body_chars.append(char)
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
                body_chars.append(char)
            else:
                body_chars.append(char)
        blocks.append("".join(body_chars))
    for body in blocks:
        key_match = re.search(r'key="([^"]+)"', body)
        if not key_match:
            continue
        scopes_match = re.search(r"scopes=\((.*?)\)", body, re.DOTALL)
        scopes = (
            re.findall(r'"([^"]+)"', scopes_match.group(1)) if scopes_match else []
        )
        for alias in re.findall(r'"([A-Z][A-Z0-9_]{2,})"', body):
            if alias in result:
                continue
            result[alias] = {"key": key_match.group(1), "scopes": scopes or ["workspace"]}
    return result


def _nested_model_prefix_reads(python_corpus: str) -> set[str]:
    """Expand nested BaseSettings prefix reads (e.g. MEMORY_* via MemorySettings).

    A nested settings model without an explicit per-field alias still consumes
    ``PREFIX + FIELD`` environment names; the fields are discovered from the
    class body and the prefix from the owning attribute plus test evidence.
    """
    reads: set[str] = set()
    for model, prefix in (("MemorySettings", "MEMORY_"),):
        block = re.search(
            rf"class {model}\(BaseSettings\):(.*?)(?=\nclass |\Z)", python_corpus, re.DOTALL
        )
        if not block:
            continue
        for field in re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s*[:=]", block.group(1), re.M):
            reads.add(prefix + field.upper())
    return reads


def build_inventory() -> dict:
    entries = _parse_template()
    corpora = _collect_text_files()
    catalog = _catalog_aliases(corpora["python"])
    nested_reads = _nested_model_prefix_reads(corpora["python"])

    variables: dict[str, dict] = {}
    seen: dict[str, int] = {}
    for entry in entries:
        seen[entry["name"]] = seen.get(entry["name"], 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    unique_names = sorted(seen)

    # Single-pass scans over each corpus (one regex alternation per corpus).
    # Compose files are scanned per file so locations stay file-granular.
    compose_alternation = re.compile(
        r"\$\{(?P<braced>" + "|".join(re.escape(n) for n in unique_names) + r")\b|\$(?P<bare>"
        + "|".join(re.escape(n) for n in unique_names) + r")\b"
    )
    compose_hits_by_file: dict[str, set[str]] = {}
    for path in COMPOSE_FILES:
        if not path.is_file():
            continue
        rel = str(path.relative_to(REPO_ROOT))
        found: set[str] = set()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            for match in compose_alternation.finditer(line):
                found.add(match.group("braced") or match.group("bare"))
        compose_hits_by_file[rel] = found
    docker_hits = _scan_corpus_once(unique_names, corpora["docker"])
    shell_hits = _scan_corpus_once(unique_names, corpora["shell"])
    python_quoted_hits = _scan_corpus_once(
        unique_names, corpora["python"], quoted_only=True
    )
    environ_hits: dict[str, list[str]] = {}
    environ_pattern = re.compile(
        r"os\.environ(?:_get)?\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
        r"|\bos\.getenv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
        r"|\bos\.environ\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']"
    )
    current = "unknown"
    wanted = set(unique_names)
    for line in corpora["python"].splitlines():
        marker = re.match(r"^# ==== (\S+) ====$", line)
        if marker:
            current = marker.group(1)
            continue
        for match in environ_pattern.finditer(line):
            hit = match.group(1) or match.group(2) or match.group(3)
            if hit in wanted:
                locations = environ_hits.setdefault(hit, [])
                if current not in locations and len(locations) < 5:
                    locations.append(current)

    for entry in entries:
        name = entry["name"]
        if name in variables:
            continue
        consumers: list[dict] = []
        compose_hits = sorted(
            rel
            for rel, found in compose_hits_by_file.items()
            if name in found
        )
        if compose_hits:
            consumers.append({"kind": "compose", "locations": compose_hits})
        elif name in COMPOSE_NATIVE_VARIABLES and re.search(
            r"profiles\s*:", corpora["compose"]
        ):
            consumers.append(
                {
                    "kind": "compose-native",
                    "locations": [str(p.relative_to(REPO_ROOT)) for p in COMPOSE_FILES if p.is_file()],
                    "note": "consumed by the Compose tool itself (service profiles)",
                }
            )
        if name in docker_hits:
            consumers.append({"kind": "docker", "locations": ["Dockerfile(s)"]})
        if name in shell_hits:
            consumers.append({"kind": "shell", "locations": ["shell entrypoints/scripts"]})
        # A bare uppercase match inside another identifier is not a consumer;
        # require a quoted alias, an environ access, or a prefix-field read.
        evidence = sorted(
            set(python_quoted_hits.get(name, []) + environ_hits.get(name, []))
        )
        if name in nested_reads:
            evidence = sorted(set(evidence + [CATALOG_FILE, "moonmind/config/settings.py::MemorySettings"]))
        if evidence:
            consumers.append({"kind": "python", "locations": evidence})
        catalog_hit = catalog.get(name)
        if catalog_hit:
            consumers.append(
                {
                    "kind": "settings-catalog",
                    "locations": [CATALOG_FILE],
                    "setting_key": catalog_hit["key"],
                }
            )
        kinds = {consumer["kind"] for consumer in consumers}
        is_secret = any(token in name for token in _SECRET_TOKENS)
        is_operation = any(part in name for part in _OPERATION_SUBSTRINGS)
        if is_secret and consumers:
            classification = "secret"
        elif catalog_hit:
            classification = "product_preference"
        elif is_operation and consumers:
            classification = "operation"
        elif kinds & {"compose", "compose-native", "docker", "shell"}:
            classification = "infrastructure_bootstrap"
        elif kinds & {"python", "settings-catalog"}:
            classification = "deployment_override"
        else:
            classification = "dead"
        if catalog_hit:
            scopes = catalog_hit["scopes"]
        elif classification == "secret":
            scopes = ["deployment"]
        elif classification in ("infrastructure_bootstrap", "deployment_override", "operation"):
            scopes = ["deployment"]
        else:
            scopes = []
        timing = (
            "pre_database"
            if classification == "infrastructure_bootstrap"
            else ("n/a" if classification == "dead" else "runtime")
        )
        record: dict = {
            "default": entry["default"],
            "template_line": entry["template_line"],
            "classification": classification,
            "owner": _CLASSIFICATION_OWNER[classification],
            "sensitive": bool(is_secret),
            "permitted_scopes": scopes,
            "timing": timing,
            "consumers": consumers,
        }
        if catalog_hit:
            record["setting_key"] = catalog_hit["key"]
        variables[name] = record

    # OMNIGENT_MOONMIND_WORKSPACE is pinned by a canonical operator doc plus an
    # executable docs test rather than a live code reference. Record that
    # contract explicitly instead of misclassifying it as dead.
    workspace_record = variables.get("OMNIGENT_MOONMIND_WORKSPACE")
    if workspace_record is not None and workspace_record["classification"] == "dead":
        workspace_record["classification"] = "deployment_override"
        workspace_record["owner"] = "deployment"
        workspace_record["permitted_scopes"] = ["deployment"]
        workspace_record["timing"] = "pre_database"
        workspace_record["consumers"] = [
            {
                "kind": "docs-contract",
                "locations": [
                    "docs/Omnigent/CombinedStackValidationAndRollback.md",
                    "tests/unit/docs/test_combined_stack_validation_docs.py",
                ],
                "note": (
                    "documented operator mount contract enforced by a docs "
                    "test; no live compose reference — candidate for future "
                    "doc reconciliation, not silent deletion"
                ),
            }
        ]

    first_run_required: list[str] = []
    first_run_recommended = [
        {
            "name": "OPENCODE_API_KEY",
            "reason": (
                "the only key the default Compose deployment needs to make "
                "'OpenCode via Omnigent' launchable"
            ),
        }
    ]
    return {
        "metadata": {
            "generated_by": "tools/config_consumer_inventory.py",
            "issue": "MoonLadderStudios/MoonMind#3941",
            "variable_count": len(variables),
            "duplicate_names_in_template": duplicates,
            "first_run_required": first_run_required,
            "first_run_recommended": first_run_recommended,
            "classification_precedence": (
                "secret > product_preference > operation > "
                "infrastructure_bootstrap > deployment_override > dead"
            ),
        },
        "variables": variables,
    }


def main(argv: list[str]) -> int:
    inventory = build_inventory()
    rendered = json.dumps(inventory, indent=2, sort_keys=False) + "\n"
    if "--check" in argv:
        checked_in = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if checked_in == rendered:
            print(f"inventory up to date: {OUTPUT}")
            return 0
        diff = difflib.unified_diff(
            checked_in.splitlines(),
            rendered.splitlines(),
            fromfile=str(OUTPUT),
            tofile="regenerated",
            lineterm="",
        )
        print("\n".join(list(diff)[:80]))
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    dead = sorted(
        name
        for name, record in inventory["variables"].items()
        if record["classification"] == "dead"
    )
    print(f"wrote {OUTPUT} ({len(inventory['variables'])} variables)")
    if dead:
        print(f"dead variables (no consumer): {', '.join(dead)}")
    if inventory["metadata"]["duplicate_names_in_template"]:
        print(
            "duplicate template names: "
            + ", ".join(inventory["metadata"]["duplicate_names_in_template"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
