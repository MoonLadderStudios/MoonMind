with open("moonmind/workflows/temporal/activity_runtime.py", "r") as f:
    text = f.read()

# 1. First block: fetch_result
block1 = """<<<<<<< HEAD
        result = await adapter.fetch_result(run_id)
        record = self._run_store.load(run_id)
        if record is not None:
            result = self._maybe_enrich_gemini_failure_result(
                result=result,
                record=record,
            )

        # Build merged metadata from the typed result, then enrich with
        # push/PR URL info using model_copy to preserve the typed contract.
        meta = dict(result.metadata or {})
=======
        try:
            result = await adapter.fetch_result(run_id)
            record = self._run_store.load(run_id)
            if record is not None:
                result = self._maybe_enrich_gemini_failure_result(
                    result=result,
                    record=record,
                )
            result_dict = result.model_dump(mode="json", by_alias=True)
            meta = dict(result_dict.get("metadata") or {})
>>>>>>> origin/main"""

repl1 = """        try:
            result = await adapter.fetch_result(run_id)
            record = self._run_store.load(run_id)
            if record is not None:
                result = self._maybe_enrich_gemini_failure_result(
                    result=result,
                    record=record,
                )

            # Build merged metadata from the typed result, then enrich with
            # push/PR URL info using model_copy to preserve the typed contract.
            meta = dict(result.metadata or {})"""

text = text.replace(block1, repl1)

# 2. Second block: fetch_result end
block2 = """<<<<<<< HEAD
        if meta:
            result = result.model_copy(update={"metadata": meta})

        return result
=======
            if meta:
                result_dict["metadata"] = meta

            return result_dict
        finally:
            await self._cleanup_managed_run_publish_support_best_effort(run_id)
>>>>>>> origin/main"""

repl2 = """            if meta:
                result = result.model_copy(update={"metadata": meta})

            return result
        finally:
            await self._cleanup_managed_run_publish_support_best_effort(run_id)"""

text = text.replace(block2, repl2)

# 3. Third block: cancel start
block3 = """<<<<<<< HEAD
            if self._run_supervisor is not None:
                try:
                    await self._run_supervisor.cancel(run_id_str)
=======
            run_id = str(run_id)
            try:
                if self._run_supervisor is not None:
                    await self._run_supervisor.cancel(run_id)
>>>>>>> origin/main"""

repl3 = """            run_id_str = str(run_id)
            if self._run_supervisor is not None:
                try:
                    await self._run_supervisor.cancel(run_id_str)"""

text = text.replace(block3, repl3)

# 4. Fourth block: asyncio cancelled error
block4 = """<<<<<<< HEAD
                except Exception as exc:
                    import asyncio as _asyncio
                    if isinstance(exc, _asyncio.CancelledError):
                        raise
=======
                else:
>>>>>>> origin/main"""

repl4 = """                except Exception as exc:
                    import asyncio as _asyncio
                    if isinstance(exc, _asyncio.CancelledError):
                        raise"""

text = text.replace(block4, repl4)

# 5. Fifth block: run_store block
block5 = """<<<<<<< HEAD
                return AgentRunStatus(
                    runId=run_id_str,
                    agentKind="managed",
                    agentId="managed",
                    status="canceled",
                )
            else:
                logger.warning(
                    "agent_runtime.cancel called for managed run %s but no supervisor configured",
                    run_id,
                )
                # Fall through to store-based cancel if possible
                if self._run_store is not None:
                    try:
                        self._run_store.update_status(
                            run_id_str,
=======
                    if self._run_store is not None:
                        self._run_store.update_status(
                            run_id,
>>>>>>> origin/main"""

repl5 = """            else:
                logger.warning(
                    "agent_runtime.cancel called for managed run %s but no supervisor configured",
                    run_id,
                )
                # Fall through to store-based cancel if possible
                if self._run_store is not None:
                    try:
                        self._run_store.update_status(
                            run_id_str,"""

text = text.replace(block5, repl5)

# 6. Sixth block: final cancel block
block6 = """<<<<<<< HEAD
                    except Exception:
                        logger.warning(
                            "agent_runtime.cancel store update failed for %s",
                            run_id,
                            exc_info=True,
                        )
                return AgentRunStatus(
                    runId=run_id_str,
                    agentKind="managed",
                    agentId="managed",
                    status="canceled",
                )
=======
            except Exception:
                logger.warning(
                    "agent_runtime.cancel failed for managed run %s",
                    run_id,
                    exc_info=True,
                )
            return AgentRunStatus(
                runId=run_id,
                agentKind="managed",
                agentId="managed",
                status="canceled",
            )
>>>>>>> origin/main"""

repl6 = """                    except Exception:
                        logger.warning(
                            "agent_runtime.cancel store update failed for %s",
                            run_id,
                            exc_info=True,
                        )
                return AgentRunStatus(
                    runId=run_id_str,
                    agentKind="managed",
                    agentId="managed",
                    status="canceled",
                )"""

text = text.replace(block6, repl6)

with open("moonmind/workflows/temporal/activity_runtime.py", "w") as f:
    f.write(text)

