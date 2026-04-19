from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_SCRIPT = REPO_ROOT / "services/temporal/scripts/bootstrap-namespace.sh"

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]
REQUIRED_SEARCH_ATTRIBUTES = {
    "mm_entry": "Keyword",
    "mm_owner_id": "Keyword",
    "mm_owner_type": "Keyword",
    "mm_state": "Keyword",
    "mm_updated_at": "Datetime",
    "mm_repo": "Keyword",
    "mm_integration": "Keyword",
    "mm_scheduled_for": "Datetime",
    "mm_has_dependencies": "Bool",
    "mm_dependency_count": "Int",
    "TaskRunId": "Keyword",
    "RuntimeId": "Keyword",
    "SessionId": "Keyword",
    "SessionEpoch": "Int",
    "SessionStatus": "Keyword",
    "IsDegraded": "Bool",
}
LEGACY_SEARCH_ATTRIBUTES = {
    key: value
    for key, value in REQUIRED_SEARCH_ATTRIBUTES.items()
    if (
        not key.startswith("mm_dependency_")
        and key != "mm_has_dependencies"
        and key
        not in {
            "TaskRunId",
            "RuntimeId",
            "SessionId",
            "SessionEpoch",
            "SessionStatus",
            "IsDegraded",
        }
    )
}
RETIRED_SEARCH_ATTRIBUTES = {
    "CustomKeywordField",
    "CustomStringField",
    "CustomTextField",
    "CustomIntField",
    "CustomDatetimeField",
    "CustomDoubleField",
    "CustomBoolField",
    "mm_continue_as_new_cause",
    "mm_dependency_state",
}


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _write_search_attributes(state_dir: Path, search_attributes: dict[str, str]) -> None:
    if not search_attributes:
        return
    lines = [f"{name} {attr_type}" for name, attr_type in search_attributes.items()]
    (state_dir / "search-attributes.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


TEMPORAL_STUB = """#!/usr/bin/env sh
set -eu
state_dir="${FAKE_TEMPORAL_STATE_DIR:?}"
cmd="$*"
printf '%s\\n' "$cmd" >> "${state_dir}/calls.log"

append_search_attribute() {
  name="$1"
  attr_type="$2"
  file="${state_dir}/search-attributes.txt"
  touch "$file"
  if [ "$attr_type" = "Keyword" ] && [ -n "${FAKE_TEMPORAL_KEYWORD_LIMIT:-}" ]; then
    current_count="$(awk '$2 == "Keyword" { count++ } END { print count + 0 }' "$file")"
    if ! grep -Eq "^${name} " "$file" && [ "$current_count" -ge "$FAKE_TEMPORAL_KEYWORD_LIMIT" ]; then
      printf '%s\\n' "cannot have more than ${FAKE_TEMPORAL_KEYWORD_LIMIT} search attribute of type Keyword." >&2
      exit 1
    fi
  fi
  if ! grep -Eq "^${name} ${attr_type}$" "$file"; then
    printf '%s %s\\n' "$name" "$attr_type" >> "$file"
  fi
}

remove_search_attribute() {
  name="$1"
  file="${state_dir}/search-attributes.txt"
  [ -f "$file" ] || return 0
  sed "/^${name} /d" "$file" > "${file}.tmp"
  mv "${file}.tmp" "$file"
}

case "$cmd" in
  *"cluster health"*)
    exit 0
    ;;
  *"namespace describe"*)
    if [ -f "${state_dir}/namespace.exists" ]; then
      exit 0
    fi
    exit 1
    ;;
  *"namespace create"*)
    touch "${state_dir}/namespace.exists"
    exit 0
    ;;
  *"namespace update"*)
    exit 0
    ;;
  *"search-attribute list"*)
    printf 'Name Type\\n'
    if [ -f "${state_dir}/search-attributes.txt" ]; then
      cat "${state_dir}/search-attributes.txt"
    fi
    exit 0
    ;;
  *"search-attribute create"*)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --name)
          name="$2"
          shift 2
          if [ "$#" -gt 1 ] && [ "$1" = "--type" ]; then
            append_search_attribute "$name" "$2"
            shift 2
          fi
          ;;
        *)
          shift
          ;;
      esac
    done
    exit 0
    ;;
  *"search-attribute remove"*)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --name)
          remove_search_attribute "$2"
          shift 2
          ;;
        *)
          shift
          ;;
      esac
    done
    exit 0
    ;;
esac
exit 0
"""


def test_namespace_bootstrap_is_idempotent_and_storage_cap_aware(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _write_executable(fake_bin / "temporal", TEMPORAL_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "moonmind"
    env.pop("TEMPORAL_NAMESPACE_RETENTION_DAYS", None)
    env["TEMPORAL_RETENTION_MAX_STORAGE_GB"] = "24"
    env["TEMPORAL_RETENTION_ESTIMATED_GB_PER_DAY"] = "6"

    first_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    assert (
        "Derived namespace retention 4 day(s) from storage cap 24 GB at 6 GB/day."
        in first_run.stdout
    )
    assert "Namespace does not exist; creating" in first_run.stdout
    assert "Storage cap guardrail is 24 GB with retention 4 day(s)." in first_run.stdout

    second_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second_run.returncode == 0, second_run.stderr
    assert "Namespace exists; updating retention" in second_run.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert "--retention 96h" in calls
    assert "namespace create" in calls
    assert "namespace update" in calls
    assert calls.count("search-attribute create") == len(REQUIRED_SEARCH_ATTRIBUTES)


def test_namespace_bootstrap_updates_existing_default_namespace_retention(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "namespace.exists").touch()

    _write_executable(fake_bin / "temporal", TEMPORAL_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "default"
    env["TEMPORAL_NAMESPACE_RETENTION_DAYS"] = "90"

    first_run = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    assert "Namespace exists; updating retention to 2160h." in first_run.stdout
    assert "Namespace policy applied. Storage cap guardrail is 100 GB with retention 90 day(s)." in first_run.stdout
    assert "Namespace does not exist; creating" not in first_run.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert "namespace create" not in calls
    assert "namespace update" in calls
    assert "--retention 2160h" in calls
    assert "search-attribute create" in calls


def test_namespace_bootstrap_skips_create_when_default_namespace_missing(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _write_executable(fake_bin / "temporal", TEMPORAL_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "default"
    env["TEMPORAL_NAMESPACE_RETENTION_DAYS"] = "90"

    result = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Built-in default namespace does not exist yet; skipping namespace create/update and retention policy." in result.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert "namespace create" not in calls
    assert "namespace update" not in calls
    assert "search-attribute create" in calls


def test_namespace_bootstrap_registers_missing_search_attributes_on_upgrade(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "namespace.exists").touch()
    _write_search_attributes(state_dir, LEGACY_SEARCH_ATTRIBUTES)
    _write_executable(fake_bin / "temporal", TEMPORAL_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "default"
    env.pop("TEMPORAL_NAMESPACE_RETENTION_DAYS", None)

    result = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Registered missing search attributes:" in result.stdout
    assert "mm_has_dependencies" in result.stdout
    assert "mm_dependency_count" in result.stdout
    assert "TaskRunId" in result.stdout
    assert "RuntimeId" in result.stdout
    assert "SessionId" in result.stdout
    assert "SessionEpoch" in result.stdout
    assert "SessionStatus" in result.stdout
    assert "IsDegraded" in result.stdout

    calls = (state_dir / "calls.log").read_text(encoding="utf-8")
    assert calls.count("search-attribute create") == 8

    registered = (state_dir / "search-attributes.txt").read_text(encoding="utf-8")
    for name, attr_type in REQUIRED_SEARCH_ATTRIBUTES.items():
        assert f"{name} {attr_type}" in registered


def test_namespace_bootstrap_retire_old_keyword_attributes_before_sql_limit_upgrade(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "namespace.exists").touch()
    _write_search_attributes(
        state_dir,
        {
            "CustomKeywordField": "Keyword",
            "mm_entry": "Keyword",
            "mm_owner_id": "Keyword",
            "mm_owner_type": "Keyword",
            "mm_state": "Keyword",
            "mm_repo": "Keyword",
            "mm_integration": "Keyword",
            "mm_continue_as_new_cause": "Keyword",
            "mm_dependency_state": "Keyword",
            "TaskRunId": "Keyword",
            "mm_updated_at": "Datetime",
            "mm_scheduled_for": "Datetime",
            "mm_has_dependencies": "Bool",
            "mm_dependency_count": "Int",
            "SessionEpoch": "Int",
            "IsDegraded": "Bool",
        },
    )
    _write_executable(fake_bin / "temporal", TEMPORAL_STUB)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_TEMPORAL_STATE_DIR"] = str(state_dir)
    env["FAKE_TEMPORAL_KEYWORD_LIMIT"] = "10"
    env["TEMPORAL_ADDRESS"] = "temporal:7233"
    env["TEMPORAL_NAMESPACE"] = "default"
    env.pop("TEMPORAL_NAMESPACE_RETENTION_DAYS", None)

    result = subprocess.run(
        ["sh", str(BOOTSTRAP_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Removed retired search attributes:" in result.stdout
    assert "CustomKeywordField" in result.stdout
    assert "mm_continue_as_new_cause" in result.stdout
    assert "mm_dependency_state" in result.stdout
    assert "Registered missing search attributes:" in result.stdout
    assert "RuntimeId" in result.stdout
    assert "SessionId" in result.stdout
    assert "SessionStatus" in result.stdout

    registered = (state_dir / "search-attributes.txt").read_text(encoding="utf-8")
    for name in RETIRED_SEARCH_ATTRIBUTES:
        assert f"{name} " not in registered
    for name, attr_type in REQUIRED_SEARCH_ATTRIBUTES.items():
        assert f"{name} {attr_type}" in registered

    keyword_count = sum(
        1 for line in registered.splitlines() if line.endswith(" Keyword")
    )
    assert keyword_count == 10
