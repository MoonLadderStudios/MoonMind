"""First-run journey contract tests for issue #3940.

The README Quick Start is the supported default journey. These tests check the
contracts behind that journey — derived from the production Compose boundary
first, then requiring the reader-facing prose to agree — rather than pinning
incidental wording.

Covers: terminal evidence (not just startup), command/URL/prerequisite parity
with the release, separation of startup/credentials/publication, honest
free-model failure dispositions, conceptual-vs-operational labeling, and safe
troubleshooting.
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
INVENTORY = REPO_ROOT / "docs" / "FirstRunServiceInventory.md"
COMPOSE = REPO_ROOT / "docker-compose.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(source: str) -> str:
    return re.sub(r"\s+", " ", source)


def _compose_services() -> dict:
    return yaml.safe_load(_read(COMPOSE)).get("services", {})


# --- REQ-01: terminal evidence, not just service startup ---


def test_quick_start_ends_at_inspectable_terminal_evidence() -> None:
    readme = _read(README)
    quick_start = readme[readme.find("## Quick Start") :]
    assert "/workflows/{workflowId}" in quick_start
    assert "outputs and artifacts" in quick_start
    normalized = _normalized(quick_start)
    assert "accepted submission is not the result" in normalized or (
        "not the result" in normalized
    )


def test_supported_first_path_names_terminal_evidence() -> None:
    readme = _read(README)
    start = readme.find("## Start here")
    assert start != -1
    first_path = readme[start : readme.find("## Runtime direction")]
    assert "Workflow Detail" in first_path


# --- REQ-02: commands, URLs, selections, prerequisites match the release ---


def test_quick_start_keeps_required_setup_commands() -> None:
    readme = _read(README)
    quick_start = readme[readme.find("## Quick Start") :]
    assert "git submodule update --init --recursive" in quick_start
    assert "docker compose up -d" in quick_start
    assert "http://localhost:7000" in quick_start
    assert "/healthz" in quick_start


def test_quick_start_states_pull_not_build_and_readiness() -> None:
    readme = _read(README)
    quick_start = _normalized(readme[readme.find("## Quick Start") :])
    assert "nothing is built locally" in quick_start
    assert "does not mean a run is ready" in quick_start


def test_quick_start_avoids_host_class_rollout_jargon() -> None:
    readme = _read(README)
    quick_start = readme[readme.find("## Quick Start") :]
    quick_start = quick_start[: quick_start.find("### Access through a host name")]
    for term in ("Host Class", "Host Classes", "rollout phase", "cutover"):
        assert term not in quick_start, f"normal path must not require {term!r}"


def test_compose_has_no_local_build_on_default_path() -> None:
    for name, svc in _compose_services().items():
        assert "build" not in svc, f"{name} must be a pulled image, not a local build"


# --- REQ-03: startup, credentials, publication, future work distinguished ---


def test_quick_start_separates_startup_eligibility_source_publication() -> None:
    readme = _normalized(_read(README))
    assert "independent concerns" in readme
    assert "no `.env` does not mean every workload is credential-free" in readme
    assert "explicit publication selection" in readme


def test_scratch_path_labeled_proposed_not_shipped() -> None:
    readme = _read(README)
    assert "proposed, not shipped" in readme
    assert "RepositoryAccessAndWorkspaceDesign" in readme
    design = _read(REPO_ROOT / "docs" / "RepositoryAccessAndWorkspaceDesign.md")
    assert "Status:** Proposed" in design


def test_no_shipped_credentialless_claim() -> None:
    readme = _read(README)
    banned = [
        "no credentials needed",
        "fully credentialless",
        "without any credentials",
        "no authentication required",
    ]
    for phrase in banned:
        assert phrase not in readme, f"README must not promise: {phrase!r}"


# --- REQ-04: provider failure without paid fallback or weakened security ---


def test_free_model_failure_disposition_is_honest() -> None:
    readme = _normalized(_read(README))
    assert "no_eligible_free_model" in readme
    assert "not a product guarantee" in readme
    assert "never silently substitutes a paid or keyed profile" in readme
    assert "explicit acceptance" in readme


def test_no_universal_free_model_guarantee() -> None:
    readme = _read(README)
    banned = [
        "always available free model",
        "free model is always available",
        "unlimited free",
    ]
    for phrase in banned:
        assert phrase not in readme, f"README must not guarantee: {phrase!r}"


def test_troubleshooting_suggests_no_weakened_security() -> None:
    readme = _normalized(_read(README))
    banned = [
        "disable authentication",
        "--no-verify",
        "down -v",
        "docker volume rm",
        "docker volume prune",
    ]
    troubleshooting = readme[readme.find("Troubleshooting the first path") :]
    for phrase in banned:
        assert phrase not in troubleshooting, f"troubleshooting must not suggest: {phrase!r}"
    assert "never the first response" in troubleshooting
    assert "Do not disable safety gates" in troubleshooting


def test_no_universal_time_promise() -> None:
    readme = _read(README)
    assert not re.search(r"in (under |less than )?(ten|10)[ -]minutes?", readme), (
        "README must not promise a universal time-to-result"
    )


def test_oauth_steps_match_supported_flow() -> None:
    readme = _read(README)
    quick_start = readme[readme.find("## Quick Start") :]
    assert "click OAuth next to the profile" in quick_start
    assert "click Finalize" in quick_start


# --- REQ-05: conceptual components vs operational services ---


def test_architecture_table_labeled_conceptual_with_inventory_link() -> None:
    readme = _read(README)
    arch = readme[readme.find("## Architecture") :]
    assert "conceptual" in arch.lower()
    assert "not a one-row-per-container inventory" in arch
    assert "FirstRunServiceInventory" in arch


def test_inventory_lists_every_compose_service() -> None:
    inventory = _read(INVENTORY)
    missing = [name for name in _compose_services() if name not in inventory]
    assert not missing, f"inventory must name every compose service, missing: {missing}"


def test_inventory_marks_profiles_and_ports_from_compose() -> None:
    services = _compose_services()
    inventory = _read(INVENTORY)
    for name, svc in services.items():
        for profile in svc.get("profiles") or []:
            assert profile in inventory, f"profile {profile!r} of {name} must appear"
    assert "127.0.0.1:7000" in inventory
    assert "8000:8000" in inventory
    assert "on-demand" in inventory.lower()
    assert "mm-omnigent-host-*" in inventory


def test_inventory_distinguishes_steady_state_from_init_and_optional() -> None:
    services = _compose_services()
    inventory = _normalized(_read(INVENTORY))
    steady = sorted(n for n, s in services.items() if not s.get("profiles") and s.get("restart") != "no")
    one_shot = sorted(
        n for n, s in services.items() if not s.get("profiles") and s.get("restart") == "no"
    )
    optional = sorted(n for n, s in services.items() if s.get("profiles"))
    assert len(steady) == 14, steady
    assert len(one_shot) == 9, one_shot
    assert len(optional) == 8, optional
    assert len(steady) + len(one_shot) + len(optional) == len(services)
    assert "are not part of the default steady-state stack" in inventory
    assert "are not part of the steady-state stack" in inventory
    assert "They are not defaults" in inventory


def test_compose_service_count_matches_inventory_groups() -> None:
    services = _compose_services()
    assert len(services) == 31, f"expected 31 services, found {len(services)}"
    inventory = _read(INVENTORY)
    for group in (
        "Steady-state defaults",
        "Init and one-shot",
        "Optional profile-gated",
        "Worker-created on-demand",
    ):
        assert group in inventory


# --- README link hygiene for the new sections ---


def test_readme_new_doc_links_resolve() -> None:
    readme = _read(README)
    for target in ("docs/FirstRunServiceInventory.md", "docs/UI/CreatePage.md"):
        assert target in readme
        assert (REPO_ROOT / target).exists(), f"README link target must exist: {target}"


def test_quick_start_requires_task_input_before_submit() -> None:
    readme = _read(README)
    quick_start = readme[readme.find("## Quick Start") :]
    normalized = _normalized(quick_start)
    assert "task instructions or select an explicit Skill" in normalized
    assert "repository and branch" in normalized
