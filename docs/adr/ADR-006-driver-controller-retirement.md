# ADR-006: BaseDeviceDriver / UnifiedMobileController Compatibility and Retirement

- **Status:** Accepted
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 3（DeviceSession/Ports 成为标准边界）；Phase 9-A（安全清理）；Phase 9-B（条件性退役）
- **Related:** 契约 §5.4（Session）、§5.8（Control Port）、§6.1（Adapter）、§10（依赖方向）、§11（目录）；[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted）、[ADR-005](ADR-005-adapter-registry-profile-builder.md)
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §2–§8、`artemis/drivers/base.py`、`artemis/controllers/unified_controller.py`、`third_party/mobile_use/controllers/unified_controller.py`、`artemis/mcp/observation.py`、`artemis/tools/base.py`

> 状态说明：本 ADR 于 2026-11-10 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7/Phase 3。

---

## 1. Context and problem

### 1.1 `BaseDeviceDriver` 是一个 224 行的“万能接口”

`artemis/drivers/base.py:71-294` 同时声明：

- 身份/屏幕：`device_id`、`screen_size`；
- 设备连接：`connect`、`disconnect`；
- 观察：`get_screen_data`、`find_element`；
- 动作：`tap`、`long_press`、`swipe`、`swipe_direction`、`input_text`、`press_key`；
- App：`launch_app`、`stop_app`、`get_current_package`；
- Shell：`execute_shell`；
- 录像：`start_video_recording`、`stop_video_recording`；
- 坐标便捷方法：`tap_normalized`、`swipe_normalized`、`tap_element`。

Android、iOS、Cloud、Mock 都被迫实现这张大表；新平台（macOS/TV）会继续被要求实现不适用方法（ADR-003 §1.1 已确认）。

### 1.2 `UnifiedMobileController` 的实际分工与预期相反

- `artemis/controllers/unified_controller.py:54` 继承 vendored `UnifiedMobileControllerBase`（`third_party/mobile_use/controllers/unified_controller.py:36`）；
- 基类 `:46-146` 直接调用 `driver.tap/swipe/input_text/get_screen_data/...`；
- Artemis 子类只实现录像/断连/分段缓存（`:419-658`）；
- `cleanup()` 调 `driver.disconnect()` 但生产无调用者（Phase 0 审计：只有集成测试调用）。

因此动作链是：

```text
ActionSession -> AdbActuator -> UnifiedMobileController(artemis)
  -> UnifiedMobileControllerBase(third_party) -> driver methods
```

`DeviceSession` 只在 iOS 真正参与动作；Android 的 `AndroidAdbDriver.session` 是惰性旁路（`drivers/android/adb_driver.py:109-122`）。

### 1.3 旧抽象已经渗入 Agent/工具层

- `McpActionExecutor` 直接构造/持有 `AdbActuator` 与 controller（`mcp/action_executor.py:100-101`）；
- `observe()` 强依赖 `controller.get_screen_data()`（`mcp/observation.py:63`）；
- `ArtemisTool` 注入 `BaseDeviceDriver` 并读 `ctx._active_driver`（`tools/base.py:65-127`）；
- `ArtemisContext` 缓存 `_active_driver`、`_mobile_controller`（`context.py:189-190`）；
- `graph/perception.py:22`、`agents/planner/planner.py:26`、`checker.py:755` 直接 import controller。

Phase 3 要把动作边界切到 Session/Ports，但不能一次性删除这些入口；Phase 9 只有在条件满足后才能退役。

### 1.4 为什么必须现在决策

如果不先定义兼容期职责与退役门槛，Phase 3 的改造会变成“一边保持旧接口、一边暗改行为”，Phase 9 又会因缺少证据而无法判断何时能删。

---

## 2. Decision drivers

1. **单一事实源**：动作/观察状态由 Session/Ports 拥有；Driver/Controller 只是 Facade。
2. **不破坏调用方**：MCP schema、CLI、SDK、Agent Tool 参数不变。
3. **可验证退役**：退役条件必须可测试，不靠“看起来没人用”。
4. **平台中立**：新平台不被要求实现手机专属方法。
5. **录像/日志有家**：在 Recording Port 落地前，Controller 继续承担，不强行塞进 Session。
6. **第三方位界**：不移除 `third_party/mobile_use`，但 Artemis 核心不再新增对它的依赖。

---

## 3. Considered options

### Option A — 立即删除 BaseDeviceDriver/Controller

- Benefits：目录干净。
- Costs / risks：MCP/工具/录像/诊断全线破坏；Phase 3 尚未接通 Ports；不可回滚。

### Option B — 永久保留宽 Driver 接口

- Benefits：零迁移。
- Costs / risks：新平台继续实现不适用方法；Session 永远不是唯一边界；违反契约 §5.4/§6.1。

### Option C — Facade + 可选 Ports + 分阶段退役门槛（推荐）

- Benefits：行为可控；旧调用方不变；新平台只实现 Ports；退役有证据。
- Costs / risks：迁移期两套入口；需要 Architecture Test 防止新代码继续依赖旧面。

### Option D — 重写 vendored Mobile Use Controller

- Benefits：彻底去第三方。
- Costs / risks：超出范围；破坏许可证/边界/历史行为；Phase 3 无法独立回滚。

**选择：Option C。**

---

## 4. Decision (accepted)

### D1 — 兼容期的硬规则

1. `BaseDeviceDriver` 与 `UnifiedMobileController` 标记为 **legacy compatibility façade**；不再新增通用方法/字段。
2. 新平台不得继承/实现 `BaseDeviceDriver`；只实现 ADR-005 的 Profile + Session + 可选 Ports。
3. 旧入口可以继续委托，但不得成为新状态的事实源；动作/观察状态由 Session/Ports 写入。
4. 任何新代码 import `drivers.base`/`controllers.unified_controller` 必须说明迁移计划；Architecture Test 阻止 L2/L3 新增依赖。

### D2 — 可选 Ports（Driver 能力拆分）

| Port | 方法（概念） | 现有位置 | 说明 |
|---|---|---|---|
| ObservationPort | `snapshot`、`ui_tree`、`ocr`、`locate` | `PerceptionSource` | 已有；Driver 适配 |
| ActuationPort | `tap_abs`、`tap_rel`、`swipe`、`input_text`、`press_key` | `ControlSink` | 已有；不同能力分开声明 |
| AppLifecyclePort | `launch`、`stop`、`current` | Driver `launch_app/stop_app/get_current_package` | 移到可选 Port |
| WindowFocusPort | `focus`、`windows` | 暂无 | 桌面/TV |
| ShellDiagnosticsPort | `shell`、`health`、`logs` | Driver `execute_shell` + diagnostics | 可选 |
| RecordingPort | `start`、`stop`、`segment` | Controller 录像方法 | 任务级资源，非 Driver |
| ReportPort | 设备事件/日志 | 暂无 | 诊断 |

规则：

- Driver 只实现其设备**真实支持**的 Port；不支持的 Port 在 Capability 中为 `unavailable/unknown`，调用返回 `capability_unavailable`，不得返回 `False` 伪装成功。
- 一个 Driver 可以实现多个 Port；Profile 只组合需要的 Port。
- `tap_normalized`/`swipe_normalized`/`tap_element` 是坐标/索引便捷方法，**不进入长期 Port**；由 L3 Action 层转换。
- `find_element` 属于 Observation/Grounding 便捷方法，长期由 Locator 提供。

### D3 — Controller 的职责与退出路径

- 兼容期保留 `UnifiedMobileController` 用于：录像、日志、旧 SDK/CLI API、尚未迁移的 Helper/Screen Client 操作。
- 设备动作方法必须逐步代理到 `DeviceSession`/`ControlSink`；不得在 Artemis 子类新增平台逻辑。
- vendored 基类保持不动；Artemis 子类通过组合（Session/Actuator）覆盖动作，不修改第三方继承链。
- `RecordingPort` 落地后，Controller 的录像方法迁出；Controller 只保留兼容转发。
- `cleanup()` 必须由 `ArtemisContext`/Session 关闭路径接管（PR-3 已让 Context 关闭 Session）；不得保留无调用者的孤儿方法。

### D4 — Driver 的职责与 Session 关系

- Driver 变成 **Session 的兼容 Facade**：
  - `connect/disconnect` → Session `open/close`（保留既有录像/UI client 清理顺序）；
  - `get_screen_data` → Session `snapshot`（保留 `ScreenData` DTO）；
  - 动作方法 → `ControlSink`（保留 bool/ActionResult 语义，ADR-004 的 `ok` 仍只表示接受/派发）。
- Android 的 `session` 惰性属性升级为真实边界：Driver 动作先委托 Session；Session 未配置时回退旧实现并记录 `legacy_path=true`。
- iOS 已通过 Session 派发；Driver 只做兼容包装。
- Driver 不再拥有平台配置；配置在 L4 Profile/Adapter（ADR-005）。
- 设备资源释放由 Session 负责（ADR-003/011）；Driver `disconnect` 只转发。

### D5 — Context 与工具访问

- `ArtemisContext.device_session` 是任务级设备入口（PR-3 已存在）。
- `ctx._active_driver`、`ctx._mobile_controller` 标记 deprecated；新代码不得读取。
- `get_driver(ctx)` 保留兼容缓存；新代码通过 `DeviceSession`/Registry 获取（ADR-005）。
- `ArtemisTool` 的 driver 注入保留到 Phase 9-A；新工具只接收 Session/Ports 或 Application Service。
- MCP `observation.py` 改为 Observation Port/Application Service；`action_executor.py` 的 Actuator 选择下沉 L4（Phase 3）。

### D6 — 第三方位界

- `third_party/mobile_use` 保留为 vendored 依赖与许可证边界；不删除、不全量重写。
- Artemis 核心不得新增对 `third_party.mobile_use.controllers` 的直接 import；现有 import 列入 Phase 9-A 清单。
- 第三方 Agent/Config 只作为入口兼容层，不进入 Device Runtime 契约。

### D7 — 退役门槛

**P9-A（安全清理）前置条件（全部满足）：**

1. Android 与 iOS 的 Flash/Pro 主要动作/观察均通过 DeviceSession/Ports；用 spy adapter 断言（不是“代码看起来调用了”）。
2. `mcp/observation.py`、`mcp/action_executor.py` 不再直接依赖 Controller/Driver。
3. `BaseDeviceDriver`、`UnifiedMobileController`、`ctx._active_driver`、`ctx._mobile_controller` 的生产调用方为零（`rg` + Architecture Test）。
4. Android/iOS 回归（单测 + 真机 Smoke/最小任务）全绿。
5. 旧公开 import 有迁移说明；shim 删除可回滚。

满足后只删除**调用方为零**的 shim；保留第三方边界与录像/诊断未迁移部分。

**P9-B（条件性退役 Mobile 专属 API）额外条件：**

6. **macOS（Phase 7）作为第一个真实非 Android/iOS Profile** 通过 Adapter Contract/Integration Test（Mock/design-only 不算）；TV 随后。
7. 新 Profile 证明 Observation/Actuation/Transport/Recording 可由 Ports 组合，且不需要 `BaseDeviceDriver`。
8. 剩余 Mobile 专属 API 有替代 Port 或明确保留理由（例如 App Lifecycle 仍由 Platform Extension 提供）。

未满足时保留 Facade，不得以“目录整洁”为理由删除。

### D8 — 迁移步骤（Phase 3）

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| 3a | 标记 legacy；新增 `DriverSessionAdapter`（driver→Session/Ports） | 无 |
| 3b | Android Driver 动作委托 Session；`session` 从旁路变主路；保留旧实现回退 + `legacy_path` 标记 | 无（等价测试） |
| 3c | `UnifiedMobileController` 动作代理到 Session；录像/日志保留 | 无 |
| 3d | MCP observation/executor 改 Ports；Actuator 构造下沉 L4 | 无（MCP schema 不变） |
| 3e | Tool/Context 新入口用 Session；旧属性 deprecated | 无 |
| 9-A | 删除调用方为零的 shim | 有（清理） |
| 9-B | 满足 D7 后退役 Mobile 专属 API | 有（契约变更） |

---

## 5. Consequences

### Positive

- Android/iOS 共用 Session/Ports；新平台只实现需要的 Port。
- 旧调用方/MCP/CLI 不变，迁移可回滚。
- 退役有可验证门槛，不会因目录美观误删。

### Negative / trade-offs

- 迁移期 Driver/Controller/Session 三层并存，必须用 spy/parity 测试锁行为。
- `get_driver`/`_active_driver` 仍是隐式依赖，需要 Architecture Test 防止扩散。
- 录像 Port 未落地前，Controller 仍需存在。

### Follow-up work

- Phase 3：DriverSessionAdapter + Controller 代理 + MCP Ports。
- Phase 5：RecordingPort/诊断 Port。
- ADR-005：Profile/Registry 作为新构建入口。

---

## 6. Contract and dependency impact

- **Layers**：L4 拥有 Driver/Controller 兼容与 Session/Ports；L2/L3 只消费 Ports。
- **Ports / DTOs**：不新增公共 DTO；可选 Ports 复用 `PerceptionSource`/`ControlSink`，后续拆 AppLifecycle/Recording。
- **Capability**：Driver 不支持的 Port 必须 `capability_unavailable`；不得 `False` 成功。
- **Import direction**：L4 Driver 不 import Agent/Graph/MCP；L2/L3 不 import Driver；`third_party` 边界保留。
- **Compatibility**：`create_driver`/`get_driver`、Controller 公共方法、MCP schema、CLI 参数不变。

---

## 7. Verification and evidence

### Unit / Contract tests

- `tests/unit/devices/test_driver_session_adapter.py`（新增）：
  - Android Driver 的 tap/swipe/input 经 Session spy（不再直接旧方法）；
  - 不支持 Port 返回 `capability_unavailable`，不是 `False`；
  - Driver `disconnect` → Session `close`，资源释放顺序正确；
  - 旧路径回退标记 `legacy_path=true`。
- `tests/unit/controllers/test_controller_delegation.py`（新增）：
  - Controller 动作代理 Session；录像/日志仍工作；`cleanup` 由 Context 触发。
- `tests/unit/mcp/test_observation_port.py` / `test_action_executor_ports.py`：
  - observation/executor 不直接依赖 Controller/Driver；MCP schema 不变。
- Architecture tests：
  - L2/L3 不 import `drivers/controllers`；
  - `ctx._active_driver`/`_mobile_controller` 只允许在兼容层出现；
  - `third_party` 依赖清单不增长。
- Android/iOS 回归：单测 + 真机 Smoke/最小任务；录像回归。

### 退役证据

- `rg` 调用方清单 + Architecture Test 结果；
- spy adapter 证明动作经 Ports；
- P9-B 的非移动 Profile Contract Test 报告。

---

## 8. Rollback

- 兼容期所有改动 additive：Driver/Controller 保留原方法；回滚 = 关闭 `ARTEMIS_DRIVER_SESSION_ADAPTER=0` 走旧路径。
- 不做数据库/公开 API 变更；删除 shim 的 PR 可 revert 恢复。
- 录像/诊断未迁移前不删除 Controller。

---

## 9. Security and safety

- Session 关闭/取消/断连必须释放输入和 lease（ADR-003/011）；Driver/Controller Facade 不得绕过。
- 不支持能力必须显式拒绝，禁止 `False`/空结果伪装成功。
- 旧 API 的 bool 结果继续只表示“接受/派发”，不得被解释为 UI/业务成功（ADR-004）。
- 兼容层不得记录凭据/屏幕内容到无保护日志。

---

## 10. Resolved / open questions

已定：

- Facade + 可选 Ports；不立即删除、不永久保留。
- 退役门槛分 P9-A（安全清理）与 P9-B（Mobile 专属 API）。
- Controller 录像/日志保留到 Recording Port 落地。
- 第三方位界保留。

2026-11-10 Owner 确认：

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | RecordingPort 归属 | Phase 5 落地；在此之前 Controller 继续承担录像/日志 | Phase 5 |
| 2 | AppLifecyclePort | 允许由 Platform Extension 提供（Profile 声明），不强制通用 Port | Phase 4/5 |
| 3 | 第一个非移动 Profile | **macOS（Phase 7）**；TV 随后 | Phase 7/8 |
| 4 | Context 旧属性 | 保留一个 deprecation 周期；P9-A 前置条件满足后才删除 | Phase 3/9-A |

---

## 11. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial proposal | Driver 224 行宽接口、Controller 动作在 third_party、Session 仅 iOS 主链；Phase 3/9 需要可验证的兼容与退役规则 |
| 2026-11-10 | Accepted | 评审通过；确认 RecordingPort 到 Phase 5、AppLifecycle 可由 Extension 提供、P9-B 首站 macOS、Context 旧属性保留一个 deprecation 周期 | 四项细节由 Owner 确认；Phase 3 可开工 |
