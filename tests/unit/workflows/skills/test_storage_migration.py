"""S3 transfer and deployment fencing behavior for issue #5003."""

from __future__ import annotations

import io
from copy import deepcopy

import pytest
from botocore.exceptions import ClientError


class Store:
    def __init__(self, objects=None):
        self.objects = objects or {}
        self.uploads = []
        self.puts = []
        self.interrupt = False

    def list_buckets(self):
        return {"Buckets": [{"Name": "artifacts"}]}

    def head_bucket(self, **kwargs):
        return {}

    def get_bucket_versioning(self, **kwargs):
        return {}

    def get_bucket_policy(self, **kwargs):
        raise ClientError({"Error": {"Code": "NoSuchBucketPolicy"}}, "GetBucketPolicy")

    def get_bucket_cors(self, **kwargs):
        raise ClientError({"Error": {"Code": "NoSuchCORSConfiguration"}}, "GetBucketCors")

    def get_bucket_lifecycle_configuration(self, **kwargs):
        raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration")

    def list_multipart_uploads(self, **kwargs):
        return {"Uploads": self.uploads}

    def get_paginator(self, name):
        store = self

        class Pages:
            def paginate(self, **kwargs):
                # Force separate pages; inventory must not truncate after one.
                for key, obj in sorted(store.objects.items()):
                    yield {"Contents": [{"Key": key, "Size": len(obj["Body"])}]}
        return Pages()

    def head_object(self, *, Key, **kwargs):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")
        obj = self.objects[Key]
        return {**deepcopy(obj), "ContentLength": len(obj["Body"]), "ETag": '"opaque"'}

    def get_object(self, *, Key, **kwargs):
        return {**self.head_object(Key=Key), "Body": io.BytesIO(self.objects[Key]["Body"])}

    def get_object_tagging(self, *, Key, **kwargs):
        return {"TagSet": self.objects[Key].get("TagSet", [])}

    def upload_fileobj(self, stream, bucket, key, *, ExtraArgs, Config):
        self.puts.append(key)
        if self.interrupt:
            self.interrupt = False
            raise OSError("interrupted upload")
        from urllib.parse import parse_qsl

        args = dict(ExtraArgs)
        tags = [{"Key": k, "Value": v} for k, v in parse_qsl(args.pop("Tagging", ""))]
        self.objects[key] = {"Body": stream.read(), **args, "TagSet": tags}


def transfer(source, destination, state, **kwargs):
    from moonmind.workflows.skills.storage_migration import transfer_buckets

    return transfer_buckets(source, destination, state, **kwargs)


def test_transfer_is_resumable_preserves_keys_metadata_and_verifies_bytes(tmp_path):
    source = Store({
        "input/a": {"Body": b"saved input", "ContentType": "application/json", "Metadata": {"sha256": "consumer"}},
        "history/b": {"Body": b"history", "TagSet": [{"Key": "use", "Value": "retained"}]},
    })
    destination = Store()
    state = tmp_path / "copy.sqlite"
    destination.interrupt = True
    with pytest.raises(OSError, match="interrupted"):
        transfer(source, destination, state)
    result = transfer(source, destination, state)
    assert result["verifiedObjects"] == 2
    assert destination.objects["input/a"]["Metadata"] == {"sha256": "consumer"}
    assert destination.objects["history/b"]["TagSet"] == source.objects["history/b"]["TagSet"]
    before = list(destination.puts)
    assert transfer(source, destination, state)["verifiedObjects"] == 2
    assert destination.puts == before
    assert source.objects["input/a"]["Body"] == b"saved input"


@pytest.mark.parametrize("bad", [b"bad", b"wrong bytes"])
def test_mismatched_existing_object_refuses_cutover_without_overwriting_source(tmp_path, bad):
    source = Store({"key": {"Body": b"saved input"}})
    destination = Store({"key": {"Body": bad}})
    with pytest.raises(ValueError, match="mismatch"):
        transfer(source, destination, tmp_path / "copy.sqlite")
    assert source.objects["key"]["Body"] == b"saved input"
    assert destination.puts == []


def test_completed_copy_is_reverified_on_retry(tmp_path):
    source = Store({"key": {"Body": b"correct"}})
    destination = Store()
    state = tmp_path / "copy.sqlite"
    transfer(source, destination, state)
    destination.objects["key"]["Body"] = b"corrupt"
    with pytest.raises(ValueError, match="mismatch"):
        transfer(source, destination, state)


def test_pending_multipart_blocks_transfer_without_aborting_upload(tmp_path):
    source = Store({"key": {"Body": b"saved"}})
    source.uploads = [{"Key": "new", "UploadId": "in-flight"}]
    with pytest.raises(ValueError, match="multipart"):
        transfer(source, Store(), tmp_path / "copy.sqlite")
    assert source.uploads[0]["UploadId"] == "in-flight"


def test_destination_cannot_keep_an_anonymous_policy_absent_from_source(tmp_path):
    source = Store({"key": {"Body": b"private artifact"}})
    destination = Store()
    destination.get_bucket_policy = lambda **kwargs: {"Policy": '{"Statement":[{"Effect":"Allow","Principal":"*"}]}'}
    with pytest.raises(ValueError, match="configuration mismatch"):
        transfer(source, destination, tmp_path / "copy.sqlite")


def test_inventory_change_during_copy_refuses_cutover(tmp_path):
    source = Store({"key": {"Body": b"saved"}})
    destination = Store()
    original = destination.upload_fileobj

    def concurrent_write(*args, **kwargs):
        original(*args, **kwargs)
        source.objects["late"] = {"Body": b"late write"}

    destination.upload_fileobj = concurrent_write
    with pytest.raises(ValueError, match="inventory"):
        transfer(source, destination, tmp_path / "copy.sqlite")
    assert source.objects["late"]["Body"] == b"late write"


def test_retry_cannot_reuse_journal_for_a_different_destination(tmp_path):
    source = Store({"key": {"Body": b"saved"}})
    state = tmp_path / "copy.sqlite"
    transfer(source, Store(), state, binding={"source": "old-volume", "destination": "new-volume"})
    with pytest.raises(ValueError, match="identity"):
        transfer(source, Store(), state, binding={"source": "old-volume", "destination": "other-volume"})


def test_rollback_copies_new_writes_back_without_deleting_retained_bytes(tmp_path):
    old = Store({"historical": {"Body": b"original"}})
    new = Store()
    transfer(old, new, tmp_path / "forward.sqlite")
    new.objects["new-step"] = {"Body": b"confirmed new effect"}
    transfer(new, old, tmp_path / "rollback.sqlite")
    assert {key: value["Body"] for key, value in old.objects.items()} == {
        key: value["Body"] for key, value in new.objects.items()
    }


@pytest.mark.asyncio
async def test_quiescence_covers_api_workers_sweepers_and_oneoffs():
    import json
    from moonmind.workflows.skills.storage_migration import quiesce_clients

    rows = [{
        "Id": name,
        "Config": {"Env": env, "Labels": {"com.docker.compose.project": "moonmind-test", "com.docker.compose.service": name}},
        "State": {"Running": True},
    } for name, env in (
        ("api", []), ("custom-worker", ["TEMPORAL_WORKER_FLEET=agent_runtime"]),
        ("sweeper", ["TEMPORAL_WORKER_FLEET=artifacts"]),
        ("oneoff", ["TEMPORAL_ARTIFACT_S3_ENDPOINT=http://store:9000"]),
        ("postgres", []), ("helper", ["TEMPORAL_WORKER_FLEET=artifacts"]),
    )]
    commands = []

    async def docker(*args, **kwargs):
        commands.append(args)
        if args[0] == "ps":
            return "\n".join(row["Id"] for row in rows)
        if args[0] == "inspect":
            return json.dumps([row for row in rows if row["Id"] in args[1:]])
        assert args[:3] == ("stop", "--time", "60")
        for row in rows:
            if row["Id"] in args[3:]:
                row["State"]["Running"] = False
        return ""

    assert await quiesce_clients(docker, "moonmind-test", exclude=("helper",)) == ["api", "custom-worker", "sweeper", "oneoff"]
    assert rows[-2]["State"]["Running"] and rows[-1]["State"]["Running"]


@pytest.mark.asyncio
async def test_cutover_does_not_activate_if_transfer_cannot_verify(tmp_path, monkeypatch):
    from moonmind.workflows.skills import storage_migration as migration

    events = []

    async def isolate():
        events.append("isolated")
        return Store({"key": {"Body": b"saved"}}), Store({"key": {"Body": b"wrong"}})

    async def activate():
        events.append("activated")

    with pytest.raises(ValueError, match="mismatch"):
        await migration.verified_cutover(isolate, activate, tmp_path / "copy.sqlite")
    assert events == ["isolated"]


class Deployment:
    """A Docker observation boundary, with independent source/destination data."""

    def __init__(self, monkeypatch, *, corrupt=False, oneoff=False, activation_error=False):
        from types import SimpleNamespace
        from moonmind.workflows.skills import storage_migration as migration

        self.events = []
        self.source = Store({"workflow/step": {"Body": b"confirmed saved work"}})
        self.destination = Store({"workflow/step": {"Body": b"corrupted saved work"}} if corrupt else {})
        self.source.meta = SimpleNamespace(endpoint_url="http://source:9000")
        self.destination.meta = SimpleNamespace(endpoint_url="http://destination:9000")
        self.rows = {}
        self.activation_error = activation_error
        self.retained_empty = False
        self.current = "original"

        def row(name, volume=None, env=(), running=True, oneoff=False):
            return {"Id": name, "Image": "original-image", "Config": {
                "Env": list(env), "Cmd": ["server", "/data"], "Entrypoint": None,
                "Labels": {"com.docker.compose.project": "moonmind-test", "com.docker.compose.service": name,
                           "com.docker.compose.oneoff": str(oneoff)},
            }, "State": {"Running": running},
                "Mounts": [{"Destination": "/data", "Type": "volume", "Name": volume}] if volume else [],
                "NetworkSettings": {"Networks": {"test-net": {"IPAddress": "source" if volume == "old-volume" else "destination"}}}}

        self.row = row
        self.rows["original"] = row("original", "old-volume")
        self.rows["api"] = row("api", env=["TEMPORAL_ARTIFACT_S3_ENDPOINT=http://minio:9000"], oneoff=oneoff)
        self.rows["helper"] = row("helper")
        self.config = {"services": {"minio": {"image": "silo:latest", "volumes": [{"type": "volume", "source": "destination", "target": "/data"}]},
                                    "api": {"environment": {"TEMPORAL_ARTIFACT_S3_ENDPOINT": "http://minio:9000"}}},
                       "volumes": {"destination": {"name": "new-volume"}}}
        monkeypatch.setenv("HOSTNAME", "helper")
        monkeypatch.setattr(migration, "s3_client", lambda endpoint, *args: self.source if endpoint == "http://source:9000" else self.destination)
        monkeypatch.setattr(migration, "qualify_store", lambda *args: self.events.append("qualified"))

    async def _run_compose_command(self, args, **kwargs):
        import json
        if "config" in args:
            return {"exitCode": 0, "stdout": json.dumps(self.config)}
        assert "up" in args
        self.events.append("activate")
        if self.activation_error:
            return {"exitCode": 1}
        self.current = "active"
        volume = self.config["volumes"][self.config["services"]["minio"]["volumes"][0]["source"]]["name"]
        self.rows["active"] = self.row("active", volume)
        return {"exitCode": 0}

    async def docker(self, *args, **kwargs):
        import json
        if args[:2] == ("volume", "ls"):
            return "new-volume" if "name=^new-volume$" in args else "old-volume"
        if args[0] == "ps":
            name_filter = next((value for value in args if value.startswith("name=")), None)
            if name_filter:
                name = name_filter.removeprefix("name=^/").removesuffix("$")
                return name if name in self.rows else ""
            if "label=com.docker.compose.service=minio" in args:
                return self.current
            return "\n".join((self.current, "api", "helper"))
        if args[0] == "inspect":
            return json.dumps([self.rows[name] for name in args[1:]])
        if args[0] == "run":
            if "--rm" in args:
                assert "readonly" in args[args.index("--mount") + 1]
                return json.dumps({"empty": self.retained_empty})
            name = args[args.index("--name") + 1]
            assert not self.rows.get("original", {"State": {"Running": False}})["State"]["Running"]
            mount = args[args.index("--mount") + 1]
            volume = "old-volume" if "old-volume" in mount else "new-volume"
            row = self.row(name, volume)
            label = args[args.index("--label") + 1]
            row["Config"]["Labels"]["moonmind.storage.transfer"] = label.split("=", 1)[1]
            self.rows[name] = row
            self.events.append("private-" + name.rsplit("-", 1)[-1])
            assert "--publish" not in args and "--network-alias" not in args
            return name
        if args[0] == "rm":
            self.rows.pop(args[1])
            return ""
        if args[0] == "stop":
            self.events.append("stop:" + ",".join(args[3:]))
            for name in args[3:]:
                self.rows[name]["State"]["Running"] = False
            return ""
        if args[0] == "start":
            self.events.append("start:" + ",".join(args[1:]))
            for name in args[1:]:
                self.rows[name]["State"]["Running"] = True
            return ""
        raise AssertionError(args)

    async def prepare(self, root, *, selected=("api", "minio"), observe=False):
        from moonmind.workflows.skills.storage_migration import prepare_compose_storage

        return await prepare_compose_storage(self, self.docker, {
            "moonmindImage": "app", "target": {"project": "moonmind-test", "services": list(selected)},
        }, root, observe_only=observe)


@pytest.mark.asyncio
async def test_controller_copies_only_after_public_source_stops(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    receipt = await deployment.prepare(tmp_path)
    assert receipt["verifiedObjects"] == 1 and receipt["sourceRetained"]
    assert deployment.events.index("stop:api") < deployment.events.index("stop:original") < deployment.events.index("private-source")
    assert deployment.events.index("qualified") < deployment.events.index("activate")
    assert not deployment.rows["api"]["State"]["Running"]  # Main apply recreates selected consumers.
    assert deployment.source.objects["workflow/step"]["Body"] == b"confirmed saved work"


@pytest.mark.asyncio
async def test_corrupt_destination_recovers_original_service_without_cutover(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch, corrupt=True)
    with pytest.raises(ValueError, match="mismatch"):
        await deployment.prepare(tmp_path)
    assert "activate" not in deployment.events
    assert deployment.rows["api"]["State"]["Running"]
    assert deployment.rows["original"]["State"]["Running"]
    assert deployment.source.objects["workflow/step"]["Body"] == b"confirmed saved work"


@pytest.mark.asyncio
async def test_uncertain_activation_stays_fenced_and_retry_reuses_verified_copy(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch, activation_error=True)
    with pytest.raises(ValueError, match="activation failed"):
        await deployment.prepare(tmp_path)
    assert not deployment.rows["api"]["State"]["Running"]
    assert not deployment.rows["original"]["State"]["Running"]
    puts = list(deployment.destination.puts)
    deployment.activation_error = False
    assert (await deployment.prepare(tmp_path))["status"] == "prepared"
    assert deployment.destination.puts == puts


@pytest.mark.asyncio
async def test_source_recovery_never_reexecutes_oneoff_effects(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch, corrupt=True, oneoff=True)
    with pytest.raises(ValueError, match="mismatch"):
        await deployment.prepare(tmp_path)
    assert not deployment.rows["api"]["State"]["Running"]
    assert deployment.rows["original"]["State"]["Running"]


@pytest.mark.asyncio
async def test_storage_only_update_resumes_consumers_outside_main_apply(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    await deployment.prepare(tmp_path, selected=("minio",))
    assert deployment.rows["api"]["State"]["Running"]


@pytest.mark.asyncio
async def test_stop_succeeded_inspection_failed_retains_consumers_for_retry(tmp_path, monkeypatch):
    import json

    deployment = Deployment(monkeypatch)
    docker = deployment.docker
    lost_inspection = False

    async def lose_inspection(*args, **kwargs):
        nonlocal lost_inspection
        if args == ("inspect", "api") and "stop:api" in deployment.events and not lost_inspection:
            lost_inspection = True
            raise OSError("lost post-stop inspection")
        return await docker(*args, **kwargs)

    deployment.docker = lose_inspection
    with pytest.raises(OSError, match="post-stop inspection"):
        await deployment.prepare(tmp_path, selected=("minio",))
    fence = json.loads(next(tmp_path.glob("*/fence.json")).read_text())
    assert "api" in fence["stopped"]
    await deployment.prepare(tmp_path, selected=("minio",))
    assert deployment.rows["api"]["State"]["Running"]


@pytest.mark.asyncio
@pytest.mark.parametrize("container_cleanup", [False, True])
async def test_activated_retry_finishes_pending_consumer_recovery(tmp_path, monkeypatch, container_cleanup):
    deployment = Deployment(monkeypatch)
    docker = deployment.docker
    failed_start = False

    async def lose_start(*args, **kwargs):
        nonlocal failed_start
        if args == ("start", "api") and not failed_start:
            failed_start = True
            raise OSError("consumer start unavailable")
        return await docker(*args, **kwargs)

    deployment.docker = lose_start
    with pytest.raises(OSError, match="consumer start unavailable"):
        await deployment.prepare(tmp_path, selected=("minio",))
    assert not deployment.rows["api"]["State"]["Running"]
    puts = list(deployment.destination.puts)
    if container_cleanup:
        deployment.current = ""
        deployment.rows.pop("active")
    await deployment.prepare(tmp_path, selected=("minio",))
    assert deployment.rows["api"]["State"]["Running"]
    assert deployment.destination.puts == puts
    assert deployment.events.count("private-source") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable_cleanup", [False, True])
async def test_lost_private_creation_ack_keeps_source_volume_exclusive(tmp_path, monkeypatch, unavailable_cleanup):
    import json

    deployment = Deployment(monkeypatch)
    docker = deployment.docker

    async def lose_creation(*args, **kwargs):
        if args[0] == "run" and args[args.index("--name") + 1].endswith("-source"):
            await docker(*args, **kwargs)
            raise OSError("lost private creation acknowledgement")
        if unavailable_cleanup and args[0] == "stop" and args[-1].endswith("-source"):
            raise OSError("private source stop unavailable")
        return await docker(*args, **kwargs)

    deployment.docker = lose_creation
    with pytest.raises(OSError, match="creation acknowledgement"):
        await deployment.prepare(tmp_path, selected=("minio",))
    fence = json.loads(next(tmp_path.glob("*/fence.json")).read_text())
    assert list(fence["temporary"].values()) == ["old-volume"]
    owners = [row for row in deployment.rows.values() if row["State"]["Running"]
              and any(mount["Name"] == "old-volume" for mount in row["Mounts"])]
    assert len(owners) <= 1
    assert deployment.rows["api"]["State"]["Running"] is not unavailable_cleanup
    assert deployment.rows["original"]["State"]["Running"] is not unavailable_cleanup
    assert deployment.source.objects["workflow/step"]["Body"] == b"confirmed saved work"


@pytest.mark.asyncio
async def test_retry_reconciles_saved_private_server_before_source_recovery(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    docker = deployment.docker

    async def unavailable_source(*args, **kwargs):
        if args[0] == "run" and args[args.index("--name") + 1].endswith("-source"):
            await docker(*args, **kwargs)
            raise OSError("lost private creation acknowledgement")
        if args[0] == "stop" and args[-1].endswith("-source"):
            raise OSError("private stop unavailable")
        return await docker(*args, **kwargs)

    deployment.docker = unavailable_source
    with pytest.raises(OSError, match="creation acknowledgement"):
        await deployment.prepare(tmp_path, selected=("minio",))
    assert not deployment.rows["original"]["State"]["Running"]

    async def failed_retry_inventory(*args, **kwargs):
        if args == ("inspect", "original", "api", "helper"):
            raise OSError("retry inventory unavailable")
        return await docker(*args, **kwargs)

    deployment.docker = failed_retry_inventory
    with pytest.raises(OSError, match="retry inventory unavailable"):
        await deployment.prepare(tmp_path, selected=("minio",))
    assert deployment.rows["original"]["State"]["Running"]
    assert deployment.rows["api"]["State"]["Running"]
    assert all(not row["State"]["Running"] for name, row in deployment.rows.items()
               if name.startswith("moonmind-storage-"))


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt,oneoff", [(False, False), (True, False), (False, True), (True, True)])
async def test_docker_full_inspect_and_short_listing_ids_recover_without_repeating_oneoffs(tmp_path, monkeypatch, corrupt, oneoff):
    import hashlib

    deployment = Deployment(monkeypatch, corrupt=corrupt, oneoff=oneoff)
    docker = deployment.docker
    identifiers = {name: hashlib.sha256(name.encode()).hexdigest() for name in deployment.rows}
    for name, identifier in identifiers.items():
        deployment.rows[name]["Id"] = identifier

    async def realistic_ids(*args, **kwargs):
        names = {value: name for name, identifier in identifiers.items() for value in (identifier, identifier[:12])}
        translated = tuple(names.get(arg, arg) for arg in args)
        result = await docker(*translated, **kwargs)
        if args[0] == "ps":
            return "\n".join((identifiers[value] if "--no-trunc" in args else identifiers[value][:12])
                             if value in identifiers else value
                             for value in result.split())
        return result

    deployment.docker = realistic_ids
    if corrupt:
        with pytest.raises(ValueError, match="mismatch"):
            await deployment.prepare(tmp_path, selected=("minio",))
    else:
        await deployment.prepare(tmp_path, selected=("minio",))
    assert deployment.rows["api"]["State"]["Running"] is not oneoff


@pytest.mark.asyncio
async def test_verified_destination_is_restored_after_container_cleanup(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    first = await deployment.prepare(tmp_path)
    deployment.current = ""
    deployment.rows.pop("active")
    recovered = await deployment.prepare(tmp_path)
    assert recovered["verifiedObjects"] == first["verifiedObjects"]
    assert deployment.current == "active"
    assert deployment.destination.objects["workflow/step"]["Body"] == b"confirmed saved work"


@pytest.mark.asyncio
async def test_new_install_accepts_only_a_verified_empty_retained_volume(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    deployment.current = ""
    deployment.retained_empty = True
    assert (await deployment.prepare(tmp_path))["status"] == "skipped"
    assert "activate" not in deployment.events


def test_failed_qualification_keeps_the_original_error_when_cleanup_fails():
    from types import SimpleNamespace
    from moonmind.workflows.skills.storage_migration import qualify_store

    def put(**kwargs):
        raise OSError("original write failure")

    def delete(**kwargs):
        raise RuntimeError("cleanup unavailable")

    with pytest.raises(OSError, match="original write failure"):
        qualify_store(SimpleNamespace(put_object=put, delete_object=delete), "bucket")


def test_incorrect_head_fails_destination_qualification():
    from types import SimpleNamespace
    from moonmind.workflows.skills.storage_migration import qualify_store

    client = SimpleNamespace(put_object=lambda **k: {}, head_object=lambda **k: {"ContentLength": 0}, delete_object=lambda **k: {})
    with pytest.raises(ValueError, match="HEAD"):
        qualify_store(client, "bucket")


@pytest.mark.asyncio
async def test_retry_recovers_saved_source_after_its_container_is_removed(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    deployment.destination.interrupt = True
    with pytest.raises(OSError, match="interrupted"):
        await deployment.prepare(tmp_path)
    deployment.current = ""
    deployment.rows.pop("original")
    receipt = await deployment.prepare(tmp_path)
    assert receipt["verifiedObjects"] == 1
    assert deployment.destination.objects["workflow/step"]["Body"] == b"confirmed saved work"


@pytest.mark.asyncio
async def test_old_forward_receipt_cannot_skip_writes_after_rollback(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    await deployment.prepare(tmp_path)
    deployment.config["volumes"]["destination"]["name"] = "old-volume"
    await deployment.prepare(tmp_path)  # Reverse copy; old volume owns new writes again.
    deployment.source.objects["after-rollback"] = {"Body": b"latest confirmed work"}
    deployment.current = ""
    deployment.rows.pop("active")
    deployment.config["volumes"]["destination"]["name"] = "new-volume"
    await deployment.prepare(tmp_path)
    assert deployment.destination.objects["after-rollback"]["Body"] == b"latest confirmed work"


@pytest.mark.asyncio
async def test_post_apply_observation_records_actual_store_without_reapplying(tmp_path, monkeypatch):
    import json

    deployment = Deployment(monkeypatch)
    await deployment.prepare(tmp_path)
    deployment.rows["active"]["Image"] = "new-installed-storage-image"
    events = list(deployment.events)
    await deployment.prepare(tmp_path, observe=True)
    owner = json.loads(next(tmp_path.glob("project-*.json")).read_text())
    assert owner["container"]["Image"] == "new-installed-storage-image"
    assert deployment.events == events


@pytest.mark.asyncio
async def test_post_apply_observation_cannot_recreate_missing_store(tmp_path, monkeypatch):
    deployment = Deployment(monkeypatch)
    await deployment.prepare(tmp_path)
    deployment.current = ""
    with pytest.raises(ValueError, match="observation"):
        await deployment.prepare(tmp_path, observe=True)
    assert deployment.events.count("activate") == 1
