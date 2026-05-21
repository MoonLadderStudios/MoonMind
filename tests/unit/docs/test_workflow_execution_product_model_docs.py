from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_MODEL = REPO_ROOT / "docs" / "Temporal" / "WorkflowExecutionProductModel.md"
TYPE_CATALOG = REPO_ROOT / "docs" / "Temporal" / "WorkflowTypeCatalogAndLifecycle.md"
COMPAT_MODEL = REPO_ROOT / "docs" / "Temporal" / "TaskExecutionCompatibilityModel.md"
AGENT_MODEL = REPO_ROOT / "docs" / "Temporal" / "ManagedAndExternalAgentExecutionModel.md"
ARCHITECTURE = REPO_ROOT / "docs" / "MoonMindArchitecture.md"
TEMPORAL_ARCHITECTURE = REPO_ROOT / "docs" / "Temporal" / "TemporalArchitecture.md"
SPEC = REPO_ROOT / "specs" / "001-workflow-execution-hard-switch" / "spec.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_workflow_execution_product_model_defines_required_terms() -> None:
    text = _read(PRODUCT_MODEL)

    assert "MoonMind does not define a separate product entity named Task." in text
    assert "Workflow Execution" in text
    assert "`workflowId`" in text
    assert "`runId`" in text
    assert "`workflowType`" in text
    assert "`entry`" in text
    assert "Step Execution" in text
    assert "artifacts" in text
    assert "`externalRefs`" in text
    assert "docs/Temporal/WorkflowLanguageHardSwitchPlan.md" in _read(SPEC)


def test_task_compatibility_doc_is_not_normative_product_framing() -> None:
    text = _read(COMPAT_MODEL)

    assert "Status: **Superseded by Workflow Execution product model**" in text
    assert "task-oriented compatibility surfaces" not in text
    assert "task compatibility" not in text.lower()
    assert "MoonMind does not define a separate product entity named Task." in text


def test_workflow_type_catalog_uses_workflow_native_user_workflow_language() -> None:
    text = _read(TYPE_CATALOG)

    assert "`MoonMind.UserWorkflow`" in text
    assert "user-submitted, Step-ledger-owning Workflow Execution" in text
    assert "standard task execution" not in text
    assert "public APIs and UI flows may still use `task` terminology" not in text


def test_root_execution_docs_do_not_define_moonmind_task_product_entity() -> None:
    combined = "\n".join(
        [
            _read(PRODUCT_MODEL),
            _read(TYPE_CATALOG),
            _read(COMPAT_MODEL),
            _read(AGENT_MODEL),
            _read(ARCHITECTURE),
            _read(TEMPORAL_ARCHITECTURE),
        ]
    )

    disallowed = [
        "represents a **task**",
        "root task workflow",
        "task-level orchestration",
        "task-level envelope",
        "standard task execution workflow",
        "Product `task` vocabulary maps primarily to `MoonMind.Run`",
    ]

    for phrase in disallowed:
        assert phrase not in combined


def test_allowed_task_references_are_qualified_in_product_model() -> None:
    text = _read(PRODUCT_MODEL)

    allowed_terms = [
        "Temporal Task",
        "Temporal Workflow Task",
        "Temporal Activity Task",
        "Temporal Task Queue",
        "Jira task",
        "Codex provider task",
    ]
    for term in allowed_terms:
        assert term in text
