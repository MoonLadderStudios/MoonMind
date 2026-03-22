"""Tests for ManagedRuntimeStrategy ABC contract."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)


class TestABCCannotBeInstantiated:
    """DOC-REQ-001 / FR-001: ABC enforces abstract contract."""

    def test_abstract_class_raises(self) -> None:
        with pytest.raises(TypeError):
            ManagedRuntimeStrategy()  # type: ignore[abstract]


class TestConcreteDefaults:
    """DOC-REQ-001 / FR-002, FR-003: concrete defaults are sensible."""

    class _MinimalStrategy(ManagedRuntimeStrategy):
        """Minimal concrete subclass implementing only abstract members."""

        @property
        def runtime_id(self) -> str:
            return "test_runtime"

        @property
        def default_command_template(self) -> list[str]:
            return ["test-cmd"]

        def build_command(self, profile, request) -> list[str]:
            return list(profile.command_template)

    def test_default_auth_mode(self) -> None:
        s = self._MinimalStrategy()
        assert s.default_auth_mode == "api_key"

    def test_shape_environment_identity(self) -> None:
        s = self._MinimalStrategy()
        env = {"HOME": "/home/user", "FOO": "bar"}
        result = s.shape_environment(env, None)
        assert result == env
        # Must be a copy, not the same dict
        assert result is not env

    def test_classify_exit_success(self) -> None:
        s = self._MinimalStrategy()
        status, failure_class = s.classify_exit(0, "", "")
        assert status == "completed"
        assert failure_class is None

    def test_classify_exit_failure(self) -> None:
        s = self._MinimalStrategy()
        status, failure_class = s.classify_exit(1, "", "")
        assert status == "failed"
        assert failure_class == "execution_error"

    def test_create_output_parser_returns_none(self) -> None:
        s = self._MinimalStrategy()
        assert s.create_output_parser() is None
