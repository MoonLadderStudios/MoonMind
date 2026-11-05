"""First-use provisioning of deployment-owned fixed-service relay resources.

Docker's resource names reconcile concurrent workers; local locks avoid duplicate
work within a worker. Existing resources must match ownership and policy before
any mutation. Provisioning never removes or replaces a retained resource.
"""

from __future__ import annotations

import asyncio
import json
import re
from weakref import WeakKeyDictionary

from moonmind.security.container_services import (
    GATEWAY_INSPECT_FORMAT,
    MANAGED_SERVICE_LABEL,
    RELAY_CONFIG_ENV,
    SERVICE_DIGEST_LABEL,
    SERVICE_LABEL,
    CommandRunner,
    ServiceConnection,
    validate_relay_launch,
)
from moonmind.utils.logging import redact_sensitive_text

_LOCKS: WeakKeyDictionary = WeakKeyDictionary()
_HEALTH_ATTEMPTS = 60
_HEALTH_DELAY = 0.25


def _labels(connection: ServiceConnection) -> dict[str, str]:
    return {
        MANAGED_SERVICE_LABEL: "v1",
        SERVICE_LABEL: connection.service_ref,
        SERVICE_DIGEST_LABEL: connection.digest,
    }


def _error(message: str, diagnostic: bytes) -> RuntimeError:
    detail = redact_sensitive_text(diagnostic.decode(errors="replace").strip())[:1000]
    return RuntimeError(f"{message}: {detail}" if detail else message)


async def _inspect(runner: CommandRunner, args: tuple[str, ...]) -> dict | None:
    code, stdout, stderr = await runner(args)
    if code:
        if re.search(
            r"no such (?:network|image|object|container)|network .+ not found",
            stderr.decode(errors="replace"),
            re.IGNORECASE,
        ):
            return None
        raise _error("service resource inspection failed", stderr)
    try:
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise TypeError("not an object")
        return value
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "service resource inspection returned invalid evidence"
        ) from exc


async def _network(runner: CommandRunner, name: str) -> dict | None:
    return await _inspect(
        runner, ("network", "inspect", "--format", "{{json .}}", name)
    )


async def _gateway(runner: CommandRunner, connection: ServiceConnection) -> dict | None:
    return await _inspect(
        runner, ("inspect", "--format", GATEWAY_INSPECT_FORMAT, connection.gateway_ref)
    )


async def _readiness_error(
    runner: CommandRunner, connection: ServiceConnection, message: str
) -> RuntimeError:
    try:
        _code, stdout, stderr = await runner(
            ("logs", "--tail", "50", connection.gateway_ref)
        )
        return _error(message, stdout + b"\n" + stderr)
    except (OSError, RuntimeError, asyncio.TimeoutError):
        return RuntimeError(
            message + "; retained container diagnostics could not be collected"
        )


def _check_network(
    connection: ServiceConnection, network: dict, *, internal: bool
) -> None:
    labels = network.get("Labels") or {}
    if any(labels.get(name) != value for name, value in _labels(connection).items()):
        raise RuntimeError("service network is not owned by this managed policy")
    if (
        network.get("Driver") != "bridge"
        or network.get("Internal") is not internal
        or network.get("EnableIPv6") is not False
    ):
        raise RuntimeError("service network isolation differs from managed policy")
    if internal and (
        (network.get("Options") or {}).get(
            "com.docker.network.bridge.gateway_mode_ipv4"
        )
        != "isolated"
        or [
            item.get("Subnet") for item in (network.get("IPAM") or {}).get("Config", [])
        ]
        != [connection.subnet]
    ):
        raise RuntimeError(
            "service network subnet or isolation differs from managed policy"
        )


def _check_gateway(connection: ServiceConnection, gateway: dict) -> None:
    labels = (gateway.get("config") or {}).get("Labels") or {}
    if any(labels.get(name) != value for name, value in _labels(connection).items()):
        raise RuntimeError("service relay is not owned by this managed policy")
    validate_relay_launch(connection, gateway, allow_stopped=True)


async def _create_network(
    runner: CommandRunner, connection: ServiceConnection, *, internal: bool
) -> None:
    name = connection.network_ref if internal else connection.egress_network_ref
    args = ["network", "create", "--driver", "bridge"]
    for key, value in _labels(connection).items():
        args.extend(("--label", f"{key}={value}"))
    if internal:
        args.extend(
            (
                "--internal",
                "--subnet",
                str(connection.subnet),
                "--opt",
                "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            )
        )
    args.append(name)
    code, _, stderr = await runner(tuple(args))
    # A timed-out or conflicting create can already have committed. Read its
    # identity before retrying, never deleting a resource to acquire its name.
    current = await _network(runner, name)
    if current is None:
        raise _error("service network creation failed", stderr)
    try:
        _check_network(connection, current, internal=internal)
    except RuntimeError as exc:
        if code:
            raise _error("service network creation failed", stderr) from exc
        raise


def _create_arguments(connection: ServiceConnection) -> tuple[str, ...]:
    health = (
        "python -m moonmind.security.service_relay --check --config-env "
        + RELAY_CONFIG_ENV
    )
    args = [
        "create",
        "--name",
        connection.gateway_ref,
        "--user",
        "1000:1000",
        "--workdir",
        "/app",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--sysctl",
        "net.ipv4.ip_forward=0",
        "--cpus",
        "1",
        "--memory",
        "256m",
        "--pids-limit",
        "64",
        "--restart",
        "unless-stopped",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=1m,uid=1000,gid=1000,mode=0700",
        "--network",
        f"name={connection.network_ref},ip={connection.relay.listen_host}",
        "--network",
        f"name={connection.egress_network_ref}",
        "--health-cmd",
        health,
        "--health-interval",
        "1s",
        "--health-timeout",
        "2s",
        "--health-retries",
        "3",
        "--env",
        RELAY_CONFIG_ENV
        + "="
        + json.dumps(
            connection.relay.to_mapping(), sort_keys=True, separators=(",", ":")
        ),
    ]
    for key, value in _labels(connection).items():
        args.extend(("--label", f"{key}={value}"))
    args.extend(
        (
            "--entrypoint",
            "python",
            connection.gateway_image,
            "-m",
            "moonmind.security.service_relay",
            "--config-env",
            RELAY_CONFIG_ENV,
        )
    )
    return tuple(args)


async def _ensure(runner: CommandRunner, connection: ServiceConnection) -> None:
    internal = await _network(runner, connection.network_ref)
    external = await _network(runner, connection.egress_network_ref)
    gateway = await _gateway(runner, connection)
    # Validate every retained resource first. A missing sibling resource does
    # not authorize mutations around an unowned or drifted resource.
    for value, is_internal in ((internal, True), (external, False)):
        if value is not None:
            _check_network(connection, value, internal=is_internal)
    if gateway is not None:
        _check_gateway(connection, gateway)
    if gateway is None:
        image = await _inspect(
            runner,
            ("image", "inspect", "--format", "{{json .}}", connection.gateway_image),
        )
        if image is None:
            code, _, stderr = await runner(("pull", connection.gateway_image))
            if code:
                raise _error("service relay image acquisition failed", stderr)
    if internal is None:
        await _create_network(runner, connection, internal=True)
    if external is None:
        await _create_network(runner, connection, internal=False)
    if gateway is None:
        code, _, stderr = await runner(_create_arguments(connection))
        gateway = await _gateway(runner, connection)
        if gateway is None:
            raise _error("service relay creation failed", stderr)
        try:
            _check_gateway(connection, gateway)
        except RuntimeError as exc:
            if code:
                raise _error("service relay creation failed", stderr) from exc
            raise
    state = gateway.get("state") or {}
    if state.get("Running") is not True:
        code, _, stderr = await runner(("start", connection.gateway_ref))
        if code:
            current = await _gateway(runner, connection)
            if (
                current is None
                or (current.get("state") or {}).get("Running") is not True
            ):
                raise await _readiness_error(
                    runner,
                    connection,
                    str(_error("service relay start failed", stderr)),
                )
    for attempt in range(_HEALTH_ATTEMPTS):
        current = await _gateway(runner, connection)
        if current is None:
            raise RuntimeError("service relay disappeared during preparation")
        _check_gateway(connection, current)
        if current.get("health") == "healthy":
            return
        if (
            current.get("health") == "unhealthy"
            or (current.get("state") or {}).get("Running") is not True
        ):
            raise await _readiness_error(
                runner, connection, "service relay did not become healthy"
            )
        if attempt + 1 < _HEALTH_ATTEMPTS:
            await asyncio.sleep(_HEALTH_DELAY)
    raise await _readiness_error(
        runner, connection, "service relay readiness timed out"
    )


async def provision_service_connection(
    runner: CommandRunner, connection: ServiceConnection
) -> None:
    if not connection.provision:
        return
    locks = _LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock = locks.setdefault(connection.gateway_ref, asyncio.Lock())
    async with lock:
        await _ensure(runner, connection)
