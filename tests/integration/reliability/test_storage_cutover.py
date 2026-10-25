"""Exercise the deployment storage owner against disposable Docker workloads."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest
import yaml

from moonmind.workflows.skills.deployment_release import docker
from moonmind.workflows.skills.storage_migration import prepare_compose_storage, s3_client

pytestmark = [pytest.mark.integration, pytest.mark.reliability_journey, pytest.mark.asyncio]


@pytest.mark.timeout(240)
@pytest.mark.parametrize("reject_copy", [False, True])
async def test_controller_active_write_cutover_and_reverse_copy(tmp_path, monkeypatch, reject_copy):
    # This is the existing host-side reliability CI route, never a managed
    # agent's Docker socket or the installed deployment's project.
    assert os.environ.get("MOONMIND_TEST_DOCKER_NETWORK"), "Use the isolated reliability CI runner"
    project = "moonmind-test-storage-" + uuid4().hex[:12]
    root = Path(__file__).resolve().parents[3]
    source_image = yaml.safe_load((root / "docker-compose.test.yaml").read_text())["services"]["artifact-source"]["image"]
    destination_image = os.environ.get("TEMPORAL_ARTIFACT_S3_IMAGE", "pgsty/silo:latest")
    compose_file = tmp_path / "compose.json"
    state = tmp_path / "state"
    credentials = {"MINIO_ROOT_USER": "migration", "MINIO_ROOT_PASSWORD": "migration-test-only"}
    config = {"services": {
        "minio": {"image": source_image, "command": ["server", "/data"], "environment": credentials, "volumes": ["old-data:/data"]},
        "peer": {"image": "python:3.12-slim", "command": ["python", "-c", "import time; time.sleep(600)"]},
        "consumer": {"image": "python:3.12-slim", "command": ["python", "-c", "import time; time.sleep(600)"],
                     "environment": {"TEMPORAL_WORKER_FLEET": "artifacts"}},
    }, "volumes": {"old-data": {}, "new-data": {}}}

    def command(args, timeout=90):
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)

    base = ["docker", "compose", "--project-name", project, "-f", str(compose_file)]

    def compose(*args):
        result = command([*base, *args])
        assert result.returncode == 0, result.stderr[-1000:]
        return result.stdout.strip()

    def inspect_service(name):
        return json.loads(subprocess.check_output(["docker", "inspect", compose("ps", "-aq", name)], text=True))[0]

    def client(service="minio"):
        row = inspect_service(service)
        address = next(iter(row["NetworkSettings"]["Networks"].values()))["IPAddress"]
        return s3_client(f"http://{address}:9000", "migration", "migration-test-only")

    class Runner:
        async def _run_compose_command(self, args, **kwargs):
            result = await asyncio.to_thread(command, [*base, *args[2:]])
            return {"exitCode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    compose_file.write_text(json.dumps(config))
    try:
        await asyncio.to_thread(compose, "pull", "--quiet")
        pulled = await asyncio.to_thread(command, ["docker", "pull", destination_image])
        assert pulled.returncode == 0, pulled.stderr[-1000:]
        await asyncio.to_thread(compose, "up", "-d", "minio", "peer", "consumer")
        source = await asyncio.to_thread(client)
        for attempt in range(30):
            try:
                await asyncio.to_thread(source.list_buckets)
                break
            except Exception:
                if attempt == 29:
                    raise
                await asyncio.sleep(1)
        bucket = "controlled-workflow"
        source.create_bucket(Bucket=bucket)
        if reject_copy:
            # Seed an actual mismatched destination, then close its process
            # before the production owner opens the volume for transfer.
            config["services"]["seed"] = {
                "image": destination_image, "command": ["server", "/data"],
                "environment": credentials, "volumes": ["new-data:/data"],
            }
            compose_file.write_text(json.dumps(config))
            await asyncio.to_thread(compose, "up", "-d", "--no-deps", "seed")
            corrupt_destination = await asyncio.to_thread(client, "seed")
            for attempt in range(30):
                try:
                    await asyncio.to_thread(corrupt_destination.list_buckets)
                    break
                except Exception:
                    if attempt == 29:
                        raise
                    await asyncio.sleep(1)
            corrupt_destination.create_bucket(Bucket=bucket)
            corrupt_destination.put_object(Bucket=bucket, Key="workflow/logical-step/0", Body=b"wrong bytes")
            await asyncio.to_thread(compose, "stop", "seed")
        urls = [source.generate_presigned_url("put_object", Params={"Bucket": bucket, "Key": f"workflow/logical-step/{i}"}, ExpiresIn=600) for i in range(100)]
        (tmp_path / "urls.json").write_text(json.dumps(urls))
        writer = r"""
import http.client,json,os,time,urllib.parse
for i,url in enumerate(json.load(open('/work/urls.json'))):
    parsed=urllib.parse.urlsplit(url)
    connection=http.client.HTTPConnection(parsed.hostname,parsed.port,timeout=15)
    connection.request('PUT',parsed.path+'?'+parsed.query,body=('effect-'+str(i)).encode())
    response=connection.getresponse()
    if response.status != 200: raise RuntimeError('controlled upload refused')
    response.read();connection.close()
    with open('/work/confirmed.jsonl','a') as stream:
        stream.write(json.dumps(i)+'\n');stream.flush();os.fsync(stream.fileno())
    time.sleep(.1)
"""
        config["services"]["writer"] = {
            "image": "python:3.12-slim", "command": ["python", "-c", writer],
            "environment": {"TEMPORAL_ARTIFACT_S3_ENDPOINT": "http://minio:9000",
                            "TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID": "migration",
                            "TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY": "migration-test-only",
                            "TEMPORAL_ARTIFACT_S3_BUCKET": bucket},
            "volumes": [f"{tmp_path}:/work"],
        }
        compose_file.write_text(json.dumps(config))
        writer_id = await asyncio.to_thread(compose, "run", "-d", "--no-deps", "writer")
        writer_row = json.loads(await docker("inspect", writer_id))[0]
        assert writer_row["Config"]["Labels"]["com.docker.compose.oneoff"].lower() == "true"
        deadline = time.monotonic() + 30
        while not (tmp_path / "confirmed.jsonl").exists():
            assert time.monotonic() < deadline, "controlled writer made no confirmed progress"
            await asyncio.sleep(.1)
        peer = await asyncio.to_thread(inspect_service, "peer")
        consumer = await asyncio.to_thread(inspect_service, "consumer")
        assert len(consumer["Id"]) == 64
        short_ids = (await docker("ps", "--all", "--quiet", "--filter", f"label=com.docker.compose.project={project}")).split()
        assert consumer["Id"][:12] in short_ids and consumer["Id"] not in short_ids
        monkeypatch.setenv("HOSTNAME", peer["Id"])
        config["services"]["minio"].update(image=destination_image, volumes=["new-data:/data"])
        compose_file.write_text(json.dumps(config))
        payload = {"moonmindImage": "unused-test-image", "target": {"project": project, "services": ["minio"]}}
        if reject_copy:
            with pytest.raises(ValueError, match="mismatch"):
                await prepare_compose_storage(Runner(), docker, payload, state)
        else:
            receipt = await prepare_compose_storage(Runner(), docker, payload, state)
            assert receipt["status"] == "prepared" and receipt["sourceRetained"]
        resumed = json.loads(await docker("inspect", consumer["Id"]))[0]
        assert resumed["State"]["Running"]
        assert not json.loads(await docker("inspect", writer_id))[0]["State"]["Running"]
        confirmed = [json.loads(line) for line in (tmp_path / "confirmed.jsonl").read_text().splitlines()]
        assert 0 < len(confirmed) < 100 and len(confirmed) == len(set(confirmed))
        if reject_copy:
            assert (await asyncio.to_thread(inspect_service, "minio"))["State"]["Running"]
            for i in confirmed:
                assert source.get_object(Bucket=bucket, Key=f"workflow/logical-step/{i}")["Body"].read() == f"effect-{i}".encode()
            return
        destination = await asyncio.to_thread(client)
        for i in confirmed:
            assert destination.get_object(Bucket=bucket, Key=f"workflow/logical-step/{i}")["Body"].read() == f"effect-{i}".encode()
        destination.put_object(Bucket=bucket, Key="workflow/next-step", Body=b"one new confirmed effect")
        expected = {obj["Key"] for obj in destination.list_objects_v2(Bucket=bucket)["Contents"]}
        config["services"]["minio"].update(image=source_image, volumes=["old-data:/data"])
        compose_file.write_text(json.dumps(config))
        rollback = await prepare_compose_storage(Runner(), docker, payload, state)
        assert rollback["status"] == "prepared"
        restored = await asyncio.to_thread(client)
        assert {obj["Key"] for obj in restored.list_objects_v2(Bucket=bucket)["Contents"]} == expected
        assert restored.get_object(Bucket=bucket, Key="workflow/next-step")["Body"].read() == b"one new confirmed effect"
        assert json.loads(await docker("inspect", consumer["Id"]))[0]["State"]["Running"]
        assert not json.loads(await docker("inspect", writer_id))[0]["State"]["Running"]
        assert (tmp_path / "confirmed.jsonl").read_text().splitlines() == [str(i) for i in confirmed]
    finally:
        # Only this test's named temporary stores and disposable project.
        for directory in state.iterdir() if state.exists() else []:
            if directory.is_dir():
                result = command(["docker", "ps", "-aq", "--filter", f"label=moonmind.storage.transfer={directory.name}"])
                if result.stdout.strip():
                    command(["docker", "rm", "-f", *result.stdout.split()])
        command([*base, "down", "--volumes", "--remove-orphans"])
