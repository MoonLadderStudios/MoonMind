import asyncio
import structlog
from typing import Set

from moonmind.auth.resolvers.base import SecretBackendResolver
from moonmind.auth.secret_refs import (
    ParsedSecretRef,
    SecretAccessDeniedError,
    SecretMissingError,
)
from moonmind.utils.logging import SecretRedactor

logger = structlog.get_logger(__name__)


class ExecSecretResolver(SecretBackendResolver):
    """
    Resolves secrets dynamically by executing a local binary (`exec://`).
    
    Security: Only explicitly allowlisted base commands are permitted to prevent
    arbitrary code execution from maliciously crafted references.
    """

    DEFAULT_ALLOWED_BINARIES: Set[str] = {
        "op",       # 1Password CLI
        "gcloud",   # Google Cloud CLI
        "aws",      # AWS CLI
        "az",       # Azure CLI
        "bw",       # Bitwarden CLI
        "bws",      # Bitwarden Secrets CLI
        "vault",    # HashiCorp Vault CLI
    }

    def __init__(self, allowed_binaries: Set[str] | None = None) -> None:
        self.allowed_binaries = allowed_binaries or self.DEFAULT_ALLOWED_BINARIES

    async def resolve(self, ref: ParsedSecretRef) -> str:
        # Example locator: `op?read?op://vault/item/field`
        # Using `?` as delimiter is customary in MoonMind exec locators
        parts = ref.locator.split("?")
        if not parts:
            raise SecretMissingError("Empty exec locator")
            
        command = parts[0]
        
        if command not in self.allowed_binaries:
            logger.warning("exec_binary_not_allowed", binary=command, locator_ref=ref.locator)
            raise SecretAccessDeniedError(f"Binary '{command}' is not in the exec allowlist")

        args = parts[1:] if len(parts) > 1 else []

        try:
            # We use exec directly (not shell) to prevent shell injection vulnerabilities
            proc = await asyncio.create_subprocess_exec(
                command,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError as e:
            logger.error("exec_binary_not_found", binary=command, error=str(e))
            raise SecretMissingError(f"Executable not found: {command}") from e
        except Exception as e:
            logger.error("exec_invocation_failed", command=command, error=str(e))
            raise SecretAccessDeniedError(f"Failed to invoke executable {command}") from e

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8").strip()
            # Do not log stdout as it might contain partial sensitive data
            redacted_error = SecretRedactor.from_environ().scrub(error_msg)
            logger.warning("exec_returned_non_zero", command=command, returncode=proc.returncode, error=redacted_error)
            raise SecretMissingError(
                f"Exec '{command}' failed with exit code {proc.returncode}"
            )

        plaintext = stdout.decode("utf-8").strip()
        if not plaintext:
            raise SecretMissingError(f"Exec '{command}' returned empty output")

        return plaintext
