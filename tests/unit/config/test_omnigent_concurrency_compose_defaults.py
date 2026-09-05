"""The default Compose deployment must not hide a concurrency ceiling.

Source issue: MoonLadderStudios/MoonMind#3878.

Effective Omnigent concurrency is the minimum of four governed limits. A
Compose default that silently sits below the code default is a fifth, invisible
one: an operator who raises ``max_parallel_runs`` to 8 would get 4 with no
reason surfaced anywhere. These tests pin the deployment defaults so that
regression cannot return unnoticed, and so the two host-capacity limits stay
operator-configurable on the documented local path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from moonmind.config.settings import TemporalSettings
from moonmind.omnigent.transport import (
    OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS,
    OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS,
    OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
    OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV,
    OMNIGENT_HTTP_MAX_CONNECTIONS_ENV,
    OMNIGENT_HTTP_MAX_KEEPALIVE_ENV,
)
from moonmind.omnigent.settings import (
    OMNIGENT_GENERIC_HOST_CAPACITY_ENV,
    OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST_ENV,
    OMNIGENT_GENERIC_HOST_COLD_LAUNCH_WINDOW_ENV,
    OMNIGENT_GENERIC_HOST_DEFAULT_CAPACITY,
    OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_BURST,
    OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_WINDOW_SECONDS,
)

_WORKER_SERVICE = "temporal-worker-agent-runtime"
_CONCURRENCY_KEY = "TEMPORAL_AGENT_RUNTIME_WORKER_CONCURRENCY"

#: Services that build the generic Omnigent execution services and therefore
#: read the aggregate host-capacity limits.
_HOST_CAPACITY_SERVICES = ("api", _WORKER_SERVICE)


def _service_environment(service: str) -> dict[str, str]:
    compose = yaml.safe_load(Path("docker-compose.yaml").read_text(encoding="utf-8"))
    environment = compose["services"][service]["environment"]
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    resolved: dict[str, str] = {}
    for entry in environment:
        key, _, value = str(entry).partition("=")
        resolved[key] = value
    return resolved


def _default_of(raw: str) -> str:
    """Return the ``${NAME:-default}`` value an empty ``.env`` collapses to."""

    inner = raw.strip()
    assert inner.startswith("${") and inner.endswith("}"), raw
    _, _, default = inner[2:-1].partition(":-")
    return default


def test_compose_does_not_lower_the_worker_concurrency_below_the_code_default():
    """A compose value under the code default is an undocumented ceiling."""

    declared = _service_environment(_WORKER_SERVICE)[_CONCURRENCY_KEY]
    # Compare against the field default, not the resolved setting: the process
    # running this test may itself have the variable set.
    code_default = TemporalSettings.model_fields[
        "agent_runtime_worker_concurrency"
    ].default

    assert int(_default_of(declared)) == code_default


def test_worker_concurrency_stays_operator_overridable():
    declared = _service_environment(_WORKER_SERVICE)[_CONCURRENCY_KEY]

    assert declared.startswith(f"${{{_CONCURRENCY_KEY}:-")


@pytest.mark.parametrize("service", _HOST_CAPACITY_SERVICES)
@pytest.mark.parametrize(
    ("env_key", "expected_default"),
    [
        (OMNIGENT_GENERIC_HOST_CAPACITY_ENV, OMNIGENT_GENERIC_HOST_DEFAULT_CAPACITY),
        (
            OMNIGENT_GENERIC_HOST_COLD_LAUNCH_BURST_ENV,
            OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_BURST,
        ),
        (
            OMNIGENT_GENERIC_HOST_COLD_LAUNCH_WINDOW_ENV,
            OMNIGENT_GENERIC_HOST_DEFAULT_COLD_LAUNCH_WINDOW_SECONDS,
        ),
    ],
)
def test_host_capacity_limits_are_declared_with_the_code_default(
    service: str, env_key: str, expected_default: int
) -> None:
    """Compose and code must agree, or the deployed limit is not the documented one."""

    declared = _service_environment(service)[env_key]

    assert declared.startswith(f"${{{env_key}:-")
    assert int(_default_of(declared)) == expected_default


@pytest.mark.parametrize("service", _HOST_CAPACITY_SERVICES)
@pytest.mark.parametrize(
    ("env_key", "expected_default"),
    [
        (OMNIGENT_HTTP_MAX_CONNECTIONS_ENV, OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS),
        (
            OMNIGENT_HTTP_MAX_KEEPALIVE_ENV,
            OMNIGENT_HTTP_DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
        ),
        (
            OMNIGENT_HTTP_KEEPALIVE_EXPIRY_ENV,
            OMNIGENT_HTTP_DEFAULT_KEEPALIVE_EXPIRY_SECONDS,
        ),
    ],
)
def test_http_pool_limits_are_forwarded_on_the_compose_path(
    service: str, env_key: str, expected_default: float
) -> None:
    """A pool setting Compose never forwards is fixed at its code default.

    Compose uses the deployment ``.env`` only for interpolation, so a variable
    absent from the service environment cannot be set by an operator at all.
    """

    declared = _service_environment(service)[env_key]

    assert declared.startswith(f"${{{env_key}:-")
    assert float(_default_of(declared)) == float(expected_default)


# --- The chosen 8/16 execution rows (MoonLadderStudios/MoonMind#3880 AC7) ---
#
# The concurrency journey is exercised at 8 and 16. Those rows are only
# meaningful if the deployment can actually be configured to carry them: a
# limit that quietly sits at 4 somewhere between the setting and the ledger
# would make the 16 row a test of the harness, not of the product.


@pytest.mark.parametrize("execution_row", [8, 16])
def test_deployment_settings_can_carry_the_chosen_execution_rows(
    execution_row: int,
) -> None:
    """Every governed layer must be settable to the row, with no hidden cap."""

    from moonmind.omnigent.host_capacity import evaluate_generic_host_capacity
    from moonmind.omnigent.settings import generic_host_capacity

    configured = generic_host_capacity(
        env={OMNIGENT_GENERIC_HOST_CAPACITY_ENV: str(execution_row)}
    )
    assert configured == execution_row

    worker_capacity = TemporalSettings(
        TEMPORAL_AGENT_RUNTIME_WORKER_CONCURRENCY=str(execution_row)
    ).agent_runtime_worker_concurrency
    assert worker_capacity == execution_row

    # The ledger admits exactly the configured number of hosts — no more, and
    # crucially no fewer.
    for active in range(execution_row):
        assert (
            evaluate_generic_host_capacity(
                active_hosts=active,
                recent_cold_launches=0,
                host_capacity=execution_row,
                cold_launch_burst=execution_row,
                cold_launch_window_seconds=30,
            ).admitted
            is True
        )


@pytest.mark.parametrize("execution_row", [8, 16])
def test_a_configurable_row_does_not_weaken_actual_admission(
    execution_row: int,
) -> None:
    """Raising the setting raises the ceiling; it never removes the ceiling."""

    from moonmind.omnigent.host_capacity import (
        LIMITING_LAYER_HOST_CAPACITY,
        evaluate_generic_host_capacity,
    )

    decision = evaluate_generic_host_capacity(
        active_hosts=execution_row,
        recent_cold_launches=0,
        host_capacity=execution_row,
        cold_launch_burst=execution_row,
        cold_launch_window_seconds=30,
    )

    assert decision.admitted is False
    assert decision.limiting_layer == LIMITING_LAYER_HOST_CAPACITY
