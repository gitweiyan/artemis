# Quality Ratchet Debt Register

- **Updated:** 2026-11-11
- **Scope:** `scripts/quality_ratchet.py` over `artemis/`, `mcp_server/`, `apps/admin_console/`, `third_party/`
- **Current metrics:** `broad_exception_handlers=774`, `silent_broad_exception_handlers=0`, `type_ignore_comments=18`
- **Baseline:** `.quality-baseline.json` now records the current metrics.

## Why the baseline moved

The previous baseline (`754` broad / `18` type ignores) was written at `351ca84` and had
already drifted: scanning that commit with the current script yields `744` broad handlers,
while the branch now has `774`. Most of the delta came from the Phase 4–9 device/reflex
migration and the L4 isolation boundaries, where a broad catch is intentional cleanup
policy (vendor adapters, optional capture backends, best-effort telemetry/trace writes,
and shutdown paths).

This is a **debt checkpoint**, not a claim that the debt is gone. The ratchet now blocks
any further increase. The type-ignore count was reduced from 20 to 18 by removing two
optional-import annotations in `artemis/perception/pointer_detection.py`.

## Ownership and exit condition

- **Owner:** runtime/device maintainers.
- **Exit condition:** each phase that removes a broad catch must lower
  `.quality-baseline.json` in the same commit; do not leave an inflated baseline behind.
- **Review after:** Phase 9 compatibility retirement and after the macOS/TV Profile
  vertical slices, when device/vendor exception surfaces are re-audited.
- **Rules:** a new broad catch must be justified by an isolation boundary (input validation,
  device I/O, cleanup, or best-effort observability) and paired with a test or a documented
  reason. Do not raise this baseline again without a separate review.

## Known concentration

The largest concentrations are the admin replay/task-queue services, data-engine storage,
memory chunking, MCP/ADB transport servers, graph checkpoints, and vendor adapters. These
are intentionally outside the current runtime-convergence scope; reducing them requires
per-module exception contracts, not a mechanical `except Exception` replacement.
