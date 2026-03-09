import pathlib

# 1. Update tests/unit/api/test_executions_temporal.py
path = pathlib.Path("tests/unit/api/test_executions_temporal.py")
content = path.read_text()
if "from api_service.api.routers.executions import _get_service, router" in content:
    content = content.replace(
        "from api_service.api.routers.executions import _get_service, router\\n", ""
    )
    content = content.replace(
        "from api_service.api.routers.executions import _get_service, router", ""
    )
    content = content.replace(
        "app.include_router(router)", "app.include_router(executions_module.router)"
    )
    content = content.replace(
        "app.dependency_overrides[_get_service]",
        "app.dependency_overrides[executions_module._get_service]",
    )
    path.write_text(content)
    print("Fixed double import in tests.")

# 2. Update api_service/api/routers/executions.py exception handling
path = pathlib.Path("api_service/api/routers/executions.py")
content = path.read_text()
if "except Exception as exc:" in content:
    # We replace exceptions related to temporal client
    content = content.replace(
        '            except Exception as exc:\\n                logger.warning(\\n                    "Failed to list Temporal executions directly: %s", exc, exc_info=True\\n                )',
        '            except RPCError as exc:\\n                logger.warning(\\n                    "Failed to list Temporal executions directly: %s", exc, exc_info=True\\n                )\\n                raise HTTPException(\\n                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,\\n                    detail={"code": "temporal_unavailable", "message": "Temporal service unavailable."},\\n                ) from exc',
    )
    # The describe_execution one
    if '    except Exception as exc:\\n        if source == "temporal":' in content:
        content = content.replace(
            '    except Exception as exc:\\n        if source == "temporal":',
            '    except RPCError as exc:\\n        if source == "temporal":',
        )
    path.write_text(content)
    print("Fixed broad exceptions in executions.py.")

# 3. Extract shared utility and fix duplicated code
sync_path = pathlib.Path("api_service/core/sync.py")
sync_content = sync_path.read_text()

if "async def sync_temporal_executions_safely" not in sync_content:
    new_funcs = """

async def sync_temporal_executions_safely(
    session: AsyncSession,
    items: list[Any],
    client: Any,
) -> list[Any]:
    import asyncio
    
    async def fetch_and_sync(item):
        try:
            return await fetch_and_sync_execution(session, item.workflow_id, client)
        except Exception as exc:
            logger.warning(
                "Failed to sync execution %s from Temporal: %s",
                item.workflow_id,
                exc,
            )
            return item

    tasks = [fetch_and_sync(item) for item in items]
    updated_items = list(await asyncio.gather(*tasks))
    await session.commit()
    return updated_items

async def sync_single_temporal_execution_safely(
    session: AsyncSession,
    workflow_id: str,
    client: Any,
) -> Any:
    try:
        record = await fetch_and_sync_execution(session, workflow_id, client)
        await session.commit()
        return record
    except Exception as exc:
        logger.warning(
            "Failed to sync execution %s from Temporal: %s",
            workflow_id,
            exc,
            exc_info=True,
        )
        return None
"""
    sync_path.write_text(sync_content + new_funcs)
    print("Added utility functions to sync.py.")

compat_path = pathlib.Path("moonmind/workflows/tasks/compatibility.py")
compat_content = compat_path.read_text()
if "sync_single_temporal_execution_safely" not in compat_content:
    old_block_1 = """                try:
                    global _shared_client_adapter
                    if "_shared_client_adapter" not in globals():
                        _shared_client_adapter = TemporalClientAdapter()
                    client = await _shared_client_adapter.get_client()
                    record = await fetch_and_sync_execution(self._session, record.workflow_id, client)
                    await self._session.commit()
                except Exception as exc:
                    logger.warning(
                        "Failed to sync execution %s from Temporal: %s",
                        record.workflow_id,
                        exc,
                        exc_info=True,
                    )"""
    new_block_1 = """                global _shared_client_adapter
                if "_shared_client_adapter" not in globals():
                    _shared_client_adapter = TemporalClientAdapter()
                client = await _shared_client_adapter.get_client()
                from api_service.core.sync import sync_single_temporal_execution_safely
                synced_record = await sync_single_temporal_execution_safely(self._session, record.workflow_id, client)
                if synced_record:
                    record = synced_record"""
    compat_content = compat_content.replace(old_block_1, new_block_1)

    old_block_2 = """            try:
                global _shared_client_adapter
                if "_shared_client_adapter" not in globals():
                    _shared_client_adapter = TemporalClientAdapter()
                client = await _shared_client_adapter.get_client()

                async def fetch_and_sync(item):
                    try:
                        return await fetch_and_sync_execution(self._session, item.workflow_id, client)
                    except Exception as exc:
                        logger.warning(
                            "Failed to sync execution %s from Temporal: %s",
                            item.workflow_id,
                            exc,
                        )
                        return item

                tasks = [fetch_and_sync(item) for item in records]
                records = await asyncio.gather(*tasks)
                await self._session.commit()
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )"""
    new_block_2 = """            global _shared_client_adapter
            if "_shared_client_adapter" not in globals():
                _shared_client_adapter = TemporalClientAdapter()
            client = await _shared_client_adapter.get_client()
            from api_service.core.sync import sync_temporal_executions_safely
            try:
                records = await sync_temporal_executions_safely(self._session, records, client)
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )"""
    compat_content = compat_content.replace(old_block_2, new_block_2)
    compat_path.write_text(compat_content)
    print("Fixed compatibility.py duplication.")

exec_path = pathlib.Path("api_service/api/routers/executions.py")
exec_content = exec_path.read_text()
if "sync_temporal_executions_safely" not in exec_content:
    old_exec_1 = """            try:
                client = temporal_client

                async def fetch_and_sync(item):
                    try:
                        return await fetch_and_sync_execution(session, item.workflow_id, client)
                    except Exception as exc:
                        logger.warning(
                            "Failed to sync execution %s from Temporal: %s",
                            item.workflow_id,
                            exc,
                        )
                        return item

                tasks = [fetch_and_sync(item) for item in result.items]
                updated_items = await asyncio.gather(*tasks)
                await session.commit()
                result.items = updated_items
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )"""
    new_exec_1 = """            from api_service.core.sync import sync_temporal_executions_safely
            try:
                client = temporal_client
                result.items = await sync_temporal_executions_safely(session, result.items, client)
            except Exception as exc:
                logger.warning(
                    "Failed to sync executions from Temporal: %s", exc, exc_info=True
                )"""

    exec_content = exec_content.replace(old_exec_1, new_exec_1)
    exec_path.write_text(exec_content)
    print("Fixed executions.py duplication.")
