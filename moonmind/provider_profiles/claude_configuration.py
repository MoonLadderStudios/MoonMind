"""Thin Claude preference adapter over the shared Profile template renderer.

Only the private API-key configuration home is authored here. Authentication,
model selection and tools continue to belong to the admitted shared launch.
This module is portable so hosts run the same validation as admission.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

try:
    from .template_renderer import ProfileTemplateRenderer, template_context_names
except ImportError:  # Packaged into the run-owned host context.
    from template_renderer import ProfileTemplateRenderer, template_context_names

_HOME = "{{runtime_support_dir}}/claude-home"
_SETTINGS = {"outputStyle", "language", "alwaysThinkingEnabled", "spinnerTipsEnabled"}


def validate_configuration(configuration):
    if set(configuration) != {"homePathOverrides", "fileTemplates"}:
        raise ValueError("unsupported Claude configuration fields")
    if configuration["homePathOverrides"] != {"CLAUDE_CONFIG_DIR": _HOME}:
        raise ValueError("home path overrides require the private Claude configuration home")
    files = []
    for raw in configuration["fileTemplates"]:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(by_alias=False, mode="json")
        if not isinstance(raw, dict):
            raise ValueError("Claude file templates must be objects")
        item = {
            "path": raw.get("path"), "format": raw.get("format", "text"),
            "content_template": raw.get("content_template", raw.get("contentTemplate", "")),
            "merge_strategy": raw.get("merge_strategy", raw.get("mergeStrategy", "replace")),
            "permissions": raw.get("permissions", "0600"),
        }
        path = str(item["path"] or "")
        relative = path.removeprefix(_HOME + "/")
        if (not path.startswith(_HOME + "/") or ".." in Path(relative).parts
            or any(ord(character) < 32 or ord(character) == 127 for character in path)):
            raise ValueError("Claude file templates must remain within the private home")
        # These are declarative preferences/instructions, not authentication,
        # hooks, plugins, persisted session state or another launch policy.
        if relative == "settings.json":
            content = item["content_template"]
            if item["format"] != "json" or not isinstance(content, dict) or set(content) - _SETTINGS:
                raise ValueError("Claude settings contain native-only options")
            for key, value in content.items():
                expected = bool if key.endswith("Enabled") else str
                if type(value) is not expected:
                    raise ValueError("invalid Claude session preference")
        elif relative == "CLAUDE.md" or (
            relative.startswith("rules/") and relative.endswith(".md")
            and "{{" not in relative
        ):
            if item["format"] != "text" or not isinstance(item["content_template"], str):
                raise ValueError("Claude instruction files require text")
        else:
            raise ValueError("Claude file templates contain native-only paths")
        if template_context_names(item["content_template"]) - {"workspace_path", "runtime_support_dir"}:
            raise ValueError("Claude file templates require unavailable or secret context")
        if item["merge_strategy"] not in {"replace", "append", "deep_merge"}:
            raise ValueError("unsupported Claude file merge strategy")
        if item["merge_strategy"] == "deep_merge" and item["format"] != "json":
            raise ValueError("Claude deep_merge requires JSON")
        if ProfileTemplateRenderer._parse_permissions(item["permissions"]) != 0o600:
            raise ValueError("Claude file templates require private permissions")
        files.append(item)
    return {"homePathOverrides": {"CLAUDE_CONFIG_DIR": _HOME}, "fileTemplates": files}


def configuration_for_profile(profile):
    try:
        return validate_configuration({
            "homePathOverrides": dict(getattr(profile, "home_path_overrides", None) or {}),
            "fileTemplates": list(getattr(profile, "file_templates", None) or []),
        }), ""
    except (ValueError, TypeError) as error:
        return None, str(error)


def materialize_configuration(configuration, *, runtime_support_dir, workspace_path):
    """Initialize an attempt's home once; retries never append twice."""
    configuration = validate_configuration(configuration)
    support = Path(runtime_support_dir).resolve()
    home = support / "claude-home"
    marker = home / ".moonmind-profile-digest"
    digest = hashlib.sha256(json.dumps([configuration, workspace_path], sort_keys=True).encode()).hexdigest()
    if home.exists():
        if home.is_symlink() or not marker.is_file() or marker.read_text() != digest:
            raise ValueError("Claude private configuration home is already owned")
        return
    support.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=support) as temporary:
        stage = Path(temporary)
        renderer = ProfileTemplateRenderer(toml_serializer=lambda _: "")
        context = {"runtime_support_dir": str(support), "workspace_path": workspace_path}
        for item in configuration["fileTemplates"]:
            # Render paths into staging but retain the real attempt paths in content.
            rendered = renderer._render_value(item["content_template"], context)
            staged = {**item, "content_template": rendered,
                      "path": item["path"].replace("{{runtime_support_dir}}", str(stage))}
            renderer._materialize_file_template(SimpleNamespace(**staged), {"runtime_support_dir": str(stage), "workspace_path": workspace_path})
        staged_home = stage / "claude-home"
        staged_home.mkdir(mode=0o700, exist_ok=True)
        staged_home.chmod(0o700)
        (staged_home / marker.name).write_text(digest)
        (staged_home / marker.name).chmod(0o600)
        os.rename(staged_home, home)
