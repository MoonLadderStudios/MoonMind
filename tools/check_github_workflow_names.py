#!/usr/bin/env python3
"""Reject task-specific names that create durable GitHub Actions clutter."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIRECTORY = Path(".github/workflows")
TOP_LEVEL_NAME = re.compile(r"^name\s*:\s*(.*?)\s*$")
FORBIDDEN_NAME_PATTERNS = (
    (re.compile(r"\bapply[\s_-]+pr\b", re.IGNORECASE), '"Apply PR"'),
    (re.compile(r"\breview[\s_-]+fix\b", re.IGNORECASE), '"Review fix"'),
    (re.compile(r"\bmilestone\b", re.IGNORECASE), '"Milestone"'),
    (re.compile(r"\bclean[\s_-]+up\b", re.IGNORECASE), '"Clean up"'),
    (
        re.compile(
            r"(?:#\s*\d+\b|\b(?:pr|pull[\s_-]+request|issue)\s*(?:#\s*)?\d+\b)",
            re.IGNORECASE,
        ),
        "a PR or issue number",
    ),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    message: str


def workflow_files(root: Path) -> list[Path]:
    directory = root / WORKFLOW_DIRECTORY
    return sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))


def _parse_name_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        raise ValueError("top-level name must be a non-empty string")

    if value.startswith('"'):
        try:
            parsed, end = json.JSONDecoder().raw_decode(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid double-quoted top-level name: {exc.msg}"
            ) from exc
        suffix = value[end:].strip()
        if suffix and not suffix.startswith("#"):
            raise ValueError("invalid content after double-quoted top-level name")
        if not isinstance(parsed, str) or not parsed.strip():
            raise ValueError("top-level name must be a non-empty string")
        return parsed.strip()

    if value.startswith("'"):
        end = 1
        while end < len(value):
            if value[end] != "'":
                end += 1
                continue
            if end + 1 < len(value) and value[end + 1] == "'":
                end += 2
                continue
            break
        if end >= len(value):
            raise ValueError("invalid single-quoted top-level name")
        suffix = value[end + 1 :].strip()
        if suffix and not suffix.startswith("#"):
            raise ValueError("invalid content after single-quoted top-level name")
        parsed = value[1:end].replace("''", "'").strip()
        if not parsed:
            raise ValueError("top-level name must be a non-empty string")
        return parsed

    parsed = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if not parsed:
        raise ValueError("top-level name must be a non-empty string")
    return parsed


def workflow_display_name(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = TOP_LEVEL_NAME.fullmatch(line)
        if match:
            return _parse_name_value(match.group(1))
    raise ValueError("workflow must declare an explicit top-level name")


def check_workflow_file(path: Path) -> list[Finding]:
    try:
        display_name = workflow_display_name(path)
    except (OSError, UnicodeError, ValueError) as exc:
        return [Finding(path, str(exc))]

    for pattern, description in FORBIDDEN_NAME_PATTERNS:
        if pattern.search(display_name):
            return [
                Finding(
                    path,
                    f"display name {display_name!r} contains {description}; "
                    "use a stable reusable workflow name and put one-off automation under tools/",
                )
            ]
    return []


def check_workflows(root: Path = REPO_ROOT) -> list[Finding]:
    return [
        finding
        for path in workflow_files(root)
        for finding in check_workflow_file(path)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args()

    files = workflow_files(args.root)
    findings = check_workflows(args.root)
    if findings:
        print("GitHub workflow display-name guard failed:")
        for finding in findings:
            try:
                path = finding.path.relative_to(args.root)
            except ValueError:
                path = finding.path
            print(f"- {path}: {finding.message}")
        return 1

    print(f"GitHub workflow display-name guard passed ({len(files)} workflows).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
