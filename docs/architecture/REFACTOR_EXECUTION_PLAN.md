# ARTEMIS 通用硬件操控 Agent：现状驱动的架构改造执行计划

> 文档类型：实施路线图（Execution Plan）  
> 基线仓库：[`gitweiyan/artemis`](https://github.com/gitweiyan/artemis)  
> 基线分支：`integration/10101205-all-fixes`  
> 代码现状基线 Commit：`1281fc3ddf0d08ec8086d46111c2e6040a02b2d2`（2026-11-08）  
> P0 文档治理 Commit：`e50bed0b6bd8b590d31b0f872bc00a955b217623`（2026-11-09；仅文档变更，不改变代码基线）  
> 配套规范：[`ARCHITECTURE_CONTRACT.md`](ARCHITECTURE_CONTRACT.md)  
> Coding Agent 规则：仓库根目录 `AGENTS.md`  
> 状态：基于上述 commit 的建议执行计划；开始每个批次前必须重新盘点分支状态。

---

## 0. 本计划最重要的前提

**本计划不是从原始 Google ARTEMIS 重新设计，也不是从零建设 iOS 适配。** 基线分支已经落地了多项关键抽象，并且已经把 iOS UVC + ESP32 HID 接入现有 Driver/Agent 执行链。后续工作应当以当前代码为起点，采用增量迁移，避免重做已经存在的能力。

本次改造的目标是：

> 将现有 Android 优先、已扩展 iOS 的 ARTEMIS，逐步收敛为面向可观察、可交互数字设备的通用硬件操控 Agent 平台。Agent Core 不感知设备平台；设备差异由可组合的 Observation、Actuation、Transport Adapter 和 Platform Profile 表达。

首批目标环境为 Android、iOS/iPadOS、macOS、Windows/Linux 桌面和智能电视。这里的“通用硬件操控”限于人机交互类数字设备，不自动扩展为工业控制、机械臂、无人机、车辆、医疗设备或其他安全关键物理控制系统。

### 0.1 现有工作必须保留

- `PerceptionSource`、`ControlSink`、`DeviceSession`、`CapabilitySet`、`ScreenGeometry` 已存在，不得在新目录中再造一套同义接口。
- Android `AndroidAdbDriver` 和现有 Android Helper/UIAutomator 工作流是兼容基线。
- iOS `IOSUvcHidDriver`、UVC 采集、ESP32 HID 控制、指针校准和 `ios-smoke` 已经存在；iOS 是当前第二种设备组合的实证样本，不是未来才开始的工作。
- `artemis/reflex/runtime.py`、`reflex/contract.py`、`reflex/registry.py` 以及外部 `skills/` 构成确定性、高频 Skill 执行通道，不是待删除的旧代码。
- `firmware/esp32-hid/` 是已经存在的独立 ESP-IDF/TinyUSB 固件工程，不应重新生成一套 `firmware/esp32/` 替代它。
- `artemis/safety/` 已有 XML、Pixel、Pointer 等可复用安全原语；后续工作是统一策略入口和调用边界，而不是重写这些算法。

### 0.2 本计划不要求立即做的事情

- 不要求一次性重命名或搬迁整个 `artemis/`。
- 不要求现在就引入动态 Python 插件市场、Entry Points 或复杂依赖注入框架。第一阶段静态 Registry + Builder 足够。
- 不要求把所有 `reflex/` 代码迁移到 `perception/` 或 `control/`；必须保留 Reflex 的高频、无 LLM、有限时执行语义。
- 不要求先实现 macOS 和 TV，再回头抽象 Core。应先稳定 Android/iOS 共用的设备契约，再增加新平台。
- 不要求让所有设备都实现 UI Tree、Touch、Mouse、Keyboard、Shell 或 App Launch。设备能力必须允许缺失。

---

## 1. 基线快照：当前仓库已经有什么

### 1.1 已落地的主要结构

| 当前路径 | 当前职责 / 已有能力 | 状态判断 | 下一步原则 |
|---|---|---|---|
| `artemis/agents/` | Planner、Operator、Checker、Validator、Explorer、Summarizer 等角色 | 已形成 | 保持平台无关；平台知识通过 Profile/Capability 注入 |
| `artemis/graph/` | Pro LangGraph State、节点与执行流 | 已形成 | 保持 Agent 编排职责，不承接设备驱动 |
| `artemis/orchestration/profiles.py` | Flash / Pro / Skill 执行档位选择 | 已形成 | 与设备平台 Profile 分开，避免混淆两个 Profile 概念 |
| `artemis/orchestration/selector.py` | `DeviceProfile` → `Composition` 纯函数，当前选择 app / uvc-hid / camera-hid | 已形成但范围较窄 | 演进为通用能力组合选择，不要在上层引入平台分支 |
| `artemis/perception/base.py` | `Frame`、`Target`、`TextBox`、`UIElement`、`PerceptionSource` | 已形成 | 增加证据与坐标空间契约，保持旧接口兼容 |
| `artemis/perception/` | App Automation、UVC、Camera、Composite、OCR、iOS target adjuster 等 | 部分通用、部分平台特定 | 通用感知管线留在此处；iOS SpringBoard 规则迁往 iOS Profile/扩展 |
| `artemis/control/base.py` | `ActionResult`、`ControlSink` Protocol | 已形成 | 区分派发回执、设备 ACK、可观察效果和业务验收 |
| `artemis/control/` | AppControl、HID、Composite Control Sink | 部分通用、部分硬件/平台策略 | 保留公共接口；硬件协议与平台导航策略逐步下沉 L4 |
| `artemis/devices/session.py` | 多 Perception Source / Control Sink 组合，汇总能力 | 已形成雏形 | 加强生命周期和能力来源；移除通用 Session 上的 iOS 指针对象 |
| `artemis/devices/geometry.py` | Frame、Normalized、Screen 坐标转换 | 已形成但坐标表达并存 | 增加显式空间标识和变换证据，兼容现有 0–1000 与 0–1 表达 |
| `artemis/devices/capabilities.py` | Boolean `CapabilitySet` | 已形成但粒度不足 | 演进为按观察/动作/传输/诊断分域的能力清单及状态 |
| `artemis/drivers/android/` | Android ADB Driver | 已落地 | 作为第一兼容 Profile，逐步从旧 Driver Facade 迁移到 DeviceSession |
| `artemis/drivers/ios/` | IOSUvcHidDriver、Config、Crop、Calibration、Smoke | 已落地 | 从 iOS 专属组合中提取可复用 Adapter；保留 iOS 专属 launcher/gesture policy |
| `artemis/drivers/factory.py` | 根据运行环境创建 Mock / Android / iOS Driver | 已能工作但平台 Builder 写在一个 Factory | 保留 L4 平台分支；逐步拆成注册式 Builder，禁止把平台分支上推到 L2/L3 |
| `artemis/safety/` | Pixel、Pointer、XML 纯安全原语 | 已落地 | 统一调用入口，但保留不同执行模式的成本策略 |
| `artemis/reflex/contract.py`、`runtime.py`、`registry.py` | Skill Contract、有限时 Reflex Loop、Skill Registry | 已形成独立执行通道 | 保留；只迁出其中的硬件 I/O 和可复用感知实现 |
| `firmware/esp32-hid/` | ESP32-S3 ESP-IDF/TinyUSB 复合 HID 固件 | 已落地 | 明确 Host-Firmware 协议版本、能力探测、超时与状态释放 |
| `artemis/platform/` | Linux/macOS/Windows 宿主进程、路径、包管理器抽象 | 已落地 | 明确它是 Host OS PAL，不是被控 macOS/Windows Platform Adapter |
| `artemis/data_engine/` | Task/Step、图片、录像、History Chunk、Trace 数据管理 | 已落地 | 明确权威数据所有权，不与 Telemetry 混成一个大模块 |
| `artemis/telemetry/` | Span、模型调用/工具/设备动作耗时等遥测 | 已落地 | 保留性能与调用链遥测职责，与原始 Step Artifact 区分 |
| `artemis/runtime/trace_store.py` | MCP 后台任务状态文件及并发安全读写 | 已落地 | 明确为后台任务状态 Store，不替代 Data Engine Trace |
| `artemis/mcp/`、`artemis/controllers/` | MCP Action Executor/Observation、UnifiedMobileController 等兼容路径 | 仍是旧执行链重要入口 | 分批迁移到新 DeviceSession/Ports，不能一次性删除 |
| `third_party/mobile_use/` | 上游 Mobile Use 的 Controller、Context、Types、Utilities | 外部兼容依赖 | 保留边界和许可证；逐步减少 Core 对其类型的直接依赖 |

### 1.2 当前真实调用链

#### Android 主链（简化，2026-11-08 核对源码）

```text
CLI / MCP / Admin Console
  -> ArtemisContext
  -> AdbActuator                          [artemis/mcp/actuators/adb.py]
  -> UnifiedMobileController              [artemis/controllers/unified_controller.py]
  -> UnifiedMobileControllerBase          [third_party/mobile_use/controllers/unified_controller.py:46-146]
  -> drivers.factory -> AndroidAdbDriver
  -> ADB / Accessibility Helper / UIAutomator2
  -> Android + Target App
```

说明：

- **动作主链不经过 `DeviceSession`。** `AndroidAdbDriver.session` 是惰性属性（`adb_driver.py:109-122`），只按 `app_session` 形状构造；当前生产路径没有消费方，引用它的是 driver 自身与测试。
- 本仓库的 `controllers/unified_controller.py` 对 `_driver` 只做录像与 `disconnect`；点击/滑动/文本/按键由 `third_party/mobile_use` 的基类直接调用 driver 方法。
- 因此 G5“让新 Session 成为唯一设备边界”的真正改造点在 `third_party/mobile_use/controllers/unified_controller.py`，而不是只改 `artemis/controllers/`。改造时优先在 `artemis` 侧加 Adapter（driver → Ports），避免直接改写 vendored 第三方代码及其边界测试。
- iOS 是当前唯一真正经 `DeviceSession` 派发动作的链路；不要把“Session 已存在”等同于“旧 Driver 链已经退役”。

#### iOS UVC + ESP32 HID 主链（简化）

```text
CLI / MCP / Agent
  -> ArtemisContext
  -> UnifiedMobileController
  -> drivers.factory (_is_ios / IosRuntimeConfig)
  -> UvcCapture / UvcStream          [当前实现位于 reflex/capture.py]
  -> Esp32HidControl                 [当前实现位于 reflex/control.py]
  -> build_ios_uvc_session           [devices/ios_uvc.py]
  -> UvcSource + HidControlSink
  -> PointerSession + SessionTapController [当前位于 reflex/mouse.py]
  -> DeviceSession
  -> IOSUvcHidDriver : BaseDeviceDriver
  -> iPhone USB HID Stack + iOS UI
```

说明：iOS 已经复用 Agent、PerceptionSource、ControlSink、DeviceSession 和 Safety 原语；但 UVC/串口 HID/指针控制仍存在 `reflex/` 依赖，`DeviceSession.pointer` 也泄漏了指针实现细节。下一阶段应该抽离这些依赖，而不是再写一套 iOS Agent。

#### Reflex / Skill 高频执行链

```text
play_reflex Tool
  -> Skill Registry
  -> SkillHandle(parse / decide / to_gestures)
  -> ReflexRuntime
  -> Capture + Control Protocol
  -> 真实设备 / 回放 / Mock
```

Reflex Runtime 的设计有意采用有限时循环、无逐帧 LLM、帧留内存、限制 blind frames 和 control failures。不能为了统一接口而强迫它在每一帧写数据库、调用 Planner 或运行重量级 SafetyNet。

### 1.3 当前可观察到的架构缺口

| ID | 缺口 | 源码锚点 | 风险 / 后果 | 优先级 |
|---|---|---|---|---|
| G1 | `BaseDeviceDriver` 仍是宽接口，含 screen/app/shell/recording 等移动设备语义 | `artemis/drivers/base.py`；`AndroidAdbDriver`；`IOSUvcHidDriver` | 新平台被迫实现不适用方法；Driver 仍是中心抽象 | P0 |
| G2 | `DeviceSession` 带 `pointer: object` 专有状态 | `artemis/devices/session.py` | 通用 Session 被一种相对指针方案污染 | P0 |
| G3 | `CapabilitySet` 是混合 Boolean 集合，Session 用 OR 汇总 | `artemis/devices/capabilities.py`；`devices/session.py` | 无法表达来源、权限、临时不可用、验证状态及能力前置条件 | P0 |
| G4 | 组合选择器只覆盖少数组合，Factory 内直接构建平台硬件对象 | `artemis/orchestration/selector.py`；`artemis/drivers/factory.py` | 增加 macOS/TV 时 Factory 继续膨胀；配置/硬件打开/组合策略交织 | P1 |
| G5 | 新旧设备抽象并存，MCP Observation/Action Executor 仍通过 UnifiedMobileController/Actuator 工作；`DeviceSession` 根本不在 Android 动作路径上 | `artemis/mcp/observation.py`；`mcp/action_executor.py`；`controllers/unified_controller.py`；`third_party/mobile_use/controllers/unified_controller.py:46-146`；`drivers/android/adb_driver.py:109-122` | 新 Session 没有成为唯一稳定设备交互边界；只改 `artemis/controllers/` 不会改变主链 | P0 |
| G6 | `control/hid.py` 混入 iOS Back 手势、键盘滚动等平台策略，并依赖 Reflex 视觉逻辑 | `artemis/control/hid.py` | 通用 HID Adapter 和 iOS UI 导航语义混杂 | P1 |
| G7 | 通用 `perception/` 含 iOS SpringBoard 图标偏移规则；UVC Locator 依赖 Reflex 模块 | `perception/ios_targets.py`；`perception/uvc.py` | 平台策略和检测实现难以独立复用 | P1 |
| G8 | `reflex/` 的执行 Runtime 与设备 I/O 实现同目录 | `reflex/runtime.py`、`reflex/contract.py`、`reflex/capture.py`、`reflex/control.py`、`reflex/mouse.py`、`reflex/vision.py` | 机械搬迁会破坏高频 Skill；不迁移则硬件实现边界不清 | P1 |
| G9 | Safety 原语、Validator Precondition、Pointer Controller 后检存在多处调用路径 | `artemis/safety/`；`agents/validator/`；`reflex/mouse.py` | 策略阈值/Incident 可能漂移；又不能把所有检查强加到 Reflex 每帧 | P1 |
| G10 | Data Engine、Telemetry、Runtime Trace Store 责任相邻但不同 | `data_engine/`；`telemetry/`；`runtime/trace_store.py` | 记录语义容易重复或互相误用 | P1 |
| G11 | `artemis/platform/` 名称容易被误认为目标设备 Platform Layer | `platform/base.py`、`darwin.py` 等 | 把宿主机环境和被控 Mac 混淆 | P2（文档/命名） |
| G12 | 坐标在 0–1000 点坐标、0–1 Box、Frame Pixel、Device Pixel 之间转换 | `devices/geometry.py`；`perception/base.py`；`control/hid.py` | 坐标空间不明确会造成错误点击和不可复现问题 | P0 |
| G13 | 固件命令协议与 Host Adapter 尚未抽象成平台无关 Firmware Bridge | `firmware/esp32-hid/`；`reflex/control.py` | 新输入板/新固件版本接入时耦合串口命令和 iOS 配置 | P1 |
| G14 | iOS 专属知识仍注入 Agent 或通用控制模块 | `agents/ios_shortcuts.py`；`reflex/ios_apps.py`；`control/hid.py` | 后续 macOS/TV 容易沿用平台 if/else 扩散 | P1 |

以上是静态源码审阅发现的边界问题，不代表每一项都已经造成线上故障；进入对应批次前必须用调用方搜索、测试和真实运行证据确认。

---

## 2. 目标架构与迁移边界

以 `ARCHITECTURE_CONTRACT.md` 为规范。整体保持五层运行架构：

1. L1 Access & Integration：CLI、MCP、SDK、API、Console。
2. L2 Agent & Orchestration：Planner、Operator、Checker、Flash、Pro、Skill 选择与任务流程。
3. L3 Interaction Intelligence：Perception、Grounding、Action Semantics、Safety、Verification。
4. L4 Device Integration Runtime：Device Registry、Session、Capability Resolver、Observation/Actuation/Transport Adapter、Platform Profile、Firmware Bridge。
5. L5 Target & Host Environment：被控制设备 OS、App、输入栈、真实硬件及宿主设备。

横向基础设施：Core Contracts、LLM/Model Runtime、Memory、Data Engine/Observability、Config、Host OS PAL。

### 2.1 目标代码组织（逻辑目标，不是一次性搬迁命令）

```text
artemis/
├── agents/                       # L2：保留 Planner / Operator / Checker 等
├── graph/                        # L2：保留 Pro Graph / State
├── orchestration/                # L2：执行档位；逐步将 Device Composition 移交 L4 Registry
│
├── perception/                   # L3：Frame / UI Tree / OCR / Fusion / Grounding
├── control/                      # L3：Action/Control 契约、组合语义与兼容门面
├── safety/                       # L3：可复用安全原语与统一 Action Gate
│
├── devices/                      # L4：Descriptor / Session / Geometry / Capabilities
├── drivers/                      # L4：旧 Driver 兼容层及平台 Driver
├── adapters/                     # L4：逐步引入可复用 Adapter（uvc / esp32_hid / adb / native_input...）
├── platform_profiles/            # L4：android / ios / macos / tv 能力组合和平台扩展
├── device_runtime/               # L4：稳定后再抽 Registry / lifecycle / health / leases
│
├── reflex/                       # 特殊 Skill 执行通道：保留 contract/runtime/registry
├── skills/                       # 确定性 Skill 插件
│
├── memory/                       # 横向：Agent 可用上下文和历史检索
├── data_engine/                  # 横向：Task/Step/Artifact/Replay 权威记录
├── telemetry/                    # 横向：Span/Latency/Token/Device metrics
├── runtime/                      # 现阶段保留；按职责将来拆 Task/Device/Trace Runtime
├── llm/、services/、config/      # 横向基础设施
├── platform/                     # 宿主机 OS PAL；不要当作被控设备插件目录
└── core/、utils/                 # 公共稳定契约 / 无领域依赖工具

firmware/
└── esp32-hid/                    # 现有独立 ESP-IDF/TinyUSB 固件，保留原路径

mcp_server/、apps/、packages/     # L1 接入层
packages/artemis-accessibility-helper/ # Android 侧部署 Helper（L4 部署工件）
third_party/                      # 外部代码，保留来源与许可证
```

### 2.2 不强制改名的原则

- 不要求立即创建 `interaction/` 大目录；现有 `perception/`、`control/`、`safety/` 已有代码和测试，先稳定接口再决定是否整合。
- `devices/` 目前是 DeviceSession/Geometry/Capabilities，不代表它已经是完整 Device Runtime。可渐进增加 Registry/Descriptor/Lease，不要把所有 Driver 塞入 `devices/`。
- `drivers/` 可作为平台兼容入口；可复用的 UVC、ESP32 HID、Serial、USB、Native Input 等能力逐渐抽成 `adapters/`，由 Profile 组合。
- `platform_profiles/` 是被控设备的平台组合；`artemis/platform/` 是 Agent 运行主机的路径/进程/包管理器抽象，两者不得混用。
- `BaseDeviceDriver`、`UnifiedMobileController`、`artemis/mcp` 里的旧入口要先变成兼容 Facade，确认调用方迁移后再删除。

---

## 3. 执行策略：先稳住 Core，再把 Android/iOS 变成两个合规 Profile

目标顺序不是“先做 iOS，再抽象”，因为 iOS 已经接入；应当是：

1. 以当前 Android 和 iOS 两条真实执行链作为回归样本，稳定通用契约。
2. 先让 Android 和 iOS 都经同一套 Device Runtime/Ports 完成观察与动作。
3. 把 iOS 专属知识、UVC/HID 物理实现和固件协议留在 L4 Profile/Adapter。
4. 在 Android/iOS 双平台契约测试稳定后，再接 macOS 与 TV。

**是否真正通用的验收，不是“目录中出现了 macos/ 和 tv/”，而是新增一个 Profile 不需要修改 Planner、Operator、Checker、Flash/Pro Graph 和通用 Action Policy。**

---

## 4. 分阶段实施计划

每个 Phase 可拆成独立 PR。一个 PR 尽量只改变一个主要架构边界；先写/更新测试，再迁移实现。移动文件和改变行为必须分开提交。

## 0.2 P1 决策基线（实施前必须形成 ADR）

以下是本轮架构评审的**推荐决策**，不是已经批准的实现细节。对应 ADR 状态在 `docs/adr/README.md` 中维护；ADR 为 Proposed 时只允许盘点、原型和测试设计，不得开始会改变公共契约的实现。

### D1 — verified Capability 的证据归属与失效

推荐采用“三段式事实来源”：

1. Adapter Manifest 声明静态能力（declared）；运行时 Probe 判断本次 Session 是否可用（available/unavailable）；Contract/Smoke 测试产生可验证证据（verified）。
2. **原始证据与不可变验证记录归 Observability/Data Engine**；Capability Registry 保存当前派生 Snapshot、设备指纹和 evidence reference，不复制大体积截图/日志；Memory 不作为能力证据库。
3. 每条验证证据必须绑定能力 ID 和适用指纹，至少考虑稳定 Device ID、目标 OS/Build、Adapter 版本、固件 Build/Protocol、连接端点身份，以及该能力依赖的权限/配置。
4. 当相关指纹变化、权限撤销、固件/协议或 Adapter 变化时，该能力的旧 `verified` 证据失效并回到 `unknown` / `declared`，等待重新验证；Session 断开只影响本次 `available`，不自动抹掉仍适用于同一指纹的历史验证证据。
5. 过了重新验证窗口应标为 stale/unknown 并触发验证，不得把“时间过期”误报成设备不支持。

**理由：** “verified”是带适用范围的证据结论，不是永久布尔属性；把原始证据放在 Trace/Artifact 体系中才能审计和复现。

### D2 — 设备与共享硬件资源的独占

推荐保留现有 `artemis/runtime/device_lock.py::DeviceExecutionLock`，继续负责“同一逻辑目标设备不可并发执行任务”；不要把它误认为 UVC/串口等 Host 资源锁。

在此基础上增加独立的 Resource Lease 抽象，至少覆盖：

- UVC Capture Device：同一采集卡实例同一时刻只允许一个 Capture Owner；
- ESP32 Serial Port：同一串口端点同一时刻只允许一个 Firmware Bridge Owner；
- 后续可扩展到 Camera、BLE Adapter、CEC Adapter 等真实共享资源。

Lease Key 必须使用稳定资源身份（USB VID/PID/Serial、系统稳定设备 UID、规范化端点），不能用会变化的设备枚举 index。一个 Device Session 可以拥有多个 Lease；多资源按稳定排序原子式获取/失败回滚，按相反顺序释放。复用现有 PID + process-created-at + owner token 的跨进程陈旧 Owner 判断思路，但不要用“给 DeviceExecutionLock 传一个伪 device_id”的方式偷渡资源锁语义。活进程的 Lease 不得仅因 TTL 到期而被另一个任务抢占；进程死亡/明确释放才可回收，心跳用于诊断与监督而非无证据抢锁。

### D3 — 剩余 iOS 分支的归位原则

不直接删除行为，不把平台判断继续留在通用 MCP/Action Executor：

- `mcp/action_executor.py` 的 LocateAnything 坐标补全与 Explorer 快速定位，迁入通用 L3 Grounding/Target Resolution Port；LocateAnything 是可注册的视觉定位实现，不是 iOS 专属能力。
- `mcp/actuators/adb.py` 的 ASCII/pinyin 文本转换迁入 Text Input Adapter/Platform Extension；BACK 的 iOS 左边缘手势与失败提示迁入语义 Navigation Action 的平台映射；App 启动/停止迁入 App Lifecycle Port。
- iOS Extension 负责提供具体策略和提示；Action Executor 只读取可用 Port、Capability 和结构化结果。迁移期间保留旧路径兼容，并用 Android/iOS 回归锁定行为。

### D4 — macOS 与 TV 的排期

Phase 7（macOS）和 Phase 8（TV）先降为 **design-only backlog**：当前只维护能力矩阵、权限/动作语义、Profile 草图和 Mock Contract Fixture，不实现生产 Adapter，不阻塞 Phase 1–6。核心 Port、Capability、Session、Lease 和 Android/iOS 双平台接入稳定后，优先选择 macOS 做第一个真实非移动设备纵向切片；TV 等 Remote/D-pad/CEC/IR 语义契约明确后再排期。

### D5 — 勾选状态

Phase 0 的“事实源收敛”和 Phase 9 的“旧规范标记 Superseded”保留 `[x]`：它们是**已经完成的单项任务**。这不代表 Phase 0 或 Phase 9 整体完成；只有各 Phase 的 Exit Criteria 全部满足后，才能宣布该 Phase 完成。不得为了让计划“看起来未开工”把真实完成的工作改回 `[ ]`。

---

### Phase 0 — 基线重核与架构差距登记（不改业务代码）

**目的：** 让本次改造完全锚定当前分支，而不是使用过期目录清单或推测。

任务：

- [x] 记录分支、HEAD SHA、工作区状态；本计划基线为 `1281fc3`，实施时重新读取（2026-11-09 审计：HEAD `a82d58e`，工作区仅 `config/artemis.jsonc` 有用户未提交改动）。
- [x] 重新运行 `uv run pytest tests/unit -q`、`uv run ruff check` 和项目现有 `scripts/quality_ratchet.py`（结果见 `TEST_BASELINE.md`）。
- [x] 记录当前已知失败与新增失败，不能把旧报告数字当成现在的实测结果（14 failed / 3535 passed / 9 skipped，其中 2 条为本地配置引起，12 条为分支既有回归）。
- [x] 追踪 Android 与 iOS 从 CLI/MCP 进入直到 Driver/DeviceSession/Adapter 的真实调用链（`CURRENT_CALL_FLOWS.md` §3–§4）。
- [x] 追踪 Flash、Pro、Skill/Reflex 三条执行路径，不把它们误归为同一个 Agent 循环（`CURRENT_CALL_FLOWS.md` §5–§7）。
- [x] 输出 import graph，重点检查 `mcp`、`controllers`、`drivers`、`devices`、`control`、`perception`、`reflex`、`safety`、`data_engine`（`IMPORT_DEPENDENCY_MATRIX.md`）。
- [x] 搜索所有 `BaseDeviceDriver`、`UnifiedMobileController`、`DeviceSession.pointer`、`CapabilitySet`、`is_ios_device`、`reflex.capture/control/mouse/vision` 的生产调用方（`BASELINE_2026-11-09.md` §4）。
- [x] 事实源收敛（2026-11-08 完成）：`ARCHITECTURE_CONTRACT.md` 是唯一规范；`docs/artemis-target-architecture.md` 与 2026-11-06 B0–B6 计划已标记 Superseded，只作历史记录，不得回填新决策。后续只需确认 AGENTS 入口与 `docs/ios-rig-playbook.md` 未漂移。

交付物：

- `docs/architecture/BASELINE_2026-11-09.md`
- `docs/architecture/CURRENT_CALL_FLOWS.md`
- `docs/architecture/IMPORT_DEPENDENCY_MATRIX.md`
- `docs/architecture/TEST_BASELINE.md`

**Exit Criteria（已满足）：** 当前测试基线、Android/iOS/Reflex 三条调用链、关键旧接口调用方都有可复现证据。Phase 0 不做大规模重构。

### Phase 1 — 稳定现有 Contracts（拆成四个独立 PR）

**目的：** 将已经存在的 `PerceptionSource` / `ControlSink` / `DeviceSession` 从“可工作抽象”提升为多平台可长期依赖的契约。不要一次性改完所有调用方；每个 PR 必须有自己的 Contract Tests 和兼容边界。

| PR | 主题 | 主要交付 | 依赖 ADR |
|---|---|---|---|
| **PR-1** | Device Identity & Descriptor | 区分 Host、物理设备、逻辑 Target、连接 Endpoint、Profile、Adapter Resource；建立稳定 Device/Resource ID | ADR-003 |
| **PR-2** | Capability Catalog & Evidence | 能力分域、规范 Capability ID、状态/来源/指纹/evidence reference；保留旧 CapabilitySet 转换层 | ADR-001、ADR-008 |
| **PR-3** | Session Lifecycle & Resource Lease | 明确 Session 生命周期与所有权；将 pointer state 从 DeviceSession 移出；保护 UVC/Serial 等共享资源 | ADR-003、ADR-011 |
| **PR-4** | Observation / Geometry / Action Contract | Frame/Target 证据关联、坐标空间、Transform、ActionResult/Receipt 状态语义及契约测试 | ADR-002、ADR-004 |

#### PR-1 — Device Identity & Descriptor

- [x] 定义 `DeviceDescriptor`：区分 Host、物理设备、逻辑 Target、连接 Endpoint、显示 Surface 和 Platform Profile（`artemis/core/identity.py`）。
- [x] 定义稳定的 `device_id`、`target_id`、`endpoint_id`、`resource_id`；同一个 iPhone 可以有 UVC 与 ESP32 两个 Endpoint，但不能因此被误认为两台 Target Device（`make_*_endpoint`、`resolve_device_id`、`ARTEMIS_DEVICE_ALIAS`、`tests/unit/devices/test_descriptors.py`）。
- [x] 将设备发现信息、用户配置和运行时连接句柄分开；Descriptor 不持有 ADB/USB/Serial 句柄（DTO 只有数据；`ctx.device_descriptor` 只读）。
- [x] 先为 Android/iOS/Mock 生成 Descriptor，不改变现有 Driver 选择与动作行为（`artemis/devices/descriptors.py`、`drivers/factory.py` 挂载；`tests/unit/drivers/test_factory_descriptor.py`）。

**PR-1 验收（已满足，2026-11-09）：** 相同 Target 在不同 Observation/Actuation Adapter 组合下身份稳定；不同共享资源拥有独立 Resource ID；无平台 SDK 依赖的单测通过。
证据：`tests/unit/core/test_identity.py`、`tests/unit/devices/test_descriptors.py`、`tests/unit/runtime/test_host_identity.py`、`tests/unit/drivers/test_factory_descriptor.py`、`tests/unit/test_context.py`；`pytest tests/unit -q` 为 14 个既有失败 + 3572 passed（新增 37 个测试，无新增失败）。

> 兼容说明：`DeviceContext` 未迁移（Phase 9）；`DeviceSession` 暂不持有 descriptor（PR-3）；`ARTEMIS_DEVICE_ALIAS` 当前只影响 descriptor 的 `device_id`，运行时锁键对齐留到 Phase 3。

#### PR-2 — Capability Catalog & Evidence

- [x] 将当前混合 Boolean `CapabilitySet` 拆为 Observation、Actuation、Transport、Diagnostics 能力视图；旧字段通过唯一转换层兼容（`artemis/core/capabilities.py`、`artemis/devices/capabilities_compat.py`）。
- [x] 统一使用契约 §5.3 的规范 Capability ID，禁止调用方自行拼写 ID（`CapabilityId` 闭集 + `CATALOG` + `spec()`；未知 ID 抛错）。
- [x] 能力状态区分 `declared`、`available`、`verified`、`unavailable`、`unknown`；状态必须带来源、适用指纹、验证时间和证据引用（`CapabilityRecord`/`CapabilityView`/`CapabilityRegistry`）。
- [x] 采用 D1 推荐的证据所有权：Data Engine/Observability 保存不可变验证记录和 Artifact；Capability Registry 保存派生 Snapshot 与 evidence reference（`SqliteCapabilityEvidenceStore` + `CapabilityArtifactStore`，全局 SQLite，不随 `--traces-path`）。
- [x] 定义 capability-specific fingerprint/invalidation；固件/协议、Adapter、OS/Build、权限或相关配置变化时，只使受影响能力证据失效（`fingerprint_fields`/`capability_fingerprint`；证据按 device + endpoints + fingerprint 查找）。
- [x] 缺少验证证据不得标为 `verified`；没有置信度/探测来源时保留 unknown/null，不伪造数值（manual 不能 verified；无 evidence 保持 declared/available）。

**PR-2 验收（代码与测试已满足，2026-11-09）：** 可以从 Capability Snapshot 追溯到验证证据；指纹变化后旧证据不再支持 verified；Session 断连不会错误覆盖历史验证事实。
证据：`tests/unit/core/test_capability_{catalog,registry}.py`、`tests/unit/data_engine/test_capability_evidence_store.py`、`tests/unit/devices/test_capabilities_compat.py`、`tests/unit/devices/test_session.py`；`pytest tests/unit -q` 为 14 个既有失败 + 3657 passed（新增 49 个测试）。

> 真机证据（2026-11-09，iPhone 14 Pro Max + ESP32-S3）：`ios-smoke --target 设置` 四段门禁全绿，
> 写入 7 条 `verified` evidence（`frame.capture`、`capture.health`、`ocr.read`、`pointer.relative`、
> `pointer.absolute`、`serial.command`、`usb.hid`）；session capability view 在 smoke 结束时 7 项均为 verified。
>
> 剩余：Android/Mock 的显式 `CapabilityManifest` 与探针（iOS 的 UVC/HID 已显式声明）；固件 Build/协议版本
> 进入 fingerprint 需 ADR-009 握手；跨同型号设备共享证据仍按 ADR-001 暂缓。

#### PR-3 — Session Lifecycle & Resource Lease

- [x] 明确 DeviceSession 的 `open/connect → health → observe/execute → close` 生命周期、取消行为和资源所有权（`artemis/devices/session.py`：状态机、幂等 close、先 control 后 perception、最后 lease）。
- [x] 将 `DeviceSession.pointer: object` 迁移到 Pointer/HID Adapter 私有状态；兼容期只读转发（`adapter_state["pointer"]` + 只读 property；iOS builder 不再写字段）。
- [x] 保留 `DeviceExecutionLock` 的逻辑目标设备互斥语义；新增 Resource Lease 保护共享 UVC 设备、ESP32 Serial Port 等 Host 侧资源（`artemis/runtime/resource_lease.py`、`drivers/factory.py`、`ios-smoke`/`ios-calibrate`、`play_reflex`/`artemis reflex`）。
- [x] Lease 使用稳定资源身份、owner token、PID + process-created-at 陈旧 Owner 检测；多资源获取顺序固定，失败时回滚已取得的 Lease（`LeaseSet` + `tests/unit/runtime/test_resource_lease.py`）。
- [x] Session/Adapter 的 close、异常、任务取消路径必须释放按键/触点、串口、Capture Handle 和所有 Lease（`Esp32HidControl.release_all`、`UvcSource.close`、`HidControlSink.close`、`ArtemisContext.__aexit__`）。
- [x] 增加并发测试：同一 UVC/串口被两个进程争用时只有一个成功；进程崩溃后可回收；正常长任务不被 TTL 抢占（含真实子进程测试）。

**PR-3 验收（代码与测试已满足，2026-11-09）：** 单设备任务锁与共享 Host 资源锁语义不混淆；多进程资源竞争、超时/取消、异常清理测试通过。
证据：`tests/unit/runtime/test_resource_lease*.py`、`tests/unit/devices/test_session.py`、`tests/unit/drivers/test_factory_descriptor.py`、`tests/unit/reflex/test_control_esp32.py`、`tests/unit/test_context.py`；`pytest tests/unit -q` 为 14 个既有失败 + 3605 passed（新增 33 个测试）。

> 剩余：真机验证（UVC/串口释放、并发拒绝、取消释放）待在机台执行。`HelperManager.attach` 已按同一 `helper:<adb-endpoint>/<serial>/forward` 键获取/释放 lease，reflex/CLI 与 Agent 路径共用。

#### PR-4 — Observation / Geometry / Action Contract

- [x] 为 Frame 建立稳定 `frame_id`/source/health（UVC/App/Camera 快照生成 id），Target 可引用产生它的 Frame（`Target.frame_id`）。**artifact reference 仍由 Data Engine 侧关联，未写入 Frame。**
- [x] Point/Box 必须携带 CoordinateSpace；显式区分 Agent Point、Target Box、Frame Pixel、Device Pixel、Pointer Frame Pixel、Window/Display（`artemis/core/coordinates.py`）。
- [x] 给 ScreenGeometry 增加可组合 Transform，覆盖 Crop、Letterbox、Rotation、Scale、Window/Display；旧方法保持兼容（`ScreenGeometry.transform()/pointer_transform()`）。
- [x] 统一 Action Intent、Action Command、Action Receipt、Verification Result 的字段和状态边界（`artemis/core/actions.py`）；`ActionResult.ok` 注释固定为 Adapter 接受/派发；Executor/Validator 写入 `action_id`/`action_attempt`/`action_state` 元数据；MCP `ActionResult` 模型不变。
- [x] 过期 Frame、坐标空间不匹配、Transform 无效时拒绝执行；不允许裸坐标跨层传递（`CoordinateTransform` 拒绝空间错配；ADB `_to_px`、HID `_frame_point` 已改用 Transform；`DeviceSession.validate_target` / `tap_target` 拒绝 `stale_frame`；Agent 0–1000 仅作为兼容边界保留）。
- [x] Contract Tests 覆盖缺能力、unknown 能力、坐标错配、Frame 过期、关闭后调用、重复释放、Adapter ACK 与后续效果分离（`test_coordinates`、`test_actions`、`test_capability_registry`、`test_session`、`test_resource_lease`）。

**PR-4 验收（代码与测试满足，2026-11-10）：** 旧 Agent Action 格式仍能通过兼容层工作；新 DTO 显式表达坐标与动作阶段；现有 Android/iOS 点击策略未改。
证据：`tests/unit/core/test_coordinates.py`、`tests/unit/core/test_actions.py`、`tests/unit/devices/test_session.py`、`tests/unit/mcp/test_actuators.py`；全量 `pytest tests/unit -q` 为 14 个既有失败 + 3680 passed（新增 23 个测试）。

> 行为变更（严格幂等重试策略、Checker 写 `VerificationResult`、执行器按帧/Transform 拒绝旧 Agent 坐标）按 ADR-004 迁移表留到 **Phase 5**（Safety/Verification 收敛），避免在契约 PR 中改变点击成功率。
>
> 真机复测（2026-11-10）：串口恢复后重跑 `ios-smoke --target 设置`，四段门禁全绿，7 项 capability 仍为 verified；`HidControlSink._frame_point` 的 Transform 切换未改变点击结果。

**Phase 1 Exit Criteria：** 四个 PR 合并；Core Contract 不导入 ADB、UVC、pyserial、TinyUSB、Android/iOS SDK；Android/iOS 兼容测试保持；能力缺失可明确报告 `capability_unavailable` / `unknown`，不会退化为 AttributeError；设备与共享硬件资源生命周期可测试、可恢复。

---

### Phase 2 — 建立 Device Runtime / Adapter Registry / Profile Composer

**目的：** 把当前 `drivers.factory` 中的硬件对象创建、环境配置和组合策略分开。

当前基础：`DeviceProfile` / `Composition` / `select_composition()` 已在 `artemis/orchestration/selector.py`。不要重写这个纯函数；应逐步明确它属于 L4 Device Composition，而不是 L2 的 Flash/Pro Task Profile。

任务：

- [ ] 将 `ProfilePlan`（flash/pro/skill）与 `DeviceProfile`（android/ios/macos/tv 设备组合）在命名、配置 Schema、调用链上明确区分。
- [ ] 将 `select_composition()` 逐步移至 L4 Device Runtime 或 Adapter Composer；保留原路径的 re-export 兼容。
- [ ] 建立静态 `AdapterRegistry` / `ProfileBuilder`：按配置和 capability 选择已注册 Builder；第一版不做动态插件市场。
- [ ] Profile 只负责组合 Observation Adapter、Actuation Adapter、Transport Adapter、可选 Platform Extension 和配置，不在 Profile 内实现 Agent 逻辑。
- [ ] 将 `drivers.factory.create_driver()` 拆成轻量路由 + 平台 Builder；允许在 L4 Composition Root 根据 profile/平台注册表分支。禁止把这种分支复制到 Agent、Graph、Perception Policy 中。
- [ ] 把设备配置（UVC device/crop、串口 port、screen geometry、capture mode）与通用 `DeviceDescriptor` 分开；敏感或本机配置仍通过安全配置来源加载。
- [ ] Profile 构建失败必须报告缺失 Adapter、能力或配置原因，不允许静默退回错误设备。
- [ ] 提供 Mock Profile 和 Profile Contract Test，测试源/汇聚/关闭顺序。

建议先支持：

- `android.app`: AppAutomationSource + AppControlSink + Android Driver/Helper。
- `ios.uvc_hid`: UvcSource + ESP32 HID Control + iOS Extension。
- `mock.screen`: Recorded/Mock Observation + Mock Control。

**Exit Criteria：** 添加一个 Mock Profile 或替换 UVC/Control Adapter，不需要修改 Operator/Planner/Checker；Android 与 iOS 当前行为仍能复现。

### Phase 3 — 让 DeviceSession / Ports 成为 Agent 执行链的标准设备边界

**目的：** 逐步解除新抽象对旧 Mobile Use Controller 的依赖。

当前旧链：

```text
MCP Action / Agent Tool
  -> AdbActuator
  -> UnifiedMobileController (artemis)
  -> UnifiedMobileControllerBase (third_party/mobile_use)
  -> BaseDeviceDriver -> platform driver
```

注意：`DeviceSession` 目前不在这条链上（见 §1.2 说明）。把动作接进 Ports 需要经过 `artemis` 侧的 driver→Ports Adapter，而不是假设基类会自动走 Session。

目标链：

```text
Application Service / Agent Tool
  -> Device Runtime / DeviceSession
  -> PerceptionSource / ControlSink Ports
  -> selected Adapter(s)
```

任务：

- [ ] 将 `BaseDeviceDriver` 明确标记为 legacy compatibility façade；新 Driver 不得继续扩大其通用抽象面。
- [ ] 将 Driver 中通用能力拆成可选协议/Port：Observation、Actuation、App Lifecycle、Window/Focus、Shell/Diagnostics、Recording；不要求所有设备实现全部协议。
- [ ] Android/iOS Driver 先适配到这些 Port；不在同一 PR 中改变业务行为。
- [ ] `UnifiedMobileController` 保留录像、日志、旧 API 等尚未迁移的能力，但其设备动作调用逐渐代理到 `DeviceSession`。
- [ ] 将 `mcp/observation.py` 从强依赖 `UnifiedMobileController.get_screen_data()` 改为依赖统一 Observation/Application Service，同时保留旧输出 DTO。
- [ ] 将 `mcp/action_executor.py` 的参数解析、目标解析、动作策略与底层 Actuator 拆开；Agent Tool DSL 保持兼容，底层 dispatch 通过 Control/Action Port。
- [ ] 审核 `mcp/actuators/adb.py`：ADB 实现属于 L4，不应由通用 Action Executor 直接选择或构造。
- [ ] 将 `ArtemisContext` 中 Device Client、Controller、Driver、Trace、Task State 的职责逐渐拆到专门对象；暂不一次性重写 Context。
- [ ] 所有旧公开 import/CLI/MCP/SDK 参数保留 Facade；调用方清零后另开 PR 删除。

**Exit Criteria：** Flash/Pro 的主要观察与动作路径通过标准 Ports 运行；MCP 工具 Schema 和现有外部调用没有非预期破坏；旧 Controller 仍可服务于未迁移的录像/诊断功能。

### Phase 4 — 抽离通用 Adapter，并把 iOS 变成一个清晰的 Platform Profile

**目的：** 利用已经存在的 iOS 实现提取通用 UVC/HID/Serial 能力，而不是复制 iOS 特例。

#### 4.1 UVC / Capture

- [ ] 将 `UvcCapture` / `UvcStream` 从 `artemis/reflex/capture.py` 逐步抽成 L4 UVC Capture Adapter（可先保留旧路径 shim）。
- [ ] `artemis/perception/uvc.py::UvcSource` 继续负责 Frame 健康检查、OCR/Locator 等感知管线，不直接管理宿主机视频设备句柄。
- [ ] UVC 设备选择、尺寸、帧率、Crop、重连、超时归 L4 Adapter 配置；Crop 变换由 Geometry 明确记录。
- [ ] Camera/HDMI/UVC 都实现同一 Observation Port，但其健康状态、延迟和标定质量不可伪装成相同。

#### 4.2 ESP32 HID / Serial

- [ ] 将 `Esp32HidControl` 从 `artemis/reflex/control.py` 抽为 L4 `Esp32HidAdapter` + Serial Transport；保留旧 `Control` Protocol 适配器给 Reflex。
- [ ] 将串口 115200 8N1、LF/CRLF 行结束、单命令等待 ACK、单行长度、长文本分片等协议事实集中到 Firmware Bridge，不散落在业务层。
- [ ] 正式区分 Host 命令 ACK、HID Report 已提交、iOS/Android 实际响应、屏幕效果验证。
- [ ] 记录固件 Build Variant 与能力：当前固件默认有 Touch/Mouse/Keyboard；iOS-only Build 关闭 Touch，走 Mouse/Keyboard；BCI-only 是独立实验配置。能力只有经过目标设备实测才能标记 verified。
- [ ] 设计后续协议版本/能力握手（先写 ADR 和兼容测试，再改固件）：协议版本、Report/Command 能力、最大报文、ACK 语义、超时、设备重启和状态释放。
- [ ] 老固件不支持的 Host 命令（例如 `amove`）必须报 `command_unsupported`（契约 §7.2），不允许静默退化成语义不同的动作。

#### 4.3 Pointer Control 与 iOS 平台策略

- [ ] 将 `PointerSession` 的位置估计、Gain Calibration、Move Planner 等放入相对指针 Adapter 的内部状态；不要放在通用 `DeviceSession`。
- [ ] 将视觉检测器放进可复用 Perception/Pointer Detection；将 pointer-vs-target 前置校验留在 Safety。
- [ ] `HidControlSink` 中通用 Mouse/Keyboard 原语与 iOS 专属 BACK 左边缘手势、Full Keyboard Access 导航策略分开。
- [ ] `perception/ios_targets.py::icon_above_label` 迁移为 iOS Profile 的目标调整插件；通用 OCR/Locator 不知道 SpringBoard 图标布局。
- [ ] `reflex/ios_apps.py`、`agents/ios_shortcuts.py` 迁为 iOS Profile 提供的可选 App Launcher / Keyboard Guidance。上层只读取可用能力和操作提示，不硬编码 `is_ios_device`。
- [ ] `drivers/ios/smoke.py` 和 `calibration.py` 保留为平台 Adapter 的诊断/校准工具，不塞入通用 Agent 核心。
- [ ] **补齐 MCP/Agent Executor 中遗留的平台分支（本项不能只删 if）：**
  - `artemis/mcp/action_executor.py`：`_locate_coordinate_target` 与 `_ask_explorer` 的 iOS LocateAnything 快速路径，迁入通用 L3 Grounding/Target Resolution Port；LocateAnything 作为可注册 Locator，能否使用由 Observation/Capability/配置决定。
  - `artemis/mcp/actuators/adb.py`：iOS ASCII/pinyin 输入转换迁入 Text Input Adapter/Platform Extension；BACK 左边缘手势及失败后的 fallback 文案迁入语义 Navigation Action 的平台映射；`manage_app` 迁入 App Lifecycle Port，由能力决定 launch/stop 是否可用。
  - `McpActionExecutor` 不再 import `is_ios_device`；`AdbActuator` 仅作为 Android/旧链兼容 Adapter，不承担跨平台决策。
  - 迁移前后保持 iOS 文本输入、BACK、App Launch、LocateAnything 和 Android 原有行为的回归测试；旧路径清零后再删除平台分支。

**Exit Criteria：** iOS Profile 仅由通用 Adapter + iOS 专属 Extension 组合；UVC 与 ESP32 HID 能被 Mock/其他 Profile 复用；Android 主链不依赖 iOS 代码。

### Phase 5 — SafetyNet 与验证语义收敛（双执行模式）

**目的：** 有统一的安全语义，但不把慢速任务 Agent 的策略强加给高频 Reflex Loop。

当前已有：`safety/xml.py`、`safety/pixel.py`、`safety/pointer.py`，以及 `agents/validator` 中的 Precondition/Execution/Incident 逻辑；`reflex/mouse.py` 也调用 Pixel/Pointer 原语。

任务：

- [ ] 定义统一 `ActionGate` / `SafetyPolicy` Port，集中动作前校验、执行约束和结果后检的统一术语。
- [ ] Validator 的 XML/Pixel Precondition、Pointer Gate 使用同一 Incident Taxonomy；避免重复实现阈值和状态名。
- [ ] 明确不同动作所需证据：绝对触控不要求 Pointer Precheck；相对指针点击必须先证明指针位置与目标一致；Remote Key 不需要像素 Pointer 校验。
- [ ] `confidence=None` 必须表示“来源未提供置信度”，不能自动映射为 `low_confidence`；没有证据不能虚构失败原因。
- [ ] 区分 `target_disappeared`、`target_shifted`、`pointer_missing`、`stale_frame`、`no_effect`、`capability_unavailable`、`command_unsupported`、`transport_error`、`device_disconnected` 等 Incident；取值以契约 §7.2 唯一命名表为准，不得另起别名。
- [ ] 任务式 Agent（Flash/Pro）执行高价值动作时运行 precondition / post-action verification。
- [ ] Reflex 高频 Loop 使用自己的安全预算：最大运行时长、FPS Cap、blind frame 上限、control failure 上限、急停/取消和释放输入状态。不要默认每帧执行重量级 SafetyNet/Trace 写盘。
- [ ] 所有执行器在取消、断连、异常退出时释放按下的键、鼠标按钮、触点和持续动作状态。

**Exit Criteria：** 同一个安全事实只有一个权威实现；每个执行模式有明确且经测试的安全边界；不存在“ADB/HID 命令成功即任务成功”的路径。

### Phase 6 — Data Engine / Telemetry / Runtime Trace 职责收敛

**目的：** 解决可关联、可回放、可观测，不是简单合并三个目录。

现状职责判断：

- `artemis/data_engine/`：Task/Step、截图、视频、History Chunk、Artifact 等持久化记录，是任务证据和回放数据的主要候选权威源。
- `artemis/telemetry/`：Span、LLM/Tool/Device Action 耗时、错误、Token Usage 等运行遥测。
- `artemis/runtime/trace_store.py`：MCP 后台任务/进程状态的并发安全 Store，不应替代 Data Engine Trace。

任务：

- [ ] 定义 `task_id`、`session_id`、`device_id`、`step_id`、`action_id`、`frame_id`、`trace_id` 的关系和创建方。
- [ ] 每个 Action 记录所用 Profile、source/adapter、target evidence、coordinate space、capability decision、dispatch result、device ACK、pre/post verification、latency 和 incident。
- [ ] 大图像/视频只存 Artifact Reference/hash/metadata，不复制进多份 JSON Trace。
- [ ] Telemetry Span 可关联 Data Engine Step，但 Span 不承担完整 Step 历史；Runtime Trace Store 只管后台任务状态。
- [ ] 增加按 Task/Device/Action/Artifact 的回放与查询测试；隐私/敏感屏幕内容遵循保留和脱敏策略。

**Exit Criteria：** 能从一次失败动作还原“观察到什么、用哪个 Adapter、执行了什么、收到什么 ACK、屏幕如何变化、为什么判定成功/失败”。

### Phase 7 — macOS Target Profile（Design-only；暂不进入实现主线）

**状态：** 设计验证 / Backlog。Phase 1–6 未完成前，不实现生产 macOS Adapter，不以此阻塞当前重构。

**设计交付：**

- [ ] 区分 `artemis/platform/darwin.py`（ARTEMIS Host OS PAL）与被控 Mac 的 Target Adapter；前者不算 macOS 控制能力。
- [ ] 输出 `macos.desktop` Profile 设计：ScreenCaptureKit/截图、Accessibility/AX Tree、窗口/焦点、原生键鼠、权限状态。
- [ ] 明确多显示器、Retina Scale、Window Offset、Display Rotation/坐标转换要求。
- [ ] 建立 Mock Observation/Input Contract Fixture 与权限状态矩阵；不实现生产 Adapter、不承诺实机能力。
- [ ] Phase 1–6 的核心契约稳定后，再由独立 ADR 决定是否启动 macOS 真实纵向切片。

**激活条件：** Android/iOS 使用统一 Ports；Capability Evidence、Session/Lease、Geometry 和 Action Receipt 契约通过测试；有明确真实 Mac 设备、权限环境和验收任务。

### Phase 8 — TV / Media Device Profile（Design-only；晚于 macOS）

**状态：** 设计验证 / Backlog。当前不实现 TV Remote、CEC、IR 或厂商 API Adapter。

**设计交付：**

- [ ] 定义非触屏 Remote Action 语义：D-pad、Select、Back、Home、Volume、Power、Input Source。
- [ ] 建立 TV Profile 草图：HDMI/API/Frame Observation + Remote/CEC/IR/Network Actuation。
- [ ] 区分“命令发送成功”“设备接受命令”“UI 状态变化”；电源/输入源等非幂等命令禁止盲目重试。
- [ ] 建立 Mock Focus-Navigation Fixture；不伪装为已接入真实电视。

**激活条件：** macOS 真实纵向切片证明非移动设备可复用 Agent Core；Remote Action、权限和安全语义形成 ADR；有明确电视型号、控制协议和真实验收环境。

### Phase 9 — 清理兼容层与目录收敛

**前置：** P9-A 的安全清理只要求 Android/iOS 已通过统一 Ports 的兼容测试且调用方已清零；P9-B 退役广义 Mobile Driver 契约前，仍要求至少一个真实非 Android/iOS Profile 通过 Adapter Contract Test。macOS/TV 目前是 design-only，不得假装已满足 P9-B。

任务：

- [ ] **P9-A 安全清理：** 搜索旧 `BaseDeviceDriver`、`UnifiedMobileController`、`artemis/mcp`、`reflex.capture/control/mouse/vision` 的生产引用；只删除调用方为零且迁移说明完成的兼容 shim。
- [ ] 根据真实依赖/变化原因决定是否把 `control/`、`perception/` 实现下沉到 Adapter；不做纯美观式重命名。
- [x] 旧规范已标记 Superseded（2026-11-08）；本阶段只保留历史记录，不回填新决策（历史 B0–B6 计划与 v1 spec 均不再更新）。
- [ ] 更新 root AGENTS 和子目录 AGENTS，确保架构规则可被 Coding Agent 直接加载。
- [ ] **P9-B 条件性退役：** 第一个真实非 Android/iOS Profile 通过 Contract/Integration Test 后，再评估退役 `BaseDeviceDriver` 中剩余 Mobile 专属 API；未满足条件时保留兼容 Facade。

**Exit Criteria：** 旧兼容入口不存在隐式业务依赖；新增平台可按 Adapter/Profile 模式扩展；Architecture Test 阻止新违规依赖。

---

## 5. PR 切分建议

| PR | 目标 | 必须分离的内容 | 回归重点 |
|---|---|---|---|
| PR-0 | Baseline inventory | 只加盘点/报告，不改运行逻辑 | 测试、CLI、MCP、Android/iOS Smoke |
| PR-1 | Core contract hardening | DTO/Capability/Geometry/Session contract + tests | 现有 Session、坐标、能力测试 |
| PR-2 | Device Registry/Builder | 只抽 Factory/Composition，不搬硬件算法 | Android Mock + iOS builder |
| PR-3 | Agent/MCP 设备边界 | `mcp/observation`、`action_executor` 迁移；保留 facade | Flash/Pro、MCP、trace |
| PR-4 | UVC + ESP32 HID Adapter | I/O 纯迁移；行为变更另 PR | iOS Smoke、pointer calibration、firmware compat |
| PR-5 | iOS Profile extraction | iOS shortcut、app launch、target adjustment 归位 | iOS 实机任务、Android 零回归 |
| PR-6 | SafetyGate consolidation | 统一 Incident/Policy，不改变 Reflex 频率策略 | Validator + Pointer + Reflex safety tests |
| PR-7 | Observability ownership | 统一 IDs/links，不删除旧 Store | replay / Admin Console / background tasks |
| PR-8 | macOS adapter vertical slice | 新平台只新增 Adapter/Profile | macOS permission + real device tests |
| PR-9 | TV adapter vertical slice | Remote semantics + observation | TV command / focus navigation tests |
| PR-10 | Legacy cleanup | 只删确认无人使用的 shim | 全量测试 + public API compatibility |

PR-3/PR-4 的顺序可根据实际依赖微调，但每个 PR 都必须能独立回滚。

---

## 6. 测试、质量门禁和真实设备验收

### 6.1 测试分层

1. **Architecture Tests**：禁止 L2/L3 import ADB、pyserial、UVC SDK、平台 SDK、固件协议实现；禁止循环依赖；允许 L4 Composition Root 识别平台。
2. **Port Contract Tests**：所有 Observation/Actuation Adapter 运行同一套接口契约测试。
3. **Adapter Unit Tests**：断连、超时、权限不足、错误 ACK、重复命令、状态释放、异常资源清理。
4. **Profile Composition Tests**：给定 Device Descriptor + Capability 状态，得到正确 Profile/Composition 和明确的选择理由。
5. **Platform Integration Tests**：Android、iOS、macOS、TV 各自平台集成。
6. **Hardware Tests**：涉及 HID/USB/UVC/CEC/IR 的真实硬件验证，单独标记 manual/hardware，不伪装成普通单元测试。
7. **Agent Regression Tests**：同一套任务样本分别验证 Flash/Pro；Skill/Reflex 使用独立高频测试。

### 6.2 当前仓库可用的测试目录

当前已有 `tests/unit/{devices,drivers,control,perception,orchestration,reflex,safety,runtime,data_engine,...}`，以及 `tests/integration/ios/`、`tests/integration/` 和 `tests/e2e/`。优先补齐现有目录，不要无必要地复制一套新测试框架。

建议新增：

```text
tests/
├── architecture/
├── contracts/
├── adapters/
├── profiles/
├── platform/
└── hardware/       # 可标 manual/hardware
```

### 6.3 基线纪律

`docs/superpowers/plans/2026-11-06-target-architecture-migration.md` 曾记录一次基线：`14 failed / 3535 passed / 9 skipped`，并指出已知失败主要在 Admin Console、Explorer 和 Video Analyzer。这是**文档中记录的历史基线，不是本次已重新运行的当前结果**；2026-11-09 的实测见 [`TEST_BASELINE.md`](TEST_BASELINE.md)。

每个 PR 必须：

- 在改动前运行并记录当前分支真实基线；
- 不把已有失败算成新增失败，也不通过删测试/放宽断言“修复”基线；
- 运行 `uv run pytest tests/unit -q`、`uv run ruff check` 和仓库现有质量 Ratchet；
- 如果硬件不可用，清楚标记“Mock 验证”或“未验证”，不可写“真实设备通过”。

### 6.4 平台验收矩阵

| 平台/Profile | Observe | Actuate | Geometry | Safety/Verification | 当前定位 |
|---|---|---|---|---|---|
| Android App | Screenshot + UI Tree + 可选 OCR | ADB/Helper absolute touch + key/text | Device pixel / display | XML + Pixel + task checker | 当前主兼容基线 |
| iOS UVC/HID | UVC Frame + 可选 OCR/LocateAnything | ESP32 Mouse/Keyboard；Touch Report 需按固件构建与系统识别能力判断 | Capture Crop ↔ Screen ↔ Pointer Frame | Pointer Precheck + Pixel Effect + Checker | 已接入，需抽离平台策略 |
| macOS Desktop | Screen Capture + Accessibility/Window | Native Keyboard/Mouse | Retina/Display/Window transforms | Focus/Window + Pixel/Accessibility | 后续首个桌面 Profile |
| TV | HDMI/API/Frame + 可选 OCR | Remote/D-pad/CEC/IR/API | Frame/TV viewport | Focus state + screen state + command evidence | 后续非触屏 Profile |

---

## 7. 风险与回滚规则

| 风险 | 处理策略 |
|---|---|
| 新 Session 和旧 Driver 同时维护状态 | 一个 PR 只指定一个事实源；旧接口从新 Session 读取，不允许双写 |
| 能力汇总 OR 导致“虚假可用” | 能力按 Source/Sink 分开声明，Session 仅组合出带来源的最终视图 |
| iOS Pointer 逻辑搬迁破坏真实动作 | 先纯迁移并做硬件回归，下一 PR 再改算法 |
| UVC Crop/Coordinate 错位 | 每个 Frame 绑定 Transform ID/geometry revision，加入回放固定样本 |
| 固件升级导致 Host/Device 协议不兼容 | 协议版本/能力协商；未知版本拒绝危险命令或进入兼容模式 |
| Retry 导致双击/重复电源切换/重复提交 | 非幂等动作默认不自动重试；要求 Action ID / idempotency policy |
| Reflex 被普通 SafetyNet 降速 | Reflex 维持独立预算、安全上限和停止策略 |
| 删除第三方 Mobile Use 引入行为回归 | 先隔离类型与 Facade；不在同一 PR 删除上游依赖 |
| 平台专属代码重新进入 Agent | Architecture Test + Prompt/Tool Profile 注入规则 |

每个迁移 PR 必须附回滚方式。纯搬迁 PR 必须做到行为不变；行为改变需要单独说明预期指标和对照样本。

---

## 8. ADR 与 Phase 阻塞关系

ADR 的唯一索引与模板：[`docs/adr/README.md`](../adr/README.md)、[`docs/adr/ADR_TEMPLATE.md`](../adr/ADR_TEMPLATE.md)。**ADR 未达到 Accepted 状态时，对应 Phase 只能做盘点/原型/测试设计，不得合入改变公共契约的实现。**

| ADR | 决策主题 | 阻塞的 PR / Phase | 状态 |
|---|---|---|---|
| ADR-001 | [Capability Model、Capability Evidence 与 Fingerprint/Invalidation](../adr/ADR-001-capability-model-evidence-fingerprint.md) | Phase 1 PR-2；Phase 2；Phase 4.2 固件能力握手 | **Accepted**（2026-11-09） |
| ADR-002 | [Coordinate Space、Transform 与迁移兼容](../adr/ADR-002-coordinate-spaces-transform.md) | Phase 1 PR-4；Phase 3 所有坐标链路；Phase 7 | **Accepted**（2026-11-10） |
| ADR-003 | [Device/Target/Endpoint Identity、Session 生命周期与资源所有权](../adr/ADR-003-device-identity-session-resource-ownership.md) | Phase 1 PR-1/PR-3；Phase 2；Phase 3 | **Accepted**（2026-11-09） |
| ADR-004 | [Action Intent/Command/Receipt/Verification 状态语义](../adr/ADR-004-action-intent-command-receipt.md) | Phase 1 PR-4；Phase 3；Phase 5 | **Accepted**（2026-11-10） |
| ADR-005 | AdapterRegistry、Profile Builder 与静态注册契约 | Phase 2 | Planned |
| ADR-006 | BaseDeviceDriver / UnifiedMobileController 兼容期与退役条件 | Phase 3；Phase 9-B | Planned |
| ADR-007 | Reflex Runtime、SafetyNet 与高频闭环安全预算 | Phase 3；Phase 5 | Planned |
| ADR-008 | [Data Engine / Telemetry / Runtime Trace 数据权威；Capability Evidence Artifact 归属](../adr/ADR-008-data-engine-evidence-ownership.md) | Phase 1 PR-2；Phase 6 | **Accepted**（2026-11-09） |
| ADR-009 | ESP32 Host-Firmware Protocol Versioning、Capability Handshake 与 ACK 语义 | Phase 4.2 | Planned |
| ADR-010 | Platform Extension、语义动作映射、Prompt Guidance/App Lifecycle/Locator 插件 | Phase 4.3（含 MCP iOS 分支清理） | Planned |
| ADR-011 | [UVC/Serial 等共享 Host Resource Lease、并发和回收语义](../adr/ADR-011-host-resource-lease.md) | Phase 1 PR-3；Phase 2 | **Accepted**（2026-11-09） |

建议顺序：ADR-003 → ADR-001/008 → ADR-002/004 → ADR-005/006 → ADR-007 → ADR-011 → ADR-009/010。实际开始某一 Phase 前，只要求其阻塞 ADR 已 Accepted；不要求提前批准所有未来平台决策。

---

## 9. 完成定义（Definition of Done）

只有同时满足以下条件，才能称为“通用硬件操控 Agent 架构已具备扩展性”：

1. Planner、Operator、Checker、Flash/Pro 不直接依赖任何平台或硬件协议。
2. Android 与 iOS 都通过同一套核心 Ports/Device Runtime 完成真实观察和动作，且原有任务回归不退化。
3. Device Capability 带状态和来源，Profile 组合不虚报能力。
4. UVC、ESP32 HID、ADB、Native Desktop Input、TV Remote 等适配器可独立测试、替换或组合。
5. Reflex Skill Runtime 仍保持确定性、高频、有限时执行能力，并复用通用设备 I/O Port，而非另起一套硬件驱动。
6. 动作派发、固件 ACK、系统响应、屏幕效果和业务验收是不同状态。
7. 坐标变换、设备生命周期、取消/释放、错误与重试规则有契约测试。
8. 增加 macOS 或 TV Profile 不需要修改 Agent Core，只允许新增/注册 Adapter、Profile、平台扩展和测试。
9. 有完整 Trace/Artifact/Replay，可解释一次动作为什么执行、用的哪个 Adapter、收到什么结果、为什么判定成功或失败。
10. 文档、AGENTS、测试与代码状态一致，不存在两个互相冲突的“架构事实源”。

