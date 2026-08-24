"""Infrastructure bootstrap for omnigent runtime."""

from __future__ import annotations

import asyncio

from moonmind.omnigent.bootstrap.image_resolution import resolve_omnigent_images
from moonmind.omnigent.bootstrap.store import save_resolved_state


async def bootstrap_infrastructure() -> None:
    """Resolve infrastructure-only prerequisites (no API key required)."""
    print("Resolving Omnigent deployment images...")
    state = await resolve_omnigent_images()
    print(f"Resolved server: {state.server_image_ref}")
    print(f"Resolved opencode host: {state.opencode_host_image_ref}")
    print(f"Build digest: {state.omnigent_build_digest}")
    save_resolved_state(state)
    print("Infrastructure bootstrap complete. State written.")


if __name__ == "__main__":
    asyncio.run(bootstrap_infrastructure())
