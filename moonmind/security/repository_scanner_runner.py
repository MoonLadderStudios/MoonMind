"""One selected scanner per Container Job; never execute repository configuration."""

from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from moonmind.security.assessments import content_key
from moonmind.security.repository_scanners import (
    RepositoryScan,
    ScannerExecution,
    ScannerOutputSanitizer,
    sanitize_native,
)
from moonmind.security.scanner_feeds import FeedSnapshotError, scanner_feed


class BoundedProcesses:
    """Admitted tool processes share one deadline, cancellation and file ceiling."""

    def __init__(self, timeout_seconds: int, max_file_bytes: int):
        self.deadline = time.monotonic() + timeout_seconds
        self.max_file_bytes = max_file_bytes
        self.canceled = False

    def cancel(self, _signum=None, _frame=None) -> None:
        self.canceled = True

    def stopped(self) -> bool:
        return self.canceled or time.monotonic() >= self.deadline

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture: Path | None = None,
        errors: Path | None = None,
    ) -> tuple[int | None, str]:
        """Return ``(exit_code, state)``; output goes only to private files."""
        if self.stopped():
            return None, "canceled" if self.canceled else "timed_out"
        try:
            with capture.open("wb") if capture else open(os.devnull, "wb") as stdout, (
                errors.open("wb") if errors else open(os.devnull, "wb")
            ) as stderr:
                # Set the file ceiling in a fresh interpreter before exec;
                # preexec_fn can deadlock a caller that already has threads.
                bootstrap = (
                    "import os,resource,sys; n=int(sys.argv[1]); "
                    "resource.setrlimit(resource.RLIMIT_FSIZE,(n,n)); "
                    "os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"
                )
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-c",
                        bootstrap,
                        str(self.max_file_bytes),
                        *argv,
                    ],
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                while True:
                    try:
                        return child.wait(timeout=0.1), "succeeded"
                    except subprocess.TimeoutExpired:
                        if self.stopped():
                            os.killpg(child.pid, signal.SIGKILL)
                            child.wait()
                            return child.returncode, (
                                "canceled" if self.canceled else "timed_out"
                            )
        except OSError:
            return None, "failed"


def scanner_commands(
    scan: RepositoryScan, *, report_root: Path, config: Path, cache: Path
) -> dict[str, list[str]]:
    """Only admitted flags; no shell, automatic rules, repository config or egress."""
    target = scan.input_path
    if scan.scanner == "gitleaks":
        common = [
            "--redact=100",
            "--report-format=json",
            "--exit-code=1",
            "--config",
            scan.rules[0].path if scan.rules else str(config),
            "--gitleaks-ignore-path",
            "/dev/null",
            "--no-banner",
        ]
        if not scan.allow_inline_suppressions:
            common.append("--ignore-gitleaks-allow")
        pathspec = (
            " -- "
            + target
            + "".join(
                " :(exclude)" + value
                for value in [scan.output_directory, *scan.exclusions]
            )
        )
        history = [
            "gitleaks",
            "git",
            *common,
            "--report-path",
            str(report_root / "history.json"),
            "--log-opts",
            "--all --full-history --no-ext-diff --no-textconv" + pathspec,
            ".",
        ]
        return {
            "history": history,
            "worktree": [
                "gitleaks",
                "dir",
                *common,
                "--report-path",
                str(report_root / "worktree.json"),
                target,
            ],
        }
    if scan.scanner == "opengrep":
        argv = [
            "opengrep",
            "scan",
            "--json",
            "--output",
            str(report_root / "scan.json"),
            "--no-git-ignore",
            "--x-ignore-semgrepignore-files",
            "--metrics=off",
            "--max-target-bytes",
            str(scan.max_input_bytes),
        ]
        if not scan.allow_inline_suppressions:
            argv.append("--disable-nosem")
        for rule in scan.rules:
            argv.extend(["--config", rule.path])
        return {"scan": [*argv, target]}
    argv = [
        "trivy",
        "filesystem",
        "--format=json",
        "--output",
        str(report_root / "scan.json"),
        "--scanners",
        ",".join(scan.trivy_scanners),
        "--config=",
        "--ignorefile=",
        "--secret-config=",
        "--offline-scan",
        "--skip-db-update",
        "--skip-java-db-update",
        "--skip-check-update",
        "--skip-version-check",
        "--disable-telemetry",
        "--cache-dir",
        str(cache),
        "--exit-code=0",
        "--include-non-failures",
    ]
    for rule in scan.rules:
        argv.extend(["--config-check", rule.path])
    return {"scan": [*argv, target]}


def _copy_inputs(
    scan: RepositoryScan, source: Path, stage: Path
) -> tuple[list[str], str, list[str]]:
    source = source.absolute()
    if source.is_symlink() or not source.is_dir():
        raise ValueError("input root is unavailable")
    assets, records, limitations = [], [], []
    used = count = 0
    for directory, dirs, files in os.walk(source, followlinks=False):
        relative = Path(directory).relative_to(source)
        kept = []
        for name in sorted(dirs):
            path = relative / name
            if (source / path).is_symlink():
                limitations.append("symlink_input_not_examined")
                continue
            if name == ".git" and (path != Path(".git") or scan.scanner != "gitleaks"):
                continue
            # Output storage and admitted exclusions never feed a repeat run.
            if any(
                path == Path(v) or Path(v) in path.parents
                for v in [scan.output_directory, *scan.exclusions]
            ):
                continue
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            path = relative / name
            git = path.parts[0] == ".git"
            if name == ".git" and len(path.parts) == 1:
                limitations.append("external_git_metadata_not_admitted")
                continue
            if (
                not git
                and scan.input_path != "."
                and not (
                    path == Path(scan.input_path)
                    or Path(scan.input_path) in path.parents
                )
            ):
                continue
            if any(
                path == Path(v) or Path(v) in path.parents
                for v in [scan.output_directory, *scan.exclusions]
            ):
                continue
            original = source / path
            info = original.lstat()
            if not stat.S_ISREG(info.st_mode):
                limitations.append("nonregular_input_not_examined")
                continue
            count += 1
            used += info.st_size
            if count > scan.max_files or used > scan.max_input_bytes:
                limitations.append("input_bound_exceeded")
                return assets, content_key(records), sorted(set(limitations))
            with original.open("rb") as stream:
                body = stream.read(min(scan.max_input_bytes, info.st_size) + 1)
            if len(body) != info.st_size:
                limitations.append("input_changed_during_snapshot")
                continue
            records.append([path.as_posix(), hashlib.sha256(body).hexdigest()])
            if not git:
                assets.append(path.as_posix())
                if scan.scanner == "trivy" and not scan.allow_inline_suppressions:
                    # Trivy honors source annotations independently of its ignore
                    # file. Disable only their directive in the private scan copy.
                    body = body.replace(
                        b"trivy:ignore", b"moonmind-disabled-ignore"
                    ).replace(b"tfsec:ignore", b"moonmind-disabled-ignore")
            destination = stage / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
    if not assets:
        limitations.append("no_inputs_examined")
    return assets, content_key(records), sorted(set(limitations))


def run_scanner(
    scan: RepositoryScan,
    *,
    source: Path = Path("/workspace"),
    output: Path = Path("/scan-output"),
    database: Path = Path("/opt/moonmind-scanner-db"),
    rules_root: Path = Path("/opt/moonmind-scanner-rules"),
) -> int:
    """Private scratch holds raw reports; only sanitized files leave the container."""
    output.mkdir(parents=True, exist_ok=True)
    processes = BoundedProcesses(scan.timeout_seconds, scan.max_report_bytes)
    previous = signal.signal(signal.SIGTERM, processes.cancel)
    with tempfile.TemporaryDirectory(prefix="moonmind-scanner-") as scratch:
        root = Path(scratch)
        stage, raw, cache = root / "input", root / "raw", root / "cache"
        stage.mkdir()
        raw.mkdir()
        cache.mkdir()
        config = root / "gitleaks.toml"
        config.write_text("[extend]\nuseDefault = true\n")
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "safe.directory",
            "GIT_CONFIG_VALUE_1": "*",
        }

        def execute(argv, *, capture: Path | None = None):
            return processes.run(argv, cwd=stage, env=env, capture=capture)

        metadata = ScannerExecution(
            inputDigest=content_key([]),
            assets=[],
            version="unavailable",
            exits={},
            states={},
        )
        try:
            metadata.assets, metadata.input_digest, metadata.limitations = _copy_inputs(
                scan, source, stage
            )
            for rule in scan.rules:
                path = rules_root / Path(rule.path).relative_to(
                    "/opt/moonmind-scanner-rules"
                )
                if (
                    not path.resolve().is_relative_to(rules_root.resolve())
                    or path.is_symlink()
                    or path.stat().st_size > scan.max_report_bytes
                ):
                    raise ValueError("rule file is not admitted")
                if hashlib.sha256(path.read_bytes()).hexdigest() != rule.sha256:
                    raise ValueError("rule content differs from admitted identity")
            metadata.feed, feed_limitations = scanner_feed(
                scan, database, cache, metadata.assets, now=datetime.now(UTC)
            )
            metadata.limitations.extend(feed_limitations)
            if metadata.feed is not None and metadata.feed.snapshot is not None:
                metadata.database_digest = metadata.feed.snapshot.snapshot_id
            version = root / "version"
            execute(
                [
                    scan.scanner,
                    "version" if scan.scanner == "gitleaks" else "--version",
                ],
                capture=version,
            )
            if version.exists():
                match = re.search(
                    rb"\b[0-9]+\.[0-9]+\.[0-9]+\b", version.read_bytes()[:4096]
                )
                if match:
                    metadata.version = match[0].decode()
            if metadata.version == "unavailable":
                metadata.limitations.append("scanner_version_unavailable")
            if scan.scanner == "gitleaks":
                metadata.git_metadata = (stage / ".git").is_dir()
                if metadata.git_metadata:
                    refs = root / "refs"
                    code, state = execute(
                        ["git", "show-ref", "--head", "--hash"], capture=refs
                    )
                    if code == 0 and state == "succeeded":
                        metadata.git_refs = sorted(
                            set(re.findall(r"(?m)^[a-f0-9]{40,64}$", refs.read_text()))
                        )[:1024]
                    else:
                        metadata.limitations.append("git_refs_unavailable")
                    metadata.shallow = (stage / ".git" / "shallow").exists()
                    if metadata.shallow:
                        metadata.limitations.append("shallow_history")
                    untracked = root / "untracked"
                    code, state = execute(
                        ["git", "ls-files", "--others", "-z"], capture=untracked
                    )
                    if code == 0 and state == "succeeded":
                        metadata.untracked_files = sorted(
                            set(untracked.read_text().rstrip("\0").split("\0"))
                            & set(metadata.assets)
                        )
                    else:
                        metadata.limitations.append("untracked_inventory_unavailable")
                else:
                    metadata.limitations.append("missing_git_metadata")
            for mode, argv in scanner_commands(
                scan, report_root=raw, config=config, cache=cache
            ).items():
                replacements = {
                    r.path: str(
                        rules_root
                        / Path(r.path).relative_to("/opt/moonmind-scanner-rules")
                    )
                    for r in scan.rules
                }
                argv = [replacements.get(arg, arg) for arg in argv]
                if mode == "history" and not metadata.git_metadata:
                    metadata.states[mode], metadata.exits[mode] = "failed", None
                    continue
                code, state = execute(argv)
                metadata.exits[mode], metadata.states[mode] = code, state
        # Retain safe diagnostics for scanner/setup failures.
        except FeedSnapshotError as exc:
            # Absent, partial, corrupt or incompatible data is never coverage.
            metadata.limitations.extend([exc.code, "scanner_prerequisite_failed"])
        except Exception:  # noqa: BLE001
            metadata.limitations.append("scanner_prerequisite_failed")
        finally:
            signal.signal(signal.SIGTERM, previous)
            # Modes a failed prerequisite never started remain on record, so
            # ingestion keeps the reason instead of discarding the evidence.
            for mode in scan.modes:
                metadata.states.setdefault(mode, "failed")
                metadata.exits.setdefault(mode, None)
            sanitizer = ScannerOutputSanitizer()
            native_inputs = {}
            unsafe_metadata = False
            # Observe every mode before releasing any output: a later report
            # can identify a secret repeated in an earlier report's filename.
            for mode in scan.modes:
                path = raw / f"{mode}.json"
                if path.exists():
                    try:
                        body = path.read_bytes()
                        sanitizer.observe_native(
                            scan.scanner, body, scan.max_report_bytes
                        )
                        native_inputs[mode] = body
                    except Exception:  # noqa: BLE001
                        unsafe_metadata = True
                        metadata.limitations.append("native_sanitization_failed")
            for mode, body in native_inputs.items():
                try:
                    safe = sanitize_native(
                        scan.scanner, body, scan.max_report_bytes, sanitizer=sanitizer
                    )
                    (output / f"{mode}.json").write_bytes(safe)
                except Exception:  # noqa: BLE001
                    unsafe_metadata = True
                    metadata.limitations.append("native_sanitization_failed")
            if unsafe_metadata:
                metadata.assets, metadata.untracked_files = [], []
                metadata.limitations.append("unsafe_metadata_withheld")
            metadata.limitations = sorted(set(metadata.limitations))
            try:
                # Admission itself is held by the worker. Retain only hashes
                # identifying its unsafe strings, never the secret corpus.
                sanitizer.metadata(scan.model_dump(mode="json", by_alias=True))
                metadata = ScannerExecution.model_validate(
                    sanitizer.metadata(metadata.model_dump())
                )
                metadata.redacted_metadata_hashes = sorted(
                    sanitizer.redacted_metadata_hashes
                )
            except Exception:  # noqa: BLE001
                metadata = ScannerExecution(
                    inputDigest=content_key([]),
                    assets=[],
                    version="unavailable",
                    exits={mode: None for mode in scan.modes},
                    states={mode: "failed" for mode in scan.modes},
                    limitations=["metadata_sanitization_failed"],
                )
            (output / "execution.json").write_text(
                metadata.model_dump_json(by_alias=True)
            )
    ok = not metadata.limitations and all(
        metadata.states.get(m) == "succeeded"
        and metadata.exits.get(m) in ({0, 1} if scan.scanner == "gitleaks" else {0})
        for m in scan.modes
    )
    print(
        "Scanner execution finished; sanitized evidence is ready."
        if ok
        else "Scanner execution incomplete; inspect retained limitations."
    )
    return 0 if ok else 2


def main(argv: list[str]) -> int:
    try:
        # Attachment jobs name their captured source and owned output; legacy
        # jobs use the live read-only workspace and its nested output mount.
        paths = (
            {"source": Path(argv[2]), "output": Path(argv[3])} if len(argv) == 4 else {}
        )
        return run_scanner(RepositoryScan.model_validate_json(argv[1]), **paths)
    # Never print raw scanner exceptions to container logs.
    except Exception:  # noqa: BLE001
        print("Scanner invocation failed; evidence is incomplete.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
