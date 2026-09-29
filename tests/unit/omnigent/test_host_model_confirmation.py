"""Exact-host confirmation of the selected model before a host is admitted."""

from __future__ import annotations

import json

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.attestation import confirm_exact_host_model

# Shape reported by a live claude-native host's model-options tunnel for a
# subscription (OAuth) login: picker aliases and bare Anthropic ids, no
# ``provider/`` qualification.
_CLAUDE_OPTIONS = {
    "models": [
        {"id": "opus[1m]", "model": "claude-opus-5-5[1m]", "isDefault": True},
        {"id": "claude-fable-5-1", "model": "claude-fable-5-1"},
        {"id": "sonnet", "model": "claude-sonnet-5"},
        {"id": "haiku", "model": "claude-haiku-4-5-20251001"},
    ],
    "routable_models": [],
}


class ServedProbeBackend:
    def __init__(self, answer: str = "served", code: int = 0, stderr: str = "") -> None:
        self.answer = answer
        self.code = code
        self.stderr = stderr
        self.calls: list[list[str]] = []

    async def run(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return self.code, f"{self.answer}\n", self.stderr


@pytest.mark.asyncio
async def test_exact_claude_host_confirms_a_catalog_family_model() -> None:
    backend = ServedProbeBackend("served")

    available, present = await confirm_exact_host_model(
        backend=backend,
        container_name="mm-host-1",
        harness_id="claude-native",
        model_options=_CLAUDE_OPTIONS,
        selected_model="claude-opus-5-5",
    )

    assert present is True
    assert "claude-opus-5-5[1m]" in available
    assert "opus[1m]" in available
    # The exact host answers with its own harness rule for these rows.
    (command,) = backend.calls
    assert command[:3] == ["docker", "exec", "mm-host-1"]
    assert json.loads(command[-2]) == _CLAUDE_OPTIONS["models"]
    assert command[-1] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_exact_claude_host_refusal_is_not_confirmed() -> None:
    available, present = await confirm_exact_host_model(
        backend=ServedProbeBackend("unserved"),
        container_name="mm-host-1",
        harness_id="claude-native",
        model_options=_CLAUDE_OPTIONS,
        selected_model="claude-mythos-1",
    )

    assert present is False
    assert available


@pytest.mark.asyncio
async def test_empty_claude_catalog_is_not_confirmed_without_a_probe() -> None:
    backend = ServedProbeBackend("served")

    available, present = await confirm_exact_host_model(
        backend=backend,
        container_name="mm-host-1",
        harness_id="claude-native",
        model_options={"models": [], "routable_models": []},
        selected_model="claude-opus-5-5",
    )

    assert (available, present) == ([], False)
    assert backend.calls == []


@pytest.mark.asyncio
async def test_claude_probe_failure_is_a_retryable_model_catalog_failure() -> None:
    with pytest.raises(HarnessPlatformError) as exc_info:
        await confirm_exact_host_model(
            backend=ServedProbeBackend("", code=1),
            container_name="mm-host-1",
            harness_id="claude-native",
            model_options=_CLAUDE_OPTIONS,
            selected_model="claude-opus-5-5",
        )

    assert exc_info.value.code == HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_qualified_catalogs_are_confirmed_by_exact_membership() -> None:
    backend = ServedProbeBackend("served")
    options = {"models": [{"qualifiedId": "opencode-go/glm-5.3", "id": "glm-5.3"}]}

    available, present = await confirm_exact_host_model(
        backend=backend,
        container_name="mm-host-1",
        harness_id="opencode-native",
        model_options=options,
        selected_model="opencode-go/glm-5.3",
    )
    _, bare_present = await confirm_exact_host_model(
        backend=backend,
        container_name="mm-host-1",
        harness_id="opencode-native",
        model_options=options,
        selected_model="glm-5.3",
    )

    assert (available, present) == (["opencode-go/glm-5.3"], True)
    assert bare_present is False
    assert backend.calls == []
