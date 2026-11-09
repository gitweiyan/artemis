# ARTEMIS 调用链审计（2026-11-09）

> 审计对象：`integration/10101205-all-fixes` @ `a82d58e`（父 `e50bed0`，计划基线 `1281fc3`）
> 审计范围：CLI / MCP / SDK 入口 → Agent 外壳 → Flash / Pro / Reflex → 设备动作 → 观测 → 记录
> 审计方法：静态 AST 扫描 + `ripgrep` 调用方检索 + 单元测试基线；**未连接任何真实设备**
> 配套文档：[`IMPORT_DEPENDENCY_MATRIX.md`](IMPORT_DEPENDENCY_MATRIX.md)、[`TEST_BASELINE.md`](TEST_BASELINE.md)、[`BASELINE_2026-11-09.md`](BASELINE_2026-11-09.md)
> 规范：[`ARCHITECTURE_CONTRACT.md`](ARCHITECTURE_CONTRACT.md)；迁移计划：[`REFACTOR_EXECUTION_PLAN.md`](REFACTOR_EXECUTION_PLAN.md)

本文中的每个跳点都标注了 `文件:行号`。三类证据分开写：

- **已确认**：源码静态可核实（函数、调用、导入边）。
- **推断**：由静态结构推断出的运行时行为，未在本次审计中动态验证。
- **未验证**：需要真实设备、daemon、凭据或实机 iOS 环境才能确认。

---

## 0. 结论摘要

1. **入口只有三类**，但真正的执行外壳在 `third_party/mobile_use`：CLI `run` 直接调用 `third_party/mobile_use.main.run_automation`，MCP 与 daemon 最终也走同一个 `Agent` 基类；`artemis.sdk.Agent` 只是它的子类。
2. **Android 动作链不经过 `DeviceSession`。** `AndroidAdbDriver.session` 是惰性属性（`artemis/drivers/android/adb_driver.py:109-122`），生产路径没有消费方；动作由 `third_party` 控制器直接调用 driver 方法。
3. **所有 Flash / Pro 动作都汇入 `ActionSession`**（进程内 MCP client/server），再经 `Actuator` → `UnifiedMobileController` → `Driver`；这是当前唯一的统一动作收口点，也是单设备串行化的实际位置。
4. **Flash 与 Pro 是两条不同的执行 + 安全路径**：Flash 用 `McpActionExecutor`（无执行前安全网，只有执行后观察），Pro 用 `ValidatorNode` + `execution_loop`（XML/Pixel 前置校验 + incident）。
5. **Reflex 自建 capture/control**（`default_capture` + `Esp32HidControl`/`HelperControl`/`AdbInputControl`），不复用 `DeviceSession`；只有指针控制通过 `driver.pointer_control` 借用 iOS 适配器。
6. **iOS 是唯一真正经 `DeviceSession` 派发动作的链路**（`IOSUvcHidDriver` → `HidControlSink` → `SessionTapController` → ESP32 串口）。
7. **平台知识在 L1/L2/L3 共发现 7 处泄漏**（`is_ios_device`、LocateAnything 路由、iOS App Launcher 等），与契约 §2/§6.2 的 MUST NOT 冲突。
8. **设备级互斥由 `AgentBase.run_task` 获取**（CLI/standalone、MCP、SDK 任务路径都覆盖，`third_party/mobile_use/sdk/agent.py:490-516`），但 `artemis reflex`、`ios-smoke`、`ios-calibrate` 直接构造 UVC/串口、完全绕过它；且 `DeviceExecutionLock` 只锁逻辑设备，不锁 UVC/串口资源。

---

## 1. 入口清单（已确认）

| 入口 | 触发方式 | 第一跳 | 最终执行体 |
|---|---|---|---|
| CLI `artemis run` | `artemis/interfaces/cli/main.py:59` | `commands/run.py:165` → `run_automation` | daemon worker 或进程内 `Agent` |
| CLI `artemis ios-smoke` | `cli/main.py:60-63` | `commands/ios_smoke.py:41` | 无 LLM 的驱动级 Smoke（`drivers/ios/smoke.py:60`） |
| CLI `artemis ios-calibrate` | `cli/main.py:64-67` | `commands/ios_calibrate.py:65` | 指针增益测量 |
| CLI `artemis reflex` | `cli/main.py:78` | `commands/reflex.py:27-30` | `ReflexRuntime`（不经 Agent） |
| CLI `artemis mcp` | `cli/main.py:75` | `commands/mcp.py` | 启动外部 MCP 服务（`mcp_server/base.py:32`） |
| MCP `mobile_run_task` | `mcp_server/tools/task_runner.py:204` | daemon 或 `mcp_server.background.task_runner` | `artemis.sdk.Agent` → 同一 `Agent` 基类 |
| MCP 诊断工具 | `mcp_server/tools/{device_state,diagnose,inspect_trace,task_manager}.py` | 读取 trace / 设备状态 | 不进入动作链 |
| SDK | `artemis/sdk/agent.py:82`（`Agent(AgentBase)`） | `AgentBase.run_task` | FlashRunner 或 Pro graph |
| Admin Console / daemon | `apps/admin_console/services/task_queue_service.py:527-552` | `python -m artemis.main <goal> --profile ...`，`ARTEMIS_TASK_WORKER=1` | worker 走 CLI `run` 的 standalone 分支 |

CLI 的入口分流（`commands/run.py:325-380`，已确认）：

- `ARTEMIS_TASK_WORKER=1` 或带 `ARTEMIS_DEVICE_QUEUE_TICKET` → 视为 worker，不再入队。
- `--standalone` 或 `ARTEMIS_STANDALONE=1` → 进程内执行。
- **iOS 强制 standalone**（`run.py:336-341`）：daemon 的队列/锁是按 ADB serial 设计的，非 ADB 机台在进程内跑。
- 其余情况 → `ensure_daemon_running()` → `submit_task_to_daemon()`（`run.py:343-380`）。

MCP `mobile_run_task` 的分流（`mcp_server/tools/task_runner.py:321-505`，已确认）：优先提交 daemon；daemon 不可用时回退为独立子进程 `python -m mcp_server.background.task_runner`，并在 `:445` 先 `DeviceExecutionLock.reserve(...)`，把 ticket 通过环境变量传给子进程。

---

## 2. 公共运行外壳（`third_party/mobile_use`，已确认）

```
CLI run（standalone / daemon worker）
  -> third_party.mobile_use.main.run_automation        [main.py:90]
  -> artemis.Agent（= artemis.sdk.Agent）              [main.py:30 → artemis/sdk/agent.py:82]

MCP mobile_run_task / daemon
  -> artemis.sdk.Agent(config)                         [artemis/sdk/agent.py:82]

两条入口汇合到 AgentBase.run_task（继承自 vendored 基类）：
  -> 解析设备 / 构建 ArtemisContext                     [third_party/.../sdk/agent.py:160-165,456]
  -> DeviceExecutionLock.acquire（逻辑设备互斥）       [third_party/.../sdk/agent.py:490-516]
  -> get_controller(context)  （录像）                 [third_party/.../sdk/agent.py:555]
  -> profile == "flash" -> FlashRunner                 [third_party/.../sdk/agent.py:581-600]
  |  否则               -> get_graph(...).astream      [third_party/.../sdk/agent.py:622-640]
  -> DataEngineCallbackHandler / end_session            [third_party/.../sdk/agent.py:604-708]
```

要点：

- `run_automation` 属于 vendored 第三方包（`third_party/mobile_use/main.py`），**CLI 的第一跳就在 L1 之外的第三方代码里**；`artemis` 只提供 `Agent` 子类、上下文与图。
- 设备上下文 `ctx.device` 决定 `platform`、`device_width/height`；iOS 由 `drivers/ios/config.py:203-204` 写入描述符尺寸，Android 由 `sdk/agent.py:1050-1063` 从 `adb window_size` 写入。
- 录制（`start_video_recording`）在任务开始时由 `get_controller(context)` 触发一次；`UnifiedMobileController` 本身只实现录制与断连（`artemis/controllers/unified_controller.py:419/539/658`）。

---

## 3. Android 任务链（已确认）

```
CLI / MCP / Agent tool
  -> AdbActuator                                  [artemis/mcp/actuators/adb.py:103]
  -> UnifiedMobileController                      [artemis/controllers/unified_controller.py:54]
  -> UnifiedMobileControllerBase (vendored)       [third_party/mobile_use/controllers/unified_controller.py:46-146]
  -> AndroidAdbDriver                             [artemis/drivers/android/adb_driver.py]
  -> adb shell input / Accessibility Helper / UIAutomator2
  -> Android + Target App
```

关键跳点：

| 步骤 | 位置 | 说明 |
|---|---|---|
| 点击 | `actuators/adb.py:174` `click()` | 先把 0–1000 转设备像素（`:126-133`），再调 `controller.tap_at` |
| 坐标换算 | `actuators/adb.py:126-133` | 使用 `ctx.device.device_width/height`；与 `utils/coordinates.py:316-326`、`ScreenGeometry` 是三套并存实现 |
| 控制器 | `third_party/.../unified_controller.py:46-90` | `tap_at` 优先用 `driver.tap_with_result`，否则 `driver.tap`；无 session |
| Driver | `drivers/android/adb_driver.py:254` | `tap` 直接拼 `adb shell input tap`（或 helper 路径） |
| 观察 | `mcp/observation.py:63` → `controller.get_screen_data()` → `adb_driver.py:226-250` | `ScreenData` 写回 `ctx.device` 尺寸（`observation.py:87-88`） |
| Session | `drivers/android/adb_driver.py:109-122` | 惰性 `build_app_session`，**当前无生产消费方**（只有测试引用） |

结论：Android 的“session 化”目前是**旁路**，不是主链。执行计划 §1.2 已按此修正。

---

## 4. iOS UVC + ESP32 HID 任务链（已确认 + 少量推断）

```
CLI run (强制 standalone) / MCP runner
  -> AgentBase -> build context
  -> create_driver(ctx)                              [drivers/factory.py:52]
     |- _is_ios(ctx)                                 [factory.py:41]
     |- IosRuntimeConfig.from_env + 测 crop           [factory.py:86-110]
     |- select_composition(DeviceProfile(...))       [factory.py:113-126]
     |- UvcCapture / UvcStream                       [reflex/capture.py]
     |- Esp32HidControl                              [reflex/control.py]
     |- build_ios_uvc_session(...)                   [devices/ios_uvc.py:40]
     |    |- UvcSource                               [perception/uvc.py:29]
     |    |- PointerSession + SessionTapController   [reflex/mouse.py]
     |    |- HidControlSink                          [control/hid.py:36]
     |    |- ScreenGeometry(frame_size, screen_size) [devices/geometry.py]
     |    `- DeviceSession(pointer=pointer)          [devices/session.py:22]
     `- IOSUvcHidDriver(session)                      [drivers/ios/ios_uvc_hid_driver.py:70]
```

动作跳点：

| 步骤 | 位置 | 说明 |
|---|---|---|
| 能力声明 | `ios_uvc_hid_driver.py:63` | 类级 `CapabilitySet`；连接后用 session 能力覆盖（`:78-84`） |
| 连接仪式 | `:98-112` | 指针 preflight → `Cmd+H` 回主屏 → `pointer.home()`，避免唤醒点击落在弹层 |
| 点击 | `:237` `tap_with_result` → `_relative_sink()`（`:194`）→ `HidControlSink.tap_abs`（`control/hid.py:105`） | 屏幕像素先经 `ScreenGeometry.screen_to_frame` 换成采集帧像素 |
| 指针执行 | `control/hid.py:105-120` → `SessionTapController.tap`（`reflex/mouse.py`，线程中执行） | 检测指针 → 移动/伺服 → 校验 → 点击；失败映射为 incident |
| 串口 | `reflex/control.py::Esp32HidControl` | 一行一命令、等 `OK`、超时 3 s（`factory.py:143`） |
| 观察 | `ios_uvc_hid_driver.py:173` `get_screen_data()` | 返回**设备屏幕**尺寸（不是裁剪帧尺寸），并写回 `ctx.device`（`observation.py:87-88`） |
| 指针状态 | `devices/session.py:22` `DeviceSession.pointer` | 指针 belief 挂在通用 Session 上；只有 `IOSUvcHidDriver` 读它（`:99/121/138`） |

失败映射（已确认）：`tap_with_result` 把 `UnhealthyFrame` → `capture_timeout`/`uvc_no_signal`，`DeviceSessionError` → `uvc_no_signal`，其余 → `incident_for_error(exc)`（`:246-267`）。

诊断链（已确认）：`artemis ios-smoke` → `drivers/ios/smoke.py:60`，阶段依次为 `connect`、`snapshot`、`frame_fit`、`pointer_motion`、`pointer_precheck`、`tap`（`smoke.py:78-288`），每步记录 `SmokeStep`。

---

## 5. Flash 链（已确认）

```
AgentBase.run_task(profile="flash")
  -> FlashRunner(ctx, goal, max_turns)              [agents/flash/runner.py:145]
     |- UnifiedMobileController(ctx)                [runner.py:165]
     |- McpActionExecutor(ctx, controller, "flash") [runner.py:168]
     |- run(state)                                  [runner.py:1151]
     |    |- observe(ctx, controller)               [runner.py:646]
     |    |- LLM turn -> tool calls
     |    `- executor.execute(name, args, ...)      [runner.py:883]
     `- no_op / dHash 结果回填                       [agents/flash/no_op.py:151]
```

- **没有执行前 precondition。** `McpActionExecutor._execute_device_action`（`mcp/action_executor.py:160`）直接 `session.call(...)`，成功后 `session.observe(settle_ms=400)`（`:209`），再把 `ok/incident` 与截图回填给模型。
- 每次动作包一个 `TraceSpan(trace_type="action")`（`:170`），写入 Data Engine（`data_engine/trace.py:619-660`）。
- `no_effect` 由 Data Engine 的 pre/post dHash 与驱动 incident 合成（`agents/flash/no_op.py:151`），不是 Checker 的业务验收。

---

## 6. Pro 链（已确认）

```
AgentBase.run_task(profile != "flash")
  -> get_graph(ctx).astream(...)                    [graph/graph.py:963]
     START -> planner -> convergence -> perception -> operator
           -> execution_check --(execute_decisions)--> validator
           -> summarizer -> convergence -> exit_settlement -> end
     [graph.py:1048-1076]
```

| 节点 | 位置 | 设备访问 |
|---|---|---|
| `perception` | `graph/perception.py:119` | `controller.get_screen_data()`（`:155`），OCR + XML 融合，截图落 Data Engine（`:212-218`） |
| `operator` | `agents/operator/operator.py` | 只产出决策，不直接动设备 |
| `execution_check` | `graph/graph.py:128` | 计划/子目标检查 |
| `validator` | `agents/validator/validator.py:65` | 解析决策 → 前置校验 → 经 `get_action_session()`（`:82`）执行 → incident/修复 |
| `execution_loop` | `agents/validator/execution_loop.py:74` | `_run_precondition_gate`：有元素元数据走 XML（`:95`），纯坐标走 Pixel |
| `summarizer` / `exit_settlement` | `graph/graph.py:302` | 汇总/退出结算；Checker 做最终验收（`agents/checker`） |

安全语义（已确认）：Pro 的前置校验是 XML-first、Pixel-fallback（`precondition_xml.py`、`precondition_pixel.py`），失败按 `ValidationErrorCategory` 分类；Reflex/Flash 不走这条路径。

---

## 7. Reflex / Skill 链（已确认）

```
Agent tool play_reflex                              [tools/reflex_tool.py:105]
  -> registry.load_skill(name)                      [reflex/registry.py:157]
  -> 解析 serial；default_capture(serial, prefer)   [reflex/capture.py]
  -> 选择 actuator：
       driver.pointer_control (iOS)                 [reflex_tool.py:148]
       | Esp32HidControl(esp32_port())              [reflex_tool.py:154-163]
       | HelperControl(serial) / AdbInputControl(serial)
  -> ReflexRuntime(capture, control, fps_cap)       [reflex/runtime.py:76]
  -> session.run(handle, seconds, should_stop)      [reflex/runtime.py:97-220]
       |- fps cap (默认 10)                          [runtime.py:47]
       |- max_blind_frames / max_control_failures     [runtime.py:77-78,126-190]
       `- skill.parse/decide/to_gestures -> control.dispatch
```

- `Capture` / `Control` 是 Reflex 自己的轻量 Protocol（`reflex/contract.py:166-181`），与 `PerceptionSource` / `ControlSink` 不同。
- CLI `artemis reflex` 使用同一 Runtime（`interfaces/cli/commands/reflex.py:27-30`）。
- 取消：`should_stop` 读 Data Engine session + `runtime/cancel_requests.py`（`reflex_tool.py:177-190`）。

---

## 8. 动作与观测的汇合点（已确认）

所有 Flash/Pro 设备工具调用都经过同一处：

```
McpActionExecutor.execute
  -> ActionSession.call(name, args)                 [mcp/action_executor.py:205]
  -> in-process FastMCP server                       [mcp/action_session.py:233-239]
  -> build_action_server(actuator)                   [mcp/action_server.py:69]
  -> make_wire_handler -> wire.bind(actuator, kwargs) [mcp/action_specs.py:879-898]
  -> Actuator (默认 AdbActuator)                     [mcp/action_executor.py:100]
  -> UnifiedMobileController                         [controllers/controller_factory.py:22]
  -> Driver
```

- `ActionSession` 的 owner loop 串行化所有调用（`mcp/action_session.py:1-30` 的模块注释明确写着这是单设备策略的收口点）。这是**当前唯一能保证两个 agent 节点不同时发设备动作的机制**。
- 观测走同一会话：`ActionSession.observe()`（`:173`）调用内部 `observe_screen` 工具（`action_server.py:104`），最终 `controller.get_screen_data()`。
- `ActionSession` 是进程内 MCP：超时用工具参数 `timeout_ms`，禁止 transport 层 `read_timeout`（模块注释里记录了两个实测故障）。

---

## 9. 安全 / 验证在各链上的分布

| 链 | 执行前 | 执行中 | 执行后 | 任务级 |
|---|---|---|---|---|
| Flash | 无 | 无 | `session.observe` + dHash/incident（`action_executor.py:209`、`flash/no_op.py:151`） | 无 Checker（`FlashRunner` 返回 status） |
| Pro | XML/Pixel precondition（`validator/execution_loop.py:74`） | pointer precheck（iOS，`safety/pointer.py`） | Validator incidents + Checker | Checker / exit settlement |
| Reflex | 无 | fps/blind/control failure 预算（`reflex/runtime.py`） | skill 自身判定 | 无 |
| iOS 驱动 | `_prepare_pointer`（`ios_uvc_hid_driver.py:114`） | `validate_pointer`（`:215`） | `tap_with_result` 的 incident | 由上层决定 |

**结论**：同一个“动作失败”事实在不同链上有不同承载（`ActionResult.incident` 字符串、`ValidationErrorCategory` 枚举、Reflex stop reason、smoke step），且 Flash 完全没有前置安全网。这与契约 §7 的“统一 Incident 语义、双执行模式”一致，但当前实现尚未统一。

---

## 10. 数据 / 遥测 / 回放写入点（已确认）

| 写入 | 位置 | 归属 |
|---|---|---|
| 动作 Span | `mcp/action_executor.py:170`（action）、`:726`（tool）→ `data_engine/trace.py:619` | Data Engine |
| Pro 图回调 | `third_party/mobile_use/sdk/agent.py:630-640` `DataEngineCallbackHandler` | Data Engine |
| 截图 / UI 树 | `graph/perception.py:212-218`、`mcp/observation.py`（`ctx.data_engine.get_image_path`） | Data Engine |
| 会话起止 | `third_party/.../sdk/agent.py:604-708` `start_session/end_session` | Data Engine |
| 后台任务状态 | `artemis/runtime/trace_store.py`（`status.json`） | Runtime Status Store |
| 遥测 | `artemis/telemetry/`（span/latency） | Telemetry |
| 视频/回放 | `controllers/unified_controller.py:419-539` + `data_engine` sessions | Data Engine |

未确认：这些写入是否覆盖 iOS 的 Action 证据（`ActionResult.metrics`）到 Trace payload 的完整链路；本次只确认了 `TraceSpan.payload = {"args": args}`（`action_executor.py:171`）与 metrics 生成点（`control/hid.py:71-103`）。

---

## 11. 平台知识泄漏点（静态已确认）

| # | 位置 | 事实 | 契约冲突 |
|---|---|---|---|
| L1 | `artemis/mcp/action_executor.py:41,311,860-875` | 直接 import `is_ios_device`，并据此走 LocateAnything + Explorer 分支 | L1/L2 不得按平台分支（§2、§6.2） |
| L2 | `artemis/mcp/actuators/adb.py:252,326,338` | iOS 文本转 ASCII、`back` 走 `go_back` | 平台语义应在 L4 Extension |
| L3 | `artemis/agents/ios_shortcuts.py:78` | iOS Prompt Guidance 注入 Agent prompt | 应由 Profile 注入（§6.2 允许注入，但当前是 import） |
| L4 | `artemis/perception/ios_targets.py:52` | SpringBoard 图标布局规则在通用 perception | §11 要求迁到 iOS Profile |
| L5 | `artemis/control/hid.py:27-33,139-180` | iOS BACK 左边缘手势、FKA 方向键滚动 | 通用 HID Sink 混入 iOS 导航策略 |
| L6 | `artemis/drivers/ios/ios_uvc_hid_driver.py:20,210` | iOS App Launcher / PointerControl 依赖 reflex 实现 | L4 依赖特殊执行通道（G8） |
| L7 | `artemis/devices/ios_uvc.py:11-19` | L4 Session builder 组装 `reflex.mouse/vision/locate_anything` | L4 → Reflex 反向依赖（G8） |

---

## 12. 与执行计划 §1.3 的差距对照

执行计划登记的 G1–G14 复核结果（仅审计，不修改）：

| ID | 状态 | 本次证据 |
|---|---|---|
| G1 宽 Driver 接口 | 确认 | `drivers/base.py:71-294`（screen/app/shell/recording/find_element 等） |
| G2 `DeviceSession.pointer` | 确认 | `devices/session.py:22`；`ios_uvc.py:92` 写入；`ios_uvc_hid_driver.py:99/121/138` 读取 |
| G3 Boolean Capability + OR | 确认 | `devices/capabilities.py`；`devices/session.py:28-34`；factory 用类级能力（`drivers/factory.py:114`） |
| G4 Factory 混杂平台/硬件 | 确认 | `drivers/factory.py:52-196`（分支 + 配置 + 测 crop + 开句柄） |
| G5 新 Session 不是唯一边界 | **确认且更严重** | Android session 无消费方（`adb_driver.py:109-122`）；真实旁路是 `third_party/.../unified_controller.py:46-146` |
| G6 `control/hid.py` 混平台策略 | 确认 | `hid.py:9,27-33,139-180` |
| G7 通用 perception 含 iOS 规则 | 确认 | `perception/ios_targets.py:52`；`perception/uvc.py:84` → `reflex.locate_anything` |
| G8 Reflex 与 I/O 同目录 | 确认 | 见上表 L6/L7 |
| G9 Safety 调用路径分散 | 确认 | Pro：`validator/execution_loop.py:74`；iOS：`drivers/ios/ios_uvc_hid_driver.py:215`；Reflex：`reflex/mouse.py`（`safety/pointer.py`） |
| G10 三个存储职责相邻 | 确认 | `data_engine/trace.py`、`runtime/trace_store.py`、`telemetry/` 三处并存 |
| G11 `platform/` 命名 | 确认 | `artemis/platform/` 是 Host PAL；ADR/契约备注已有 |
| G12 坐标多表达 | 确认 | `ScreenGeometry`（0–1000↔frame↔screen）、`utils/coordinates.py:316-326`、`actuators/adb.py:126-133` 三套 |
| G13 固件协议未抽象 | 确认 | `reflex/control.py::Esp32HidControl`（760 行，含命令/ACK/分片）被 factory/CLI 直接使用 |
| G14 iOS 知识进 Agent/通用控制 | 确认 | 见 §11 的 L1–L5 |

新增发现（计划未登记）：

| ID | 发现 | 证据 | 影响 |
|---|---|---|---|
| N1 | 两个动作执行器（Flash 的 `McpActionExecutor` 与 Pro 的 `ValidatorNode`）语义不同 | `agents/flash/runner.py:168`、`agents/validator/validator.py:82` | 同一动作在 Flash/Pro 下安全与观测不一致 |
| N2 | `third_party/mobile_use` 是双向依赖与执行主链 | `sdk/agent.py:456-640`、`controllers/unified_controller.py:36` | 契约的 L1/L2 边界在入口处被第三方包穿透 |
| N3 | `CapabilitySet` 定义在 L4 `devices/`，被 L3 反向 import | `perception/base.py:8`、`control/base.py:8` | DTO 归属与依赖方向冲突（ADR-001 需解决） |
| N4 | `DeviceExecutionLock` 是**逻辑设备**锁，不是 UVC/串口资源锁；无 Agent 路径绕过它 | 取锁：`third_party/mobile_use/sdk/agent.py:490-516`；无锁：`interfaces/cli/commands/reflex.py`、`ios_smoke.py`、`ios_calibrate.py` | reflex/smoke/calibrate 与任务并发时争抢同一 UVC/串口 |
| N5 | 坐标换算存在三套实现 | §G12 | ADR-002 前任何“统一归一化”都会引入行为变化 |
| N6 | 每个动作都经过进程内 MCP 序列化 | `mcp/action_session.py:1-30` | Reflex 无法复用该通道（延迟预算不同） |

---

## 13. 未验证与已知风险

**未验证（需要真实环境）：**

- Android 真机上的 Flash/Pro 动作、录制、Helper 回退路径。
- iOS 机台的 `ios-smoke` 六阶段、指针校准（`gain`）与真实点击命中率。
- daemon 队列/锁在并发任务下的实际行为（本次只读代码；未启动 daemon）。
- `ActionSession` 的超时/取消在真实慢设备上的表现（模块注释记录了历史故障，本次未复现）。
- macOS / TV：代码中不存在目标平台 Adapter，只有 Host PAL。

**推断（静态结构得出，未动态验证）：**

- Android 动作确实不经 `DeviceSession`（依据：无 `.session` 消费方；`third_party` 控制器直接调 driver）。
- iOS 的 `DeviceSession.capabilities` 在 driver `__init__` 时覆盖类级声明（依据：`ios_uvc_hid_driver.py:78-84`）。
- Flash 的 `no_effect` 判定依赖 Data Engine dHash（依据：`flash/no_op.py:151` + `action_executor.py:209`）。

---

## 14. 复现命令

```bash
# 入口与分流
rg -n "run_automation|ARTEMIS_TASK_WORKER|ARTEMIS_DEVICE_QUEUE_TICKET" artemis third_party apps mcp_server
# 动作收口
rg -n "get_action_session|build_action_server|wire.bind" artemis
# 平台知识
rg -n "is_ios_device|locate_anything|ios_shortcuts|ios_targets" artemis
# session 是否在主链
rg -n "\.session\b" artemis/drivers artemis/controllers third_party/mobile_use
# 锁范围
rg -n "DeviceExecutionLock" artemis mcp_server apps
```

静态导入矩阵见 [`IMPORT_DEPENDENCY_MATRIX.md`](IMPORT_DEPENDENCY_MATRIX.md)；测试基线见 [`TEST_BASELINE.md`](TEST_BASELINE.md)。
