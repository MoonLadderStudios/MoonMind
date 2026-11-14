"""Source-free, offline checks inside the deployment's scanner image.

The caller supplies one native selection and mounts only its admitted feed
cache read-only. This entrypoint never opens target inputs or credentials,
refreshes feeds, or runs a scan. Source-dependent requirements remain unknown.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from moonmind.schemas.repository_scanner_models import ImageAssessment, RepositoryScan
from moonmind.security.repository_scanner_runner import (
    BoundedProcesses,
    ScannerRulesError,
    resolve_rules,
)
from moonmind.security.scanner_feeds import (
    FeedSnapshotError,
    current_snapshot,
    feed_evidence,
    load_snapshot,
)

_TOOL_PATH = "/usr/local/bin:/usr/bin:/bin"
_MAX_SELECTION_BYTES = 128 * 1024


def _check(component: str, code: str, status: str = "ready") -> dict:
    return {"component": component, "status": status, "code": code}


def _tool_check(scanner: str) -> dict:
    binary = shutil.which(scanner, path=_TOOL_PATH)
    if binary is None:
        return _check(scanner, "scanner_executable_unavailable", "blocked")
    with tempfile.TemporaryDirectory(prefix="scanner-readiness-") as directory:
        root = Path(directory)
        output = root / "version"
        # Opengrep unpacks its packaged runtime on first execution. Only this
        # private scratch is writable; no inherited authentication or config.
        env = {
            "PATH": _TOOL_PATH,
            "HOME": directory,
            "TMPDIR": directory,
            "XDG_CACHE_HOME": directory,
            "SEMGREP_SEND_METRICS": "off",
            "SEMGREP_ENABLE_VERSION_CHECK": "0",
        }
        process = BoundedProcesses(30, 512 * 1024 * 1024)
        code, state = process.run(
            [binary, "version" if scanner == "gitleaks" else "--version"],
            cwd=root,
            env=env,
            capture=output,
        )
        if state != "succeeded" or code != 0:
            return _check(scanner, "scanner_version_unavailable", "blocked")
        with output.open("rb") as stream:
            version = re.search(rb"\b[0-9]+\.[0-9]+\.[0-9]+\b", stream.read(4096))
        if version is None:
            return _check(scanner, "scanner_version_unavailable", "blocked")
    return _check(scanner, "scanner_available")


def inspect_selection(
    selection: dict,
    *,
    database: Path = Path("/opt/moonmind-scanner-db"),
    rules_root: Path = Path("/opt/moonmind-scanner-rules"),
    now: datetime | None = None,
) -> dict:
    """Return bounded diagnostic codes, never configuration or native output."""
    if not isinstance(selection, dict) or set(selection) not in (
        {"repositoryScan"},
        {"imageAssessment"},
    ):
        raise ValueError("invalid_selection")
    repository = "repositoryScan" in selection
    admitted = (
        RepositoryScan.model_validate(selection["repositoryScan"])
        if repository
        else ImageAssessment.model_validate(selection["imageAssessment"])
    )
    scanner = admitted.scanner if repository else "trivy"
    checks = [_tool_check(scanner)]
    if repository:
        try:
            resolve_rules(admitted, rules_root)
            checks.append(_check("rules", "scanner_rules_available"))
        except ScannerRulesError as exc:
            checks.append(_check("rules", exc.code, "blocked"))
    if scanner == "trivy":
        snapshot = None
        try:
            if admitted.database_cache_ref:
                snapshot = (
                    load_snapshot(database, admitted.database_digest)
                    if admitted.database_digest
                    else current_snapshot(database)
                )
                checks.append(_check("feeds", "feed_snapshot_available"))
            evidence = feed_evidence(
                snapshot,
                misconfig=repository and "misconfig" in admitted.trivy_scanners,
                max_age_hours=admitted.feed_max_age_hours,
                now=now or datetime.now(UTC),
            )
            if evidence.stale_components:
                checks.append(
                    _check(
                        "feeds",
                        "feed_snapshot_stale",
                        "blocked" if admitted.require_fresh_feeds else "unverified",
                    )
                )
            elif snapshot is not None:
                checks.append(_check("feeds", "feed_freshness_verified"))
            if evidence.checks == "embedded":
                checks.append(_check("checks", "embedded_checks"))
            if repository and "vuln" in admitted.trivy_scanners:
                if snapshot is None or "vulnerability" not in snapshot.components:
                    checks.append(
                        _check("feeds", "feed_snapshot_incomplete", "blocked")
                    )
                if snapshot is None or "java" not in snapshot.components:
                    checks.append(
                        _check(
                            "java",
                            "java_requirement_source_dependent",
                            "unverified",
                        )
                    )
            elif not repository and admitted.operation != "sbom-generate":
                if snapshot is None or "vulnerability" not in snapshot.components:
                    checks.append(
                        _check("feeds", "feed_snapshot_incomplete", "blocked")
                    )
        except FeedSnapshotError as exc:
            checks.append(_check("feeds", exc.code, "blocked"))
        except OSError:
            checks.append(_check("feeds", "feed_snapshot_unavailable", "blocked"))
    return {
        "schemaVersion": "scanner-readiness/v1",
        "selectionDigest": hashlib.sha256(
            json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "status": (
            "blocked" if any(c["status"] == "blocked" for c in checks) else "ready"
        ),
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) > 1:
            raise ValueError("invalid_selection")
        raw = args[0] if args else sys.stdin.read(_MAX_SELECTION_BYTES + 1)
        if len(raw.encode()) > _MAX_SELECTION_BYTES:
            raise ValueError("invalid_selection")
        result = inspect_selection(json.loads(raw))
    except (ValueError, TypeError, RecursionError):
        print(
            json.dumps(
                {
                    "schemaVersion": "scanner-readiness/v1",
                    "status": "blocked",
                    "checks": [_check("selection", "invalid_selection", "blocked")],
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
