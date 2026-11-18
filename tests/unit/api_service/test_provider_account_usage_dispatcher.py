"""Production account-usage dispatch (MoonLadderStudios/MoonMind#5363)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from api_service.services import provider_account_usage as service_module
from api_service.services.provider_account_usage import (
    CollectionRequest,
    DispatchResult,
    get_account_usage_dispatcher,
    install_account_usage_dispatcher,
)
from api_service.services.provider_account_usage_collection import (
    TemporalAccountUsageDispatcher,
)
from moonmind.provider_profiles.usage_collection import UsageCollectorRegistry

pytestmark = pytest.mark.asyncio

REQUEST = CollectionRequest(
    profile_id="alpha",
    credential_generation=2,
    provider_id="openai",
    runtime_id="codex_cli",
    attempt_id="attempt-1",
    trigger="manual",
    deadline_at=datetime(2026, 11, 18, 12, 2, tzinfo=UTC),
)


class Adapter:
    runtime_id = "codex_cli"

    def supports(self, *, provider_id: str) -> bool:
        return provider_id == "openai"


class Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.starts: list[tuple] = []
        self.error = error

    async def start_workflow(self, name, payload, **kwargs):
        self.starts.append((name, payload, kwargs))
        if self.error is not None:
            raise self.error


def _dispatcher(client: Client, adapters=(Adapter(),)):
    async def factory():
        return client

    return TemporalAccountUsageDispatcher(
        collectors=UsageCollectorRegistry(tuple(adapters)), client_factory=factory
    )


async def test_dispatch_starts_one_bounded_collection_workflow_per_attempt() -> None:
    client = Client()

    assert await _dispatcher(client).dispatch(REQUEST) is DispatchResult.DISPATCHED

    [(name, payload, kwargs)] = client.starts
    assert name == "MoonMind.ProviderAccountUsageCollection"
    assert kwargs["id"] == "provider-account-usage:alpha:attempt-1"
    assert kwargs["execution_timeout"].total_seconds() > 0
    assert payload == {
        "profile_id": "alpha",
        "credential_generation": 2,
        "provider_id": "openai",
        "runtime_id": "codex_cli",
        "attempt_id": "attempt-1",
        "trigger": "manual",
        "deadline_at": "2026-11-18T12:02:00+00:00",
    }


async def test_redispatching_a_running_attempt_coalesces() -> None:
    client = Client(
        WorkflowAlreadyStartedError("provider-account-usage:alpha:attempt-1", "x")
    )
    assert await _dispatcher(client).dispatch(REQUEST) is DispatchResult.DISPATCHED


async def test_providers_without_a_collector_are_not_supported() -> None:
    client = Client()
    dispatcher = _dispatcher(client, adapters=())

    assert dispatcher.supports(REQUEST) is False
    assert await dispatcher.dispatch(REQUEST) is DispatchResult.NOT_SUPPORTED
    assert client.starts == []


async def test_the_temporal_dispatcher_is_the_process_default(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_dispatcher", None)
    try:
        assert isinstance(
            get_account_usage_dispatcher(), TemporalAccountUsageDispatcher
        )
    finally:
        install_account_usage_dispatcher(None)
