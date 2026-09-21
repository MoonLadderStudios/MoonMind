"""Operator-admission boundary coverage (MoonLadderStudios/MoonMind#4347).

Proves the single-user operator admission boundary without a database:
fresh local loopback admission with no User-table query, approved remote
admission through the configured URL via the existing trusted-ingress
proof (boolean admission, never a User mapping), denial of direct-backend,
forged-header, hostile-Host/origin, worker-credential and container-local
loopback bypasses, actionable denial/unavailability (never a default
user), credentials never in URLs, and the bounded stream
revalidation/closure policy.
"""

from __future__ import annotations

import time

import pytest


def _local_env(monkeypatch):
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("MOONMIND_PROXY_IDENTITY_NAMESPACE", raising=False)
    monkeypatch.delenv("MOONMIND_PROXY_IDENTITY_HEADER", raising=False)


def _remote_env(monkeypatch, **overrides):
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example.com")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "10.20.0.5")
    monkeypatch.setenv("MOONMIND_PROXY_IDENTITY_NAMESPACE", "corp-ingress")
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _headers(**extra):
    headers = {key.lower(): value for key, value in extra.items()}
    return headers


def test_local_loopback_admits_without_user_table(monkeypatch):
    from moonmind.security import operator_admission as oa

    _local_env(monkeypatch)
    admission = oa.resolve_operator_admission(
        client_host="127.0.0.1",
        host_header="127.0.0.1:7000",
        method="GET",
        headers=_headers(),
    )
    assert isinstance(admission, oa.OperatorAdmission)
    assert admission.via == "loopback"
    # The boundary carries no person identity and never resolves one.
    assert admission.subject is None
    assert "no application user" in admission.describe().lower()


def test_local_non_loopback_denied_without_default_user(monkeypatch):
    from moonmind.security import operator_admission as oa

    _local_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="10.20.0.9",
            host_header="10.20.0.4:7000",
            method="GET",
            headers=_headers(),
        )
    assert exc_info.value.http_status == 401
    assert exc_info.value.code == "auth_required"


def test_local_container_loopback_header_does_not_admit(monkeypatch):
    """A workload container presenting Host: localhost is still denied.

    Its packets arrive from a bridge IP, so client_host is not loopback
    even though the Host header claims loopback.
    """
    from moonmind.security import operator_admission as oa

    _local_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="172.18.0.7",
            host_header="localhost:8000",
            method="GET",
            headers=_headers(),
        )
    assert exc_info.value.http_status == 401


def test_untrusted_forwarding_headers_never_admit(monkeypatch):
    from moonmind.security import operator_admission as oa

    _local_env(monkeypatch)
    forged = _headers(
        **{
            "X-Forwarded-For": "127.0.0.1",
            "X-Forwarded-Host": "127.0.0.1",
            "X-Forwarded-Proto": "http",
            "X-Moonmind-User": "operator",
            "X-Real-IP": "127.0.0.1",
        }
    )
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="172.18.0.7",
            host_header="api:8000",
            method="GET",
            headers=forged,
        )
    assert exc_info.value.http_status == 401


def test_worker_credentials_never_confer_operator_access(monkeypatch):
    from moonmind.security import operator_admission as oa

    _local_env(monkeypatch)
    worker = _headers(
        **{
            "Authorization": "Bearer worker-token-abc",
            "X-Moonmind-Execution-Fanout": "v1",
        }
    )
    with pytest.raises(oa.OperatorAdmissionError):
        oa.resolve_operator_admission(
            client_host="172.18.0.7",
            host_header="api:8000",
            method="GET",
            headers=worker,
        )


def test_remote_approved_ingress_admits_without_user_mapping(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    admission = oa.resolve_operator_admission(
        client_host="10.20.0.5",
        host_header="operator.example.com",
        method="GET",
        headers=_headers(**{"X-Moonmind-User": "alice-stable-id"}),
    )
    assert admission.via == "trusted_ingress"
    # Boolean admission: no persisted person is resolved or imported.
    assert admission.subject is None


def test_remote_forged_header_from_untrusted_peer_denied(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="198.51.100.9",
            host_header="operator.example.com",
            method="GET",
            headers=_headers(**{"X-Moonmind-User": "alice-stable-id"}),
        )
    assert exc_info.value.http_status == 401


def test_remote_direct_backend_loopback_denied(monkeypatch):
    """Container-local loopback never admits once a remote URL is configured."""
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="127.0.0.1",
            host_header="operator.example.com",
            method="GET",
            headers=_headers(),
        )
    assert exc_info.value.http_status == 401


def test_remote_hostile_host_denied(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="10.20.0.5",
            host_header="evil.example.com",
            method="GET",
            headers=_headers(**{"X-Moonmind-User": "alice-stable-id"}),
        )
    assert exc_info.value.http_status == 403
    assert exc_info.value.code == "host_forbidden"


def test_remote_hostile_origin_mutation_denied(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="10.20.0.5",
            host_header="operator.example.com",
            method="POST",
            headers=_headers(
                **{
                    "X-Moonmind-User": "alice-stable-id",
                    "Origin": "https://evil.example.com",
                }
            ),
        )
    assert exc_info.value.http_status == 403


def test_remote_invalid_credential_denied(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(monkeypatch)
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="10.20.0.5",
            host_header="operator.example.com",
            method="GET",
            headers=_headers(**{"X-Moonmind-User": "local"}),
        )
    assert exc_info.value.http_status == 401
    assert exc_info.value.code == "auth_invalid"


def test_remote_misconfigured_ingress_is_unavailable(monkeypatch):
    from moonmind.security import operator_admission as oa

    _remote_env(
        monkeypatch,
        MOONMIND_TRUSTED_INGRESS=None,
        MOONMIND_TRUSTED_PROXIES=None,
    )
    with pytest.raises(oa.OperatorAdmissionError) as exc_info:
        oa.resolve_operator_admission(
            client_host="10.20.0.5",
            host_header="operator.example.com",
            method="GET",
            headers=_headers(**{"X-Moonmind-User": "alice-stable-id"}),
        )
    assert exc_info.value.http_status == 503


def test_no_user_table_imports_in_boundary():
    import pathlib

    source = pathlib.Path("moonmind/security/operator_admission.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "from api_service.db" not in lowered
    assert "session.get(user" not in lowered
    assert "get_or_create_default_user" not in lowered
    assert "usermanager" not in lowered


def test_stream_revalidation_bound_reuses_existing_bound():
    from moonmind.security import operator_admission as oa
    from moonmind.security import session_authority_4121 as s

    assert oa.STREAM_REVALIDATION_SECONDS == s.SESSION_REVOCATION_INTERVAL_SECONDS
    now = time.time()
    assert oa.is_stream_authorization_stale(
        admitted_at=now - oa.STREAM_REVALIDATION_SECONDS - 1, now=now
    ) is True
    assert oa.is_stream_authorization_stale(admitted_at=now, now=now) is False


def test_revocation_closes_streams_but_not_durable_work(monkeypatch):
    from moonmind.security import operator_admission as oa

    policy = oa.OperatorStreamPolicy()
    assert policy.closes_streams_on_revocation is True
    assert policy.cancels_admitted_work is False
    assert "durable" in policy.describe().lower()
