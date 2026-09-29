"""Consumer adoption of the MoonSpec verification bundle (MoonMind#4266).

Exercises the real resolver -> projection -> portable-helper boundary for the
newly admitted ``moonspec-verify`` snapshot: report preflight
(``validate-report``), resolved-Skill identity (``identity``), a stale
previously installed snapshot, and a missing helper. No instruction-wording
assertions: pure Skill wording is reviewed, not unit-tested.

Stale-helper provenance: ``tests/unit/workflows/skills/fixtures/
stale-moonspec-verify-acceptance.py`` holds the byte-identical helper projected
from ``moonspec@cd1c09b`` (the consumer pin before this issue's adoption).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from moonmind.workflows.skills.resolver import resolve_skill_markdown_path

ROOT = Path(__file__).resolve().parents[4]
PROJECTED_SKILL_DIR = ROOT / ".agents" / "skills" / "moonspec-verify"
STALE_HELPER = (
    Path(__file__).resolve().parent / "fixtures" / "stale-moonspec-verify-acceptance.py"
)

HELPER_REL = Path("scripts") / "acceptance.py"
POLICY_REL = Path("references") / "acceptance-policy.md"


def _load_helper(path: Path) -> ModuleType:
    module = ModuleType("acceptance_under_test")
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def _git(repo: Path, *args: str) -> str:
    return (
        subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)
        .decode()
        .strip()
    )


@pytest.fixture
def candidate_repo(tmp_path: Path) -> Path:
    """A real Git repository whose HEAD is also the completion target."""
    repo = tmp_path / "candidate"
    repo.mkdir()
    _git(repo, "init", "-b", "release")
    _git(repo, "config", "user.email", "adoption@example.test")
    _git(repo, "config", "user.name", "Adoption fixture")
    (repo / "app.py").write_text("def value():\n    return 42\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "implementation")
    return repo


@pytest.fixture
def fresh_snapshot(tmp_path: Path, monkeypatch) -> Path:
    """Newly admitted snapshot: a copy of the actually projected Skill."""
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    shutil.copytree(PROJECTED_SKILL_DIR, mirror / "moonspec-verify")
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.workflow.skills_local_mirror_root",
        str(mirror),
        raising=False,
    )
    return mirror / "moonspec-verify"


@pytest.fixture
def stale_snapshot(tmp_path: Path, monkeypatch) -> Path:
    """Previously installed snapshot: pre-adoption helper bytes, valid Skill."""
    mirror = tmp_path / "stale-mirror"
    skill_dir = mirror / "moonspec-verify"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: moonspec-verify\ndescription: stale\n---\n# Verify\n",
        encoding="utf-8",
    )
    shutil.copyfile(STALE_HELPER, skill_dir / HELPER_REL)
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.workflow.skills_local_mirror_root",
        str(mirror),
        raising=False,
    )
    return skill_dir


@pytest.fixture
def helperless_snapshot(tmp_path: Path, monkeypatch) -> Path:
    """Admitted snapshot missing its required helper asset."""
    mirror = tmp_path / "helperless-mirror"
    skill_dir = mirror / "moonspec-verify"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: moonspec-verify\ndescription: helperless\n---\n# Verify\n",
        encoding="utf-8",
    )
    shutil.copyfile(PROJECTED_SKILL_DIR / POLICY_REL, skill_dir / POLICY_REL)
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.workflow.skills_local_mirror_root",
        str(mirror),
        raising=False,
    )
    return skill_dir


def _current_and_report(helper: ModuleType, repo: Path, tmp_path: Path):
    current = helper.capture(repo, "example/repo", "release")
    source = b"Return 42. Keep value callable. Deployment is a separate operation."
    current["scope"] = helper.scope(
        source, "example/repo#1", ["AC-1", "AC-2"], complete=True
    )
    current["freshnessPolicy"] = "content"
    # Evidence refs point at genuinely executed commands, not invented output.
    rows = []
    for requirement, check in (
        ("AC-1", "import app; assert app.value() == 42"),
        ("AC-2", "import app; assert callable(app.value)"),
    ):
        result = subprocess.run(
            [sys.executable, "-c", check],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        log = tmp_path / f"{requirement}.json"
        log.write_text(
            json.dumps(
                {
                    "command": check,
                    "exitCode": result.returncode,
                    "subject": current["subject"],
                }
            ),
            encoding="utf-8",
        )
        rows.append({"requirementId": requirement, "evidenceRefs": [str(log)]})
    binding = {key: current[key] for key in ("subject", "scope", "completionTarget")}
    binding.update(
        schemaVersion="acceptance/v1", evidence=rows, freshness={"policy": "content"}
    )
    report = {
        "verdict": "FULLY_IMPLEMENTED",
        "recommendedNextAction": "advance",
        "recoverableInCurrentRuntime": True,
        "invalid": False,
        "degraded": False,
        "validatedRefs": {"acceptance": binding},
    }
    return current, report


def test_newly_admitted_snapshot_resolves_policy_and_helper(fresh_snapshot):
    skill_md = resolve_skill_markdown_path("moonspec-verify")
    assert skill_md is not None
    skill_dir = skill_md.parent
    assert (skill_dir / POLICY_REL).is_file()
    helper_path = skill_dir / HELPER_REL
    assert helper_path.is_file()

    helper = _load_helper(helper_path)
    for entrypoint in (
        "capture",
        "scope",
        "reuse",
        "validate_report",
        "skill_identity",
    ):
        assert callable(getattr(helper, entrypoint, None)), entrypoint


def test_report_preflight_accepts_representative_success(
    fresh_snapshot, candidate_repo, tmp_path
):
    helper = _load_helper(fresh_snapshot / HELPER_REL)
    current, report = _current_and_report(helper, candidate_repo, tmp_path)

    outcome = helper.validate_report(report, current)
    assert outcome == {"valid": True, "errors": []}
    assert helper.reuse(report, current)["reusable"] is True


def test_report_preflight_rejects_malformed_producer_output(fresh_snapshot):
    helper = _load_helper(fresh_snapshot / HELPER_REL)

    assert helper.validate_report(object(), None)["valid"] is False
    incompatible = {
        "verdict": "FULLY_IMPLEMENTED",
        "recommendedNextAction": "blocked",
        "recoverableInCurrentRuntime": True,
    }
    outcome = helper.validate_report(incompatible, None)
    assert outcome["valid"] is False
    assert any("recommendedNextAction" in error for error in outcome["errors"])
    hanging_success = dict(
        incompatible,
        recommendedNextAction="advance",
        remainingWork=[{"requirement": "R-1", "gapType": "x", "remainingWork": "y"}],
    )
    outcome = helper.validate_report(hanging_success, None)
    assert outcome["valid"] is False
    assert any("remaining work" in error for error in outcome["errors"])
    dangling = {
        "verdict": "ADDITIONAL_WORK_NEEDED",
        "recommendedNextAction": "reattempt_current_step",
        "recoverableInCurrentRuntime": False,
    }
    outcome = helper.validate_report(dangling, None)
    assert outcome["valid"] is False
    assert any("remainingWork" in error for error in outcome["errors"])


def test_preflight_cli_boundary_reports_and_identifies(fresh_snapshot, tmp_path):
    helper_path = fresh_snapshot / HELPER_REL
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "verdict": "ADDITIONAL_WORK_NEEDED",
                "recommendedNextAction": "blocked",
                "recoverableInCurrentRuntime": False,
                "remainingWork": [
                    {
                        "requirement": "AC-1",
                        "gapType": "implementation",
                        "remainingWork": "Implement AC-1.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(helper_path),
            "validate-report",
            "--report",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["valid"] is True

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"verdict": "FULLY_IMPLEMENTED", ', encoding="utf-8")
    failed = subprocess.run(
        [
            sys.executable,
            str(helper_path),
            "validate-report",
            "--report",
            str(malformed),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["valid"] is False

    identified = subprocess.run(
        [sys.executable, str(helper_path), "identity"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert identified.returncode == 0, identified.stderr
    identity = json.loads(identified.stdout)
    assert identity["skill"] == "moonspec-verify"
    assert identity["skillPath"] == str(fresh_snapshot)
    for name in (
        "SKILL.md",
        "references/acceptance-policy.md",
        "scripts/acceptance.py",
    ):
        digest = hashlib.sha256((fresh_snapshot / name).read_bytes()).hexdigest()
        assert identity["files"][name] == f"sha256:{digest}"


def test_stale_snapshot_cannot_masquerade_as_adopted(stale_snapshot):
    skill_md = resolve_skill_markdown_path("moonspec-verify")
    assert skill_md is not None
    assert skill_md.parent == stale_snapshot
    stale_helper = _load_helper(stale_snapshot / HELPER_REL)

    # Long-standing behavior survives; the preflight entrypoints do not exist.
    for entrypoint in ("capture", "scope", "reuse"):
        assert callable(getattr(stale_helper, entrypoint, None)), entrypoint
    for entrypoint in ("validate_report", "skill_identity", "read_report_json"):
        assert getattr(stale_helper, entrypoint, None) is None, entrypoint

    with pytest.raises(AttributeError):
        stale_helper.validate_report({}, None)

    stale_digest = hashlib.sha256(
        (stale_snapshot / HELPER_REL).read_bytes()
    ).hexdigest()
    fresh_digest = hashlib.sha256(
        (PROJECTED_SKILL_DIR / HELPER_REL).read_bytes()
    ).hexdigest()
    assert stale_digest != fresh_digest


def test_missing_helper_fails_explicitly_at_resolved_snapshot(helperless_snapshot):
    skill_md = resolve_skill_markdown_path("moonspec-verify")
    assert skill_md is not None
    helper_path = skill_md.parent / HELPER_REL

    # The only helper binding is the snapshot-local path: no silent fallback
    # to another snapshot's copy.
    assert helper_path.parent.parent == skill_md.parent
    assert not helper_path.exists()
    with pytest.raises(FileNotFoundError):
        helper_path.read_bytes()
