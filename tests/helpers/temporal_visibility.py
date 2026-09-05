"""Provision test namespaces from the deployment-owned visibility schema."""
from pathlib import Path

from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest


async def register_deployment_search_attributes(env) -> None:
    script = (Path(__file__).resolve().parents[2] / "services/temporal/scripts/bootstrap-namespace.sh").read_text()
    declaration = script.split("REQUIRED_SEARCH_ATTRIBUTES=$(cat <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    types = {
        "Keyword": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
        "KeywordList": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST,
        "Text": IndexedValueType.INDEXED_VALUE_TYPE_TEXT,
        "Int": IndexedValueType.INDEXED_VALUE_TYPE_INT,
        "Bool": IndexedValueType.INDEXED_VALUE_TYPE_BOOL,
        "Datetime": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
        "Double": IndexedValueType.INDEXED_VALUE_TYPE_DOUBLE,
    }
    attributes = {}
    for line in declaration.splitlines():
        name, kind = line.split(":", 1)
        attributes[name] = types[kind]
    await env.client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(namespace=env.client.namespace, search_attributes=attributes)
    )
