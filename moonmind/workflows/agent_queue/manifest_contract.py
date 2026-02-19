"""Validation and normalization helpers for manifest queue jobs."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, MutableMapping, TypedDict
from urllib.parse import urlsplit

import yaml

from moonmind.config.settings import settings

_BASE_ALLOWED_SOURCE_KINDS = frozenset({"inline", "registry"})
_PROFILE_PROVIDER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_PROFILE_FIELD_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_PROFILE_ENV_TOKEN_RE = re.compile(r"[^A-Za-z0-9]+")
_VAULT_MOUNT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_VAULT_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_VAULT_FIELD_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_ACTIONS = frozenset({"plan", "run"})
ALLOWED_OPTION_KEYS = frozenset({"dryRun", "forceFull", "maxDocs"})
EMBEDDING_PROVIDER_CAPABILITIES = {
    "openai": "openai",
    "google": "google",
    "ollama": "ollama",
}
VECTOR_STORE_CAPABILITIES = {
    "qdrant": "qdrant",
    "pgvector": "pgvector",
    "milvus": "milvus",
}
DATA_SOURCE_CAPABILITIES = {
    "githubrepositoryreader": "github",
    "googledrivereader": "gdrive",
    "confluencereader": "confluence",
    "simpledirectoryreader": "local_fs",
}
SAFE_REFERENCE_PREFIXES = ("${", "profile://", "vault://")
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_key",
        "client_secret",
        "secret",
        "secret_key",
        "private_key",
        "password",
        "token",
        "auth_token",
        "bearer_token",
    }
)
SUSPECT_VALUE_PREFIXES_LOWER = (
    "sk-",
    "sk_live_",
    "sk_test_",
    "rk_live_",
    "rk_test_",
    "pk_live_",
    "pk_test_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "xoxp-",
    "xoxb-",
    "xapp-",
    "ya29.",
)
SUSPECT_VALUE_PREFIXES_UPPER = ("AKIA", "ASIA", "EAAC", "AIza".upper())
SUSPECT_VALUE_SUBSTRINGS = (
    "token=",
    "secret=",
    "password=",
    "api_key=",
    "apikey=",
    "client_secret=",
    "access_key=",
    "bearer ",
)
_JWT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+=*$")
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")


def _configured_manifest_capabilities() -> tuple[str, ...]:
    """Return baseline manifest capability labels from settings."""

    configured = getattr(
        settings.spec_workflow,
        "manifest_required_capabilities",
        ("manifest",),
    )
    normalized: list[str] = []
    for token in configured:
        text = str(token).strip().lower()
        if text:
            normalized.append(text)
    if not normalized:
        return ("manifest",)
    # Preserve order while removing duplicates.
    return tuple(dict.fromkeys(normalized))


class ManifestContractError(ValueError):
    """Raised when manifest queue payloads violate the contract."""


class ManifestProfileSecretRef(TypedDict):
    provider: str
    field: str
    envKey: str
    normalized: str


class ManifestVaultSecretRef(TypedDict):
    mount: str
    path: str
    field: str
    ref: str


def normalize_manifest_job_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize manifest payloads for queue persistence."""

    if not isinstance(payload, Mapping):
        raise ManifestContractError("payload must be an object")
    manifest_obj = payload.get("manifest")
    if not isinstance(manifest_obj, Mapping):
        raise ManifestContractError("manifest payload is required")

    manifest_name = _clean_str(manifest_obj.get("name"))
    if not manifest_name:
        raise ManifestContractError("manifest.name must be a non-empty string")

    action_raw = _clean_str(manifest_obj.get("action")) or "run"
    action = action_raw.lower()
    if action not in ALLOWED_ACTIONS:
        supported = ", ".join(sorted(ALLOWED_ACTIONS))
        raise ManifestContractError(f"manifest.action must be one of: {supported}")

    source_node = manifest_obj.get("source")
    source, source_content = _normalize_source(source_node, manifest_name)
    parsed_manifest = _parse_manifest_yaml(source_content)
    detect_manifest_secret_leaks(parsed_manifest)
    manifest_version = _detect_manifest_version(parsed_manifest)
    if manifest_version != "v0":
        raise ManifestContractError(
            "manifest version must be 'v0' per ManifestTaskSystem contract"
        )

    metadata = parsed_manifest.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ManifestContractError("manifest metadata block is required")
    metadata_name = _clean_str(metadata.get("name"))
    if not metadata_name:
        raise ManifestContractError("metadata.name must be defined in manifest YAML")
    if metadata_name != manifest_name:
        raise ManifestContractError(
            "manifest.name must match metadata.name in the manifest YAML"
        )

    manifest_hash = _compute_manifest_hash(source_content)
    required_capabilities = derive_required_capabilities(parsed_manifest)
    options = _normalize_options(manifest_obj.get("options"))
    effective_run_config = _build_effective_run_config(parsed_manifest, options)
    secret_refs = _collect_secret_refs(parsed_manifest)

    source["contentHash"] = manifest_hash
    source["version"] = manifest_version
    if source["kind"] == "registry":
        source.pop("content", None)

    normalized_manifest = {
        "name": manifest_name,
        "action": action,
        "source": source,
        "options": options,
    }

    normalized_payload = {
        "manifest": normalized_manifest,
        "manifestHash": manifest_hash,
        "manifestVersion": manifest_version,
        "requiredCapabilities": required_capabilities,
        "effectiveRunConfig": effective_run_config,
    }
    if secret_refs:
        normalized_payload["manifestSecretRefs"] = secret_refs
    return normalized_payload


def derive_required_capabilities(manifest: Mapping[str, Any]) -> list[str]:
    """Return ordered list of capability labels required for the manifest."""

    if not isinstance(manifest, Mapping):
        raise ManifestContractError("manifest object must be a mapping")
    version = _detect_manifest_version(manifest)
    if version != "v0":
        raise ManifestContractError(
            "only version 'v0' manifests are supported for capability derivation"
        )

    seen: set[str] = set()
    ordered_caps: list[str] = []

    def _add(cap: str) -> None:
        token = cap.strip().lower()
        if token and token not in seen:
            seen.add(token)
            ordered_caps.append(token)

    for base_capability in _configured_manifest_capabilities():
        _add(base_capability)

    embeddings_node = manifest.get("embeddings")
    if not isinstance(embeddings_node, Mapping):
        raise ManifestContractError("embeddings block is required in manifest YAML")
    provider_label = _clean_str(embeddings_node.get("provider")).lower()
    if not provider_label:
        raise ManifestContractError("embeddings.provider must be set")
    provider_capability = EMBEDDING_PROVIDER_CAPABILITIES.get(provider_label)
    if provider_capability is None:
        raise ManifestContractError(
            f"unsupported embeddings provider '{provider_label}' in manifest"
        )
    _add("embeddings")
    _add(provider_capability)

    vector_store_node = manifest.get("vectorStore")
    if not isinstance(vector_store_node, Mapping):
        raise ManifestContractError("vectorStore block is required in manifest YAML")
    vector_store_type = _clean_str(vector_store_node.get("type")).lower()
    capability = VECTOR_STORE_CAPABILITIES.get(vector_store_type)
    if capability is None:
        raise ManifestContractError(
            f"unsupported vectorStore.type '{vector_store_type}' in manifest"
        )
    _add(capability)

    data_sources = manifest.get("dataSources")
    if not isinstance(data_sources, list) or not data_sources:
        raise ManifestContractError("manifest must include at least one data source")
    for entry in data_sources:
        if not isinstance(entry, Mapping):
            raise ManifestContractError("each dataSources entry must be an object")
        ds_type = _clean_str(entry.get("type"))
        if not ds_type:
            raise ManifestContractError("dataSources entries must declare a type")
        capability_token = DATA_SOURCE_CAPABILITIES.get(ds_type.lower())
        if capability_token is None:
            raise ManifestContractError(
                f"unsupported data source type '{ds_type}' in manifest"
            )
        _add(capability_token)

    return ordered_caps


def _normalize_source(
    source_node: Any,
    manifest_name: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(source_node, Mapping):
        raise ManifestContractError("manifest.source must be an object")
    kind = _clean_str(source_node.get("kind")).lower()
    if not kind:
        raise ManifestContractError("manifest.source.kind must be provided")
    allowed_source_kinds = _allowed_source_kinds()
    if kind not in allowed_source_kinds:
        supported = ", ".join(sorted(allowed_source_kinds))
        raise ManifestContractError(f"manifest.source.kind must be one of: {supported}")

    raw_content = source_node.get("content")
    content = None
    if raw_content is not None:
        if not isinstance(raw_content, str):
            raise ManifestContractError("manifest.source.content must be a string")
        if not raw_content.strip():
            raise ManifestContractError("manifest.source.content must not be empty")
        content = raw_content

    if content is None:
        raise ManifestContractError(
            "manifest.source.content is required for normalization"
        )

    source: dict[str, Any] = {"kind": kind, "content": content}
    if kind == "registry":
        registry_name = _clean_str(source_node.get("name")) or manifest_name
        if not registry_name:
            raise ManifestContractError(
                "registry manifest.source.name must be provided or match manifest.name"
            )
        source["name"] = registry_name
        source.pop("content", None)
    elif kind == "path":
        path_value = _clean_str(source_node.get("path"))
        if not path_value:
            raise ManifestContractError(
                "manifest.source.path must be defined for path sources"
            )
        source["path"] = path_value

    return source, content


def _normalize_options(options_node: Any) -> dict[str, Any]:
    if options_node is None:
        return {}
    if not isinstance(options_node, Mapping):
        raise ManifestContractError("manifest.options must be an object when provided")

    normalized: dict[str, Any] = {}
    for key, value in options_node.items():
        if key not in ALLOWED_OPTION_KEYS:
            allowed = ", ".join(sorted(ALLOWED_OPTION_KEYS))
            raise ManifestContractError(f"manifest.options only supports: {allowed}")
        if key in {"dryRun", "forceFull"}:
            normalized[key] = _parse_manifest_bool_option(key, value)
        elif key == "maxDocs":
            if value is None:
                normalized[key] = None
            else:
                try:
                    parsed = int(value)
                except (TypeError, ValueError) as exc:  # pragma: no cover
                    raise ManifestContractError(
                        "manifest.options.maxDocs must be an integer"
                    ) from exc
                if parsed < 1:
                    raise ManifestContractError("manifest.options.maxDocs must be >= 1")
                normalized[key] = parsed
    return normalized


def _parse_manifest_bool_option(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    raise ManifestContractError(f"manifest.options.{key} must be a boolean")


def _build_effective_run_config(
    manifest: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    run_node = manifest.get("run")
    base: dict[str, Any] = dict(run_node) if isinstance(run_node, Mapping) else {}
    for key, value in overrides.items():
        base[key] = value
    return base


def _parse_manifest_yaml(content: str) -> Mapping[str, Any]:
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:  # pragma: no cover
        raise ManifestContractError("manifest YAML is invalid") from exc
    if not isinstance(parsed, MutableMapping):
        raise ManifestContractError("manifest YAML must decode to an object")
    return parsed


def _detect_manifest_version(manifest: Mapping[str, Any]) -> str:
    version = manifest.get("version")
    if isinstance(version, str):
        return version.strip().lower()
    return "legacy"


def _compute_manifest_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def sanitize_manifest_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a redacted manifest payload safe for API serialization.

    Inline manifest YAML content must never be exposed via queue APIs, but the
    UI still needs lightweight metadata (name, action, source kind, hash, and
    derived capabilities) to display manifest jobs. This helper strips raw
    `manifest.source.content` while retaining the audit-friendly fields callers
    expect (hash, version, and capability labels).
    """

    if not isinstance(payload, Mapping):
        return {}

    sanitized: dict[str, Any] = {}
    manifest_obj = payload.get("manifest")
    if isinstance(manifest_obj, Mapping):
        manifest_view: dict[str, Any] = {}

        name = _clean_str(manifest_obj.get("name"))
        if name:
            manifest_view["name"] = name

        action = _clean_str(manifest_obj.get("action"))
        if action:
            manifest_view["action"] = action

        source_obj = manifest_obj.get("source")
        if isinstance(source_obj, Mapping):
            source_view: dict[str, Any] = {}
            kind = _clean_str(source_obj.get("kind"))
            if kind:
                source_view["kind"] = kind
            registry_name = _clean_str(source_obj.get("name"))
            if registry_name:
                source_view["name"] = registry_name
            path_value = _clean_str(source_obj.get("path"))
            if path_value:
                source_view["path"] = path_value
            if source_view:
                manifest_view["source"] = source_view

        options_obj = manifest_obj.get("options")
        if isinstance(options_obj, Mapping) and options_obj:
            manifest_view["options"] = dict(options_obj)

        if manifest_view:
            sanitized["manifest"] = manifest_view

    manifest_hash = _clean_str(payload.get("manifestHash"))
    if manifest_hash:
        sanitized["manifestHash"] = manifest_hash

    manifest_version = _clean_str(payload.get("manifestVersion"))
    if manifest_version:
        sanitized["manifestVersion"] = manifest_version

    required_caps_raw = payload.get("requiredCapabilities")
    if isinstance(required_caps_raw, list):
        caps: list[str] = []
        seen: set[str] = set()
        for item in required_caps_raw:
            label = _clean_str(item).lower()
            if label and label not in seen:
                caps.append(label)
                seen.add(label)
        sanitized["requiredCapabilities"] = caps

    effective_run_config = payload.get("effectiveRunConfig")
    if isinstance(effective_run_config, Mapping):
        sanitized["effectiveRunConfig"] = dict(effective_run_config)

    secret_refs_obj = payload.get("manifestSecretRefs")
    if isinstance(secret_refs_obj, Mapping):
        sanitized_refs: dict[str, Any] = {}

        profile_refs = secret_refs_obj.get("profile")
        if isinstance(profile_refs, list):
            cleaned_profile: list[dict[str, str]] = []
            seen_profile: set[str] = set()
            for entry in profile_refs:
                if not isinstance(entry, Mapping):
                    continue
                env_key = _clean_str(entry.get("envKey"))
                normalized = _clean_str(entry.get("normalized"))
                provider = _clean_str(entry.get("provider"))
                field = _clean_str(entry.get("field"))
                if not env_key or not normalized:
                    continue
                if normalized in seen_profile:
                    continue
                seen_profile.add(normalized)
                cleaned_profile.append(
                    {
                        "envKey": env_key,
                        "normalized": normalized,
                        "provider": provider,
                        "field": field,
                    }
                )
            if cleaned_profile:
                sanitized_refs["profile"] = cleaned_profile

        vault_refs = secret_refs_obj.get("vault")
        if isinstance(vault_refs, list):
            cleaned_vault: list[dict[str, str]] = []
            seen_vault: set[str] = set()
            for entry in vault_refs:
                if not isinstance(entry, Mapping):
                    continue
                ref = _clean_str(entry.get("ref"))
                mount = _clean_str(entry.get("mount"))
                path = _clean_str(entry.get("path"))
                field_name = _clean_str(entry.get("field"))
                if not ref or ref in seen_vault:
                    continue
                seen_vault.add(ref)
                cleaned_vault.append(
                    {
                        "ref": ref,
                        "mount": mount,
                        "path": path,
                        "field": field_name,
                    }
                )
            if cleaned_vault:
                sanitized_refs["vault"] = cleaned_vault

        if sanitized_refs:
            sanitized["manifestSecretRefs"] = sanitized_refs

    return sanitized


def _allowed_source_kinds() -> frozenset[str]:
    kinds = set(_BASE_ALLOWED_SOURCE_KINDS)
    if settings.spec_workflow.allow_manifest_path_source:
        kinds.add("path")
    return frozenset(kinds)


def detect_manifest_secret_leaks(*nodes: Any) -> None:
    """Raise when manifest structures contain raw secret values."""

    for node in nodes:
        _scan_for_secret_values(node)


def _scan_for_secret_values(node: Any, *, key_hint: bool = False) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            child_hint = key_hint
            if isinstance(key, str):
                normalized_key = key.strip().lower()
                if normalized_key in SENSITIVE_FIELD_NAMES:
                    child_hint = True
            _scan_for_secret_values(value, key_hint=child_hint)
    elif isinstance(node, (list, tuple, set)):
        for item in node:
            _scan_for_secret_values(item, key_hint=key_hint)
    elif isinstance(node, str):
        if _value_looks_like_secret(node, key_hint=key_hint):
            raise ManifestContractError(
                "manifest contains raw secret material; replace tokens with env/profile/vault references"
            )


def _value_looks_like_secret(value: str, *, key_hint: bool) -> bool:
    trimmed = value.strip()
    if not trimmed:
        return False
    if _is_safe_reference(trimmed):
        return False
    lowered = trimmed.lower()
    uppered = trimmed.upper()

    if key_hint:
        return True
    if trimmed.startswith("-----BEGIN "):
        return True
    if any(lowered.startswith(prefix) for prefix in SUSPECT_VALUE_PREFIXES_LOWER):
        return True
    if any(uppered.startswith(prefix) for prefix in SUSPECT_VALUE_PREFIXES_UPPER):
        return True
    if any(token in lowered for token in SUSPECT_VALUE_SUBSTRINGS):
        return True
    if _looks_like_jwt(trimmed):
        return True
    if _looks_like_base64_secret(trimmed):
        return True
    return False


def _is_safe_reference(value: str) -> bool:
    return value.startswith(SAFE_REFERENCE_PREFIXES) or (
        value.startswith("${") and value.endswith("}")
    )


def _looks_like_jwt(value: str) -> bool:
    if value.count(".") != 2:
        return False
    header, payload, signature = value.split(".", 2)
    segments = (header, payload, signature)
    return all(
        len(segment) >= 10 and _JWT_SEGMENT_RE.fullmatch(segment)
        for segment in segments
    )


def _looks_like_base64_secret(value: str) -> bool:
    compact = value.replace("\n", "").replace("\r", "")
    if len(compact) < 40:
        return False
    return bool(_BASE64ISH_RE.fullmatch(compact))


def _collect_secret_refs(
    manifest: Mapping[str, Any],
) -> dict[str, list[dict[str, str]]]:
    profile_refs: list[ManifestProfileSecretRef] = []
    vault_refs: list[ManifestVaultSecretRef] = []
    seen_profile: set[str] = set()
    seen_vault: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for child in value.values():
                _walk(child)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _walk(item)
            return
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return
            lowered = stripped.lower()
            if lowered.startswith("profile://"):
                ref = _parse_profile_reference(stripped)
                if ref["normalized"] not in seen_profile:
                    seen_profile.add(ref["normalized"])
                    profile_refs.append(ref)
                return
            if lowered.startswith("vault://"):
                ref = _parse_vault_reference(stripped)
                if ref["ref"] not in seen_vault:
                    seen_vault.add(ref["ref"])
                    vault_refs.append(ref)
                return

    _walk(manifest)

    refs: dict[str, list[dict[str, str]]] = {}
    if profile_refs:
        refs["profile"] = profile_refs
    if vault_refs:
        refs["vault"] = vault_refs
    return refs


def _parse_profile_reference(value: str) -> ManifestProfileSecretRef:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "profile":
        raise ManifestContractError(
            "profile secret references must use profile:// scheme"
        )
    provider = parsed.netloc.strip()
    field = parsed.fragment.strip()
    if not provider or not field:
        raise ManifestContractError(
            "profile secret references must include provider and #field segments"
        )
    if not _PROFILE_PROVIDER_RE.fullmatch(provider):
        raise ManifestContractError(
            "profile secret provider contains invalid characters"
        )
    if not _PROFILE_FIELD_RE.fullmatch(field):
        raise ManifestContractError("profile secret field contains invalid characters")
    env_key = _profile_env_key(provider, field)
    normalized = f"profile://{provider.lower()}#{field.lower()}"
    return ManifestProfileSecretRef(
        provider=provider.lower(),
        field=field.lower(),
        envKey=env_key,
        normalized=normalized,
    )


def _profile_env_key(provider: str, field: str) -> str:
    def _normalize(token: str) -> str:
        cleaned = _PROFILE_ENV_TOKEN_RE.sub("_", token.strip())
        return cleaned.strip("_")

    provider_token = _normalize(provider)
    field_token = _normalize(field)
    if not provider_token or not field_token:
        raise ManifestContractError(
            "profile secret references must include provider and field segments"
        )
    return f"{provider_token.upper()}_{field_token.upper()}"


def _parse_vault_reference(value: str) -> ManifestVaultSecretRef:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "vault":
        raise ManifestContractError("vault secret references must use vault:// scheme")
    mount = parsed.netloc.strip()
    path = parsed.path.lstrip("/").strip()
    field = parsed.fragment.strip()
    if not mount or not path or not field:
        raise ManifestContractError(
            "vault secret references must include mount/path and #field"
        )
    if not _VAULT_MOUNT_RE.fullmatch(mount):
        raise ManifestContractError("vault mount contains invalid characters")
    if not _VAULT_PATH_RE.fullmatch(path):
        raise ManifestContractError("vault path contains invalid characters")
    if any(segment in {"..", "."} for segment in path.split("/")):
        raise ManifestContractError("vault path traversal is not allowed")
    if not _VAULT_FIELD_RE.fullmatch(field):
        raise ManifestContractError("vault field contains invalid characters")
    normalized = f"vault://{mount}/{path}#{field}"
    return ManifestVaultSecretRef(mount=mount, path=path, field=field, ref=normalized)


__all__ = [
    "ManifestContractError",
    "normalize_manifest_job_payload",
    "derive_required_capabilities",
    "sanitize_manifest_payload",
    "detect_manifest_secret_leaks",
]
