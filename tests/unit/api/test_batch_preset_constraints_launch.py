"""Rendered batch preset commands deliver exactly the current constraints.

Each batch preset expands to a helper invocation; these tests parse that
rendered command with the real helper parser so an earlier constraints file
left in the workspace cannot reach a new child (MoonLadderStudios/MoonMind#4264,
#4274).
"""

from __future__ import annotations

import json
import runpy
import shlex
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESETS_DIR = _REPO_ROOT / "api_service" / "data" / "presets"
_CONSTRAINTS_FILE = "artifacts/batch-workflows-constraints.txt"

_PRESET_INPUTS = {
    "batch-workflows": {
        "jira_project_key": "MM",
        "jira_status": "In Progress",
        "run_ref": "preset:jira-implement",
    },
    "batch-github-workflows": {
        "issue_range": "3142-3150",
        "run_ref": "preset:github-issue-implement",
        "repository": "MoonLadderStudios/MoonMind",
    },
}


@asynccontextmanager
async def _catalog_service(tmp_path: Path, slug: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/{slug}.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    seed_dir = tmp_path / "presets"
    seed_dir.mkdir()
    shutil.copy(_PRESETS_DIR / f"{slug}.yaml", seed_dir / f"{slug}.yaml")
    try:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=seed_dir)
            yield service
    finally:
        await engine.dispose()


def _rendered_helper_argv(instructions: str) -> list[str]:
    lines = instructions.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip().startswith('python3 "$MOONMIND_ACTIVE_SKILLS_DIR')
    )
    command: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        continued = stripped.endswith("\\")
        command.append(stripped.removesuffix("\\"))
        if not continued:
            break
    return shlex.split(" ".join(command))[2:]


def _load_helper(slug: str) -> dict[str, Any]:
    return runpy.run_path(
        str(_REPO_ROOT / ".agents" / "skills" / slug / "bin" / "batch_workflows.py")
    )


async def _read_launch_constraints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slug: str, constraints: str
) -> str:
    async with _catalog_service(tmp_path, slug) as service:
        expanded = await service.expand_template(
            slug=slug,
            scope="global",
            scope_ref=None,
            inputs={**_PRESET_INPUTS[slug], "constraints": constraints},
        )
    helper = _load_helper(slug)
    args = helper["_parse_args"](
        _rendered_helper_argv(expanded["steps"][0]["instructions"])
    )
    monkeypatch.chdir(tmp_path)
    return helper["_read_constraints"](args)


@pytest.mark.parametrize("slug", sorted(_PRESET_INPUTS))
async def test_empty_constraints_ignore_earlier_constraints_file(
    tmp_path, monkeypatch, slug
):
    stale = tmp_path / _CONSTRAINTS_FILE
    stale.parent.mkdir(parents=True)
    stale.write_text("Earlier run: only touch docs/", encoding="utf-8")

    assert await _read_launch_constraints(tmp_path, monkeypatch, slug, "") == ""


@pytest.mark.parametrize("slug", sorted(_PRESET_INPUTS))
async def test_empty_constraints_launch_without_constraints_file(
    tmp_path, monkeypatch, slug
):
    assert not (tmp_path / _CONSTRAINTS_FILE).exists()

    assert await _read_launch_constraints(tmp_path, monkeypatch, slug, "") == ""


@pytest.mark.parametrize("slug", sorted(_PRESET_INPUTS))
async def test_constraints_reach_helper_through_materialized_file(
    tmp_path, monkeypatch, slug
):
    value = 'a"; $(touch /tmp/pwned) # `tick` $HOME\nnewline ☃'
    materialized = tmp_path / _CONSTRAINTS_FILE
    materialized.parent.mkdir(parents=True)
    materialized.write_text(value, encoding="utf-8")

    assert await _read_launch_constraints(tmp_path, monkeypatch, slug, value) == value


def _rendered_helper_command(instructions: str) -> str:
    lines = instructions.splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip().startswith('python3 "$MOONMIND_ACTIVE_SKILLS_DIR')
    )
    command: list[str] = []
    for line in lines[start:]:
        command.append(line.strip())
        if not line.strip().endswith("\\"):
            break
    return "\n".join(command)


def _run_rendered_command(tmp_path: Path, command: str) -> list[str]:
    """Execute the rendered launch in a real shell with a recording python3."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv.json"
    recorder = bin_dir / "python3"
    recorder.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    recorder.chmod(0o755)
    subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "MOONMIND_ACTIVE_SKILLS_DIR": str(tmp_path / "skills"),
            "MOONMIND_REPOSITORY_CONNECTION_REF": "conn-1",
        },
        check=True,
        timeout=30,
    )
    return json.loads(argv_log.read_text(encoding="utf-8"))


async def _expanded_command(tmp_path: Path, slug: str, inputs: dict[str, Any]) -> str:
    async with _catalog_service(tmp_path, slug) as service:
        expanded = await service.expand_template(
            slug=slug,
            scope="global",
            scope_ref=None,
            inputs={**_PRESET_INPUTS[slug], **inputs},
        )
    return _rendered_helper_command(expanded["steps"][0]["instructions"])


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize("slug", sorted(_PRESET_INPUTS))
async def test_hostile_inputs_reach_helper_as_literal_data(tmp_path, slug):
    marker = tmp_path / "pwned"
    hostile = {
        "max_workflows": f"$(touch {marker}-max)",
        "repository": f"o/r$(touch {marker}-repo)`touch {marker}-tick`",
    }
    if slug == "batch-github-workflows":
        hostile["issue_range"] = f'1-2"; touch {marker}-range; echo "'

    argv = _run_rendered_command(
        tmp_path, await _expanded_command(tmp_path, slug, hostile)
    )

    assert sorted(path.name for path in tmp_path.glob("pwned*")) == []
    assert _flag_value(argv, "--max-workflows") == hostile["max_workflows"]
    if slug == "batch-github-workflows":
        assert _flag_value(argv, "--github-issue-range") == hostile["issue_range"]
        assert _flag_value(argv, "--github-repository") == hostile["repository"]
        assert _flag_value(argv, "--repository-connection-ref") == "conn-1"


@pytest.mark.parametrize("slug", sorted(_PRESET_INPUTS))
async def test_explicit_empty_max_workflows_matches_omitted_default(tmp_path, slug):
    helper = _load_helper(slug)
    parsed = {}
    for label, inputs in (("omitted", {}), ("empty", {"max_workflows": ""})):
        workdir = tmp_path / label
        workdir.mkdir()
        command = await _expanded_command(workdir, slug, inputs)
        argv = _run_rendered_command(workdir, command)
        parsed[label] = helper["_parse_args"](argv).max_workflows

    assert parsed == {"omitted": 25, "empty": 25}
