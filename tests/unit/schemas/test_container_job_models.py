from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from moonmind.schemas.container_job_models import ContainerJobAccepted, ContainerJobSubmitRequest, ensure_temporal_safe

def payload():
    return {"idempotencyKey":"request-1","source":{"source":"omnigent","omnigentSessionId":"s"},"spec":{"image":{"reference":"ubuntu:24.04"},"workspace":{"kind":"sandbox","workspaceId":"ws"},"resources":{"cpuMillis":1000,"memoryMiB":512}}}

def test_submit_is_versioned_compact_and_reuses_workspace_locator():
    model = ContainerJobSubmitRequest.model_validate(payload())
    data = model.model_dump(mode="json", by_alias=True)
    assert data["contractVersion"] == "v1" and data["spec"]["workspace"]["kind"] == "sandbox"
    assert ensure_temporal_safe(model) == ensure_temporal_safe(model)

@pytest.mark.parametrize("field,value", [("dockerHost","tcp://daemon"),("socketPath","/var/run/docker.sock"),("hostPath","/tmp"),("privileged",True),("devices",["/dev/kvm"]),("labels",{"moonmind.owner":"x"}),("registryCredentials",{"password":"x"})])
def test_submit_rejects_caller_docker_authority(field, value):
    data = payload(); data["spec"][field] = value
    with pytest.raises(ValidationError): ContainerJobSubmitRequest.model_validate(data)

def test_sensitive_environment_and_absolute_outputs_are_rejected():
    data = payload(); data["spec"]["environment"] = [{"name":"API_TOKEN","value":"raw"}]
    with pytest.raises(ValidationError): ContainerJobSubmitRequest.model_validate(data)
    data = payload(); data["spec"]["outputs"] = [{"name":"x","relativePath":"/host/x"}]
    with pytest.raises(ValidationError): ContainerJobSubmitRequest.model_validate(data)

def test_accepted_response_has_async_queued_identity():
    result = ContainerJobAccepted(jobId="container-job:" + "a" * 32, createdAt=datetime.now(timezone.utc))
    assert result.state == "queued"
