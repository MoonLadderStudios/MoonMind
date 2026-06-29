from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "sync_moonspec_submodule.py"
)
SPEC = importlib.util.spec_from_file_location("sync_moonspec_submodule", MODULE_PATH)
assert SPEC is not None
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
assert SPEC.loader is not None
SPEC.loader.exec_module(mod)


def test_projection_plan_reads_moonspec_submodule_manifest() -> None:
    files, unexpected = mod._planned_files(mod.DEFAULT_SOURCE, "moonmind")
    targets = {item.target.relative_to(mod.REPO_ROOT).as_posix() for item in files}

    assert ".agents/skills/moonspec-doc-reconcile/agents/openai.yaml" in targets
    assert ".gemini/commands/moonspec.orchestrate.toml" in targets
    assert ".gemini/commands/speckit.*.toml" in unexpected


def test_top_level_directory_projection_manages_target_directory() -> None:
    files, _unexpected = mod._planned_files(mod.DEFAULT_SOURCE, "moonmind")
    gemini_command = next(
        item
        for item in files
        if item.target.relative_to(mod.REPO_ROOT).as_posix()
        == ".gemini/commands/moonspec.orchestrate.toml"
    )

    assert gemini_command.managed_root.relative_to(mod.REPO_ROOT).as_posix() == (
        ".gemini/commands"
    )


def test_markdown_header_preserves_skill_front_matter() -> None:
    text = "---\nname: moonspec-test\n---\n# Body\n"
    result = mod._with_header(
        Path("SKILL.md"),
        Path("skills/moonspec-test/SKILL.md"),
        text,
    )

    assert result.startswith("---\nname: moonspec-test\n---\n")
    assert (
        "Generated from vendor/moonspec/bundle/skills/moonspec-test/SKILL.md"
        in result
    )


def test_shell_header_preserves_shebang() -> None:
    text = "#!/usr/bin/env bash\nset -e\n"
    result = mod._with_header(
        Path("check-prerequisites.sh"),
        Path("scripts/bash/check-prerequisites.sh"),
        text,
    )

    assert result.startswith("#!/usr/bin/env bash\n# Generated from")
