"""Shared helpers for Spec Kit Celery workflows."""

from __future__ import annotations

import os
import shutil


class CliVerificationError(RuntimeError):
    """Raised when a required command-line tool cannot be executed."""

    def __init__(self, message: str, *, cli_path: str | None = None) -> None:
        super().__init__(message)
        self.cli_path = cli_path


def verify_cli_is_executable(cli_name: str) -> str:
    """Ensure a CLI exists on ``PATH`` and is executable.

    Args:
        cli_name: The binary name to locate.

    Returns:
        The resolved absolute path to the executable.

    Raises:
        CliVerificationError: If the CLI cannot be located or executed.
    """

    cli_path = shutil.which(cli_name)
    if not cli_path:
        raise CliVerificationError(
            f"The '{cli_name}' CLI is not available on PATH; rebuild the automation "
            "image to include the bundled CLI.",
            cli_path=None,
        )

    if not os.access(cli_path, os.X_OK):
        raise CliVerificationError(
            f"The '{cli_name}' CLI was found at '{cli_path}' but is not executable.",
            cli_path=cli_path,
        )

    return cli_path


__all__ = ["CliVerificationError", "verify_cli_is_executable"]
