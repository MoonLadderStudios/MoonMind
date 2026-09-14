"""Unit coverage for the #3938 first-run harness helpers."""

from moonmind.first_run.harness import (
    FIRST_RUN_PHASES,
    build_live_report,
    build_timing_record,
    check_clean_install_env,
    classify_first_run_failure,
    first_run_fixture,
    plan_scoped_teardown,
    redact_diagnostics,
    resolve_same_authority,
    validate_disposable_project,
)


def test_validate_disposable_project() -> None:
    validate_disposable_project("moonmind-test")
    validate_disposable_project("moonmind-test-a1")
    for bad in ("moonmind", "moonmind-test-", "Moonmind-test", "x", ""):
        try:
            validate_disposable_project(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_check_clean_install_env() -> None:
    assert check_clean_install_env({}, dot_env_exists=False) == []
    violations = check_clean_install_env(
        {"OPENAI_API_KEY": "sk-x"}, dot_env_exists=True
    )
    assert len(violations) == 2


def test_fixture_and_authority_equivalence() -> None:
    fixture = first_run_fixture()
    assert fixture["publication"] == {"mode": "none", "authorized": False}
    auto = {
        "profileId": "p",
        "providerProfileRef": "opencode-zen-free",
        "launchPolicyRef": "omnigent-on-demand@1",
    }
    assert resolve_same_authority(auto, dict(auto)) is True
    other = dict(auto, providerProfileRef="paid-profile")
    assert resolve_same_authority(auto, other) is False


def test_failure_taxonomy_never_counts_provider_failure_as_success() -> None:
    for kind in (
        "provider_unavailable",
        "provider_rate_limited",
        "image_resolution_failed",
        "bootstrap_authority_missing",
        "startup_interrupted",
        "worker_restart",
        "startup_failure",
        "admission_failure",
    ):
        record = classify_first_run_failure(kind)
        assert record["countable_as_success"] is False
        assert record["substitution_permitted"] is False
    assert classify_first_run_failure("unknown")["tier"] == "platform"


def test_scoped_teardown_and_redaction() -> None:
    plan = plan_scoped_teardown(
        "moonmind-test-x", ["moonmind-test-x_a", "moonmind_b", "down -v"]
    )
    assert plan.resources == ["moonmind-test-x_a"]
    assert len(plan.refused) == 2
    assert "ghp_secret" not in redact_diagnostics("token=ghp_secret")


def test_timing_and_live_report() -> None:
    phases = {p: 1.0 for p in FIRST_RUN_PHASES}
    record = build_timing_record(phases, "cold")
    assert record["within_budget"] is True
    report = build_live_report(
        revision="r",
        image_digests={},
        provider="p",
        model="m",
        cache_condition="warm",
        result="ok",
        cleanup_status="clean",
    )
    for key in (
        "revision",
        "image_digests",
        "provider",
        "model",
        "cache_condition",
        "phase_timings",
        "result",
        "cleanup_status",
    ):
        assert key in report
