# ARTEMIS 通用硬件操控 Agent 架构契约

> 文档类型：规范性架构契约（Normative）  
> 基线仓库：[`gitweiyan/artemis`](https://github.com/gitweiyan/artemis/tree/integration/10101205-all-fixes)  
> 基线 Commit：`1281fc3ddf0d08ec8086d46111c2e6040a02b2d2`（2026-11-08）  
> 执行计划：[`REFACTOR_EXECUTION_PLAN.md`](REFACTOR_EXECUTION_PLAN.md)  
> 状态：目标契约；按执行计划渐进落地，不代表所有目标模块已经实现。

本文件中的 **MUST / MUST NOT / SHOULD / SHOULD NOT / MAY** 分别表示强制要求、禁止要求、建议要求、建议禁止和可选要求。偏离 MUST/MUST NOT 必须提交 ADR；不得通过改名、移动文件或新增兼容包装规避。

---

## 0. 产品定位、范围与非目标

### 0.1 产品定位

ARTEMIS 是一个**面向可观察、可交互数字设备的通用硬件操控 Agent 平台**。它接收目标和约束，通过一种或多种观察通道理解设备状态，选择设备真实具备的输入/控制能力，执行动作，并根据后续证据验证动作效果和用户目标。

首批目标环境：

- Android 手机、平板、模拟器；
- iOS / iPadOS 设备及模拟器；
- macOS 桌面、窗口和应用；
- Windows / Linux 桌面；
- 智能电视、电视盒子、媒体设备。

架构可扩展到其他具有数字观测与交互接口的设备，但每一种新设备必须通过能力契约、权限、真实环境验证和安全评估接入。

### 0.2 明确的范围边界

第一阶段的“通用硬件操控”指 **Human-Computer Interaction（HCI）类数字设备**：状态可以通过屏幕、UI Tree、日志、设备 API 或其他数字传感输入观察；操作通过 Touch、Mouse、Keyboard、Remote、ADB、Accessibility、系统 API、USB HID、CEC、IR、Bluetooth 或厂商协议完成。

本契约不自动授权 ARTEMIS 用于工业控制、机械臂、无人机、车辆、医疗设备、强动力执行器或其他安全关键物理系统。此类场景需要独立的实时系统、安全联锁、危险分析、故障容错和认证，不能只复用 UI Agent 的 Action 接口。

### 0.3 架构目标

1. Agent Core 不依赖 OS、设备品牌、连接方式、驱动、固件或输入协议。
2. 观察、感知、Grounding、动作语义、执行、Transport、Firmware 可独立组合与替换。
3. 通过 Capability + Adapter + Platform Profile 接入设备，不为每个平台复制 Planner/Operator/Checker。
4. Android 是现有兼容基线；已接入的 iOS UVC + ESP32 HID 是第二种组合验证样本。
5. macOS、TV 等新平台只增加 Adapter/Profile/Platform Extension，不修改通用 Agent 的平台分支。
6. 动作派发、设备 ACK、操作系统响应、可观察 UI 效果和业务目标完成必须区分。
7. 所有资源生命周期、坐标变换、错误、取消、输入释放、证据和回放可追踪。

---

## 1. 现状与目标：哪些已经有，哪些是目标

本契约以当前分支实际代码为基础，不把已存在的实现重复列为待建设。

### 1.1 已有实现（基线事实）

- `artemis/perception/base.py`：Frame、Target、TextBox、UIElement、PerceptionSource。
- `artemis/perception/`：AppAutomationSource、UvcSource、CameraSource、CompositePerception、OCR/Locator 相关代码。
- `artemis/control/base.py`：ActionResult、ControlSink；`app_control.py`、`hid.py`、`composite.py` 已有实现。
- `artemis/devices/`：DeviceSession、CapabilitySet、ScreenGeometry、App Session 和 iOS UVC Session Builder。
- `artemis/orchestration/`：Flash/Pro/Skill 执行档位选择和 Device Composition Selector。
- `artemis/drivers/android/` 与 `artemis/drivers/ios/`：Android ADB Driver 和 iOS UVC/HID Driver。
- `artemis/safety/`：Pixel、Pointer、XML 纯安全原语；Validator 也有独立执行前置检查与 Incident 逻辑。
- `artemis/reflex/`：确定性、高频、有限时 Skill Runtime，以及当前仍在使用的 Capture/Control/Pointer/视觉实现。
- `firmware/esp32-hid/`：ESP32-S3 ESP-IDF/TinyUSB 固件，具备 Touch、Mouse、Keyboard 及实验性 Absolute Pointer/BCI 相关构建路径。
- `artemis/platform/`：ARTEMIS Host OS 的路径、进程与包管理器抽象。

### 1.2 尚未达到的目标状态

- `BaseDeviceDriver` 仍是较宽的兼容接口；新 Device Runtime 尚未完全成为 MCP/Agent 的唯一设备边界。
- `UnifiedMobileController`、`artemis/mcp/observation.py`、`mcp/action_executor.py` 仍承担旧 Mobile Use 执行链的重要职责。
- Capability 仍是混合 Boolean 集合；没有统一的 declared/available/verified/unknown 状态和来源。
- DeviceSession 仍暴露 `pointer: object`，将相对指针状态放入通用 Session。
- Device Factory / Selector 已有平台组合概念，但尚未形成完整的 Adapter Registry / Profile Builder。
- `control/hid.py`、`perception/ios_targets.py`、`reflex/ios_apps.py`、`agents/ios_shortcuts.py` 等仍含 iOS 特定策略或跨层依赖。
- Reflex 的 Skill Runtime 与硬件 I/O 共处 `reflex/`，需要区分“保留高频运行时”和“抽离可复用设备实现”。
- Data Engine、Telemetry、Runtime Trace Store 尚需明确数据权威和关联规则。
- macOS Target Adapter 和 TV Profile 尚未形成完整、经真实环境验证的实现。

**规范解释：** 本文件中的未来接口/目录是目标约束，不代表当前分支已经全部实现。实施状态必须以代码与测试为准。

---

## 2. 五层运行架构 + 横向基础设施

### L1 — Access & Integration（访问与集成层）

职责：接收外部任务、鉴权/校验、创建 Task、查询状态、展示结果。

现有主要路径：

- `mcp_server/`
- `artemis/interfaces/cli/`
- `artemis/interfaces/sdk/`、`artemis/sdk/`、`packages/artemis-client/`
- `apps/admin_console/`、`apps/showcase_ui/`

MUST：

- 将外部请求转换成稳定 Task/Application Service 请求。
- 通过 Application Service/Device Registry 查询设备、提交任务和读取结果。
- 保持外部 MCP Tool Schema、CLI 参数和 SDK 语义兼容，破坏性变化需要版本策略。

MUST NOT：

- 直接打开 ADB、UVC、USB、Serial、BLE、CEC 或厂商设备连接。
- 生成 HID Report、串口固件命令或 IR 波形。
- 在 Web Handler/MCP Tool/CLI 中实现 Agent 推理或硬件协议。

### L2 — Agent & Orchestration（Agent 与编排层）

职责：理解用户目标、形成计划、选择执行策略、组织多轮任务、判断目标是否完成。

现有主要路径：`artemis/agents/`、`artemis/graph/`、`artemis/orchestration/profiles.py`。

包含：Planner、Operator、Checker、Summarizer、Diagnoser、Flash Runner、Pro Graph 等。

MUST：

- 只处理用户 Goal、约束、Capability View、Perceived State、Action Result 和验证证据。
- 只通过 L3 Tool/Interaction Contract 访问感知与动作能力。
- 可根据 capability 决定动作策略，但不得直接依赖设备平台名和底层协议。
- 任务计划与最终目标验收不得等同于“设备命令成功”。

MUST NOT：

- 在 Agent/Prompt/Graph 中硬编码 `if platform == ios/android/macos/tv` 来选择 ADB/HID/CEC 等协议。
- import `adbutils`、`uiautomator2`、`pyserial`、UVC SDK、平台输入 SDK、TinyUSB 固件实现。
- 将 `OK`、USB mounted、HID Report 已发送或工具返回成功视为用户任务完成。

允许的例外：平台扩展可提供受控的 Prompt Guidance、App Launcher 或键盘导航说明，但必须由 L4 Profile 注册并作为数据/能力注入；通用 Agent 不直接 import 平台实现，也不自行解析平台名。

### L3 — Interaction Intelligence（交互智能层）

职责：把观察样本变成环境状态和目标，把 Agent 的语义意图变成经过策略检查的标准动作，再请求 L4 执行并验证结果。

包含四个相互协作的能力域：

1. **Perception**：图像/视频帧预处理、OCR、UI Tree、窗口/控件解析、多源融合。
2. **Grounding**：把自然语言目标与 UI/屏幕/窗口元素关联，产出带来源、置信信息和坐标空间的 Grounded Target。
3. **Action Intelligence**：Action Intent、参数标准化、复合动作、策略选择、动作执行协调。
4. **Safety & Verification**：执行前置条件、风险检查、动作后状态变化检查、Incident 分类和业务验收证据接口。

现有主要路径：`artemis/perception/`、`artemis/control/` 的公共语义接口、`artemis/safety/`、部分 `artemis/tools/`。

MUST：

- 使用平台中立 DTO；Observation 可以只有图像，也可以有 UI Tree、窗口、OCR、日志等多模态信息。
- 对每个 Grounded Target 保留 source、frame reference、坐标空间和 confidence 状态。
- 通过 L4 Ports 请求设备能力，不直接打开硬件设备句柄。
- 把 Action Dispatch Result 与 Observation/Verification Result 分开。
- 对没有证据的能力或置信度如实标 unknown/null。

MUST NOT：

- 直接调用 ADB shell、UVC、Serial、USB、BLE、厂商 SDK 或 TinyUSB 命令。
- 在通用 OCR/Locator 中硬编码 iOS SpringBoard、Android Resource ID、macOS AX 元素类型或电视厂商 UI。
- 假设所有设备都有 UI Tree、Touch、Mouse、Keyboard、Window 或 Shell。

### L4 — Device Integration Runtime（设备集成运行时）

职责：发现/描述设备，管理设备 Session 和资源，组合 Adapter，处理协议及平台差异，向 L3 暴露稳定设备 Port。

目标子域：

- Device Registry / Discovery / Descriptor；
- Capability Registry / Resolver；
- DeviceSession、连接生命周期、资源租约、并发锁、健康状态；
- Observation Adapter：ADB Screenshot、Accessibility Helper、UVC、ScreenCaptureKit、HDMI Capture、厂商 API 等；
- Actuation Adapter：ADB Input、Touch、Mouse、Keyboard、Native Desktop Input、Remote/D-pad、CEC、IR、厂商 API 等；
- Transport Adapter：USB、Serial/CDC、BLE、TCP/IP、ADB Forward、IPC 等；
- Platform Profile：android、ios、macos、tv 等 Adapter 组合；
- Firmware Bridge：Host 与 ESP32/其他控制板固件之间的协议、能力、版本和生命周期。

现有主要路径：`artemis/devices/`、`artemis/drivers/`、`artemis/controllers/`（兼容层）、`artemis/clients/`、部分 `artemis/reflex/` 硬件 I/O、`firmware/`。

MUST：

- 负责实际硬件/OS 连接、重连、超时、资源关闭、输入状态释放、错误映射和协议兼容。
- 根据能力与 Profile 组合实际 Adapter；组合失败给出明确原因。
- Capability 必须按设备实例/接口/Session 真实状态返回，不能只读类级默认值。
- 支持一个 Device Session 组合多个 Perception Source 和多个 Control Sink；每个 Source/Sink 保留来源信息。
- 允许平台分支出现在此层的 Composition Root、Profile Builder 和平台 Adapter 内。

MUST NOT：

- 依赖 Planner、Operator、Checker、Prompt、LangGraph State 或 MCP Server。
- 自己理解自然语言业务目标或规划多步骤任务。
- 把某个平台可用的能力伪装成所有设备共有能力。

### L5 — Target & Host Environment（目标与宿主环境）

L5 是 ARTEMIS 作用的真实外部环境：Android/iOS/macOS/Windows/Linux/TV OS、目标 App、Window Manager、Accessibility/Input Stack、USB/HID Stack、外接硬件、网络服务和真实设备行为。

L5 不是 Python Package 子目录。

设备侧 Helper 和固件是 ARTEMIS 的部署工件/设备端组件，逻辑归属 L4 的 Adapter/Firmware Bridge；它们在 L5 的真实系统中运行并与系统交互。

---

## 3. 横向基础设施（不强塞进五层）

| 模块 | 现有主要路径 | 权威职责 | 不应承担 |
|---|---|---|---|
| Core Contracts | `artemis/core/`；逐步整理稳定 DTO/Ports | 类型、ID、错误码、跨层接口 | Agent 流程、平台 Driver 实现 |
| Model Runtime | `artemis/llm/`、`artemis/services/` | Provider、模型路由、超时/重试、结构化输出、Token 计量 | Planner/Operator 业务 Prompt 与计划策略 |
| Memory | `artemis/memory/` | Transcript、上下文压缩、History Retrieval、Agent 可用历史视图 | 设备句柄、原始媒体的唯一存储 |
| Data Engine | `artemis/data_engine/` | Task/Step、原始截图/录像引用、History Chunk、Artifact、Replay 数据 | 决定 Agent 下一步动作 |
| Telemetry | `artemis/telemetry/` | Span、调用链、耗时、Token/设备动作指标 | 取代 Step/Artifact 数据库 |
| Runtime Status Store | `artemis/runtime/trace_store.py` | 后台任务状态、进程间并发安全状态读写 | 作为唯一的执行证据/回放数据库 |
| Config | `artemis/config/`、`config/artemis.jsonc` | Schema、Loader、Validation、默认配置 | 在配置模块打开设备或执行动作 |
| Host OS PAL | `artemis/platform/` | 当前运行 ARTEMIS 的主机 OS 路径、进程、包管理器等 | 被控 macOS/Windows 的屏幕和输入适配 |
| Utils | `artemis/utils/` | 无领域依赖、可复用的纯工具 | 收容 OCR、ADB、平台导航等业务实现 |

横向模块可被多个层依赖，但必须有稳定接口和依赖规则。不要因为某模块被多个层使用就复制到多个层。

---

## 4. 执行模式契约：Flash / Pro / Reflex 是不同的运行方式

ARTEMIS 当前至少有三种不同执行模式，不能把它们都抽象成同一种 ReAct Agent Loop。

| 模式 | 当前实现 | 核心特点 | 对设备层的要求 |
|---|---|---|---|
| Flash | `artemis/agents/flash/` | 单 Agent 反应式 LLM 工具循环，优先低延迟 | 每次动作后重新观察/回填结果 |
| Pro | `artemis/graph/` + `agents/` | Planner/Operator/Validator/Checker 分阶段协作 | 动作前校验、Incident、检查点和最终验收 |
| Skill / Reflex | `artemis/reflex/runtime.py` + `skills/` | 确定性/专用 Policy，高频、有限时、无逐帧 LLM | 低延迟 Capture/Control Port、帧/动作预算、异常停止与输入释放 |

MUST：

- Flash/Pro 使用相同平台中立的 Device Runtime 和 Action/Observation Contract。
- Reflex 保留自己的 `Capture`/`Control` 轻量循环语义；可通过 Adapter Bridge 复用 Device Runtime，但不强迫每帧写 DB、运行完整 Checker 或重量级 SafetyNet。
- Skill 的 `parse`、`decide`、`to_gestures` 属于 Skill Policy，不自动等同于通用 LLM `DecisionProvider`。
- 只有在第二种真实 Skill/Policy 需要被多个模式共享时，才考虑抽象统一 Decision Protocol；不提前建设空接口。

---

## 5. 核心 Domain Contract

以下定义表达语义要求。迁移期间可以通过兼容字段/Facade 渐进实现，不要求一次性照抄类名。

### 5.1 Device Identity 与 Descriptor

必须区分：

- **Host**：运行 ARTEMIS 的电脑/服务器；
- **Target Device**：被控制的手机、桌面、电视等；
- **Physical Interface**：USB Port、Serial Port、Network Endpoint、BLE Device 等；
- **Logical Surface**：窗口、显示器、App、TV Input、屏幕区域等；
- **Profile**：把 Adapter 组合成可执行设备方案的描述。

`DeviceDescriptor` 至少表达 `device_id`、`platform_family`、`model`（可选）、OS/version（可选）、screen/display info（可选）、物理接口关联、Profile ID 和能力来源。不得把宿主机 `darwin/linux/windows` 与目标设备 `ios/android/macos/tv` 混为一个 `platform` 字段。

### 5.2 Device Profile 与 Composition

Profile 描述能力组合，不是新的 Agent。

示例：

```yaml
profile_id: ios.uvc_hid
platform_family: ios
observation:
  - adapter: uvc.capture
  - pipeline: image.ocr
actuation:
  - adapter: esp32_hid.mouse_keyboard
transport:
  - adapter: serial.command_channel
extensions:
  - ios.keyboard_navigation
  - ios.app_launcher
requirements:
  - capability: frame.capture
  - capability: pointer.relative
```

该 YAML 仅示意结构，不代表当前已经存在 YAML Profile Loader。

规则：

- Profile 可以标记平台身份，用于日志、配置、诊断和适配器选择。
- L2/L3 不得根据平台字符串自行推断能力；必须读取 Capability View。
- 一个设备可以同时使用多个 Observation Source 和 Actuation Sink。
- Profile 必须声明必需能力、可选能力和 fallback 规则。
- 不允许在发生非幂等动作失败后，未确认状态就切换另一个 Adapter 重放动作。

### 5.3 Capability Model

当前 `CapabilitySet` 是一组 Boolean 标志，适合第一阶段兼容，但不能作为长期能力注册模型的全部。

长期能力至少分为：

- **Observation**：`frame.capture`、`ui_tree.read`、`ocr.read`、`window.inspect`、`log.read` 等；
- **Actuation**：`touch.absolute`、`pointer.relative`、`pointer.absolute`、`keyboard.key`、`text.input`、`remote.dpad`、`app.launch`、`window.focus` 等；
- **Transport**：`usb.hid`、`serial.command`、`adb`、`ble`、`network.rpc`、`hdmi.cec` 等；
- **Diagnostics**：`device.health`、`input.status`、`capture.health`、`firmware.info`、`shell.exec` 等。

能力 ID 统一为 `<域>.<能力>`（小写、点分隔）。同一个能力不允许并存两种写法（例如 `observation.frame` 与 `frame.capture`），旧写法必须在转换层处理，不得进入新代码。

每个 capability 必须带状态：

| 状态 | 含义 | 示例 |
|---|---|---|
| `declared` | Adapter/固件声明支持，尚未在当前实例确认 | 固件 Descriptor 存在 Absolute Pointer Collection |
| `available` | 当前实例连接/权限/配置满足，可尝试使用 | 当前串口端口打开成功 |
| `verified` | 在目标设备/系统上通过约定 Smoke/Contract 测试 | 某台 iPhone 实测接受该 Pointer 输入 |
| `unavailable` | 当前实例确定不可用或权限缺失 | 当前 iOS Build 未启用 Touch Report |
| `unknown` | 无足够证据判断 | 设备未连接，无法探测 |

能力记录还应携带：来源 Adapter、设备/固件版本、权限与前置条件、探测时间、验证证据和失败原因。

**不能从“代码有方法”“HID Descriptor 可枚举”“串口返回 OK”推断目标系统已支持该能力。**

当前 `CapabilitySet` 到规范能力 ID 的映射（迁移期唯一权威）：

| 现有 `CapabilitySet` 字段 | 规范能力 ID | 说明 |
|---|---|---|
| `supports_ui_tree` | `ui_tree.read` | |
| `supports_absolute_tap` | `touch.absolute` | |
| `supports_relative_pointer` | `pointer.relative` | |
| `supports_ocr` | `ocr.read` | |
| `supports_text_input` | `text.input` | |
| `supports_shell` | `shell.exec` | |
| `supports_app_launch` | `app.launch` | |
| —（由 `PerceptionSource` 实现隐式保证） | `frame.capture` | Session 组装时按 Source 补声明 |
| `requires_pointer_precheck` | —（不是能力） | 是派生的安全约束，由 `pointer.relative` + 平台扩展推导；迁移期继续由兼容字段承载，但不得加入长期能力注册表 |

规则：新增代码只能使用规范能力 ID；`CapabilitySet` 作为兼容视图继续存在，字段到 ID 的映射只允许出现在一处转换层，不得由调用方手写能力字符串。

### 5.3.1 Capability Evidence、Fingerprint 与失效

`verified` 必须是可追溯、范围明确的证据结论，不是 Adapter 自报的永久 Boolean。

MUST：

- 每条 `verified` 记录引用不可变的 Smoke/Contract Test 结果或真实设备验证 Artifact，至少包括 capability ID、验证结论、时间、设备身份、适用 fingerprint、测试版本和执行环境。
- 区分静态声明、当前 Session 可用性和历史验证证据：断连影响当前 `available`，不自动删除同一设备/版本上的历史 `verified` 证据。
- 验证证据必须限定适用范围；不同设备、固件 Build、协议版本或权限状态不得继承彼此的 verified。
- 相关 fingerprint 变化、权限撤销、Adapter/Driver 语义变化、Firmware/Protocol 变化、目标 OS/Build 变化时，使受影响 Capability 的旧证据失效并要求重新验证。
- 证据过期/不新鲜应转为 `unknown` 或 `revalidation_required`，不能误报为 `unavailable`。
- 缺少 evidence reference 或 fingerprint 的能力不得标记为 `verified`。

证据存储位置、Snapshot 缓存与 fingerprint 字段清单见 [ADR-001](../adr/ADR-001-capability-model-evidence-fingerprint.md)/[ADR-008](../adr/ADR-008-data-engine-evidence-ownership.md)（Accepted 2026-11-09）。推荐将原始证据和 Artifact 放在 Observability/Data Engine；Capability Registry 只维护派生 Snapshot 与引用；Memory 不得作为验证证据的权威存储。

### 5.4 DeviceSession

`DeviceSession` 是一台目标设备在一次运行期间的交互上下文，负责：

- Device Identity / Profile / Capability View；
- 一个或多个 Observation Source；
- 一个或多个 Actuation Sink；
- Geometry/Coordinate Transform；
- Adapter 生命周期、健康状态、连接状态和资源释放；
- session_id、设备状态版本及事件关联。

MUST NOT：

- 放入 `pointer: object` 这类某种 Adapter 专属状态；指针 Belief、Gain、轨迹应属于 Pointer/HID Adapter 的私有 Session State。
- 直接暴露 ADB/Serial/UVC 句柄给 Agent。
- 依赖 `IOSUvcHidDriver` 等具体平台类。

#### 5.4.1 Device Lease 与共享 Resource Lease

设备任务互斥和 Host 资源互斥是两个不同范围：

- 目标设备级任务锁：防止同一逻辑 Target 同时被两个任务操作。当前 `artemis/runtime/device_lock.py::DeviceExecutionLock` 已提供 PID-aware FIFO/per-device 互斥，应优先保留并演进。
- Host 共享资源 Lease：防止两个不同 Target 或进程同时打开同一个 UVC Capture Device、ESP32 Serial Port、Camera、BLE Adapter 等独占资源。现有 DeviceExecutionLock 不能自动证明这些资源已被独占。

MUST：

- 每个共享资源具有稳定 `resource_id`；禁止使用易变化的枚举 index 作为唯一身份。
- 一个 DeviceSession/Runtime Allocation 可以持有多个 Lease；资源 Lease 在 Adapter 打开句柄前取得，在 close/cancel/error 路径释放。
- 多资源获取顺序必须确定；部分获取失败时回滚已取得的 Lease，防止死锁和半启动。
- 跨进程 Owner 必须可识别（process ID、process creation time、owner token、session/task ID）；只有确认 Owner 进程已死亡或显式释放后才能回收。不得只因 TTL 到期就抢占仍存活的长任务。
- 输入类 Adapter 在 Session 关闭、取消或异常退出时必须释放按键、鼠标按钮、触点等持续状态。
- 资源竞争、跨进程抢占、Owner 崩溃恢复、取消释放和重复 close 必须有测试。

Lease Manager 的实现是否复用/抽取 `DeviceExecutionLock` 的底层原语，以及 FIFO/公平性策略，由 [ADR-003](../adr/ADR-003-device-identity-session-resource-ownership.md)（Accepted 2026-11-09）与 [ADR-011](../adr/ADR-011-host-resource-lease.md)（Accepted 2026-11-09）决定。

### 5.5 Observation / Frame / Grounding

当前 `PerceptionSource`、`Frame`、`Target` 作为兼容基线继续演进。

规则：

- 一个 Observation 可以包含图像帧、UI Tree、Accessibility Node、OCR、窗口信息、日志或设备状态，不要求每种设备都提供全部字段。
- `Frame` 必须明确 `frame_id`、source、captured_at 时间域、width/height、rotation（适用时）、health 和 artifact reference（如已持久化）。
- `Target` 必须引用产生它的 Frame/Observation；box/point 必须带坐标空间；confidence 允许为 null/unknown；source 必须可追溯。
- UI Tree 不存在是合法能力缺失，不应伪造空树并报告“已成功感知结构”。
- OCR、视觉检测和 Accessibility 的证据来源必须保留，Fusion 不能抹掉来源。

### 5.6 Coordinate Space / Geometry

当前仓库实际存在多种坐标表达：

- Agent Action Point：`ScreenGeometry` 文档/方法使用 0–1000；
- `Target.box`：0–1 归一化 Box；
- Captured Frame Pixel：UVC/Camera Frame 中的像素；
- Device Screen Pixel：目标设备物理屏幕像素；
- Pointer Frame Pixel：裁剪后图像中检测到的指针像素；
- Window/Display Coordinate：桌面多屏、Retina、窗口局部坐标。

规范要求：

1. 所有 Point/Box 必须注明 `CoordinateSpace`，不能只传裸 `(x, y)`。
2. 转换必须经过 `ScreenGeometry`/`CoordinateTransform`，禁止在 Agent/Driver 中散落 `x * width / 1000`。
3. Crop、Letterbox、Rotation、Display Scale、Window Offset 都必须参与 transform。
4. Frame 和 Transform 必须能关联到同一版本；不能用过期 Frame 的位置去操作新页面。
5. 对模型输入/输出的历史坐标格式使用 Compatibility Adapter；是否最终统一为 `[0,1]` 或 `[0,1000]` 必须由 ADR 决策。迁移期间不允许静默混用。
6. 坐标换算失败或无法确认屏幕变换时拒绝动作，不盲点。

### 5.7 Action Intent / Command / Result

动作至少区分三种语义：

- **Intent**：用户/Agent 希望发生什么（例如打开应用、点击目标、按遥控器向上）；
- **Command**：针对某个已选 Adapter 的标准可执行输入（例如 Touch Point、Key Usage、Remote Key）；
- **Physical Report/Protocol**：USB HID Report、ADB 命令、Serial Line、CEC Frame 等，属于 L4。

不要把高层 `press_key("BACK")` 默认为某个设备的物理按键。某个平台可能以手势实现 Back，另一个平台可能通过 Remote Back 键实现；这是 Platform Extension/Capability Mapping，不是跨平台相同的物理输入。

动作状态至少区分：

```text
proposed
  -> validated
  -> dispatched
  -> adapter_acknowledged
  -> observed_effect
  -> verified_success | verified_failure | unknown
```

- `dispatched`：Host 已向 Adapter/Transport 提交命令。
- `adapter_acknowledged`：Adapter/Firmware/OS 接受或确认命令；并不代表界面响应。
- `observed_effect`：后续 Observation 发现环境变化。
- `verified_success`：目标状态满足约定验收条件。
- `verified_failure`：有证据表明目标未达成或操作失败。
- `unknown`：证据不足，不得伪装成功。

现有 `ActionResult.ok` 在兼容期只表示 Adapter 控制调用成功/接受；不能解释成 UI/业务完成。

非幂等操作（点击提交、购买、开关电源、确认支付、切换输入源等）默认禁止盲目重试。需要 Action ID、幂等策略或动作后观察确认。

### 5.8 Control / Actuation Port

当前 `ControlSink` 已有 `tap_abs`、`tap_rel`、`swipe`、`input_text`、`press_key`。这可以作为当前兼容 API，但长期设备动作不止手机 Touch：还包括桌面窗口、键盘/鼠标、电视 D-pad/Remote、Power/Volume、CEC/IR 等。

演进规则：

- 保留稳定的基本交互方法；新增能力通过可选子 Port/Typed Action 与 Capability 声明，不把所有设备操作塞入一个巨型接口。
- 触控、相对指针、绝对指针、键盘、文字输入、遥控器、窗口管理、App Lifecycle 和 Shell 是不同能力，不可静默互相降级。
- `tap_abs` 传入设备屏幕坐标不意味着底层真的支持绝对 Touch；相对 Mouse 可以通过定位算法实现“目标位置点击”，但它必须声明 `relative_pointer` 与 Pointer Precheck。
- `ControlSink` 不直接导入具体 USB/Serial/ADB 库；实现硬件协议的类属于 L4 Adapter。

---

## 6. Adapter 与 Firmware Bridge 契约

### 6.1 Adapter 分类

| 类别 | 输入/输出 | 示例 | 所属 |
|---|---|---|---|
| Observation Adapter | 设备/传感数据 → Frame/Observation | ADB screenshot、UVC、ScreenCaptureKit、HDMI Capture | L4 |
| Perception Pipeline | Frame/Tree → OCR/Target/State | OCR、UI Tree Parse、Visual Grounding、Fusion | L3 |
| Actuation Adapter | 标准动作 → 目标设备输入 | ADB Input、ESP32 HID、Native Input、CEC、IR | L4 |
| Transport Adapter | 字节/命令传输 | Serial、USB、BLE、TCP、ADB | L4 |
| Platform Extension | 平台特定语义/启动/校准/权限信息 | iOS Back Gesture、macOS AX 权限、TV Remote Key Map | L4 |
| Firmware Bridge | Host ↔ Firmware 协议协商与命令生命周期 | ESP32 UART Command Bridge | L4 |

同一物理设备可以组合多个 Adapter。UVC 负责观察采集，ESP32 HID 负责输入执行，两者通过 DeviceSession/时序与几何元数据关联，不要求在同一个固件或同一个 Driver 类内实现。

### 6.2 Platform Profile 不是硬编码分支

允许在 L4 选择 Profile：

- `android.app`：AppAutomation/Accessibility/UIAutomator + ADB/Helper Action；
- `ios.uvc_hid`：UVC Observation + ESP32 HID Mouse/Keyboard + iOS App/Keyboard Extension；
- `macos.desktop`：Screen Capture + Accessibility/Window + Native Keyboard/Mouse；
- `tv.remote`：HDMI/API Observation + Remote/D-pad/CEC/IR Actuation。

以上为组合示例，不代表全部能力已实现或 verified。

平台扩展还可声明语义动作映射与可选指导信息，例如 Back 的实现方式、文本输入编码、App Launch/Stop、窗口焦点策略、Remote Key Map 和平台权限提示。上层 Action Executor 只读取标准 Port、Capability 和结构化 Extension Result，不按 `platform == ...` 分支。LocateAnything/OCR/Visual Grounding 是可复用 L3 Locator，不应作为某一平台的专属执行流程。

**允许** `drivers.factory`/Registry/Profile Builder 根据平台 ID 选择对应 Profile。**禁止** Planner/Operator/Checker/通用 Perception/Action Policy 直接根据平台字符串选择协议或假设平台能力。

### 6.3 ESP32 / TinyUSB 固件边界

当前固件路径是 `firmware/esp32-hid/`，主机端串口控制代码目前位于 `artemis/reflex/control.py`，iOS Session 通过 `HidControlSink` 使用它。

当前已知协议/构建事实（需按具体固件 Build Variant 核对）：

- ESP32-S3，ESP-IDF + `espressif/esp_tinyusb`；
- USB OTG 连接目标设备，独立 UART/COM USB 口连接 Host；
- Host Serial 默认 115200 8N1；一行一条命令；每条命令完成回一行 `OK`，错误回 `ERR ...`；Host 必须等 ACK 再发下一条；单行有长度上限，长文本需要分片；
- 默认复合构建含 Touchscreen、Mouse、Keyboard；iOS-only build 关闭 Touchscreen，只启用 Keyboard + Mouse；
- BCI-only 是独立的实验 Build Variant；Descriptor/Report 存在不等于所有 iOS 版本都已支持/验证该能力。

契约：

1. `FirmwareBridge` 负责握手、协议版本、命令 ID/顺序、超时、ACK 解析、重连、固件 Variant 和能力清单。
2. `Esp32HidAdapter` 将标准 Actuation Command 映射为固件命令；不得让 Operator 知道 `amove`、`aclick`、HID Usage Code 或 UART 行格式。
3. ACK 只证明固件完成其协议定义的处理/提交，不证明 iOS/Android 接收或执行了输入。
4. Host 一次只发送固件当前协议允许的命令数；未确认旧协议吞吐能力前不得改为无界并发。
5. Touch、Relative Mouse、Absolute Pointer、Keyboard 是不同 Capability；某一 Build Variant 不支持时要明确拒绝。
6. 取消/超时/断连时释放按键、鼠标按钮、触点，避免卡键或持续触摸。
7. 固件协议升级保持 Host/Device 兼容测试；旧固件需有能力降级，不允许假装支持新命令。

---

## 7. Safety / Verification 契约

### 7.1 两个不同的安全闭环

**任务式交互（Flash/Pro）：**

- 执行前：目标存在性、Bounds/覆盖、Frame 新鲜度、Pointer 与 Target 一致性、能力和风险策略；
- 执行后：屏幕/树/焦点/设备状态变化；
- 任务级：Checker 根据 Goal、Plan、Assertions 和证据验收。

**高频 Reflex Skill：**

- 每帧解析失败计数、Blind Frame 上限；
- 动作派发失败计数；
- 最大 FPS、最大运行时间、取消与停止；
- 必要时的危险输入释放；
- 不默认逐帧执行重量级 Vision/LLM/Trace/Checker。

二者必须报告一致的 Incident 语义，但可以使用不同的安全预算和执行频率。

### 7.2 Incident 分类原则

Incident 表达可操作的事实，而不是猜测。下表是**跨层唯一命名表**：L1 工具层、L3 Safety/Validator、L4 Adapter、Data Engine/Telemetry 在跨越自身边界传递 Incident 时，只能使用 `canonical` 列的值。各层可以各自持有 StrEnum，但字符串取值必须与本表一致；新增取值必须先改本表，再改实现。

| canonical | 现状 | 含义 |
|---|---|---|
| `capability_unavailable` | 新增（草稿曾用 `unsupported`，废弃） | 当前设备/实例不具备或未确认该能力 |
| `command_unsupported` | 新增 | Adapter/固件不识别该命令（例如旧固件无 `amove`）；不得退化成语义不同的动作 |
| `device_disconnected` | 新增（草稿曾用 `device_unavailable`，废弃） | 目标设备断连 |
| `transport_error` | 新增 | USB/Serial/TCP/BLE 等传输层错误 |
| `firmware_error` | 新增 | 固件返回 `ERR`、协议不兼容或固件崩溃 |
| `resource_busy` | 新增（ADR-011） | Host 共享资源被另一个 ARTEMIS owner 占用（UVC/串口/helper 隧道）；必须带 `resource_id` 与 owner 摘要 |
| `capture_timeout` | 已有 | 采集在预算内没有出帧 |
| `uvc_no_signal` | 已有 | 采集卡无信号（HDMI/HDCP、息屏） |
| `decode_error` | 新增 | 帧解码失败 |
| `stale_frame` | 已有 | 验证帧超过新鲜度预算 |
| `target_disappeared` | 已有 | 目标从画面/树中消失 |
| `target_shifted` | 已有 | 目标移动，原坐标失效 |
| `target_occupied` | 已有 | 目标被弹层/键盘/其他元素遮挡 |
| `target_unverified` | 新增 | 没有足够证据确认目标位置 |
| `pointer_missing` | 已有 | 相对指针不可见 |
| `pointer_off_target` | 已有 | 指针在目标框外 |
| `pointer_not_centered` | 已有 | 指针在目标内但未进入点击容差 |
| `screen_changed` | 已有 | 动作期间画面被替换（已批准的计划失效） |
| `no_effect` | 已有 | 已派发且屏幕未发生变化 |
| `action_rejected` | 新增 | 策略/权限/执行器拒绝执行 |
| `verification_failed` | 新增 | 有证据表明目标未达成（Checker/业务验收） |
| `cancelled` | 新增 | 用户或上层取消 |
| `deadline_exceeded` | 新增 | 超出 deadline |
| `safety_interlock` | 新增 | 安全联锁触发 |

实现锚点：`artemis/agents/validator/categories.py::ValidationErrorCategory` 已包含上表标“已有”的取值。这些字符串**不得重命名**（`flash/no_op.py`、`operator/prompts.py`、`drivers/ios/smoke.py`、`runtime/trace_export.py` 都在按字符串消费）。

Validator 内部专用、不进入跨层表的取值：`none`、`general`、`pixel_target_disappeared`、`pixel_bypassed`、`xml_bypassed`。跨层上报时必须映射到上表，例如 `pixel_target_disappeared` → `target_disappeared`，`pixel_bypassed` / `xml_bypassed` → `verification_failed`。

置信信息：

- 有数值 confidence 时，才能按阈值判断低置信。
- confidence 为 null 时，记录 `confidence_source=unknown`，不要自动生成 `low_confidence`。

---

## 8. Memory / Data Engine / Telemetry 的数据所有权

| 数据 | 唯一责任模块 | 内容 |
|---|---|---|
| 原始 Task/Step/Action 事实、截图/视频 Artifact 引用 | Data Engine | 可回放、可审计的执行证据 |
| Capability Evidence、验证结论与验证 Artifact | Data Engine（全局证据库） | 跨运行的验证记录、指纹与证据引用 |
| LLM/Tool/Adapter 调用 Span、Latency、Token、Metrics | Telemetry | 性能与调用链观察 |
| Agent Transcript、压缩摘要、History Retrieval View | Memory | 下一轮 Agent 可用上下文 |
| 后台进程状态、MCP Worker 状态、status.json 并发写入 | Runtime Status Store | 任务进程运行状态 |

规则：

- 数据模块可通过 `task_id/session_id/step_id/action_id/trace_id/frame_id` 关联，但不得重复拥有同一事实。
- Memory 可以引用 Data Engine Artifact/Step；不能成为原始截图/视频唯一存储。
- Telemetry Span 可以引用 Step/Action；不负责持久化所有屏幕帧。
- Runtime Status Store 不承担 UI 交互证据或 Replay 数据库。
- 能力证据库不随单次运行目录（`--traces-path`）隔离；标记 `verified` 前证据必须已提交。
- 敏感屏幕、个人账号、Token、密码和设备凭据不得进入普通 Trace Payload；存储需遵守保留/脱敏策略。

---

## 9. Host OS 与 Target Platform 的边界

必须区分：

- **Host OS PAL**：ARTEMIS 当前运行在 macOS/Linux/Windows 上时，如何管理进程、路径、临时目录、依赖包；现有 `artemis/platform/` 属于此类。
- **Target Platform Adapter**：ARTEMIS 要控制一台 macOS/Windows/Android/iOS/TV 时，如何获取目标屏幕/树和发送输入；属于 L4 `drivers/adapters/platform_profiles`。

例如，`artemis/platform/darwin.py` 的存在不能作为“已经具备 macOS 桌面控制能力”的证明。未来控制 Mac，需要独立的 Screen Capture、Accessibility/Window、Native Input Adapter 与系统权限状态。

---

## 10. 依赖方向与模块边界

### 10.1 允许的主依赖方向

```text
L1 Access
   -> Application Service / L2 Orchestrator
      -> L3 Interaction Intelligence
         -> L4 Device Ports / Device Runtime
            -> OS API / Driver / Transport / Firmware
               -> L5 Target Environment
```

横向模块由明确接口提供服务：Model Runtime、Memory、Data Engine、Telemetry、Config、Core Contracts。

### 10.2 明确禁止的依赖

- L2 Agent → ADB/USB/Serial/UVC/HID/CEC/IR/平台 SDK；
- L3 Perception/Action Policy → 直接打开硬件句柄或构造固件 Report；
- L4 Driver/Adapter → Planner/Operator/Checker/Prompt/LangGraph State/MCP Server；
- L5 环境细节 → 反向侵入 Agent Core；
- Host OS PAL → 被控 macOS Desktop Adapter（两者只能共享必要的通用 Host 工具）。

### 10.3 允许的特殊位置

- L4 Composition Root 可以按 `platform_family`、Profile ID 和设备发现结果选 Builder。
- Platform Extension 可持有平台特定的 App Launcher、Back Gesture、权限检查和 Prompt Guidance，但只通过注册/注入机制对上层提供能力。
- 兼容期 `BaseDeviceDriver` 和 `UnifiedMobileController` 可以存在，但新业务实现不得持续增加对其内部字段的直接依赖。
- Reflex 的轻量 `Capture` / `Control` Protocol 可以作为高速执行接口，通过 Adapter Bridge 连接 Device Runtime。

---

## 11. 当前目录的归属与渐进演进

| 现有目录 | 主要职责 | 目标归属 / 处理方式 |
|---|---|---|
| `apps/`、`mcp_server/`、`artemis/interfaces/`、`packages/` | 外部接入 | L1；保持薄入口 |
| `artemis/agents/`、`graph/` | Agent 决策与图编排 | L2；去除平台硬编码 |
| `artemis/orchestration/profiles.py` | Flash/Pro/Skill Task Profile | L2；保留并与设备 Profile 区分 |
| `artemis/orchestration/selector.py` | 当前 Device Composition 纯选择 | 逻辑归 L4；过渡期保持兼容路径/re-export |
| `artemis/perception/` | Frame/UI Tree/OCR/Locator/Fusion | L3；抽出平台专属处理和采集句柄 |
| `artemis/control/base.py`、`composite.py` | Control Port 和通用组合语义 | L3 契约/门面 |
| `artemis/control/app_control.py`、`hid.py` | 当前具体 Control Sink 实现 | 逐步拆成 L4 Adapter 实现，保留 L3 Port |
| `artemis/safety/` | 纯安全原语 | L3；与 Validator 的策略调用统一 |
| `artemis/devices/` | Session/Geometry/Capability | L4 Device Runtime 核心；逐步补 Registry/Descriptor/Lifecycle |
| `artemis/drivers/` | Android/iOS/Cloud/Mock Driver | L4 平台 Adapter/兼容 Facade |
| `artemis/controllers/` | Unified Mobile Controller/旧兼容入口 | 迁移期 Facade；职责逐渐变薄 |
| `artemis/clients/` | Accessibility/Screen Client | 按真实调用归 L4 Observation/Transport Adapter |
| `artemis/mcp/` | Observation、Action Execution、Action Server、ADB Actuator | 混合目录；逐文件拆到 L1/L3/L4，不按目录整体归层 |
| `artemis/reflex/contract.py`、`runtime.py`、`registry.py` | 确定性 Skill Contract/Runtime/Registry | 保留为 Reflex 执行通道 |
| `artemis/reflex/capture.py`、`control.py` | 当前底层 Capture/Control 实现 | I/O 逐步迁到 L4 Adapters，保留 Reflex Bridge |
| `artemis/reflex/mouse.py`、`vision.py`、`motion.py` | Pointer 追踪/运动/视觉工具 | 算法按职责拆到 L3 Perception/Safety 或 L4 Pointer Adapter；不要整体搬迁 |
| `artemis/reflex/ios_apps.py`、`artemis/agents/ios_shortcuts.py` | iOS 平台 Launcher/Guidance | L4 iOS Extension，通过 Profile 注入 |
| `artemis/perception/ios_targets.py` | SpringBoard 图标/标签几何规则 | iOS Profile Grounding Extension |
| `firmware/esp32-hid/` | ESP32 TinyUSB 固件 | L4 Firmware Artifact/Bridge；独立构建测试 |
| `packages/artemis-accessibility-helper/` | Android 设备侧 Helper | L4 部署工件；运行在 L5 Android OS |
| `artemis/platform/` | Host OS PAL | 横向 Host Runtime；不是 Target Platform Adapter |
| `artemis/memory/` | Agent 上下文与历史视图 | 横向 Memory |
| `artemis/data_engine/` | Step/Artifact/Replay | 横向 Data Engine |
| `artemis/telemetry/` | Span/Performance | 横向 Telemetry |
| `artemis/runtime/` | 混合 Runtime/Trace Store/设备进程辅助 | 逐文件拆职责；不要整体归 L4 |
| `artemis/context.py` | Task/Device/Controller 等共享上下文 | 迁移期兼容 Context；逐步分离 Task Context/Device Session/Trace Context |
| `artemis/utils/` | 通用工具与领域工具混合 | 通用纯函数保留；Coordinates/OCR/Video/UI 等按领域逐步归位 |

目录归属是逻辑职责，不要求一刀切移动文件。

---

## 12. 新增平台的接入契约

增加新平台时必须先填完整的 Platform Onboarding Checklist：

1. **目标环境与场景**：被控设备是什么，屏幕/窗口/状态从哪里观测，具体用户任务是什么。
2. **Observation**：有哪些原始源（图像、UI Tree、窗口、日志、状态 API），健康和时序如何判断。
3. **Actuation**：有哪些输入执行器（Touch、Pointer、Keyboard、Remote、API），实际系统语义是什么。
4. **Transport/Firmware**：是否需要 USB/Serial/BLE/Network/Firmware，协议版本和资源生命周期是什么。
5. **Capability Manifest**：列出声明、运行可用、真实验证、缺失/未知能力，证据和前置条件。
6. **Geometry**：屏幕/窗口/采集帧/裁剪/旋转/多屏的坐标空间和转换。
7. **Profile Composition**：只组合现有 Adapter；确实缺乏复用能力时才新建平台专属 Adapter。
8. **Safety/Verification**：动作前检查、动作后观察、非幂等重试、权限失败和急停方式。
9. **Trace/Diagnostics**：每个动作能关联 Device、Profile、Adapter、Target、Receipt 和 Verification。
10. **Tests**：Mock、Port Contract、Adapter Lifecycle、Profile Composition、平台集成、真实硬件验证。
11. **Documentation**：平台能力矩阵、权限要求、已知限制、Smoke/Calibration 手册。

不得为新增设备复制 Planner/Operator/Checker/Flash/Pro。

---

## 13. 测试与质量契约

每个实现 Adapter 都应通过：

- Port Contract Tests；
- Lifecycle/Timeout/Cancel/Close Tests；
- Capability Truthfulness Tests；
- Error/Incident Mapping Tests；
- Geometry/Coordinate Transform Tests（若涉及屏幕）；
- Idempotency/Retry/Release Tests（若涉及输入）；
- Profile Composition Tests；
- Mock/Recorded Fixture Tests；
- 真实平台/硬件测试（适用时）。

Architecture Tests 应阻止：

- L2/L3 直接 import 平台驱动或传输库；
- L4 import Agent/Graph/Prompt；
- 未声明的循环依赖；
- `utils/` 出现新的领域实现；
- 新平台能力只通过 if/else 散落在 Agent。

提交说明必须报告：变更层级、新增/修改 Port/DTO/Capability、旧调用兼容情况、执行的测试、Mock 与实机验证区别、未验证能力、回滚方法。

---

## 14. 最终架构验收标准

ARTEMIS 满足以下条件，才能称为通用硬件操控 Agent，而不是“支持更多平台的 Android Agent”：

- Agent Core 与设备型号、OS、Adapter、Firmware 无直接依赖。
- Android/iOS 使用同一套 Device Runtime/Ports，且现有工作流不退化。
- 新增 macOS/TV 只需注册 Adapter/Profile/Platform Extension，不修改 Planner/Operator/Checker。
- Observation、Actuation、Transport 可独立替换和组合。
- 能力缺失是第一类状态，不需要伪造实现。
- 坐标、权限、会话、动作语义和时间戳有明确契约。
- 设备 ACK 与界面/业务验证分离。
- Reflex Skill 保持高频和确定性，同时复用通用设备 I/O Port。
- 数据/遥测/记忆边界清晰，失败可以解释、定位和回放。

