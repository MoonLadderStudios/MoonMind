"""Deployment-level rollout gate for autonomous remediation (issue #3512, Area 7).

Autonomous (``admin_auto``) remediation mutation stays fail-closed until an
operator explicitly enables it for the deployment. This is a separate control
from per-link authority, approvals, locks, cooldowns, and verification: those
still apply on top. The gate exists so a deployment can keep every remediation
link in manual/approval-gated operation even if a link is authored with
``admin_auto`` before the operator acceptance matrix has passed.
"""

from __future__ import annotations


def autonomous_remediation_rollout_enabled() -> bool:
    """Return whether autonomous remediation mutation is permitted here.

    Fail-closed: any error resolving settings, or an unset flag, means autonomous
    remediation is refused.
    """

    try:
        from moonmind.config.settings import settings

        return bool(settings.feature_flags.remediation_autonomous_rollout_enabled)
    except Exception:
        return False


__all__ = ["autonomous_remediation_rollout_enabled"]
