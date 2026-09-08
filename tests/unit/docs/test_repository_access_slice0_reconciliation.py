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
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from _semantic_docs_3964 import assert_semantic_present
from moonmind.omnigent.conformance import SECRET_PATTERN as SHARED_SECRET_PATTERN

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

# Optional-token and environment-delivery sites (§10.2.2 G-rows / §10.2.3
# E-rows). A file taking an explicit `github_token` option or injecting
# `GH_TOKEN`/`GITHUB_TOKEN` into a child/process environment must be
# inventoried just like a global-resolver call site, so an explicit-token
# consumer such as the OAuth host cannot be left on raw token delivery
# while the listed ports cut over.
OPTIONAL_TOKEN_PATTERN = re.compile(r"\bgithub_token\b")
ENV_DELIVERY_PATTERN = re.compile(r"\bGH_TOKEN\b|\bGITHUB_TOKEN\b")
ENV_DELIVERY_SINK_PATTERN = re.compile(
    r"child_env|build_github_token_git_environment|os\.environ|env\["
)

# Pure scan/redaction helpers and migrations carry token patterns without
# delivering credentials; they are not delivery sites.
SCAN_ONLY_MARKERS = (
    "scrub_github_tokens",
    "redact_secrets",
    "SECRET_PATTERN",
    "_GITHUB_TOKEN_PATTERN",
    "assert_secret_free",
)

SECRET_SHAPES = (
    SHARED_SECRET_PATTERN,
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
    # The design must keep stating its handoff is proposed, not permitted
    # (#3964: exact prose loosened to the gate contract).
    assert_semantic_present(
        text, ("may already be bypassed",), context="design handoff gate"
    )
    assert_semantic_present(
        text,
        ("proposed changes to the owning contracts and guidance",),
        context="design handoff scope",
    )
    # Slice-0 enables no runtime path.
    assert_semantic_present(
        _read(PLAN_DOC),
        ("no runtime qualification is claimed",),
        context="slice-0 runtime scope",
    )


def test_agents_recovery_gate_stays_mandatory() -> None:
    text = _read(AGENTS_DOC)
    # Remote-checkpoint recovery gate (#3964: exact sentence loosened; the
    # production behavior is owned by the recovery path, this pins the
    # documented gate).
    assert_semantic_present(
        text,
        ("remotely verified recovery checkpoint", "cleanup"),
        context="AGENTS.md recovery gate",
    )


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
    # The §10.7 aggregate identifier list alone is not ownership evidence:
    # every claim must resolve to a structured owner/record outside it
    # (§1 traceability rows or the §10.2–§10.6 owning records).
    structured_plan = plan.split("### 10.7")[0]
    for claim in EXPECTED_CLAIMS:
        assert claim in design, f"{claim} missing from design"
        assert claim in structured_plan, (
            f"{claim} appears only in the §10.7 aggregate list, not in a "
            "structured §1/§10.2–§10.6 owner/record mapping"
        )


def test_inventory_paths_exist() -> None:
    text = _read(PLAN_DOC)
    record = text.split("## 10. Slice-0 reconciliation record")[1]
    paths = sorted(set(re.findall(r"`((?:moonmind|api_service|frontend)/[^`]+?\.py)`", record)))
    assert paths, "Slice-0 record must cite exact module paths"
    for rel in paths:
        assert (REPO_ROOT / rel).exists(), f"inventoried path does not exist: {rel}"


def _is_scan_only(text: str, path: Path) -> bool:
    if "migrations" in str(path).replace("\\", "/"):
        return True
    return any(marker in text for marker in SCAN_ONLY_MARKERS)


def _is_token_delivery_site(text: str) -> bool:
    """Detect optional-token params and token env-delivery sinks."""
    if not OPTIONAL_TOKEN_PATTERN.search(text):
        has_token = False
    else:
        has_token = True
    has_env = bool(ENV_DELIVERY_PATTERN.search(text))
    if not (has_token or has_env):
        return False
    # An explicit `github_token` option counts even without env injection;
    # env material counts when it reaches a delivery sink.
    if has_token and not has_env:
        return True
    return bool(ENV_DELIVERY_SINK_PATTERN.search(text))


def test_global_resolver_call_sites_are_inventoried() -> None:
    record = _read(PLAN_DOC).split("## 10. Slice-0 reconciliation record")[1]
    offenders: list[str] = []
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _is_scan_only(text, path):
            continue
        is_resolver_site = (
            any(symbol in text for symbol in GLOBAL_RESOLVER_SYMBOLS)
            and "def resolve_github_credential" not in text
            and "def resolve_github_token_for_launch" not in text
            and "auth/github_credentials" not in str(path)
        )
        is_delivery_site = _is_token_delivery_site(text)
        if not (is_resolver_site or is_delivery_site):
            continue
        # Typed secret-ref resolution is not global discovery.
        if "auth/secret_refs" in str(path).replace("\\", "/"):
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if f"`{rel}`" not in record:
            offenders.append(rel)
    assert not offenders, (
        "Credential-resolution or explicit-token/environment-delivery call "
        "sites missing from the Slice-0 "
        f"inventory (docs/tmp/RepositoryAccessAndWorkspaceDecouplingPlan.md §10.2): {offenders}"
    )


def test_fixtures_honor_schema_semantics_without_fake_values() -> None:
    raw = _read(FIXTURES_DOC)
    # Shared repository secret scanner: covers ghp_, github_pat_, AIza,
    # ATATT, AKIA, private-key blocks, and token=/password=/secret
    # assignments per the repository credential guardrails.
    assert not SHARED_SECRET_PATTERN.search(raw), "fixture leaks secret-shaped value"
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
    assert "repository" not in scratch
    assert "connectionRef" not in raw.split("name: scratch-report")[1].split("- name:")[0]

    anonymous = by_name["anonymous-public-read"]
    # Canonical authoring keeps the authored repository as a top-level
    # sibling of `workspaceSource` (design §3 shapes), never nested inside it.
    assert anonymous["workspaceSource"] == {"kind": "repository"}
    repo = anonymous["repository"]
    assert repo["accessMode"] == "anonymous"
    assert "connectionRef" not in repo
    assert repo["repository"]["name"] == "MoonLadderStudios/MoonMind"

    routed = by_name["routed-default"]
    assert routed["workspaceSource"] == {"kind": "repository"}
    assert routed["repository"]["accessMode"] == "routed"
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
