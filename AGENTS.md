# ARTEMIS Repository Rules for Coding Agents

> 本文件只保留 Coding Agent 必须立即执行的规则；规范性内容不在本文件重复。
>
> - 架构语义（层级职责、Port/DTO、Capability、坐标、Action 状态、数据所有权）：[`docs/architecture/ARCHITECTURE_CONTRACT.md`](docs/architecture/ARCHITECTURE_CONTRACT.md)
> - 实施顺序与迁移批次：[`docs/architecture/REFACTOR_EXECUTION_PLAN.md`](docs/architecture/REFACTOR_EXECUTION_PLAN.md)
> - iOS 真机机台操作：[`docs/ios-rig-playbook.md`](docs/ios-rig-playbook.md)
>
> 冲突时以 `docs/architecture/` 下的文档为准；本文件与之冲突时，先修本文档，不要按旧措辞实施。
> 不得新增第三份平行架构规范；新增/替换规范必须同时更新上面三个入口。

---

## 1. 项目目标

ARTEMIS 正在从 Android 优先、已接入 iOS 的移动 UI Agent，演进为**面向可观察、可交互数字设备的通用硬件操控 Agent 平台**。首批目标：Android、iOS/iPadOS、macOS、Windows/Linux 桌面、TV。

当前分支已存在 `PerceptionSource`、`ControlSink`、`DeviceSession`、`CapabilitySet`、`ScreenGeometry`、Android/iOS Driver、`artemis/safety/`、Reflex Skill Runtime 与 `firmware/esp32-hid/`。**先检查现有实现和调用方，不要重复建设同名抽象。**

## 2. 开始任务前的强制步骤

1. 阅读本文件 + 契约 + 执行计划；涉及 iOS 机台时另读 `docs/ios-rig-playbook.md`。
2. 检查分支、HEAD、`git status`、最近相关提交与现有测试；不得覆盖用户未提交改动。
3. 搜索真实调用方、import 依赖、配置和测试；不得仅凭目录名推断职责。
4. 声明本次改动属于哪一层/横向模块，影响哪些 Port/DTO/Capability/Adapter/Profile，回归范围是什么。
5. 触及层级职责、公共 Port/DTO、坐标空间、Capability 状态、Session 生命周期、Action 状态语义、固件/Transport 协议或 Reflex 安全预算时，先提出 ADR；未批准前不得改变契约。
6. 小步修改：一个 PR 一个主要边界；纯搬迁与行为改变分开提交。
7. 先写/更新测试，再改实现；“代码可导入”不等于“设备能力已验证”。

## 3. 分层速查（摘要）

| 层 | 主要目录 | 职责 | 硬禁止 |
|---|---|---|---|
| L1 Access | `mcp_server/`、`artemis/interfaces/`、`artemis/sdk/`、`apps/` | 接入、校验、Application Service、展示 | 打开 ADB/UVC/USB/Serial/BLE/CEC/IR；编码 HID/固件命令；在入口写 Agent 推理 |
| L2 Agent | `artemis/agents/`、`graph/`、`orchestration/profiles.py` | 规划、执行、验收；Flash/Pro/Skill 选择 | 平台分支 `if platform == ...`；import 驱动/传输库；把命令成功当任务完成 |
| L3 Interaction | `artemis/perception/`、`control/` 契约、`safety/` | 感知、Grounding、动作语义、安全与验证 | 打开硬件句柄；生成固件 Report/串口命令/IR 波形；伪造能力或置信度 |
| L4 Device Runtime | `artemis/devices/`、`drivers/`、`clients/`、目标 `adapters/`、`firmware/` | Session、Capability、Adapter、Transport、Profile、Firmware Bridge | 依赖 Planner/Operator/Checker/Prompt/Graph State/MCP；把单平台能力伪装成通用能力 |
| 横向 | `core/ llm/ memory/ data_engine/ telemetry/ runtime/ config/ platform/` | 契约、模型、上下文、证据、遥测、配置、Host OS PAL | 把 `platform/` 当被控桌面 Adapter；把领域逻辑塞进 `utils/` |

完整 MUST/MUST NOT 见契约 §2、§10、§11。平台分支只允许出现在 L4 Composition Root / Profile Builder。

## 4. 现有执行通道不可破坏

- `reflex/contract.py`、`runtime.py`、`registry.py` 是确定性、高频、有限时的 Skill Runtime：不得并入普通 LLM Agent Loop，也不要求逐帧写 DB / 跑重量级 SafetyNet。
- Reflex 的安全边界是 FPS、deadline、blind frame、control failure、cancel/stop 与输入释放。
- `reflex/capture.py`、`control.py`、`mouse.py`、`vision.py` 的设备 I/O 可逐步下沉 L4，但必须保留 Capture/Control 兼容 Adapter，且延迟与 Skill 行为不得回归。
- 不要把 Skill 的 `policy.py` 直接当作通用 `DecisionProvider`；除非出现第二个真实消费场景并通过 ADR。

## 5. 设备接入规则

新增平台或硬件前，按契约 §12 的 Onboarding Checklist 填完全部条目；至少先明确：

1. Host / Target Device / Physical Interface / Logical Surface / Profile 的边界。
2. Observation / Actuation / Transport / Diagnostics 能力清单与**真实证据**；无实测只能标 `declared` / `unavailable` / `unknown`。
3. 复用现有 Adapter；只有真实平台差异才新增平台专属 Adapter。
4. 经 `DeviceSession` 与标准 Ports 接入；Agent 不得直接依赖平台 Driver。
5. Mock / Port Contract / Lifecycle / Profile Composition / 真机测试齐备。

不得为 Android/iOS/macOS/TV 各复制一套 Planner/Operator/Checker。

### 当前 iOS UVC + ESP32 HID 约束

- `firmware/esp32-hid/` 是唯一固件工程；不要新建第二套同目的目录。
- 复合构建、iOS-only Build、BCI-only 实验 Build 能力不同；源码里存在 Descriptor 不等于 iOS 已支持。
- 串口 `OK` 只表示固件协议层确认，不代表 iOS UI 已响应；必须用后续 Observation/Verification 验证。
- 不得假设外部 Digitizer Touch 在 iOS 上可用；以具体 Build Variant + 目标 iOS 版本实测为准。

## 6. 代码修改规则

- 改接口前搜索全部调用方，更新调用方和测试；公共 API 变化必须提供兼容层或迁移说明。
- 不做与当前任务无关的大规模重命名、目录搬迁或格式化。
- 迁移 `BaseDeviceDriver`、`UnifiedMobileController`、`artemis/mcp`、`reflex` 时使用 Facade/Re-export；调用方清零后才删除旧入口。
- 注意动作真实旁路：`third_party/mobile_use/controllers/unified_controller.py` 直接调用 driver 方法，`DeviceSession` 目前不在 Android 动作主链上（见执行计划 §1.2）。改设备边界时优先在 `artemis` 侧加 Adapter，避免直接改写 vendored 代码及其边界测试。
- `DeviceSession` 不得新增指针、ADB、UVC、平台 launcher 等专属字段。
- 坐标必须注明 CoordinateSpace/Unit/Frame/Transform；禁止裸坐标跨 Frame/Screen/Window/Pointer 空间传递。
- 非幂等动作不能盲目自动重试；必须考虑 Action ID、已执行状态与后续观察。
- 按下状态（key/button/touch）必须在异常、取消、断连时释放。
- 凭据、账号、密码、Token、个人屏幕内容不得写入无保护日志。
- 不得删除或弱化测试、架构门禁和验证规则来让 CI 通过。

## 7. 文档与规范

- 架构规范的唯一位置是 `docs/architecture/`；本文件顶部三个入口必须保持有效链接。
- 已标记 Superseded 的文档（`docs/artemis-target-architecture.md`、2026-11-06 的 B0–B6 计划）只作历史记录，不得回填新决策，也不得据此实施。
- 同一事实只允许一个权威命名：能力 ID 见契约 §5.3，Incident 见契约 §7.2；不得在代码或文档中另起别名。
- 文档改动同样要过一致性检查（相对链接、术语、与代码锚点是否仍然成立）。

## 8. 测试与提交报告

根据变更至少运行相关测试：

- 单元测试：`uv run pytest tests/unit -q`
- 风格检查：`uv run ruff check`
- 仓库已有质量 Ratchet（如本分支仍启用）
- 对应 Port Contract / Adapter / Profile / Integration / Hardware Tests

提交报告必须说明：

1. 改动文件与逻辑层级；
2. 是否改变 Port/DTO/Capability/坐标/Action 状态语义；
3. 真实调用方与依赖方向；
4. 运行的测试和结果；
5. 哪些是 Mock 验证、哪些是真实设备验证、哪些仍未知；
6. 兼容路径与回滚方式。

**禁止**把“未连接硬件”“只跑了 Mock”“固件 ACK 成功”写成“平台能力验证通过”。

## 9. iOS Rig 专用规则

- 动 iOS 机台（UVC / ESP32 HID / 指针 / 点击）之前，先读 `docs/ios-rig-playbook.md`；该文档是具体机台触发条件与排障顺序的事实源。
- 运行 `ARTEMIS_ESP32_PORT=<port> artemis ios-smoke --target <看得见的目标>`，要求既有 Smoke 阶段全部通过后再做 Agent 层调试。
- 点击无效时，检查 Spotlight/弹层/焦点状态；按现有 Rig Playbook 的规则优先尝试 Esc，不要盲目重复点击。
- 原有 Rig Playbook 的基线、人工校准、端口和已知限制必须保留，不得用通用架构条款覆盖。

## 10. 架构变更 ADR

ADR 的唯一索引和模板分别为 [`docs/adr/README.md`](docs/adr/README.md) 与 [`docs/adr/ADR_TEMPLATE.md`](docs/adr/ADR_TEMPLATE.md)。所有 ADR 必须放在 `docs/adr/`，不得只在执行计划或聊天记录中记录决策；Proposed 不等于批准，只有 Accepted 才解除对应 Phase 的实现阻塞。

以下情况必须先创建 ADR：改变层级职责；改变公共 Port/DTO；引入/删除 Capability；改变坐标/时间/session 规则；改变 Action 状态语义；引入新物理执行器/Transport/Firmware 协议；删除公开兼容接口；改变 Reflex 高频安全预算。

ADR 至少包含：背景、当前实现证据、决策、备选方案、依赖影响、迁移/回滚、测试证据和安全影响。

## 11. 推荐的 Coding Agent 工作输出

开始实现前，先输出：

- 本任务属于哪一层/横向模块；
- 当前真实调用链和调用方；
- 哪些现有接口/模块已经实现，哪些仍缺失；
- 计划改动的 Port/DTO/Capability/Adapter/Profile；
- 兼容性和用户未提交改动保护措施；
- 测试计划和真实硬件验证条件。

完成后提供可复现命令、测试结果、未验证能力和回滚步骤。禁止只汇报“重构完成”而没有证据。
