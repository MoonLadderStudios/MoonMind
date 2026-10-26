"""Project the existing Docker capability switches into the socket proxy's ACLs.

The maintained proxy owns HTTP parsing and bidirectional upgrade transport.
Operation owners retain their budgets; this hop does not cut off long execs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BINARY = "/usr/local/bin/moonmind-socket-proxy"
SECTIONS = (
    "AUTH",
    "BUILD",
    "COMMIT",
    "CONFIGS",
    "CONTAINERS",
    "DISTRIBUTION",
    "EVENTS",
    "EXEC",
    "IMAGES",
    "INFO",
    "NETWORKS",
    "NODES",
    "PING",
    "PLUGINS",
    "SECRETS",
    "SERVICES",
    "SESSION",
    "SWARM",
    "SYSTEM",
    "TASKS",
    "VERSION",
    "VOLUMES",
)
METHODS = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "CONNECT",
    "TRACE",
)
# A provided but impossible expression keeps denied methods at HTTP 403.
NO_ROUTES = "a^"


def enabled(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def policy(environ: Mapping[str, str]) -> dict[str, str]:
    sections = [
        "_ping" if section == "PING" else section.lower()
        for section in SECTIONS
        if enabled(environ.get(section, "1" if section in {"PING", "VERSION"} else "0"))
    ]
    if enabled(environ.get("ALLOW_RESTARTS", "0")):
        sections.append(r"containers/[a-zA-Z0-9_.-]+/(stop|restart|kill)")
    routes = rf"(?i)(/v[0-9.]+)?/({'|'.join(sections)}).*" if sections else NO_ROUTES
    writes = enabled(environ.get("POST", "0"))
    return {
        method: routes if method == "GET" or writes else NO_ROUTES for method in METHODS
    }


def arguments(environ: Mapping[str, str]) -> list[str]:
    return [
        BINARY,
        "-listenip=0.0.0.0",
        "-allowfrom=0.0.0.0/0,::/0",
        "-logjson",
        *(f"-allow{method}={routes}" for method, routes in policy(environ).items()),
    ]


if __name__ == "__main__":
    os.execv(BINARY, arguments(os.environ))
