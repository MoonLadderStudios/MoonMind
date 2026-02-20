import pytest

from moonmind.rag import cli as rag_cli


def test_parse_filters_happy_path():
    filters = rag_cli.parse_filters(["repo=moonmind", "tenant=prod"])
    assert filters == {"repo": "moonmind", "tenant": "prod"}


def test_parse_filters_invalid_format():
    with pytest.raises(rag_cli.CliError):
        rag_cli.parse_filters(["invalid-filter"])


def test_parse_budget_args_happy_path():
    budgets = rag_cli.parse_budget_args(["tokens=1200", "latency_ms=800"])
    assert budgets == {"tokens": 1200, "latency_ms": 800}


def test_parse_budget_args_invalid_value():
    with pytest.raises(rag_cli.CliError):
        rag_cli.parse_budget_args(["tokens=abc"])
