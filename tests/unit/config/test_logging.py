import json
import logging

import structlog

from moonmind.config.logging import configure_logging, default_log_fields_from_env


def test_default_log_fields_from_env_includes_worker_identity(monkeypatch):
    monkeypatch.setenv("TEMPORAL_WORKER_FLEET", "agent_runtime")
    monkeypatch.setenv("MOONMIND_WORKER_ID", "worker-17")

    fields = default_log_fields_from_env()

    assert fields["service"] == "temporal-worker-agent-runtime"
    assert fields["component"] == "agent_runtime"
    assert fields["worker_fleet"] == "agent_runtime"
    assert fields["worker_id"] == "worker-17"


def test_configure_logging_emits_structlog_json_with_execution_context(
    capsys, monkeypatch
):
    monkeypatch.setenv("MOONMIND_STRUCTURED_LOGS", "1")
    monkeypatch.setenv("MOONMIND_SERVICE_NAME", "api-service")
    monkeypatch.setenv("MOONMIND_COMPONENT", "api")
    monkeypatch.setenv("MOONMIND_WORKER_ID", "api-worker-1")
    monkeypatch.setenv("MOONMIND_WORKER_FLEET", "api")
    original_handlers = list(logging.root.handlers)

    try:
        configure_logging(level="INFO", structured=None)
        logging.getLogger("moonmind.test").info(
            "stdlib event",
            extra={"workflow_id": "wf-1", "run_id": "run-1"},
        )
        structlog.get_logger("moonmind.structlog.test").info(
            "structlog event",
            workflow_id="wf-2",
            run_id="run-2",
        )
    finally:
        logging.root.handlers = original_handlers
        structlog.reset_defaults()

    records = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    stdlib_record = next(
        record for record in records if record.get("event") == "stdlib event"
    )
    structlog_record = next(
        record for record in records if record.get("event") == "structlog event"
    )

    assert stdlib_record["service"] == "api-service"
    assert stdlib_record["component"] == "api"
    assert stdlib_record["worker_fleet"] == "api"
    assert stdlib_record["worker_id"] == "api-worker-1"
    assert stdlib_record["workflow_id"] == "wf-1"
    assert stdlib_record["run_id"] == "run-1"
    assert structlog_record["service"] == "api-service"
    assert structlog_record["workflow_id"] == "wf-2"
    assert structlog_record["run_id"] == "run-2"


def test_configure_logging_preserves_filter_injected_fields_for_native_structlog(
    capsys, monkeypatch
):
    monkeypatch.setenv("MOONMIND_STRUCTURED_LOGS", "1")
    original_handlers = list(logging.root.handlers)

    class InjectTraceFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.trace_id = "trace-from-filter"
            record.workflow_id = "wf-from-filter"
            return True

    try:
        configure_logging(level="INFO", structured=True)
        for handler in logging.root.handlers:
            handler.addFilter(InjectTraceFilter())

        structlog.get_logger("moonmind.structlog.filter").info(
            "structlog filtered event",
            workflow_id="wf-from-event",
        )
    finally:
        logging.root.handlers = original_handlers
        structlog.reset_defaults()

    records = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    structlog_record = next(
        record for record in records if record.get("event") == "structlog filtered event"
    )

    assert structlog_record["trace_id"] == "trace-from-filter"
    assert structlog_record["workflow_id"] == "wf-from-event"
