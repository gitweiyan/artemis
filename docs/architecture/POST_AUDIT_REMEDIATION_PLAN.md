# Post-Audit Runtime Convergence Plan

- **Status:** Proposed remediation plan
- **Audit object:** `docs/SHOWCASE_RESULT_20261111.md`（代码审计基线 `c4455da`）
- **Current branch:** `integration/10101205-all-fixes`
- **Authority:** 本文件是执行计划补充，不是架构规范。契约仍以 `docs/architecture/ARCHITECTURE_CONTRACT.md` 为准，阶段顺序仍以 `REFACTOR_EXECUTION_PLAN.md` 为准。
- **Decision:** 暂停继续扩展 Phase 7/8；先完成 P0 运行时收敛和 P1 证据/门禁；P9-B 继续阻塞。

---

## 1. Audit verdict

审计的核心判断成立：

> 当前是 **Architecture Foundation Implemented，Runtime Convergence In Progress**，不是 Phase 1–6 全部完成。

确认成立的高优先级问题：

1. `DeviceSession` 没有正确拒绝 `FAILED`；`open()` 和 `_ensure_usable()` 都能把失败态重新变成可执行态。
2. `DeviceSession.close()` 在 `CancelledError`/异常清理时可能跳过 Lease 释放；`LeaseSet.release()` 也没有逐项释放隔离。
3. Android 的 Session 仍是 Driver 内部惰性包装；`build_android_profile()` 不把 Session 放进 `ctx.device_session`，观察仍走 `UnifiedMobileController`/`ControllerObservationPort`。
4. UVC 没有无序列号时的真实 USB port path 解析；`descriptor_from_device_context()` 会退回配置提示。
5. `ARTEMIS_DEVICE_ALIAS` 只改变 DeviceDescriptor；Driver、DeviceExecutionLock、Trace 仍可能使用原始 ID。
6. Architecture Test 只覆盖了少量已经干净的边界，缺少显式 legacy allowlist/退出条件。
7. Flash 没有与 Pro 对等的 `ActionGate` 前置校验；`res.ok` 仍可能被当成动作成功。
8. `drivers/factory.py` 仍从 `reflex.capture`/`orchestration.selector` 取实现；`ios_uvc.py` 仍直接依赖 `reflex.mouse`。
9. Phase 6 的敏感屏幕内容脱敏/保留策略未完成；`TEST_BASELINE.md` 仍是历史快照。
10. ADR-012 索引重复（已在本轮修复）。

需要修正的审计表述/时间差：

- 审计针对 `c4455da`；之后已补 Task ID、Flash action evidence、DEVICE_ACTION span、Flash span duration（`c0308f9` 等）。这些不应重复实现，但隐私/完整回放仍未完成。
- `ControllerObservationPort` 是显式兼容层，不应立即删除；应通过 allowlist 和 Phase 3R 迁移窗口处理。
- `ResourceLease` 的 resource_id 语义本身正确；问题在 UVC **identity 输入**没有真实 USB path，而不是 Lease 机制要重写。

---

## 2. Priority and sequencing

```text
Stage 0  docs/status hygiene
   ↓
Stage 1  Session lifecycle correctness          P0
   ↓
Stage 2  Android Session/observation ownership  P0
   ↓
Stage 3  Canonical identity + UVC USB path      P0
   ↓
Stage 4  Architecture matrix + Flash safety     P1
   ↓
Stage 5  Evidence/tests/baseline                P2
   ↓
P9-B gate (after real macOS slice and inventory)
```

Do not start the macOS real adapter while Stage 1–3 are open. ADR-012 may stay Proposed.

---

## 3. Stage 0 — Status correction (small)

### 0.1 Correct plan/status language

- Keep Phase 3 as **In Progress**, Phase 4/5/6 as **Partial**, Phase 7 design-only.
- Change Phase 6 replay/privacy checkbox to a note/partial; do not say “all Phase 6 tasks complete”.
- Add a link to this remediation plan from the execution plan and root AGENTS.

### 0.2 Current audit refresh

Create `POST_MIGRATION_ARCHITECTURE_AUDIT.md` after Stage 1–4 with a fresh scan:

- Android actions: which go through Session? which still go through UnifiedMobileController?
- Android observation: Controller vs SessionSource.
- iOS action/observation paths and remaining `reflex.mouse`/`reflex.capture` dependency.
- Flash/Pro port consumption.
- Production imports of legacy entries.
- Full test/lint/format/ratchet baseline at the commit being reviewed.

### 0.3 Small fixes

- Duplicate ADR index row (done).
- Update `TEST_BASELINE.md` only after the Stage 5 full run.

**Exit:** docs accurately say “foundation implemented, runtime convergence in progress”.

---

## 4. Stage 1 — Session lifecycle correctness (P0)

> Status (2026-11-11): implemented. `FAILED` is terminal; close finishes cleanup under cancellation and re-raises; `LeaseSet.release()` attempts every lease. Tests: failed-session terminal, cancelled closer, lease release isolation.

### 1.1 FAILED is terminal

Files: `artemis/devices/session.py`, `tests/unit/devices/test_session.py` (new/extend).

Rules:

- `open()` on `FAILED` raises `DeviceSessionError` (not READY).
- `_ensure_usable()` allows only `READY`/`DEGRADED`; `NEW` may open; `FAILED`/`CLOSING`/`CLOSED` reject.
- `snapshot()`/`control_for()`/`probe_capabilities()` cannot use a failed session.
- Retry must create a new Session.

Tests:

- NEW→READY; READY→idempotent; FAILED→open rejected; FAILED→snapshot/control rejected.
- Existing lifecycle tests still pass.

### 1.2 Close must finish cleanup under cancellation/errors

Files: `session.py`, `runtime/resource_lease.py`, tests.

Rules:

- Per-participant close failures are collected; one failure does not skip later participants.
- `CancelledError` is treated as cancellation, not a normal cleanup error; record it, finish input/lease cleanup, then re-raise (or return with cancellation recorded, according to caller contract).
- `LeaseSet.release()` attempts every lease, collects errors, clears ownership; raises the first error only after all attempts.
- `DeviceSession.close()` always attempts Lease release in `finally`; no Lease leaks when a participant fails.

Tests:

- Closer raises `RuntimeError`; later participant and lease still cleaned.
- Task is cancelled during control close; perception close and lease release still run.
- Two leases, one release raises; second still released; error surfaced after cleanup.
- Idempotent double close.

**Rollback:** pure lifecycle fix; no public DTO changes. If cancellation semantics need adjustment, revert close ordering only.

**Exit:** no failed-session reuse; no leaked lease on cancellation; tests pass.

---

## 5. Stage 2 — Android Session/observation ownership (P0)

> Status (2026-11-11): implemented. `build_android_profile()` returns a Session and attaches `ctx.device_session`; `AppAutomationSource.screen_data()`/`SessionObservationPort` and `get_screen_data()` prefer it; graph perception uses the Session path when present, controller only as fallback.

> Real-device verification (2026-11-11): Android `EUWSOBXCZLPFNJF6` Flash `打开设置` completed; `trace actions` shows 5 actions (press_key/manage_app/swipes) with adapter ACK and no incident.

### 2.1 Session becomes the Android profile's runtime object

Files: `drivers/factory.py`, `devices/app_session.py`, `devices/session.py`, `drivers/android/adb_driver.py`, tests.

Rules:

- `build_android_profile()` returns `ProfileBuild(session=..., driver=...)` and attaches `ctx.device_session` (same lifecycle as iOS). `create_driver()` may keep returning the driver facade.
- `AndroidAdbDriver.session` remains a compatibility view but must point to the same session when the context owns one.
- Session open/close ownership moves to the context, not a lazily created driver wrapper.
- Do not add Android-specific fields to `DeviceSession`.

### 2.2 Session-backed Observation Port

Files: `mcp/observation.py`, `graph/perception.py`, `perception/app_automation.py`, tests.

- Add `SessionObservationPort` that uses `session.snapshot()` and `session.perception[0].ui_tree()`; preserve `ObserveResult` fields (screenshot path, elements text, hierarchy_ok).
- `ControllerObservationPort` remains a fallback with an explicit `legacy_reason`/allowlist, not the preferred path.
- `graph/perception.py` must obtain observation through the Session/Port when `ctx.device_session` exists; no direct `UnifiedMobileController` construction in the main path.
- Preserve OCR/fusion/settling behavior and the `latest_screenshot`/`latest_ui_hierarchy` state shape.

Tests:

- Android mock session yields the same ObserveResult as the old controller path.
- `graph.perception` does not instantiate UnifiedMobileController when a session is present.
- Session observation failure maps to existing observation error semantics.
- Android regression tests; iOS smoke unchanged.

**Risk:** UI tree/fusion behavior changes are high impact. Split pure SessionObservationPort (no behavior change) from graph wiring. Keep controller fallback for one release.

**Exit:** Android Flash/Pro observation and action main path use Session; controller only compatibility fallback.

---

## 6. Stage 3 — Canonical identity and UVC USB path (P0)

> Status (2026-11-11): implemented in host-side code. Follow-up R2 (same day): Session observation now uses `session.snapshot()` before source data; arbitrary adapter cleanup exceptions are captured; the controller is not used when a real Session exists. Real-device R2: iOS smoke/Flash and Android Flash passed; no Controller fallback was observed. `resolve_canonical_device_id`/`ctx.canonical_device_id` drives ActionEvidence and the SDK device lock; UVC identity resolver prefers USB serial, then topology port path, then explicit config hint (strict mode fails closed). Real USB probe/port-path evidence is still pending because the current capture card has no USB serial/topology record.
> Real-device verification (2026-11-11): `ios-smoke --target 设置` passed all four stages; Flash `打开设置` produced action evidence with `device_id=ios-uvc`, task_id=session, latency ~7.1s and a matching `telemetry_spans.jsonl`; Pro `回到主屏` completed with Checker 2/2. UVC still resolves as `CONFIG_HINT` because the host USB probe exposes no serial/topology for this capture card.

> LocateAnything (2026-11-11): service `http://127.0.0.1:7863` verified on the unlocked iPhone rig: warm-up primed in 254 ms, locate used `source=locate-anything` in 1219 ms, all four smoke stages passed.

### 3.1 Canonical device identity

Files: `core/identity.py`, `devices/descriptors.py`, `drivers/factory.py`, `runtime/device_lock.py`, context/bootstrap, tests.

Define two separate facts:

- `physical_serial` / ADB serial: required transport address, never overwritten by alias.
- `canonical_device_id`: alias-resolved target identity from DeviceDescriptor.

Rules:

- `DeviceDescriptor.device_id` is canonical for device lease, ActionEvidence, Trace, and evidence fingerprint.
- Driver compatibility facade may retain physical serial for ADB operations, but must expose canonical identity for locks/evidence.
- `DeviceExecutionLock` receives canonical target ID where available; legacy callers keep physical serial with an explicit compatibility field.
- Log both when they differ, once at session start.

Tests:

- `ARTEMIS_DEVICE_ALIAS=lab-phone` yields descriptor/driver facade/lock key consistency.
- ADB serial remains the physical serial; commands still target it.
- Multi-device aliases produce distinct locks.

### 3.2 UVC identity resolver

Files: `devices/descriptors.py`, new `drivers/ios/uvc_identity.py` (or L4 observer), `drivers/factory.py`.

Order:

1. explicit `uvc_hardware_key` (operator/test pin);
2. USB hardware serial;
3. USB topology/port path (stable per host);
4. config hint with explicit `IdentitySource.CONFIG_HINT` and a warning.

Rules:

- No `/dev/videoN`/“USB Video” as a silent unique identity.
- If no stable key and strict exclusive mode is requested, fail closed with a clear remediation.
- Preserve the existing resource_id format and fingerprint invalidation.

Tests:

- serial → HARDWARE_SERIAL;
- port path → USB_PORT_PATH;
- missing → CONFIG_HINT + warning/fail-closed mode;
- changing device name does not change resource_id when topology is stable.

**Exit:** descriptor/driver/lock/evidence use one canonical target identity; UVC resource key is stable across reconnects when possible, explicitly config-hinted otherwise.

---

## 7. Stage 4 — Architecture matrix and Flash safety (P1)

> Status (2026-11-11): architecture matrix and Flash policy metadata implemented. `LEGACY_ALLOWED` now pins every known legacy import; new violations fail. `safety/flash.py` declares single/burst pre/post-verification posture and is attached to Flash action records. Actual Flash gate enforcement and post-verification remain follow-up because they can change click/launch behavior.

> P1-4 (2026-11-11): allowlist converted to structured `LegacyImport` entries with owner/exit/review_after; tests fail on unregistered, stale or expired entries. Remaining Flash pointer/burst enforcement is tracked separately.
 Cleanup started: graph→controllers removed (43 entries remain); graph still uses the Session-aware observation service, while its legacy controller fallback is outside L2.
 graph→mcp.observation removed by extracting `perception/observation.py`; graph now depends on the L3 observation boundary. 42 entries remain.
 Checker/Planner now use the same service; 40 entries remain.
 FlashRunner controller import removed; 39 entries remain.
 Validator/factory/crop cleanup leaves 35 entries; iOS/Android Flash passed after the change.
 Action executor moved to `artemis.actions.executor`; 34 entries remain.
 Video analyzer moved behind `devices.video`; 33 entries remain.
 iOS Reflex helpers moved behind L3/L4 adapters; 27 entries remain (17 CLI, 10 L3 DTO).
 CLI moved behind action facades; 10 L3 DTO entries remain.

> Stage 4b (2026-11-11): Flash refuses a point action with no pre-action frame (`stale_frame`) and keeps pixel `verify_effect` opt-in via `verify_after`; Flash stays latency-first and does not pay automatic post-verification on every launch. Policy declaration remains in every action record.
> P1-3 (2026-11-11): Flash precondition now validates the pre-action frame, 1-based index bounds, normalized target shape and records observation skew; pointer alignment remains in the controller. Burst enforcement and a true pointer gate still follow.
 Burst budget/whitelist (max 8) and pointer-sink delegation are now implemented and tested.
 Pointer sink presence is now checked; alignment is delegated to IOSControlSink. Burst enforcement remains the remaining P1-3 item.

> Stage 4b real-device (2026-11-11): iOS Flash `打开设置` produced manage_app action evidence with `post_verification.ok=true`; a middle `click` without a pre-action frame was correctly refused as `stale_frame` and did not block the eventual task. Android Flash on `EUWSOBXCZLPFNJF6` completed with action evidence.
> Efficiency follow-up (2026-11-11): after making pixel verification opt-in, iOS Flash `回到主屏` completed successfully with the LocateAnything service enabled; no automatic post-verification latency was added.

### 4.1 Import Boundary Matrix with legacy allowlist

Files: `tests/unit/architecture/test_import_boundaries.py`, maybe `tests/unit/architecture/allowlist.py`.

Add rules for the known remaining directions:

- `agents`/`graph` → `controllers` is legacy; allow only explicit files until Stage 2 completes.
- `perception`/`control` → `devices` (L3→L4) allowlist.
- `drivers` → `reflex`/`orchestration` allowlist with exit phase.
- `interfaces` → `drivers`/`reflex` allowlist (L1 entry adapters).

Allowlist entry shape:

```python
LegacyImport(
    importer="artemis.graph.perception",
    imported="artemis.controllers.unified_controller",
    owner="Phase 3R",
    exit_condition="SessionObservationPort is the main path",
)
```

Rules:

- New violations fail immediately.
- Existing allowlist entries fail if their `review_after` phase has passed.
- Do not add a broad wildcard exception.

### 4.2 Flash safety policy

Files: `agents/flash/runner.py`, `mcp/action_executor.py`, `safety/gate.py`, tests.

Decide/document:

- Flash single high-value action: at least pointer/target/observation precondition; reuse lightweight `ActionGate` where possible.
- Flash burst: explicit risk budget; only actions whose semantics are safe in a burst; no post-verification claim.
- Flash result must not be reported as verified merely because `res.ok=True`; use `dispatched`/`adapter_acknowledged` semantics and no-op/observation evidence.
- Pro keeps XML-first/Pixel fallback.
- Reflex keeps independent budget.

Tests:

- Flash single click can be intercepted by a gate/observability policy.
- Burst does not claim post-verification.
- `res.ok` alone never sets `verification=verified`.
- Existing No-Op Guard behavior preserved.

**Exit:** Flash/Pro/Reflex safety differences are explicit and tested; architecture allowlist has owner/exit conditions.

---

## 8. Stage 5 — Evidence, privacy, baseline (P2)

1. Sensitive content policy (documented, not implemented): see [`PRIVACY_REDACTION_RETENTION_PLAN.md`](PRIVACY_REDACTION_RETENTION_PLAN.md). Policy/redaction/retention hooks and tests remain future work; do not claim persisted secrets are already removed.
2. Fresh audit:
   - Generate `POST_MIGRATION_ARCHITECTURE_AUDIT.md` with call-flow/import matrix.
3. Fresh baseline:
   - Run `uv run pytest tests/unit -q`, `ruff check`, `ruff format --check`, quality ratchet.
   - Update `TEST_BASELINE.md` with commit, date, exact failures, Mock vs real evidence.
4. Correct Phase statuses and P9-B gate docs.

---

## 9. P9-B / macOS gating

- P9-B remains blocked; ADR-012 is Proposed.
- Do not implement macOS adapters until Stage 1–3 complete; otherwise the same runtime-convergence problem is duplicated on a third platform.
- After Stage 1–3, revisit ADR-012; then implement macOS 7b–7e and real smoke.
- P9-B removal requires a separate inventory/approval after macOS real evidence.

## 10. Definition of done for this remediation

- `DeviceSession` failed/cancel/lease tests green.
- Android Flash/Pro observation and action main path use Session; controller only explicit legacy fallback.
- Canonical identity is consistent across descriptor/lock/evidence; UVC identity is honest.
- Architecture allowlist is explicit and enforced.
- Flash/Pro/Reflex safety modes are documented and tested; `res.ok` is not verification.
- New post-migration audit and test baseline committed.
- No claim that Phase 1–6 are fully complete until these pass.
