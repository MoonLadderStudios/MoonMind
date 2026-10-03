import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moonmind.workflows.temporal import activity_runtime


@pytest.mark.parametrize("implicit_tls", [False, True])
def test_notification_smtp_verifies_server_certificates(monkeypatch, implicit_tls):
    smtp = MagicMock()
    monkeypatch.setattr(
        activity_runtime.smtplib, "SMTP_SSL" if implicit_tls else "SMTP", smtp
    )
    monkeypatch.setattr(
        activity_runtime,
        "scan_outbound_text",
        lambda *args, **kwargs: SimpleNamespace(allowed=True),
    )
    activity_runtime._send_execution_notification_email(
        {"workflowId": "example"},
        sender="sender@example.invalid",
        recipients=["recipient@example.invalid"],
        smtp_host="smtp.example.invalid",
        smtp_port=465 if implicit_tls else 587,
        smtp_username="operator",
        smtp_password="smtp-test-credential",
        smtp_use_tls=True,
        smtp_use_ssl=implicit_tls,
        timeout_seconds=5,
    )
    client = smtp.return_value.__enter__.return_value
    tls_call = smtp.call_args if implicit_tls else client.starttls.call_args
    context = tls_call.kwargs["context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    client.login.assert_called_once_with("operator", "smtp-test-credential")
