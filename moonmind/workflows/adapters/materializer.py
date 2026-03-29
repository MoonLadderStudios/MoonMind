"""Provider Profile Materialization module."""

import logging
import os
import tempfile
from typing import Dict, List, Tuple
from moonmind.schemas.agent_runtime_models import ManagedRuntimeProfile
from moonmind.workflows.adapters.secret_boundary import SecretResolverBoundary

logger = logging.getLogger(__name__)


class ProviderProfileMaterializer:
    """Materializes the environment and command for a Managed Agent Runtime profile."""

    def __init__(
        self,
        base_env: Dict[str, str],
        secret_resolver: SecretResolverBoundary,
    ):
        self._base_env = base_env.copy()
        self._secret_resolver = secret_resolver
        self.generated_files: List[str] = []

    async def materialize(self, profile: ManagedRuntimeProfile) -> Tuple[Dict[str, str], List[str]]:
        """Materialize the environment and compute the command line.

        The materialization pipeline performs the following steps:
        1. Start from the base environment.
        2. Remove any keys listed in ``profile.clear_env_keys``.
        3. Resolve ``profile.secret_refs`` via the configured SecretResolverBoundary
           and inject the resolved values into the environment.
        4. Apply ``profile.file_templates``, writing secret-interpolated content to
           secure temp files and injecting the paths into the environment.
        5. Apply ``profile.env_template``, allowing interpolation of resolved secrets.
        6. Apply ``profile.env_overrides`` as final environment overrides.
        7. Construct the command from ``profile.command_template``; if empty, fall
           back to ``[profile.runtime_id]``.

        Returns a tuple of ``(env, command)``.
        """
        env = self._base_env.copy()

        # Step 2: Clear keys
        for k in profile.clear_env_keys:
            env.pop(k, None)

        # Step 3: Resolve Plaintext Secrets Just In Time
        resolved_secrets = await self._secret_resolver.resolve_secrets(profile.secret_refs)
        env.update(resolved_secrets)

        # Step 4: Materialize file_templates (write secrets to temp files)
        for env_var, template_content in (profile.file_templates or {}).items():
            content = str(template_content)
            for sec_key, sec_val in resolved_secrets.items():
                content = content.replace("{{" + sec_key + "}}", str(sec_val))
            fd, tmp_path = tempfile.mkstemp(prefix="mm_profile_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(content)
                os.chmod(tmp_path, 0o600)
            except Exception:
                os.unlink(tmp_path)
                raise
            env[env_var] = tmp_path
            self.generated_files.append(tmp_path)

        # Step 5: Expand templates
        for k, v in profile.env_template.items():
            val = str(v)
            for sec_key, sec_val in resolved_secrets.items():
                val = val.replace("{{" + sec_key + "}}", str(sec_val))
            env[k] = val

        # Step 6: Delta Overrides
        for k, v in profile.env_overrides.items():
            env[k] = str(v)

        # Step 7: Command Construction
        cmd = profile.command_template.copy()
        if not cmd:
            cmd = [profile.runtime_id]

        return env, cmd

    def cleanup(self) -> None:
        """Called upon worker shutdown to remove any secrets stored in files."""
        for path in self.generated_files:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError as exc:
                # Best-effort cleanup: log and continue on failure to delete generated secret files.
                logger.debug("Failed to remove generated file %s: %s", path, exc)
        self.generated_files.clear()
