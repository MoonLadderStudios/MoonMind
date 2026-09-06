"""Run-record schema for the issue #4024 release gate.

Records what each run actually executes -- source/build and image digests,
architecture, schema/policy versions, database engine/version, artifact
backend, workflow/worker topology, provider adapter, scenario, and
substitutions -- and separates helper/in-memory results from real local
processes, real PostgreSQL/object-store tests, actual workflow
dispatch/replay, exact packaged-runtime tests, and protected live
verification:

- a PostgreSQL-named run on SQLite is rejected, not recorded as PostgreSQL;
- patch-marker mocks are not recorded-history replay;
- a browser fixture is not live enrollment.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


class ExecutionClass:
    """What infrastructure actually executed the run."""

    HELPER_IN_MEMORY = "helper_in_memory"
    REAL_LOCAL_PROCESS = "real_local_process"
    REAL_POSTGRES_OBJECT_STORE = "real_postgres_object_store"
    WORKFLOW_DISPATCH_REPLAY = "workflow_dispatch_replay"
    EXACT_PACKAGED_RUNTIME = "exact_packaged_runtime"
    LIVE_PROTECTED = "live_protected"

    ALL = (
        HELPER_IN_MEMORY,
        REAL_LOCAL_PROCESS,
        REAL_POSTGRES_OBJECT_STORE,
        WORKFLOW_DISPATCH_REPLAY,
        EXACT_PACKAGED_RUNTIME,
        LIVE_PROTECTED,
    )


@dataclass(frozen=True)
class RunRecord:
    """Immutable record of what one gate run actually executed."""

    scenario: str
    execution_class: str
    source_revision: str = ""
    build_digest: str = ""
    image_digest: str = ""
    architecture: str = ""
    schema_version: str = ""
    policy_version: str = ""
    db_engine: str = ""
    db_version: str = ""
    artifact_backend: str = ""
    workflow_topology: str = ""
    worker_topology: str = ""
    provider_adapter: str = ""
    substitutions: tuple[str, ...] = field(default_factory=tuple)

    def validate(self) -> None:
        """Fail closed when the record mislabels what ran."""
        if self.execution_class not in ExecutionClass.ALL:
            raise ValueError(f"unknown execution_class: {self.execution_class!r}")
        if not self.scenario:
            raise ValueError("scenario is required")
        db = self.db_engine.strip().lower()
        if db in {"postgresql", "postgres"} and self.execution_class != (
            ExecutionClass.REAL_POSTGRES_OBJECT_STORE
        ):
            raise ValueError(
                "a PostgreSQL-named run on another execution class does not "
                "prove PostgreSQL races; use real_postgres_object_store"
            )
        if self.execution_class == ExecutionClass.HELPER_IN_MEMORY and (
            self.db_engine or self.image_digest or self.workflow_topology
        ):
            raise ValueError(
                "helper/in-memory results must not carry real-run identity "
                "(db_engine, image_digest, workflow_topology)"
            )
        if self.execution_class == ExecutionClass.LIVE_PROTECTED and not self.provider_adapter:
            raise ValueError(
                "live verification must name its provider adapter; "
                "a browser fixture is not live enrollment"
            )

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible artifact payload."""
        record = asdict(self)
        record["substitutions"] = list(self.substitutions)
        return record


@dataclass(frozen=True)
class GateAggregate:
    """One selected check's outcome inside the aggregate gate report."""

    check: str
    selected: bool
    passed: bool
    skipped: bool
    artifacts_complete: bool


def aggregate_gate_result(checks: tuple[GateAggregate, ...]) -> bool:
    """Aggregate selected gate outcomes without masking failure.

    Selected failures, unexpected skips, missing artifacts, and incomplete
    scenarios never become success in the aggregate report. Unselected
    checks are ignored; an empty selection fails closed so an unknown
    change cannot silently select nothing and pass.
    """
    selected = [check for check in checks if check.selected]
    if not selected:
        return False
    return all(
        check.passed and not check.skipped and check.artifacts_complete
        for check in selected
    )
