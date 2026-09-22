"""Standalone MoonMind deployment controller (MoonLadderStudios/MoonMind#4500).

This package is intentionally dependency-free: only the Python standard
library. It must never import ``moonmind.*`` (application API, DB, Temporal,
artifact service, provider manager, Omnigent, LLM). Release configuration is
accepted as plain data (dicts / JSON files), never via the target
application's Python bootstrap.

Modules:
  redact       - credential redaction for logs and state summaries
  compose_plan - semantic pull-then-up plan for the changed-service path
  store        - crash-safe local operation record (desired vs installed)
  kernel_lock  - kernel-owned per-stack file lock with legacy protection
  mounts       - daemon-visible bind-mount adapter (POSIX/Windows/WSL)
  lifecycle    - pre-apply validation / post-apply verification model
  auth         - deployment-owned shared-secret request authentication
  controller   - orchestration over injected runners (no Docker import)
  server       - small authenticated local HTTP endpoint (stdlib only)
"""

from __future__ import annotations
