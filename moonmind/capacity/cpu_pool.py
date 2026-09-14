"""One kernel-enforced, ledger-reserved CPU pool per Docker backend.

cpuMillis=0 selects fair sharing within this pool. Positive CPU limits preserve
their existing reservation and Docker quota. The pool itself reserves its whole
quota so retained workers cannot allocate that CPU a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from moonmind.capacity.machine_reservations import (
    MachineCapacityLedger,
    MachineResourceBudget,
    STATE_ACTIVE,
    STATE_PRELAUNCH,
    STATE_RELEASED,
    WORKLOAD_CLASS_OBSERVED,
    machine_reservation_id,
)


class CpuPoolUnavailable(RuntimeError):
    """Shared CPU has no currently enforceable allocation."""


class CpuPoolBusy(CpuPoolUnavailable):
    """Existing capacity must drain before this pool can be established."""


@dataclass(frozen=True)
class CpuPoolLaunch:
    parent: str
    holder: str
    cpu_millis: int

    @property
    def docker_args(self) -> list[str]:
        return ["--cgroup-parent", self.parent, "--cpu-shares", "1024"]


class DockerCpuPool:
    def __init__(
        self,
        *,
        runner: Any,
        ledger: MachineCapacityLedger,
        backend_ref: str,
        helper_image: str | None = None,
    ) -> None:
        self.runner = runner
        self.ledger = ledger
        self.backend_ref = backend_ref
        digest = hashlib.sha256(backend_ref.encode()).hexdigest()[:24]
        self.leaf = f"moonmindcpu{digest}.slice"
        self._helper_image = helper_image
        self._driver: str | None = None
        self.reservation_id = machine_reservation_id(
            backend_ref=backend_ref,
            owner_kind="cpu_pool",
            owner_ref=self.leaf,
            generation=1,
        )

    async def _checked(self, *args: str) -> str:
        code, out, err = await self.runner(tuple(args))
        if code:
            raise CpuPoolUnavailable(
                "CPU pool Docker operation failed: "
                + err.decode(errors="replace")[:300]
            )
        return out.decode().strip()

    async def _authority(self) -> tuple[str, str]:
        if self._driver is None:
            raw = await self._checked(
                "info",
                "--format",
                "{{.CgroupVersion}}\t{{.CgroupDriver}}\t{{json .SecurityOptions}}",
            )
            parts = raw.split("\t")
            if (
                len(parts) != 3
                or parts[0] != "2"
                or parts[1] not in {"cgroupfs", "systemd"}
            ):
                raise CpuPoolUnavailable(
                    "shared CPU requires a cgroup v2 Docker backend"
                )
            if "rootless" in parts[2]:
                raise CpuPoolUnavailable(
                    "shared CPU requires deployment-owned cgroup authority"
                )
            self._driver = parts[1]
        if self._helper_image is None:
            hostname = os.environ.get("HOSTNAME", "")
            if not hostname:
                raise CpuPoolUnavailable(
                    "resource helper needs the trusted worker image identity"
                )
            self._helper_image = await self._checked(
                "inspect", "--format", "{{.Image}}", hostname
            )
        if not re.fullmatch(
            r"(?:sha256:[0-9a-f]{64}|[a-z0-9][^\s@]*@sha256:[0-9a-f]{64})",
            self._helper_image,
        ):
            raise CpuPoolUnavailable(
                "resource helper image must have immutable authority"
            )
        parent = self.leaf if self._driver == "systemd" else "/" + self.leaf
        return self._helper_image, parent

    async def _helper(self, operation: str, cpu_millis: int | None = None) -> str:
        image, parent = await self._authority()
        name = "mm-resource-helper-" + uuid4().hex
        command = [
            "run",
            "--detach",
            "--name",
            name,
            "--user",
            "0:0",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cgroupns",
            "host",
            "--memory",
            "128m",
            "--pids-limit",
            "32",
            "--cpus",
            "0.1",
            "--label",
            "moonmind.resource-helper=true",
            "--mount",
            "type=bind,src=/sys/fs/cgroup,dst=/host-cgroup"
            + (",readonly" if operation == "observe" else ""),
        ]
        if operation == "configure":
            command += ["--cap-add", "SYS_ADMIN", "--rm", "--cgroup-parent", parent]
        source = Path(__file__).with_name("cgroup_helper.py").read_text()
        command += ["--entrypoint", "python", image, "-c", source, operation, self.leaf]
        if cpu_millis is not None:
            command.append(str(cpu_millis))
        try:
            await self._checked(*command)
        except BaseException:
            await self.runner(("rm", "--force", name))
            raise
        return name

    async def launch_args(self) -> list[str]:
        _, parent = await self._authority()
        return ["--cgroup-parent", parent, "--cpu-shares", "1024"]

    async def verify(self, lease: CpuPoolLaunch) -> int:
        from api_service.db.models import MachineCapacityReservation

        async with self.ledger._factory()() as session:
            row = await session.get(MachineCapacityReservation, self.reservation_id)
            if (
                row is None
                or row.state != STATE_ACTIVE
                or row.cpu_millis < lease.cpu_millis
            ):
                raise CpuPoolUnavailable("CPU pool admission no longer holds")
            held_cpu_millis = row.cpu_millis
        if (
            await self._checked(
                "inspect", "--format", "{{.State.Running}}", lease.holder
            )
            != "true"
        ):
            raise CpuPoolUnavailable("CPU pool launch holder expired")
        return held_cpu_millis

    async def prepare(self, budget: MachineResourceBudget) -> CpuPoolLaunch:
        from api_service.db.models import MachineCapacityReservation
        from moonmind.capacity.docker_inventory import probe_owned_containers

        _, parent = await self._authority()
        await self.reconcile()
        await self.ledger.reconcile(
            backend_ref=self.backend_ref,
            inventory=await probe_owned_containers(self.runner),
        )
        async with self.ledger._factory()() as session:
            await self.ledger.lock_for_admission(session)
            row = await session.get(MachineCapacityReservation, self.reservation_id)
            usage = await self.ledger.usage_within(
                session, backend_ref=self.backend_ref
            )
            own = (
                row.cpu_millis
                if row is not None and row.state in {STATE_ACTIVE, STATE_PRELAUNCH}
                else 0
            )
            available = budget.cpu_millis - usage.reserved_cpu_millis + own
            if usage.reconciliation_blocked or available < 100:
                raise CpuPoolBusy("waiting for shared CPU capacity")
            if row is None:
                row = MachineCapacityReservation(
                    reservation_id=self.reservation_id,
                    backend_ref=self.backend_ref,
                    workload_class=WORKLOAD_CLASS_OBSERVED,
                    owner_kind="cpu_pool",
                    owner_ref=self.leaf,
                    generation=1,
                    state=STATE_PRELAUNCH,
                )
                session.add(row)
            # The reservation commits before the kernel can consume more CPU.
            # An existing active quota is never reduced by accounting alone.
            if own and available < own:
                raise CpuPoolBusy("CPU pool needs reconciliation before admission")
            row.cpu_millis = available
            if row.state != STATE_ACTIVE:
                row.state = STATE_PRELAUNCH
                row.expires_at = datetime.now(UTC) + timedelta(seconds=300)
            await session.commit()
        holder = await self._helper("configure", available)
        import asyncio

        try:
            for _ in range(50):
                raw = await self._checked("logs", holder)
                if raw:
                    receipt = json.loads(raw)
                    if (
                        receipt.get("ready")
                        and receipt.get("cpuMax") == f"{available * 100} 100000"
                    ):
                        break
                    raise CpuPoolUnavailable(
                        "CPU pool returned invalid enforcement evidence"
                    )
                await asyncio.sleep(0.1)
            else:
                raise CpuPoolUnavailable("CPU pool setup did not become ready")
            async with self.ledger._factory()() as session:
                await self.ledger.lock_for_admission(session)
                row = await session.get(MachineCapacityReservation, self.reservation_id)
                if (
                    row is None
                    or row.cpu_millis != available
                    or row.state == STATE_RELEASED
                ):
                    raise CpuPoolUnavailable(
                        "CPU pool reservation changed during setup"
                    )
                row.state = STATE_ACTIVE
                row.expires_at = None
                await session.commit()
            return CpuPoolLaunch(parent, holder, available)
        except BaseException:
            await self.runner(("rm", "--force", holder))
            raise

    async def finish_launch(self, lease: CpuPoolLaunch, container: str) -> None:
        await self.verify(lease)
        actual = await self._checked(
            "inspect",
            "--format",
            "{{.HostConfig.CgroupParent}}\t{{.State.Status}}\t{{.State.StartedAt}}",
            container,
        )
        parts = actual.split("\t")
        if (
            len(parts) != 3
            or parts[0] != lease.parent
            or parts[1] not in {"running", "exited"}
            or parts[2].startswith("0001-")
        ):
            raise CpuPoolUnavailable("workload did not enter its enforced CPU pool")
        # Starting a systemd scope must not reset its parent slice's quota.
        helper = await self._helper("observe")
        try:
            await self._checked("wait", helper)
            observation = json.loads(await self._checked("logs", helper))
            quota, period = observation["cpuMax"].split()
            held_cpu_millis = await self.verify(lease)
            if quota == "max" or int(quota) * 1000 > held_cpu_millis * int(period):
                raise CpuPoolUnavailable("CPU pool enforcement changed during launch")
        finally:
            await self.runner(("rm", "--force", helper))
        await self._checked("rm", "--force", lease.holder)

    async def reconcile(self) -> bool:
        """Release the pool only after the kernel proves every consumer is gone."""
        from api_service.db.models import MachineCapacityReservation

        async with self.ledger._factory()() as session:
            await self.ledger.lock_for_admission(session)
            row = await session.get(MachineCapacityReservation, self.reservation_id)
            if row is None or row.state != STATE_ACTIVE:
                return False
            helper = await self._helper("observe")
            try:
                await self._checked("wait", helper)
                observation = json.loads(await self._checked("logs", helper))
                if observation.get("populated") is not False:
                    return False
                row.state = STATE_RELEASED
                row.cpu_millis = 0
                await session.commit()
                return True
            finally:
                await self.runner(("rm", "--force", helper))
