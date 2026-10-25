"""Bounded S3 copying for the existing deployment owner.

The controller fences clients and isolates the source before calling this code.
Only object APIs cross the storage-format boundary; neither references nor the
source's data are rewritten. The SQLite receipt lives on deployment state.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from urllib.parse import urlencode
from uuid import uuid4

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError


CONTENT_FIELDS = (
    "ContentType", "CacheControl", "ContentDisposition", "ContentEncoding",
    "ContentLanguage", "Expires", "WebsiteRedirectLocation", "Metadata",
)
MISSING = {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}


def _missing(exc, codes=MISSING):
    return str(exc.response.get("Error", {}).get("Code")) in codes


def s3_client(endpoint, access, secret):
    return boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=access,
        aws_secret_access_key=secret, region_name="us-east-1",
        config=Config(connect_timeout=5, read_timeout=30, proxies={},
                      retries={"max_attempts": 2}, s3={"addressing_style": "path"}),
    )


def _deadline_check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("Storage transfer budget exhausted; resume the retained receipt")


def _inventory(client, buckets, deadline):
    """Ordered, paged inventory digest; object bodies are never buffered here."""
    digest = hashlib.sha256()
    for bucket in sorted(buckets):
        _deadline_check(deadline)
        digest.update(json.dumps([bucket]).encode())
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            _deadline_check(deadline)
            for item in page.get("Contents", []):
                row = [bucket, item["Key"], item["Size"], item.get("ETag"), str(item.get("LastModified"))]
                digest.update(json.dumps(row, ensure_ascii=True).encode())
                yield bucket, item["Key"], int(item["Size"]), digest
    yield None, None, 0, digest


def _hash_stream(stream, deadline, spool=None):
    digest, size = hashlib.sha256(), 0
    with closing(stream):
        while chunk := stream.read(1024 * 1024):
            _deadline_check(deadline)
            digest.update(chunk)
            size += len(chunk)
            if spool is not None:
                spool.write(chunk)
    return digest.hexdigest(), size


def _attributes(client, bucket, key, head):
    attributes = {name: head[name] for name in CONTENT_FIELDS if name in head}
    tags = client.get_object_tagging(Bucket=bucket, Key=key).get("TagSet", [])
    if tags:
        attributes["Tagging"] = urlencode(sorted((tag["Key"], tag["Value"]) for tag in tags))
    return attributes


def _bucket_configuration(source, destination, bucket):
    # The application retains history with immutable object keys and DB claims,
    # not S3 versions. Refuse unsupported installed configurations, preserving
    # all versions and lock state on the untouched source.
    if source.get_bucket_versioning(Bucket=bucket).get("Status"):
        raise ValueError("Versioned/locked buckets require a version-preserving transfer; source retained")
    if source.list_multipart_uploads(Bucket=bucket, MaxUploads=1).get("Uploads"):
        raise ValueError("Unfinished source multipart uploads prevent cutover; resume them on the source")
    try:
        destination.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if not _missing(exc):
            raise
        destination.create_bucket(Bucket=bucket)
    # Preserve effective anonymous/bucket authorization, CORS and lifecycle
    # settings. Never interpret an unreadable policy as an absent policy.
    for get_name, put_name, field, absent in (
        ("get_bucket_policy", "put_bucket_policy", "Policy", {"NoSuchBucketPolicy"}),
        ("get_bucket_cors", "put_bucket_cors", "CORSConfiguration", {"NoSuchCORSConfiguration"}),
        ("get_bucket_lifecycle_configuration", "put_bucket_lifecycle_configuration", "LifecycleConfiguration", {"NoSuchLifecycleConfiguration"}),
    ):
        try:
            value = getattr(source, get_name)(Bucket=bucket)
        except ClientError as exc:
            if not _missing(exc, absent):
                raise
            try:
                getattr(destination, get_name)(Bucket=bucket)
            except ClientError as target_error:
                if not _missing(target_error, absent):
                    raise
            else:
                raise ValueError("Destination bucket configuration mismatch; cutover refused")
            continue
        content = value["Policy"] if field == "Policy" else {"CORSRules" if field == "CORSConfiguration" else "Rules": value["CORSRules" if field == "CORSConfiguration" else "Rules"]}
        getattr(destination, put_name)(Bucket=bucket, **{field: content})


def transfer_buckets(source, destination, state_path, *, binding=None, buckets=None, timeout_seconds=840):
    """Copy once, reconcile uncertain uploads, then verify every required byte.

    Existing mismatches stop the operation rather than silently replacing
    evidence. Committed receipts never replace a fresh read of either store.
    Extra retained destination objects are not deleted, including on rollback.
    """
    deadline = time.monotonic() + timeout_seconds
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = json.dumps(binding or {}, sort_keys=True)
    run = uuid4().hex
    with closing(sqlite3.connect(path)) as db:
        os.chmod(path, 0o600)
        db.execute("CREATE TABLE IF NOT EXISTS identity (binding TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS objects (bucket TEXT, key TEXT, sha256 TEXT, size INTEGER, verified_run TEXT, PRIMARY KEY(bucket,key))")
        identity = db.execute("SELECT binding FROM identity").fetchone()
        if identity is not None and identity[0] != descriptor:
            raise ValueError("Storage receipt identity differs from requested source/destination")
        if identity is None:
            db.execute("INSERT INTO identity VALUES (?)", (descriptor,))
            db.commit()
        selected_buckets = sorted(buckets) if buckets is not None else None
        buckets = selected_buckets if selected_buckets is not None else sorted(row["Name"] for row in source.list_buckets()["Buckets"])
        for bucket in buckets:
            _bucket_configuration(source, destination, bucket)
        count, total = 0, 0
        for bucket, key, expected_size, inventory in _inventory(source, buckets, deadline):
            if bucket is None:
                initial_inventory = inventory.hexdigest()
                break
            _deadline_check(deadline)
            response = source.get_object(Bucket=bucket, Key=key)
            attributes = _attributes(source, bucket, key, response)
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as spool:
                digest, size = _hash_stream(response["Body"], deadline, spool)
                if size != expected_size:
                    raise ValueError("Source inventory changed during transfer")
                try:
                    head = destination.head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if not _missing(exc):
                        raise
                    spool.seek(0)
                    destination.upload_fileobj(
                        spool, bucket, key, ExtraArgs=attributes,
                        Config=TransferConfig(multipart_threshold=8 * 1024 * 1024,
                                              multipart_chunksize=8 * 1024 * 1024,
                                              max_concurrency=1, use_threads=False),
                    )
                    head = destination.head_object(Bucket=bucket, Key=key)
                target_attributes = _attributes(destination, bucket, key, head)
                copied_digest, copied_size = _hash_stream(
                    destination.get_object(Bucket=bucket, Key=key)["Body"], deadline,
                )
                if (digest, size, attributes) != (copied_digest, copied_size, target_attributes):
                    raise ValueError("Destination content/metadata mismatch; cutover refused, source retained")
            db.execute("INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?)", (bucket, key, digest, size, run))
            db.commit()
            count += 1
            total += size
        current_buckets = selected_buckets if selected_buckets is not None else sorted(row["Name"] for row in source.list_buckets()["Buckets"])
        final_inventory = None
        for _, _, _, inventory in _inventory(source, current_buckets, deadline):
            final_inventory = inventory.hexdigest()
        if buckets != current_buckets or initial_inventory != final_inventory:
            raise ValueError("Source inventory changed; cutover refused")
        for bucket in buckets:
            if source.list_multipart_uploads(Bucket=bucket, MaxUploads=1).get("Uploads"):
                raise ValueError("Concurrent multipart upload prevents cutover")
        return {"verifiedObjects": count, "verifiedBytes": total,
                "inventorySha256": final_inventory, "buckets": buckets}


def qualify_store(client, bucket):
    """Probe writes, multipart, HEAD/range and HTTP presigning before admission."""
    from urllib.request import ProxyHandler, build_opener

    key, payload, upload = f".moonmind-qualification/{uuid4().hex}", b"MoonMind storage qualification", None
    failure = None
    try:
        client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="text/plain")
        if client.head_object(Bucket=bucket, Key=key)["ContentLength"] != len(payload):
            raise ValueError("Destination HEAD qualification returned an incorrect size")
        with closing(client.get_object(Bucket=bucket, Key=key, Range="bytes=1-4")["Body"]) as body:
            if body.read() != payload[1:5]:
                raise ValueError("Destination range qualification returned different bytes")
        upload = client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
        part = client.upload_part(Bucket=bucket, Key=key, UploadId=upload, PartNumber=1, Body=payload)
        client.complete_multipart_upload(Bucket=bucket, Key=key, UploadId=upload,
                                        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": part["ETag"]}]})
        upload = None
        url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60)
        with build_opener(ProxyHandler({})).open(url, timeout=15) as response:
            if response.read() != payload:
                raise ValueError("Presigned storage qualification returned different bytes")
        upload = client.create_multipart_upload(Bucket=bucket, Key=key)["UploadId"]
        client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
        upload = None
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if upload:
                client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            if failure is None:
                raise
            failure.add_note("Storage qualification cleanup also failed; original failure retained")


async def quiesce_clients(docker, project, *, exclude=()):
    """Stop real producers, including project one-offs, before fencing S3."""
    identifiers = (await docker("ps", "--all", "--quiet", "--filter",
                                f"label=com.docker.compose.project={project}")).split()
    rows = json.loads(await docker("inspect", *identifiers)) if identifiers else []
    if len(rows) != len(identifiers):
        raise ValueError("Storage writer inventory is incomplete")
    selected = []
    for row in rows:
        config = row["Config"]
        if config.get("Labels", {}).get("com.docker.compose.project") != project:
            raise ValueError("Storage writer inventory escaped deployment authority")
        env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
        if row["Id"] in exclude or not (
            "TEMPORAL_ARTIFACT_S3_ENDPOINT" in env or "TEMPORAL_WORKER_FLEET" in env
            or config.get("Labels", {}).get("com.docker.compose.service") == "api"
        ):
            continue
        if row["State"]["Running"] or row["State"].get("Restarting"):
            selected.append(row["Id"])
    if selected:
        await docker("stop", "--time", "60", *selected, timeout_seconds=120)
        observed = json.loads(await docker("inspect", *selected))
        if len(observed) != len(selected) or any(row["State"]["Running"] or row["State"].get("Restarting") for row in observed):
            raise ValueError("Storage writers are still running")
    return selected


async def verified_cutover(isolate, activate, state_path, *, binding=None, qualify=None):
    source, destination = await isolate()
    receipt = await asyncio.to_thread(
        transfer_buckets, source, destination, state_path,
        binding=binding, timeout_seconds=480,
    )
    if qualify is not None:
        await qualify(destination, receipt)
    await activate()
    return {"status": "prepared", **receipt}


def _write_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _data_mount(row):
    mounts = [mount for mount in row.get("Mounts", []) if mount["Destination"] == "/data"]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume":
        raise ValueError("Automatic storage cutover requires one named /data volume; source retained")
    return mounts[0]["Name"]


def _environment(row):
    return dict(item.split("=", 1) for item in row["Config"].get("Env", []) if "=" in item)


async def _service_row(docker, project):
    identifiers = (await docker("ps", "--all", "--quiet", "--filter",
                                f"label=com.docker.compose.project={project}", "--filter",
                                "label=com.docker.compose.service=minio")).split()
    if not identifiers:
        return None
    if len(identifiers) != 1:
        raise ValueError("Storage service identity is ambiguous")
    rows = json.loads(await docker("inspect", *identifiers))
    if len(rows) != 1:
        raise ValueError("Storage service observation is incomplete")
    return rows[0]


async def prepare_compose_storage(runner, docker, payload, directory, *, observe_only=False):
    """One controller-owned cutover, before any application recreation.

    The public source process is stopped as a final write fence. An isolated
    copy of that *same* server opens its original volume, without the public
    port or DNS aliases. Interrupted presigned requests therefore cannot race
    the final inventory. A rollback follows the identical procedure in reverse.
    """
    rendered = await runner._run_compose_command(
        ("docker", "compose", "config", "--format", "json"),
        requested_image=payload["moonmindImage"], max_stdout_chars=None,
    )
    if rendered["exitCode"]:
        raise ValueError("Cannot render storage cutover configuration")
    config = json.loads(rendered["stdout"])
    spec = config.get("services", {}).get("minio")
    if spec is None:
        return {"status": "skipped", "reason": "external storage deployment"}
    project = payload["target"]["project"]
    mounts = [m for m in spec.get("volumes", []) if m["target"] == "/data"]
    if len(mounts) != 1 or mounts[0].get("type") != "volume":
        raise ValueError("Storage /data must use a named volume for automatic cutover")
    logical_volume = mounts[0]["source"]
    destination_volume = config["volumes"][logical_volume]["name"]
    state_directory = Path(directory)
    active_path = state_directory / ("project-" + hashlib.sha256(project.encode()).hexdigest()[:24] + ".json")
    active = json.loads(active_path.read_text()) if active_path.exists() else {}
    if active and active["project"] != project:
        raise ValueError("Storage recovery project identity differs")
    original = await _service_row(docker, project)
    source_container_missing = original is None
    if observe_only and original is None:
        raise ValueError("Storage owner observation unavailable; no server was recreated")
    if original is None:
        # The current owner survives container cleanup. An old forward-copy
        # receipt cannot supersede writes made after a subsequent rollback.
        if active.get("volume") == destination_volume:
            retained_destination = (await docker("volume", "ls", "--quiet", "--filter", f"name=^{destination_volume}$")).split()
            if destination_volume not in retained_destination:
                raise ValueError("Verified destination volume is missing; source retained, no empty store created")
            result = await runner._run_compose_command(
                ("docker", "compose", "up", "-d", "--no-deps", "--pull", "never", "--no-build", "--wait", "minio"),
                requested_image=payload["moonmindImage"],
            )
            restored = await _service_row(docker, project)
            if result["exitCode"] or restored is None or _data_mount(restored) != destination_volume or not restored["State"]["Running"]:
                raise ValueError("Verified storage volume could not be restored after container cleanup")
            _write_private(active_path, {**active, "container": restored, "phase": "active"})
            return {**(active.get("receipt") or {"status": "skipped"}), "recoveredAfterContainerCleanup": True}
        original = active.get("container")
    if original is None:
        # A removed container without provenance is not an empty installation.
        retained = await docker("volume", "ls", "--quiet", "--filter",
                                f"label=com.docker.compose.project={project}", "--filter",
                                "label=com.docker.compose.volume=minio-data")
        if retained.strip() and destination_volume not in retained.split():
            for volume in retained.split():
                # Compose may create an unused declared volume on a fresh
                # install. Inspect only emptiness, read-only, without running
                # another storage server over the predecessor's disk format.
                probe = await docker(
                    "run", "--rm", "--pull", "never", "--network", "none", "--user", "0:0",
                    "--entrypoint", "python", "--mount", f"type=volume,source={volume},target=/data,readonly",
                    payload["moonmindImage"], "-c",
                    "import json,pathlib; print(json.dumps({'empty':not any(pathlib.Path('/data').iterdir())}))",
                )
                if json.loads(probe).get("empty") is not True:
                    raise ValueError("Retained source volume has no server; restore its recorded original service before cutover")
        return {"status": "skipped", "reason": "new storage installation"}
    source_volume = _data_mount(original)
    if observe_only and source_volume != destination_volume:
        raise ValueError("Storage owner observation differs from the selected data volume")
    if source_container_missing:
        retained_source = (await docker("volume", "ls", "--quiet", "--filter", f"name=^{source_volume}$")).split()
        if source_volume not in retained_source:
            raise ValueError("Recorded source volume is missing; no empty replacement was created")
    _write_private(active_path, {"project": project, "volume": source_volume, "container": original,
                                 "phase": "observed", "receipt": active.get("receipt", {}) if active.get("volume") == source_volume else {}})
    if source_volume == destination_volume:
        return {"status": "skipped", "reason": "storage volume already selected"}
    binding = {"project": project, "source": source_volume, "destination": destination_volume}
    key = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()[:24]
    directory = Path(directory) / key
    directory.mkdir(parents=True, exist_ok=True)
    # This private descriptor retains the source image, command, credentials
    # and mount provenance even after Compose removes the original container.
    descriptor = directory / "source.json"
    if descriptor.exists():
        recorded = json.loads(descriptor.read_text())
        if recorded["binding"] != binding:
            raise ValueError("Storage source receipt identity differs")
    else:
        _write_private(descriptor, {"binding": binding, "container": original})
    helper = json.loads(await docker("inspect", os.environ["HOSTNAME"]))[0]
    common_networks = sorted(set(helper["NetworkSettings"]["Networks"]) & set(original["NetworkSettings"]["Networks"]))
    if not common_networks:
        raise ValueError("Storage transfer helper has no deployment network")
    network = common_networks[0]
    temporary = []
    fence_path = directory / "fence.json"
    fence = json.loads(fence_path.read_text()) if fence_path.exists() else {}
    if fence and fence["binding"] != binding:
        raise ValueError("Storage fence identity differs")
    stopped = list(fence.get("stopped", []))
    activated = False
    copy_proof = None

    async def temporary_store(role, image, volume, env, entrypoint, command):
        name = f"moonmind-storage-{key}-{role}"
        identifiers = await docker("ps", "--all", "--quiet", "--filter", f"name=^/{name}$")
        if identifiers.strip():
            row = json.loads(await docker("inspect", identifiers.strip()))[0]
            if row["Config"].get("Labels", {}).get("moonmind.storage.transfer") != key or _data_mount(row) != volume:
                raise ValueError("Temporary storage identity differs")
            # Recreate only the disposable server, retaining its named data.
            # Qualification must use this attempt's staged image/settings,
            # rather than a process left by a prior latest resolution.
            await docker("stop", "--time", "30", name, timeout_seconds=60)
            await docker("rm", name)
        args = ["run", "-d", "--name", name, "--label", f"moonmind.storage.transfer={key}",
                "--network", network, "--mount", f"type=volume,source={volume},target=/data",
                "--env-file", "/dev/stdin"]
        if entrypoint:
            args += ["--entrypoint", entrypoint[0]]
        args += [image, *(entrypoint[1:] if entrypoint else []), *command]
        await docker(*args, input_bytes=("\n".join(f"{k}={v}" for k, v in env.items()) + "\n").encode())
        temporary.append(name)
        row = json.loads(await docker("inspect", name))[0]
        ip = row["NetworkSettings"]["Networks"][network]["IPAddress"]
        client = s3_client(f"http://{ip}:9000", env.get("MINIO_ROOT_USER", "minioadmin"), env.get("MINIO_ROOT_PASSWORD", "minioadmin"))
        for attempt in range(20):
            try:
                await asyncio.to_thread(client.list_buckets)
                return client, f"http://{ip}:9000"
            except Exception:
                if attempt == 19:
                    raise
                await asyncio.sleep(1)

    async def isolate():
        nonlocal stopped
        stopped = list(dict.fromkeys([*stopped, *await quiesce_clients(docker, project, exclude=(helper["Id"],))]))
        _write_private(fence_path, {"binding": binding, "stopped": stopped, "sourceContainer": original["Id"]})
        if not source_container_missing:
            await docker("stop", "--time", "60", original["Id"], timeout_seconds=120)
            observed = json.loads(await docker("inspect", original["Id"]))[0]
            if observed["State"]["Running"] or observed["State"].get("Restarting"):
                raise ValueError("Public source storage did not stop")
        source, _ = await temporary_store("source", original["Image"], source_volume, _environment(original),
                                         original["Config"].get("Entrypoint"), original["Config"].get("Cmd") or [])
        destination, _ = await temporary_store("destination", spec["image"], destination_volume,
                                              spec.get("environment", {}), spec.get("entrypoint"),
                                              spec.get("command") or ["server", "/data", "--console-address", ":9001"])
        return source, destination

    async def qualify(client, receipt):
        nonlocal copy_proof
        bucket = (receipt["buckets"] or ["moonmind-temporal-artifacts"])[0]
        if not receipt["buckets"]:
            await asyncio.to_thread(client.create_bucket, Bucket=bucket)
        await asyncio.to_thread(qualify_store, client, bucket)
        # Configured application credentials must retain their exact privilege;
        # never grant them root access as part of migration.
        for service in config["services"].values():
            env = service.get("environment", {})
            endpoint = env.get("TEMPORAL_ARTIFACT_S3_ENDPOINT", "")
            from urllib.parse import urlsplit
            if urlsplit(endpoint).hostname not in {"minio", "moonmind-temporal-artifacts-s3"}:
                continue
            application = s3_client(client.meta.endpoint_url, env.get("TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID", "minioadmin"),
                                    env.get("TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY", "minioadmin"))
            await asyncio.to_thread(qualify_store, application, env.get("TEMPORAL_ARTIFACT_S3_BUCKET", bucket))
        copy_proof = {"status": "prepared", **receipt, **binding, "sourceRetained": True}
        _write_private(directory / "copy-verified.json", copy_proof)

    async def activate():
        nonlocal activated
        destination_row = json.loads(await docker("inspect", temporary[-1]))[0]
        for name in temporary:
            await docker("stop", "--time", "30", name, timeout_seconds=60)
        # Set before the uncertain boundary. Failure here never restarts old
        # writers beside an unobserved new server.
        activated = True
        _write_private(active_path, {"project": project, "volume": destination_volume,
                                     "container": destination_row, "phase": "activating", "receipt": copy_proof})
        result = await runner._run_compose_command(
            ("docker", "compose", "up", "-d", "--no-deps", "--pull", "never", "--no-build", "--wait", "minio"),
            requested_image=payload["moonmindImage"],
        )
        if result["exitCode"]:
            raise ValueError("Storage activation failed; writers remain fenced, source volume retained")
        current = await _service_row(docker, project)
        if current is None or _data_mount(current) != destination_volume or not current["State"]["Running"]:
            raise ValueError("Storage activation could not be confirmed")
        _write_private(active_path, {"project": project, "volume": destination_volume,
                                     "container": current, "phase": "active", "receipt": copy_proof})

    async def resume_consumers(*, selected_release):
        # A Compose one-off may have already confirmed a publication effect.
        # Its entrypoint is never re-executed by storage cleanup. Durable work
        # retries through its existing owner, preserving the logical step.
        if not stopped:
            return
        identifiers = (await docker("ps", "--all", "--quiet", "--no-trunc", "--filter",
                                    f"label=com.docker.compose.project={project}")).split()
        recoverable = [identifier for identifier in stopped if identifier in identifiers]
        rows = json.loads(await docker("inspect", *recoverable)) if recoverable else []
        selected = set(payload["target"].get("services", [])) if selected_release else set()
        resume = []
        for row in rows:
            labels = row["Config"].get("Labels", {})
            if str(labels.get("com.docker.compose.oneoff", "false")).lower() == "true":
                continue
            if labels.get("com.docker.compose.service") not in selected:
                resume.append(row["Id"])
        if resume:
            await docker("start", *resume)

    try:
        receipt = await verified_cutover(isolate, activate, directory / "copy.sqlite", binding=binding, qualify=qualify)
        _write_private(directory / "receipt.json", {**receipt, **binding, "sourceRetained": True})
        await resume_consumers(selected_release=True)
        return {**receipt, **binding, "sourceRetained": True}
    finally:
        # Never delete volumes or copy state. Cleanup errors must not start
        # two servers over one source volume, or mask a confirmed activation.
        clean = True
        for name in temporary:
            try:
                await docker("stop", "--time", "30", name, timeout_seconds=60)
            except Exception:
                clean = False
        if not activated and clean and not source_container_missing:
            await docker("start", original["Id"])
            await resume_consumers(selected_release=False)
