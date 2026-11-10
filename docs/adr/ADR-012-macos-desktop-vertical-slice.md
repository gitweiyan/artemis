# ADR-012: macOS Desktop Vertical Slice and P9-B Activation

- **Status:** Proposed
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 7 implementation；P9-B（BaseDeviceDriver Mobile 专属 API 退役）
- **Related:** 契约 §9/§12；[MACOS_PROFILE_DESIGN.md](../architecture/MACOS_PROFILE_DESIGN.md)；[ADR-003](ADR-003-device-identity-session-resource-ownership.md)、[ADR-004](ADR-004-action-intent-command-receipt.md)、[ADR-005](ADR-005-adapter-registry-profile-builder.md)、[ADR-010](ADR-010-platform-extensions-semantic-actions.md)、[ADR-012 design fixtures](../../tests/fixtures/macos)

> 状态说明：本 ADR 当前为 **Proposed**。Phase 7 的生产 Adapter、真实 Mac smoke 和 P9-B 清理都不得在批准前执行。

---

## 1. Context and problem

Phase 7 已交付 `docs/architecture/MACOS_PROFILE_DESIGN.md` 与 Mock fixtures，但明确是 **design-only**：

- 没有生产 `MacOSObservationAdapter`/`MacOSInputAdapter`；
- 没有真实 Mac capability evidence；
- P9-B 仍被阻塞，因为还没有一个真实非 Android/iOS Profile 通过 Adapter Contract/Integration Test；
- `artemis/platform/darwin.py` 只是 Host OS PAL（Application Support 路径、进程树、Homebrew），不是被控 Mac 的 Target Adapter。

本机环境证据（2026-11-10）：

- macOS `26.4`（Build `25E246`），Apple M5 Pro，内置 Retina + 外接显示器；
- PyObjC `Quartz`/`AppKit`/`Foundation` 可导入；
- `ScreenCaptureKit`、`ApplicationServices` Python module 当前不可导入；
- `/usr/sbin/screencapture`、`/usr/bin/osascript` 可用；
- `cliclick`、`mss` 不可用；
- `artemis/drivers/macos` 尚不存在。

因此必须先决定：第一片真实 macOS 纵向切片用什么目标、采集/输入/AX 后端、权限和验收任务，以及 P9-B 的解除条件。

---

## 2. Decision drivers

1. **真实证据**：不能用 Mock、`screencapture` 成功或 CGEvent 返回成功代替 UI 验收。
2. **最小纵向切片**：先捕获一个窗口、读取窗口/焦点/可选 AX、做一次可验证的点击/键盘/文本，再扩展。
3. **权限诚实**：Screen Recording、Accessibility、Automation、Input Monitoring 分别探测；不可用即 `capability_unavailable`。
4. **不污染 Host PAL**：`artemis/platform/darwin.py` 保持 Host 职责；被控 Mac 走 `artemis/drivers/macos` + Profile。
5. **可回滚**：不删除 `BaseDeviceDriver`，`macos.desktop` 注册为附加 Profile；P9-B 只在真实测试通过后单独清理。
6. **可测试**：Mock fixtures 先于生产 Adapter；真实 Mac smoke 单独标记 hardware。
7. **不复制 Agent**：macOS 只新增 Adapter/Profile/Extension，不改 Planner/Operator/Checker。

---

## 3. Considered options

### Option A — 只用 shell（`screencapture` + AppleScript + `osascript`）

- Benefits: 无新 Python 依赖；可快速验证权限和窗口。
- Costs: 文本解析脆弱；AX 树能力有限；键盘/鼠标事件仍要 PyObjC 或额外工具；难以做结构化错误/端口。

### Option B — 纯 PyObjC 原生（ScreenCaptureKit + ApplicationServices + Quartz）

- Benefits: 类型化、性能好、可做完整 AX/CGEvent。
- Costs: 当前环境缺 `ScreenCaptureKit`/`ApplicationServices` module；依赖安装和 TCC 调试成本高；首片可能被环境阻塞。

### Option C — 混合：`screencapture` 采集 + PyObjC CGEvent 输入 + AX 后端可替换（推荐）

- Benefits: 首片能立即产生真实帧和输入；输入/坐标先打通；AX 可用 PyObjC ApplicationServices（后续安装）或 `osascript`/System Events 兼容；采集后续可替换为 ScreenCaptureKit Adapter。
- Costs: 采集是 subprocess，延迟/错误面比原生大；AX 兼容路径需要明确降级；长期仍应迁到原生采集。

**选择：Option C。**

---

## 4. Proposed decision

### D1 — 首个 Target 是本机（self-host）Mac

第一片真实切片以当前这台 Mac 作为被控 Target，设备 ID 建议 `macos-desktop-self`。Host ARTEMIS 与 Target 可以是同一台机器，但身份和权限边界仍按“Host PAL ≠ Target Adapter”处理：

- Host PAL：路径、临时目录、进程、Homebrew；
- Target Adapter：`artemis/drivers/macos` 中的观察/输入/窗口；
- 不得把 `platform.darwin` 的存在当作 Target 能力。

后续若接第二台 Mac，只新增 Target endpoint/远程传输，不复制 Profile/Agent。

### D2 — `macos.desktop` Profile 与能力

首片 required capabilities：

- `frame.capture`
- `window.inspect`
- `window.focus`
- `pointer.absolute`
- `keyboard.key`
- `text.input`
- `ui_tree.read`（若 Accessibility 不可用则明确 unavailable，不假装有）

首片 optional：

- `ocr.read`
- `grounding.locate`
- `device.health`
- `log.read`

`ui_tree.read` 在 AX 不可用时可以降级为“不可用 + OCR/窗口元数据”，但必须报告 `capability_unavailable`；不得把窗口标题当作 UI tree。

### D3 — Adapter 分层

```text
macos.desktop Profile (L4)
  MacOSCaptureAdapter      screencapture/Quartz first; ScreenCaptureKit later
  MacOSInputAdapter        Quartz CGEvent absolute click/key/Unicode text
  MacOSWindowAdapter       Quartz window list + NSWorkspace/AppleScript focus
  MacOSAXAdapter           PyObjC ApplicationServices if available,
                           osascript/System Events fallback (read-only)
  MacOSPermissionChecks    TCC probes + remediation
  MacOSPlatformExtension   AppLifecycle/Navigation/TextInput/TargetAdjuster/Guidance
```

Rules:

- `screencapture` subprocess must have a finite timeout and PNG/JPEG validation; failure maps to `capture_timeout`/`transport_error`/`uvc_no_signal` equivalents (macOS-specific reason text, canonical Incident).
- CGEvent input must release held keys/buttons on cancel/error/session close; a successful `CGEventPost` is only dispatch.
- AX fallback is read-only; do not use AX write/AppleScript GUI scripting as a success shortcut.
- Window IDs are ephemeral; frame/action evidence binds `window_id` + `display_id` + `transform_id`.

### D4 — Coordinate contract

Use the design in `MACOS_PROFILE_DESIGN.md`:

- `HOST_LOGICAL` (points), `DISPLAY_PIXEL`, `WINDOW_POINT`, `WINDOW_PIXEL`, `AGENT_POINT`;
- multi-display negative origins, Retina scale, window offset, rotation;
- every frame/action records transform ID; scale/origin/rotation changes invalidate the action (`coordinate_space_mismatch`/`stale_frame`).

### D5 — Permissions are capabilities

Probe at session open and on each degraded action:

| Permission | Required for | Failure |
|---|---|---|
| Screen Recording | capture | `capability_unavailable` |
| Accessibility | AX tree + CGEvent input | `capability_unavailable` |
| Input Monitoring | optional input observation | `capability_unavailable`/unknown |
| Automation/Apple Events | app activation via AppleScript | `action_rejected`/`capability_unavailable` |

A TCC prompt/denial must never be converted into a successful action or a `verified` capability.

### D6 — First real acceptance task

Required smoke (hardware, safe, no settings mutation):

1. Capture a frame of a known Finder window (or current app) and record display/window/transform.
2. Read window list/title/focus; if AX available, read at least one AX element and focus.
3. Move pointer to a known safe point and click a non-destructive control (e.g. open a Finder window or focus a window); observe the resulting frame/window/focus.
4. Type ASCII/Unicode into a disposable search field if available; verify text/focus via observation.
5. Cancel/close and verify held input release.

Acceptance requires structured `ActionResult`, observation evidence, no permission bypass, and no claim of business success from CGEvent/AppleScript return alone.

### D7 — P9-B activation criteria

P9-B may be reconsidered only after:

- `macos.desktop` passes Port Contract tests (Mock + fixture) **and** the real Mac smoke above;
- capability evidence is written with macOS build/display/permission fingerprint;
- `BaseDeviceDriver` Mobile-specific callers have a migration inventory;
- a separate PR/ADR approves removal of the Mobile-only API.

Until then, `BaseDeviceDriver`, `UnifiedMobileController`, `artemis/mcp` and Reflex compatibility paths remain.

### D8 — Non-goals

- No App Store/MDM/remote-control product feature.
- No full ScreenCaptureKit implementation in the first slice unless it becomes necessary.
- No delete of Mobile compatibility APIs.
- No macOS/TV Agent duplication.
- No claim that Mock fixtures are real-device evidence.

---

## 5. Consequences

### Positive

- Gives P9-B a concrete, honest activation path.
- Reuses existing Profile/Ports/Extension/Coordinate/Evidence contracts.
- First slice can be tested without waiting for ScreenCaptureKit Python bindings.
- Clear permission/degradation semantics for a new platform.

### Negative / trade-offs

- `screencapture` subprocess latency and error surface; must be replaced or bounded.
- AX fallback via AppleScript is less structured and must be isolated.
- CGEvent requires Accessibility; permission state can make the slice look “broken” until granted.
- P9-B remains blocked for at least one more implementation cycle.

### Follow-up work

1. Add `artemis/drivers/macos` adapters + Profile registration.
2. Add real Mac Port Contract fixture runner and permission probes.
3. Add `artemis macos-smoke` (hardware/manual).
4. Record capability evidence/fingerprint.
5. Only then re-evaluate P9-B.

---

## 6. Contract and dependency impact

- Layers/domains: L4 macOS Adapter/Profile; no L2/L3 platform branch.
- Ports/DTOs: existing `PerceptionSource`/`ControlSink`/`ScreenGeometry`/`DeviceSession`/`CapabilityManifest`; no new platform-specific fields on `DeviceSession`.
- Capability IDs: existing ADR-001 catalog only; `ui_tree.read` may be `unavailable` if AX absent.
- Adapters/Profiles: new `artemis/drivers/macos` and `macos.desktop` profile; optional ScreenCaptureKit/AX backends.
- Import direction: `agents`/`graph`/`interfaces` must not import PyObjC or macOS driver; host PAL remains separate.
- Compatibility: additive; no public removal.

---

## 7. Migration plan

| Step | Content | Behavior |
|---|---|---|
| 7a | Mock fixtures + Port Contract tests (already designed) | none |
| 7b | `MacOSCaptureAdapter`/window metadata/permission probe | new macOS only |
| 7c | `MacOSInputAdapter` CGEvent + release | new macOS only |
| 7d | AX adapter (PyObjC or osascript fallback) | new macOS only |
| 7e | Profile registration + real Mac smoke | new macOS only |
| P9-B | After real smoke + inventory, separate cleanup | removes only proven unused Mobile APIs |

**Non-goals:** no TV, no ScreenCaptureKit requirement, no Agent duplication.

---

## 8. Verification and evidence

### Unit / Contract

- Mock display/AX/input/permission fixtures (already in `tests/fixtures/macos/`).
- Capture timeout/invalid PNG/empty window list.
- CGEvent dispatch/release/cancel.
- Coordinate transforms for multi-display/Retina/rotation/window offset.
- Permission matrix never maps unknown to available.
- Profile composition/capability truthfulness.

### Real Mac (hardware/manual)

- Run the D6 smoke on this Mac; record macOS version/build, display IDs/scales/origins, permissions, window, frame, pointer path, action receipt, post-observation.
- Separate “Mock passed” from “real Mac passed” in the report.

### P9-B Gate

Only after real Mac contract/integration evidence and a migration inventory.

---

## 9. Rollback

- macOS Profile is additive; unregister/disable it without changing Android/iOS behavior.
- If `screencapture`/CGEvent proves unreliable, keep the adapter disabled and continue with Mock/contract tests; do not replace platform branches in Agent core.
- No database/schema migration is required for the first slice.
- P9-B remains untouched until explicitly approved.

---

## 10. Security and safety

- TCC permission prompts must be visible/actionable; no bypass.
- Never record passwords, full screen contents, or credential UI in ordinary traces; use artifact references/retention policy.
- Non-idempotent actions are not auto-retried; stale display/window/frame rejects.
- Input release on cancel/error/close is mandatory.
- A successful CGEvent/AppleScript call is only dispatch; UI verification is separate.
- Do not use AX write actions as a shortcut when a physical input + observation is required.

---

## 11. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial vertical-slice proposal | Phase 7 design exists but no real macOS evidence; P9-B needs a concrete gate |
