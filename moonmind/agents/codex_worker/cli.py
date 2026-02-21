"""CLI entrypoint for the standalone MoonMind Codex worker daemon."""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import subprocess
from typing import Mapping, Sequence

from celery_worker.runtime_mode import (
    format_invalid_gemini_cli_auth_mode_error,
    inspect_gemini_home_for_auth_mode,
    is_invalid_gemini_cli_auth_mode,
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
)
from moonmind.rag.guardrails import GuardrailError, ensure_rag_ready
from moonmind.rag.settings import RagRuntimeSettings
from moonmind.workflows.skills.registry import get_stage_adapter


def _resolve_worker_runtime(env: Mapping[str, str]) -> str:
    runtime = (
        str(env.get("MOONMIND_WORKER_RUNTIME", "codex")).strip().lower() or "codex"
    )
    allowed = {"codex", "gemini", "claude", "universal"}
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
            "SPEC_WORKFLOW_DEFAULT_SKILL",
            "MOONMIND_DEFAULT_SKILL",
        ),
        default="speckit",
    )
    discover_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_DISCOVER_SKILL",
            "SPEC_WORKFLOW_DISCOVER_SKILL",
            "MOONMIND_DISCOVER_SKILL",
        ),
        default=default_skill,
    )
    submit_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_SUBMIT_SKILL",
            "SPEC_WORKFLOW_SUBMIT_SKILL",
            "MOONMIND_SUBMIT_SKILL",
        ),
        default=default_skill,
    )
    publish_skill = _first_non_empty(
        source,
        (
            "WORKFLOW_PUBLISH_SKILL",
            "SPEC_WORKFLOW_PUBLISH_SKILL",
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


def _configured_skills_require_speckit(source: Mapping[str, str]) -> bool:
    """Return whether current worker config requires Speckit executable checks."""

    if not _env_flag(
        _first_non_empty(source, ("WORKFLOW_USE_SKILLS", "SPEC_WORKFLOW_USE_SKILLS")),
        default=True,
    ):
        return False
    return any(
        get_stage_adapter(skill_name) == "speckit"
        for skill_name in _configured_stage_skills(source)
    )


def _redact_value(text: str, secrets: Sequence[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


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

    message = (
        result.stderr.strip()
        or result.stdout.strip()
        or f"command failed: {' '.join(command)}"
    )
    raise RuntimeError(_redact_value(message, redaction_values))


def _verify_speckit_cli(
    speckit_path: str,
    *,
    redaction_values: Sequence[str] = (),
) -> None:
    """Validate Speckit CLI across legacy and shimmed command variants."""

    try:
        _run_checked_command(
            [speckit_path, "--version"],
            redaction_values=redaction_values,
        )
        return
    except RuntimeError as exc:
        # Some environments ship `speckit` as a `specify` shim that does not
        # expose `--version`. Fall back to `--help` to verify executability.
        if "no such option: --version" not in str(exc).lower():
            raise

    _run_checked_command(
        [speckit_path, "--help"],
        redaction_values=redaction_values,
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


def _is_cli_usage_error(message: str) -> bool:
    """Return whether failure text looks like an unsupported-command error."""

    lowered = message.strip().lower()
    return any(
        marker in lowered
        for marker in (
            "unknown command",
            "no such option",
            "unrecognized option",
            "invalid choice",
        )
    )


def _run_claude_auth_status_check(
    *,
    claude_path: str,
    source: Mapping[str, str],
    redaction_values: Sequence[str] = (),
) -> None:
    """Validate Claude auth status with a configurable command + compatibility fallback."""

    custom_raw = str(source.get("MOONMIND_CLAUDE_AUTH_STATUS_COMMAND", "")).strip()
    if custom_raw:
        custom_command = shlex.split(custom_raw)
        if not custom_command:
            raise RuntimeError(
                "MOONMIND_CLAUDE_AUTH_STATUS_COMMAND cannot be empty when set"
            )
        _run_checked_command(
            custom_command,
            redaction_values=redaction_values,
        )
        return

    first_error: RuntimeError | None = None
    fallback_error: RuntimeError | None = None

    try:
        _run_checked_command(
            [claude_path, "auth", "status"],
            redaction_values=redaction_values,
        )
        return
    except RuntimeError as exc:
        first_error = exc
        if not _is_cli_usage_error(str(exc)):
            raise

    try:
        _run_checked_command(
            [claude_path, "login", "status"],
            redaction_values=redaction_values,
        )
        return
    except RuntimeError as exc:
        fallback_error = exc

    raise RuntimeError(
        "Claude authentication status check failed for both "
        "`claude auth status` and `claude login status`. "
        f"Primary error: {first_error}. Fallback error: {fallback_error}"
    ) from fallback_error


def run_preflight(env: Mapping[str, str] | None = None) -> None:
    """Validate CLI dependencies and auth state before daemon start."""

    source = env if env is not None else os.environ
    runtime = _resolve_worker_runtime(source)

    codex_path: str | None = None
    if runtime in {"codex", "universal"}:
        try:
            codex_path = verify_cli_is_executable("codex")
        except CliVerificationError as exc:
            raise RuntimeError(str(exc)) from exc

    gemini_path: str | None = None
    if runtime in {"gemini", "universal"}:
        try:
            gemini_path = verify_cli_is_executable(
                str(source.get("MOONMIND_GEMINI_BINARY", "gemini")).strip() or "gemini"
            )
        except CliVerificationError as exc:
            raise RuntimeError(str(exc)) from exc

    claude_path: str | None = None
    if runtime in {"claude", "universal"}:
        try:
            claude_path = verify_cli_is_executable(
                str(source.get("MOONMIND_CLAUDE_BINARY", "claude")).strip() or "claude"
            )
        except CliVerificationError as exc:
            raise RuntimeError(str(exc)) from exc

    speckit_path: str | None = None
    if _configured_skills_require_speckit(source):
        try:
            speckit_path = verify_cli_is_executable("speckit")
        except CliVerificationError as exc:
            raise RuntimeError(str(exc)) from exc

    _validate_embedding_profile(source)
    try:
        ensure_rag_ready(RagRuntimeSettings.from_env(source))
    except GuardrailError as exc:
        raise RuntimeError(str(exc)) from exc

    github_token = str(source.get("GITHUB_TOKEN", "")).strip()
    redaction_values = (github_token,) if github_token else ()

    if speckit_path is not None:
        _verify_speckit_cli(
            speckit_path,
            redaction_values=redaction_values,
        )
    if codex_path is not None:
        _run_checked_command(
            [codex_path, "login", "status"],
            redaction_values=redaction_values,
        )
    if gemini_path is not None:
        gemini_auth_mode, gemini_auth_mode_raw = resolve_gemini_cli_auth_mode(env=source)
        if is_invalid_gemini_cli_auth_mode(gemini_auth_mode_raw):
            raise RuntimeError(
                format_invalid_gemini_cli_auth_mode_error(gemini_auth_mode_raw)
            )

        _gemini_home, gemini_home_issue = inspect_gemini_home_for_auth_mode(
            auth_mode=gemini_auth_mode,
            gemini_home=source.get("GEMINI_HOME"),
            isdir=os.path.isdir,
            access=os.access,
        )
        if gemini_home_issue == "missing_for_oauth":
            raise RuntimeError(
                "GEMINI_HOME is required when MOONMIND_GEMINI_CLI_AUTH_MODE=oauth"
            )
        if gemini_home_issue == "not_directory":
            raise RuntimeError(
                "GEMINI_HOME must point to an existing directory when "
                f"MOONMIND_GEMINI_CLI_AUTH_MODE={gemini_auth_mode}"
            )
        if gemini_home_issue == "not_writable_for_oauth":
            raise RuntimeError(
                "GEMINI_HOME must be writable when "
                "MOONMIND_GEMINI_CLI_AUTH_MODE=oauth"
            )
        _run_checked_command(
            [gemini_path, "--version"],
            redaction_values=redaction_values,
        )
    if claude_path is not None:
        _run_checked_command(
            [claude_path, "--version"],
            redaction_values=redaction_values,
        )
        _run_claude_auth_status_check(
            claude_path=claude_path,
            source=source,
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
