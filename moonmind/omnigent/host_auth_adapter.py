"""Adapter to the pinned upstream Omnigent runner-tunnel authentication.

This module deliberately contains no token parsing or hashing.  Those semantics
remain owned by the pinned Omnigent submodule and are invoked through the same
verifier used by its stock websocket tunnel route.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Mapping

PINNED_OMNIGENT_COMMIT = "b95e41eca8b52723b0c154cfda6f06e681eba447"
PINNED_PROTOCOL_PROFILE = "omnigent.runner_tunnel.b95e41ec"


class UpstreamHostAuthError(RuntimeError):
    """Stable, credential-free failure raised by the upstream adapter."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class HostCredentialGeneration:
    """Resolved, service-side credential material for one generation.

    Instances are deliberately ephemeral. Durable configuration carries the
    secret reference and generation; resolution supplies ``token`` only at the
    request boundary.
    """

    secret_ref: str
    generation: int
    token: str
    revoked: bool = False

    def __post_init__(self) -> None:
        if not self.secret_ref or "://" not in self.secret_ref:
            raise ValueError("host credential secret_ref is required")
        if self.generation < 1:
            raise ValueError("host credential generation must be positive")
        if not self.token:
            raise ValueError("resolved host credential is empty")


@dataclass(frozen=True, slots=True)
class UpstreamHostIdentity:
    runner_id: str
    credential_generation: int
    protocol_profile: str = PINNED_PROTOCOL_PROFILE


class OmnigentHostAuthAdapter:
    """Execute the pinned upstream runner tunnel verifier."""

    def __init__(self, *, credentials: tuple[HostCredentialGeneration, ...]) -> None:
        if not credentials:
            raise UpstreamHostAuthError(
                "embedded host credential is not configured",
                code="host_auth_not_configured",
            )
        active = tuple(item for item in credentials if not item.revoked)
        if not active:
            raise UpstreamHostAuthError(
                "embedded host credential is revoked",
                code="host_credential_revoked",
            )
        generations = [item.generation for item in credentials]
        if len(generations) != len(set(generations)):
            raise UpstreamHostAuthError(
                "embedded host credential generations are ambiguous",
                code="host_auth_configuration_invalid",
            )
        self._credentials = active
        self._allowed_tokens = frozenset(item.token for item in active)
        # Load only from the repository-pinned bundle. An arbitrary installed
        # ``omnigent`` distribution must never supply authorization semantics
        # while this adapter advertises PINNED_PROTOCOL_PROFILE.
        verify, identity = _load_pinned_source_entrypoints()
        self._verify = verify
        self._token_bound_runner_id = getattr(identity, "token_bound_runner_id")
        self.token_header = str(getattr(identity, "RUNNER_TUNNEL_TOKEN_HEADER"))

    def verify(self, headers: Mapping[str, Any]) -> UpstreamHostIdentity:
        """Return the upstream token-bound identity without retaining secrets."""

        entries = [(str(k), str(v)) for k, v in headers.items()]
        values = [v for k, v in entries if k.lower() == self.token_header.lower()]
        if len(values) != 1:
            raise UpstreamHostAuthError(
                "runner tunnel credential is required exactly once",
                code="host_credential_malformed",
            )
        normalized = {
            (self.token_header if k.lower() == self.token_header.lower() else k): v
            for k, v in entries
        }
        try:
            # The upstream allow-list verifier returns None after authorization;
            # its identity helper is then the authoritative identity conversion.
            self._verify(normalized, allowed_tunnel_tokens=self._allowed_tokens)
            runner_id = self._token_bound_runner_id(values[0])
        except (RuntimeError, ValueError) as exc:
            raise UpstreamHostAuthError(
                "runner tunnel credential was rejected",
                code="host_credential_rejected",
            ) from exc
        generation = next(
            item.generation for item in self._credentials if item.token == values[0]
        )
        return UpstreamHostIdentity(
            runner_id=runner_id,
            credential_generation=generation,
        )


def assert_pinned_omnigent_auth_contract() -> None:
    """Fail preflight if the expected upstream verifier surface has drifted."""

    OmnigentHostAuthAdapter(
        credentials=(
            HostCredentialGeneration(
                secret_ref="env://OMNIGENT_HOST_RUNNER_TOKEN",
                generation=1,
                token="preflight-only",
            ),
        )
    )


def _load_pinned_source_entrypoints() -> tuple[Any, Any]:
    """Execute exact pinned functions when the full upstream package is absent.

    MoonMind's API image does not install the server's optional dependency set.
    The fallback compiles the verifier function directly from the pinned
    submodule source, preserving upstream semantics without copying them.
    """

    root = Path(__file__).resolve().parents[2] / "omnigent" / "omnigent"
    identity_path = root / "runner" / "identity.py"
    route_path = root / "server" / "routes" / "runner_tunnel.py"
    try:
        spec = spec_from_file_location("_moonmind_pinned_omnigent_identity", identity_path)
        if spec is None or spec.loader is None:
            raise ImportError("identity module spec unavailable")
        identity = module_from_spec(spec)
        spec.loader.exec_module(identity)

        tree = ast.parse(route_path.read_text(encoding="utf-8"), filename=str(route_path))
        node = next(
            item
            for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "_expected_runner_id_from_headers"
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace: dict[str, Any] = {
            "Mapping": Mapping,
            "RUNNER_TUNNEL_TOKEN_HEADER": identity.RUNNER_TUNNEL_TOKEN_HEADER,
            "token_bound_runner_id": identity.token_bound_runner_id,
        }
        exec(compile(module, str(route_path), "exec"), namespace)
        return namespace["_expected_runner_id_from_headers"], identity
    except (AttributeError, ImportError, OSError, StopIteration, SyntaxError) as exc:
        raise UpstreamHostAuthError(
            "pinned Omnigent runner auth entrypoint is unavailable",
            code="host_auth_protocol_drift",
        ) from exc


__all__ = [
    "OmnigentHostAuthAdapter",
    "HostCredentialGeneration",
    "PINNED_OMNIGENT_COMMIT",
    "PINNED_PROTOCOL_PROFILE",
    "UpstreamHostAuthError",
    "UpstreamHostIdentity",
    "assert_pinned_omnigent_auth_contract",
]
