"""Provider Profile Materialization module."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    ManagedRuntimeProfile,
    RuntimeFileTemplate,
)
from moonmind.workflows.adapters.secret_boundary import SecretResolverBoundary
from moonmind.provider_profiles.template_renderer import ProfileTemplateRenderer

logger = logging.getLogger(__name__)

class ProviderProfileMaterializer(ProfileTemplateRenderer):
    """Materializes the environment and command for a Managed Agent Runtime profile."""

    def __init__(
        self,
        base_env: dict[str, str],
        secret_resolver: SecretResolverBoundary,
    ):
        from moonmind.provider_profiles.codex_configuration import render_toml

        super().__init__(toml_serializer=render_toml)
        self._base_env = base_env.copy()
        self._secret_resolver = secret_resolver
        self.generated_dirs: list[str] = []

    async def materialize(
        self,
        profile: ManagedRuntimeProfile,
        *,
        workspace_path: str | None = None,
        runtime_support_dir: str | None = None,
    ) -> tuple[dict[str, str], list[str]]:
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
        if profile.runtime_id == "codex_cli" and profile.file_templates:
            from moonmind.provider_profiles.codex_configuration import (
                repaired_managed_templates,
            )

            templates, repaired = repaired_managed_templates(profile)
            if repaired:
                profile = profile.model_copy(
                    update={
                        "file_templates": [
                            RuntimeFileTemplate.model_validate(item)
                            for item in templates
                        ]
                    }
                )

        # Step 2: Clear keys — #3821 launch-boundary defense. Revalidate or
        # rederive the effective clearing policy through the single isolation
        # authority and fail closed when required policy cannot be produced.
        # Only key names (safe metadata) are recorded; never credential values.
        from moonmind.provider_profiles.isolation_policy import (
            IsolationPolicyError,
            resolve_launch_clear_env_keys,
        )

        _behavior = profile.command_behavior
        _behavior_map = _behavior if isinstance(_behavior, dict) else {}
        try:
            _effective_keys, _isolation_meta = resolve_launch_clear_env_keys(
                {
                    "runtime_id": profile.runtime_id,
                    "provider_id": profile.provider_id,
                    "authentication_method": profile.auth_mode,
                    "credential_source": profile.credential_source,
                    "runtime_materialization_mode": profile.runtime_materialization_mode,
                    "auth_state": profile.auth_state,
                    "clear_env_keys": list(profile.clear_env_keys or []),
                    "command_behavior": dict(_behavior_map),
                }
            )
        except IsolationPolicyError as exc:
            logger.warning(
                "provider_profile_isolation_launch_blocked profile_id=%s runtime=%s provider=%s reason=%s",
                profile.profile_id,
                profile.runtime_id,
                profile.provider_id,
                exc.as_detail().get("code", "isolation_error"),
            )
            raise ValueError(
                "Launch blocked: required launch-safety isolation policy cannot "
                f"be safely produced ({exc.message}). Repair the Provider Profile "
                "before launch."
            ) from exc
        logger.debug(
            "provider_profile_isolation_launch_resolved profile_id=%s strategy=%s source=%s keys=%d",
            profile.profile_id,
            _isolation_meta.get("strategy_id"),
            _isolation_meta.get("source"),
            len(_effective_keys),
        )
        for k in _effective_keys:
            env.pop(k, None)

        # Step 3: Resolve Plaintext Secrets Just In Time
        resolved_secrets = await self._secret_resolver.resolve_secrets(profile.secret_refs)
        template_secret_aliases = self._collect_from_secret_ref_aliases(
            profile.env_template
        )
        template_secret_aliases.update(
            self._collect_from_secret_ref_aliases(profile.home_path_overrides)
        )
        for file_template in profile.file_templates or []:
            template_secret_aliases.update(
                self._collect_from_secret_ref_aliases(file_template.path)
            )
            template_secret_aliases.update(
                self._collect_from_secret_ref_aliases(file_template.content_template)
            )
        for alias in template_secret_aliases:
            env.pop(alias, None)
        env.update(
            {
                k: str(v)
                for k, v in resolved_secrets.items()
                if k not in template_secret_aliases
            }
        )

        resolved_workspace_path = (
            str(Path(workspace_path).expanduser().resolve()) if workspace_path else ""
        )
        resolved_support_dir = self._resolve_runtime_support_dir(
            runtime_support_dir=runtime_support_dir,
            workspace_path=workspace_path,
        )
        context: dict[str, Any] = {
            **{k: str(v) for k, v in resolved_secrets.items()},
            "workspace_path": resolved_workspace_path,
            "runtime_support_dir": resolved_support_dir,
        }

        # Step 4: Materialize file_templates
        for file_template in profile.file_templates or []:
            self._materialize_file_template(file_template, context)

        # Step 5: Expand templates
        env.update(self.render_environment(profile.env_template, context))

        # Step 6: Apply home_path_overrides
        for k, v in profile.home_path_overrides.items():
            env[k] = self._render_string(str(v), context)

        # Step 7: Apply oauth_home mount-path environment shaping
        self._apply_oauth_home_runtime_env(profile, env)

        # Step 8: Delta Overrides
        for k, v in profile.env_overrides.items():
            env[k] = str(v)

        # Step 9: Command Construction
        cmd = profile.command_template.copy()
        if not cmd:
            cmd = [profile.runtime_id]

        return env, cmd

    def _resolve_runtime_support_dir(
        self,
        *,
        runtime_support_dir: str | None,
        workspace_path: str | None,
    ) -> str:
        if runtime_support_dir:
            support_dir = Path(runtime_support_dir).expanduser().resolve()
            support_dir.mkdir(parents=True, exist_ok=True)
            return str(support_dir)

        if workspace_path:
            support_dir = Path(workspace_path).expanduser().resolve().parent / ".moonmind"
            support_dir.mkdir(parents=True, exist_ok=True)
            return str(support_dir.resolve())

        temp_dir = Path(tempfile.mkdtemp(prefix="mm_profile_support_")).resolve()
        self.generated_dirs.append(str(temp_dir))
        return str(temp_dir)

    @staticmethod
    def _apply_oauth_home_runtime_env(
        profile: ManagedRuntimeProfile,
        env: dict[str, str],
    ) -> None:
        mount_path = str(profile.volume_mount_path or "").strip()
        if (
            str(profile.runtime_materialization_mode or "").strip() != "oauth_home"
            or not mount_path
        ):
            return

        env["MANAGED_AUTH_VOLUME_PATH"] = mount_path
        runtime_id = str(profile.runtime_id or "").strip()
        if runtime_id == "claude_code":
            env["CLAUDE_HOME"] = mount_path
            # The pinned Claude Code CLI ignores CLAUDE_HOME; the config dir
            # override is what steers credential reads into the OAuth home.
            env["CLAUDE_CONFIG_DIR"] = mount_path
            env["CLAUDE_VOLUME_PATH"] = mount_path
        elif runtime_id == "codex_cli":
            env["CODEX_HOME"] = mount_path

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
        for path in reversed(self.generated_dirs):
            try:
                shutil.rmtree(path)
            except OSError:
                logger.debug(
                    "Failed to remove generated directory %s",
                    path,
                    exc_info=True,
                )
        self.generated_dirs.clear()
