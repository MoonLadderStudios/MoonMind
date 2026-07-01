#!/usr/bin/env python3
"""Emit a non-destructive status-token inventory report for MM-1080."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import os
from pathlib import Path
import sys


DEFAULT_TOKENS = (
    "mm_state",
    "closeStatus",
    "temporalStatus",
    "no_changes",
    "awaiting_action",
    "queued",
    "in-progress",
    "StepExecutionStatus",
)

DEFAULT_SCAN_ROOTS = (
    "api_service",
    "docs",
    "frontend/src",
    "moonmind",
    "tests",
    "tools",
)

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "var",
}

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True, slots=True)
class TokenClassification:
    token: str
    guessed_domain: str
    canonicality: str
    action: str


TOKEN_CLASSIFICATIONS: dict[str, TokenClassification] = {
    "mm_state": TokenClassification(
        token="mm_state",
        guessed_domain="workflow_lifecycle_state",
        canonicality="canonical",
        action="keep_canonical",
    ),
    "closeStatus": TokenClassification(
        token="closeStatus",
        guessed_domain="temporal_close_status",
        canonicality="canonical",
        action="keep_canonical",
    ),
    "temporalStatus": TokenClassification(
        token="temporalStatus",
        guessed_domain="temporal_status",
        canonicality="canonical",
        action="keep_canonical",
    ),
    "no_changes": TokenClassification(
        token="no_changes",
        guessed_domain="legacy_or_migration_status",
        canonicality="historical",
        action="historical_migration_only",
    ),
    "awaiting_action": TokenClassification(
        token="awaiting_action",
        guessed_domain="dashboard_compatibility_status",
        canonicality="compatibility",
        action="rename_domain_specific",
    ),
    "queued": TokenClassification(
        token="queued",
        guessed_domain="provider_normalized_status",
        canonicality="boundary_canonical",
        action="move_to_provider_boundary",
    ),
    "in-progress": TokenClassification(
        token="in-progress",
        guessed_domain="provider_native_status",
        canonicality="non_canonical",
        action="move_to_provider_boundary",
    ),
    "StepExecutionStatus": TokenClassification(
        token="StepExecutionStatus",
        guessed_domain="step_execution_artifact_status",
        canonicality="canonical",
        action="keep_canonical",
    ),
}

REPORT_COLUMNS = (
    "token",
    "guessed_domain",
    "files",
    "canonicality",
    "action",
)


def classify_token(token: str) -> TokenClassification:
    return TOKEN_CLASSIFICATIONS.get(
        token,
        TokenClassification(
            token=token,
            guessed_domain="unknown",
            canonicality="unknown",
            action="delete_unused",
        ),
    )


def iter_text_files(root: Path, scan_roots: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for scan_root in scan_roots:
        base = root / scan_root
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix in TEXT_SUFFIXES:
                files.append(base)
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in SKIP_DIRS]
            for filename in filenames:
                path = Path(dirpath) / filename
                if path.suffix in TEXT_SUFFIXES:
                    files.append(path)
    return sorted(set(files))


def find_token_files(
    *,
    root: Path,
    scan_roots: tuple[str, ...],
    tokens: tuple[str, ...],
) -> dict[str, list[str]]:
    matches = {token: [] for token in tokens}
    for path in iter_text_files(root, scan_roots):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for token in tokens:
            if token in text:
                try:
                    rel_path = path.relative_to(root).as_posix()
                except ValueError:
                    rel_path = path.as_posix()
                matches[token].append(rel_path)
    return matches


def build_report_rows(
    *,
    root: Path,
    scan_roots: tuple[str, ...],
    tokens: tuple[str, ...],
) -> list[dict[str, str]]:
    token_files = find_token_files(root=root, scan_roots=scan_roots, tokens=tokens)
    rows: list[dict[str, str]] = []
    for token in tokens:
        classification = classify_token(token)
        rows.append(
            {
                "token": token,
                "guessed_domain": classification.guessed_domain,
                "files": ";".join(token_files[token]),
                "canonicality": classification.canonicality,
                "action": classification.action,
            }
        )
    return rows


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan. Defaults to the current directory.",
    )
    parser.add_argument(
        "--token",
        action="append",
        dest="tokens",
        help="Token to scan. May be repeated. Defaults to the seeded MM-1080 tokens.",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        dest="scan_roots",
        help="Relative path to scan. May be repeated.",
    )
    parser.add_argument(
        "--fail-on-unknown",
        action="store_true",
        help=(
            "Exit nonzero after writing the CSV report when a scanned token is "
            "unknown or assigned to the unknown domain."
        ),
    )
    return parser.parse_args(argv)


def has_unknown_or_misplaced_rows(rows: list[dict[str, str]]) -> bool:
    for row in rows:
        has_files = bool(row.get("files"))
        if not has_files:
            continue
        if row.get("guessed_domain") == "unknown":
            return True
        if row.get("canonicality") == "unknown":
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = Path(args.root).resolve()
    tokens = tuple(args.tokens or DEFAULT_TOKENS)
    scan_roots = tuple(args.scan_roots or DEFAULT_SCAN_ROOTS)
    writer = csv.DictWriter(sys.stdout, fieldnames=REPORT_COLUMNS)
    writer.writeheader()
    rows = build_report_rows(root=root, scan_roots=scan_roots, tokens=tokens)
    writer.writerows(rows)
    if args.fail_on_unknown and has_unknown_or_misplaced_rows(rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
