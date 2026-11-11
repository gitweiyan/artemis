# Post-Migration Architecture Audit — 2026-11-11

- **Audit commit:** `998a0c4` (working tree also has user-owned `config/artemis.jsonc`; not part of this audit)
- **Scope:** current Android/iOS call chains, Session/observation ownership, identity, import boundaries, test baseline.
- **Status language:** This is the current runtime audit; it does not overturn `ARCHITECTURE_CONTRACT.md` or `REFACTOR_EXECUTION_PLAN.md`.

> Correcting the earlier `c4455da` audit: Task ID, Flash action evidence, DEVICE_ACTION spans and Flash span duration were added after that audit. The privacy/retention plan is documented but **not implemented**. Phase 1–6 are still not fully “complete”.

## 1. Current status

| Area | Status | Evidence |
|---|---|---|
| Core identity/capability/session DTOs | Implemented foundation; L3 DTOs now core-owned | `core/identity.py`, `core/capabilities.py`, `core/errors.py` |
| Registry/Profile Builder | Implemented foundation; legacy factory imports remain | `devices/registry.py`, `devices/composition.py`, `drivers/factory.py` |
| Android action path | Uses Driver facade → Session sink; action contracts live in `actions` | `agents/flash/runner.py`, `actions/executor.py`, `drivers/android/adb_driver.py` |
| Android observation path | Session-backed L3 service; controller fallback only when no Session | `perception/observation.py`, `mcp/observation.py` (shim) |
| iOS UVC/HID | Session-owned; pointer/extension adapters | `devices/ios_uvc.py`, `adapters/*`, `control/ios_hid.py` |
| Identity | Canonical descriptor/Session/evidence/lock; UVC hardware-first, explicit config hint only as fallback | `core/identity.py`, `drivers/ios/uvc_identity.py` |
| Reflex | Independent budget/release/incident mapping | `reflex/runtime.py`, `reflex/contract.py` |
| Flash safety | Cheap pre-frame guard + No-Op Guard + all-action burst gate; pixel verification is explicit opt-in | `safety/flash.py`, `agents/flash/runner.py` |
| Data/Telemetry | ActionEvidence/Task ID/DEVICE_ACTION spans/CLI query | `data_engine/ids.py`, `telemetry/`, `trace actions` |
| Privacy/retention | **Documented only** | `PRIVACY_REDACTION_RETENTION_PLAN.md` |
| macOS/TV | Design-only/not started | `MACOS_PROFILE_DESIGN.md`, ADR-012 Proposed |
| P9-B | Blocked | no real non-mobile profile contract |

## 2. Current Android call chain

### Action (Flash/Pro)

```text
FlashRunner / Validator
  -> actions.executor.McpActionExecutor.execute
  -> ActionSession.call
  -> Actuator (AdbActuator)
  -> UnifiedMobileController
  -> AndroidAdbDriver (legacy driver facade)
  -> DeviceSession.control_for(absolute=True)
  -> AppControlSink
  -> AndroidAdbDriver._tap_legacy / action method
  -> ADB
```

The action reaches the Session sink, but the **transport/facade remains `UnifiedMobileController` + `BaseDeviceDriver`**. `AndroidAdbDriver.session` is now the same Session attached to `ctx.device_session` by the profile builder; it is no longer a separate lazy runtime object.

### Observation

```text
graph.perception.perception_node / Flash post-action observe / Validator
  -> perception.observation.get_screen_data(ctx, controller)
  -> if ctx.device_session exists:
        session.snapshot() + source screen/UI tree
        -> ObservationEvidence(frame/source/geometry/health/age)
     else:
        UnifiedMobileController.get_screen_data  # compatibility fallback
```
`artemis/mcp/observation.py` is now only a re-export shim; Flash and Validator import
the L3 service directly.

This closes the earlier “observation always through Controller” gap for Android when the Session is present. `graph/perception.py` still imports `UnifiedMobileController` as the fallback and is allowlisted.

### Remaining Android caveats

- `FlashRunner.__init__` still constructs a `UnifiedMobileController`; it is the action facade/legacy seam.
- `McpActionExecutor` and validator tool declarations still type controller; no new platform branch was added.
- No Android real-device test for multi-device aliases or a failure/cancel path in this audit.

## 3. Current iOS call chain

```text
Factory -> build_ios_uvc_session
  -> DeviceSession
      ├─ UvcSource (frame/OCR/locate)
      ├─ IOSControlSink
      ├─ RelativePointerAdapter
      └─ ResourceLease
  -> IOSUvcHidDriver
  -> MCP ActionSession / Flash / Pro
      -> UnifiedMobileController (compatibility controller)
      -> IOSUvcHidDriver
```

The **Session owns the adapters/leases**; the controller is still the upper-layer compatibility entry. `IOSControlSink` owns BACK/FKA; `RelativePointerAdapter` owns pointer state. `drivers/factory.py` still imports `reflex.capture` and `orchestration.selector`; these are explicit legacy allowlist entries.

## 4. Identity

```text
DeviceDescriptor.device_id (alias/canonical)
  ├─ DeviceSession.descriptor
  ├─ ActionEvidence.ids.device_id
  ├─ TelemetrySpan.device_id / task_id
  └─ SDK DeviceExecutionLock (pre-descriptor alias fallback)

physical ADB serial
  └─ ADB transport only; never overwritten by alias
```

UVC resolution order is serial → USB topology port path → explicit key → `CONFIG_HINT`. The current capture card did not expose a serial/topology record, so the live iOS runs record `CONFIG_HINT`; that is explicit, not a fake USB identity.

## 5. Import boundary matrix

The strict unit tests in `tests/unit/architecture/test_import_boundaries.py` cover:

- core/safety/drivers/perception forbidden directions;
- agents → platform-specific drivers/adapters/actuators;
- an explicit `LEGACY_ALLOWED` allowlist for:
  - agents/graph → controllers;
  - drivers → reflex/orchestration;
  - interfaces → drivers/reflex;
  - perception/control → devices capability/session DTOs.

The allowlist is migration debt, not permission to add new dependencies. Current notable legacy paths:

| Importer | Dependency | Why still allowed |
|---|---|---|
| `agents/flash/runner.py` | `controllers.unified_controller` | action facade until Phase 3R |
| `agents/checker`, `agents/planner` | controller | compatibility execution/observation |
| `graph/perception.py` | controller | compatibility fallback when no Session |
| `drivers/factory.py` | reflex.capture, orchestration.selector | legacy builder seams |

New violations fail immediately; existing entries must be removed when their owner phase completes.

## 6. Test baseline (current, 2026-11-11)

```text
uv run pytest tests/unit -q
0 failed, 3835 passed, 9 skipped, 88 warnings in 149.98s
```

Known failures: none. The former 12 Explorer/VideoAnalyzer failures were test-harness
issues: the tests mocked `genai.Client` but not `ctx._genai_client`, so the agents
fell back to the universal LangChain path and resolved a MagicMock provider. They now
attach the mock client and exercise the intended native branch.
- R2 progress: Session cleanup now captures arbitrary ordinary adapter exceptions via tasks; Session observation goes through `session.snapshot()` and does not fall back to the controller when a real Session exists.
- R2 real-device (2026-11-11): iOS smoke + LocateAnything passed; Flash `回到主屏` and `打开设置` completed with 15 action records (one no_effect click was reported, task recovered); Android `EUWSOBXCZLPFNJF6` Flash completed with Session-backed observation (`3e59d67b...`).
- P1-3 progress: Flash single-action precondition checks frame presence, 1-based index bounds, normalized target shape, and now rejects observations older than 5s for high-risk actions (`click`/`tap`/`long_press`/text input). Flash stamps the clock after initial and post-action captures; the action record carries the actual age and threshold. Pointer alignment remains delegated to the relative-pointer sink.
- P1-4 progress: `LEGACY_IMPORTS` is now structured (`importer/imported/owner/exit_condition/review_after`), covers devices→reflex and graph→mcp, fails unregistered imports, stale entries and expired review phases.
- P1-4 cleanup (2026-11-11): removed `graph/perception.py → controllers.unified_controller`; graph now calls the Session-aware `get_screen_data()` service and its L2 controller fallback lives outside the graph. Allowlist count is 43; remaining debt is listed in `legacy_allowlist.py`.
- P1-4 cleanup 2 (2026-11-11): graph now imports `perception.observation.get_screen_data`; the MCP observation module re-exports/delegates to it. `graph → mcp.observation` and `graph → controllers` are both removed. Allowlist count is 42.
- P1-4 cleanup 3 (2026-11-11): Checker final-screen capture and Planner initial-screen fallback now use the same `perception.observation` service, removing both `agents → controllers` entries. Allowlist count is 40.
- P1-4 cleanup 4 (2026-11-11): FlashRunner no longer imports `UnifiedMobileController`; its executor owns the actuator/controller compatibility seam. Allowlist count is 39.
- P1-4 cleanup 5 (2026-11-11): Validator tool declarations dropped the controller type import; factory/crop now import UVC from `adapters.uvc`; factory composition uses L4 `devices.composition`. Allowlist count is 35.
- P1-4 cleanup 6 (2026-11-11): `McpActionExecutor` moved to `artemis.actions.executor`; the MCP path is a compatibility shim and Flash no longer imports `mcp.action_executor`. Allowlist count is 34.
- P1-4 cleanup 7 (2026-11-11): video analyzer imports the L4 `devices.video` boundary instead of `controllers.controller_factory`. Allowlist count is 33.
- P1-4 cleanup 8 (2026-11-11): pinyin/ios_apps/locate_anything/pointer_control/mouse moved to text/drivers/perception/adapters with legacy shims; iOS device/driver imports no longer reach into reflex. Allowlist count is 27.
- Relative-pointer relocation: all 3815 passing unit tests remain green; an iOS smoke retry was blocked by the phone locking (suspended=1/mouse=0/keyboard=0) before pointer work.
- Post-cleanup real-device check: iOS Flash `回到主屏` (`e04f13f8...`) and Android Flash (`8f06e756...`) completed after the L4 composition change.
- Post-observation-boundary real-device check (2026-11-11): iOS Flash `回到主屏` (`635ef881...`) and Android Flash (`7f6f9bb1...`) completed after the extraction.
- P1-4 cleanup 9 (2026-11-11): CLI commands now import `actions.ios_rig`/`actions.reflex_service` facades; all 17 interface→drivers/reflex allowlist entries are removed. Only 10 L3→devices DTO entries remain.
- Pro observation check (2026-11-11): after unlocking the iPhone, iOS Pro `回到主屏` completed (`8d362f08...`), including Checker final screen capture through the new observation service.
- P1-3 pointer progress (2026-11-11): Flash precondition verifies a relative-pointer sink exists when the Session requires pointer precheck and marks alignment as delegated to the sink; missing sink returns capability_unavailable. Burst behavior remains declarative.
- P1-3 burst progress (2026-11-11): every device action now consumes the burst budget; `FLASH_BURST_ACTIONS` is the single admission allowlist and an action outside it is rejected before dispatch. The 8-action cap remains; alignment is still delegated to the relative-pointer sink.
- P1-1 Session close fix (2026-11-11): arbitrary ordinary lease-release exceptions and a lease `CancelledError` no longer strand the Session in `CLOSING`; cleanup reaches `CLOSED` and cancellation is re-raised only afterwards (`dbbfed5`).
- UVC identity decision (2026-11-11): resolver is hardware-first (USB Serial → Port Path → explicit hint → device-name hint); explicit hints cannot mask hardware, and strict mode rejects hints (`abb494d`).
- Current allowlist: **0 entries**. `CapabilitySet` and Session errors moved to `core`; `devices.capabilities` and L3 imports are shims/core-bound.
- Adapter/Reflex boundary (2026-11-11): adapters no longer import `artemis.reflex`; shared `Capture/Frame/Gesture/Control` moved to `core.reflex_contract`, motion to `perception.motion`, and helper attach/legacy control to `adapters`. The ratchet now also checks `adapters→reflex`.
- P0 real-device check (2026-11-11 19:06): with the phone unlocked, `ios-smoke --target 快捷指令` passed connect/snapshot/frame_fit/pointer_motion/locate/pointer_precheck/tap (7 capability records). Flash `回到主屏` completed in session `41b6d9a8-47e9-4fee-a8f8-288133e69e46` (2 steps, success) and released both UVC and serial leases; trace `traces/41b6d9a8-47e9-4fee-a8f8-288133e69e46_PASS_2026-11-11T19-07-41`.
- P0 real-device scope: this validates the Session close path exercised by Flash and the one-action path, not a multi-action burst rejection; the burst gate remains unit-tested.
- R3.1b observation boundary (2026-11-11): the implementation moved to `perception/observation.py`; MCP is a shim, action result DTOs moved to `core`, and `ObservationEvidence` carries frame/source/geometry/health/age on `ScreenData`. MCP `observe_screen` schema is unchanged. Unit: 3831 passed.
- P1-4 completion (2026-11-11): `CapabilitySet` moved to `core.capabilities`, `DeviceSessionError`/`SessionClosedError` to `core.errors`; `devices.capabilities` remains a shim and all 10 L3→devices entries are removed. Allowlist is empty, not a claim that every import boundary (especially adapters→reflex) is clean.
- Adapter boundary completion (2026-11-11): `adapters→reflex` is now zero and guarded by the architecture test; `reflex.control`/`attach`/`motion` remain compatibility shims. A post-move iOS smoke attempt was blocked by the locked phone (`suspended=1`); the last real smoke/Flash evidence predates this move.
- Test baseline cleanup (2026-11-11): the 12 existing Explorer/VideoAnalyzer failures are fixed by attaching the mocked native client; full unit is now **0 failed / 3831 passed / 9 skipped** (`f1aa129`). The quality ratchet (774 broad handlers vs its 754 baseline) remains separate debt.
- Observation freshness real-device check (2026-11-11 20:57): Flash session `6dc1c787-c5a2-456b-b448-f5ba93f6dba1` rejected three `click` actions with `stale_frame`; evidence showed `observation is 20.6s old (max 5.0s for click)` and `safety_precondition.allowed=false`, so the actions were not dispatched. The task then stopped on the existing No-Op Guard (failed, not a false success).
- Action receipt slice (2026-11-11): `ActionEvidence` now serializes `action_state` and `goal_verified` alongside `device_ack`; Flash can reach `adapter_acknowledged` or `observed_effect` but never writes task-level verification, and Validator only raises ACK to `observed_effect` when its post-action gate saw an effect. `trace actions` shows State separately.
- Action receipt real-device check (2026-11-11 21:02): Flash `回到主屏` session `6f0175e5-d187-4720-b252-4cce4971d840` completed successfully; action evidence reports `device_ack=adapter_acknowledged`, `action_state=adapter_acknowledged`, `goal_verified=false`. The task success claim therefore does not masquerade as action verification.
- Goal verification (2026-11-11): `RunOutcome.goal_verified` is written only by the final Checker path (`verdicts_allow_release` and no unmet subgoals); plan completion and adapter ACKs never set it. Real Pro run `b9e51024-0377-481e-a3c3-298350a01aca` completed with Checker 3/3 and `run_outcome.goal_verified=true`, while its action evidence remained `adapter_acknowledged`.
- Action-receipt effect evidence (2026-11-11): Flash carries the no-op guard's dHash verdict through `NoOpVerdict.changed`; a visible frame change raises the action to `observed_effect`, while no-op/failed actions stay at or below `adapter_acknowledged`. Real session `38ece0b8-7f4a-4da4-ae5e-4750246588a5` records `action_state=observed_effect` and `goal_verified=false` after a HOME action.
- Flash burst risk budget (2026-11-11): high-risk actions (`click`/`tap`/`long_press`/text input) now have a separate ceiling of 2 per turn inside the 8-action burst budget; the third high-risk action is rejected before dispatch. Low-risk actions keep the 8-action budget. This is bounded admission, not the material's stricter "no click by default" policy.
- R3.3 action boundary (2026-11-11): canonical `action_names`, `action_specs`, `action_manifest` and `ActionSession` moved from `artemis.mcp` to `artemis.actions`; the MCP paths are compatibility shims. No `artemis.agents.*` module imports `artemis.mcp` now, and the architecture guard checks the whole package. `actions/session.py` still owns the in-memory MCP transport as its application adapter.
- R3.4 acceptance (2026-11-11): iOS smoke/Flash/Pro/cancel/lease all passed; Android Flash `ad0ef70a...`, Pro `4f06fa3c...` (`goal_verified=true`), cancel `318bf58c...`, and helper lease release also passed (UIAutomator2 backend, helper unavailable warning). After physical replug, iOS smoke and Android Flash `9858ede3...` passed again; mid-task unplug remains unverified. See `R3_ACCEPTANCE_REPORT.md`.
- Post-adapter real-device check (2026-11-11 20:40): after unlocking, `ios-smoke --target 快捷指令` passed all seven stages again (7 capability records). Flash `回到主屏` completed in session `b9cb2860-76dd-4f2d-ada7-7d1adae260ce` (2 steps, 8.2s), action evidence `6fdde1e03a4...` (`ios-uvc`/`press_key`/`adapter_acknowledged`), and both UVC/serial leases were released. Trace: `traces/b9cb2860-76dd-4f2d-ada7-7d1adae260ce_PASS_2026-11-11T20-40-01`.
- Post-observation-boundary real-device attempt (2026-11-11 19:15): `ios-smoke` failed at `snapshot` with `frame health='black'` (`uvc_no_signal`) before any pointer work; no post-migration real-device claim is made.
- Post-migration real-device rerun (2026-11-11 20:28): after reconnecting the rig, `ios-smoke --target 快捷指令` passed all seven stages (7 capability records). Flash `回到主屏` completed in session `39fd9ed9-5857-48e2-9597-52afacc382a6` (2 steps, 9.1s); action evidence `3d85695e55f...` records `ios-uvc` / `press_key` / `adapter_acknowledged`; both UVC and serial leases were released. Trace: `traces/39fd9ed9-5857-48e2-9597-52afacc382a6_PASS_2026-11-11T20-28-47`. UVC identity remains `CONFIG_HINT` (no serial/topology exposed).

`uv run ruff check`: pass.
`uv run ruff format --check .`: 16 files would be reformatted (pre-existing baseline; not part of this fix).
`quality_ratchet`: `broad_exception_handlers=774`, `silent_broad_exception_handlers=0`, `type_ignore_comments=18`; the checked-in baseline is now current, with the debt and exit condition recorded in `QUALITY_RATCHET_DEBT.md`.

Real-device evidence available:

- iOS smoke + LocateAnything (`source=locate-anything`) passed; Flash/Pro tasks completed; action evidence/telemetry spans present.
- Android Flash on `EUWSOBXCZLPFNJF6` completed with action evidence.
- UVC live identity remains `CONFIG_HINT` until a stable USB serial/topology is available.

## 7. Remaining work

1. Stage 5 privacy/retention implementation and tests (documented only).
2. Remove remaining controller/reflex/orchestration allowlist entries only after migration.
3. Full replay query by task/profile and an Admin/CLI unified view.
4. Real failure/cancel/lease evidence on both mobile platforms.
5. P9-B remains blocked until a real macOS Profile contract test.
