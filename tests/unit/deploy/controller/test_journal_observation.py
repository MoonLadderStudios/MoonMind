"""Read-only journal discovery failures produce bounded, retryable operations."""

import io
import json

import pytest
from conftest import load


@pytest.mark.parametrize("boundary", ["config", "ps", "inspect"])
@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_managed_journal_observation_failure_is_recorded_before_handoff(
    controller_path, tmp_path, monkeypatch, boundary, failure
):
    server = load("server")
    record = server.record_mod
    store = record.OperationStore(tmp_path / "state")
    monkeypatch.setenv("MOONMIND_CONTROLLER_MANAGED", "1")
    monkeypatch.setattr(
        server, "prepare_release_configuration", lambda store, op, **kwargs: op
    )
    failures = []
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            commands.append(args)
            # The existing one-off ownership probe precedes journal discovery.
            if args[:2] == ("docker", "ps") and "--all" not in args:
                return {"exit": 0, "output": ""}
            observed = "config" if "config" in args else args[1]
            assert observed in ("config", "ps", "inspect"), "no deployment mutations"
            if observed == boundary:
                failures.append(observed)
                if failure == "exception":
                    raise OSError(
                        f"{boundary} read unavailable token=private-test-value"
                    )
                return {"exit": 1, "output": "private unrendered contents"}
            if observed == "config":
                return {
                    "exit": 0,
                    "output": json.dumps(
                        {
                            "services": {
                                "temporal-worker-agent-runtime": {},
                                "temporal-worker-artifacts": {},
                            }
                        }
                    ),
                }
            assert observed == "ps"
            return {"exit": 0, "output": "installed-writer"}

    monkeypatch.setattr(server.engine, "subprocess_runner", Runner)
    monkeypatch.setattr(
        server,
        "hand_off_to_requested_controller",
        lambda *args, **kwargs: pytest.fail("observation failure must precede handoff"),
    )
    monkeypatch.setattr(
        server.engine,
        "apply",
        lambda *args, **kwargs: pytest.fail("observation failure must precede apply"),
    )
    app = server.build_app(
        store=store, secret="test-secret", legacy_writer_probe=lambda: False
    )

    def post(path, body):
        statuses = []
        encoded = json.dumps(body).encode()
        response = app(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": path,
                "CONTENT_LENGTH": str(len(encoded)),
                "wsgi.input": io.BytesIO(encoded),
                "HTTP_AUTHORIZATION": "Bearer test-secret",
            },
            lambda status, headers: statuses.append(status),
        )
        return statuses[0], json.loads(b"".join(response))

    status, result = post(
        "/v1/operations",
        {
            "stack": "moonmind",
            "desiredImage": "example/moonmind:latest",
            "target": {
                "project": "moonmind-test",
                "projectDir": str(tmp_path),
                "composeFiles": ["compose.json"],
                "services": [
                    "temporal-worker-agent-runtime",
                    "temporal-worker-artifacts",
                ],
            },
        },
    )
    assert status == "202 Accepted", result
    operation = store.load(result["operationId"])
    assert operation["status"] == "failed"
    assert operation["installed"] is None
    assert operation["autoAttemptsExhausted"] is True
    assert len(operation["attempts"]) == record.MAX_AUTO_ATTEMPTS
    assert len(failures) == record.MAX_AUTO_ATTEMPTS
    assert all("journal" in attempt["error"] for attempt in operation["attempts"])
    expected = (
        f"{boundary} read unavailable"
        if failure == "exception"
        else {
            "config": "Target journal consumer configuration is unavailable",
            "ps": "Docker consumers unavailable",
            "inspect": "Docker consumer images unavailable",
        }[boundary]
    )
    assert expected in operation["errorSummary"]
    assert "private-test-value" not in json.dumps(operation)
    assert "private unrendered contents" not in json.dumps(operation)

    # The terminal record exposes Retry instead of staying staged behind a 500.
    status, retried = post(f"/v1/operations/{operation['operationId']}/retry", {})
    assert status == "202 Accepted", retried
    retried = store.load(operation["operationId"])
    assert retried["status"] == "failed"
    assert retried["attemptGroup"] == 2
    assert len(retried["attempts"]) == 2 * record.MAX_AUTO_ATTEMPTS
    assert retried["attempts"][: record.MAX_AUTO_ATTEMPTS] == operation["attempts"]
    assert len(failures) == 2 * record.MAX_AUTO_ATTEMPTS
