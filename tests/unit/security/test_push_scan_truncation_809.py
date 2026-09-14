"""MoonMind#809: push-scan truncation and scanner-error fail-closed tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.config.settings import settings
from moonmind.publish import service as publish_service_module
from moonmind.publish.service import PublishService

pytestmark = pytest.mark.asyncio


def _enable_high_security(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", "true")
    monkeypatch.setattr(settings.security, "high_security_mode", True)


async def _run_scan_with_texts(monkeypatch, tmp_path: Path, *, log_text: str, files_text: str, diff_text: str):
    _enable_high_security(monkeypatch)
    svc = PublishService()

    async def _fake_read(*, repo_dir, env, timeout, args):
        if args[0] == "log":
            return log_text
        if args[:2] == ["diff", "--name-only"]:
            assert "-z" in args, "push-scan file enumeration must use NUL delimiters"
            return files_text
        return diff_text

    monkeypatch.setattr(svc, "_read_git_text_for_scan", _fake_read)
    return await svc._scan_git_push_before_publish(
        repo_dir=tmp_path,
        branch_name="feature",
        base_ref="origin/main",
        env={},
    )


def _nul_files(*names: str) -> str:
    """Simulate `git diff --name-only -z` output for the given pathnames."""
    return "\x00".join(names) + "\x00"


async def test_push_scan_allows_clean_small_payload(monkeypatch, tmp_path: Path) -> None:
    await _run_scan_with_texts(
        monkeypatch,
        tmp_path,
        log_text="commit abc\nsubject hello\n",
        files_text=_nul_files("app.py"),
        diff_text="+hello\n",
    )


async def test_push_scan_blocks_truncated_commit_metadata(monkeypatch, tmp_path: Path) -> None:
    oversized = "x" * (publish_service_module._PUBLISH_PUSH_SCAN_MAX_COMMIT_METADATA_CHARS + 1)
    with pytest.raises(RuntimeError, match="coverage incomplete"):
        await _run_scan_with_texts(
            monkeypatch,
            tmp_path,
            log_text=oversized,
            files_text=_nul_files("app.py"),
            diff_text="+hello\n",
        )


async def test_push_scan_blocks_truncated_file_list(monkeypatch, tmp_path: Path) -> None:
    max_files = publish_service_module._PUBLISH_PUSH_SCAN_MAX_CHANGED_FILES
    files_text = _nul_files(*(f"file-{i}.py" for i in range(max_files + 1)))
    with pytest.raises(RuntimeError, match="changed file list"):
        await _run_scan_with_texts(
            monkeypatch,
            tmp_path,
            log_text="commit abc\n",
            files_text=files_text,
            diff_text="+hello\n",
        )


async def test_push_scan_preserves_nul_delimited_pathnames(monkeypatch, tmp_path: Path) -> None:
    """A newline-containing pathname must survive enumeration as one entry."""
    _enable_high_security(monkeypatch)
    svc = PublishService()
    seen_pathspecs: list[str] = []

    async def _fake_read(*, repo_dir, env, timeout, args):
        if args[0] == "log":
            return "commit abc\n"
        if args[:2] == ["diff", "--name-only"]:
            assert "-z" in args
            return _nul_files("we\nird.py", "plain.py")
        if "--" in args:
            seen_pathspecs.append(args[-1])
        return "+hello\n"

    monkeypatch.setattr(svc, "_read_git_text_for_scan", _fake_read)
    await svc._scan_git_push_before_publish(
        repo_dir=tmp_path,
        branch_name="feature",
        base_ref="origin/main",
        env={},
    )
    assert seen_pathspecs == ["we\nird.py", "plain.py"]


async def test_push_scan_blocks_uninspected_binary_diff(monkeypatch, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="is binary and was not inspected"):
        await _run_scan_with_texts(
            monkeypatch,
            tmp_path,
            log_text="commit abc\n",
            files_text=_nul_files("blob.bin"),
            diff_text="Binary files a/blob.bin and b/blob.bin differ\n",
        )


async def test_push_scan_blocks_oversized_diff(monkeypatch, tmp_path: Path) -> None:
    oversized_diff = "y" * (publish_service_module._PUBLISH_PUSH_SCAN_MAX_FILE_DIFF_CHARS + 1)
    with pytest.raises(RuntimeError, match="coverage incomplete"):
        await _run_scan_with_texts(
            monkeypatch,
            tmp_path,
            log_text="commit abc\n",
            files_text=_nul_files("app.py"),
            diff_text=oversized_diff,
        )


async def test_push_scan_blocks_secret_without_leaking_value(monkeypatch, tmp_path: Path) -> None:
    secret = "synthetic-push-secret-12345"
    with pytest.raises(RuntimeError) as exc_info:
        await _run_scan_with_texts(
            monkeypatch,
            tmp_path,
            log_text="commit abc\n",
            files_text=_nul_files("app.py"),
            diff_text=f"+password={secret}\n",
        )
    assert "blocked by high security scan" in str(exc_info.value)
    assert secret not in str(exc_info.value)


async def test_push_scan_scanner_error_is_distinct_from_block(monkeypatch, tmp_path: Path) -> None:
    _enable_high_security(monkeypatch)
    svc = PublishService()

    async def _fake_read(*, repo_dir, env, timeout, args):
        if args[0] == "log":
            return "commit abc\n"
        if args[:2] == ["diff", "--name-only"]:
            assert "-z" in args
            return _nul_files("app.py")
        return "+hello\n"

    monkeypatch.setattr(svc, "_read_git_text_for_scan", _fake_read)

    def _boom(*args, **kwargs):
        raise RuntimeError("scanner down")

    monkeypatch.setattr(publish_service_module, "scan_outbound_bundle", _boom)
    with pytest.raises(RuntimeError, match="enforcement unavailable"):
        await svc._scan_git_push_before_publish(
            repo_dir=tmp_path,
            branch_name="feature",
            base_ref="origin/main",
            env={},
        )
