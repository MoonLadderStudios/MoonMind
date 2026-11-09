"""Profile template rendering shared by direct and packaged native launches.

The renderer has no application dependencies. Credential resolution, directory
ownership and cleanup remain with its caller; template/file semantics and the
shared nonsecret environment guard live here. Packaged hosts provide the same maintained TOML serializer explicitly.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SENSITIVE_ENVIRONMENT_KEY_PATTERN = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|auth|credential|docker_host|registry_auth)", re.I)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PROTECTED = re.compile(
    r"^(?:MOONMIND|OMNIGENT|LD|DYLD|PYTHON|NODE|NPM|BASH|SHELL|ENV|BASH_ENV|"
    r"GIT|GH|SSH|SSL|CURL|REQUESTS|AWS|AZURE|GOOGLE|DOCKER|CONTAINER|"
    r"RUBY|PERL|JAVA|JDK|JRE|PHP|LUA|RUST|CARGO|GODEBUG|GOTOOLCHAIN|"
    r"OPENCODE|CLAUDE|CODEX|OPENAI|ANTHROPIC)|"
    r"^(?:PATH|HOME|USER|LOGNAME|PWD|OLDPWD|IFS|CDPATH|SHELLOPTS|BASHOPTS|"
    r"PYTHONPATH|PYTHONHOME|NODE_OPTIONS|TMPDIR|TMP|TEMP|RES_OPTIONS|LOCALDOMAIN|"
    r"HOSTALIASES|HOSTNAME|TZDIR|LOCPATH|NLSPATH|TERMINFO|TERMINFO_DIRS)$|PROXY$",
    re.IGNORECASE,
)
_SECRET_KEY = re.compile(
    r"(?:private|signing|encryption|access)[_-]?key", re.IGNORECASE
)


def is_safe_project_environment_entry(key: str, raw_value: str) -> bool:
    """Whether one literal can use the nonsecret runtime projection."""
    if not isinstance(raw_value, str) or not isinstance(key, str):
        return False
    value = raw_value.strip()
    safe = bool(_NAME.fullmatch(key)) and not (
        _PROTECTED.search(key)
        or SENSITIVE_ENVIRONMENT_KEY_PATTERN.search(key)
        or _SECRET_KEY.search(key)
    )
    safe = safe and bool(value) and not any(
        ord(c) < 32 or ord(c) == 127 for c in raw_value
    )
    if safe and "://" in value:
        try:
            parsed = urlsplit(value)
            safe = not ("@" in parsed.netloc or parsed.query or parsed.fragment)
        except ValueError:
            return False
    return bool(safe)


PROVIDER_LITERAL_LAUNCH_ENV = frozenset(
    {
        "ANTHROPIC_SMALL_FAST_MODEL",
        "API_TIMEOUT_MS",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    }
)


def validate_provider_literal_launch_environment(
    values: dict[str, str],
) -> dict[str, str]:
    """Validate the non-secret tuning values carried by the shared host."""
    unknown = set(values) - PROVIDER_LITERAL_LAUNCH_ENV
    if unknown:
        unknown = {
            key for key in unknown
            if not is_safe_project_environment_entry(key, values[key])
        }
    if unknown:
        raise ValueError(
            f"unsupported provider launch environment keys: {sorted(unknown)}"
        )
    for key, value in values.items():
        if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
            raise ValueError(f"invalid provider launch environment {key}")
    return dict(values)


_TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def template_context_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str):
        names.update(match.group(1).strip() for match in _TEMPLATE_PATTERN.finditer(value))
    elif isinstance(value, dict):
        if set(value) == {"from_secret_ref"}:
            names.add(str(value["from_secret_ref"]).strip())
        else:
            for child in value.values():
                names.update(template_context_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(template_context_names(child))
    return names


def validate_environment_template_context(values: dict[str, str]) -> dict[str, str]:
    """Only attempt-owned, nonsecret paths can cross the shared handoff."""
    for value in values.values():
        names = template_context_names(value)
        if not names or names - {"workspace_path", "runtime_support_dir"}:
            raise ValueError("Profile environment template requires unavailable context")
    return dict(values)


class ProfileTemplateRenderer:
    def __init__(self, *, toml_serializer: Callable[[Any], str]):
        self._toml_serializer = toml_serializer
        self.generated_files: list[str] = []

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

    def _collect_from_secret_ref_aliases(self, value: Any) -> set[str]:
        return template_context_names(value)

    def render_environment(self, values: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
        return {key: self._render_env_value(value, context) for key, value in values.items()}

    def _render_env_value(self, value: Any, context: dict[str, Any]) -> str:
        rendered = self._render_value(value, context)
        if isinstance(rendered, (dict, list)):
            raise ValueError("env_template values must resolve to strings")
        return str(rendered)

    def _materialize_file_template(
        self,
        file_template: Any,
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
        content = self._render_file_content(file_template, file_path, rendered_content)
        file_path.write_text(content, encoding="utf-8")
        os.chmod(file_path, self._parse_permissions(file_template.permissions))
        self.generated_files.append(str(file_path))

    def _render_file_content(
        self,
        file_template: Any,
        file_path: Path,
        rendered_content: Any,
    ) -> str:
        merge_strategy = str(file_template.merge_strategy or "replace").strip().lower()
        if merge_strategy == "replace" or not file_path.exists():
            return self._serialize_file_content(
                rendered_content,
                file_format=file_template.format,
            )
        if merge_strategy == "deep_merge":
            return self._render_deep_merged_file_content(
                file_path=file_path,
                rendered_content=rendered_content,
                file_format=file_template.format,
            )
        if merge_strategy == "append":
            existing = file_path.read_text(encoding="utf-8")
            addition = self._serialize_file_content(
                rendered_content,
                file_format=file_template.format,
            )
            separator = "" if not existing or existing.endswith("\n") else "\n"
            return f"{existing}{separator}{addition}"
        raise ValueError(
            "fileTemplates[].mergeStrategy must be one of: replace, deep_merge, append"
        )

    def _render_deep_merged_file_content(
        self,
        *,
        file_path: Path,
        rendered_content: Any,
        file_format: str,
    ) -> str:
        if not isinstance(rendered_content, dict):
            raise ValueError("deep_merge fileTemplates require object content_template")

        existing = self._parse_existing_structured_file(file_path, file_format=file_format)
        merged = self._deep_merge_dicts(existing, rendered_content)
        return self._serialize_file_content(merged, file_format=file_format)

    def _parse_existing_structured_file(
        self,
        file_path: Path,
        *,
        file_format: str,
    ) -> dict[str, Any]:
        raw = file_path.read_text(encoding="utf-8")
        if not raw.strip():
            return {}
        normalized_format = str(file_format).strip().lower()
        if normalized_format == "json":
            parsed = json.loads(raw)
        elif normalized_format == "toml":
            parsed = tomllib.loads(raw)
        else:
            raise ValueError("deep_merge is supported only for json and toml fileTemplates")
        if not isinstance(parsed, dict):
            raise ValueError("deep_merge existing file content must be an object")
        return parsed

    def _deep_merge_dicts(
        self,
        base: dict[str, Any],
        overlay: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = self._deep_merge_dicts(existing, value)
            else:
                merged[key] = value
        return merged

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
        return self._toml_serializer(value)
