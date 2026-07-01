from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import sys


_MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "audit_status_tokens.py"
)
_SPEC = importlib.util.spec_from_file_location("audit_status_tokens", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
audit_status_tokens = importlib.util.module_from_spec(_SPEC)
sys.modules["audit_status_tokens"] = audit_status_tokens
_SPEC.loader.exec_module(audit_status_tokens)


def test_seeded_tokens_have_required_domains_and_actions() -> None:
    expected = {
        "mm_state": ("workflow_lifecycle_state", "keep_canonical"),
        "closeStatus": ("temporal_close_status", "keep_canonical"),
        "temporalStatus": ("temporal_status", "keep_canonical"),
        "no_changes": ("legacy_or_migration_status", "historical_migration_only"),
        "awaiting_action": (
            "dashboard_compatibility_status",
            "rename_domain_specific",
        ),
        "queued": ("provider_normalized_status", "move_to_provider_boundary"),
        "in-progress": ("provider_native_status", "move_to_provider_boundary"),
        "StepExecutionStatus": ("step_execution_artifact_status", "keep_canonical"),
    }

    for token, (domain, action) in expected.items():
        classification = audit_status_tokens.classify_token(token)
        assert classification.guessed_domain == domain
        assert classification.action == action


def test_report_emits_required_columns_and_file_matches(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    code = tmp_path / "moonmind"
    docs.mkdir()
    code.mkdir()
    (docs / "status.md").write_text(
        "The API exposes temporalStatus and closeStatus.\n", encoding="utf-8"
    )
    (code / "provider.py").write_text(
        "provider_status = 'in-progress'\nnormalized_status = 'queued'\n",
        encoding="utf-8",
    )

    rows = audit_status_tokens.build_report_rows(
        root=tmp_path,
        scan_roots=("docs", "moonmind"),
        tokens=("temporalStatus", "closeStatus", "queued", "in-progress"),
    )

    assert set(rows[0]) == {
        "token",
        "guessed_domain",
        "files",
        "canonicality",
        "action",
    }
    by_token = {row["token"]: row for row in rows}
    assert by_token["temporalStatus"]["files"] == "docs/status.md"
    assert by_token["closeStatus"]["files"] == "docs/status.md"
    assert by_token["queued"]["files"] == "moonmind/provider.py"
    assert by_token["in-progress"]["files"] == "moonmind/provider.py"


def test_csv_report_is_parseable(tmp_path: Path, capsys) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "status.md").write_text("mm_state\n", encoding="utf-8")

    exit_code = audit_status_tokens.main(
        ["--root", str(tmp_path), "--scan-root", "docs", "--token", "mm_state"]
    )

    assert exit_code == 0
    parsed = list(csv.DictReader(capsys.readouterr().out.splitlines()))
    assert parsed == [
        {
            "token": "mm_state",
            "guessed_domain": "workflow_lifecycle_state",
            "files": "docs/status.md",
            "canonicality": "canonical",
            "action": "keep_canonical",
        }
    ]
