from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import sync_moonspec  # noqa: E402


def _make_bundle(root: Path) -> Path:
    source_root = root / "moonspec"
    bundle = source_root / "bundle"
    (bundle / "skills" / "moonspec-verify" / "agents").mkdir(parents=True)
    (bundle / "skills" / "moonspec-verify" / "SKILL.md").write_text(
        "---\nname: moonspec-verify\n---\n# Verify\n", encoding="utf-8"
    )
    (bundle / "skills" / "moonspec-verify" / "agents" / "openai.yaml").write_text(
        "wrapper: true\n", encoding="utf-8"
    )
    (bundle / "scripts" / "bash").mkdir(parents=True)
    (bundle / "scripts" / "bash" / "setup-plan.sh").write_text(
        "#!/usr/bin/env bash\necho plan\n", encoding="utf-8"
    )
    (bundle / "commands" / "gemini").mkdir(parents=True)
    (bundle / "commands" / "gemini" / "moonspec.verify.toml").write_text(
        "description = 'verify'\n", encoding="utf-8"
    )
    (bundle / "projections").mkdir()
    (bundle / "projections" / "moonmind.yaml").write_text(
        "schemaVersion: 1\n"
        "consumer: moonmind\n"
        "mappings:\n"
        "  - from: skills/\n"
        "    to: .agents/skills/\n"
        "    mode: directory\n"
        "  - from: scripts/bash/\n"
        "    to: .specify/scripts/bash/\n"
        "    mode: directory\n"
        "  - from: commands/gemini/\n"
        "    to: .gemini/commands/\n"
        "    mode: directory\n"
        "unexpectedLegacy:\n"
        "  - .gemini/commands/speckit.*.toml\n",
        encoding="utf-8",
    )
    (bundle / "moonspec.bundle.yaml").write_text(
        "schemaVersion: 1\n"
        "name: moonspec\n"
        "identity:\n"
        "  canonicalPrefix: moonspec\n"
        "  skillPrefix: moonspec-\n"
        "  commandPrefix: /moonspec.\n"
        "projections:\n"
        "  moonmind:\n"
        "    path: projections/moonmind.yaml\n",
        encoding="utf-8",
    )
    return source_root


def _run(root: Path, *flags: str) -> int:
    source_root = root / "moonspec"
    return sync_moonspec.main(
        ["--source", str(source_root), "--repo-root", str(root), *flags]
    )


def test_write_vendors_real_files_and_check_passes(tmp_path, capsys):
    _make_bundle(tmp_path)

    assert _run(tmp_path, "--write") == 0

    skill_md = tmp_path / ".agents" / "skills" / "moonspec-verify" / "SKILL.md"
    assert skill_md.is_file() and not skill_md.is_symlink()
    assert "name: moonspec-verify" in skill_md.read_text(encoding="utf-8")
    script = tmp_path / ".specify" / "scripts" / "bash" / "setup-plan.sh"
    assert script.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert stat.S_IMODE(script.stat().st_mode) == 0o755
    assert (tmp_path / ".gemini" / "commands" / "moonspec.verify.toml").is_file()

    capsys.readouterr()
    assert _run(tmp_path, "--check") == 0
    assert "MoonSpec projection is current" in capsys.readouterr().out


def test_check_reports_missing_and_content_drift(tmp_path, capsys):
    _make_bundle(tmp_path)
    assert _run(tmp_path, "--write") == 0
    skill_md = tmp_path / ".agents" / "skills" / "moonspec-verify" / "SKILL.md"
    skill_md.write_text("hand edited\n", encoding="utf-8")
    (tmp_path / ".gemini" / "commands" / "moonspec.verify.toml").unlink()

    assert _run(tmp_path, "--check") == 1

    err = capsys.readouterr().err
    assert "content differs from moonspec/bundle" in err
    assert "missing: .gemini/commands/moonspec.verify.toml" in err


def test_check_reports_mode_drift(tmp_path, capsys):
    _make_bundle(tmp_path)
    assert _run(tmp_path, "--write") == 0
    script = tmp_path / ".specify" / "scripts" / "bash" / "setup-plan.sh"
    script.chmod(0o644)

    assert _run(tmp_path, "--check") == 1

    err = capsys.readouterr().err
    assert (
        "mode differs from moonspec projection: "
        ".specify/scripts/bash/setup-plan.sh (000644 != 000755)"
    ) in err


def test_check_rejects_symlinked_targets(tmp_path, capsys):
    source_root = _make_bundle(tmp_path)
    skill_dir = tmp_path / ".agents" / "skills" / "moonspec-verify"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").symlink_to(
        source_root / "bundle" / "skills" / "moonspec-verify" / "SKILL.md"
    )

    assert _run(tmp_path, "--check") == 1
    assert "symlink (must be a vendored real file)" in capsys.readouterr().err


def test_write_replaces_symlinks_with_real_files(tmp_path):
    source_root = _make_bundle(tmp_path)
    skill_dir = tmp_path / ".agents" / "skills" / "moonspec-verify"
    skill_dir.mkdir(parents=True)
    link = skill_dir / "SKILL.md"
    link.symlink_to(
        source_root / "bundle" / "skills" / "moonspec-verify" / "SKILL.md"
    )

    assert _run(tmp_path, "--write") == 0

    assert not link.is_symlink() and link.is_file()
    # The bundle source must never be clobbered by writing through the link.
    assert "name: moonspec-verify" in (
        source_root / "bundle" / "skills" / "moonspec-verify" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_write_prunes_stale_projection_managed_files_only(tmp_path):
    _make_bundle(tmp_path)
    assert _run(tmp_path, "--write") == 0

    stale_skill = tmp_path / ".agents" / "skills" / "moonspec-removed"
    stale_skill.mkdir(parents=True)
    (stale_skill / "SKILL.md").write_text("old\n", encoding="utf-8")
    foreign_skill = tmp_path / ".agents" / "skills" / "pr-resolver"
    foreign_skill.mkdir(parents=True)
    (foreign_skill / "SKILL.md").write_text("native\n", encoding="utf-8")
    stale_template = tmp_path / ".specify" / "scripts" / "bash" / "old.sh"
    stale_template.write_text("old\n", encoding="utf-8")
    legacy = tmp_path / ".gemini" / "commands" / "speckit.verify.toml"
    legacy.write_text("legacy\n", encoding="utf-8")
    foreign_command = tmp_path / ".gemini" / "commands" / "other.toml"
    foreign_command.write_text("keep\n", encoding="utf-8")

    assert _run(tmp_path, "--write") == 0

    assert not stale_skill.exists()
    assert not stale_template.exists()
    assert not legacy.exists()
    assert (foreign_skill / "SKILL.md").is_file()
    assert foreign_command.is_file()


def test_write_prunes_duplicate_stale_matches_once(tmp_path):
    source_root = _make_bundle(tmp_path)
    recipe = source_root / "bundle" / "projections" / "moonmind.yaml"
    recipe.write_text(
        recipe.read_text(encoding="utf-8")
        + "  - .specify/scripts/bash/legacy.sh\n",
        encoding="utf-8",
    )
    assert _run(tmp_path, "--write") == 0
    legacy = tmp_path / ".specify" / "scripts" / "bash" / "legacy.sh"
    legacy.write_text("old\n", encoding="utf-8")

    assert _run(tmp_path, "--write") == 0

    assert not legacy.exists()


def test_plan_fails_fast_on_unclassified_directory_mapping(tmp_path, capsys):
    source_root = _make_bundle(tmp_path)
    recipe = source_root / "bundle" / "projections" / "moonmind.yaml"
    recipe.write_text(
        "schemaVersion: 1\n"
        "consumer: moonmind\n"
        "mappings:\n"
        "  - from: skills/\n"
        "    to: .claude/commands/\n"
        "    mode: directory\n",
        encoding="utf-8",
    )

    assert _run(tmp_path, "--check") == 1
    assert "no ownership rule" in capsys.readouterr().err


def test_plan_rejects_mapping_escaping_repo_root(tmp_path):
    source_root = _make_bundle(tmp_path)
    recipe = source_root / "bundle" / "projections" / "moonmind.yaml"
    recipe.write_text(
        "schemaVersion: 1\n"
        "consumer: moonmind\n"
        "mappings:\n"
        "  - from: skills/\n"
        "    to: ../outside/\n"
        "    mode: directory\n",
        encoding="utf-8",
    )

    assert _run(tmp_path, "--check") == 1
    assert not (tmp_path.parent / "outside").exists()


def test_missing_bundle_manifest_reports_submodule_hint(tmp_path, capsys):
    assert _run(tmp_path, "--check") == 1
    assert "git submodule update --init moonspec" in capsys.readouterr().err


def test_repo_projection_matches_pinned_bundle():
    """The committed vendored files must match the pinned moonspec bundle.

    Guards the same invariant as the CI drift gate so a stale vendored copy
    fails the unit suite even when the moonspec-projection job is skipped.
    """
    if not (REPO_ROOT / "moonspec" / "bundle" / "moonspec.bundle.yaml").is_file():
        pytest.skip("moonspec submodule not initialized")

    plan = sync_moonspec._plan(REPO_ROOT / "moonspec", "moonmind", REPO_ROOT)
    drift = sync_moonspec._drift(plan, REPO_ROOT)
    assert drift == [], (
        "vendored MoonSpec files drifted from the pinned bundle; run "
        "'git submodule update --init moonspec' then "
        f"'python3 tools/sync_moonspec.py --write': {drift}"
    )
