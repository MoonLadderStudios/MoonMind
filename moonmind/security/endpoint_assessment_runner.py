"""One OpenSCAP evaluation, or one result import, per Container Job.

A captured root filesystem tar or Docker/OCI image archive is unpacked as the
unprivileged scanner user into private scratch and evaluated with
``OSCAP_PROBE_ROOT``; no daemon, network, privilege or remediation is used.
Unpacking cannot keep file ownership, and an offline copy has no processes,
kernel or services, so rules whose checks read either are classified from the
shipped content and reported as not assessed instead of pass or fail.

A live host is evaluated in place over SSH through the admitted native
gateway, with the pinned host key as the only known host and the delivered
host-inspection key as the only identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import tarfile
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from moonmind.schemas.repository_scanner_models import EndpointAssessment
from moonmind.security.endpoint_assessments import (
    CONTENT_DIRECTORY,
    INPUT_FILES,
    INPUT_NAME,
    TAILORING_FILE,
    TAILORING_INPUT,
    XCCDF,
    EndpointExecution,
    EndpointOs,
    EndpointResultsError,
    ExtractionRecord,
    parse_results,
    sanitize_results,
)
from moonmind.security.repository_scanner_runner import BoundedProcesses

_OVAL = "http://oval.mitre.org/XMLSchema/oval-definitions-5"
#: OVAL probes that read live system state rather than files.
RUNTIME_PROBES = frozenset(
    {
        "apparmorstatus",
        "environmentvariable",
        "environmentvariable58",
        "fwupdsecattr",
        "iflisteners",
        "inetlisteningservers",
        "interface",
        "ldap",
        "ldap57",
        "partition",
        "process",
        "process58",
        "routingtable",
        "runlevel",
        "selinuxboolean",
        "sql",
        "sql57",
        "sysctl",
        "systemdunitdependency",
        "systemdunitproperty",
        "uname",
    }
)
#: Probes that compare file ownership, modes or labels.
METADATA_PROBES = frozenset(
    {
        "fileextendedattribute",
        "rpmverify",
        "rpmverifyfile",
        "rpmverifypackage",
        "selinuxsecuritycontext",
    }
)
METADATA_FIELDS = frozenset(
    {
        "user_id",
        "group_id",
        "uread",
        "uwrite",
        "uexec",
        "gread",
        "gwrite",
        "gexec",
        "oread",
        "owrite",
        "oexec",
        "suid",
        "sgid",
        "sticky",
        "has_extended_acl",
    }
)
#: Paths that exist only on a running system.
_SYSTEM_PATH = re.compile(r"^\^?/(?:proc|sys|run|dev)(?:/|\\/|$)")
_MAX_RAW_BYTES = 512 * 1024 * 1024
_MAX_MEMBERS = 500_000
_MAX_EXTRACTED_BYTES = 16 * 1024**3


class InputError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _local(tag) -> tuple[str, str]:
    if not isinstance(tag, str) or not tag.startswith("{"):
        return "", tag if isinstance(tag, str) else ""
    namespace, _, local = tag[1:].partition("}")
    return namespace, local


def classify_offline_rules(datastream: Path) -> dict[str, str]:
    """Rules whose OVAL checks read live state (``runtime``) or file metadata.

    Walks each rule's definitions through extended definitions, tests,
    objects, filter and state references and variables, so an indirect
    dependency is found as well as a direct one.
    """
    rules, edges, own = {}, {}, {}

    def ids(element, attribute):
        return {
            value
            for node in element.iter()
            if (value := node.get(attribute)) and node is not element
        }

    for _, element in ET.iterparse(datastream, events=("end",)):
        namespace, local = _local(element.tag)
        if namespace == XCCDF and local == "Rule" and element.get("id"):
            rules[element.get("id")] = {
                ("def", ref.get("name"))
                for ref in element.iter(f"{{{XCCDF}}}check-content-ref")
                if ref.get("name")
            }
            element.clear()
        elif namespace == _OVAL and local == "definition" and element.get("id"):
            edges[("def", element.get("id"))] = {
                ("test", ref) for ref in ids(element, "test_ref")
            } | {("def", ref) for ref in ids(element, "definition_ref")}
            element.clear()
        elif namespace == _OVAL and local.endswith("_variable") and element.get("id"):
            edges[("var", element.get("id"))] = {
                ("obj", ref) for ref in ids(element, "object_ref")
            } | {("var", ref) for ref in ids(element, "var_ref")}
            element.clear()
        elif namespace.startswith(_OVAL + "#") and element.get("id"):
            key = element.get("id")
            if local.endswith("_test"):
                probe = local.removesuffix("_test")
                node = ("test", key)
                edges[node] = {("obj", ref) for ref in ids(element, "object_ref")} | {
                    ("state", ref) for ref in ids(element, "state_ref")
                }
            elif local.endswith("_object"):
                probe = local.removesuffix("_object")
                node = ("obj", key)
                children = list(element.iter())
                edges[node] = (
                    {
                        ("state", (child.text or "").strip())
                        for child in children
                        if _local(child.tag)[1] == "filter"
                    }
                    | {
                        ("obj", (child.text or "").strip())
                        for child in children
                        if _local(child.tag)[1] == "object_reference"
                    }
                    | {("var", ref) for ref in ids(element, "var_ref")}
                )
                if any(
                    _local(child.tag)[1] in {"filepath", "path"}
                    and _SYSTEM_PATH.match((child.text or "").strip())
                    for child in children
                ):
                    own.setdefault(node, set()).add("runtime")
            elif local.endswith("_state"):
                probe = local.removesuffix("_state")
                node = ("state", key)
                edges[node] = {("var", ref) for ref in ids(element, "var_ref")}
                if probe in {"file", "fileextendedattribute"} and any(
                    _local(child.tag)[1] in METADATA_FIELDS for child in element
                ):
                    own.setdefault(node, set()).add("metadata")
            else:
                continue
            if probe in RUNTIME_PROBES:
                own.setdefault(node, set()).add("runtime")
            elif probe in METADATA_PROBES:
                own.setdefault(node, set()).add("metadata")
            element.clear()

    # Spread each need from the nodes that have it to everything that reaches
    # them. Shared and deep dependencies are then complete for every parent,
    # whatever order the content lists them in.
    parents: dict[tuple, set] = {}
    for node, children in edges.items():
        for child in children:
            parents.setdefault(child, set()).add(node)
    reached: dict[tuple, set] = {}
    for kind in ("runtime", "metadata"):
        stack = [node for node, kinds in own.items() if kind in kinds]
        seen = set(stack)
        while stack:
            node = stack.pop()
            reached.setdefault(node, set()).add(kind)
            for parent in parents.get(node, ()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)

    result = {}
    for rule_id, definitions in rules.items():
        found = set()
        for definition in definitions:
            found |= reached.get(definition, set())
        if "runtime" in found:
            result[rule_id] = "runtime"
        elif "metadata" in found:
            result[rule_id] = "metadata"
    return result


def _regular(path: Path, limit: int, code: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise InputError(code) from exc
    if not stat.S_ISREG(info.st_mode):
        raise InputError(code)
    if info.st_size > limit:
        raise InputError("input_bound_exceeded")
    return path


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _inside(root: Path, path: str) -> bool:
    resolved = os.path.realpath(path)
    return os.path.commonpath([resolved, str(root)]) == str(root)


class _Unpacker:
    """Unpack tar members into ``root`` without ownership, devices or escapes."""

    def __init__(self, root: Path, limit: int):
        self.root = Path(os.path.realpath(root))
        self.limit = limit
        self.used = 0
        self.record = ExtractionRecord(members=0)
        self.layer: set[str] = set()

    def _name(self, member: tarfile.TarInfo) -> str | None:
        name = os.path.normpath(member.name.lstrip("/")).removeprefix("./")
        if name in {"", "."} or name.startswith("../") or name == "..":
            return None
        return name

    def _remove(self, relative: str) -> None:
        path = self.root / relative
        if not _inside(self.root, str(path.parent)):
            return
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _hide_lower(self, parent: Path) -> None:
        """Remove lower-layer entries beneath ``parent``; keep this layer's own.

        The layer's entries may precede its opaque marker, with or without
        their directories, so a directory stays while it holds one of them.
        """
        holding = set()
        for current, directories, files in os.walk(parent, topdown=False):
            here = Path(current)
            for name in directories + files:
                child = here / name
                relative = child.relative_to(self.root).as_posix()
                if relative in self.layer or relative in holding:
                    holding.add(here.relative_to(self.root).as_posix())
                elif child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()

    def whiteout(self, member: tarfile.TarInfo) -> bool:
        """Apply an image-layer whiteout to lower layers; True when it was one."""
        name = self._name(member)
        if name is None:
            return False
        directory, _, base = name.rpartition("/")
        if base == ".wh..wh..opq":
            parent = self.root / directory
            if (
                _inside(self.root, str(parent))
                and parent.is_dir()
                and not parent.is_symlink()
            ):
                self._hide_lower(parent)
            return True
        if base.startswith(".wh."):
            target = f"{directory}/{base[4:]}".lstrip("/")
            if target not in self.layer:
                self._remove(target)
            return True
        return False

    def filter(self, member: tarfile.TarInfo, _dest: str):
        if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
            self.record.special_files_skipped += 1
            return None
        if os.path.normpath(member.name.lstrip("/")) == ".":
            return None  # The archive's own root directory.
        name = self._name(member)
        if name is None:
            self.record.unsafe_members_refused += 1
            return None
        try:
            member = tarfile.tar_filter(member.replace(name=name), str(self.root))
        except tarfile.FilterError:
            self.record.unsafe_members_refused += 1
            return None
        if member.islnk():
            link = os.path.normpath(member.linkname.lstrip("/"))
            if link.startswith("..") or not _inside(self.root, str(self.root / link)):
                self.record.unsafe_members_refused += 1
                return None
            member = member.replace(linkname=link)
        existing = self.root / name
        if existing.is_symlink() or (existing.exists() and not existing.is_dir()):
            existing.unlink()
        elif existing.is_dir() and not member.isdir():
            shutil.rmtree(existing)
        if member.isreg():
            self.used += member.size
            if self.used > self.limit:
                raise InputError("input_bound_exceeded")
        self.record.members += 1
        if self.record.members > _MAX_MEMBERS:
            raise InputError("input_bound_exceeded")
        self.layer.add(name)
        # Owner access only; ownership itself is never applied without root.
        mode = member.mode or 0
        if member.isdir():
            mode |= 0o700
        elif member.isreg() or member.islnk():
            mode |= 0o600
        return member.replace(mode=mode, uid=os.getuid(), gid=os.getgid())

    def unpack(self, bundle: tarfile.TarFile, *, layer: bool = False) -> None:
        self.layer = set()
        for member in bundle:
            if layer and self.whiteout(member):
                continue
            try:
                bundle.extract(member, self.root, filter=self.filter)
            except (OSError, KeyError, tarfile.ExtractError) as exc:
                if isinstance(exc, InputError):
                    raise
                # A link to a missing target, or a member the unprivileged
                # user cannot write, is recorded rather than guessed.
                self.record.members_not_extracted += 1


def _blob(bundle: tarfile.TarFile, members: dict, name: str, limit: int = 1 << 20):
    member = members.get(name)
    if member is None or not member.isreg() or member.size > limit:
        raise InputError("image_archive_malformed")
    return bundle.extractfile(member).read()


def image_layers(bundle: tarfile.TarFile) -> tuple[dict, list[str]]:
    """Members and ordered layer names of a single-image Docker or OCI archive."""
    members = {}
    for count, member in enumerate(bundle):
        if count >= 100_000:
            raise InputError("image_archive_malformed")
        members[member.name.removeprefix("./")] = member
    try:
        if "manifest.json" in members:
            images = json.loads(_blob(bundle, members, "manifest.json"))
            if not isinstance(images, list) or len(images) != 1:
                raise InputError("image_archive_not_single_image")
            layers = images[0].get("Layers")
        elif {"index.json", "oci-layout"} <= set(members):
            index = json.loads(_blob(bundle, members, "index.json"))
            manifests = index.get("manifests") or []
            if len(manifests) != 1:
                raise InputError("image_archive_not_single_image")
            digest = manifests[0].get("digest") or ""
            manifest = json.loads(
                _blob(bundle, members, "blobs/sha256/" + digest.removeprefix("sha256:"))
            )
            layers = [
                "blobs/sha256/" + str(layer.get("digest", "")).removeprefix("sha256:")
                for layer in manifest.get("layers") or []
            ]
        else:
            raise InputError("image_archive_malformed")
    except (ValueError, AttributeError, TypeError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError("image_archive_malformed") from exc
    if (
        not isinstance(layers, list)
        or not layers
        or not all(isinstance(name, str) and name in members for name in layers)
    ):
        raise InputError("image_archive_malformed")
    return members, layers


def unpack_input(kind: str, archive: Path, root: Path, limit: int) -> ExtractionRecord:
    unpacker = _Unpacker(root, limit)
    try:
        with tarfile.open(archive, "r:*") as bundle:
            if kind == "rootfs":
                unpacker.unpack(bundle)
            else:
                members, layers = image_layers(bundle)
                for name in layers:
                    with bundle.extractfile(members[name]) as stream, tarfile.open(
                        fileobj=stream, mode="r|*"
                    ) as layer:
                        unpacker.unpack(layer, layer=True)
                    unpacker.record.layers += 1
    except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(
            "image_archive_malformed" if kind == "image" else "rootfs_archive_malformed"
        ) from exc
    return unpacker.record


def read_os_release(root: Path) -> EndpointOs | None:
    """``ID``, ``VERSION_ID`` and ``PRETTY_NAME`` as the copy states them."""
    root = Path(os.path.realpath(root))
    for relative in ("etc/os-release", "usr/lib/os-release"):
        path = root / relative
        for _ in range(8):
            if not path.is_symlink():
                break
            target = os.readlink(path)
            path = (
                root / target.lstrip("/")
                if target.startswith("/")
                else path.parent / target
            )
        if not _inside(root, str(path)) or not path.is_file():
            continue
        values = {}
        for line in path.read_bytes()[:65536].decode("utf-8", "replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key in {"ID", "VERSION_ID", "PRETTY_NAME"}:
                value = value.strip().strip("\"'")[:256]
                if value:
                    values[key] = value
        if values:
            return EndpointOs(
                id=values.get("ID"),
                versionId=values.get("VERSION_ID"),
                prettyName=values.get("PRETTY_NAME"),
            )
    return None


def _content(content: Path, name: str) -> tuple[Path, str, str | None]:
    """A shipped datastream, checked against the image's content manifest."""
    manifest = json.loads((content / "manifest.json").read_text())
    expected = (manifest.get("datastreams") or {}).get(name)
    path = _regular(content / name, _MAX_RAW_BYTES, "content_unavailable")
    digest = _file_digest(path)
    if expected != digest:
        raise InputError("content_digest_mismatch")
    release = manifest.get("release")
    return path, digest, release if isinstance(release, str) else None


def oscap_command(
    assessment: EndpointAssessment, *, datastream: Path, raw: Path, tailoring=None
) -> list[str]:
    """Only evaluation flags: no remediation, fix generation or remote fetch."""
    argv = ["oscap", "xccdf", "eval", "--profile", assessment.profile]
    if tailoring is not None:
        argv += ["--tailoring-file", str(tailoring)]
    return [*argv, "--results-arf", str(raw / "arf.xml"), str(datastream)]


def run_endpoint_assessment(
    assessment: EndpointAssessment,
    *,
    inputs: Path = Path("/inputs"),
    output: Path,
    content: Path = Path(CONTENT_DIRECTORY),
) -> int:
    output.mkdir(parents=True, exist_ok=True)
    processes = BoundedProcesses(assessment.timeout_seconds, _MAX_RAW_BYTES)
    previous = signal.signal(signal.SIGTERM, processes.cancel)
    evaluate = assessment.target != "results"
    execution = EndpointExecution(
        target=assessment.target,
        version="unavailable" if evaluate else "not_run",
        state="failed",
    )
    limitations: list[str] = []
    with tempfile.TemporaryDirectory(prefix="moonmind-endpoint-") as scratch:
        work = Path(scratch)
        raw, root = work / "raw", work / "root"
        raw.mkdir()
        root.mkdir()
        try:
            source = _regular(
                inputs / INPUT_NAME / INPUT_FILES[assessment.target],
                assessment.max_input_bytes,
                "endpoint_input_unavailable",
            )
            execution.input_digest = _file_digest(source)
            if not evaluate:
                body = _regular(
                    source, _MAX_RAW_BYTES, "endpoint_input_unavailable"
                ).read_bytes()
                execution.state = "succeeded"
                limitations.extend(_retain(assessment, body, output, keep_target=True))
            else:
                datastream, digest, release = _content(content, assessment.datastream)
                execution.content_file = assessment.datastream
                execution.content_digest = digest
                execution.content_release = release
                tailoring = None
                if assessment.tailoring_artifact_ref:
                    tailoring = _regular(
                        inputs / TAILORING_INPUT / TAILORING_FILE,
                        8 * 1024 * 1024,
                        "tailoring_unavailable",
                    )
                    execution.tailoring_digest = _file_digest(tailoring)
                execution.extraction = unpack_input(
                    assessment.target,
                    source,
                    root,
                    min(4 * assessment.max_input_bytes, _MAX_EXTRACTED_BYTES),
                )
                if execution.extraction.unsafe_members_refused:
                    limitations.append("input_members_refused")
                if execution.extraction.members_not_extracted:
                    limitations.append("input_members_not_extracted")
                execution.os_release = read_os_release(root)
                offline = classify_offline_rules(datastream)
                env = {
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(work),
                    "TMPDIR": str(work),
                    "OSCAP_PROBE_ROOT": str(root),
                }
                version = work / "version"
                processes.run(
                    ["oscap", "--version"], cwd=work, env=env, capture=version
                )
                if version.exists():
                    match = re.search(
                        rb"\b[0-9]+\.[0-9]+\.[0-9]+\b", version.read_bytes()[:4096]
                    )
                    if match:
                        execution.version = match[0].decode()
                if execution.version == "unavailable":
                    limitations.append("scanner_version_unavailable")
                execution.exit_code, execution.state = processes.run(
                    oscap_command(
                        assessment, datastream=datastream, raw=raw, tailoring=tailoring
                    ),
                    cwd=work,
                    env=env,
                )
                # 0: every rule passed; 2: at least one failed; else an error.
                if execution.state != "succeeded" or execution.exit_code not in (0, 2):
                    limitations.append("scanner_exit_failure")
                arf = raw / "arf.xml"
                if not arf.is_file():
                    limitations.append("missing_malformed_or_truncated_results")
                else:
                    limitations.extend(
                        _retain(assessment, arf.read_bytes(), output, keep_target=False)
                    )
                    try:
                        present = {
                            rule.rule_id
                            for rule in parse_results(
                                (output / "results.xml").read_bytes(),
                                assessment.max_report_bytes,
                            ).rules
                        }
                    except (EndpointResultsError, OSError):
                        present = set()
                    execution.offline_rules = {
                        rule: kind for rule, kind in offline.items() if rule in present
                    }
        except InputError as exc:
            limitations.extend([exc.code, "scanner_prerequisite_failed"])
        except Exception:  # noqa: BLE001
            limitations.append("scanner_prerequisite_failed")
        finally:
            signal.signal(signal.SIGTERM, previous)
            if execution.state == "succeeded" and processes.canceled:
                execution.state = "canceled"
            execution.limitations = sorted(set(limitations))
            (output / "execution.json").write_text(
                execution.model_dump_json(by_alias=True)
            )
    ok = execution.state == "succeeded" and not execution.limitations
    print(
        "Endpoint assessment finished; checked evidence is ready."
        if ok
        else "Endpoint assessment incomplete; inspect retained limitations."
    )
    return 0 if ok else 2


# --- Live host ---------------------------------------------------------------

#: The delivered host-inspection credential: an OpenSSH private key file.
HOST_CREDENTIAL = Path("/run/moonmind-credentials/host")
#: The one known_hosts name; SSH checks the host only against this entry.
HOST_ALIAS = "moonmind-endpoint"
_REMOTE_DIRECTORY = re.compile(r"/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,8}")
_CLEANUP_SECONDS = 10


def _private_copy(source: Path, destination: Path) -> Path:
    """Copy the key into private scratch; SSH refuses group-readable keys."""
    try:
        body = _regular(source, 64 * 1024, "host_credential_unavailable").read_bytes()
    except OSError as exc:
        raise InputError("host_credential_unavailable") from exc
    if not body.strip():
        raise InputError("host_credential_unavailable")
    fd = os.open(
        destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(body if body.endswith(b"\n") else body + b"\n")
    return destination


def ssh_command(
    assessment: EndpointAssessment, *, known_hosts: Path, identity: Path, remote: str
) -> list[str]:
    """One non-interactive SSH session to the admitted host, through the gateway.

    The pinned key is the only known host and the only accepted algorithm, so
    an unknown or changed host stops in key exchange, before authentication
    uses the credential. No agent, forwarding, TTY, local command or
    multiplexing is enabled; the identity is a file path, never a value.
    """
    host = assessment.host
    key_type = host.host_key.split()[0]
    options = {
        "BatchMode": "yes",
        "StrictHostKeyChecking": "yes",
        "UserKnownHostsFile": str(known_hosts),
        "GlobalKnownHostsFile": "/dev/null",
        "HostKeyAlias": HOST_ALIAS,
        "HostKeyAlgorithms": (
            "rsa-sha2-512,rsa-sha2-256" if key_type == "ssh-rsa" else key_type
        ),
        "CheckHostIP": "no",
        "UpdateHostKeys": "no",
        "VerifyHostKeyDNS": "no",
        "IdentitiesOnly": "yes",
        "IdentityFile": str(identity),
        "IdentityAgent": "none",
        "PreferredAuthentications": "publickey",
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "ForwardAgent": "no",
        "ForwardX11": "no",
        "ClearAllForwardings": "yes",
        "PermitLocalCommand": "no",
        "ControlMaster": "no",
        "ControlPath": "none",
        "RequestTTY": "no",
        "ConnectTimeout": "20",
        # Keepalives keep a long evaluation inside the gateway's idle limit.
        "ServerAliveInterval": "10",
        "ServerAliveCountMax": "3",
        "ProxyCommand": (
            f"{sys.executable} -I -m moonmind.security.endpoint_host_tunnel %h %p"
        ),
        "LogLevel": "ERROR",
    }
    argv = ["ssh", "-F", "/dev/null", "-T", "-p", str(host.port), "-l", host.account]
    for name, value in options.items():
        argv += ["-o", f"{name}={value}"]
    return [*argv, "--", host.address, remote]


def remote_commands(assessment: EndpointAssessment, directory: str) -> dict[str, str]:
    """The only remote commands: fixed text plus pattern-checked identifiers.

    The datastream and results stay in a private temporary directory that is
    removed afterwards. ``sudo -n`` is used only when the host's admission
    names elevated evaluation, and never prompts.
    """
    if not _REMOTE_DIRECTORY.fullmatch(directory) or ".." in directory.split("/"):
        raise InputError("remote_preparation_failed")
    sudo = "sudo -n " if assessment.host.elevated else ""
    skipped = "".join(f" --skip-rule {rule}" for rule in assessment.skip_rule_ids)
    return {
        "upload": f"cat > {directory}/content.xml",
        "evaluate": (
            f"{sudo}oscap xccdf eval --profile {assessment.profile}{skipped} "
            f"--results-arf {directory}/results.xml {directory}/content.xml"
            " >/dev/null"
        ),
        "fetch": f"{sudo}cat -- {directory}/results.xml",
        "cleanup": f"{sudo}rm -rf -- {directory}",
    }


#: Creates the private directory and reports OpenSCAP's version.
PREPARE = "umask 077 && d=$(mktemp -d) && printf '%s\\n' \"$d\" && oscap --version"


def ssh_failure(stderr: bytes) -> str:
    """A bounded code for an SSH failure; the text itself is never retained."""
    from moonmind.security.endpoint_host_tunnel import DIAGNOSTIC

    text = stderr[:65536].decode("utf-8", "replace")
    if DIAGNOSTIC in text:
        outcome = text.split(DIAGNOSTIC, 1)[1].split()[0:1]
        return {
            "timed_out": "host_connect_timed_out",
            "denied": "host_connection_denied",
        }.get(outcome[0] if outcome else "", "host_unreachable")
    if (
        "Host key verification failed" in text
        or "IDENTIFICATION HAS CHANGED" in text
        or "host key is known" in text
        or "no matching host key type" in text
    ):
        return "host_identity_unverified"
    if "Permission denied" in text or "Too many authentication failures" in text:
        return "host_authentication_failed"
    if "Load key" in text or "invalid format" in text:
        return "host_credential_invalid"
    if "Connection timed out" in text:
        return "host_connect_timed_out"
    if "Connection refused" in text or "Connection closed" in text:
        return "host_unreachable"
    return "host_connection_failed"


class _HostStop(Exception):
    """A live step failed; its code is already recorded."""


def run_live_host_assessment(
    assessment: EndpointAssessment,
    *,
    output: Path,
    content: Path = Path(CONTENT_DIRECTORY),
    credential: Path = HOST_CREDENTIAL,
    environ=None,
) -> int:
    """Evaluate the admitted host in place and retain only its rebuilt result.

    Each step is one SSH session; any failure stops the run with a bounded
    code. The remote directory is removed even after a timeout or stop, and
    the local key copy is removed with the private scratch.
    """
    environ = os.environ if environ is None else environ
    output.mkdir(parents=True, exist_ok=True)
    processes = BoundedProcesses(assessment.timeout_seconds, _MAX_RAW_BYTES)
    previous = signal.signal(signal.SIGTERM, processes.cancel)
    execution = EndpointExecution(target="host", version="unavailable", state="failed")
    limitations: list[str] = []
    remote = None
    with tempfile.TemporaryDirectory(prefix="moonmind-endpoint-host-") as scratch:
        work = Path(scratch)
        os.chmod(work, 0o700)
        raw = work / "raw"
        raw.mkdir(mode=0o700)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(work),
            "TMPDIR": str(work),
            "SHELL": "/bin/sh",
            **{
                name: environ[name]
                for name in ("MOONMIND_EGRESS_PROXY", "MOONMIND_EGRESS_DEADLINE")
                if name in environ
            },
        }

        def session(
            command, *, runner=processes, stdin=None, capture=None, record=True
        ):
            errors = work / "stderr"
            code, state = runner.run(
                ssh_command(
                    assessment,
                    known_hosts=work / "known_hosts",
                    identity=work / "identity",
                    remote=command,
                ),
                cwd=work,
                env=env,
                stdin=stdin,
                capture=capture,
                errors=errors,
            )
            stderr = errors.read_bytes()[:65536] if errors.exists() else b""
            errors.unlink(missing_ok=True)
            if state != "succeeded" or code == 255:
                if record and state != "succeeded":
                    execution.state = state
                elif record:
                    limitations.append(ssh_failure(stderr))
                raise _HostStop
            return code, stderr

        try:
            datastream, digest, release = _content(content, assessment.datastream)
            execution.content_file = assessment.datastream
            execution.content_digest = digest
            execution.content_release = release
            (work / "known_hosts").write_text(
                f"{HOST_ALIAS} {assessment.host.host_key}\n"
            )
            _private_copy(credential, work / "identity")
            prepared = raw / "prepare"
            code, _ = session(PREPARE, capture=prepared)
            lines = (
                prepared.read_bytes()[:4096].decode("utf-8", "replace").splitlines()
                if prepared.exists()
                else []
            )
            if lines and _REMOTE_DIRECTORY.fullmatch(lines[0]):
                remote = lines[0]
            if code == 127 or (remote and code):
                limitations.append("remote_scanner_unavailable")
                raise _HostStop
            if code or remote is None:
                limitations.append("remote_preparation_failed")
                raise _HostStop
            match = re.search(r"\b[0-9]+\.[0-9]+\.[0-9]+\b", "\n".join(lines[1:]))
            if match:
                execution.version = match[0]
            else:
                limitations.append("scanner_version_unavailable")
            commands = remote_commands(assessment, remote)
            code, _ = session(commands["upload"], stdin=datastream)
            if code:
                limitations.append("remote_upload_failed")
                raise _HostStop
            code, stderr = session(commands["evaluate"])
            execution.exit_code = code
            # 0: every rule passed; 2: at least one failed; else an error.
            if code not in (0, 2):
                limitations.append(
                    "elevation_refused"
                    if assessment.host.elevated and b"sudo:" in stderr
                    else "scanner_exit_failure"
                )
                raise _HostStop
            arf = raw / "arf.xml"
            code, _ = session(commands["fetch"], capture=arf)
            if code or not arf.is_file():
                limitations.append("missing_malformed_or_truncated_results")
                raise _HostStop
            execution.state = "succeeded"
            limitations.extend(
                _retain(assessment, arf.read_bytes(), output, keep_target=False)
            )
        except InputError as exc:
            limitations.extend([exc.code, "scanner_prerequisite_failed"])
        except _HostStop:
            pass
        except Exception:  # noqa: BLE001
            limitations.append("scanner_prerequisite_failed")
        finally:
            signal.signal(signal.SIGTERM, previous)
            if remote is not None:
                # A short session of its own: a stop or elapsed budget still
                # removes what this job placed on the host.
                cleanup = BoundedProcesses(_CLEANUP_SECONDS, 1 << 20)
                try:
                    code, _ = session(
                        remote_commands(assessment, remote)["cleanup"],
                        runner=cleanup,
                        record=False,
                    )
                    if code:
                        limitations.append("remote_cleanup_unconfirmed")
                except (_HostStop, InputError):
                    limitations.append("remote_cleanup_unconfirmed")
            if processes.canceled and execution.state in ("succeeded", "failed"):
                execution.state = "canceled"
            execution.limitations = sorted(set(limitations))
            (output / "execution.json").write_text(
                execution.model_dump_json(by_alias=True)
            )
    ok = execution.state == "succeeded" and not execution.limitations
    print(
        "Endpoint assessment finished; checked evidence is ready."
        if ok
        else "Endpoint assessment incomplete; inspect retained limitations."
    )
    return 0 if ok else 2


def _retain(assessment, body: bytes, output: Path, *, keep_target: bool) -> list[str]:
    """Rebuild the allow-listed ``TestResult``; nothing else leaves the job."""
    try:
        safe = sanitize_results(body, _MAX_RAW_BYTES, keep_target=keep_target)
    except EndpointResultsError as exc:
        return [exc.code]
    if len(safe) > assessment.max_report_bytes:
        return ["results_exceed_bound"]
    (output / "results.xml").write_bytes(safe)
    return []


def readiness(
    assessment: EndpointAssessment, *, content: Path = Path(CONTENT_DIRECTORY)
) -> int:
    """OpenSCAP runs and, for an evaluation, the selected datastream is intact."""
    processes = BoundedProcesses(60, 1 << 20)
    with tempfile.TemporaryDirectory(prefix="moonmind-endpoint-ready-") as scratch:
        work = Path(scratch)
        version = work / "version"
        code, state = processes.run(
            ["oscap", "--version"],
            cwd=work,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": scratch},
            capture=version,
        )
        match = (
            re.search(rb"\b[0-9]+\.[0-9]+\.[0-9]+\b", version.read_bytes()[:4096])
            if version.exists()
            else None
        )
    if code != 0 or state != "succeeded" or match is None:
        print("OpenSCAP is unavailable.")
        return 2
    if assessment.target != "results":
        try:
            _content(content, assessment.datastream)
        except (InputError, OSError, ValueError):
            print("The selected ComplianceAsCode datastream is unavailable.")
            return 2
    if assessment.target == "host" and shutil.which("ssh") is None:
        print("The SSH client for live host evaluation is unavailable.")
        return 2
    print(f"OpenSCAP {match[0].decode()} ready.")
    return 0


def main(argv: list[str]) -> int:
    try:
        if argv[1] == "--readiness":
            return readiness(EndpointAssessment.model_validate_json(argv[2]))
        assessment = EndpointAssessment.model_validate_json(argv[1])
        if assessment.target == "host":
            return run_live_host_assessment(assessment, output=Path(argv[2]))
        return run_endpoint_assessment(assessment, output=Path(argv[2]))
    # Never print raw exceptions: they can carry paths or input text.
    except Exception:  # noqa: BLE001
        print("Endpoint assessment invocation failed; evidence is incomplete.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
