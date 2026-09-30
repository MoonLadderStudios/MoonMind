"""Vector-free deployment and workflow regression coverage (#4114).

Hermetic regression owner for the Qdrant-removal release
(MoonLadderStudios/MoonMind#4114, parent #4103, acceptance contract #4105).
Live clean-install and upgraded-instance execution is not simulated here;
it runs in existing CI and this module pins the product wiring it depends on.

Evidence map:

- Live startup and ordinary work: the ``integration-ci`` job runs
  ``tools/first_run_journey_3938.sh`` on a clean default install (no
  ``.env``) and with ``--upgrade`` on an in-place upgraded instance. Each
  candidate instance runs init-db, reaches API/worker readiness, does
  ordinary work (submit/redeliver, artifacts, recurring dispatch, preset,
  worker restart with work in flight, dashboard cancel, read-back), and
  runs the ``vector_free`` phase of ``tools/single_user_journey_checks.py``
  (live ``/healthz``, served OpenAPI, and a retired submission rejected
  with no execution created). ``test_vector_free_live_journey_*`` and
  ``test_vector_free_journey_phase_*`` pin that wiring and the phase's
  failure modes. Shared-runtime context, artifact, and recovery journeys
  live in ``tests/integration/reliability_journey``.
- Clean dependencies: ``test_dependency_guard_*`` negative controls,
  ``test_dependency_removal_landed_no_manifest_only_distributions``, and
  the lockfile/import scans. Installed dependency/image evidence belongs to
  #4111 and Manifest retirement integration to #4193 (see
  ``test_vector_free_reuses_sibling_evidence_without_duplication``).
- Topology: ``test_compose_*`` guards the default and test Compose files,
  every declared profile, and every documented ``--profile`` /
  ``COMPOSE_PROFILES`` combination, with fixture negative controls for
  renamed services, optional profiles, embedded vector startup, and
  pgvector init SQL.
- Startup (hermetic slice): clean-interpreter production imports
  (``test_vector_free_startup_*``), settings init and retired-env
  inertness on clean and stale env (``test_vector_free_settings_*``), and
  the served health route (``test_vector_free_api_health_*``).
- Admission and retirement rejection: ``test_admission_*``,
  ``test_vector_free_ordinary_admission_*``,
  ``test_vector_free_rejection_*``, ``test_vector_free_denied_context_*``,
  and ``test_vector_free_upgraded_residue_*`` exercise the production
  request, checkpoint, and retrieval-issuance boundaries with no
  consequential effects on rejection.
- Operator surfaces: ``test_env_template_*`` and the registered CLI tree
  (``test_cli_command_tree_*``).

Separately authorized live storage/service/cutover observations stay with
#4115 and #4189 and are not certified by this module.

Negative controls (issue requirement 5): every guard below is proven with a
fixture that reintroduces the retired capability (transitive requirement,
renamed service, optional profile, vector init SQL, leaked tool descriptor,
old required-vector payload) and must fail in the owning guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    reject_retired_vector_fields,
    strip_absent_vector_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Reviewed topology/dependency/startup contracts.
#
# These pure helpers are the reviewed contract named by the issue: they reject
# literal or renamed vector services, optional vector profiles, embedded
# vector startup, pgvector init SQL, transitive Qdrant distributions, and
# vector startup attempts in logs. Real-repo tests run them over the actual
# checkout; fixture tests prove each negative control fails for the intended
# reason.
# ---------------------------------------------------------------------------

_VECTOR_IMAGE_RE = re.compile(
    r"qdrant|pgvector|milvus|vector-embed", re.IGNORECASE
)
_VECTOR_SERVICE_NAME_RE = re.compile(
    r"qdrant|milvus|vector[-_ ]?(db|store|service|index)|embeddings?|pgvector",
    re.IGNORECASE,
)
_VECTOR_PROFILE_RE = re.compile(r"qdrant|vector|embedding|pgvector", re.IGNORECASE)
_VECTOR_ENV_RE = re.compile(r"QDRANT_(URL|HOST|PORT|ENABLED)|VECTOR_STORE_PROVIDER")
_VECTOR_PORTS = {"6333", "6334"}
_VECTOR_COMMAND_RE = re.compile(
    r"qdrant|pgvector|init-embeddings|embedding-init", re.IGNORECASE
)
_VECTOR_SQL_RE = re.compile(
    r"CREATE\s+EXTENSION[^;]*vector"
    r"|USING\s+(ivfflat|hnsw)"
    r"|\bpgvector\b",
    re.IGNORECASE,
)
_QDRANT_DISTRIBUTION_RE = re.compile(r"^qdrant(-client)?$", re.IGNORECASE)
_RETIRED_TOOL_DESCRIPTOR_RE = re.compile(
    r"qdrant|followUpRetrieval|follow_up_retrieval", re.IGNORECASE
)
# Only the retired surfaces themselves: the top-level native ``manifest``
# group (#4192) and commands/options naming the retired vector backend.
# Skill, image, checkpoint, and other ordinary manifests stay permitted.
_RETIRED_CLI_GROUPS = {"manifest", "manifests"}
_RETIRED_CLI_BACKEND_RE = re.compile(
    r"(?:^|-)(?:qdrant|rag)(?:-|$)|(?:^|-)vector-?(?:store|db)s?(?:-|$)"
)

# MoonLadderStudios/MoonMind#4110: the native Qdrant volume declaration is
# removed; only ``moonmind_retrieval_state`` (classified by #4107) is an
# allowed vector-named volume. Any other vector-named volume is a
# reintroduction.
_KNOWN_VECTOR_FREE_VOLUMES = {"moonmind_retrieval_state"}


def _env_items(service: dict) -> list[str]:
    env = service.get("environment", [])
    if isinstance(env, dict):
        return [f"{key}={value}" for key, value in env.items()]
    return [str(item) for item in env]


def check_compose_vector_free(compose: dict) -> list[str]:
    """Return human-readable problems when Compose carries vector wiring."""
    problems: list[str] = []
    services = compose.get("services", {}) or {}
    for name, service in services.items():
        service = service or {}
        if _VECTOR_SERVICE_NAME_RE.search(str(name)):
            # A live *service* with a vector name is never allowed.
            problems.append(f"service {name!r} carries a vector service name")
        image = str(service.get("image", ""))
        if _VECTOR_IMAGE_RE.search(image):
            problems.append(f"service {name!r} uses vector image {image!r}")
        depends = service.get("depends_on", {})
        depends_keys = (
            depends.keys() if isinstance(depends, dict) else list(depends or [])
        )
        for dep in depends_keys:
            if _VECTOR_SERVICE_NAME_RE.search(str(dep)):
                problems.append(
                    f"service {name!r} depends on vector service {dep!r}"
                )
        for item in _env_items(service):
            if _VECTOR_ENV_RE.search(item):
                problems.append(
                    f"service {name!r} wires retired vector env {item!r}"
                )
        for port in service.get("ports", []) or []:
            if any(border in str(port) for border in _VECTOR_PORTS):
                problems.append(
                    f"service {name!r} exposes vector port {port!r}"
                )
        for key in ("command", "entrypoint"):
            command = service.get(key)
            blob = " ".join(command) if isinstance(command, list) else str(
                command or ""
            )
            if _VECTOR_COMMAND_RE.search(blob):
                problems.append(
                    f"service {name!r} embeds vector startup in {key}: {blob!r}"
                )
        for profile in service.get("profiles", []) or []:
            if _VECTOR_PROFILE_RE.search(str(profile)):
                problems.append(
                    f"service {name!r} hides vector backend "
                    f"behind optional profile {profile!r}"
                )
    volumes = compose.get("volumes", {}) or {}
    for volume in volumes:
        if (
            _VECTOR_SERVICE_NAME_RE.search(str(volume))
            and volume not in _KNOWN_VECTOR_FREE_VOLUMES
        ):
            problems.append(f"volume {volume!r} reintroduces vector state")
    return problems


def check_init_sql_vector_free(sql_text: str) -> list[str]:
    """Return problems when init SQL installs a vector extension/index."""
    statements = [
        line
        for line in sql_text.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ]
    code = "\n".join(statements)
    if _VECTOR_SQL_RE.search(code):
        return ["init SQL installs vector extension/index"]
    return []


def check_dependency_vector_free(distribution_names: list[str]) -> list[str]:
    """Return problems when a distribution set carries a Qdrant package."""
    return [
        f"distribution {name!r} reintroduces the retired Qdrant SDK"
        for name in distribution_names
        if _QDRANT_DISTRIBUTION_RE.match(str(name).strip())
    ]


def check_tool_manifest_vector_free(descriptors: list[str]) -> list[str]:
    """Return problems when launch/tool manifests leak retired descriptors."""
    return [
        f"tool descriptor {descriptor!r} leaks retired retrieval capability"
        for descriptor in descriptors
        if _RETIRED_TOOL_DESCRIPTOR_RE.search(str(descriptor))
    ]


def check_cli_command_tree_vector_free(typer_app) -> list[str]:
    """Return problems when the registered CLI tree exposes retired commands."""
    import typer.main

    problems: list[str] = []

    def retired(name: str) -> bool:
        normalized = re.sub(r"[-_]+", "-", name.lstrip("-").lower())
        return bool(_RETIRED_CLI_BACKEND_RE.search(normalized))

    def walk(command, path: tuple[str, ...]) -> None:
        label = " ".join(path) or "<root>"
        top_level_group = len(path) == 1 and path[0].lower() in _RETIRED_CLI_GROUPS
        if path and (top_level_group or retired(path[-1])):
            problems.append(f"command {path[-1]!r} ({label}) is a retired surface")
        for param in command.params:
            for opt in getattr(param, "opts", []):
                if retired(opt):
                    problems.append(
                        f"option {opt!r} on {label!r} is a retired surface"
                    )
        for name, sub in (getattr(command, "commands", None) or {}).items():
            walk(sub, (*path, name))

    walk(typer.main.get_command(typer_app), ())
    return problems


# ---------------------------------------------------------------------------
# Topology: real Compose file plus negative controls.
# ---------------------------------------------------------------------------


def test_compose_has_no_live_vector_service() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert check_compose_vector_free(compose) == []


def _declared_profiles(compose: dict) -> set[str]:
    profiles: set[str] = set()
    for service in ((compose.get("services", {})) or {}).values():
        for profile in (service or {}).get("profiles", []) or []:
            profiles.add(str(profile))
    return profiles


def test_compose_test_file_has_no_live_vector_service() -> None:
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.test.yaml").read_text()
    )
    assert check_compose_vector_free(compose) == []


def test_compose_all_profiles_are_vector_free() -> None:
    """Every declared profile renders without a vector backend.

    Hermetic YAML render under sanitized env: the full file (all profiles
    enabled) carries no vector wiring, and no declared profile name itself
    selects a vector backend. A renamed vector profile must fail here via
    the optional-profile guard, not hide behind ``COMPOSE_PROFILES``.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert check_compose_vector_free(compose) == []
    profiles = _declared_profiles(compose)
    # The test only means something when profiles exist to enumerate.
    assert profiles, "expected declared Compose profiles to enumerate"
    for profile in sorted(profiles):
        assert not _VECTOR_PROFILE_RE.search(profile), (
            f"profile {profile!r} selects a vector backend"
        )


def test_compose_documented_combinations_reference_no_vector_profile() -> None:
    """Documented ``--profile``/``COMPOSE_PROFILES`` combos stay vector-free.

    Every profile named by the supported-stack runbook must exist in the
    canonical Compose file and must not select a vector backend. Adding a
    documented vector combination must fail here.
    """
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    declared = _declared_profiles(compose)
    runbook = (
        REPO_ROOT / "docs/Omnigent/CombinedStackValidationAndRollback.md"
    ).read_text(encoding="utf-8")
    referenced: set[str] = set(
        re.findall(r"--profile\s+([A-Za-z0-9_.\-]+)", runbook)
    )
    for combo in re.findall(r"COMPOSE_PROFILES=\"([^\"]*)\"", runbook):
        referenced.update(
            part.strip() for part in combo.split(",") if part.strip()
        )
    assert referenced, "expected documented Compose profiles to enumerate"
    for profile in sorted(referenced):
        assert profile in declared, (
            f"documented profile {profile!r} is not a declared Compose profile"
        )
        assert not _VECTOR_PROFILE_RE.search(profile), (
            f"documented profile {profile!r} selects a vector backend"
        )


def test_compose_rejects_renamed_vector_service() -> None:
    fixture = {
        "services": {
            "api": {"image": "moonmind:latest"},
            "vector-db": {"image": "qdrant/qdrant:v1.17.1"},
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("vector-db" in problem for problem in problems)
    assert any("vector image" in problem for problem in problems)


def test_compose_rejects_optional_vector_profile() -> None:
    fixture = {
        "services": {
            "api": {"image": "moonmind:latest"},
            "search-sidecar": {
                "image": "moonmind:latest",
                "profiles": ["vectors"],
                "depends_on": ["qdrant"],
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("optional profile" in problem for problem in problems)
    assert any("depends on vector service" in problem for problem in problems)


def test_compose_rejects_embedded_vector_startup() -> None:
    fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "command": "init-embeddings && ./start-api.sh",
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("embeds vector startup" in problem for problem in problems)


def test_compose_rejects_vector_port_and_env_wiring() -> None:
    fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "environment": ["QDRANT_URL=http://qdrant:6333"],
                "ports": ["6333:6333"],
            },
        },
        "volumes": {},
    }
    problems = check_compose_vector_free(fixture)
    assert any("retired vector env" in problem for problem in problems)
    assert any("vector port" in problem for problem in problems)


def test_compose_rejects_every_retired_vector_env_key() -> None:
    """Every retired vector env key fails even without ``QDRANT_URL``.

    Regression for Codex review 3971751049: the env guard must accept any
    ``_VECTOR_ENV_RE`` match, so ``QDRANT_ENABLED=true`` (and friends) cannot
    hide behind the absence of ``QDRANT_URL``.
    """
    for env_item in (
        "QDRANT_ENABLED=true",
        "QDRANT_HOST=qdrant",
        "QDRANT_PORT=6333",
        "VECTOR_STORE_PROVIDER=qdrant",
    ):
        fixture = {
            "services": {
                "api": {"image": "moonmind:latest", "environment": [env_item]},
            },
            "volumes": {},
        }
        problems = check_compose_vector_free(fixture)
        assert any("retired vector env" in problem for problem in problems), (
            f"retired vector env {env_item!r} was not flagged"
        )
    # Mapping-form environment carries the same wiring and must also fail.
    mapping_fixture = {
        "services": {
            "api": {
                "image": "moonmind:latest",
                "environment": {"QDRANT_ENABLED": "true"},
            },
        },
        "volumes": {},
    }
    assert any(
        "retired vector env" in problem
        for problem in check_compose_vector_free(mapping_fixture)
    )


def test_compose_rejects_canonical_vector_store_images() -> None:
    """Every canonical store backend image fails the topology guard.

    Regression for Codex review 3971751070: ``VECTOR_STORE_CAPABILITIES``
    names ``qdrant``, ``pgvector``, and ``milvus``; the image guard must flag
    each of them instead of maintaining an incomplete test-only list.
    """
    for image in (
        "qdrant/qdrant:v1.17.1",
        "pgvector/pgvector:pg16",
        "milvusdb/milvus:v2.4.0",
    ):
        fixture = {
            "services": {"search": {"image": image}},
            "volumes": {},
        }
        problems = check_compose_vector_free(fixture)
        assert any("vector image" in problem for problem in problems), (
            f"canonical vector image {image!r} was not flagged"
        )


def test_compose_has_no_qdrant_storage_volume() -> None:
    """#4110: the Qdrant volume declaration is removed, not retained."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    assert "qdrant-storage" not in (compose.get("volumes", {}) or {})
    assert any(
        "reintroduces vector state" in problem
        for problem in check_compose_vector_free(
            {"services": {}, "volumes": {"qdrant-storage": {}}}
        )
    )
    fixture = {"services": {}, "volumes": {"pgvector-data": {}}}
    assert any(
        "reintroduces vector state" in problem
        for problem in check_compose_vector_free(fixture)
    )


def _init_artifact_paths() -> list[Path]:
    """Enumerate every database initialization artifact under review.

    Regression for Codex review 3971751061: a fixed two-path list stays
    green when a new artifact (for example
    ``init_db_scripts/02-enable-vector.sql``) installs ``CREATE EXTENSION
    vector``. Enumerating the initialization directories keeps the guard
    closed as artifacts are added.
    """
    paths: list[Path] = []
    for directory in (REPO_ROOT / "init_db_scripts", REPO_ROOT / "init_db"):
        if directory.is_dir():
            paths.extend(sorted(p for p in directory.iterdir() if p.is_file()))
    return paths


def test_init_scripts_reject_vector_sql() -> None:
    assert check_init_sql_vector_free("CREATE EXTENSION vector;") != []
    assert (
        check_init_sql_vector_free(
            "CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);"
        )
        != []
    )
    paths = _init_artifact_paths()
    # The test only means something when init artifacts exist to enumerate.
    assert paths, "expected database initialization artifacts to enumerate"
    for path in paths:
        assert check_init_sql_vector_free(path.read_text(encoding="utf-8")) == [], (
            f"init artifact {path} installs vector extension/index"
        )


# ---------------------------------------------------------------------------
# Clean dependencies: fixture guards plus explicit residual ownership.
# ---------------------------------------------------------------------------


def test_dependency_guard_passes_without_qdrant() -> None:
    assert check_dependency_vector_free(["fastapi", "pydantic"]) == []


def test_dependency_guard_rejects_direct_requirement() -> None:
    assert check_dependency_vector_free(["qdrant-client"]) != []


def test_dependency_guard_rejects_transitive_fixture_requirement() -> None:
    # Negative control: a fixture SDK that transitively pulls the retired
    # client must fail in the owning guard, not silently install it.
    assert (
        check_dependency_vector_free(
            ["moonmind-fixture-sdk", "Qdrant-Client", "httpx"]
        )
        != []
    )


def test_dependency_removal_landed_no_manifest_only_distributions() -> None:
    """Prove the MR5 dependency removal landed (sibling ownership resolved).

    MoonLadderStudios/MoonMind#4192 removed the native Manifest/RAG product:
    ``pyproject.toml`` and ``poetry.lock`` must not declare ``qdrant-client``,
    ``llama-index``, or any reader package. This replaces the retired
    ``test_dependency_removal_pending_sibling_ownership`` pin, which recorded
    the ``qdrant-client`` residual owned by #4106-#4113.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "qdrant-client" not in pyproject
    assert "llama-index" not in pyproject
    assert "llama_index" not in pyproject
    lock = (REPO_ROOT / "poetry.lock").read_text(encoding="utf-8")
    assert 'name = "qdrant-client"' not in lock
    assert 'name = "llama-index"' not in lock
    assert "llama-index-readers-" not in lock


# ---------------------------------------------------------------------------
# Public admission: real production path, old payloads and hidden residue.
# ---------------------------------------------------------------------------


def test_admission_rejects_old_required_vector_payload() -> None:
    old_payload = {
        "rag": {"collections": ["docs"], "required": True},
        "followUpRetrieval": {"enabled": True, "collections": ["repo"]},
    }
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(old_payload, field_path="payload")


def test_admission_rejects_snake_case_variant() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"follow_up_retrieval": {"collections": ["repo"]}},
            field_path="payload",
        )


def test_admission_rejects_retired_fields_in_workflow_branch() -> None:
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"]}}, field_path="workflow"
        )


def test_admission_strips_hidden_state_residue_without_changing_inputs() -> None:
    params = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    assert strip_absent_vector_fields(dict(params)) == {
        "instructions": "summarize"
    }
    # Absent/empty/disabled values are not retired requirements and pass.
    reject_retired_vector_fields({}, field_path="payload")
    reject_retired_vector_fields({"rag": None}, field_path="payload")


def test_tool_manifest_guard_rejects_leaked_retired_descriptor() -> None:
    assert check_tool_manifest_vector_free(["shell", "qdrant_search"]) != []
    assert (
        check_tool_manifest_vector_free(["followUpRetrieval"]) != []
    )
    assert check_tool_manifest_vector_free(["shell", "artifact_read"]) == []


# ---------------------------------------------------------------------------
# Shipped operator surfaces: env template and registered CLI tree.
# ---------------------------------------------------------------------------


def test_env_template_advertises_no_active_vector_backend() -> None:
    template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^QDRANT_ENABLED=\"true\"", template)
    assert not re.search(r"(?m)^VECTOR_STORE_PROVIDER=\"qdrant\"", template)
    assert not re.search(r"(?m)^QDRANT_URL=", template)


def test_cli_command_tree_registers_no_retired_vector_command() -> None:
    """The registered CLI tree has no vector/Manifest command or option.

    Checks the Typer command tree the ``moonmind`` entrypoint actually
    dispatches (#4192 removed the ``manifest`` group) instead of banning
    words in rendered help, which legitimately carries the retirement
    notice. A fixture app that registers a retired group or option must
    fail the same guard.
    """
    import typer
    from typer.testing import CliRunner

    from moonmind.cli import app

    assert check_cli_command_tree_vector_free(app) == []
    retired = CliRunner().invoke(app, ["manifest", "--help"], color=False)
    assert retired.exit_code != 0

    fixture = typer.Typer()
    manifest_app = typer.Typer()

    @manifest_app.command("run")
    def _manifest_run() -> None:
        pass

    @fixture.command("status")
    def _status(qdrant_url: str = typer.Option("", "--qdrant-url")) -> None:
        pass

    fixture.add_typer(manifest_app, name="manifest")
    problems = check_cli_command_tree_vector_free(fixture)
    assert any("'manifest'" in problem for problem in problems), problems
    assert any("'--qdrant-url'" in problem for problem in problems), problems

    # Positive control: Skill, image, and checkpoint manifests are ordinary
    # product surfaces, not the retired native Manifest/vector backend.
    permitted = typer.Typer()
    skill_app = typer.Typer()
    checkpoint_app = typer.Typer()

    @skill_app.command("validate-manifest")
    def _validate(skill_manifest: str = typer.Option("", "--skill-manifest")) -> None:
        pass

    @skill_app.command("manifest")
    def _skill_manifest() -> None:
        pass

    @checkpoint_app.command("restore")
    def _restore(
        checkpoint_manifest: str = typer.Option("", "--checkpoint-manifest"),
    ) -> None:
        pass

    @permitted.command("image-inspect")
    def _inspect(image_manifest: str = typer.Option("", "--image-manifest")) -> None:
        pass

    permitted.add_typer(skill_app, name="skill")
    permitted.add_typer(checkpoint_app, name="checkpoint")
    assert check_cli_command_tree_vector_free(permitted) == []


# ---------------------------------------------------------------------------
# Clean dependencies: live-import scan (no Qdrant SDK, no Manifest product).
# ---------------------------------------------------------------------------


def _python_sources_under(*roots: str) -> list[Path]:
    sources: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        sources.extend(sorted(base.rglob("*.py")))
    return sources


def test_clean_imports_have_no_live_qdrant_sdk() -> None:
    """No shipped first-party module imports the retired Qdrant SDK.

    Retirement notices, drain/cutover tooling, and the hermetic regression
    guards themselves may name Qdrant; a live ``import qdrant_client`` /
    ``from qdrant_client`` outside those owners is a reintroduction.
    """
    offenders: list[str] = []
    for path in _python_sources_under("moonmind", "api_service", "services"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.match(
                r"(import\s+qdrant_client|from\s+qdrant_client\s+import)",
                stripped,
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], f"live Qdrant SDK imports: {offenders}"


def test_no_live_manifest_product_imports() -> None:
    """No shipped module imports the removed native Manifest product package.

    ``moonmind/manifest/*``, the execution ``manifest_contract``, and the
    Temporal ``manifest_ingest`` modules were removed by #4192; a live import
    of any of them is a reintroduction. Drain-gate, cutover-rehearsal, and
    retirement-guard modules may reference the retired names in prose.
    """
    offender_pattern = re.compile(
        r"^\s*(import\s+moonmind\.manifest|from\s+moonmind\.manifest[\s.]"
        r"|from\s+moonmind\.workflows\.executions\.manifest_contract\s+import"
        r"|from\s+moonmind\.workflows\.temporal(\.workflows)?\.manifest_ingest\s+import"
        r"|import\s+moonmind\.workflows\.temporal(\.workflows)?\.manifest_ingest)",
    )
    allowed_name_res = (
        re.compile(r"manifest_ingest_drain"),
        re.compile(r"qdrant_cutover_rehearsal"),
        re.compile(r"test_vector_free_regression_4114"),
        re.compile(r"test_manifest_retirement_qualification_4193"),
        re.compile(r"test_manifest_ingest_drain"),
    )
    offenders: list[str] = []
    for path in _python_sources_under("moonmind", "api_service", "services"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rx.search(rel) for rx in allowed_name_res):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if offender_pattern.match(line):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == [], f"live Manifest product imports: {offenders}"


def test_poetry_lock_has_no_qdrant_distribution_transitive() -> None:
    """Every Qdrant distribution spelling stays out of the resolved lockfile.

    Case-insensitive negative control: ``Qdrant-Client`` (transitive casing
    variant) must fail the same guard as the canonical ``qdrant-client`` pin.
    """
    assert check_dependency_vector_free(["Qdrant-Client"]) != []
    lock = (REPO_ROOT / "poetry.lock").read_text(encoding="utf-8")
    assert not re.search(r'name\s*=\s*"qdrant[^"]*"', lock, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Real startup (hermetic slice): migrations, settings, entrypoints.
# ---------------------------------------------------------------------------


def test_migrations_carry_no_vector_sql() -> None:
    """Alembic migrations install no vector extension or vector index."""
    versions = REPO_ROOT / "api_service/migrations/versions"
    assert versions.is_dir(), "expected Alembic versions directory"
    scripts = sorted(versions.glob("*.py"))
    assert scripts, "expected Alembic migration scripts to enumerate"
    offenders: list[str] = []
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        code_lines = [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if _VECTOR_SQL_RE.search("\n".join(code_lines)):
            offenders.append(script.name)
    assert offenders == [], f"migrations install vector SQL: {offenders}"


_VECTOR_ENV_KEYS_4114 = (
    "QDRANT_URL",
    "QDRANT_HOST",
    "QDRANT_PORT",
    "QDRANT_ENABLED",
    "QDRANT_API_KEY",
    "VECTOR_STORE_PROVIDER",
    "VECTOR_STORE_COLLECTION_NAME",
    "RAG_ENABLED",
    "RAG_SIMILARITY_TOP_K",
    "DEFAULT_EMBEDDING_PROVIDER",
    "GOOGLE_EMBEDDING_MODEL",
    "OPENAI_EMBEDDING_MODEL",
)


@pytest.mark.parametrize(
    "stale_env",
    [
        pytest.param({}, id="clean-default"),
        pytest.param(
            {
                "QDRANT_URL": "http://qdrant:6333",
                "QDRANT_HOST": "qdrant",
                "QDRANT_PORT": "6333",
                "QDRANT_ENABLED": "true",
                "VECTOR_STORE_PROVIDER": "qdrant",
                "RAG_ENABLED": "true",
            },
            id="upgraded-stale-env",
        ),
    ],
)
def test_vector_free_settings_init_exposes_no_vector_surface(
    monkeypatch: pytest.MonkeyPatch, stale_env: dict[str, str]
) -> None:
    """Real settings init stays vector-free on clean and upgraded envs."""
    from moonmind.config.settings import AppSettings

    for key in _VECTOR_ENV_KEYS_4114:
        monkeypatch.delenv(key, raising=False)
    for key, value in stale_env.items():
        monkeypatch.setenv(key, value)
    settings = AppSettings(_env_file=None)
    for field in (
        "qdrant",
        "rag",
        "vector_store_provider",
        "vector_store_collection_name",
        "default_embedding_provider",
    ):
        assert not hasattr(settings, field), field


def _consumed_env_sentinels(settings, sentinels: set[str]) -> set[str]:
    """Return sentinels that surface anywhere in a settings tree's values."""
    from pydantic import BaseModel, SecretBytes, SecretStr

    found: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name, None))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item)
        else:
            if isinstance(value, (SecretStr, SecretBytes)):
                value = value.get_secret_value()
            text = value.decode() if isinstance(value, bytes) else str(value)
            found.update(sentinel for sentinel in sentinels if sentinel in text)

    walk(settings)
    return found


def test_vector_free_settings_consume_no_retired_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upgraded instance: no settings field, nested or aliased, reads vector env.

    Each retired key carries a unique sentinel; the real settings tree must
    construct and expose none of them. A fixture settings model that still
    wires ``QDRANT_URL`` must fail the same check.
    """
    from pydantic_settings import BaseSettings, SettingsConfigDict

    from moonmind.config.settings import AppSettings

    sentinels = {key: f"stale-4114-{key.lower()}" for key in _VECTOR_ENV_KEYS_4114}
    for key, value in sentinels.items():
        monkeypatch.setenv(key, value)

    settings = AppSettings(_env_file=None)
    assert _consumed_env_sentinels(settings, set(sentinels.values())) == set()

    class _LegacyVectorSettings(BaseSettings):
        model_config = SettingsConfigDict(extra="ignore")
        qdrant_url: str = ""

    legacy = _LegacyVectorSettings(_env_file=None)
    assert _consumed_env_sentinels(legacy, set(sentinels.values())) == {
        sentinels["QDRANT_URL"]
    }


# ---------------------------------------------------------------------------
# Admission matrix wiring: checkpoint models and AgentExecutionRequest.
# ---------------------------------------------------------------------------


def _checkpoint_branch_source() -> dict:
    return {
        "runId": "run-1",
        "checkpointBoundary": "before_execution",
        "checkpointRef": "artifact://tenant/repo/branch.json",
    }


def test_checkpoint_branch_create_rejects_retired_retrieval() -> None:
    """Checkpoint branch admission rejects explicit retired retrieval."""
    from pydantic import ValidationError

    from moonmind.schemas.checkpoint_branch_models import (
        CheckpointBranchCreateRequest,
    )

    base: dict = {
        "source": _checkpoint_branch_source(),
        "label": "branch",
        "instructions": {"text": "do work"},
        "workspacePolicy": "continue_from_previous_execution",
        "idempotencyKey": "idem-branch-1",
        "followUpRetrieval": {"enabled": True, "collections": ["repo"]},
    }
    with pytest.raises(ValidationError, match="4105|retired|vector"):
        CheckpointBranchCreateRequest.model_validate(base)
    # Absent/disabled residue still parses so historical payloads load.
    ok = CheckpointBranchCreateRequest.model_validate(
        {**base, "followUpRetrieval": {"enabled": False}}
    )
    assert ok.follow_up_retrieval == {"enabled": False}


def test_checkpoint_branch_continue_rejects_retired_retrieval() -> None:
    """Checkpoint continue/fork admission rejects retired retrieval."""
    from pydantic import ValidationError

    from moonmind.schemas.checkpoint_branch_models import (
        CheckpointBranchContinueRequest,
    )

    base: dict = {
        "label": "continue",
        "instructions": {"text": "continue work"},
        "idempotencyKey": "idem-continue-1",
        "followUpRetrieval": {"enabled": True},
    }
    with pytest.raises(ValidationError, match="4105|retired|vector"):
        CheckpointBranchContinueRequest.model_validate(base)


def test_agent_execution_request_rejects_retired_parameters() -> None:
    """AgentExecutionRequest admission rejects explicit retired parameters."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    with pytest.raises(ValueError, match="4105|retired|vector"):
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-4114",
            idempotencyKey="idem-4114",
            parameters={"rag": {"collections": ["docs"], "required": True}},
        )


def test_agent_execution_request_accepts_vector_free_explicit_inputs() -> None:
    """Scoped explicit inputs/Skills/artifacts admit with no vector settings."""
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-4114-free",
        idempotencyKey="idem-4114-free",
        parameters={"instructions": "summarize"},
        skill={"name": "document-update"},
        inputRefs=["artifact://tenant/repo/input.md"],
    )
    dumped = request.model_dump(by_alias=True)
    assert dumped["parameters"] == {"instructions": "summarize"}
    assert "rag" not in dumped["parameters"]
    assert "followUpRetrieval" not in dumped["parameters"]


def test_retry_reuses_exact_input_without_duplicate_delivery() -> None:
    """Absent-field stripping is idempotent and never mutates caller inputs.

    Retry must reuse the exact admitted input: stripping absent/disabled
    residue twice yields the same mapping, and the caller's dict keeps its
    original keys (the helper operates on the admitted copy).
    """
    admitted = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    first = strip_absent_vector_fields(dict(admitted))
    second = strip_absent_vector_fields(dict(first))
    assert first == {"instructions": "summarize"}
    assert second == first
    assert set(admitted) == {
        "instructions",
        "rag",
        "followUpRetrieval",
    }


# ---------------------------------------------------------------------------
# Chat/tools (hermetic slice): the production capability owners issue no
# retired tool or credential.
# ---------------------------------------------------------------------------


def test_capability_issuance_grants_no_retired_tool_descriptor(
    tmp_path: Path,
) -> None:
    """Issued capability manifests and retrieval tokens stay vector-free.

    Every authority source grants the retired descriptors alongside the full
    canonical set; the resolved session manifest must still name none of
    them. Retrieval issuance through the production registry fails closed
    and persists no live authority for the session.
    """
    from api_service.retrieval_capabilities import (
        RetrievalBudgetSnapshot,
        RetrievalCapabilityError,
        RetrievalCapabilityRegistry,
    )
    from moonmind.omnigent.effective_capabilities import (
        CAPABILITY_NAMES,
        adapt_provider_capabilities,
        resolve_effective_capabilities,
    )

    retired = ("qdrant_search", "followUpRetrieval", "follow_up_retrieval")
    granted = {name: True for name in (*CAPABILITY_NAMES, *retired)}
    authority = {
        "agentProfileRef": "profile://default",
        "agentProfileDigest": "sha256:profile",
        "providerProfileId": "provider-1",
        "providerProfileGeneration": "1",
        "launchPolicyRef": "policy://launch",
        "policySnapshotRef": "policy://snapshot",
        "policyDigest": "sha256:policy",
        "effectiveLaunchSnapshotRef": "launch://snapshot",
        "sessionEpoch": "1",
        "authorityFresh": True,
    }
    adapted = adapt_provider_capabilities(granted)
    resolved = resolve_effective_capabilities(
        authority=authority,
        upstream_capabilities=adapted,
        profile_capabilities=granted,
        launch_capabilities=granted,
        state_capabilities=granted,
        caller_capabilities=granted,
        session_status="running",
    )
    manifest = resolved.manifest()
    assert all(manifest["capabilities"].values())
    assert check_tool_manifest_vector_free(list(adapted)) == []
    assert check_tool_manifest_vector_free(list(manifest["capabilities"])) == []
    assert check_tool_manifest_vector_free(list(manifest["decisions"])) == []

    registry = RetrievalCapabilityRegistry(tmp_path)
    budget = RetrievalBudgetSnapshot(
        tenant_id="operator",
        repository="repo",
        run_id="run-1",
        workspace_id="ws-1",
        host_id="host-1",
        session_id="session-1",
        step_id="step-1",
        workflow_id="workflow-1",
        bridge_session_id="bridge-1",
        policy_version="v1",
        collections=("docs",),
        filters=(),
    )
    with pytest.raises(RetrievalCapabilityError, match="retired"):
        registry.issue(budget, lifetime_seconds=60)
    assert registry.has_live_session_authority(session_id="session-1") is False
    assert (
        registry.live_scope_capability(
            run_id="run-1", host_id="host-1", session_id="session-1", step_id="step-1"
        )
        is None
    )


# ---------------------------------------------------------------------------
# Manifest retirement (Sep 9 correction): absence + drain-gate predicate.
# ---------------------------------------------------------------------------


def test_worker_catalog_has_no_manifest_workflow_or_activity() -> None:
    """New worker/catalog registers no Manifest workflow or Activity.

    Retired ManifestIngest rows stay readable through the historical read
    scope (MoonLadderStudios/MoonMind#4189); that read-only name is the only
    permitted mention and never becomes a registration.
    """
    from moonmind.workflows.temporal.workflow_registry import (
        HISTORICAL_PRODUCT_WORKFLOW_TYPES,
        workflow_projection_scopes,
    )

    assert HISTORICAL_PRODUCT_WORKFLOW_TYPES == ("MoonMind.ManifestIngest",)
    assert not set(HISTORICAL_PRODUCT_WORKFLOW_TYPES) & set(
        workflow_projection_scopes()
    )
    registry = (
        REPO_ROOT / "moonmind/workflows/temporal/workflow_registry.py"
    ).read_text(encoding="utf-8")
    code_lines = [
        line
        for line in registry.splitlines()
        if not line.lstrip().startswith("#")
        and not line.startswith("HISTORICAL_PRODUCT_WORKFLOW_TYPES = ")
    ]
    code = "\n".join(code_lines)
    assert "ManifestIngest" not in code
    assert "manifest_ingest" not in code
    assert "manifest.compile" not in code
    assert "manifest.write_summary" not in code
    assert not (REPO_ROOT / "moonmind/manifest").exists()
    assert not list(
        (REPO_ROOT / "moonmind/workflows/temporal/workflows").glob(
            "*manifest_ingest*"
        )
    )
    assert not list((REPO_ROOT / "api_service/api/routers").glob("*manifest*"))


def test_manifest_drain_gate_blocks_on_open_histories() -> None:
    """Drain predicate allows removal only when every dimension drains to zero.

    Hermetic predicate logic (live counts stay protected): zero open
    histories/tasks/schedules is removable; any nonzero dimension retains the
    old release. Fixture replay never authorizes deployment drainage.
    """
    from moonmind.gates.manifest_ingest_drain import (
        ManifestIngestDrainUsage,
        evaluate_manifest_ingest_drain,
    )

    drained = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage(0, 0, 0))
    assert drained.may_deploy_removal is True
    assert drained.outstanding == 0
    blocked = evaluate_manifest_ingest_drain(ManifestIngestDrainUsage(1, 0, 0))
    assert blocked.may_deploy_removal is False
    assert blocked.outstanding == 1
    assert "open_manifest_ingest_histories" in blocked.blocking_dimensions


# ---------------------------------------------------------------------------
# Vector-free startup and ordinary-workflow execution (hermetic slice).
#
# MoonLadderStudios/MoonMind#4114 R2/A1: the remaining live clean/upgraded
# startup and runtime journeys execute in existing CI on disposable Compose
# services -- this module cannot own them from unit_fast (no external
# process, network, Docker, or Temporal server). What it CAN own, and adds
# here, is the real production import, readiness-wiring, and admission
# execution that those journeys depend on: every check below exercises the
# shipped implementation (not a fixture service) with no vector
# configuration, and every rejection asserts no consequential effects.
# Sibling ownership is consumed, not reproduced: installed
# dependency/image evidence belongs to #4111, Manifest retirement
# integration to #4193 (see the reuse test below).
# ---------------------------------------------------------------------------


def test_vector_free_startup_imports_without_vector_env() -> None:
    """Real production modules import with no vector configuration.

    Clean-default hermetic slice of R2: a fresh interpreter with a sanitized
    env (no ``QDRANT_*`` / ``VECTOR_*`` / embedding keys) imports the shipped
    startup path -- settings, execution contract, agent runtime schemas,
    checkpoint branch models, retrieval capabilities, worker registry, drain
    gate, and capability resolution. A fresh process is required because
    ``importlib.import_module`` returns modules already cached by collection
    or earlier tests. A vector-gated import (missing-env failure or a live
    ``qdrant_client`` import at module scope) fails here.
    """
    import os
    import subprocess
    import sys

    env = {
        key: value
        for key, value in os.environ.items()
        if not (_VECTOR_ENV_RE.search(key) or "EMBEDDING" in key.upper())
    }
    modules = (
        "moonmind.config.settings",
        "moonmind.workflows.executions.execution_contract",
        "moonmind.schemas.agent_runtime_models",
        "moonmind.schemas.checkpoint_branch_models",
        "api_service.retrieval_capabilities",
        "moonmind.workflows.temporal.workflow_registry",
        "moonmind.gates.manifest_ingest_drain",
        "moonmind.omnigent.effective_capabilities",
    )
    script = f"""
import importlib, sys
for name in {modules!r}:
    importlib.import_module(name)
leaked = sorted(m for m in sys.modules if m.split(".")[0] == "qdrant_client")
assert not leaked, f"startup imported qdrant_client: {{leaked}}"
from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    reject_retired_vector_fields,
)
# Negative control: the clean import must not smuggle a bypass -- an
# explicit retired requirement is still rejected after these imports.
try:
    reject_retired_vector_fields(
        {{"rag": {{"collections": ["docs"], "required": True}}}},
        field_path="payload",
    )
except WorkflowContractError as exc:
    assert "4105" in str(exc), exc
else:
    raise AssertionError("retired requirement admitted after clean import")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def test_vector_free_api_health_routes_have_no_vector_gate() -> None:
    """The production app serves ``/healthz`` and no vector-backend path.

    The served OpenAPI contract is rendered from the production app's
    included routers (direct ``app.routes`` introspection is not used
    because middleware instrumentation wraps the route table, see the #4193
    boundary-suite precedent). Live ``/healthz`` readiness on clean and
    upgraded instances is executed by the integration-ci first-run journey's
    ``vector_free`` phase.
    """
    from api_service.main import app

    openapi_paths = set(app.openapi().get("paths", {}).keys())
    assert "/healthz" in openapi_paths
    assert not any(
        _VECTOR_SERVICE_NAME_RE.search(str(path)) for path in openapi_paths
    ), "served API contract exposes a vector-backend path"


def test_vector_free_ordinary_admission_preserves_explicit_context() -> None:
    """Ordinary vector-free work admits with explicit context and artifacts.

    Admission only, through the shared production request model that serves
    multiple harnesses (``AgentExecutionRequest`` plus the #4105 execution
    contract): explicit instructions, input refs, Skill selection, and
    workspace spec admit with no vector settings and are preserved verbatim,
    and omitted vector-free inputs admit with no retrieval authority.
    Execution to a terminal outcome and recovery run in the integration-ci
    first-run journey and ``tests/integration/reliability_journey``.
    """
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    explicit = {
        "instructions": "summarize the attached notes",
        "workspaceSpec": {"mode": "scoped"},
    }
    reject_retired_vector_fields(dict(explicit), field_path="parameters")
    assert strip_absent_vector_fields(dict(explicit)) == explicit
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-4114-ordinary",
        idempotencyKey="idem-4114-ordinary",
        parameters=dict(explicit),
        skill={"name": "document-update"},
        inputRefs=["artifact://tenant/repo/input.md"],
    )
    dumped = request.model_dump(by_alias=True)
    assert dumped["parameters"] == explicit
    assert "rag" not in dumped["parameters"]
    assert "followUpRetrieval" not in dumped["parameters"]
    assert dumped["inputRefs"] == ["artifact://tenant/repo/input.md"]
    assert dumped["skill"] == {"name": "document-update"}
    # Omitted and explicit-empty vector-free inputs admit identically with
    # no retrieval authority.
    omitted = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-4114-omitted",
        idempotencyKey="idem-4114-omitted",
    )
    assert omitted.model_dump(by_alias=True)["parameters"] == {}
    assert "rag" not in omitted.model_dump(by_alias=True)["parameters"]


def test_vector_free_rejection_has_no_consequential_effects() -> None:
    """Retirement rejection leaves inputs and authority unchanged.

    Hermetic slice of R3 at the actual request/tool/worker boundaries: an
    explicit retired requirement raises at the execution contract, the agent
    runtime request, the checkpoint branch boundary, and the retrieval
    issuance boundary, while the caller's payload keeps its keys and no
    capability token, artifact, or drain-gate state is produced.
    """
    import copy

    from pydantic import ValidationError

    from api_service.retrieval_capabilities import (
        RetrievalBudgetSnapshot,
        RetrievalCapabilityError,
        RetrievalCapabilityRegistry,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.schemas.checkpoint_branch_models import (
        CheckpointBranchCreateRequest,
    )

    retired = {
        "instructions": "summarize",
        "rag": {"collections": ["docs"], "required": True},
    }
    before = copy.deepcopy(retired)
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(dict(retired), field_path="payload")
    with pytest.raises(ValueError, match="4105|retired|vector"):
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-4114-no-effect",
            idempotencyKey="idem-4114-no-effect",
            parameters=dict(retired),
        )
    with pytest.raises(ValidationError, match="4105|retired|vector"):
        CheckpointBranchCreateRequest.model_validate(
            {
                "source": _checkpoint_branch_source(),
                "label": "branch",
                "instructions": {"text": "do work"},
                "workspacePolicy": "continue_from_previous_execution",
                "idempotencyKey": "idem-4114-no-effect",
                "followUpRetrieval": {"enabled": True, "collections": ["repo"]},
            }
        )
    budget = RetrievalBudgetSnapshot(
        tenant_id="tenant",
        repository="repo",
        run_id="run-1",
        workspace_id="ws-1",
        host_id="host-1",
        session_id="session-1",
        step_id="step-1",
        workflow_id="workflow-1",
        bridge_session_id="bridge-1",
        policy_version="v1",
        collections=("docs",),
        filters=(),
    )
    with pytest.raises(RetrievalCapabilityError, match="retired"):
        RetrievalCapabilityRegistry().issue(budget, lifetime_seconds=60)
    assert retired == before


def test_vector_free_denied_context_does_not_widen_access_or_empty_success() -> None:
    """A denied context source never becomes wider access or empty success.

    Hermetic slice of R4: an explicit retired requirement must raise at
    admission (never strip down to an empty success), an incomplete
    vector-free admission carries no retrieval authority, and the shared
    provider-capability adapter grants no workspace mutation or retired
    retrieval descriptor from an empty/denied input.
    """
    from moonmind.omnigent.effective_capabilities import (
        PROVIDER_CAPABILITY_ALIASES,
        adapt_provider_capabilities,
    )
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    # Explicit retired fields are denied even when stripping would succeed.
    denied = {"rag": {"collections": ["docs"], "required": True}}
    assert strip_absent_vector_fields(dict(denied)) == denied
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(dict(denied), field_path="payload")
    # Incomplete vector-free admission carries no retrieval authority.
    incomplete = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-4114-denied",
        idempotencyKey="idem-4114-denied",
        parameters={},
    )
    dumped = incomplete.model_dump(by_alias=True)
    assert dumped["parameters"] == {}
    assert not any(
        key in dumped["parameters"] for key in ("rag", "collections")
    )
    # The capability adapter grants no mutation and issues no retired
    # retrieval descriptor from an empty input.
    adapted = adapt_provider_capabilities({})
    assert adapted["mutateWorkspace"] is False
    assert check_tool_manifest_vector_free(list(PROVIDER_CAPABILITY_ALIASES)) == []


def test_vector_free_upgraded_residue_stripped_at_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Representative upgraded instance: stale residue is inert at admission.

    Upgraded-instance hermetic slice of R2 at #4114's own admission
    boundary (settings-level inertness belongs to #4115): an old deployment
    carrying stale disabled residue plus retired env keys still admits --
    residue strips to the clean explicit mapping -- while an explicit
    retired requirement is still rejected. This mirrors the upgraded
    instance without claiming its live Compose startup.
    """
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("VECTOR_STORE_PROVIDER", "qdrant")
    residue = {
        "instructions": "summarize",
        "rag": {},
        "followUpRetrieval": {"enabled": False},
    }
    reject_retired_vector_fields(dict(residue), field_path="payload")
    assert strip_absent_vector_fields(dict(residue)) == {
        "instructions": "summarize"
    }
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            {"rag": {"collections": ["docs"], "required": True}},
            field_path="payload",
        )


def test_vector_free_reuses_sibling_evidence_without_duplication() -> None:
    """Reuse sibling evidence with accurate scope instead of reproducing it.

    Hermetic slice of A2: installed dependency/image evidence belongs to
    #4111 and Manifest retirement integration to #4193 -- both suites exist
    in this checkout with their owning guards, this module imports no
    Docker/image inspector and no Manifest compiler, and the existing
    impact selector routes this suite to required CI (same pattern as the
    sibling qualification suites).
    """
    from tools.select_test_suites import select_suites

    assert (
        REPO_ROOT / "tests/unit/config/test_vector_free_defaults_4115.py"
    ).is_file()
    assert (
        REPO_ROOT
        / "tests/unit/config/test_manifest_retirement_qualification_4193.py"
    ).is_file()
    assert (
        REPO_ROOT
        / "tests/unit/api/routers/test_manifest_retirement_boundaries_4193.py"
    ).is_file()
    own_source = Path(__file__).read_text(encoding="utf-8")
    import_lines = [
        line
        for line in own_source.splitlines()
        if re.match(r"\s*(import|from)\s+docker[\s.]", line)
    ]
    assert import_lines == [], f"module imports a Docker inspector: {import_lines}"
    product_import_lines = [
        line
        for line in own_source.splitlines()
        if re.match(
            r"\s*(import\s+moonmind\.manifest|from\s+moonmind\.manifest[\s.])",
            line,
        )
        or (
            "manifest_ingest" in line
            and "manifest_ingest_drain" not in line
            and re.match(r"\s*(import|from)\s+", line)
        )
    ]
    assert product_import_lines == [], (
        f"module imports the retired Manifest product: {product_import_lines}"
    )
    compile_lines = [
        line
        for line in own_source.splitlines()
        if "manifest.compile" in line and re.match(r"\s*(import|from)\s+", line)
    ]
    assert compile_lines == [], (
        f"module imports the retired Manifest compiler: {compile_lines}"
    )
    selection = select_suites(
        ["tests/unit/config/test_vector_free_regression_4114.py"]
    )
    assert selection.unit_fast is True


def test_vector_free_live_journey_wires_actual_product_boundaries() -> None:
    """Existing CI's first-run journey executes the vector-free boundaries.

    MoonLadderStudios/MoonMind#4114 R2/A1: the disposable default first-run
    journey (``tools/first_run_journey_3938.sh`` + stdlib
    ``tools/single_user_journey_checks.py``) already boots a clean default
    install with no ``.env`` and drives ordinary work (submit/redeliver,
    artifacts, recurring, preset, worker restart, dashboard cancel,
    read-back) on disposable Compose services. This test pins that the
    journey actually exercises the vector-free product boundaries instead
    of counting YAML parsing or sentinel fixtures:

    - the helper exposes a ``vector_free`` phase whose retired probe
      carries an explicit retired requirement that the real production
      admission path (``reject_retired_vector_fields`` from #4105)
      rejects;
    - the helper's phase issues the live ``/healthz``, OpenAPI, and
      retired-submission requests (exercised against a recording fake API
      in ``test_vector_free_journey_phase_*``);
    - the shell runs that phase on the candidate (fresh installs and the
      post-upgrade candidate instance), never on the pre-upgrade old
      release.
    """
    import importlib.util

    helper_path = REPO_ROOT / "tools/single_user_journey_checks.py"
    assert helper_path.is_file(), "missing live journey helper"
    spec = importlib.util.spec_from_file_location(
        "single_user_journey_checks_4114", helper_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(getattr(module, "vector_free", None)), (
        "journey helper exposes no vector_free phase"
    )
    assert callable(getattr(module, "vector_free_retired_probe", None)), (
        "journey helper exposes no vector_free_retired_probe payload"
    )
    probe = module.vector_free_retired_probe()
    assert isinstance(probe, dict) and isinstance(probe.get("payload"), dict)
    with pytest.raises(WorkflowContractError, match="4105"):
        reject_retired_vector_fields(
            probe["payload"], field_path="payload"
        )

    shell = (REPO_ROOT / "tools/first_run_journey_3938.sh").read_text(
        encoding="utf-8"
    )
    assert "checks vector_free" in shell, (
        "first-run journey never runs the vector_free phase"
    )
    fresh_body = shell.split("fresh_journey()")[1].split("\n}\n")[0]
    assert "checks vector_free" in fresh_body
    # The post-upgrade candidate instance reuses the same fresh_journey,
    # so the vector_free phase runs there too; the pre-upgrade old release
    # (which predates retirement) must not run it inline.
    assert "fresh_journey after-upgrade" in shell
    pre_upgrade_body = shell.split("bring_up 3")[1].split("Upgrading $PROJECT_NAME")[0]
    assert "vector_free" not in pre_upgrade_body


def _load_journey_helper():
    import importlib.util

    helper_path = REPO_ROOT / "tools/single_user_journey_checks.py"
    spec = importlib.util.spec_from_file_location(
        "single_user_journey_checks_4114_phase", helper_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RETIREMENT_422 = {
    "detail": {
        "code": "invalid_execution_request",
        "message": (
            "payload.rag has been retired (MoonLadderStudios/MoonMind#4105). "
            "Remove the vector retrieval/indexing fields."
        ),
    }
}


class _RecordingJourneyApi:
    """Fake live API recording the ``vector_free`` phase's requests.

    ``persist_probe`` simulates a regression that stores the rejected probe
    before answering 422; ``health``/``rejection`` override the responses.
    """

    def __init__(
        self,
        *,
        health: dict | None = None,
        rejection: object = None,
        persist_probe: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self.health = health or {"status": "ok", "database": "connected"}
        self.rejection = _RETIREMENT_422 if rejection is None else rejection
        self.persist_probe = persist_probe
        self.executions: dict[str, dict] = {
            "mm:existing": {"workflowId": "mm:existing", "title": "journey"}
        }

    def request(self, method, path, *, body=None, expect=(200,), **_kwargs):
        import json

        self.calls.append((method, path.split("?")[0]))
        if (method, path) == ("GET", "/healthz"):
            status, payload = 200, self.health
        elif (method, path) == ("GET", "/openapi.json"):
            status, payload = 200, {"paths": {"/healthz": {}, "/api/executions": {}}}
        elif method == "GET" and path.startswith("/api/executions?"):
            status, payload = 200, {"items": list(self.executions.values())}
        elif method == "GET" and path.startswith("/api/executions/"):
            workflow_id = path.rsplit("/", 1)[1].replace("%3A", ":")
            status, payload = 200, self.executions[workflow_id]
        elif (method, path) == ("POST", "/api/executions"):
            if self.persist_probe:
                instructions = body["payload"]["workflow"]["instructions"]
                self.executions["mm:probe"] = {
                    "workflowId": "mm:probe",
                    "title": instructions,
                }
            status, payload = 422, self.rejection
        else:
            raise AssertionError(f"unexpected request {method} {path}")
        assert status in expect, (method, path, status)
        return status, json.dumps(payload).encode()

    def json(self, method, path, **kwargs):
        import json

        return json.loads(self.request(method, path, **kwargs)[1])


def test_vector_free_journey_phase_probes_live_boundaries() -> None:
    """The live phase reads health/OpenAPI and probes without side effects."""
    module = _load_journey_helper()
    api = _RecordingJourneyApi()
    state: dict = {}

    module.vector_free(api, state)

    assert api.calls == [
        ("GET", "/healthz"),
        ("GET", "/openapi.json"),
        ("GET", "/api/executions"),
        ("POST", "/api/executions"),
        ("GET", "/api/executions"),
    ]
    assert state["vector_free"] == {
        "healthz": "ok",
        "openapiPaths": 2,
        "retiredRejected": True,
    }
    probe = module.vector_free_retired_probe("marker")
    assert module.RETIRED_VECTOR_DIAGNOSTIC not in str(probe), (
        "probe text could satisfy the retirement diagnostic by echo"
    )


@pytest.mark.parametrize(
    "health",
    [
        {"status": "ok", "milvus": {"status": "connected"}},
        {"status": "ok", "services": {"embedding_service": "ready"}},
        {"status": "ok", "backends": [{"pgvector": "up"}]},
        {"status": "ok", "qdrant": "connected"},
    ],
)
def test_vector_free_journey_phase_rejects_retired_health_backend(
    health: dict,
) -> None:
    module = _load_journey_helper()
    api = _RecordingJourneyApi(health=health)

    with pytest.raises(module.JourneyFailure, match="retired vector backend"):
        module.vector_free(api, {})
    assert ("POST", "/api/executions") not in api.calls


@pytest.mark.parametrize(
    "rejection",
    [
        # A generic validation error echoing the probe input.
        {
            "detail": [
                {
                    "msg": "invalid",
                    "input": {"instructions": "vector-free journey retired probe"},
                }
            ]
        },
        {"detail": {"code": "invalid_execution_request", "message": "retired vector"}},
        {"detail": {"code": "other", "message": _RETIREMENT_422["detail"]["message"]}},
    ],
)
def test_vector_free_journey_phase_requires_structured_retirement_diagnostic(
    rejection: object,
) -> None:
    module = _load_journey_helper()
    api = _RecordingJourneyApi(rejection=rejection)

    with pytest.raises(module.JourneyFailure, match="retirement diagnostic"):
        module.vector_free(api, {})


def test_vector_free_journey_phase_rejects_nested_execution_identity() -> None:
    module = _load_journey_helper()
    rejection = {
        "detail": {**_RETIREMENT_422["detail"], "execution": {"workflowId": "mm:x"}}
    }
    api = _RecordingJourneyApi(rejection=rejection)

    with pytest.raises(module.JourneyFailure, match="execution identity"):
        module.vector_free(api, {})


def test_vector_free_journey_phase_rejects_persisted_probe() -> None:
    module = _load_journey_helper()
    api = _RecordingJourneyApi(persist_probe=True)

    with pytest.raises(module.JourneyFailure, match="persisted executions"):
        module.vector_free(api, {})
