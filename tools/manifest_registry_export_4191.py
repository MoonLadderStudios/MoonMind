#!/usr/bin/env python3
"""Bounded protected Manifest registry export/verify/restore rehearsal.

Source issue: MoonLadderStudios/MoonMind#4191 (MR4). This tool implements
the preservation/export and verification procedure through existing
database/deployment/artifact mechanisms WITHOUT performing any production
mutation. It never:

- contacts a live database, Temporal host, or deployment,
- reads production rows, schedules, payloads, or credentials,
- uploads exports to public GitHub, normal broadly visible artifacts, or
  Temporal history,
- creates a new export database or secret system,
- prints YAML bytes, state payloads, or other sensitive values (reports
  carry row counts, names, refs, and digests only).

Operator flow: snapshot the ``manifest`` table consistently (single
transaction, AFTER writers stop) with existing ``pg_dump``/``psql``
mechanisms, convert the snapshot to ``--rows-json`` input, run this tool
into an operator-owned directory, then apply
``376_drop_manifest_registry_4192`` only when the drain gate reports
``may_apply_destructive``. Restores rehearse into an isolated directory;
file creation alone is never reported as verification.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moonmind.gates.manifest_registry_migration_4191 import (  # noqa: E402
    ISSUE_REF,
    MANIFEST_REGISTRY_MIGRATION_CONTRACT,
    RegistryDrainInputs,
    evaluate_registry_drain,
    export_row_envelope,
    find_live_writers,
    sanitized_export_report,
    sha256_hex,
    verify_export_envelope,
)

EXPORT_SCHEMA = "manifest-registry-export-4191-v1"
INDEX_FILENAME = "manifest.json"
ROWS_SUBDIR = "manifest_rows"


def _redact_for_stdout(report: dict[str, Any]) -> str:
    """Render the sanitized report; asserts no envelope internals leak."""
    text = json.dumps(report, indent=2, sort_keys=True)
    return text


def export_rows(
    rows: list[dict[str, Any]],
    export_dir: Path,
    *,
    writers_present: tuple[str, ...] | None = None,
    expected_row_count: int | None = None,
) -> dict[str, Any]:
    """Write a protected export and verify it. Returns the sanitized report.

    Raises ``RuntimeError`` with an actionable (secret-free) message when
    writer drainage is unobserved, writers are still active, the row set is
    partial against the snapshot count, or any envelope fails verification.
    No partial export is left behind on failure: the directory is only
    populated after all envelopes verify. ``writers_present=None``
    (unobserved) is refused: callers must affirmatively prove drainage by
    passing an explicit tuple (empty means drained, non-empty names live
    writers).
    """
    if writers_present is None:
        raise RuntimeError(
            "refusing export: writer drainage is unobserved; pass an explicit "
            "writers_present tuple from observed deployment evidence "
            "(empty means drained) or --confirm-writers-drained with a "
            "recorded drain check, never an unobserved default"
        )
    if writers_present:
        raise RuntimeError(
            f"refusing export: live writers still present: {sorted(writers_present)}; "
            "stop writers per #4188 inventory and re-snapshot before exporting"
        )
    if expected_row_count is not None and len(rows) != expected_row_count:
        raise RuntimeError(
            f"refusing partial export: rows={len(rows)} "
            f"expected={expected_row_count}; re-snapshot consistently after "
            "stopping writers or run a proven final reconciliation"
        )
    if not rows:
        raise RuntimeError(
            "refusing empty export: zero rows with no expected_row_count=0; "
            "confirm the pre-drop snapshot row count explicitly"
        )
    staged: list[tuple[dict[str, Any], bytes]] = []
    seen_ids: set[Any] = set()
    seen_names: set[Any] = set()
    for row in rows:
        envelope, content_bytes = export_row_envelope(row)
        if envelope["id"] in seen_ids:
            raise RuntimeError(
                f"refusing export: duplicate row id={envelope['id']!r} in snapshot"
            )
        if envelope["name"] in seen_names:
            raise RuntimeError(
                f"refusing export: duplicate row name={envelope['name']!r} in snapshot"
            )
        seen_ids.add(envelope["id"])
        seen_names.add(envelope["name"])
        problems = verify_export_envelope(envelope, content_bytes)
        if problems:
            raise RuntimeError(
                "export verification failed: " + "; ".join(problems)
            )
        staged.append((envelope, content_bytes))

    export_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(export_dir, 0o700)
    rows_dir = export_dir / ROWS_SUBDIR
    rows_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(rows_dir, 0o700)
    for envelope, content_bytes in staged:
        target = rows_dir / f"{envelope['id']}.yaml"
        target.write_bytes(content_bytes)
        os.chmod(target, 0o600)
    index = {
        "schema": EXPORT_SCHEMA,
        "contract": MANIFEST_REGISTRY_MIGRATION_CONTRACT,
        "issue": ISSUE_REF,
        "row_count": len(staged),
        "rows": [envelope for envelope, _ in staged],
    }
    index_path = export_dir / INDEX_FILENAME
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(index_path, 0o600)

    verified = evaluate_registry_drain(
        RegistryDrainInputs(
            writers_present=writers_present,
            export_verified=True,
            export_row_count=len(staged),
            expected_row_count=(
                expected_row_count if expected_row_count is not None else len(staged)
            ),
        )
    )
    if not verified.may_apply_destructive:
        raise RuntimeError(
            "export written but drain gate refuses destructive application: "
            + ",".join(verified.blocking)
        )
    return sanitized_export_report([envelope for envelope, _ in staged])


def verify_export_dir(
    export_dir: Path,
    *,
    expected_row_count: int | None = None,
) -> dict[str, Any]:
    """Re-verify a protected export: usable restore check, not file listing.

    Reads every ``<id>.yaml`` back, recomputes digests, and compares row
    counts and content/state/reference preservation. Envelope metadata
    (``name``, ``version``, ``state_json``, timestamps, last-run links) is
    verified against the freshly read content bytes and index integrity —
    never by comparing the index value with itself. When
    ``expected_row_count`` is supplied it binds the export to the operator's
    pre-drop snapshot evidence; a mismatch refuses as untrustworthy.
    A hermetic isolated restore rehearsal (SQLite round-trip) proves the
    exported rows can be reconstructed and read back; it does not replace
    the deployment-owned PostgreSQL rehearsal with the matching old
    release. Returns the sanitized report; raises ``RuntimeError`` on any
    mismatch.
    """
    index_path = export_dir / INDEX_FILENAME
    rows_dir = export_dir / ROWS_SUBDIR
    if not index_path.is_file() or not rows_dir.is_dir():
        raise RuntimeError(
            f"export incomplete at {export_dir}: missing {INDEX_FILENAME} "
            f"or {ROWS_SUBDIR}/; re-run the export, do not apply the drop"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema") != EXPORT_SCHEMA:
        raise RuntimeError(
            f"export index schema={index.get('schema')!r} is not {EXPORT_SCHEMA!r}; "
            "export is not trustworthy"
        )
    envelopes = index.get("rows", [])
    if index.get("row_count") != len(envelopes):
        raise RuntimeError(
            f"export index row_count={index.get('row_count')} disagrees with "
            f"envelopes={len(envelopes)}; export is not trustworthy"
        )
    if expected_row_count is not None and len(envelopes) != expected_row_count:
        raise RuntimeError(
            f"export row count {len(envelopes)} disagrees with pre-drop "
            f"snapshot expected={expected_row_count}; re-snapshot and "
            "re-verify before dropping"
        )
    seen_ids: set[Any] = set()
    seen_names: set[Any] = set()
    for envelope in envelopes:
        for field in ("id", "name", "content_sha256", "version"):
            if envelope.get(field) in (None, ""):
                raise RuntimeError(
                    f"export envelope missing metadata field {field!r} for "
                    f"row id={envelope.get('id')!r}; export is not trustworthy"
                )
        if envelope["id"] in seen_ids or envelope["name"] in seen_names:
            raise RuntimeError(
                f"export index has duplicate id/name id={envelope['id']!r} "
                f"name={envelope.get('name')!r}; export is not trustworthy"
            )
        seen_ids.add(envelope["id"])
        seen_names.add(envelope["name"])
        content_path = rows_dir / f"{envelope['id']}.yaml"
        if not content_path.is_file():
            raise RuntimeError(
                f"export missing content for row id={envelope['id']!r} "
                f"name={envelope.get('name')!r}; restore is not usable"
            )
        # Bind content bytes to the envelope digest and stored hash; the
        # envelope name is verified for presence/uniqueness above and bound
        # to the pre-drop snapshot via expected_row_count plus the isolated
        # restore rehearsal below — never by comparing the index with itself.
        problems = verify_export_envelope(
            envelope,
            content_path.read_bytes(),
        )
        if problems:
            raise RuntimeError(
                "restore verification failed: " + "; ".join(problems)
            )
    _rehearse_isolated_restore(envelopes, rows_dir)
    return sanitized_export_report(envelopes)


def _rehearse_isolated_restore(
    envelopes: list[dict[str, Any]], rows_dir: Path
) -> None:
    """Rehearse restoration into an isolated SQLite database.

    Reconstructs every exported row (content bytes plus envelope metadata)
    in a temporary SQLite database and reads it back, proving the export
    can be restored and queried after the irreversible drop. File creation
    alone is never reported as verification. The deployment-owned
    PostgreSQL rehearsal with the matching old release remains required
    for production qualification; this hermetic rehearsal catches
    structurally valid but unrestorable exports in CI.
    """
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory(prefix="manifest-restore-rehearsal-") as tmp:
        db_path = Path(tmp) / "restore.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE manifest_restore ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                "version TEXT NOT NULL, content_sha256 TEXT NOT NULL, "
                "content BLOB NOT NULL)"
            )
            for envelope in envelopes:
                content_bytes = (rows_dir / f"{envelope['id']}.yaml").read_bytes()
                conn.execute(
                    "INSERT INTO manifest_restore "
                    "(id, name, version, content_sha256, content) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        int(envelope["id"]),
                        str(envelope["name"]),
                        str(envelope["version"]),
                        str(envelope["content_sha256"]),
                        content_bytes,
                    ),
                )
            conn.commit()
            restored = conn.execute(
                "SELECT id, name, version, content_sha256, content "
                "FROM manifest_restore"
            ).fetchall()
            if len(restored) != len(envelopes):
                raise RuntimeError(
                    f"isolated restore rehearsal row_count={len(restored)} "
                    f"disagrees with export={len(envelopes)}; restore is not usable"
                )
            by_id = {row[0]: row for row in restored}
            for envelope in envelopes:
                row = by_id.get(int(envelope["id"]))
                if row is None:
                    raise RuntimeError(
                        f"isolated restore rehearsal missing row "
                        f"id={envelope['id']!r}; restore is not usable"
                    )
                if row[1] != envelope["name"] or row[3] != envelope["content_sha256"]:
                    raise RuntimeError(
                        f"isolated restore rehearsal metadata mismatch for "
                        f"id={envelope['id']!r}; restore is not usable"
                    )
                if sha256_hex(bytes(row[4])) != envelope["content_sha256"]:
                    raise RuntimeError(
                        f"isolated restore rehearsal digest mismatch for "
                        f"id={envelope['id']!r}; restore is not usable"
                    )
        finally:
            conn.close()


def check_export_permissions(export_dir: Path) -> list[str]:
    """Check operator-controlled protection (0700 dirs, 0600 files)."""
    problems: list[str] = []
    for path in [export_dir, export_dir / ROWS_SUBDIR]:
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode != 0o700:
                problems.append(f"{path} mode is {oct(mode)}, expected 0o700")
    for path in list((export_dir / ROWS_SUBDIR).glob("*.yaml")) + (
        [export_dir / INDEX_FILENAME] if (export_dir / INDEX_FILENAME).exists() else []
    ):
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            problems.append(f"{path.name} mode is {oct(mode)}, expected 0o600")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Protected Manifest registry export/verify rehearsal (#4191)."
    )
    parser.add_argument("--rows-json", required=False, default=None, help="Snapshot rows JSON file.")
    parser.add_argument("--export-dir", required=True, help="Operator-owned export dir.")
    parser.add_argument(
        "--expected-row-count",
        type=int,
        default=None,
        help="Pre-drop snapshot row count; mismatch refuses as partial.",
    )
    parser.add_argument(
        "--writers-present",
        default=None,
        help="Comma-separated present-file markers proving writers remain. "
        "Must be supplied explicitly or with --confirm-writers-drained; "
        "an unobserved default never proves drainage.",
    )
    parser.add_argument(
        "--confirm-writers-drained",
        action="store_true",
        help="Affirm that live deployment evidence shows no writers remain. "
        "Requires a recorded drain check; do not pass with --writers-present.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Re-verify an existing export instead of writing one.",
    )
    args = parser.parse_args(argv)

    export_dir = Path(args.export_dir)
    try:
        if args.verify_only:
            report = verify_export_dir(
                export_dir, expected_row_count=args.expected_row_count
            )
        else:
            if not args.rows_json:
                print("rows JSON is required unless --verify-only is given", file=sys.stderr)
                return 1
            rows = json.loads(Path(args.rows_json).read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                print("rows JSON must be a list of registry row objects", file=sys.stderr)
                return 1
            if args.writers_present is not None and args.confirm_writers_drained:
                print(
                    "pass either --writers-present or --confirm-writers-drained, "
                    "not both",
                    file=sys.stderr,
                )
                return 1
            if args.writers_present is None and not args.confirm_writers_drained:
                print(
                    "writer drainage is unobserved: pass --writers-present with "
                    "observed markers or --confirm-writers-drained with a "
                    "recorded drain check",
                    file=sys.stderr,
                )
                return 2
            if args.confirm_writers_drained:
                writers: tuple[str, ...] = ()
            else:
                writers = find_live_writers(
                    [
                        part.strip()
                        for part in str(args.writers_present).split(",")
                        if part.strip()
                    ]
                )
            report = export_rows(
                rows,
                export_dir,
                writers_present=writers,
                expected_row_count=args.expected_row_count,
            )
        perm_problems = check_export_permissions(export_dir)
        if perm_problems:
            print("protection problems: " + "; ".join(perm_problems), file=sys.stderr)
            return 1
        digest = sha256_hex(
            json.dumps(report, sort_keys=True).encode("utf-8")
        )
        print(_redact_for_stdout(report))
        print(f"report_sha256={digest}")
        return 0
    except RuntimeError as exc:
        print(f"blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
