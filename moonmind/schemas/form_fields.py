"""Project nested Pydantic fields into the shared authoring form."""

import re


def form_fields_schema(native: dict, *, include_defaults: bool = True) -> dict:
    """Inline local model definitions and concrete nullable arms for the form."""
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
        if not include_defaults:
            value.pop("default", None)
        result = {key: inline(item) for key, item in value.items() if key != "$defs"}
        if "const" in result:
            result["enum"] = [result["const"]]
        for name, field in result.get("properties", {}).items():
            words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
            field["title"] = words[0].upper() + words[1:]
        return result

    return inline(native)
