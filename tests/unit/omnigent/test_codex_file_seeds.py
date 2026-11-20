"""Selected Codex enrollment seeds are bounded data, never templates or secrets."""

import asyncio
import os
import subprocess
import sys

import pytest

from moonmind.omnigent.codex_file_seeds import (
    CodexFileSeedUnavailable,
    SEED_READ_SCRIPT,
    capture_codex_file_seeds,
    validate_seed_paths,
)


class LocalReader:
    def __init__(self, root):
        self.root = root
        self.calls = []

    async def run(self, argv, *, input_bytes, output_limit_bytes, timeout_seconds, binary_stdout_limit_bytes=None):
        self.calls.append(argv)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return 0, b"selected", b""
        if argv[:3] == ["docker", "rm", "--force"]:
            return 0, b"", b""
        assert binary_stdout_limit_bytes == output_limit_bytes
        assert output_limit_bytes > 65536
        assert timeout_seconds <= 30
        assert "none" == argv[argv.index("--network") + 1]
        assert "--read-only" in argv
        assert (
            argv[argv.index("--mount") + 1]
            == "type=volume,source=selected,target=/selected,readonly"
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", SEED_READ_SCRIPT, str(self.root)],
            input=input_bytes,
            capture_output=True,
        )
        return result.returncode, result.stdout, result.stderr


@pytest.mark.asyncio
async def test_captures_raw_present_and_absent_not_rendered(tmp_path):
    raw = "Keep {{workspace_path}} literally.\r\n"
    (tmp_path / "AGENTS.md").write_bytes(raw.encode())
    backend = LocalReader(tmp_path)
    receipt = await capture_codex_file_seeds(
        backend,
        volume_ref="selected",
        writer_image_ref="trusted-image",
        paths=["AGENTS.md", "rules/missing.json"],
    )
    assert receipt == {
        "version": 1,
        "files": [
            {"path": "AGENTS.md", "present": True, "content": raw},
            {"path": "rules/missing.json", "present": False},
        ],
    }
    assert (tmp_path / "AGENTS.md").read_bytes() == raw.encode()


@pytest.mark.parametrize(
    "path",
    [
        "auth.json",
        "config.toml",
        "rules/../auth.json",
        "rules//file.md",
        "rules/.hidden.md",
        "rules/{{workspace_path}}.md",
        "../AGENTS.md",
        "/AGENTS.md",
        "preferences.json",
        "rules/hook.py",
    ],
)
def test_capture_path_scope_is_narrower_than_arbitrary_file_writes(path):
    with pytest.raises(CodexFileSeedUnavailable):
        validate_seed_paths([path])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "parent_symlink",
        "hardlink",
        "directory",
        "fifo",
        "invalid_utf8",
        "oversized",
        "secret",
        "json_secret",
        "malformed_json",
    ],
)
async def test_unsafe_seeds_are_refused_without_content_in_error(tmp_path, kind):
    sentinel = "ghp_private_seed_must_not_escape"
    path = tmp_path / "AGENTS.md"
    paths = ["AGENTS.md"]
    if kind == "symlink":
        (tmp_path / "auth.json").write_text(sentinel)
        path.symlink_to(tmp_path / "auth.json")
    elif kind == "parent_symlink":
        elsewhere = tmp_path / "other"
        elsewhere.mkdir()
        (elsewhere / "data.json").write_text("{}")
        (tmp_path / "rules").symlink_to(elsewhere)
        paths = ["rules/data.json"]
    elif kind == "hardlink":
        (tmp_path / "auth.json").write_text(sentinel)
        os.link(tmp_path / "auth.json", path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif kind == "oversized":
        path.write_text("a" * 65537)
    elif kind in {"json_secret", "malformed_json"}:
        (tmp_path / "rules").mkdir()
        paths = ["rules/data.json"]
        (tmp_path / paths[0]).write_text(
            '{"password":"anything"}' if kind == "json_secret" else "{broken"
        )
    else:
        path.write_text(sentinel)
    with pytest.raises(CodexFileSeedUnavailable) as error:
        await capture_codex_file_seeds(
            LocalReader(tmp_path),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=paths,
        )
    assert sentinel not in str(error.value)


@pytest.mark.asyncio
async def test_transport_errors_do_not_echo_seed_output():
    class Failed:
        async def run(self, *args, **kwargs):
            if args[0][:3] == ["docker", "volume", "inspect"]:
                return 0, b"selected", b""
            if args[0][:3] == ["docker", "rm", "--force"]:
                return 0, b"", b""
            return 1, b"private seed", b"private diagnostic"

    with pytest.raises(RuntimeError, match="seed reader failed") as error:
        await capture_codex_file_seeds(
            Failed(),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["AGENTS.md"],
        )
    assert "private" not in str(error.value)


def test_count_and_overlap_are_bounded():
    with pytest.raises(CodexFileSeedUnavailable):
        validate_seed_paths([f"rules/{i}.md" for i in range(33)])
    with pytest.raises(CodexFileSeedUnavailable):
        validate_seed_paths(["rules/a.md", "rules/a.md/b.md"])


@pytest.mark.asyncio
async def test_structured_api_key_cannot_enter_receipt(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules/prefs.json").write_text(
        '{"api_key":"synthetic-unrecognized-format"}'
    )
    with pytest.raises(CodexFileSeedUnavailable):
        await capture_codex_file_seeds(
            LocalReader(tmp_path),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["rules/prefs.json"],
        )


@pytest.mark.asyncio
async def test_cancel_drains_daemon_reader_before_return():
    started = asyncio.Event()
    calls = []

    class Blocking:
        async def run(self, argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["docker", "volume", "inspect"]:
                return 0, b"selected", b""
            if argv[:2] == ["docker", "run"]:
                started.set()
                await asyncio.Event().wait()
            return 0, b"", b""

    task = asyncio.create_task(
        capture_codex_file_seeds(
            Blocking(),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["AGENTS.md"],
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    assert calls[-1][:3] == ["docker", "rm", "--force"]


@pytest.mark.parametrize(
    "content",
    ['{"api_key":"opaque"}', '{"password":"opaque"}', '{"x": [ {"secret":"opaque"} ]}'],
)
def test_restored_receipt_validation_rejects_sensitive_data(content):
    from moonmind.omnigent.codex_file_seeds import validate_seed_capture

    with pytest.raises(CodexFileSeedUnavailable):
        validate_seed_capture(
            {
                "version": 1,
                "files": [
                    {"path": "rules/options.json", "present": True, "content": content}
                ],
            },
            ["rules/options.json"],
        )


@pytest.mark.asyncio
async def test_total_bound_applies_across_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("a" * 40000)
    (tmp_path / "AGENTS.override.md").write_text("b" * 40000)
    with pytest.raises(CodexFileSeedUnavailable):
        await capture_codex_file_seeds(
            LocalReader(tmp_path),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["AGENTS.md", "AGENTS.override.md"],
        )


@pytest.mark.asyncio
async def test_missing_volume_is_not_silently_created():
    calls = []

    class Missing:
        async def run(self, argv, **kwargs):
            calls.append(argv)
            return 1, b"", b"missing"

    with pytest.raises(RuntimeError):
        await capture_codex_file_seeds(
            Missing(),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["AGENTS.md"],
        )
    assert len(calls) == 1 and calls[0][:3] == ["docker", "volume", "inspect"]


@pytest.mark.asyncio
async def test_repeated_cancel_cannot_interrupt_reader_cleanup():
    started, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Blocking:
        async def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "volume", "inspect"]:
                return 0, b"selected", b""
            if argv[:2] == ["docker", "run"]:
                started.set()
                await asyncio.Event().wait()
            cleaning.set()
            await finish.wait()
            return 0, b"", b""

    task = asyncio.create_task(
        capture_codex_file_seeds(
            Blocking(),
            volume_ref="selected",
            writer_image_ref="trusted-image",
            paths=["AGENTS.md"],
        )
    )
    await started.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0.02)
    still_draining = not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    assert still_draining


@pytest.mark.parametrize('content', [
    '{"api_key": "synthetic-unrecognized-format"}',
    'Notes\n```json\n{"api_key": "synthetic-unrecognized-format"}\n```',
    "{'api_key': 'synthetic-unrecognized-format'}",
    r'{"api\u005fkey": "synthetic-unrecognized-format"}',
], ids=['json-key', 'markdown-code-fence', 'single-quoted-key', 'escaped-json-key'])
def test_quoted_credentials_in_markdown_cannot_enter_or_restore_receipt(content):
    from moonmind.omnigent.codex_file_seeds import validate_seed_capture

    with pytest.raises(CodexFileSeedUnavailable) as error:
        validate_seed_capture({'version': 1, 'files': [
            {'path': 'AGENTS.md', 'present': True, 'content': content},
        ]}, ['AGENTS.md'])
    assert 'synthetic-unrecognized-format' not in str(error.value)


@pytest.mark.parametrize('path,content', [
    ('AGENTS.md', 'OPENAI_API_KEY=opaque-value'),
    ('AGENTS.md', 'export OPENAI_API_KEY="opaque-value"'),
    ('AGENTS.md', '{"refresh_token": "opaque-value"}'),
    ('AGENTS.md', r'{"refresh\u005ftoken": "opaque-value"}'),
    ('AGENTS.md', '{"settings": {"clientSecret": "opaque-value"}}'),
    ('AGENTS.md', 'Notes: xoxb-synthetic-token-body'),
    ('AGENTS.md', 'gho_' + 'a' * 24),
    ('rules/options.json', '{"OPENAI_API_KEY": "opaque-value"}'),
    ('rules/options.json', '{"refresh_token": "opaque-value"}'),
    ('rules/options.json', '{"settings": {"clientSecret": "opaque-value"}}'),
    ('rules/options.json', '{"note": "xoxb-synthetic-token-body"}'),
    ('rules/options.toml', 'OPENAI_API_KEY="opaque-value"'),
    ('rules/options.toml', 'refresh_token="opaque-value"'),
    ('rules/options.toml', '[settings]\nclientSecret="opaque-value"'),
    ('rules/options.toml', 'note="xoxb-synthetic-token-body"'),
], ids=[
    'markdown-api-key', 'markdown-exported-api-key', 'markdown-session-key',
    'markdown-escaped-session-key', 'markdown-nested-client-secret',
    'markdown-chat-token-prefix', 'markdown-oauth-prefix',
    'json-api-key', 'json-session-key', 'json-nested-client-secret',
    'json-chat-token-prefix', 'toml-api-key', 'toml-session-key',
    'toml-nested-client-secret', 'toml-chat-token-prefix',
])
def test_known_credential_names_and_prefixes_refuse_before_receipt(content, path):
    from moonmind.omnigent.codex_file_seeds import validate_seed_capture
    with pytest.raises(CodexFileSeedUnavailable) as error:
        validate_seed_capture({'version': 1, 'files': [
            {'path': path, 'present': True, 'content': content},
        ]}, [path])
    assert 'opaque-value' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('cleanup_ok', [True, False])
async def test_timeout_preserves_retry_type_only_after_confirmed_cleanup(cleanup_ok):
    calls = []
    class TimeoutBackend:
        async def run(self, argv, **kwargs):
            calls.append(argv[:3])
            if argv[:3] == ['docker', 'volume', 'inspect']:
                return 0, b'selected', b''
            if argv[:3] == ['docker', 'rm', '--force']:
                return (0 if cleanup_ok else 1), b'', b'private cleanup diagnostic'
            raise TimeoutError('private transport diagnostic')
    expected = TimeoutError if cleanup_ok else RuntimeError
    with pytest.raises(expected) as error:
        await capture_codex_file_seeds(TimeoutBackend(), volume_ref='selected',
            writer_image_ref='trusted-image', paths=['AGENTS.md'])
    assert 'private' not in str(error.value)
    assert calls[-1] == ['docker', 'rm', '--force']


@pytest.mark.parametrize('path,content', [
    ('AGENTS.md', 'Read ~/.codex/AGENTS.md and keep ~/.claude/CLAUDE.md in sync.\n'),
    ('rules/options.json', '{"instructions": "Read ~/.codex/AGENTS.md"}'),
    ('rules/options.toml', 'instructions="Read ~/.codex/AGENTS.md"'),
])
def test_nonsecret_auth_directory_references_preserve_exact_seed_bytes(path, content):
    from moonmind.omnigent.codex_file_seeds import validate_seed_capture

    receipt = {'version': 1, 'files': [
        {'path': path, 'present': True, 'content': content},
    ]}
    assert validate_seed_capture(receipt, [path]) == receipt
