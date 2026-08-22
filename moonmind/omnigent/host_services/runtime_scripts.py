"""Generic host entrypoint construction."""

from __future__ import annotations

from typing import Any


class OmnigentRuntimeScriptService:
    def build_entrypoint(
        self,
        *,
        credential_handles: list[dict[str, Any]],
        skill_attachment: dict[str, Any],
        tool_attachments: tuple[dict[str, Any], ...] = (),
        control_attachment: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, str]]:
        generation_checks: list[str] = []
        for handle in credential_handles:
            for attachment in handle.get("attachments", []):
                target = str(attachment.get("targetPath") or "")
                generation = int(handle["credentialGeneration"])
                if target:
                    generation_checks.append(
                        f"{target}/.moonmind-generation:{generation}"
                    )
        environment = {
            "MOONMIND_CREDENTIAL_GENERATION_CHECKS": ",".join(generation_checks),
            "MOONMIND_ACTIVE_SKILLS_DIR": str(skill_attachment["targetPath"]),
        }
        tool_bins = [
            str(item["targetPath"]).rstrip("/") + "/bin"
            for item in tool_attachments
            if item.get("targetPath")
        ]
        if tool_bins:
            environment["PATH"] = ":".join(
                [*tool_bins, "/opt/venv/bin", "/usr/local/bin", "/usr/bin", "/bin"]
            )
        allowed_runtime_environment = {"OMNIGENT_CONFIG_HOME"}
        for handle in credential_handles:
            for key, value in dict(handle.get("runtimeEnvironment") or {}).items():
                if key not in allowed_runtime_environment or not str(value).startswith(
                    "/"
                ):
                    raise ValueError(
                        f"unsupported credential runtime environment {key}"
                    )
                existing = environment.get(key)
                if existing is not None and existing != value:
                    raise ValueError(
                        f"conflicting credential runtime environment {key}"
                    )
                environment[key] = str(value)
        control_path = (
            f"{control_attachment['targetPath']}/api-token"
            if control_attachment is not None
            else ""
        )
        environment["MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"] = control_path
        script = (
            "set -eu; "
            "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
            "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
            "oldifs=$IFS; IFS=,; "
            "for check in ${MOONMIND_CREDENTIAL_GENERATION_CHECKS:-}; do "
            "path=${check%:*}; generation=${check##*:}; "
            'test -r "$path"; test "$(cat "$path")" = "$generation"; done; '
            'IFS=$oldifs; test -d "$MOONMIND_ACTIVE_SKILLS_DIR"; '
            'if [ -n "${MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE:-}" ]; then '
            'test -r "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"; '
            'OMNIGENT_API_TOKEN=$(cat "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"); '
            "export OMNIGENT_API_TOKEN; fi; "
            'exec omnigent host --server "$1" --non-interactive'
        )
        return script, environment


__all__ = ["OmnigentRuntimeScriptService"]
