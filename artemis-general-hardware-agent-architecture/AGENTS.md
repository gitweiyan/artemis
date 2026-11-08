# ARTEMIS Repository Rules for Coding Agents

> 必须先阅读：`docs/architecture/ARCHITECTURE_CONTRACT.md` 和 `docs/architecture/REFACTOR_EXECUTION_PLAN.md`。
> 本文件是仓库级执行规则；架构语义以 Architecture Contract 为准。

## 1. 项目目标

ARTEMIS 正在从 Android 优先的移动 UI Agent 渐进演进为**面向可观察、可交互计算设备的通用硬件操控 Agent 平台**，目标平台包括 Android、iOS、macOS、桌面系统和 TV。首期不等同于工业机器人/PLC/车辆等任意物理控制系统。

架构目标：

- L1 Access & Integration
- L2 Agent & Orchestration
- L3 Interaction Intelligence
- L4 Device Integration Runtime（Device Registry、Sessions、Adapters、Platform Profiles、Firmware Bridge）
- L5 Target & Host Environment
- 横向基础设施：Core Contracts、Model Runtime、Memory、Observability、Config

Agent 核心必须与设备平台、硬件连接和设备协议解耦。平台差异必须放在 L4 Adapter/Profile 中，以能力声明和契约接口暴露给上层。

## 2. 开始任务前的强制步骤

1. 阅读架构契约和执行计划相关章节。
2. 检查当前分支、工作区状态、最近相关提交和现有测试；不得覆盖用户未提交改动。
3. 搜索真实调用方、import 依赖、配置和测试。不得仅凭目录名推断模块职责。
4. 明确本次变更的层级、Port、DTO、Adapter/Profile 及回归范围。
5. 涉及边界变化时先提出 ADR；未批准前不得擅自改变契约。
6. 优先小步修改，保持现有 CLI/MCP/SDK/Web Console 和 Android 工作流兼容。

## 3. 架构硬约束

### L1 — Access & Integration

- MCP、CLI、API、SDK、Web Console 只负责接入、校验、调用应用服务和展示结果。
- 禁止在入口层直接打开 ADB/UVC/USB/Serial/BLE、调用 HID 或编码设备协议。

### L2 — Agent & Orchestration

- Planner、Operator、Checker、Summarizer、Flash/Pro 必须设备无关。
- 禁止在 Agent Prompt、Tool 选择或 Graph 中硬编码 `ios/android/macos/tv` 的底层驱动行为。
- Agent 只使用标准状态、能力目录、Action Intent/Command 和执行结果。

### L3 — Interaction Intelligence

- Perception 处理原始样本，输出语义状态；Grounding 输出有证据、有坐标空间的目标；Actions 负责策略、编排和验证。
- 禁止直接打开物理设备、操作 USB/Serial/ADB 句柄或生成固件专用 HID Report。
- 禁止把命令发送成功当作 UI/业务目标完成。

### L4 — Device Integration Runtime

- 所有平台/硬件差异放在 Adapter、Transport、Firmware Bridge 或 Platform Profile。
- 通用机制应优先放在可复用 Adapter；平台目录只做平台原生实现、组合和配置。
- Adapter 不得依赖 Agent、Prompt、Graph State 或 MCP Server。
- 能力必须区分 supported / available / verified / unavailable / unknown；没有真机/目标环境证据不得标记 verified。

### L5 — Target & Host Environment

- OS、系统输入栈、目标应用和真实硬件行为视为外部环境。
- 不得从 Descriptor 合法、命令 ACK 或 API 返回成功推断目标系统一定接受或执行了动作。

### 横向基础设施

- Core Contracts 只放稳定 Port、DTO、枚举、ID 和错误码。
- Memory 管理 Agent 可用上下文；Observability 管理原始执行事件和证据。
- Model Runtime 管理 Provider/路由，不承载 Agent 业务策略。
- 禁止将领域逻辑随意塞进 `utils/`、`common/`、`helpers/`。

## 4. 通用设备接入规则

新增设备平台时，按顺序完成：

1. 明确 Device、Target、Profile 和 Session 的身份边界；
2. 列出 Observation、Actuation、Transport、Diagnostic 能力；
3. 选择可复用 Adapter，只有确实存在平台差异才新建平台专属实现；
4. 定义 Capability Manifest，包括权限、前置条件、参数范围和验证状态；
5. 通过标准 Port 接入；
6. 编写 Mock、Adapter Contract、Lifecycle、Capability 和真实平台测试；
7. 更新平台兼容矩阵、已知限制和 Trace/诊断信息。

不得为每种设备复制一套 Planner/Operator/Checker。

### 示例：平台组合而非平台专用核心

- Android：ADB、Accessibility Helper/UIAutomator2、ADB/scrcpy 观察等 Adapter 的组合。
- iOS：UVC 观察适配器 + ESP32 HID Host/Firmware Bridge + 已验证的 HID Profile。
- macOS：ScreenCaptureKit/Accessibility 观察 + 原生键鼠输入适配器及系统权限检查。
- TV：HDMI/系统截图/状态 API 观察 + Remote/CEC/IR/Bluetooth 等输入适配器。

以上是架构组合示例，不代表所有设备和能力已实现或已验证。

## 5. 代码修改要求

- 改接口前先搜索全部调用方，更新调用方和测试；若公共 API 变化，提供兼容层或版本迁移说明。
- 不做与当前任务无关的大规模重命名、目录搬迁或格式化。
- 迁移时采用 Facade/Re-export 兼容旧 import；待调用方清零后再删除旧入口。
- 异步资源必须具备 timeout、cancel、close 和异常清理路径。
- 设备命令必须处理 session identity、deadline、重复命令和错误分类。
- 非幂等动作不能盲目自动重试。
- 触控/鼠标/键盘/遥控器语义不可静默互相降级。
- 所有坐标必须带 coordinate space 和有效 transform；禁止散落比例换算。
- 设备断连、任务取消或进程异常必须尽力释放按键、按钮、触点等持续状态。
- 凭据、个人数据和敏感屏幕内容不得写入无保护日志。

## 6. 测试与验收

根据变更至少运行相关测试：

- 单元测试；
- Architecture/Import Boundary Tests；
- Port Contract Tests；
- Adapter Capability/Lifecycle Tests；
- 平台集成测试；
- 真实硬件测试（触及硬件行为时）。

报告必须说明：

1. 改动文件和职责归属；
2. 是否新增或改变 Port/DTO/Capability；
3. 依赖方向是否遵守契约；
4. 执行了哪些测试及结果；
5. 哪些能力仅 Mock 验证，哪些已在真实设备验证；
6. 已知限制和回滚方法。

禁止：

- 删除或弱化架构测试以让 CI 通过；
- 将未经验证的功能标记为 supported/verified；
- 仅凭固件 ACK 声称系统动作成功；
- 以“后续再整理”为由引入新的反向依赖；
- 在没有 ADR 的情况下改变架构分层或公共契约。

## 7. 变更决策

以下情况必须先创建 ADR：改变层级职责、改变核心 Port/DTO 语义、增加平台/Adapter 类别、改变坐标/时间/session 规则、改变动作状态语义、引入新物理执行器或删除公共兼容接口。

ADR 至少包含：背景、决策、备选方案、依赖影响、迁移与回滚、测试证据、安全影响。

## 8. 推荐的任务执行提示

开始实施时，先输出：

- 本任务属于哪一层/横向模块；
- 当前真实调用链与受影响的调用方；
- 计划新增/修改的 Port、DTO、Adapter 或 Profile；
- 需要保持的兼容性；
- 测试计划和风险。

随后按最小改动实施。完成后给出可复现命令、测试结果和未验证事项。
