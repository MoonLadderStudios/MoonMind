"""Fail-fast guards that keep hermetic unit tests off the network.

API unit tests build a FastAPI app and override the service, user, and
Temporal-client dependencies. Anything they leave un-overridden still resolves
the Compose-only hostnames from ``moonmind.config.settings`` (the artifact
MinIO endpoint and the API Postgres host). Those lookups fail, and the failure
is swallowed, so the tests pass, but only after DNS and client retry backoff:
about 8 to 30 seconds per request depending on the resolver. These guards make
the same failures immediate so the tests keep their current behaviour without
the wait.
"""

from __future__ import annotations

import ipaddress
import socket

_LOOPBACK_HOSTS = frozenset({None, "", "localhost"})


def install_settings_backed_artifact_store_guard(monkeypatch) -> None:
    """Refuse to build a network-backed artifact store from settings.

    Tests that need a real store either inject one or switch the settings
    backend to ``local_fs``; both keep working. Only the S3 branch, which
    opens a connection in its constructor, is refused.
    """

    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactService,
        TemporalArtifactValidationError,
    )

    original = TemporalArtifactService._build_store_from_settings

    def _guarded_build_store_from_settings():
        from moonmind.config.settings import settings

        if settings.workflow.temporal_artifact_backend == "s3":
            raise TemporalArtifactValidationError(
                "Refusing to open a network-backed artifact store from settings "
                "inside unit tests. Inject a store or use the local_fs backend."
            )
        return original()

    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(_guarded_build_store_from_settings),
    )


def install_dns_guard(monkeypatch) -> None:
    """Fail DNS resolution for any non-loopback hostname immediately."""

    real_getaddrinfo = socket.getaddrinfo

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOOPBACK_HOSTS:
            try:
                ipaddress.ip_address(str(host))
            except ValueError:
                raise socket.gaierror(
                    socket.EAI_NONAME,
                    f"Refusing DNS lookup for {host!r} inside unit tests; "
                    "override the dependency that opens this connection.",
                ) from None
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
