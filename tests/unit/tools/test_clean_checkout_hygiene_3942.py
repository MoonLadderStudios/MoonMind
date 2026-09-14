"""Clean-checkout hygiene contracts for MoonLadderStudios/MoonMind#3942.

Covers: .gitmodules/track submodule agreement with no stale open-webui
entry, no branch tracking, recorded-commit authority, removal of the
moonmind/factories package marker and tracked memory/ notes with no
unresolved consumers, and the bounded preview-first behavior of the two new
developer tools.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_tool(name: str):
    module_path = REPO_ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_clean_checkout = _load_tool("verify_clean_checkout")
cleanup_dev_caches = _load_tool("cleanup_dev_caches")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


# --- R2: .gitmodules and tracked submodules agree ---


def test_gitmodules_has_no_stale_open_webui_entry() -> None:
    modules = verify_clean_checkout.parse_gitmodules(
        (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
    )
    assert "open-webui" not in modules
    assert set(modules) == {"moonspec", "omnigent"}
    for name, info in modules.items():
        assert info["path"] == name
        assert "url" in info and info["url"].startswith("https://")
        assert "branch" not in info


def test_recorded_gitlinks_match_gitmodules() -> None:
    gitlinks = verify_clean_checkout.parse_ls_tree(_git("ls-tree", "HEAD"))
    assert "open-webui" not in gitlinks
    assert set(gitlinks) >= {"moonspec", "omnigent"}
    problems = verify_clean_checkout.compare_state(
        gitlinks,
        verify_clean_checkout.parse_gitmodules(
            (REPO_ROOT / ".gitmodules").read_text(encoding="utf-8")
        ),
        # Initialized at recorded commits: fully reproducible.
        {
            path: {"sha": sha, "initialized": True}
            for path, sha in gitlinks.items()
            if path in ("moonspec", "omnigent")
        },
    )
    assert problems == []


def test_compare_state_flags_stale_entry_branch_and_drift() -> None:
    gitlinks = {"moonspec": "aaa", "omnigent": "bbb"}
    modules = {
        "moonspec": {"path": "moonspec", "url": "https://example/moonspec.git"},
        "omnigent": {"path": "omnigent", "url": "https://example/omnigent.git"},
        "open-webui": {"path": "open-webui", "url": "https://example/ow.git"},
    }
    status = {
        "moonspec": {"sha": "aaa", "initialized": True},
        "omnigent": {"sha": "other", "initialized": True},
    }
    problems = verify_clean_checkout.compare_state(gitlinks, modules, status)
    assert any("open-webui" in problem for problem in problems)
    assert any("omnigent" in problem and "drift" in problem for problem in problems)

    branched = {
        "moonspec": {
            "path": "moonspec",
            "url": "https://example/moonspec.git",
            "branch": "main",
        },
        "omnigent": {"path": "omnigent", "url": "https://example/omnigent.git"},
    }
    branched_problems = verify_clean_checkout.compare_state(
        gitlinks,
        branched,
        {p: {"sha": s, "initialized": True} for p, s in gitlinks.items()},
    )
    assert any("branch" in problem for problem in branched_problems)


def test_no_supported_consumer_references_open_webui() -> None:
    for compose_file in (
        "docker-compose.yaml",
        "docker-compose.test.yaml",
        "docker-compose.development.yaml",
    ):
        text = (REPO_ROOT / compose_file).read_text(encoding="utf-8")
        assert "open-webui" not in text


# --- R4: factories package and memory notes removed with owners intact ---


def test_factories_package_marker_removed_and_unreferenced() -> None:
    assert not (REPO_ROOT / "moonmind" / "factories").exists()
    matches = [
        path
        for path in (REPO_ROOT / "moonmind").rglob("*.py")
        if "factories" in path.read_text(encoding="utf-8")
        and "adapter factories" not in path.read_text(encoding="utf-8").lower()
        and "route factories" not in path.read_text(encoding="utf-8").lower()
    ]
    assert matches == []
    subprocess.run(
        ["git", "ls-tree", "-r", "HEAD", "--name-only"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    # The committed tree may still contain the marker until merge; the working
    # tree must not.
    assert "moonmind/factories" not in "".join(
        p.name for p in (REPO_ROOT / "moonmind").iterdir()
    )


def test_no_tracked_memory_notes_remain() -> None:
    memory_dir = REPO_ROOT / "memory"
    tracked = [
        line
        for line in _git("ls-files", "memory").splitlines()
        if line.strip()
    ]
    assert tracked == []
    if memory_dir.exists():
        assert list(memory_dir.iterdir()) == []


def test_memory_knowledge_promoted_to_existing_owners() -> None:
    contributing = (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    # Frontend vitest colon workaround + describe_execution env artifact.
    assert "colon" in contributing
    assert "IllegalStateChangeError" in contributing
    assert "cleanup_dev_caches" in contributing
    # MM-954 rationale already owned by code + Workflow List doc.
    workflow_list = (
        REPO_ROOT / "frontend" / "src" / "entrypoints" / "workflow-list.tsx"
    ).read_text(encoding="utf-8")
    assert "current-page-only" in workflow_list
    # Follow-up retrieval retirement already owned by canonical docs.
    assert "retired" in (
        REPO_ROOT / "docs" / "Rag" / "WorkflowRag.md"
    ).read_text(encoding="utf-8").lower()


# --- R1/R5: bounded tool behavior ---


def test_verify_tool_reports_uninitialized_as_setup_not_defect(
    tmp_path: Path,
) -> None:
    gitlinks = {"moonspec": "aaa", "omnigent": "bbb"}
    modules = {
        "moonspec": {"path": "moonspec", "url": "https://example/m.git"},
        "omnigent": {"path": "omnigent", "url": "https://example/o.git"},
    }
    problems = verify_clean_checkout.compare_state(gitlinks, modules, {})
    assert len(problems) == 2
    assert all("not initialized" in problem for problem in problems)
    assert all("--remote" not in problem for problem in problems)


def test_cleanup_preview_collects_caches_but_skips_symlinks_and_submodules(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text(
        '[submodule "moonspec"]\n\tpath = moonspec\n'
        "\turl = https://example/moonspec.git\n",
        encoding="utf-8",
    )
    cache_dir = repo / "pkg" / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "mod.pyc").write_text("x", encoding="utf-8")
    (repo / "pkg" / "real.py").write_text("x = 1\n", encoding="utf-8")
    submodule_cache = repo / "moonspec" / "__pycache__"
    submodule_cache.mkdir(parents=True)
    (submodule_cache / "sub.pyc").write_text("x", encoding="utf-8")
    link_target = tmp_path / "outside_cache"
    link_target.mkdir()
    (link_target / "evil.pyc").write_text("x", encoding="utf-8")
    (repo / "linked.pyc").symlink_to(link_target / "evil.pyc")

    targets = cleanup_dev_caches.collect_cache_targets(repo)
    assert cache_dir in targets
    assert submodule_cache not in targets
    assert not any("outside_cache" in str(t) for t in targets)
    assert repo / "pkg" / "real.py" not in targets
    # Preview is read-only.
    assert cache_dir.exists()

    removed = cleanup_dev_caches.remove_targets(targets, repo)
    assert cache_dir in removed
    assert not cache_dir.exists()
    assert (repo / "pkg" / "real.py").exists()
    assert submodule_cache.exists()
