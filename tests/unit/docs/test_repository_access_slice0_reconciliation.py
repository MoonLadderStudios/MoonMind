"""Slice-0 reconciliation conformance for issue #4004.

Guards the deliverables of the repository-access contract/ownership
reconciliation: the design stays Proposed (no gate relaxation), AGENTS.md's
remote-checkpoint recovery gate stays mandatory, the tmp plan carries the
complete Slice-0 record (inventory, contract mapping, recovery decision,
support matrix, partition, fixtures/baseline/decision), the owner-defined
fixtures honor schema semantics without fake values, and every global
credential-resolution call site is inventoried so reintroduction fails CI
with an actionable message.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

DESIGN_DOC = REPO_ROOT / "docs" / "RepositoryAccessAndWorkspaceDesign.md"
PLAN_DOC = REPO_ROOT / "docs" / "tmp" / "RepositoryAccessAndWorkspaceDecouplingPlan.md"
FIXTURES_DOC = REPO_ROOT / "docs" / "tmp" / "repository-access-slice0-fixtures.yaml"
AGENTS_DOC = REPO_ROOT / "AGENTS.md"

RECHECKED_BASELINE = "5da35a2ba"

EXPECTED_CLAIMS = (
    [f"CONTRACT-{n:03d}" for n in range(1, 15)]
    + [f"DOC-REQ-{n:03d}" for n in range(1, 6)]
    + [f"INV-{n:03d}" for n in range(1, 10)]
    + ["NON-GOAL-001"]
    + [f"QUALITY-{n:03d}" for n in range(1, 10)]
    + [f"TEST-{n:03d}" for n in range(1, 5)]
)

# Files that must appear in the Slice-0 inventory (§10.2). New global
# resolver call sites must be added to the inventory, not hidden from it.
GLOBAL_RESOLVER_SYMBOLS = (
    "resolve_github_credential",
    "resolve_github_token_for_launch",
)

SECRET_SHAPES = tuple(
    re.compile(pattern)
    for pattern in (
        r"ghp_[A-Za-z0-9_]+",
        r"github_pat_[A-Za-z0-9_]+",
        r"AIza[A-Za-z0-9_\-]+",
        r"AKIA[A-Z0-9]+",
        r"BEGIN .*PRIVATE KEY",
    )
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _repo_python_files() -> list[Path]:
    roots = (REPO_ROOT / "moonmind", REPO_ROOT / "api_service")
    files: list[Path] = []
    for root in roots:
        files.extend(p for p in root.rglob("*.py") if p.is_file())
    return files


def test_design_remains_proposed_no_gate_relaxation() -> None:
    text = _read(DESIGN_DOC)
    assert "**Status:** Proposed" in text
    # The design must keep stating its handoff is proposed, not permitted.
    assert "may already be bypassed" in text
    assert "proposed changes to the owning contracts and guidance" in text
    # Slice-0 enables no runtime path.
    assert "No runtime qualification is claimed by this documentation task" in _read(PLAN_DOC)


def test_agents_recovery_gate_stays_mandatory() -> None:
    text = _read(AGENTS_DOC)
    assert "publish a remotely verified recovery checkpoint before cleanup" in text


def test_plan_carries_complete_slice0_record() -> None:
    text = _read(PLAN_DOC)
    for section in (
        "## 10. Slice-0 reconciliation record (issue #4004)",
        "### 10.1 Rechecked baseline and method",
        "### 10.2 Rechecked consumer inventory with owners",
        "### 10.3 Module-owned contract mapping, wire versions, and type owners",
        "### 10.4 Recovery-guidance reconciliation (reviewed docs decision)",
        "### 10.5 Supported-combination matrix and capture/restore owners",
        "### 10.6 Work partition, compatibility, digests, gates, rejections",
        "### 10.7 Fixtures, claim/link checks, and review decision",
    ):
        assert section in text, section
    assert RECHECKED_BASELINE in text
    # No second connection domain or target domain may be introduced.
    assert "No `SourceControlConnection`" in text
    assert "no `RepositoryTargetV2`" in text
    # Sibling partition must name every related issue.
    for issue in ("#2615", "#1090", "#2619", "#3938", "#3940", "#4003", "#4004"):
        assert issue in text, issue


def test_all_42_claims_resolve_to_tracked_work() -> None:
    assert len(EXPECTED_CLAIMS) == 42
    design = _read(DESIGN_DOC)
    plan = _read(PLAN_DOC)
    for claim in EXPECTED_CLAIMS:
        assert claim in design, f"{claim} missing from design"
        assert claim in plan, f"{claim} not tracked in plan"


def test_inventory_paths_exist() -> None:
    text = _read(PLAN_DOC)
    record = text.split("## 10. Slice-0 reconciliation record")[1]
    paths = sorted(set(re.findall(r"`((?:moonmind|api_service|frontend)/[^`]+?\.py)`", record)))
    assert paths, "Slice-0 record must cite exact module paths"
    for rel in paths:
        assert (REPO_ROOT / rel).exists(), f"inventoried path does not exist: {rel}"


def test_global_resolver_call_sites_are_inventoried() -> None:
    record = _read(PLAN_DOC).split("## 10. Slice-0 reconciliation record")[1]
    offenders: list[str] = []
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if (
            any(symbol in text for symbol in GLOBAL_RESOLVER_SYMBOLS)
            and "def resolve_github_credential" not in text
            and "def resolve_github_token_for_launch" not in text
            and "auth/github_credentials" not in str(path)
        ):
            rel = str(path.relative_to(REPO_ROOT))
            if f"`{rel}`" not in record:
                offenders.append(rel)
    assert not offenders, (
        "Global credential-resolution call sites missing from the Slice-0 "
        f"inventory (docs/tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md §10.2): {offenders}"
    )


def test_fixtures_honor_schema_semantics_without_fake_values() -> None:
    raw = _read(FIXTURES_DOC)
    for shape in SECRET_SHAPES:
        assert not shape.search(raw), f"fixture leaks secret-shaped value: {shape.pattern}"
    assert "GITHUB_TOKEN" not in raw
    assert "password" not in raw.lower()
    fixtures = yaml.safe_load(raw)
    assert isinstance(fixtures, list) and len(fixtures) >= 3
    by_name = {item["name"]: item for item in fixtures}

    scratch = by_name["scratch-report"]
    assert scratch["workspaceSource"]["kind"] == "scratch"
    assert "repository" not in scratch["workspaceSource"]
    assert "connectionRef" not in raw.split("name: scratch-report")[1].split("- name:")[0]

    anonymous = by_name["anonymous-public-read"]
    repo = anonymous["workspaceSource"]["repository"]
    assert anonymous["workspaceSource"]["kind"] == "repository"
    assert repo["accessMode"] == "anonymous"
    assert "connectionRef" not in repo
    assert repo["repository"]["name"] == "MoonLadderStudios/MoonMind"

    routed = by_name["routed-default"]
    assert routed["workspaceSource"]["repository"]["accessMode"] == "routed"
    for item in fixtures:
        assert item["publish"]["mode"] == "none"


def test_design_plan_cross_links_hold() -> None:
    assert "RepositoryAccessAndWorkspaceDecouplingPlan.md" in _read(DESIGN_DOC)
    assert "RepositoryAccessAndWorkspaceDesign.md" in _read(PLAN_DOC)


def test_no_second_target_domain_in_code() -> None:
    offenders = []
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "SourceControlConnection" in text or "RepositoryTargetV2" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"forbidden parallel domain found in: {offenders}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
