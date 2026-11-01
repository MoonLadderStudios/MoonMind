"""Render admitted runtime commands after the execution context is prepared."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from moonmind.schemas.agent_runtime_models import (
    RuntimeCommandInvocation,
    RuntimeCommandRenderResult,
)

SELECTED_HARNESS_RECOGNITION = "selected_harness"


@dataclass(frozen=True)
class RuntimeCommandRenderer:
    slash_passthrough: bool = False
    native_command_transport: bool = False
    command_allowlist: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def supports_slash_passthrough(self) -> bool:
        return self.slash_passthrough

    def supports_native_command_transport(self) -> bool:
        return self.native_command_transport

    def materialized_command_allowlist(self) -> Mapping[str, Mapping[str, str]]:
        return self.command_allowlist

    def render_runtime_command(self, request: Any) -> RuntimeCommandRenderResult:
        """Render final instruction text after MoonMind context preparation."""

        command = getattr(request, "runtime_command", None)
        instruction = getattr(request, "instruction_ref", None) or ""
        if command is None:
            return RuntimeCommandRenderResult(
                status="ok",
                renderMode="plain_prompt",
                renderedInstruction=instruction,
            )
        if isinstance(command, dict):
            try:
                command = RuntimeCommandInvocation.model_validate(command)
            except ValidationError:
                return RuntimeCommandRenderResult(
                    status="failed",
                    failureReason="runtime_command_render_failed",
                    diagnostics={"message": "Invalid runtime command metadata."},
                )
        if not isinstance(command, RuntimeCommandInvocation):
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={"message": "Invalid runtime command metadata."},
            )
        if command.render_mode == "native_command":
            return self._render_native_runtime_command(instruction, command)
        if command.render_mode == "materialized_command":
            return self._render_materialized_runtime_command(instruction, command)
        if (
            command.recognition_mode == "escaped_literal"
            or not command.requires_runtime_recognition
        ):
            literal = command.instruction_body or instruction
            prepared = self._prepared_context_after_runtime_command(
                instruction,
                command,
            )
            rendered = self._render_literal_runtime_command(
                literal=literal,
                prepared_context=prepared,
            )
            return RuntimeCommandRenderResult(
                status="ok",
                renderMode="plain_prompt",
                renderedInstruction=rendered,
                invocation=command,
            )
        if not self.supports_slash_passthrough():
            return RuntimeCommandRenderResult(
                status="fallback",
                renderMode="plain_prompt",
                renderedInstruction=instruction,
                fallbackEvent={
                    "reason": "unsupported_runtime",
                    "fallbackMode": "literal_prompt",
                },
                invocation=command,
            )
        raw_command = self._runtime_command_text(command)
        if raw_command is None:
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": (
                        "Runtime command metadata must include a non-empty "
                        "rawCommand or command."
                    )
                },
                invocation=command,
            )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        rendered = self._join_instruction_parts(
            raw_command,
            command.instruction_body,
            prepared,
        )
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="prompt_prefix",
            renderedInstruction=rendered,
            invocation=command,
        )

    def _render_native_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> RuntimeCommandRenderResult:
        if not self.supports_native_command_transport():
            return RuntimeCommandRenderResult(
                status="unsupported",
                renderMode="unsupported",
                renderedInstruction=instruction,
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Runtime does not support native command transport."
                },
                invocation=command,
            )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="native_command",
            nativeCommandPayload={
                "type": "runtime.command",
                "command": command.command,
                "args": command.args,
                "rawCommand": command.raw_command,
                "instructionBody": command.instruction_body,
                "preparedContext": prepared,
            },
            invocation=command,
        )

    def _render_materialized_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> RuntimeCommandRenderResult:
        allowlist = self.materialized_command_allowlist()
        target = allowlist.get(command.command)
        materialized_command = command.materialized_command or {}
        if command.hint_status != "hinted" or target is None:
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Command is not allowlisted for materialized rendering."
                },
                invocation=command,
            )
        target_path = str(target.get("path") or "")
        invocation = str(target.get("invocation") or "")
        requested_path = str(materialized_command.get("path") or target_path)
        requested_invocation = str(materialized_command.get("invocation") or invocation)
        if requested_path != target_path or requested_invocation != invocation:
            return RuntimeCommandRenderResult(
                status="failed",
                failureReason="runtime_command_render_failed",
                diagnostics={
                    "message": "Materialized command target is outside the allowlist."
                },
                invocation=command,
            )
        prepared = self._prepared_context_after_runtime_command(instruction, command)
        return RuntimeCommandRenderResult(
            status="ok",
            renderMode="materialized_command",
            renderedInstruction=self._join_instruction_parts(
                invocation,
                command.instruction_body,
                prepared,
            ),
            materializedTargets=[
                {
                    "path": target_path,
                    "command": command.command,
                    "invocation": invocation,
                }
            ],
            invocation=command,
        )

    @staticmethod
    def _join_instruction_parts(*parts: str | None) -> str:
        return "\n\n".join(
            str(part).strip() for part in parts if str(part or "").strip()
        )

    def _render_literal_runtime_command(
        self,
        *,
        literal: str,
        prepared_context: str,
    ) -> str:
        return self._join_instruction_parts(
            "Literal runtime command text:",
            literal,
            prepared_context,
        )

    @staticmethod
    def _runtime_command_text(command: RuntimeCommandInvocation) -> str | None:
        raw_command = command.raw_command.strip()
        if raw_command:
            return raw_command
        command_name = command.command.strip()
        if not command_name:
            return None
        args = command.args.strip()
        return f"/{command_name}{(' ' + args) if args else ''}"

    def _prepared_context_after_runtime_command(
        self,
        instruction: str,
        command: RuntimeCommandInvocation,
    ) -> str:
        prepared = instruction or ""
        raw_parts = [command.raw_command]
        if command.instruction_body:
            raw_parts.append(command.instruction_body)
        raw_instruction = "\n".join(part for part in raw_parts if part)
        for candidate in (raw_instruction, command.instruction_body):
            candidate = str(candidate or "").strip()
            if not candidate:
                continue
            if prepared == candidate:
                return ""
            for separator in ("\n\n", "\n"):
                prefix = f"{candidate}{separator}"
                if prepared.startswith(prefix):
                    return prepared[len(prefix) :].strip()
            # Skill activation can precede the authored command. Remove its
            # complete block once, preserving context on both sides; a body
            # fragment alone must not be removed from unrelated context.
            if command.raw_command.strip() and candidate.startswith(
                command.raw_command.strip()
            ):
                before, matched, after = prepared.partition(f"\n\n{candidate}")
                if matched and (not after or after.startswith("\n")):
                    return self._join_instruction_parts(before, after)
        return prepared.strip()
