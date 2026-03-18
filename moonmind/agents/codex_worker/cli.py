"""CLI entrypoint for the standalone MoonMind Codex worker daemon."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import subprocess
from typing import Mapping, Sequence

from moonmind.agents.codex_worker.runtime_mode import (
    format_invalid_claude_cli_auth_mode_error,
    format_invalid_gemini_cli_auth_mode_error,
    inspect_claude_home_for_auth_mode,
    inspect_gemini_home_for_auth_mode,
    is_invalid_claude_cli_auth_mode,
    is_invalid_gemini_cli_auth_mode,
    resolve_claude_cli_auth_mode,
    resolve_gemini_cli_auth_mode,
)
from moonmind.agents.codex_worker.handlers import CodexExecHandler
from moonmind.agents.codex_worker.utils import (
    CliVerificationError,
    verify_cli_is_executable,
)
from moonmind.agents.codex_worker.worker import (
    CodexWorker,
    CodexWorkerConfig,
    QueueApiClient,
    QueueClientError,
)
from moonmind.jules.runtime import JULES_RUNTIME_DISABLED_MESSAGE
from moonmind.jules.runtime import (
    build_runtime_gate_state as build_jules_runtime_gate_state,
)
from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_CHARS = 1024
_TOKEN_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gh[pousr][-_][A-Za-z0-9_-]{8,}"),
    re.compile(r"github_pat[_-][A-Za-z0-9_-]{8,}", re.IGNORECASE),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bATATT[0-9A-Za-z_-]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def _resolve_worker_runtime(env: Mapping[str, str]) -> str:
    runtime = (
        str(env.get("MOONMIND_WORKER_RUNTIME", "codex")).strip().lower() or "codex"
    )
    allowed = {"codex", "gemini_cli", "claude", "jules", "universal"}
    if runtime not in allowed:
        supported = ", ".join(sorted(allowed))
        raise RuntimeError(f"MOONMIND_WORKER_RUNTIME must be one of: {supported}")
    return runtime


def _env_flag(value: str | None, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _first_non_empty(
    source: Mapping[str, str], keys: Sequence[str], *, default: str = ""
) -> str:
    for key in keys:
        value = str(source.get(key, "")).strip()
        if value:
            return value
    return default


def _configured_stage_skills(source: Mapping[str, str]) -> tuple[str, ...]:
    default_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_DEFAULT_SKILL",
            "MOONMIND_DEFAULT_SKILL",
        ),
        default="auto",
    )
    discover_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_DISCOVER_SKILL",
            "MOONMIND_DISCOVER_SKILL",
        ),
        default=default_skill,
    )
    submit_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_SUBMIT_SKILL",
            "MOONMIND_SUBMIT_SKILL",
        ),
        default=default_skill,
    )
    publish_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_PUBLISH_SKILL",
            "MOONMIND_PUBLISH_SKILL",
        ),
        default=default_skill,
    )
    values = [
        default_skill.strip(),
        discover_skill.strip(),
        submit_skill.strip(),
        publish_skill.strip(),
    ]
    return tuple(dict.fromkeys(value for value in values if value))




def _worker_capabilities(source: Mapping[str, str]) -> tuple[str, ...]:
    """Return normalized worker capability labels from env configuration."""

    raw = str(source.get("MOONMIND_WORKER_CAPABILITIES", "")).strip()
    if not raw:
        return ()
    normalized = []
    for entry in raw.split(","):
        token = entry.strip().lower()
        if token:
            normalized.append(token)
    return tuple(dict.fromkeys(normalized))


def _effective_worker_capabilities(
    source: Mapping[str, str], runtime: str
) -> tuple[str, ...]:
    """Return capabilities using the same defaults as CodexWorkerConfig."""

    configured = _worker_capabilities(source)
    if configured:
        return configured
    if runtime == "universal":
        capabilities = ["codex", "gemini_cli", "claude", "git", "gh"]
        if _jules_runtime_gate_from_env(source).enabled:
            capabilities.insert(3, "jules")
        return tuple(capabilities)
    return (runtime, "git", "gh")


def _jules_runtime_gate_from_env(source: Mapping[str, str]):
    """Return Jules runtime gate state derived from worker environment."""

    return build_jules_runtime_gate_state(
        env=source,
        error_message=JULES_RUNTIME_DISABLED_MESSAGE,
    )


def _redact_value(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _TOKEN_REDACTION_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _truncate_error_message(
    message: str, *, max_chars: int = _MAX_ERROR_MESSAGE_CHARS
) -> str:
    if len(message) <= max_chars:
        return message
    head_chars = min(768, max_chars - 4)
    tail_chars = max_chars - head_chars - 3
    return f"{message[:head_chars]}...{message[-tail_chars:]}"


def _run_checked_command(
    command: list[str],
    *,
    input_text: str | None = None,
    redaction_values: Sequence[str] = (),
    env_overrides: Mapping[str, str] | None = None,
    unset_env_keys: Sequence[str] = (),
) -> None:
    run_kwargs: dict[str, object] = {
        "input": input_text,
        "capture_output": True,
        "text": True,
    }
    if env_overrides or unset_env_keys:
        process_env = os.environ.copy()
        if env_overrides:
            process_env.update(env_overrides)
        for key in unset_env_keys:
            process_env.pop(key, None)
        run_kwargs["env"] = process_env

    result = subprocess.run(command, **run_kwargs)
    if result.returncode == 0:
        return

    detail = (result.stderr or "").strip() or (result.stdout or "").strip()
    command_hint = (
        " ".join(command[:2]) if len(command) > 1 else " ".join(command) or "<empty>"
    )
    if detail:
        message = f"command failed ({result.returncode}): {command_hint} | {detail.splitlines()[-1]}"
        message = _redact_value(message, redaction_values)
        message = _truncate_error_message(message)
        raise RuntimeError(message)

    raise RuntimeError(
        _redact_value(
            f"command failed ({result.returncode}): {command_hint}", redaction_values
        )
    )




def _validate_embedding_profile(env: Mapping[str, str]) -> None:
    """Enforce embedding prerequisites for runtime profiles that use Google."""

    provider = str(env.get("DEFAULT_EMBEDDING_PROVIDER", "google")).strip().lower()
    if provider != "google":
        return

    google_key = str(env.get("GOOGLE_API_KEY", "")).strip()
    gemini_key = str(env.get("GEMINI_API_KEY", "")).strip()
    if google_key or gemini_key:
        return

    model = str(env.get("GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")).strip()
    raise RuntimeError(
        "Google embedding profile is configured "
        f"(provider=google, model={model or 'unknown'}) but GOOGLE_API_KEY "
        "or GEMINI_API_KEY is missing."
    )


def _verify_codex_search_cli(source: Mapping[str, str]) -> str:
    """Validate ripgrep availability for Codex-first repository search defaults."""

    binary = str(source.get("MOONMIND_RG_BINARY", "rg")).strip() or "rg"
    try:
        return verify_cli_is_executable(binary)
    except CliVerificationError as exc:
        raise RuntimeError(
            "Codex runtime requires ripgrep (`rg`) for first-pass repository "
            "search commands. Install ripgrep in the execution environment (or "
            "set MOONMIND_RG_BINARY to a compatible `rg` executable on PATH). "
            f"Details: {exc}"
        ) from exc


def run_preflight(env: Mapping[str, str] | None = None) -> None:
    """Validate CLI dependencies and auth state before daemon start."""

    source = env if env is not None else os.environ
    runtime = _resolve_worker_runtime(source)
    capabilities = _effective_worker_capabilities(source, runtime)
    runtime_verification_order = ("codex", "gemini_cli", "claude", "jules")
    resolved_paths: dict[str, str | None] = {
        "codex": None,
        "gemini_cli": None,
        "claude": None,
        "jules": None,
        "rg": None,
    }

    for runtime_name in runtime_verification_order:
        if runtime_name == "claude":
            if runtime_name not in capabilities:
                continue
            claude_auth_mode, claude_auth_mode_raw = resolve_claude_cli_auth_mode(
                env=source
            )
            if is_invalid_claude_cli_auth_mode(claude_auth_mode_raw):
                raise RuntimeError(
                    format_invalid_claude_cli_auth_mode_error(claude_auth_mode_raw)
                )
            if claude_auth_mode == "api_key":
                anthropic_key = str(source.get("ANTHROPIC_API_KEY", "")).strip()
                claude_key = str(source.get("CLAUDE_API_KEY", "")).strip()
                if not anthropic_key and not claude_key:
                    raise RuntimeError(
                        "ANTHROPIC_API_KEY or CLAUDE_API_KEY is required when "
                        "Claude runtime uses API key authentication."
                    )
            if claude_auth_mode == "oauth":
                claude_home = source.get("CLAUDE_HOME")
                _claude_home, issue = inspect_claude_home_for_auth_mode(
                    auth_mode="oauth",
                    claude_home=claude_home,
                    isdir=os.path.isdir,
                    access=os.access,
                )
                if issue == "missing_for_oauth":
                    raise RuntimeError(
                        "CLAUDE_HOME is required for OAuth Claude CLI authentication."
                    )
                if issue == "not_directory":
                    raise RuntimeError(
                        f"CLAUDE_HOME must point to an existing directory: {_claude_home}"
                    )
                if issue == "not_writable_for_oauth":
                    raise RuntimeError(
                        "CLAUDE_HOME must be writable for OAuth Claude CLI authentication."
                    )
            try:
                resolved_paths["claude"] = verify_cli_is_executable(
                    str(source.get("MOONMIND_CLAUDE_BINARY", "claude")).strip()
                    or "claude"
                )
            except CliVerificationError as exc:
                raise RuntimeError(str(exc)) from exc
            continue
        if runtime_name == "jules":
            if runtime_name not in capabilities:
                continue
            gate = _jules_runtime_gate_from_env(source)
            if not gate.enabled:
                raise RuntimeError(gate.error_message)
            continue

        if runtime_name == "codex":
            if runtime_name not in capabilities:
                continue
            binary = "codex"
        else:
            if runtime_name not in capabilities:
                continue
            binary = (
                str(source.get("MOONMIND_GEMINI_BINARY", "gemini_cli")).strip() or "gemini_cli"
            )

        try:
            resolved_paths[runtime_name] = verify_cli_is_executable(binary)
        except CliVerificationError as exc:
            raise RuntimeError(str(exc)) from exc

    if "codex" in capabilities:
        resolved_paths["rg"] = _verify_codex_search_cli(source)

    _validate_embedding_profile(source)
    try:
        ensure_rag_ready(RagRuntimeSettings.from_env(source))
    except GuardrailError as exc:
        raise RuntimeError(str(exc)) from exc

    github_token = str(source.get("GITHUB_TOKEN", "")).strip()
    redaction_values = (github_token,) if github_token else ()


    if resolved_paths["rg"] is not None:
        _run_checked_command(
            [resolved_paths["rg"], "--version"],
            redaction_values=redaction_values,
        )

    if resolved_paths["codex"] is not None:
        _run_checked_command(
            [resolved_paths["codex"], "login", "status"],
            redaction_values=redaction_values,
        )

    if resolved_paths["gemini_cli"] is not None:
        gemini_auth_mode, gemini_auth_mode_raw = resolve_gemini_cli_auth_mode(
            env=source
        )
        if is_invalid_gemini_cli_auth_mode(gemini_auth_mode_raw):
            raise RuntimeError(
                format_invalid_gemini_cli_auth_mode_error(gemini_auth_mode_raw)
            )

        gemini_home = source.get("GEMINI_CLI_HOME") or source.get("GEMINI_HOME")
        _gemini_home, gemini_home_issue = inspect_gemini_home_for_auth_mode(
            auth_mode=gemini_auth_mode,
            gemini_home=gemini_home,
            isdir=os.path.isdir,
            access=os.access,
        )
        if gemini_home_issue == "missing_for_oauth":
            raise RuntimeError(
                "GEMINI_CLI_HOME (or GEMINI_HOME fallback) is required when "
                "MOONMIND_GEMINI_CLI_AUTH_MODE=oauth"
            )
        if gemini_home_issue == "not_directory":
            raise RuntimeError(
                "GEMINI_CLI_HOME (or GEMINI_HOME fallback) must point to an existing "
                "directory when "
                f"MOONMIND_GEMINI_CLI_AUTH_MODE={gemini_auth_mode}"
            )
        if gemini_home_issue == "not_writable_for_oauth":
            raise RuntimeError(
                "GEMINI_CLI_HOME (or GEMINI_HOME fallback) must be writable when "
                "MOONMIND_GEMINI_CLI_AUTH_MODE=oauth"
            )
        _run_checked_command(
            [resolved_paths["gemini_cli"], "--version"],
            redaction_values=redaction_values,
        )

    if resolved_paths["claude"] is not None:
        _run_checked_command(
            [resolved_paths["claude"], "--version"],
            redaction_values=redaction_values,
        )

    if not github_token:
        return

    try:
        gh_path = verify_cli_is_executable("gh")
    except CliVerificationError as exc:
        raise RuntimeError(str(exc)) from exc

    _run_checked_command(
        [
            gh_path,
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--with-token",
        ],
        input_text=github_token,
        redaction_values=redaction_values,
        unset_env_keys=("GITHUB_TOKEN", "GH_TOKEN"),
    )
    _run_checked_command(
        [gh_path, "auth", "setup-git"],
        redaction_values=redaction_values,
        unset_env_keys=("GITHUB_TOKEN", "GH_TOKEN"),
    )
    _run_checked_command(
        [gh_path, "auth", "status", "--hostname", "github.com"],
        redaction_values=redaction_values,
        unset_env_keys=("GITHUB_TOKEN", "GH_TOKEN"),
    )


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser for worker runtime options."""

    parser = argparse.ArgumentParser(prog="moonmind-codex-worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one claim cycle and exit.",
    )
    return parser


async def _run(args: argparse.Namespace) -> None:
    config = CodexWorkerConfig.from_env()
    config.workdir.mkdir(parents=True, exist_ok=True)

    run_preflight()

    queue_client = QueueApiClient(
        base_url=config.moonmind_url,
        worker_token=config.worker_token,
    )
    if config.worker_token:
        try:
            await queue_client.replace_worker_runtime_capabilities(
                runtime_capabilities=config.build_runtime_capabilities(),
            )
        except QueueClientError as exc:
            logger.warning(
                "Worker could not sync runtime capabilities: %s",
                exc,
            )

    handler = CodexExecHandler(
        workdir_root=config.workdir,
        default_codex_model=config.default_codex_model,
        default_codex_effort=config.default_codex_effort,
    )
    worker = CodexWorker(
        config=config,
        queue_client=queue_client,
        codex_exec_handler=handler,
    )

    try:
        if args.once:
            await worker.run_once()
        else:
            await worker.run_forever()
    finally:
        await queue_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Entry point for `moonmind-codex-worker`."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        parser.exit(status=1, message=f"moonmind-codex-worker failed: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
