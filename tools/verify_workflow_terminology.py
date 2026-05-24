#!/usr/bin/env python3
"""MM-731 workflow terminology guardrails.

The checks are intentionally scoped to executable/public surfaces where legacy
Task or Step Attempt terminology would re-enter API, schema, link, or UI
contracts. Rule-documentation and historical migration text stay outside this
gate so the check remains actionable.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED_QUALIFIED_TASK_TERMS = (
    "Temporal Task",
    "Temporal Workflow Task",
    "Temporal Activity Task",
    "Temporal Task Queue",
    "Workflow Task",
    "Activity Task",
    "Task Queue",
    "Jira task",
    "Codex task",
    "Codex provider task",
)


@dataclass(frozen=True)
class Rule:
    name: str
    paths: tuple[str, ...]
    pattern: re.Pattern[str]
    message: str


RUNTIME_RULES = (
    Rule(
        name="execution-attempt-route",
        paths=(
            "api_service/api/routers/executions.py",
            "frontend/src/generated/openapi.ts",
        ),
        pattern=re.compile(r"/(?:api/)?executions[^\n\"']*/attempts\b|/attempts\b"),
        message="Use /step-executions for public execution routes.",
    ),
    Rule(
        name="execution-attempt-schema-field",
        paths=("frontend/src/generated/openapi.ts",),
        pattern=re.compile(
            r"\battempts\??:\s*components\[\"schemas\"\]\[\"StepExecutionProjectionModel\"\]\[\]"
        ),
        message="Use the stepExecutions response field for Step Execution lists.",
    ),
    Rule(
        name="legacy-ui-copy",
        paths=(
            "frontend/src/entrypoints/workflow-start.tsx",
            "frontend/src/entrypoints/workflow-detail.tsx",
            "frontend/src/entrypoints/workflow-list.tsx",
        ),
        pattern=re.compile(r"\b(Create Task|Task Detail|Task ID|Step Attempt)\b"),
        message="Use workflow-native UI copy.",
    ),
    Rule(
        name="execution-response-legacy-fields",
        paths=(
            "tests/unit/api/test_executions_temporal.py",
            "tests/contract/test_temporal_execution_api.py",
        ),
        pattern=re.compile(r"BANNED_EXECUTION_(?:RESPONSE_KEYS|SCHEMA_FIELDS)\s*=\s*\{"),
        message="Banned execution field tests must remain present.",
    ),
)

DOC_RULES = (
    Rule(
        name="canonical-doc-unqualified-task",
        paths=(
            "docs/Temporal/WorkflowExecutionProductModel.md",
            "docs/Temporal/WorkflowTypeCatalogAndLifecycle.md",
            "docs/Temporal/ManagedAndExternalAgentExecutionModel.md",
            "docs/MoonMindArchitecture.md",
        ),
        pattern=re.compile(
            r"\b(MoonMind task|task detail|create task|task status|task-oriented|task-first|task-compatible)\b",
            re.IGNORECASE,
        ),
        message="Canonical docs must not use unqualified MoonMind task terminology.",
    ),
)


@dataclass(frozen=True)
class Finding:
    rule: str
    path: Path
    line_number: int
    line: str
    message: str


def _iter_rule_files(rule: Rule, root: Path) -> Iterable[Path]:
    for path_text in rule.paths:
        path = root / path_text
        if path.exists():
            yield path


def _line_is_allowed(line: str) -> bool:
    return any(term in line for term in ALLOWED_QUALIFIED_TASK_TERMS)


def check_rules(rules: Iterable[Rule], *, root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for rule in rules:
        for path in _iter_rule_files(rule, root):
            relative_path = path.relative_to(root)
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if rule.name == "execution-response-legacy-fields":
                    # This rule asserts that the guard set exists. It is not a
                    # banned occurrence.
                    continue
                if rule.pattern.search(line) and not _line_is_allowed(line):
                    findings.append(
                        Finding(
                            rule=rule.name,
                            path=relative_path,
                            line_number=line_number,
                            line=line.strip(),
                            message=rule.message,
                        )
                    )
    return findings


def check_required_test_guard_sets(*, root: Path = REPO_ROOT) -> list[Finding]:
    required_terms = {
        "attempt",
        "attempts",
        "stepAttemptId",
        "taskId",
        "taskRunId",
        "taskStatus",
    }
    files = (
        root / "tests/unit/api/test_executions_temporal.py",
        root / "tests/contract/test_temporal_execution_api.py",
    )
    findings: list[Finding] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        missing = sorted(term for term in required_terms if f'"{term}"' not in text)
        if missing:
            findings.append(
                Finding(
                    rule="execution-response-legacy-fields",
                    path=path.relative_to(root),
                    line_number=1,
                    line=f"missing: {', '.join(missing)}",
                    message="Banned execution field tests must include all MM-731 terms.",
                )
            )
    return findings


def run(mode: str, *, root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    if mode in {"runtime", "all"}:
        findings.extend(check_rules(RUNTIME_RULES, root=root))
        findings.extend(check_required_test_guard_sets(root=root))
    if mode in {"docs", "all"}:
        findings.extend(check_rules(DOC_RULES, root=root))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("runtime", "docs", "all"),
        default="all",
        help="Check scope.",
    )
    args = parser.parse_args(argv)
    findings = run(args.mode)
    if not findings:
        print(f"MM-731 workflow terminology check passed ({args.mode}).")
        return 0

    print(f"MM-731 workflow terminology check failed ({args.mode}):")
    for finding in findings:
        print(
            f"{finding.path}:{finding.line_number}: {finding.rule}: "
            f"{finding.message} :: {finding.line}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
