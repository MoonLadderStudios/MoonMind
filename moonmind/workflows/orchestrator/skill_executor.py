"""Execute orchestrator-selected skills from shared mirrors."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping

_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TASK_EXECUTION_CONTEXT_ENV = "MOONMIND_ORCHESTRATOR_TASK_STEP_EXECUTION"
_TASK_INSTRUCTIONS_ENV = "MOONMIND_ORCHESTRATOR_TASK_STEP_INSTRUCTIONS"


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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _resolve_compose_project(skill_args: Mapping[str, Any]) -> str | None:
    compose_project = skill_args.get("composeProject") or skill_args.get(
        "compose_project"
    )
    if compose_project:
        text = str(compose_project).strip()
        if text:
            return text

    env_project = (
        os.getenv("COMPOSE_PROJECT_NAME") or os.getenv("COMPOSE_PROJECT") or ""
    ).strip()
    return env_project or None


def _flag_enabled(skill_args: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        if key in skill_args:
            return _coerce_bool(skill_args.get(key))
    return False


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
    is_update_moonmind = (
        skill_id == "update-moonmind" or script_path.name == "run-update-moonmind.sh"
    )

    if is_update_moonmind or {"repo", "repo_path", "repository"} & set(skill_args):
        _append_flag(command, "--repo", str(repo_path))
    if "branch" in skill_args:
        _append_flag(command, "--branch", skill_args.get("branch"))
    if "updateCommand" in skill_args or "update_command" in skill_args:
        raise RuntimeError(
            "Custom update commands are not supported for orchestrator skill runs."
        )
    if is_update_moonmind and _flag_enabled(skill_args, "allowDirty", "allow_dirty"):
        command.append("--allow-dirty")
    if is_update_moonmind:
        compose_project = _resolve_compose_project(skill_args)
        _append_flag(command, "--compose-project", compose_project)
    if is_update_moonmind and _flag_enabled(
        skill_args, "noComposePull", "no_compose_pull"
    ):
        command.append("--no-compose-pull")
    if is_update_moonmind and _flag_enabled(skill_args, "dryRun", "dry_run"):
        command.append("--dry-run")
    if is_update_moonmind and _flag_enabled(
        skill_args, "restartOrchestrator", "restart_orchestrator"
    ):
        command.append("--restart-orchestrator")

    return (command, repo_path)


def _parse_json_object(raw: str, *, source: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid {source} payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{source} must decode to a JSON object.")
    return parsed


def _parse_skill_args(raw: str | None) -> dict[str, Any]:
    return _parse_json_object(raw or "", source="--skill-args-json")


def _resolve_step_execution_context() -> tuple[dict[str, Any], str]:
    raw = os.getenv(_TASK_EXECUTION_CONTEXT_ENV) or ""
    payload = _parse_json_object(raw, source="task-step execution context")
    instructions_raw = payload.get("instructions")
    instructions = (
        str(instructions_raw) if isinstance(instructions_raw, str) else ""
    ).strip()
    return payload, instructions


def _validate_skill_id(skill_id: str) -> str:
    normalized = str(skill_id or "").strip()
    if not _SKILL_ID_PATTERN.fullmatch(normalized):
        raise RuntimeError(
            "Invalid skill id. Allowed format: lowercase letters, numbers, hyphen, underscore."
        )
    return normalized


def _pin_pythonpath_to_repo_root(
    env: Mapping[str, str],
    repo_path: Path,
) -> dict[str, str]:
    """Return a process environment with repo path pinned first in PYTHONPATH."""

    process_env = dict(env)
    repo_root = str(repo_path.resolve())
    raw_pythonpath = str(process_env.get("PYTHONPATH", ""))
    entries = [entry for entry in raw_pythonpath.split(os.pathsep) if entry.strip()]
    normalized_repo = str(Path(repo_root).resolve())

    deduped: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            normalized = str(Path(entry).expanduser().resolve())
        except OSError:
            normalized = entry
        if normalized == normalized_repo:
            continue
        if normalized in seen:
            continue
        deduped.append(entry)
        seen.add(normalized)

    process_env["PYTHONPATH"] = os.pathsep.join([repo_root, *deduped])
    return process_env


def _extract_package_root(path: Path, package_name: str) -> Path | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part == package_name:
            return Path(*parts[: index + 1]).resolve()
    return None


def _analyze_import_probe(
    *,
    repo_path: Path,
    probe_payload: Mapping[str, Any],
) -> str | None:
    """Return warning text when the imported moonmind package root mismatches repo."""

    module_file_raw = str(probe_payload.get("module_file", "")).strip()
    if not module_file_raw:
        return None

    workspace_root = repo_path.resolve()
    workspace_package_root = (workspace_root / "moonmind").resolve()
    imported_package_root = _extract_package_root(
        Path(module_file_raw).expanduser().resolve(),
        "moonmind",
    )
    if imported_package_root is None:
        return None
    if imported_package_root == workspace_package_root:
        return None

    sys_path_entries = probe_payload.get("sys_path")
    if isinstance(sys_path_entries, list):
        sys_path_sample = ", ".join(str(item) for item in sys_path_entries[:8])
    else:
        sys_path_sample = "<unavailable>"

    has_workspace_entry = False
    has_app_entry = False
    if isinstance(sys_path_entries, list):
        for raw in sys_path_entries:
            text = str(raw or "").strip()
            if not text:
                continue
            with suppress(OSError):
                resolved = Path(text).expanduser().resolve()
                if resolved == workspace_root:
                    has_workspace_entry = True
            if text == "/app" or text.startswith("/app/"):
                has_app_entry = True

    qualifier = (
        "mixed module roots detected"
        if has_workspace_entry and has_app_entry
        else "module root mismatch detected"
    )
    return (
        "skill-executor warning: "
        f"{qualifier}; expected moonmind under {workspace_package_root}, "
        f"but imported from {imported_package_root} (module_file={module_file_raw}); "
        f"sys.path sample=[{sys_path_sample}]"
    )


def _runtime_import_probe(
    *,
    repo_path: Path,
    process_env: Mapping[str, str],
) -> str | None:
    """Probe moonmind import resolution and return diagnostics when roots diverge."""

    probe_script = (
        "import json, sys\n"
        "try:\n"
        "    import moonmind  # type: ignore\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'error': str(exc), 'sys_path': sys.path}), end='')\n"
        "else:\n"
        "    print(json.dumps({'module_file': getattr(moonmind, '__file__', ''), "
        "'sys_path': sys.path}), end='')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe_script],
            cwd=str(repo_path),
            env=dict(process_env),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return f"skill-executor warning: failed to run import probe: {exc}"

    if result.returncode != 0:
        error_tail = (result.stderr or result.stdout or "").strip()
        if error_tail:
            return (
                "skill-executor warning: import probe exited non-zero "
                f"(rc={result.returncode}): {error_tail}"
            )
        return (
            "skill-executor warning: import probe exited non-zero "
            f"(rc={result.returncode})"
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return (
            "skill-executor warning: import probe emitted invalid JSON; "
            f"stdout={result.stdout!r}"
        )
    if not isinstance(payload, dict):
        return "skill-executor warning: import probe payload was not a JSON object"
    if payload.get("error"):
        return (
            "skill-executor warning: unable to import moonmind during probe: "
            f"{payload.get('error')}"
        )
    return _analyze_import_probe(repo_path=repo_path, probe_payload=payload)


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
        default=None,
        help="Optional JSON object of skill arguments",
    )
    args = parser.parse_args(argv)

    try:
        normalized_skill_id = _validate_skill_id(args.skill_id)
        workspace_root = _resolve_workspace_root()
        step_payload, step_instructions = _resolve_step_execution_context()
        if args.skill_args_json is not None:
            skill_args = _parse_skill_args(args.skill_args_json)
        else:
            skill_args_raw = step_payload.get("skillArgs")
            skill_args = (
                dict(skill_args_raw) if isinstance(skill_args_raw, dict) else {}
            )
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

    process_env = os.environ.copy()
    process_env = _pin_pythonpath_to_repo_root(process_env, repo_path)
    diagnostic = _runtime_import_probe(repo_path=repo_path, process_env=process_env)
    if diagnostic:
        print(diagnostic, file=sys.stderr)
    if step_instructions:
        process_env[_TASK_INSTRUCTIONS_ENV] = step_instructions

    process = subprocess.run(
        command,
        cwd=str(cwd),
        env=process_env,
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
