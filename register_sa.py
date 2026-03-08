import asyncio
from temporalio.client import Client
from temporalio.api.enums.v1 import IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest

async def main():
    client = await Client.connect("temporal:7233", namespace="moonmind")
    await client.operator_service.add_search_attributes(
        AddSearchAttributesRequest(
            namespace="moonmind",
            search_attributes={
                "mm_entry": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_id": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_owner_type": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_state": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_updated_at": IndexedValueType.INDEXED_VALUE_TYPE_DATETIME,
                "mm_repo": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_integration": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
                "mm_continue_as_new_cause": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD,
            }
        )
    )
    print("Search attributes registered.")

asyncio.run(main())
