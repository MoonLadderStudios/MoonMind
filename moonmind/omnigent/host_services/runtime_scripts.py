"""Generic host entrypoint construction."""

from __future__ import annotations

from typing import Any


_OPENCODE_RUNTIME_ENV = {
    # MoonMind qualifies the exact pinned image + credential + model before
    # launch. Re-fetching mutable catalog data inside every session duplicates
    # that authority and can block ``opencode serve`` before its loopback API is
    # ready. The compiled catalog remains available to the pinned binary.
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    # Omnigent supplies its policy bridge as an explicit configured plugin.
    # OpenCode's unrelated built-in auth plugins are not part of this host
    # class and add another unbounded initialization surface.
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    # The host image is digest-pinned; an in-session updater must not mutate or
    # delay that runtime after its deployment qualification has passed.
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
}

# The on-demand host receives these values from the sandbox egress boundary.
# Omnigent deliberately filters the host environment before spawning a runner,
# so their *names* must survive the host -> runner hop. The OpenCode server then
# inherits the values through its own restricted environment builder.
_OPENCODE_PROXY_ENV_NAMES = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


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
        # Always include the Omnigent venv so `docker exec` probes (attestation,
        # version checks) resolve the omnigent binary without a full PATH.
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
        # Stage harness credentials from read-only volumes into the writable
        # tmpfs home. OpenCode needs to create repos/, cache/ etc. inside its
        # data directory, which a read-only credential mount would block.
        staging_dir = "/run/mm-credentials/opencode"
        opencode_data = "/home/app/.local/share/opencode"
        has_opencode_materializer = any(
            str(attachment.get("targetPath") or "").rstrip("/") == staging_dir
            for handle in credential_handles
            for attachment in handle.get("attachments", [])
        )
        if has_opencode_materializer:
            environment.update(_OPENCODE_RUNTIME_ENV)
            environment["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] = ",".join(
                sorted({*_OPENCODE_RUNTIME_ENV, *_OPENCODE_PROXY_ENV_NAMES})
            )
        script = (
            "set -eu; "
            "unset OPENAI_API_KEY ANTHROPIC_API_KEY OPENCODE_AUTH_CONTENT "
            "OPENCODE_CONFIG OPENCODE_CONFIG_CONTENT; "
            "oldifs=$IFS; IFS=,; "
            "for check in ${MOONMIND_CREDENTIAL_GENERATION_CHECKS:-}; do "
            "path=${check%:*}; generation=${check##*:}; "
            'test -r "$path"; test "$(cat "$path")" = "$generation"; done; '
            'IFS=$oldifs; test -d "$MOONMIND_ACTIVE_SKILLS_DIR"; '
            "if [ -d "
            '"' + staging_dir + '"'
            " ]; then "
            "mkdir -p " + opencode_data + "; "
            "cp "
            '"' + staging_dir + '/auth.json" '
            + opencode_data + "/auth.json; "
            "chown 1000:1000 " + opencode_data + "/auth.json; "
            "chmod 0600 " + opencode_data + "/auth.json; fi; "
            'if [ -n "${MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE:-}" ]; then '
            'test -r "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"; '
            'OMNIGENT_API_TOKEN=$(cat "$MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE"); '
            "export OMNIGENT_API_TOKEN; fi; "
            'exec omnigent host --server "$1" --non-interactive'
        )
        return script, environment


__all__ = ["OmnigentRuntimeScriptService"]
