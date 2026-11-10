# Phase 9 Compatibility Cleanup Audit

- **Date:** 2026-11-10
- **Scope:** P9-A safe cleanup only; P9-B is intentionally not attempted.
- **Authority:** `docs/architecture/ARCHITECTURE_CONTRACT.md` and `REFACTOR_EXECUTION_PLAN.md` remain the source of truth.

## 1. P9-B gate (not met)

P9-B retires the remaining Mobile-specific `BaseDeviceDriver` API only after a real non-Android/iOS Profile passes Adapter Contract/Integration tests. The first target is macOS (Phase 7), which is currently **design-only** and has no real Mac adapter or evidence. Therefore:

- do **not** delete `BaseDeviceDriver` or its Mobile API;
- do **not** treat the Phase 7 mock fixtures as a real macOS Profile;
- keep the Facade and re-export paths.

## 2. P9-A reference inventory

Production import scan (`rg` over `artemis/**/*.py`, excluding caches):

| Legacy entry | Production callers (examples) | Decision |
|---|---|---|
| `artemis.drivers.base.BaseDeviceDriver` | `drivers/factory.py`, `drivers/android`, `drivers/ios`, `drivers/cloud`, `drivers/mock`, many `tools/*` | Keep; active compatibility contract |
| `artemis.controllers.unified_controller.UnifiedMobileController` | `agents/flash/runner.py`, `agents/planner/planner.py`, `agents/checker/checker.py`, `graph/perception.py` | Keep; Android/in-process action path |
| `artemis.mcp.*` | Validator/Flash/Observer/action server/actuators | Keep; active L1 runtime surface |
| `artemis.reflex.capture` | factory, ios smoke/calibrate, reflex tool/CLI, UVC crop | Keep; still the Reflex Capture facade |
| `artemis.reflex.control` | reflex tool/CLI, ios calibrate, `adapters/esp32_control.py` | Keep; Reflex Control facade |
| `artemis.reflex.mouse` | `devices/ios_uvc.py` (PointerSession/SessionTapController) | Keep; relative-pointer compatibility |
| `artemis.reflex.vision` | no production imports; tests use it | Keep as re-export shim for external/legacy callers; new code uses `perception/pointer_detection.py` |
| `artemis.platform.darwin` | Host OS PAL only | Never count as macOS target control |

No entry had zero callers and a completed migration notice, so **no production module was deleted in P9-A**. `reflex/vision.py` was already reduced to a re-export shim in Phase 4.3; it remains until the compatibility period ends.

## 3. Control/Perception placement decision

- `control/` remains the L3-facing sink contract plus the existing compatibility implementations; `IOSControlSink` already owns iOS-only policy. No further directory-wide move is justified without a second real platform consumer.
- `perception/pointer_detection.py` is the reusable detection implementation; `reflex/vision.py` is only a shim.
- `artemis/platform/darwin.py` remains Host OS PAL; a future controlled-Mac adapter belongs under `artemis/drivers/macos` (not implemented).

## 4. New architecture guard

`tests/unit/architecture/test_import_boundaries.py` now blocks new imports:

- `artemis.core` → agents/devices/drivers/mcp/reflex/interfaces
- `artemis.safety` → agents/drivers/mcp/reflex/interfaces
- `artemis.drivers` → agents/graph/mcp
- `artemis.perception` → drivers/agents/graph/mcp
- `artemis.agents` → platform-specific `drivers.ios/android`, `adapters`, `mcp.actuators`

The guard is intentionally narrow: it protects the boundaries that are already clean and does not pretend to prove full architecture compliance. One documented exception remains: `artemis/core/diagnostics/device_smoke.py` lazily imports `artemis.mcp.adb_server` for a diagnostic probe. It is excluded explicitly in the guard and is a follow-up, not a newly allowed dependency.

## 5. Follow-up conditions

1. macOS ADR accepted and a real Mac Profile passes contract/integration tests.
2. Legacy callers of `BaseDeviceDriver`/`UnifiedMobileController` inventoried and migrated or explicitly accepted.
3. `reflex/vision`/UVC re-export shims removed only after external compatibility is documented.
4. Repeat the reference scan and add architecture guards before deleting any public entry.
