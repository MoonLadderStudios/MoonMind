"""Virtual-id binding between browser-facing ids and canonical session ids.

The browser never sees provider session ids directly; it addresses sessions by
an opaque chat-binding id. This module owns that translation as a pure bijection
so lifecycle code can stay in terms of canonical bridge session ids.
"""

from __future__ import annotations

from moonmind.omnigent.domain.identifiers import is_chat_binding_id


class VirtualBindingRegistry:
    def __init__(self) -> None:
        self._to_session: dict[str, str] = {}
        self._to_binding: dict[str, str] = {}

    def bind(self, chat_binding_id: str, bridge_session_id: str) -> None:
        if not is_chat_binding_id(chat_binding_id):
            raise ValueError(f"Not a chat-binding id: {chat_binding_id!r}")
        self._to_session[chat_binding_id] = bridge_session_id
        self._to_binding[bridge_session_id] = chat_binding_id

    def session_for(self, chat_binding_id: str) -> str | None:
        return self._to_session.get(chat_binding_id)

    def binding_for(self, bridge_session_id: str) -> str | None:
        return self._to_binding.get(bridge_session_id)


__all__ = ["VirtualBindingRegistry"]
