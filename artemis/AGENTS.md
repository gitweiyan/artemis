# Artemis Source Rules

Read the repository root `AGENTS.md` and `docs/architecture/ARCHITECTURE_CONTRACT.md` first. This file adds only local rules for `artemis/`.

- Keep L1/L2/L3 free of platform-specific driver imports; platform selection belongs in L4 composition roots.
- `core/` is stable DTO/Ports/Enums/IDs/Errors only. Do not import devices/drivers/mcp/reflex/agents from it.
- `safety/` is pure policy/primitives; do not import agents/drivers/mcp/reflex.
- `perception/` must not import drivers/agents/graph/mcp; locators/capture implementations stay behind Ports.
- `drivers/` must not import agents/graph/mcp.
- `agents/` must not import platform-specific `drivers.ios/android`, concrete `adapters`, or MCP actuators.
- New platform behavior goes into an L4 Adapter/Profile/PlatformExtension, never an `if platform == ...` in Agent code.
- Keep compatibility shims until their callers are zero and the migration notice is recorded; do not delete public entry points opportunistically.
- Run `tests/unit/architecture/test_import_boundaries.py` when adding imports across these boundaries.
