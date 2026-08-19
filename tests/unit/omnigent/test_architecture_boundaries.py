"""Architecture-boundary enforcement for the Omnigent package.

Source: MoonLadderStudios/MoonMind#3711 ([Omnigent control plane 10/11]).

Runs the deterministic ``tools/check_omnigent_architecture`` guard against the
real tree (which must stay clean) and unit-tests each rule against synthetic
fixture trees so a regression in the guard itself is caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKER_PATH = REPO_ROOT / "tools" / "check_omnigent_architecture.py"

_spec = importlib.util.spec_from_file_location(
    "check_omnigent_architecture", _CHECKER_PATH
)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass annotation resolution can find the module.
sys.modules[_spec.name] = checker
_spec.loader.exec_module(checker)


def test_real_omnigent_tree_has_no_boundary_violations() -> None:
    violations = checker.check_omnigent_architecture()
    assert violations == [], "\n".join(v.render() for v in violations)


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _clean_tree(root: Path) -> None:
    _write(root, "domain/__init__.py", "")
    _write(
        root,
        "domain/failures.py",
        "from enum import Enum\n\n\nclass OmnigentFailureReason(str, Enum):\n"
        "    AUTH_FAILURE = 'auth_failure'\n",
    )
    _write(root, "ports/__init__.py", "from .sessions import SessionRepositoryPort\n")
    _write(
        root,
        "ports/sessions.py",
        "from typing import Protocol\n\n"
        "from moonmind.omnigent.domain.failures import OmnigentFailureReason\n\n\n"
        "class SessionRepositoryPort(Protocol):\n    ...\n",
    )
    _write(
        root,
        "adapters/__init__.py",
        "from moonmind.omnigent.ports import SessionRepositoryPort\n",
    )
    # SQLAlchemy is allowed, but only inside the persistence adapter subtree.
    _write(
        root,
        "adapters/persistence/__init__.py",
        "from moonmind.omnigent.ports import SessionRepositoryPort\n"
        "import sqlalchemy\n",
    )


def test_clean_fixture_tree_passes(tmp_path) -> None:
    _clean_tree(tmp_path)
    assert checker.check_omnigent_architecture(tmp_path) == []


def test_pure_layer_forbidden_import_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(root=tmp_path, rel="domain/store.py", source="import sqlalchemy\n")
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-forbidden-import" in rules


def test_pure_layer_fastapi_import_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(root=tmp_path, rel="ports/web.py", source="from fastapi import APIRouter\n")
    violations = checker.check_omnigent_architecture(tmp_path)
    assert any(v.rule == "pure-layer-forbidden-import" for v in violations)


def test_pure_layer_environment_read_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="domain/config.py",
        source="import os\n\nVALUE = os.environ['X']\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-env-read" in rules


def test_dependency_direction_back_edge_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    # domain (rank 0) importing ports (rank 1) is a forbidden back-edge.
    _write(
        root=tmp_path,
        rel="domain/leaky.py",
        source="from moonmind.omnigent.ports import SessionRepositoryPort\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "dependency-direction" in rules


def test_duplicate_vocabulary_is_flagged(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/redefine.py",
        source="from enum import Enum\n\n\nclass OmnigentFailureReason(str, Enum):\n"
        "    OTHER = 'other'\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "duplicate-vocabulary" in rules


def test_duplicate_control_plane_outcome_is_flagged(tmp_path) -> None:
    # The single-canonical-vocabulary rule covers more than the failure enum:
    # ControlPlaneOutcome and FencingScope each have one authoritative home too.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/outcomes.py",
        source="from enum import Enum\n\n\nclass ControlPlaneOutcome(str, Enum):\n"
        "    APPLIED = 'applied'\n",
    )
    _write(
        root=tmp_path,
        rel="domain/outcomes.py",
        source="from enum import Enum\n\n\nclass ControlPlaneOutcome(str, Enum):\n"
        "    APPLIED = 'applied'\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "duplicate-vocabulary" in rules


def test_duplicate_status_vocabulary_is_flagged(tmp_path) -> None:
    # The single-canonical-vocabulary rule also covers the status/capability
    # vocabulary (issue AC7: "duplicated vocabulary" beyond the three conflict
    # enums). Provider-status normalization, session lifecycle, terminal outcome,
    # lease/submission/desired-lifecycle state, and the decision/reason tables each
    # have exactly one home; redefining one anywhere else is a duplicate.
    status_vocabulary = {
        "ProviderStatusClass": "TERMINAL_SUCCESS = 'terminal_success'",
        "SessionLifecyclePhase": "CLOSED = 'closed'",
        "TerminalOutcome": "SUCCESS = 'success'",
        "LeaseState": "HELD = 'held'",
        "SubmissionState": "ACCEPTED = 'accepted'",
        "DesiredLifecycle": "RUN = 'run'",
        "DecisionKind": "NO_OP = 'no_op'",
        "ReasonCode": "SESSION_FAILED = 'session_failed'",
    }
    for enum_name, member in status_vocabulary.items():
        _clean_tree(tmp_path)
        _write(
            root=tmp_path,
            rel="adapters/redefine_status.py",
            source=(
                f"from enum import Enum\n\n\nclass {enum_name}(str, Enum):\n"
                f"    {member}\n"
            ),
        )
        _write(
            root=tmp_path,
            rel="domain/status_vocab.py",
            source=(
                f"from enum import Enum\n\n\nclass {enum_name}(str, Enum):\n"
                f"    {member}\n"
            ),
        )
        rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
        assert "duplicate-vocabulary" in rules, enum_name


def test_status_vocabulary_defined_once_is_allowed(tmp_path) -> None:
    # A single authoritative definition of a status enum must NOT be flagged --
    # only redefinition is a duplicate.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="domain/status_vocab.py",
        source=(
            "from enum import Enum\n\n\nclass ProviderStatusClass(str, Enum):\n"
            "    TERMINAL_SUCCESS = 'terminal_success'\n"
        ),
    )
    violations = checker.check_omnigent_architecture(tmp_path)
    assert not any(
        v.rule == "duplicate-vocabulary" for v in violations
    ), "\n".join(v.render() for v in violations)


def test_application_layer_forbidden_import_is_flagged(tmp_path) -> None:
    # The application layer is infra-free: it coordinates use cases over ports
    # and domain types, never concrete SQLAlchemy/FastAPI/Docker.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="application/reconcile_session.py",
        source="import sqlalchemy\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-forbidden-import" in rules


def test_adapters_web_framework_import_is_flagged(tmp_path) -> None:
    # No decomposed layer below the facade may import a web framework.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/provider_http/client.py",
        source="from fastapi import APIRouter\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "adapters-web-framework" in rules


def test_adapters_sqlalchemy_outside_persistence_is_flagged(tmp_path) -> None:
    # SQLAlchemy in a non-persistence adapter subtree reaches past the port.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/provider_http/client.py",
        source="import sqlalchemy\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "adapters-sqlalchemy-containment" in rules


def test_adapters_sqlalchemy_inside_persistence_is_allowed(tmp_path) -> None:
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/persistence/postgres.py",
        source="import sqlalchemy\n",
    )
    violations = checker.check_omnigent_architecture(tmp_path)
    assert not any(
        v.rule == "adapters-sqlalchemy-containment" for v in violations
    ), "\n".join(v.render() for v in violations)


def test_pure_layer_provider_native_string_is_flagged(tmp_path) -> None:
    # A provider-native status string in the pure domain layer is vendor
    # vocabulary that must be translated to canonical vocabulary in an adapter.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="domain/status.py",
        source="TERMINAL = 'codex_completed'\n",
    )
    rules = {v.rule for v in checker.check_omnigent_architecture(tmp_path)}
    assert "pure-layer-provider-vocabulary" in rules


def test_ports_layer_provider_native_import_is_flagged(tmp_path) -> None:
    # Ports are infra-free too: importing a provider-native module leaks vendor
    # vocabulary into the port surface.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="ports/provider.py",
        source="from moonmind.providers.claude import ClaudeClient\n",
    )
    violations = checker.check_omnigent_architecture(tmp_path)
    assert any(
        v.rule == "pure-layer-provider-vocabulary" for v in violations
    ), "\n".join(v.render() for v in violations)


def test_provider_native_vocabulary_allowed_in_adapters(tmp_path) -> None:
    # Adapters are exactly where provider-native vocabulary is translated into
    # canonical domain observations/outcomes, so it must NOT be flagged there.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="adapters/provider_http/claude_client.py",
        source="PROVIDER_TERMINAL = 'claude_completed'\n",
    )
    violations = checker.check_omnigent_architecture(tmp_path)
    assert not any(
        v.rule == "pure-layer-provider-vocabulary" for v in violations
    ), "\n".join(v.render() for v in violations)


def test_pure_layer_docstring_naming_provider_is_not_flagged(tmp_path) -> None:
    # Docstrings may name the providers a pure layer abstracts; only canonical
    # (non-docstring) string constants and imports are constrained.
    _clean_tree(tmp_path)
    _write(
        root=tmp_path,
        rel="domain/observations.py",
        source=(
            '"""Canonical observations translated from Codex, Claude, and '
            'Gemini providers."""\n\n'
            "from enum import Enum\n\n\n"
            "class ObservationKind(str, Enum):\n"
            "    TURN_COMPLETED = 'turn_completed'\n"
        ),
    )
    violations = checker.check_omnigent_architecture(tmp_path)
    assert not any(
        v.rule == "pure-layer-provider-vocabulary" for v in violations
    ), "\n".join(v.render() for v in violations)
