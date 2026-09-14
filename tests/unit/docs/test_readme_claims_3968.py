"""Factual-contract review tests for issue #3968.

The README makes security, resilience, and support promises. These tests
check the factual contracts behind those promises — derived from the
production code boundary first, then requiring the reader-facing prose to
agree — rather than pinning incidental marketing wording.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
CHECKLIST = REPO_ROOT / "docs" / "READMEClaimEvidence.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(source: str) -> str:
    return re.sub(r"\s+", " ", source)


# --- Code-derived ground truth (production boundary first) ---


def test_high_security_default_is_false_in_settings() -> None:
    settings_src = _read(REPO_ROOT / "moonmind" / "config" / "settings.py")
    assert 'alias="MOONMIND_HIGH_SECURITY_MODE"' in settings_src
    # Derive the default from the Field declaration, don't hardcode blindly.
    match = re.search(
        r"high_security_mode:\s*bool\s*=\s*Field\(\s*(True|False)",
        settings_src,
    )
    assert match is not None, "high_security_mode default must be declared"
    assert match.group(1) == "False"
    readme = _normalized(_read(README))
    assert "default `false`" in readme or "default False" in readme


def test_outbound_scan_contract_is_text_only_and_opt_in() -> None:
    scan_src = _read(REPO_ROOT / "moonmind" / "security" / "outbound_scan.py")
    assert "def resolve_high_security_mode" in scan_src
    assert "def scan_outbound_text" in scan_src
    assert "def scan_outbound_bundle" in scan_src
    readme = _normalized(_read(README))
    assert "outside the native text-scan contract" in readme
    assert "SecretsSystem" in readme or "Secrets System" in readme


def test_objective_preservation_helper_exists_and_readme_scopes_rebuy() -> None:
    finalization_src = _read(
        REPO_ROOT / "moonmind" / "workflows" / "temporal" / "github_issue_finalization.py"
    )
    assert "def separate_objective_from_execution" in finalization_src
    assert '"rebuyImplementation": False' in finalization_src or (
        "'rebuyImplementation': False" in finalization_src
    )
    readme = _read(README)
    assert "Completed work is never re-bought" not in readme


def test_rollout_states_exist_and_readme_uses_them() -> None:
    rollout_src = _read(REPO_ROOT / "moonmind" / "omnigent" / "runtime_provider_rollout.py")
    for state in ("retired_for_new_work", "direct_compatibility_only"):
        assert state in rollout_src, f"rollout code must define {state}"
    selection_src = _read(
        REPO_ROOT / "moonmind" / "workflows" / "executions" / "runtime_target_selection.py"
    )
    assert "def resolve_runtime_target_selection" in selection_src
    readme = _read(README)
    assert "direct_compatibility_only" in readme
    assert "retired_for_new_work" in readme
    assert "RuntimeProviderRollout" in readme


# --- Banned unqualified absolutes (REQ-03) ---


def test_no_unqualified_absolute_claims() -> None:
    readme = _read(README)
    banned = [
        "boundaries the agent can't cross",
        "automatically redacted from logs",
        "Completed work is never re-bought",
        "does not produce duplicates",
        "retry-safe, so a crash",
        "close your laptop",
        "close your machine",
        "survive host reboots",
        "fire and forget, literally",
    ]
    for phrase in banned:
        assert phrase not in readme, f"README must not contain unqualified: {phrase!r}"


def test_laptop_claim_states_host_suspend_limit() -> None:
    readme = _normalized(_read(README))
    assert "suspend" in readme.lower()
    assert "another device" in readme.lower()


# --- First path before runtime vocabulary (REQ-01) ---


def test_supported_first_path_precedes_runtime_direction() -> None:
    readme = _read(README)
    start = readme.find("## Start here")
    runtime = readme.find("## Runtime direction")
    quickstart = readme.find("## Quick Start")
    assert start != -1, "README must open with a supported-first-path section"
    assert runtime != -1 and quickstart != -1
    assert start < runtime, "first path must precede runtime-direction terminology"
    first_path = readme[start:runtime]
    assert "docker compose up -d" in first_path
    assert "localhost:7000" in first_path
    assert "Provider Profile" in first_path
    assert "laptop" in first_path or "suspend" in first_path


# --- Claim/evidence checklist (REQ-02) ---


def test_claim_evidence_checklist_exists_with_required_columns() -> None:
    assert CHECKLIST.exists(), "editorial claim/evidence checklist must exist"
    table = _read(CHECKLIST)
    for column in (
        "Enforced boundary",
        "Default or opt-in",
        "Production caller",
        "Verification test",
        "Limitation",
    ):
        assert column in table, f"checklist must record {column}"
    assert "not a runtime registry" in table.replace("*", "")
    readme = _read(README)
    assert "READMEClaimEvidence" in readme


# --- Current vs planned (REQ-04) ---


def test_current_vs_planned_governance_report_labeled() -> None:
    readme = _read(README)
    assert "3969" in readme, "README must reference the planned governance report issue"
    assert "planned" in readme.lower()
    assert "RunGovernanceReports" in readme
    assert (REPO_ROOT / "docs" / "Governance" / "RunGovernanceReports.md").exists()
    assert "Where this is headed (planned)" in readme
    assert "These views exist today" in readme


# --- Support/retirement agreement (REQ-05) ---


def test_support_section_cites_canonical_authorities() -> None:
    readme = _read(README)
    assert "CodexSupportAndCutover" in readme
    assert "never makes a combination supported" in readme or (
        "being present" in readme and "supported" in readme
    )


def test_readme_internal_doc_links_resolve() -> None:
    readme = _read(README)
    targets = sorted(set(re.findall(r"\((docs/[^)]+?\.md)\)", readme)))
    assert targets, "README must link canonical docs"
    missing = [t for t in targets if not (REPO_ROOT / t.split("#")[0]).exists()]
    assert not missing, f"README links must resolve: {missing}"
