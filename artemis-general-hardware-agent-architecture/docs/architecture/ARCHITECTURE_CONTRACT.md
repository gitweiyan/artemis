# ARTEMIS 通用硬件操控 Agent 架构契约

> 文档类型：规范性架构契约（Normative）  
> 目标：将 ARTEMIS 从 Android 优先的移动 UI 自动化工程，渐进演进为可接入 iOS、Android、macOS、Windows/Linux 桌面、智能电视及其他交互式设备的通用硬件操控 Agent 平台。  
> 适用对象：ARTEMIS 核心代码、设备适配器、平台插件、固件工程、开发者和 Coding Agent。  
> 状态：目标架构基线；落地顺序以 `REFACTOR_EXECUTION_PLAN.md` 为准。

本文件中的 **MUST / MUST NOT / SHOULD / SHOULD NOT / MAY** 分别表示强制要求、禁止要求、建议要求、建议禁止和可选要求。实现若需要偏离 MUST/MUST NOT，必须先提交并批准 ADR；不能通过改名、移动文件或增加兼容包装绕过契约。

---

## 0. 产品边界与架构目标

### 0.1 我们构建的是什么

ARTEMIS 是一个**面向可观察、可交互计算设备的通用硬件操控 Agent 平台**。它接收目标和约束，通过一种或多种观察通道理解环境，选择设备具备的输入/控制能力，执行动作并验证结果。

首批目标环境：

- Android 手机 / 平板 / Emulator；
- iOS / iPadOS 设备及模拟器；
- macOS 桌面、窗口和应用；
- Windows / Linux 桌面（架构预留，按实际需求接入）；
- 智能电视、电视盒子及媒体设备。

架构要能表达多种传感器与执行器组合，包括截图、视频帧、Accessibility/UI Tree、窗口信息、OCR/视觉结果、ADB、原生键鼠事件、USB HID、串口桥接、Bluetooth、HDMI-CEC、IR、厂商 API 等。

### 0.2 明确的范围边界

“通用硬件操控”在本项目第一阶段指**人机交互类数字设备**：其状态可以通过屏幕、语义树、日志或其他数字观测获得，其操作通过输入设备、系统接口或厂商协议完成。

本契约不自动授权把 ARTEMIS 用于工业控制、车辆控制、医疗设备、无人机、机械臂、强动力执行器或其他涉及人身/财产安全的物理控制。此类场景需要独立的安全架构、硬件联锁、风险分析、实时性保证和认证，不得仅复用 UI Agent 的 Action 接口后直接接入。

### 0.3 核心目标

1. Agent 决策层不依赖操作系统、设备品牌、连接方式和具体驱动。
2. Observation、Perception、Action、Transport、Firmware 分层，各自可独立替换。
3. 设备通过能力声明、能力验证和 Platform Profile 被接入，而不是在 Operator 中增加平台分支。
4. Android 现有能力作为第一兼容平台保留，以回归测试证明抽象有效。
5. iOS、macOS、TV 通过组合可复用适配器接入，不复制 Planner/Operator/Checker。
6. 所有动作和状态变化可追踪、可重放、可诊断；命令发出不等于目标达成。
7. 允许设备只有观察、只有控制或同时具备两者；不强迫所有设备都实现统一的截图和触摸接口。

---

## 1. 架构总览：五层运行架构 + 横向基础设施

### L1 — Access & Integration（访问与集成层）

负责外部系统如何提交任务、查看状态和取得结果。

包括：MCP Server、CLI、HTTP/Web API、Python SDK、Web Console、IDE 集成、Webhook/Notifier。

MUST：
- 将请求转换为稳定的 `TaskRequest` / Application Service 调用；
- 负责输入校验、身份/权限边界、任务提交和结果呈现；
- 通过 Device Registry / Application Service 查询可用设备，不直接操作底层驱动。

MUST NOT：
- 直接打开 ADB、UVC、USB、串口、BLE 或厂商设备连接；
- 编码 HID Report 或设备专用命令；
- 在 MCP Tool、CLI Command 或 Web Handler 中实现 Agent 推理与平台控制逻辑。

### L2 — Agent & Orchestration（Agent 与编排层）

负责理解用户目标、形成计划、选择工具、管理任务生命周期和组织多轮执行。

包括：Planner、Operator、Checker、Summarizer、Diagnoser 等 Agent；Flash/Pro Runner；LangGraph/Graph；任务状态与编排策略。

MUST：
- 以目标、约束、可观测状态、能力目录和动作结果为输入；
- 通过平台无关的 Tool / Application / Interaction API 工作；
- 根据状态证据决定下一步，并在任务结束时判断目标是否达成。

MUST NOT：
- 写 `if platform == "ios"`、`if device == "tv"` 等设备策略分支来决定 HID/ADB/CEC 细节；
- import ADB、uiautomator2、UVC SDK、pyserial、pyusb、TinyUSB 固件代码或厂商 SDK；
- 将 transport ACK、固件 ACK 或输入事件发送成功等同于任务完成。

### L3 — Interaction Intelligence（交互智能层）

负责把原始观测转换为可供 Agent 使用的状态，并把 Agent 的语义意图转为经策略检查的标准交互命令，再通过 Port 请求设备执行和验证。

主要子域：

- **Perception**：图像/视频预处理、OCR、Accessibility/UI Tree 解析、窗口/控件解析、多源融合；
- **Grounding**：语言目标与屏幕/窗口/场景元素关联，输出带证据和坐标空间的目标；
- **Action Model & Planner**：定义标准动作意图、拆分复合动作、解析动作参数；
- **Policy & Safety**：前置条件、动作风险等级、用户确认、节流、动作 Burst 策略；
- **Execution Coordinator**：执行编排、超时、重试、结果分类、Incident；
- **Verification**：动作后状态变化检查和任务目标验收的证据接口。

MUST：
- 操作平台无关的 `Observation`、`PerceivedState`、`GroundedTarget`、`ActionIntent` 和 `ActionCommand`；
- 只通过 L4 提供的稳定 Port 请求设备观察/执行；
- 保留来源、时间、坐标空间、置信度和证据引用；
- 把动作派发结果与环境状态验证分开。

MUST NOT：
- 直接打开摄像头、UVC、ADB 或串口；
- 直接生成某一固件的 HID Report 或 CEC/IR 波形；
- 假设所有平台都有相同的 Accessibility Tree、触摸、鼠标、键盘或窗口模型。

### L4 — Device Integration Runtime（设备集成运行时）

负责发现、描述、连接、管理和操作实际设备；承载可复用适配器、平台组合配置和固件桥接。

主要子域：

- Device Registry / Discovery；
- Device Descriptor / Capability Registry / Capability Resolver；
- Device Session、资源租约、并发锁、健康检查和生命周期；
- Observation Adapters（截图、UVC、ADB screencap、ScreenCaptureKit、HDMI capture、Accessibility 等）；
- Actuation Adapters（ADB input、原生键鼠、HID、触控、遥控器、CEC、IR、厂商 API 等）；
- Transport Adapters（USB、Serial/CDC、BLE、TCP/IP 等）；
- Platform Profiles（Android、iOS、macOS、TV 等组合描述）；
- Host-side Firmware Bridge 和固件版本/协议协商。

MUST：
- 实现 L3 定义/使用的 Device Ports；
- 按能力声明和已验证能力提供可执行操作；
- 负责设备资源生命周期、重连、超时、错误映射及安全释放；
- 将平台差异封装在 Adapter/Profile 内。

MUST NOT：
- 依赖 Planner、Operator、Checker、Prompt、LangGraph State 或 MCP Server；
- 理解自然语言业务任务并自行规划任务；
- 将某个具体设备的能力伪装成所有设备都有的通用能力。

### L5 — Target & Host Environment（目标与宿主环境）

运行 ARTEMIS 或被 ARTEMIS 控制的外部系统，包括：Android/iOS/macOS/Windows/Linux/TV OS、目标应用、窗口管理器、Accessibility/Input Stack、USB/HID Stack、外接硬件、网络服务和真实设备行为。

L5 不是 ARTEMIS Python Package 的一个子目录。设备侧 Helper、ESP32 固件等属于 L4 的部署工件，但它们与 L5 环境交互。

---

## 2. 横向基础设施（不强行归入五层）

| 模块 | 责任 | 不负责什么 |
|---|---|---|
| `core/contracts` | 稳定 DTO、Ports、枚举、错误码、ID、协议版本 | 不放 Agent 流程、平台驱动实现 |
| `model_runtime` / `llm` | 模型 Provider、路由、重试、限流、结构化输出适配 | 不放 Planner/Operator 的任务策略和 Prompt 业务逻辑 |
| `memory` | Transcript、摘要、历史检索、Agent 上下文视图 | 不拥有设备句柄，不作为原始证据唯一存储 |
| `observability` / `data_engine` | Event、Trace、截图/视频引用、动作记录、指标、回放 | 不决定 Agent 下一步动作 |
| `config` | 配置 Schema、加载、校验和 Profile 配置 | 不执行设备操作，不包含隐式业务流程 |
| `core` / `utils` | 稳定公共类型与真正通用的工具 | 不作为业务代码的任意收容目录 |

横向模块通过接口服务于一个或多个层。它们可以有依赖方向约束，但不能为了“看起来五层完整”而被强塞到 L2 或 L3。

---

## 3. 目标代码组织

这是目标逻辑组织，不要求一次性按目录搬迁。保留现有公开 Python import 和 CLI/MCP 入口的兼容性，先建立边界，再逐步迁移实现。

```text
repo-root/
├── AGENTS.md
├── artemis/                              # 保留现有 Python 包名
│   ├── interfaces/                       # L1：CLI/API 等入口适配
│   ├── agents/                           # L2：Planner/Operator/Checker 等
│   ├── orchestration/                    # L2：Flash/Pro/Graph/Task flow
│   │
│   ├── interaction/                      # L3：交互智能能力
│   │   ├── perception/
│   │   │   ├── image/
│   │   │   ├── video/
│   │   │   ├── ocr/
│   │   │   ├── accessibility/
│   │   │   ├── window_tree/
│   │   │   └── fusion/
│   │   ├── grounding/
│   │   ├── actions/
│   │   │   ├── intents/
│   │   │   ├── planning/
│   │   │   ├── policy/
│   │   │   └── execution/
│   │   ├── verification/
│   │   └── tools/                        # 可供 Agent 调用的能力门面
│   │
│   ├── device_runtime/                   # L4：设备注册、发现、会话和能力路由
│   │   ├── registry/
│   │   ├── discovery/
│   │   ├── sessions/
│   │   ├── capabilities/
│   │   └── health/
│   │
│   ├── adapters/                         # L4：可组合适配器与平台插件
│   │   ├── observation/
│   │   │   ├── adb/
│   │   │   ├── uvc/
│   │   │   ├── screen_capture_kit/
│   │   │   ├── hdmi_capture/
│   │   │   └── accessibility/
│   │   ├── actuation/
│   │   │   ├── adb_input/
│   │   │   ├── native_desktop_input/
│   │   │   ├── hid/
│   │   │   ├── tv_remote/
│   │   │   ├── hdmi_cec/
│   │   │   └── infrared/
│   │   ├── transport/
│   │   │   ├── usb/
│   │   │   ├── serial/
│   │   │   ├── bluetooth/
│   │   │   └── network/
│   │   └── platforms/                    # 只做组合、profile 和平台注册
│   │       ├── android/
│   │       ├── ios/
│   │       ├── macos/
│   │       └── tv/
│   │
│   ├── core/
│   │   └── contracts/                    # Ports / DTO / error codes
│   ├── model_runtime/                    # 可逐步承接现有 llm/services
│   ├── memory/
│   ├── observability/                    # 可逐步承接 data_engine/trace
│   ├── config/
│   └── utils/
│
├── firmware/                             # 非 Python Adapter；独立构建和发布
│   └── esp32/
│       ├── protocol/
│       ├── hid_descriptors/
│       ├── hid_reports/
│       ├── safety/
│       └── tests/
│
├── mcp_server/                           # L1：对外 MCP Server
├── apps/                                 # L1：管理控制台 / Web UI
├── packages/                             # L1：SDK
├── third_party/                          # 上游依赖，保留来源和许可证
└── tests/
    ├── architecture/
    ├── contract/
    ├── adapter/
    ├── integration/
    ├── platform/
    └── hardware/
```

### 3.1 关键结构原则

- `platforms/<platform>/` 是**组合配置**，不是把所有平台实现都塞进一个目录。它声明某个平台如何组合观察源、输入执行器、Transport、权限要求和能力验证规则。
- `adapters/observation/`、`actuation/`、`transport/` 放可复用的机制实现。UVC、ESP32 HID、Serial、CEC 等不属于某个 OS 的私有实现。
- 平台原生 API 的实现可以放在 `adapters/platforms/<platform>/` 下；若可复用，也可以放到 `observation/` 或 `actuation/` 的通用适配器目录。
- `firmware/` 独立于 Python 包构建。Host Adapter 负责和固件通信，固件只负责协议与硬件报告，不承载 Agent。
- 一个设备可以组合多个 Observation Adapter 和多个 Actuation Adapter；不要求一对一。
- 同一类能力可以由多个 Adapter 提供，例如 Android 的截图可来自 ADB 或 scrcpy，iOS 的观察可来自 UVC 或模拟器接口。
- 不要求所有平台都具有相同能力；差异通过 `CapabilitySet` 和 Profile 明确表达。

---

## 4. 现有 ARTEMIS 模块的目标归属与迁移原则

以下是逻辑迁移映射，不是要求对现有代码按名称自动搬迁。具体源文件和依赖必须在 Phase 0 通过真实 import/call graph 核对。

| 现有目录/能力 | 目标归属 | 迁移原则 |
|---|---|---|
| `artemis/agents/` | L2 `agents/` | 保留 Agent 角色；Explorer 中可复用图像处理能力逐步抽到 L3 |
| `artemis/graph/` | L2 `orchestration/` | 保留原图和 State 行为，先加兼容导入再迁移 |
| Flash Runner | L2 `orchestration/flash/` | 保留快速反应式执行语义，不绑定 Android 控制实现 |
| `artemis/tools/` | L3 `interaction/tools/` 或应用工具门面 | Tool 仅适配 Agent 输入输出，真实能力下沉到 L3/L4 |
| `artemis/mcp/observation*` | 按职责拆到 L3 Perception 与 L4 Observation Adapter | 原始设备采集是 L4，状态融合/语义构建是 L3 |
| `artemis/mcp/action_executor*` | L3 `interaction/actions/execution/` | 负责动作调度和结果归一化，不拥有设备驱动 |
| `artemis/mcp/actuators/` | L4 `adapters/actuation/` | 设备/协议细节留在 Adapter |
| `artemis/controllers/` | L4 `device_runtime/` 或 Adapter Facade | 统一 Controller 可以逐步转为 Session/Port 的兼容门面 |
| `artemis/drivers/` | L4 `adapters/` | 按 observation/actuation/transport/platform 分类，而不是全部堆在 drivers |
| `artemis/runtime/` | 拆入 orchestration、device_runtime、observability | Task Runtime、Device Session、Trace Store 分离；保留旧路径兼容层 |
| `artemis/memory/` | 横向 `memory/` | 继续服务多 Agent，不复制到各层 |
| `artemis/data_engine/`、Trace | 横向 `observability/` | 原始证据和执行回放独立于 Agent 上下文记忆 |
| `artemis/llm/`、`artemis/services/` | 横向 `model_runtime/` 或领域服务 | 逐文件分类，不将所有 services 机械归并 |
| `artemis/interfaces/` | L1 `interfaces/` | CLI/API 作为入口，调用 Application Service |
| `mcp_server/` | L1 | 保留独立服务；不得直接 import 具体设备驱动 |
| `apps/admin_console/` | L1 | 管理、查看、回放通过 API/Service，不直接控制设备句柄 |
| `packages/artemis-client/` | L1 SDK | 保持客户端轻量和公共 API 兼容 |
| `third_party/mobile_use/` | 第三方边界 | 不直接改上游代码；通过适配层包装并记录版本/License |
| `config/` | 横向 Config | 保留集中配置体验，内部 Schema 按领域拆分 |

---

## 5. 核心领域模型与接口契约

### 5.1 必须区分的对象

- **Device**：物理或虚拟设备实例，例如一台 iPhone、一台 Mac、一台电视或 Android Emulator。
- **Target**：本次任务要操作的逻辑对象，例如整台设备、一个 Display、Window、App、页面或媒体输入源。
- **Platform Profile**：平台能力和 Adapter 的组合描述，例如 `macos_desktop_v1`。
- **Device Session**：一次连接/执行会话，绑定 Device、Target、Profile、Adapter 实例和生命周期。
- **Observation**：一个观察样本或一组同步/异步样本，可以是图像、语义树、窗口列表、音频、日志、状态数据等。
- **Perceived State**：L3 将一个或多个 Observation 解释后形成的状态及证据。
- **Action Intent**：Agent 表达的目标操作意图，不含 USB/ADB/HID 等设备实现细节。
- **Action Command**：L3 根据目标和能力解析出的标准交互动作。
- **Adapter Command**：L4 内部命令，仅存在于对应 Adapter/Transport 边界，不泄漏给 Agent。
- **Action Receipt**：设备命令执行链的回执。
- **Verification Result**：动作效果或业务目标的独立验证结果。

### 5.2 标准 Ports

接口放在 `core/contracts/ports/`，实现放在 L4 Adapter。以下是语义示意，签名可以适配项目的 async/sync 技术栈，但不得改变职责。

```python
from typing import Protocol, Sequence

class ObservationPort(Protocol):
    async def open(self, session: "DeviceSessionSpec") -> None: ...
    async def observe(self, request: "ObservationRequest") -> Sequence["ObservationSample"]: ...
    async def close(self) -> None: ...

class ActuationPort(Protocol):
    async def get_capabilities(self) -> "CapabilitySet": ...
    async def execute(self, command: "ActionCommand") -> "ActionReceipt": ...
    async def release_all(self, reason: str) -> None: ...
    async def close(self) -> None: ...

class DeviceProvider(Protocol):
    async def discover(self) -> Sequence["DeviceDescriptor"]: ...
    async def create_session(self, spec: "DeviceSessionSpec") -> "DeviceSession": ...

class DiagnosticPort(Protocol):
    async def health(self) -> "HealthReport": ...
    async def diagnose(self) -> "DiagnosticReport": ...
```

不是每个 Adapter 都必须同时实现 Observation 和 Actuation。单一职责 Adapter 可以分别实现一个 Port；`DeviceSession` 组合多个 Port。

### 5.3 Observation 数据契约

`ObservationSample` 至少包含：

- `sample_id`、`device_id`、`session_id`、`source_id`；
- `modality`：`image`、`video_frame`、`accessibility_tree`、`window_tree`、`text`、`audio`、`log`、`system_state` 等；
- `captured_at` 和明确的 clock/source metadata；
- `payload` 或不可变 `artifact_ref`；
- 可选 `coordinate_space`、`display_id`、`window_id`、`orientation`、`transform_id`；
- `quality` / `freshness` / `availability` 信息。

规则：

- 不得把旧帧伪装成新帧；过期样本必须标记 stale。
- 原始采集和高阶感知分开：L4 采集/解码，L3 做 OCR、视觉增强、透视校正、树解析和多源融合。
- 多源采样可能存在时间差，融合结果必须保留各自时间戳，不能无依据宣称完全同步。
- 对大图像/视频优先传引用或 artifact handle，避免把大字节对象重复塞入 Agent Transcript。
- `ObservationSample` 不应只用 `screenshot` 命名，以免把未来非视觉传感器排除在模型之外。

### 5.4 Perceived State 与 Grounded Target

L3 可以提供平台无关的 UI/交互状态模型，但不能假设所有环境都是手机 UI。

建议 `PerceivedState` 统一承载：

- `visual_regions`：视觉区域、对象、边界框；
- `semantic_nodes`：Accessibility/UI/DOM/Window/TV 菜单等语义节点；
- `text_spans`：OCR 或原生文本；
- `focus_state`：当前焦点、选中项、活动窗口/输入框；
- `navigation_context`：可见页面、路由或层级信息；
- `evidence_refs`：支持判断的 Observation/Trace 引用。

`GroundedTarget` 必须带 `target_ref`、定位方法、置信度、证据引用和坐标空间（若有坐标）。元素索引只在对应 session/state revision 内有效，不得跨页面或跨状态复用。

### 5.5 Action Intent / Action Command

Agent 输出语义动作，不输出设备协议包。标准动作可按能力逐步扩展，例如：

- `activate(target)`、`select(target)`、`focus(target)`；
- `pointer_move(point)`、`pointer_click(point, button)`；
- `key_press(key)`、`key_chord(keys)`、`text_input(text)`；
- `touch_tap(point)`、`touch_gesture(path)`、`long_press(point)`；
- `scroll(direction, amount, target)`；
- `remote_key(key)`、`media_control(command)`；
- `wait(condition, timeout)`、`launch(target)`（仅在 Profile 声明支持时）。

要求：

- 动作类型必须明确语义，不能用含糊的 `click` 同时代表触屏 Tap、鼠标 Click 和遥控器 Select。
- Action Command 应包含 `action_id`、`target_id`、`session_id`、deadline/timeout、参数和可选 `expected_effect`。
- 坐标参数必须标注坐标空间、原点、单位、范围和 transform/version。
- 复合动作可以被 L3 分解；是否允许 Burst 由策略决定，不得由底层 Adapter 静默改变语义。
- 设备不支持某动作时必须返回 `UNSUPPORTED_CAPABILITY`，不能悄悄换一种可能改变用户意图的输入方式。

### 5.6 Action Receipt 与 Verification 分离

至少区分以下阶段：

1. `proposed`：Agent 提出意图；
2. `policy_approved`：L3 安全策略通过；
3. `dispatched`：L4 已派发命令；
4. `transport_acknowledged`：Host/Transport 确认收到；
5. `adapter_completed`：Adapter/固件完成其定义的动作；
6. `effect_observed`：后续 Observation 检测到效果；
7. `goal_verified` / `goal_failed`：Checker 或 Verification 逻辑判定目标结果。

每个 Adapter 只对自己能证明的阶段负责。固件 `report_sent` 不等于 OS 输入栈接受，OS 接受不等于 App 状态改变，App 状态改变不等于用户目标达成。

---

## 6. Capability 与 Platform Profile 契约

### 6.1 能力模型

每个设备/会话必须暴露能力集合，至少区分：

- `supported`：Adapter 声明支持；
- `available`：当前设备/权限/连接状态下可使用；
- `verified`：已在指定设备型号、OS 版本、固件版本或运行环境中通过测试；
- `unavailable`：当前无法使用；
- `unknown`：尚未探测或没有证据。

能力项应表达 `capability_id`、版本、参数范围、前置条件、权限要求、风险等级、证据/验证环境和来源 Adapter。

禁止把配置中“计划支持”的功能写成 `verified`。Agent 只能选择 `available` 且满足策略条件的能力。

### 6.2 Profile 只做组合，不承载 Agent 逻辑

Profile 负责声明：

- 支持的平台/设备类型与匹配条件；
- Observation Adapter 列表及优先级；
- Actuation Adapter 列表及能力；
- Transport 和固件依赖；
- 权限/授权检查；
- 坐标/显示/窗口约定；
- Capability 验证状态与已知限制；
- 默认健康检查和恢复策略。

Profile MUST NOT 包含 Planner Prompt、Operator 分支、任务工作流或业务规则。

示例（仅表达组合方式，不表示所有能力已经实现）：

```yaml
profile_id: ios_external_hid_v1
platform: ios
observation:
  - adapter: uvc
actuation:
  - adapter: esp32_hid
transport:
  - adapter: serial_cdc
capabilities:
  - pointer.relative
  - keyboard.text_input
  - touch.digitizer
  # 每项 verified 状态须由真实设备测试维护
known_constraints:
  - "HID descriptor/report 被主机接受不代表所有 iOS 交互语义可用"
```

另一个 Profile 可以是：

```yaml
profile_id: macos_native_v1
platform: macos
observation:
  - adapter: screen_capture_kit
  - adapter: accessibility_api
actuation:
  - adapter: native_desktop_input
capabilities:
  - display.capture
  - accessibility.tree
  - pointer.absolute
  - keyboard.text_input
```

TV Profile 可以组合 HDMI Capture、Android TV ADB、HDMI-CEC、Bluetooth Remote 或 IR；具体组合取决于电视型号及可验证能力。

### 6.3 平台能力差异不可被抹平

- iOS 外部 HID 不等于原生任意触控注入；必须逐机型/OS 验证。
- macOS 原生 API 受系统权限、TCC、输入监控和屏幕录制授权影响；授权缺失必须明确报告。
- Android 的 ADB、Accessibility Helper、UIAutomator2 能力应保留为 Android Profile 的实现细节。
- TV 可能只有遥控器导航、CEC 电源/输入切换，未必有完整像素级触控或 Accessibility Tree。
- 无屏幕设备可以用状态 API、日志、传感器或音频作为 Observation；平台不得因为缺少截图被判定为不兼容。

---

## 7. 坐标、时间与状态新鲜度

### 7.1 坐标空间

不得存在无类型的裸坐标。至少区分：

- `frame_px`：捕获帧像素；
- `display_px`：显示物理像素；
- `screen_normalized`：相对某个 Display/Viewport 的归一化坐标；
- `logical_point`：平台逻辑点；
- `window_local`：窗口局部坐标；
- `adapter_native`：特定 Adapter 的原生坐标/逻辑范围；
- `hid_logical`：HID Descriptor 定义的逻辑范围。

坐标转换只能经 `CoordinateTransform/CoordinateMapper` 完成，且必须携带 `transform_id` 和版本。分辨率、方向、缩放、裁剪、窗口布局、透视校正或显示器变化后，旧 transform 必须失效或重新验证。

不得默认 `frame_px == screen_px == HID logical`。无可靠映射时，应拒绝执行或要求重新标定。

### 7.2 时间与新鲜度

- 同一 Host 进程内的持续时间和超时优先使用 monotonic clock。
- 跨进程、设备固件或网络时钟必须记录 clock source/offset/uncertainty；不得直接比较不兼容时钟。
- Observation 必须标记采集时间和 freshness；动作前置校验必须确认依据没有过期。
- 关键动作记录 action_id、sample_id、session_id、adapter_id 和关联 Trace ID。

### 7.3 State Revision

每次观察或重要环境变化可推进 `state_revision`。GroundedTarget 和元素索引绑定其来源 revision；状态变化后，旧定位应重新验证，不能无条件继续执行。

---

## 8. 生命周期、并发与错误处理

### 8.1 Device Session

一个 Session 组合一个 Target、一个 Device/Profile 和若干独立 Adapter。观察通道和控制通道可以分别连接、失败、重连和关闭。

- 同一物理执行器默认串行；只有 Adapter 明确支持并发/多指/多通道时才允许并行。
- 设备资源通过 Session/Lease 管理，不允许 Agent 直接持有设备句柄。
- Session 关闭、任务取消、异常和进程退出时必须执行 best-effort cleanup 和 `release_all`。
- 重连后必须更新 session/boot identity，旧命令、旧目标和旧坐标不能跨 session 重放。

### 8.2 错误类型

至少区分：

- `DEVICE_NOT_FOUND` / `DEVICE_BUSY` / `SESSION_LOST`；
- `PERMISSION_DENIED` / `AUTH_REQUIRED`；
- `CAPABILITY_UNAVAILABLE` / `UNSUPPORTED_CAPABILITY`；
- `OBSERVATION_TIMEOUT` / `STALE_OBSERVATION` / `OBSERVATION_INVALID`；
- `GROUNDING_FAILED` / `PRECONDITION_FAILED`；
- `TRANSPORT_ERROR` / `PROTOCOL_MISMATCH` / `FIRMWARE_ERROR`；
- `ACTION_TIMEOUT` / `ACTION_REJECTED` / `ACTION_PARTIAL`；
- `VERIFICATION_FAILED` / `GOAL_NOT_REACHED`。

错误结果应包含可重试性、发生层、关联 ID 和可诊断证据。不要只返回 `success=False` 和自由文本。

### 8.3 重试与幂等

- 只有明确幂等或可查询先前结果的命令才可自动重试。
- 非幂等命令（例如发送文本、提交表单、购买、删除、切换状态）不得因 ACK 丢失而盲目重发。
- Host-Firmware 协议必须有 `session_id`、序列号/command_id、deadline 和重复命令处理规则。
- 超时后必须先查询动作状态或重新观察，再决定恢复策略。

---

## 9. 安全与权限

1. Adapter 只申请完成其职责所需的最小权限。
2. 高风险/不可逆操作必须经过 L3 Policy；必要时要求用户明确确认。
3. 涉及购买、支付、发消息、删除数据、账号安全、系统重置等操作，不能仅因点击动作可执行就自动放行。
4. 固件和输入 Adapter 必须提供可恢复的 release/reset 行为；断连或 watchdog 到期不得遗留按键、按钮或触点按下状态。
5. 不支持的能力必须拒绝，不得静默降级到含义不同的输入方式。
6. Trace 中的凭据、个人内容和屏幕敏感信息应遵循数据最小化、脱敏和保留策略。
7. 新增能对外部物理世界造成影响的 Adapter，必须先做独立安全评审；不得仅靠本契约中的通用 UI Action 接口上线。

---

## 10. Agent / 开发者必须遵守的依赖规则

### 10.1 允许的依赖

```text
L1 Access / Interfaces
      ↓ Application Services / Task API
L2 Agents / Orchestration
      ↓ platform-neutral Interaction API
L3 Perception / Grounding / Actions / Verification
      ↓ Core Ports & Contracts
L4 Device Runtime / Adapters / Firmware Bridge
      ↓ OS APIs / Transports / Hardware
L5 Target & Host Environment
```

L4 实现 `core/contracts` 中定义的 Port。L3 通过 Port 调用设备能力，不 import L4 具体实现。Composition Root/Plugin Registry 可以在启动时装配具体实现，但这种装配代码必须位于明确的 bootstrap/wiring 模块，不能扩散到 Agent 和业务逻辑。

横向基础设施通过抽象接口按需使用：

- Agent/Orchestration 使用 Model Runtime、Memory、Observability；
- L3 使用 Core Contracts、Observability、Perception 配置；
- L4 使用 Core Contracts、Config、Observability；
- L1 使用 Application Service、Config 和只读状态服务。

### 10.2 绝对禁止的反向依赖

- `agents/**` → `adapters/**`、`firmware/**`、`drivers/**`、具体 OS SDK；
- `interaction/perception/**` → `adapters/**` 或设备句柄；
- `interaction/actions/**` → 具体 ADB/USB/Serial/ESP32/CEC 实现；
- `device_runtime/**`、`adapters/**` → `agents/**`、`orchestration/**`、Prompt/Graph State；
- `firmware/**` → Python Agent、MCP Server、LLM 或任务计划；
- `core/contracts/**` → 任意业务实现；
- `mcp_server/**`、`apps/**`、`interfaces/**` → 具体 Adapter 实现。

`utils/`、`common/`、`helpers/` 等目录名不能用来规避依赖约束。按真实 import 和职责检查。

---

## 11. 测试与兼容性契约

每个 Adapter 至少应通过：

1. **Port Contract Tests**：open/close、超时、错误映射、能力查询、资源释放；
2. **Capability Tests**：声明能力、可用能力和已验证能力一致；
3. **Lifecycle Tests**：断连、重连、取消、重复关闭、并发访问；
4. **Action Semantics Tests**：输入参数和动作语义没有被静默改变；
5. **Observability Tests**：所有关键命令可关联 session/action/trace；
6. **Hardware/Platform Tests**：针对实际机型、OS、固件、权限和连接拓扑的实测。

平台 Profile 必须声明测试矩阵和已知限制。不能用 Mock 测试替代真实硬件验证，也不能把单一设备测试结果外推为整个 OS 的能力保证。

对现有 ARTEMIS：

- 保留 CLI、MCP、SDK 和 Web Console 的现有调用兼容性，除非有版本化迁移方案；
- 保留 Flash/Pro 主要运行语义；
- Android 现有任务集和设备流程要进入回归测试；
- 旧路径可以暂时作为 Facade/Re-export，但必须有迁移期限和调用方清单；
- 删除旧实现前必须完成等价测试、文档与回滚准备。

---

## 12. ADR 与契约变更规则

以下变更必须提交 Architecture Decision Record（ADR）：

- 修改五层职责或依赖方向；
- 新增/删除核心 Port 或改变 DTO 语义；
- 新增平台 Profile 或新的 Adapter 类别；
- 改变 Action Receipt / Verification 状态语义；
- 改变坐标空间、时钟或 session identity 规则；
- 引入具有外部影响或安全风险的执行器；
- 删除兼容接口或改变公共 API。

ADR 至少说明：背景、决策、备选方案、影响目录、迁移/回滚方案、测试证据和安全影响。

任何 Agent 不得为了让当前实现通过测试而自行降低契约要求、删除架构检查或把 Adapter 具体依赖引入上层。

---

## 13. 一页式架构判断规则

每次新增代码先回答：

1. 它是在决定“做什么”（L2），理解“看到了什么/如何交互”（L3），还是具体“怎样连接和驱动设备”（L4）？
2. 它能否被另一个平台复用？如果能，放入通用 Adapter，而不是平台目录。
3. 它是否仅负责组合某个平台所需能力？如果是，放进 Platform Profile。
4. 它是否保存跨 Agent 的上下文或原始证据？分别归 Memory 或 Observability。
5. 上层能否只依赖 Port/DTO？如果不能，先补接口，不要直接 import 具体实现。
6. 该能力是否经过真实设备验证？若没有，状态只能是 `unknown` / `advertised`，不能标为 `verified`。

**最终目标不是五层目录整齐，而是核心 Agent 稳定、设备能力可组合、平台接入可替换、执行过程可验证。**
