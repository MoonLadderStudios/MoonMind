"""Provider Profile Materialization module."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from moonmind.schemas.agent_runtime_models import (
    ManagedRuntimeProfile,
    RuntimeFileTemplate,
)
from moonmind.workflows.adapters.secret_boundary import SecretResolverBoundary

logger = logging.getLogger(__name__)
_TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


class ProviderProfileMaterializer:
    """Materializes the environment and command for a Managed Agent Runtime profile."""

    def __init__(
        self,
        base_env: Dict[str, str],
        secret_resolver: SecretResolverBoundary,
    ):
        self._base_env = base_env.copy()
        self._secret_resolver = secret_resolver
        self.generated_files: list[str] = []
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

        # Step 2: Clear keys
        for k in profile.clear_env_keys:
            env.pop(k, None)

        # Step 3: Resolve Plaintext Secrets Just In Time
        resolved_secrets = await self._secret_resolver.resolve_secrets(profile.secret_refs)
        env.update({k: str(v) for k, v in resolved_secrets.items()})

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
        for k, v in profile.env_template.items():
            env[k] = self._render_env_value(v, context)

        # Step 6: Apply home_path_overrides
        for k, v in profile.home_path_overrides.items():
            env[k] = self._render_string(str(v), context)

        # Step 7: Delta Overrides
        for k, v in profile.env_overrides.items():
            env[k] = str(v)

        # Step 8: Command Construction
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

    def _render_string(self, value: str, context: dict[str, Any]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in context:
                raise ValueError(f"Unknown template variable: {key!r}")
            return str(context[key])

        return _TEMPLATE_PATTERN.sub(_replace, value)

    def _render_value(self, value: Any, context: dict[str, Any]) -> Any:
        if isinstance(value, str):
            return self._render_string(value, context)
        if isinstance(value, dict):
            if set(value.keys()) == {"from_secret_ref"}:
                secret_key = str(value["from_secret_ref"]).strip()
                if secret_key not in context:
                    raise ValueError(
                        f"Unknown secret ref alias in template: {secret_key!r}"
                    )
                return str(context[secret_key])
            return {
                str(k): self._render_value(v, context)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._render_value(item, context) for item in value]
        return value

    def _render_env_value(self, value: Any, context: dict[str, Any]) -> str:
        rendered = self._render_value(value, context)
        if isinstance(rendered, (dict, list)):
            raise ValueError("env_template values must resolve to strings")
        return str(rendered)

    def _materialize_file_template(
        self,
        file_template: RuntimeFileTemplate,
        context: dict[str, Any],
    ) -> None:
        rendered_path = self._render_string(file_template.path, context)
        if not rendered_path.strip():
            raise ValueError("fileTemplates[].path resolved to blank")

        runtime_support_dir = str(context.get("runtime_support_dir") or "").strip()
        if not runtime_support_dir:
            raise ValueError("runtime_support_dir is required for file materialization")

        support_dir = Path(runtime_support_dir).expanduser().resolve()
        requested_path = Path(rendered_path).expanduser()
        if requested_path.is_absolute():
            file_path = requested_path.resolve()
        else:
            file_path = (support_dir / requested_path).resolve()

        if not file_path.is_relative_to(support_dir):
            raise ValueError("fileTemplates[].path must stay within runtime_support_dir")

        file_path.parent.mkdir(parents=True, exist_ok=True)

        rendered_content = self._render_value(file_template.content_template, context)
        content = self._serialize_file_content(
            rendered_content,
            file_format=file_template.format,
        )
        file_path.write_text(content, encoding="utf-8")
        os.chmod(file_path, self._parse_permissions(file_template.permissions))
        self.generated_files.append(str(file_path))

    @staticmethod
    def _parse_permissions(value: str | int | None) -> int:
        if value is None:
            return 0o600
        if isinstance(value, int):
            return value
        normalized = str(value).strip()
        if not normalized:
            return 0o600
        return int(normalized, 8)

    def _serialize_file_content(self, value: Any, *, file_format: str) -> str:
        if file_format == "toml":
            return self._to_toml(value)
        if file_format == "json":
            return json.dumps(value, indent=2, sort_keys=True) + "\n"
        if isinstance(value, (dict, list)):
            return json.dumps(value, indent=2, sort_keys=True) + "\n"
        return str(value)

    def _to_toml(self, value: Any) -> str:
        if not isinstance(value, dict):
            raise ValueError("TOML file_templates require object content_template")

        lines: list[str] = []
        scalar_items = {
            key: val for key, val in value.items() if not isinstance(val, dict)
        }
        table_items = {
            key: val for key, val in value.items() if isinstance(val, dict)
        }

        for key, val in scalar_items.items():
            lines.append(f"{key} = {self._toml_scalar(val)}")

        for key, val in table_items.items():
            if lines:
                lines.append("")
            lines.extend(self._render_toml_table([str(key)], val))

        return "\n".join(lines) + "\n"

    def _render_toml_table(self, path: list[str], value: dict[str, Any]) -> list[str]:
        lines = [f"[{'.'.join(path)}]"]
        scalar_items = {
            key: val for key, val in value.items() if not isinstance(val, dict)
        }
        table_items = {
            key: val for key, val in value.items() if isinstance(val, dict)
        }

        for key, val in scalar_items.items():
            lines.append(f"{key} = {self._toml_scalar(val)}")

        for key, nested in table_items.items():
            lines.append("")
            lines.extend(self._render_toml_table(path + [str(key)], nested))
        return lines

    def _toml_scalar(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return "[" + ", ".join(self._toml_scalar(item) for item in value) + "]"
        return json.dumps(str(value))

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
