"""Manifest v0 validator.

Performs structural and semantic validation of v0 manifest YAML files:

1. Schema validation via Pydantic (``ManifestV0.model_validate``).
2. Cross-field semantic checks (dimension ↔ model, auth presence).
3. Secret leak detection (raw secret material in manifest values).
4. Security policy enforcement (PII redaction, metadata allowlist).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import ValidationError

from moonmind.schemas.manifest_v0_models import ManifestV0, is_retired_vector_key

# ---------------------------------------------------------------------------
# Secret detection patterns (from DOC-REQ-007)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{36,}"),  # GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),  # GitHub fine-grained PAT
    re.compile(r"AIza[A-Za-z0-9_\\-]{35}"),  # Google API key
    re.compile(r"ATATT[A-Za-z0-9_\\-]{10,}"),  # Atlassian token
    re.compile(r"AKIA[A-Z0-9]{16}"),  # AWS access key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI key
    re.compile(r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----"),
]

# MoonLadderStudios/MoonMind#4108: managed-vector pipeline retired. New
# manifests must omit the retired blocks; historical manifests remain
# readable via ManifestV0 (extra="allow") but are rejected here before
# fetch/dispatch. Detection is the one normalized check shared with
# pipeline/queue (is_retired_vector_key): 'vectorstore', 'VectorStore'
# and padded variants reject like the canonical spellings.

@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # ERROR, WARNING
    field: str
    message: str

@dataclass
class ValidationResult:
    """Aggregated validation result."""

    valid: bool
    manifest: Optional[ManifestV0] = None
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    def summary(self) -> str:
        """Human-readable summary for CLI output."""
        if self.valid:
            w = len(self.warnings)
            suffix = f" ({w} warning{'s' if w != 1 else ''})" if w else ""
            return f"✓ Manifest is valid{suffix}"
        errors = len(self.errors)
        return f"✗ Manifest has {errors} error{'s' if errors != 1 else ''}"

def validate_manifest_file(path: str | Path) -> ValidationResult:
    """Validate a manifest YAML file at *path*.

    Returns a :class:`ValidationResult` with parsed manifest (if valid) and
    all issues found during validation.
    """
    p = Path(path)
    if not p.exists():
        return ValidationResult(
            valid=False,
            issues=[
                ValidationIssue("ERROR", "file", f"Manifest file not found: {p}")
            ],
        )

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        return ValidationResult(
            valid=False,
            issues=[
                ValidationIssue("ERROR", "file", f"Cannot read manifest: {exc}")
            ],
        )

    return validate_manifest_string(raw)

def validate_manifest_string(content: str) -> ValidationResult:
    """Validate a manifest YAML string.

    Returns a :class:`ValidationResult` with parsed manifest (if valid) and
    all issues found during validation.
    """
    issues: List[ValidationIssue] = []

    # 1. YAML parse
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return ValidationResult(
            valid=False,
            issues=[
                ValidationIssue("ERROR", "yaml", f"YAML parse error: {exc}")
            ],
        )

    if not isinstance(parsed, dict):
        return ValidationResult(
            valid=False,
            issues=[
                ValidationIssue(
                    "ERROR", "yaml", "Manifest must be a YAML mapping (object)"
                )
            ],
        )

    # 2. Secret leak scan (before Pydantic — catch leaks even in malformed manifests)
    _scan_secrets(parsed, "", issues)

    # 2b. Retired vector-ingest contract (#4108): reject before any
    # fetch/dispatch/side effect. Historical reads use ManifestV0 directly.
    _check_retired_vector_fields(parsed, issues)
    if any(i.severity == "ERROR" for i in issues):
        return ValidationResult(valid=False, issues=issues)

    # 3. Schema validation via Pydantic
    try:
        manifest = ManifestV0.model_validate(parsed)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(l) for l in err["loc"])
            issues.append(
                ValidationIssue("ERROR", loc or "schema", err["msg"])
            )
        return ValidationResult(valid=False, issues=issues)

    # 4. Semantic checks (beyond Pydantic model_validator)
    _check_auth_presence(manifest, issues)
    _check_security_policy(manifest, issues)
    _check_data_source_ids_unique(manifest, issues)

    has_errors = any(i.severity == "ERROR" for i in issues)
    return ValidationResult(
        valid=not has_errors,
        manifest=manifest,
        issues=issues,
    )

# ---------------------------------------------------------------------------
# Internal checks
# ---------------------------------------------------------------------------

def _scan_secrets(
    obj: object, path: str, issues: List[ValidationIssue]
) -> None:
    """Recursively scan manifest values for raw secret material."""
    if isinstance(obj, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(obj):
                issues.append(
                    ValidationIssue(
                        "ERROR",
                        path or "value",
                        "Possible raw secret detected. Use ${ENV} references "
                        "instead of embedding credentials.",
                    )
                )
                return  # one finding per value
    elif isinstance(obj, dict):
        for key, val in obj.items():
            _scan_secrets(val, f"{path}.{key}" if path else key, issues)
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            _scan_secrets(val, f"{path}[{idx}]", issues)

def _check_auth_presence(
    manifest: ManifestV0, issues: List[ValidationIssue]
) -> None:
    """Warn when a data source type likely requires auth but none is provided."""
    _TYPES_NEEDING_AUTH = {
        "GithubRepositoryReader",
        "GoogleDriveReader",
        "ConfluenceReader",
    }
    for ds in manifest.dataSources:
        if ds.type in _TYPES_NEEDING_AUTH and ds.auth is None:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    f"dataSources.{ds.id}.auth",
                    f"DataSource type '{ds.type}' typically requires auth. "
                    f"Ensure credentials are provided via ${{ENV}} references.",
                )
            )

def _check_security_policy(
    manifest: ManifestV0, issues: List[ValidationIssue]
) -> None:
    """Validate security settings when present."""
    if manifest.security is None:
        return

    # T026: PII redaction enforcement
    if manifest.security.piiRedaction:
        # When PII redaction is requested, we need transforms with a splitter
        # configured so that content passes through chunking where redaction
        # can be applied. Without transforms, raw document text flows straight
        # to downstream handling without redaction. Configure
        # transforms.splitter to enable chunking-time PII filtering.
        if manifest.transforms is None or manifest.transforms.splitter is None:
            issues.append(
                ValidationIssue(
                    "WARNING",
                    "security.piiRedaction",
                    "PII redaction is enabled but no transforms.splitter is configured. "
                    "Without a splitter, raw document text may flow downstream without "
                    "redaction. Configure transforms.splitter to enable chunking-time "
                    "PII filtering.",
                )
            )

    # T027: Metadata allowlist enforcement
    if manifest.security.allowlistMetadata:
        allowed = set(manifest.security.allowlistMetadata)
        for ds in manifest.dataSources:
            # Check if data source params include metadata keys not in allowlist
            extra_meta = ds.params.get("extraMetadata", {})
            if isinstance(extra_meta, dict):
                for key in extra_meta:
                    if key not in allowed:
                        issues.append(
                            ValidationIssue(
                                "ERROR",
                                f"dataSources.{ds.id}.params.extraMetadata.{key}",
                                f"Metadata key '{key}' is not in "
                                f"security.allowlistMetadata {sorted(allowed)}. "
                                f"Add it to the allowlist or remove it from the "
                                f"data source params.",
                            )
                        )

def _check_retired_vector_fields(
    parsed: dict, issues: List[ValidationIssue]
) -> None:
    """Reject retired managed-vector keys before Pydantic/fetch/dispatch.

    MoonLadderStudios/MoonMind#4108 retired ``embeddings``, ``vectorStore``,
    ``indices`` and ``retrievers``. New manifests carrying any of them fail
    actionably here; historical artifacts remain readable via
    ``ManifestV0`` (``extra="allow"``) without importing/connecting to Qdrant.
    """
    for field in sorted(parsed):
        if not is_retired_vector_key(field):
            continue
        if parsed[field] in (None, [], {}):
            continue
        issues.append(
            ValidationIssue(
                "ERROR",
                field,
                f"'{field}' was retired in MoonLadderStudios/MoonMind#4108: "
                "MoonMind ships no managed vector indexing, embedding, "
                "collection or retrieval pipeline. Remove this block; "
                "new vector-ingest manifests are not ingested.",
            )
        )

def _check_data_source_ids_unique(
    manifest: ManifestV0, issues: List[ValidationIssue]
) -> None:
    """Ensure all dataSource IDs are unique."""
    seen: dict[str, int] = {}
    for ds in manifest.dataSources:
        seen[ds.id] = seen.get(ds.id, 0) + 1
    for ds_id, count in seen.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    "ERROR",
                    f"dataSources.{ds_id}",
                    f"Duplicate dataSource id '{ds_id}' (appears {count} times)",
                )
            )
