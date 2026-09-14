"""Consumer-inventory coverage for first-run configuration simplification.

Source issue: MoonLadderStudios/MoonMind#3941 (acceptance REQ-01 and REQ-07).

Every variable retained in ``.env-template`` must resolve to at least one
real consumer with an owning boundary, recorded in the machine-readable
``config/consumer_inventory.json`` artifact. A comment or a self-listed
inventory entry is not proof of use: each record must name consumer evidence
whose location still exists in the repository.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / ".env-template"
INVENTORY_PATH = REPO_ROOT / "config" / "consumer_inventory.json"
GENERATOR_PATH = REPO_ROOT / "tools" / "config_consumer_inventory.py"

# Historical September baseline quoted by the issue assessment: the template
# held 304 assignments over 621 lines with comment-level documentation only.
BASELINE_TEMPLATE_ASSIGNMENTS = 304

KNOWN_OWNERS = frozenset(
    {
        "deployment",
        "settings-system",
        "secrets-system",
        "provider-profiles",
        "operations",
        "retirement",
    }
)

KNOWN_CLASSIFICATIONS = frozenset(
    {
        "infrastructure_bootstrap",
        "deployment_override",
        "product_preference",
        "secret",
        "operation",
        "dead",
    }
)


def _template_assignments() -> dict[str, int]:
    assignments: dict[str, int] = {}
    for lineno, line in enumerate(
        TEMPLATE.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = re.match(
            r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line
        )
        if match:
            assignments.setdefault(match.group(1), lineno)
    return assignments


def _inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "config_consumer_inventory", GENERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_template_variable_has_an_inventory_record():
    assignments = _template_assignments()
    inventory = _inventory()
    assert set(inventory["variables"]) == set(assignments), (
        "inventory drift: "
        f"missing={sorted(set(assignments) - set(inventory['variables']))} "
        f"stale={sorted(set(inventory['variables']) - set(assignments))}"
    )


def test_every_record_names_a_real_consumer_with_an_owner():
    inventory = _inventory()
    for name, record in sorted(inventory["variables"].items()):
        assert record["classification"] in KNOWN_CLASSIFICATIONS, name
        assert record["owner"] in KNOWN_OWNERS, name
        assert record["timing"] in {"pre_database", "runtime", "n/a"}, name
        assert isinstance(record["permitted_scopes"], list), name
        consumers = record["consumers"]
        assert isinstance(consumers, list) and consumers, (
            f"{name}: retained variable has no consumer evidence"
        )
        for consumer in consumers:
            assert consumer["kind"] in {
                "compose",
                "compose-native",
                "docker",
                "shell",
                "python",
                "settings-catalog",
                "docs-contract",
            }, f"{name}: {consumer}"
            assert consumer["locations"], f"{name}: consumer has no location"


def test_consumer_locations_still_exist():
    inventory = _inventory()
    missing: list[str] = []
    for name, record in sorted(inventory["variables"].items()):
        for consumer in record["consumers"]:
            for location in consumer["locations"]:
                candidate = location.split("::")[0]
                if candidate in {"Dockerfile(s)", "shell entrypoints/scripts"}:
                    continue
                if not (REPO_ROOT / candidate).exists():
                    missing.append(f"{name}: {location}")
    assert not missing, f"consumer evidence no longer resolves: {missing}"


def test_no_dead_variables_remain():
    inventory = _inventory()
    dead = sorted(
        name
        for name, record in inventory["variables"].items()
        if record["classification"] == "dead"
    )
    assert not dead, (
        "variables without any consumer must be removed through the "
        f"obsolete-configuration lifecycle, not retained: {dead}"
    )


def test_no_duplicate_template_assignments():
    inventory = _inventory()
    assert inventory["metadata"]["duplicate_names_in_template"] == [], (
        inventory["metadata"]["duplicate_names_in_template"]
    )


def test_inventory_regeneration_is_deterministic():
    module = _load_generator()
    regenerated = module.build_inventory()
    checked_in = _inventory()
    assert regenerated == checked_in, (
        "config/consumer_inventory.json is stale: rerun "
        "python3 tools/config_consumer_inventory.py"
    )


def test_first_run_contract_is_explicit():
    inventory = _inventory()
    metadata = inventory["metadata"]
    assert metadata["first_run_required"] == [], (
        "the supported default path must start with no mandatory overrides"
    )
    recommended = {
        item["name"] for item in metadata["first_run_recommended"]
    }
    assert "OPENCODE_API_KEY" in recommended
    header = TEMPLATE.read_text(encoding="utf-8").splitlines()
    assert header[0].startswith("# First run"), (
        "the template must open with a short first-run section"
    )
    assert "config/consumer_inventory.json" in TEMPLATE.read_text(encoding="utf-8")


def test_template_is_measurably_simpler_than_baseline():
    assignments = _template_assignments()
    assert len(assignments) < BASELINE_TEMPLATE_ASSIGNMENTS, (
        f"{len(assignments)} assignments, baseline was "
        f"{BASELINE_TEMPLATE_ASSIGNMENTS}"
    )
