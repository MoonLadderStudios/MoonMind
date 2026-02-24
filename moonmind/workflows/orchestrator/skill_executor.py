"""Execute orchestrator-selected skills from shared mirrors."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _resolve_workspace_root() -> Path:
    raw = (
        os.getenv("WORKFLOW_REPO_ROOT")
        or os.getenv("SPEC_WORKFLOW_REPO_ROOT")
        or os.getenv("WORKSPACE_ROOT")
        or ""
    ).strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (Path.cwd() / root).resolve()
        return root.resolve()
    return Path.cwd().resolve()


def _resolve_skill_roots(workspace_root: Path) -> tuple[Path, ...]:
    local_root_raw = (
        os.getenv("WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT")
        or os.getenv("SPEC_SKILLS_LOCAL_MIRROR_ROOT")
        or f"{workspace_root}/.agents/skills/local"
    )
    legacy_root_raw = (
        os.getenv("WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT")
        or os.getenv("SPEC_SKILLS_LEGACY_MIRROR_ROOT")
        or f"{workspace_root}/.agents/skills"
    )

    roots: list[Path] = []
    for raw in (local_root_raw, legacy_root_raw):
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (workspace_root / candidate).resolve()
        candidate = candidate.resolve()
        nested = (candidate / "skills").resolve()
        for entry in (candidate, nested):
            if entry not in roots:
                roots.append(entry)

    codex_home_raw = (os.getenv("CODEX_HOME") or "").strip()
    if codex_home_raw:
        codex_root = Path(codex_home_raw).expanduser()
        if not codex_root.is_absolute():
            codex_root = (workspace_root / codex_root).resolve()
        codex_skills = (codex_root.resolve() / "skills").resolve()
        if codex_skills not in roots:
            roots.append(codex_skills)

    return tuple(roots)


def _resolve_skill_path(skill_id: str, workspace_root: Path) -> Path:
    _validate_skill_id(skill_id)
    checked_roots: list[str] = []
    for root in _resolve_skill_roots(workspace_root):
        checked_roots.append(str(root))
        candidate = root / skill_id
        if candidate.is_dir() and (candidate / "SKILL.md").is_file():
            return candidate.resolve()
    joined = ", ".join(checked_roots) if checked_roots else "<none>"
    raise RuntimeError(
        f"Skill '{skill_id}' was not found in configured mirrors ({joined})."
    )


def _resolve_repo_path(workspace_root: Path, skill_args: Mapping[str, Any]) -> Path:
    repo_raw = (
        skill_args.get("repo")
        or skill_args.get("repo_path")
        or skill_args.get("repository")
        or "."
    )
    candidate = Path(str(repo_raw)).expanduser()
    if not candidate.is_absolute():
        candidate = (workspace_root / candidate).resolve()
    if not candidate.exists() or not candidate.is_dir():
        raise RuntimeError(f"Resolved repo path does not exist: {candidate}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"Resolved repo path must remain inside workspace root: {workspace_root}"
        ) from exc
    return resolved


def _detect_script(skill_path: Path, skill_id: str) -> Path:
    candidates = (
        skill_path / "scripts" / "run.sh",
        skill_path / "scripts" / f"run-{skill_id}.sh",
        skill_path / "scripts" / f"{skill_id}.sh",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        f"No runnable script found for skill '{skill_id}' under {skill_path / 'scripts'}."
    )


def _append_flag(command: list[str], flag: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    command.extend([flag, text])


def _resolve_skill_command(
    *,
    script_path: Path,
    skill_id: str,
    repo_path: Path,
    skill_args: Mapping[str, Any],
) -> tuple[list[str], Path]:
    if str(skill_args.get("command") or "").strip():
        raise RuntimeError(
            "skill_args.command is not supported for orchestrator skill runs."
        )

    command = ["bash", str(script_path)]
    is_moonmind_update = skill_id in {
        "moonmind-update",
        "moonmind-update-workflow",
        "update-moonmind",
    } or script_path.name in {"run-moonmind-update.sh", "run-update-moonmind.sh"}

    if is_moonmind_update or {"repo", "repo_path", "repository"} & set(skill_args):
        _append_flag(command, "--repo", str(repo_path))
    if "branch" in skill_args:
        _append_flag(command, "--branch", skill_args.get("branch"))
    if "updateCommand" in skill_args or "update_command" in skill_args:
        raise RuntimeError(
            "Custom update commands are not supported for orchestrator skill runs."
        )
    if is_moonmind_update and bool(
        skill_args.get("allowDirty") or skill_args.get("allow_dirty")
    ):
        command.append("--allow-dirty")
    if is_moonmind_update and bool(
        skill_args.get("noComposePull") or skill_args.get("no_compose_pull")
    ):
        command.append("--no-compose-pull")
    if is_moonmind_update and bool(
        skill_args.get("dryRun") or skill_args.get("dry_run")
    ):
        command.append("--dry-run")

    return (command, repo_path)


def _parse_skill_args(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid --skill-args-json payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("--skill-args-json must decode to a JSON object.")
    return parsed


def _validate_skill_id(skill_id: str) -> str:
    normalized = str(skill_id or "").strip()
    if not _SKILL_ID_PATTERN.fullmatch(normalized):
        raise RuntimeError(
            "Invalid skill id. Allowed format: lowercase letters, numbers, hyphen, underscore."
        )
    return normalized


def is_runnable_skill(skill_id: str, workspace_root: Path | None = None) -> bool:
    root = workspace_root or _resolve_workspace_root()
    try:
        normalized = _validate_skill_id(skill_id)
        skill_path = _resolve_skill_path(normalized, root)
        _detect_script(skill_path, normalized)
    except RuntimeError:
        return False
    return True


def list_runnable_skill_names(workspace_root: Path | None = None) -> tuple[str, ...]:
    root = workspace_root or _resolve_workspace_root()
    seen: set[str] = set()
    runnable: list[str] = []
    for skills_root in _resolve_skill_roots(root):
        if not skills_root.is_dir():
            continue
        try:
            entries = sorted(skills_root.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name in seen:
                continue
            if not is_runnable_skill(entry.name, workspace_root=root):
                continue
            seen.add(entry.name)
            runnable.append(entry.name)
    return tuple(runnable)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve and execute a shared orchestrator skill."
    )
    parser.add_argument("--skill-id", required=True, help="Skill identifier")
    parser.add_argument(
        "--skill-args-json",
        default="{}",
        help="Optional JSON object of skill arguments",
    )
    args = parser.parse_args(argv)

    try:
        normalized_skill_id = _validate_skill_id(args.skill_id)
        workspace_root = _resolve_workspace_root()
        skill_args = _parse_skill_args(args.skill_args_json)
        skill_path = _resolve_skill_path(normalized_skill_id, workspace_root)
        repo_path = _resolve_repo_path(workspace_root, skill_args)
        script_path = _detect_script(skill_path, normalized_skill_id)
        command, cwd = _resolve_skill_command(
            script_path=script_path,
            skill_id=normalized_skill_id,
            repo_path=repo_path,
            skill_args=skill_args,
        )
    except RuntimeError as exc:
        print(f"skill-executor error: {exc}", file=sys.stderr)
        return 2

    process = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr)
    return int(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
