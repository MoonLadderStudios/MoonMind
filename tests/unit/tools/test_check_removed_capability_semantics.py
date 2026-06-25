from __future__ import annotations

from pathlib import Path

from tools.check_removed_capability_semantics import (
    check_removed_capability_semantics,
    iter_scanned_files,
    main,
)


def _old_camel() -> str:
    return "runtime" + "Capability" + "Version"


def _generic_camel() -> str:
    return "capability" + "Version"


def _old_words() -> str:
    return "capability" + " " + "version"


def test_guard_allows_name_only_preset_batch_skill_and_tool_versions(
    tmp_path: Path,
) -> None:
    (tmp_path / "presets").mkdir()
    (tmp_path / "batches").mkdir()
    (tmp_path / ".agents" / "skills" / "local").mkdir(parents=True)
    (tmp_path / "tools").mkdir()

    (tmp_path / "presets" / "jira.json").write_text(
        '{"presetSlug": "jira-orchestrate", "version": "1"}',
        encoding="utf-8",
    )
    (tmp_path / "batches" / "resolver.yaml").write_text(
        "preset-slug: batch-workflows\nversion: 1\n",
        encoding="utf-8",
    )
    (tmp_path / ".agents" / "skills" / "local" / "SKILL.md").write_text(
        "name: jira-implement\nmetadata:\n  version: local\n",
        encoding="utf-8",
    )
    (tmp_path / "tools" / "tool.json").write_text(
        '{"tool-name": "jira.get_issue", "version": "1"}',
        encoding="utf-8",
    )

    assert check_removed_capability_semantics(tmp_path) == []


def test_guard_allows_unrelated_hint_catalog_version(tmp_path: Path) -> None:
    (tmp_path / "runtime.ts").write_text(
        "const hintCatalogVersion = '2026-05-13';\n",
        encoding="utf-8",
    )

    assert check_removed_capability_semantics(tmp_path) == []


def test_guard_rejects_removed_runtime_semantic_patterns(tmp_path: Path) -> None:
    (tmp_path / "runtime.ts").write_text(
        "\n".join(
            [
                f"const stored = command.{_old_camel()};",
                f"const preview = config.{_generic_camel()};",
                "const words = '" + _old_words() + "';",
                "const hyphen = 'capability" + "-" + "version';",
                "const snake = 'capability" + "_" + "version';",
            ]
        ),
        encoding="utf-8",
    )

    findings = check_removed_capability_semantics(tmp_path)

    assert [finding.pattern for finding in findings] == [
        "runtime-camel",
        "generic-camel",
        "generic-words",
        "hyphenated",
        "snake",
    ]


def test_guard_scans_static_docs_seeds_and_fixtures(tmp_path: Path) -> None:
    for relative in (
        "docs/Steps/SlashCommands.md",
        "api_service/seeds/runtime.json",
        "tests/fixtures/workflow.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_old_camel(), encoding="utf-8")

    findings = check_removed_capability_semantics(tmp_path)

    assert sorted(str(finding.path) for finding in findings) == [
        "api_service/seeds/runtime.json",
        "docs/Steps/SlashCommands.md",
        "tests/fixtures/workflow.json",
    ]


def test_guard_excludes_transient_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "context.json"
    artifact.parent.mkdir()
    artifact.write_text(_old_camel(), encoding="utf-8")

    assert list(iter_scanned_files(tmp_path)) == []
    assert check_removed_capability_semantics(tmp_path) == []


def test_guard_cli_reports_static_rejection(tmp_path: Path, capsys) -> None:
    (tmp_path / "frontend.ts").write_text(_generic_camel(), encoding="utf-8")

    exit_code = main(["--root", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "MM-917" in output
    assert "frontend.ts:1" in output
    assert "generic-camel" in output
