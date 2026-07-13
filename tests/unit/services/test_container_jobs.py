import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from api_service.db.models import Base
from api_service.services.container_jobs import ContainerJobNotFoundError, ContainerJobService
from moonmind.schemas.container_job_models import ContainerJobCancelRequest, ContainerJobSubmitRequest, OwnerIdentity

@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as value: yield value
    await engine.dispose()

def submission():
    return ContainerJobSubmitRequest(idempotencyKey="key",source={"source":"mcp","callerRequestId":"r"},spec={"image":{"reference":"alpine"},"workspace":{"kind":"managed_runtime","runtimeId":"codex","agentRunId":"run"},"resources":{"cpuMillis":100,"memoryMiB":64}})

@pytest.mark.asyncio
async def test_create_or_replay_and_owner_scoped_reads(session):
    service=ContainerJobService(session); owner=OwnerIdentity(principalId="user-1",principalType="user")
    first=await service.submit(owner=owner,request=submission()); second=await service.submit(owner=owner,request=submission())
    assert first.job_id == second.job_id and second.replayed
    assert (await service.status(owner_id="user-1",job_id=first.job_id)).state == "queued"
    with pytest.raises(ContainerJobNotFoundError): await service.status(owner_id="user-2",job_id=first.job_id)

@pytest.mark.asyncio
async def test_owner_scoped_idempotent_cancel(session):
    service=ContainerJobService(session); accepted=await service.submit(owner=OwnerIdentity(principalId="u",principalType="user"),request=submission())
    request=ContainerJobCancelRequest(idempotencyKey="cancel-1")
    first=await service.cancel(owner_id="u",job_id=accepted.job_id,request=request); second=await service.cancel(owner_id="u",job_id=accepted.job_id,request=request)
    assert first.accepted and second.replayed and second.state == "canceling"
    with pytest.raises(ContainerJobNotFoundError): await service.cancel(owner_id="other",job_id=accepted.job_id,request=request)
