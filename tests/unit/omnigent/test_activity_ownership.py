import pytest
from temporalio import activity

from moonmind.omnigent.activity_ownership import delivery_was_revoked


@pytest.mark.parametrize(
    "reason", ["worker_shutdown", "timed_out", "paused", "reset", "not_found"]
)
@pytest.mark.parametrize("cancel_requested", [False, True])
def test_sdk_cancellation_reason_owns_cleanup(monkeypatch, reason, cancel_requested):
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    details = activity.ActivityCancellationDetails(
        **{reason: True, "cancel_requested": cancel_requested}
    )
    monkeypatch.setattr(activity, "cancellation_details", lambda: details)
    assert delivery_was_revoked() is (not cancel_requested)


def test_non_activity_cancellation_retains_normal_cleanup():
    assert not delivery_was_revoked()
