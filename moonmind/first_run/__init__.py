"""First-run harness package (MoonLadderStudios/MoonMind#3938)."""

from moonmind.first_run.harness import (  # noqa: F401
    FIRST_RUN_PHASES,
    TEN_MINUTE_BUDGET_SECONDS,
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
