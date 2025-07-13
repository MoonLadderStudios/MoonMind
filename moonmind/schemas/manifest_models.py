from __future__ import annotations

from pathlib import Path
from os import PathLike
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, model_validator


class SecretRef(BaseModel):
    provider: str
    key: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class AuthItem(BaseModel):
    value: Optional[str] = None
    secretRef: Optional[SecretRef] = None

    @model_validator(mode="after")
    def check_xor(cls, values):  # type: ignore[override]
        if (values.value is None) == (values.secretRef is None):
            raise ValueError("Exactly one of value or secretRef must be provided")
        return values


from pydantic import RootModel


class Defaults(RootModel[Dict[str, Any]]):
    root: Dict[str, Any] = Field(default_factory=dict)


class Reader(BaseModel):
    name: Optional[str] = None
    type: str
    enabled: bool = True
    init: Dict[str, Any] = Field(default_factory=dict)
    load_data: List[Dict[str, Any]] = Field(default_factory=list)


class Spec(BaseModel):
    defaults: Optional[Defaults] = None
    auth: Dict[str, AuthItem] = Field(default_factory=dict)
    readers: List[Reader]


class Manifest(BaseModel):
    apiVersion: str
    kind: str
    metadata: Dict[str, Any]
    spec: Spec

    @classmethod
    def model_validate_yaml(cls, data: PathLike | str) -> "Manifest":
        if isinstance(data, (str, Path)) and not isinstance(data, bytes):
            if isinstance(data, (str, Path)) and Path(str(data)).exists():
                content = Path(str(data)).read_text()
            else:
                content = data
        else:
            raise TypeError("model_validate_yaml expects a path or YAML string")
        parsed = yaml.safe_load(content)
        return cls.model_validate(parsed)


def export_schema(path: PathLike) -> None:
    path = Path(path)
    import json

    schema = Manifest.model_json_schema()
    path.write_text(json.dumps(schema, indent=2))
