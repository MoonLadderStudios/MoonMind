"""Profile template rendering shared by direct and packaged native launches.

The renderer has no application dependencies. Credential resolution, directory
ownership and cleanup remain with its caller; only template/file semantics live
here. Packaged hosts provide the same maintained TOML serializer explicitly.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

_TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


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
        aliases: set[str] = set()
        if isinstance(value, str):
            for match in _TEMPLATE_PATTERN.finditer(value):
                aliases.add(match.group(1).strip())
        elif isinstance(value, dict):
            if set(value.keys()) == {"from_secret_ref"}:
                aliases.add(str(value["from_secret_ref"]).strip())
                return aliases
            for child in value.values():
                aliases.update(self._collect_from_secret_ref_aliases(child))
        elif isinstance(value, list):
            for child in value:
                aliases.update(self._collect_from_secret_ref_aliases(child))
        return aliases

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

