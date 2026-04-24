"""Unit tests for RAG CLI helpers (DOC-REQ-003, DOC-REQ-005)."""

from __future__ import annotations

import pytest

from moonmind.rag.cli import CliError, parse_budget_args, parse_filters

def test_parse_filters_happy_path() -> None:
    result = parse_filters(["repo=moonmind", "tenant=prod"])
    assert result == {"repo": "moonmind", "tenant": "prod"}

def test_parse_filters_rejects_missing_equals() -> None:
    with pytest.raises(CliError, match="Expected key=value"):
        parse_filters(["invalid_filter"])

def test_parse_filters_rejects_empty_key() -> None:
    with pytest.raises(CliError, match="Both key and value required"):
        parse_filters(["=value"])

def test_parse_filters_rejects_empty_value() -> None:
    with pytest.raises(CliError, match="Both key and value required"):
        parse_filters(["key="])

def test_parse_filters_handles_value_with_equals() -> None:
    result = parse_filters(["key=value=extra"])
    assert result == {"key": "value=extra"}

def test_parse_filters_returns_empty_for_empty_input() -> None:
    assert parse_filters([]) == {}

def test_parse_budget_args_happy_path() -> None:
    result = parse_budget_args(["tokens=500", "latency_ms=800"])
    assert result == {"tokens": 500, "latency_ms": 800}

def test_parse_budget_args_rejects_non_integer() -> None:
    with pytest.raises(CliError, match="must use an integer value"):
        parse_budget_args(["tokens=abc"])

def test_parse_budget_args_rejects_missing_equals() -> None:
    with pytest.raises(CliError, match="Expected key=value"):
        parse_budget_args(["tokens500"])

def test_parse_budget_args_returns_empty_for_empty_input() -> None:
    assert parse_budget_args([]) == {}
