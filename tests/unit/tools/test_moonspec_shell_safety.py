"""Exercise the checked-in MoonSpec shell projection as a consumer."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURE = "001-shell-safety"
GUIDANCE = """# Local guidance

## Active Technologies
- Existing language

## Recent Changes
- old one
- old two
- old three
<!-- MANUAL ADDITIONS START -->
- manual one
- manual two
- manual three
<!-- MANUAL ADDITIONS END -->
"""


@pytest.fixture
def consumer(tmp_path):
    root = tmp_path / 'consumer "quoted"'
    root.mkdir()
    shutil.copytree(REPO_ROOT / ".specify", root / ".specify")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    plan = root / "specs" / FEATURE / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        "# Plan\n**Language/Version**: Python 3.12\n"
        "**Primary Dependencies**: pytest\n**Storage**: N/A\n"
        "**Project Type**: single\n"
    )
    return root


def run(root, script, *args, env=None):
    return subprocess.run(
        ["bash", str(root / ".specify/scripts/bash" / script), *args],
        cwd=root,
        env={**os.environ, "SPECIFY_FEATURE": FEATURE, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("template", [True, False])
def test_setup_emits_only_valid_lossless_json(consumer, template):
    if not template:
        (consumer / ".specify/templates/plan-template.md").unlink()
    result = run(consumer, "setup-plan.sh", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "FEATURE_SPEC": str(consumer / "specs" / FEATURE / "spec.md"),
        "IMPL_PLAN": str(consumer / "specs" / FEATURE / "plan.md"),
        "SPECS_DIR": str(consumer / "specs" / FEATURE),
        "BRANCH": FEATURE,
        "HAS_GIT": "true",
    }
    assert result.stderr


def test_feature_assignments_do_not_execute_branch_contents(consumer):
    branch = "002-feature';touch injected;#"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source .specify/scripts/bash/common.sh; eval "$(get_feature_paths)"; '
            'printf "%s\\0%s" "$CURRENT_BRANCH" "$FEATURE_DIR"',
        ],
        cwd=consumer,
        env={**os.environ, "SPECIFY_FEATURE": branch},
        capture_output=True,
        text=True,
        check=False,
    )
    assert not (consumer / "injected").exists()
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\0") == [
        branch,
        str(consumer / "specs" / branch),
    ]


@pytest.mark.parametrize("args", [(), ("claude",)])
def test_context_updates_require_explicit_write(consumer, args):
    target = consumer / "CLAUDE.md"
    target.write_text(GUIDANCE)
    before = target.read_bytes(), target.stat().st_mtime_ns
    result = run(consumer, "update-agent-context.sh", *args)
    assert result.returncode == 0, result.stderr
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before


def test_authorized_context_update_preserves_symlink_metadata_and_manual_text(consumer):
    target = consumer / "context.md"
    target.write_text(GUIDANCE)
    target.chmod(0o640)
    link = consumer / "CLAUDE.md"
    link.symlink_to(target.name)
    result = run(consumer, "update-agent-context.sh", "--write", "claude")
    assert result.returncode == 0, result.stderr
    assert link.is_symlink()
    assert os.readlink(link) == target.name
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    content = target.read_text()
    assert "Python 3.12 + pytest" in content
    marker = "<!-- MANUAL ADDITIONS START -->"
    assert content[content.index(marker) :] == GUIDANCE[GUIDANCE.index(marker) :]
    assert "old three" not in content


def test_context_update_propagates_publish_failure(consumer):
    for name in ("CLAUDE.md", "GEMINI.md"):
        (consumer / name).write_text(GUIDANCE)
    commands = consumer / "fake-bin"
    commands.mkdir()
    (commands / "mv").write_text("#!/usr/bin/env bash\nexit 71\n")
    (commands / "mv").chmod(0o755)
    result = run(
        consumer,
        "update-agent-context.sh",
        "--write",
        env={"PATH": f"{commands}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert "Failed to update target file" in result.stderr
    assert "completed successfully" not in result.stdout
    for name in ("CLAUDE.md", "GEMINI.md"):
        assert (consumer / name).read_text() == GUIDANCE
    assert not list(consumer.rglob(".agent_update.*"))
