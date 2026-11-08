"""Reusable operator authorization records, separate from candidate context.

A record describes the operator's authority to assess a target. It does not
prove that authority, authorize publishing changes, or turn discovered assets
into targets. Only the existing operator admission boundary can select it for
execution. Generations prevent old admissions from reviving after revocation.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from moonmind.security.workload_profiles import (
    _REF,
    AdmittedAssessmentScope,
    AssessmentEngagement,
    AssessmentEngagementTerms,
    _Model,
)

SETTINGS_KEY = "security.assessment_authorizations"


class AssessmentAuthorization(AssessmentEngagementTerms):
    ref: str = Field(pattern=_REF)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    generation: str | None = Field(None, max_length=64)
    scope: AdmittedAssessmentScope

    @model_validator(mode="after")
    def _unbound_scope(self) -> "AssessmentAuthorization":
        if self.scope.engagement is not None:
            raise ValueError(
                "authorization records cannot contain an admitted engagement"
            )
        if self.scope.max_cpu_millis is None or self.scope.max_memory_mib is None:
            raise ValueError(
                "authorization records require explicit CPU and memory limits"
            )
        if {"web-assessment", "tls-assessment"}.intersection(
            self.scope.profile_refs
        ) and self.scope.max_requests_per_second is None:
            raise ValueError(
                "web and TLS authorizations require an explicit request rate"
            )
        return self


class AssessmentAuthorizations(_Model):
    authorizations: tuple[AssessmentAuthorization, ...] = Field((), max_length=64)

    @model_validator(mode="after")
    def _unique(self) -> "AssessmentAuthorizations":
        refs = [item.ref for item in self.authorizations]
        if len(refs) != len(set(refs)):
            raise ValueError("authorization references must be unique")
        return self


def authorization_scope_input_schema() -> dict[str, Any]:
    """Project native fields for authoring, never saved or admitted authority.

    Single nullable arms and local references are flattened for the shared form
    renderer. Optional object defaults are not introduced, so opening the editor
    cannot add a target or credential grant. Native validation remains final.
    """
    native = AdmittedAssessmentScope.model_json_schema(by_alias=True)
    definitions = native.get("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if "$ref" in value:
            definition = value.pop("$ref").removeprefix("#/$defs/")
            value = {**definitions[definition], **value}
        choices = value.get("anyOf")
        if isinstance(choices, list):
            concrete = [choice for choice in choices if choice.get("type") != "null"]
            if len(concrete) == 1 and len(concrete) != len(choices):
                value.pop("anyOf")
                value = {**concrete[0], **value}
                if value.get("default") is None:
                    value.pop("default", None)
                return inline(value)
        result = {key: inline(item) for key, item in value.items() if key != "$defs"}
        if "const" in result:
            result["enum"] = [result["const"]]
        for name, field in result.get("properties", {}).items():
            words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
            field["title"] = words[0].upper() + words[1:]
        return result

    # Engagement identity/generation is stamped only by trusted launch admission.
    native["properties"].pop("engagement")
    schema = inline(native)
    schema["required"] = [*schema["required"], "maxCpuMillis", "maxMemoryMiB"]
    schema["properties"]["assessmentRef"]["description"] = (
        "Use this same assessment reference in the selected cloud or web preset."
    )
    schema["properties"]["profileRefs"]["description"] = (
        "Installed workload profile references, for example web-assessment or cloud-posture."
    )
    schema["properties"]["actionClasses"]["description"] = (
        "Explicit allowed actions, for example web-passive, web-active or cloud-posture."
    )
    expiry = schema["properties"]["grants"]["items"]["properties"]["expiresAt"]
    expiry["x-moonmind-widget"] = "text"
    expiry["description"] = "ISO time with timezone, for example 2026-12-01T12:00:00Z."
    return schema


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def scope_digest(scope: AdmittedAssessmentScope) -> str:
    return hashlib.sha256(
        _json(scope.model_dump(mode="json", by_alias=True, exclude_none=True))
    ).hexdigest()


def authorization_scope(record: AssessmentAuthorization) -> AdmittedAssessmentScope:
    if not record.generation:
        raise ValueError("authorization must be saved before admission")
    engagement = AssessmentEngagement(
        authorizationRef=record.ref,
        authorizationGeneration=record.generation,
        authorityKind=record.authority_kind,
        authoritySource=record.authority_source,
        validFrom=record.valid_from,
        validUntil=record.valid_until,
        maxTotalJobSeconds=record.max_total_job_seconds,
    )
    return record.scope.model_copy(update={"engagement": engagement})


def _authority(record: AssessmentAuthorization) -> dict[str, Any]:
    return record.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude={"name", "generation"}
    )


def prepare_authorizations_update(value: Any, previous: Any) -> dict[str, Any]:
    """Assign generations under the Settings row lock, never trust client IDs.

    Changing unrelated records or display names preserves accepted work. Any
    authority change, disable/re-enable, or removal/recreation has a new identity.
    Budget accounting remains keyed to assessmentRef across generations.
    """
    incoming = AssessmentAuthorizations.model_validate(value)
    current = AssessmentAuthorizations.model_validate(previous or {})
    old = {item.ref: item for item in current.authorizations}
    records = []
    for item in incoming.authorizations:
        retained = old.get(item.ref)
        generation = (
            retained.generation
            if retained
            and retained.generation
            and json.dumps(_authority(retained), sort_keys=True, allow_nan=False)
            == json.dumps(_authority(item), sort_keys=True, allow_nan=False)
            else str(uuid4())
        )
        records.append(item.model_copy(update={"generation": generation}))
    return AssessmentAuthorizations(authorizations=tuple(records)).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
