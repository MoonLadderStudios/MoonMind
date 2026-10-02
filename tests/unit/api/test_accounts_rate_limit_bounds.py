from starlette.requests import Request

from api_service.api.routers import accounts_4122 as accounts


def test_rate_limit_storage_is_bounded_for_unique_identities():
    limiter = accounts._AccountsRateLimiter()
    for i in range(5000):
        limiter.allow(key=f"login:{i}", limit=20, window_seconds=60)
    assert len(limiter._buckets) <= 4096


def test_rejected_source_cannot_allocate_more_login_buckets(monkeypatch):
    limiter = accounts._AccountsRateLimiter()
    monkeypatch.setattr(accounts, "_accounts_rate_limiter", limiter)
    request = Request({"type": "http", "client": ("192.0.2.1", 12345)})
    for i in range(20):
        assert accounts._check_rate_limit(request, "login", f"user-{i}") is None
    original = set(limiter._buckets)
    for i in range(20, 200):
        assert (
            accounts._check_rate_limit(request, "login", f"user-{i}").status_code == 429
        )
    assert set(limiter._buckets) == original
