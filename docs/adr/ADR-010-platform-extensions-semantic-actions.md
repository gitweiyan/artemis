# ADR-010: Platform Extensions, Semantic Actions and Locator Plugins

- **Status:** Proposed
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 4.3（iOS Platform Extension 抽取）；Phase 7/8 的 macOS/TV 语义动作
- **Related:** 契约 §2（L2/L3/L4）、§5.7（Action）、§5.8（Control Port）、§6.2（Platform Profile）、§11（目录）；[ADR-001](ADR-001-capability-model-evidence-fingerprint.md)（Accepted）、[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted）、[ADR-004](ADR-004-action-intent-command-receipt.md)（Accepted）、[ADR-005](ADR-005-adapter-registry-profile-builder.md)（Accepted）、[ADR-006](ADR-006-driver-controller-retirement.md)（Accepted）
- **Audit evidence:** `artemis/agents/ios_shortcuts.py:9-80`、`artemis/mcp/action_executor.py:42,303-333,865-877`、`artemis/mcp/actuators/adb.py:34,255,329-341`、`artemis/control/hid.py:38-44,220,328-347`、`artemis/perception/ios_targets.py:1-20`、`artemis/reflex/ios_apps.py:28-49`、`artemis/perception/uvc.py:58,142-144`、`artemis/reflex/locate_anything.py:31,80`

> 状态说明：Proposed。批准前只允许盘点、原型和测试设计；不得合入改变平台扩展契约或 Agent 提示注入的实现。

---

## 1. Context and problem

### 1.1 平台知识散落在 L1/L2/L3

当前 iOS 相关逻辑并不只在 L4：

| 位置 | 内容 | 层 |
|---|---|---|
| `agents/ios_shortcuts.py:9-80` | `is_ios_device`、`IOS_KEYBOARD_GUIDANCE`、把 guidance 注入 Prompt | L2 |
| `mcp/action_executor.py:42,303-333` | `is_ios_device` → LocateAnything 坐标补全 | L1/Tool |
| `mcp/action_executor.py:865-877` | Explorer 在 iOS 上先用 LocateAnything | L1/Tool |
| `mcp/actuators/adb.py:34,255,329-341` | iOS 文本转 ASCII、`back` 映射到 `go_back` | L1/L4 混合 |
| `control/hid.py:38-44,220,328-347` | iOS BACK 左边缘手势、FKA 方向键滚动 | L3 |
| `perception/ios_targets.py:1-20` | SpringBoard 标签→图标几何抬升 | L3 |
| `reflex/ios_apps.py:28-49` | iOS Spotlight App Launcher | L4 特殊通道 |
| `perception/uvc.py:58,142-144` | UVC 源内的 LocateAnything | L3 |
| `reflex/locate_anything.py:31,80` | LocateAnything HTTP 客户端/检测器 | L3/L4 混合 |

这些代码共享同一模式：**在通用模块里判断平台，再选一条策略**。新增 macOS/TV 时会复制更多 `is_ios_device`/`if platform`，违反契约 §2/§6.2。

### 1.2 语义动作与物理输入没有分开

- `press_key("back")` 在 Android 是 keyevent，在 iOS 是左边缘手势（`control/hid.py:328-347`），但调用方只看到同一个字符串；
- `launch_app` 在 Android 是 package manager，在 iOS 是 Spotlight（`reflex/ios_apps.py`），在 TV 可能是 app list/remote；
- 文本输入在 iOS 需要 ASCII/pinyin（`adb.py:255`），在 Android 可直接输入中文。

当前用 `is_ios_device` 分支实现这些差异，而不是由 Platform Extension 提供映射。

### 1.3 Locator 被当成 iOS 专属

LocateAnything 是 HTTP 视觉定位服务（`reflex/locate_anything.py`），本质是 L3 Grounding 实现，但在 `action_executor.py:318`/`uvc.py:142` 里按 iOS 启用。结果是：

- 通用 Grounding 无法复用；
- 关闭/回退逻辑散落；
- 其他平台（macOS/TV）想用同一服务需要复制 iOS 分支。

### 1.4 为什么现在必须决策

Phase 4.3 要把 iOS 变成清晰 Profile；Phase 5 的 Action/Verification 需要语义动作映射；Phase 7/8 会新增 macOS/TV。如果不先定 Extension 契约，`is_ios_device` 会继续扩散。

---

## 2. Decision drivers

1. **平台知识只在 L4 Extension**：L2/L3/L1 只读 Capability/Port/结构化 Guidance。
2. **语义动作与物理输入分离**：`back/home/app_switch/launch` 是 ActionIntent；具体映射由 Extension 提供。
3. **可组合**：同一 Extension 可以被多个 Profile 复用；一个 Profile 可以组合多个 Extension。
4. **不破坏现有行为**：iOS 的 BACK、Spotlight、LocateAnything、图标抬升必须等价。
5. **可测试/可回退**：Fake Extension + Android/iOS 回归；旧路径保留一个阶段。
6. **不做插件市场**：静态注册（ADR-005），第一版只有内置 Extension。

---

## 3. Considered options

### Option A — 继续用 `is_ios_device`

- Benefits：改动最小。
- Costs / risks：平台分支继续扩散；新增平台复制策略；L2 依赖平台；测试只能靠 Mock platform。

### Option B — 把平台差异全部塞进 Prompt

- Benefits：不改执行层。
- Costs / risks：Prompt 无法保证 Back/Launch 的物理语义；模型可能手搓 Spotlight（已有假成功教训）。

### Option C — PlatformExtension + 可选 Port/Plugin（推荐）

- Benefits：语义/物理分离；L1–L3 平台中立；可测试；可复用 LocateAnything。
- Costs / risks：需要定义 Extension 契约与注入路径；迁移期两套路径。

### Option D — 每个平台独立 Agent/Executor

- Benefits：简单直接。
- Costs / risks：明确违反契约 §2/§11；回归矩阵爆炸。

**选择：Option C。**

---

## 4. Decision (proposed)

### D1 — PlatformExtension 契约

```python
@dataclass(frozen=True)
class PlatformExtension:
    extension_id: str
    platform_family: str
    capabilities: CapabilityManifest
    #: Optional ports; only implemented methods are exposed.
    app_lifecycle: AppLifecyclePort | None = None
    navigation: NavigationPolicy | None = None
    text_input: TextInputPolicy | None = None
    target_adjuster: TargetAdjuster | None = None
    grounding: TargetLocator | None = None
    prompt_guidance: PromptGuidance | None = None
    permissions: PermissionChecks | None = None
```

规则：

1. Extension 只由 L4 Profile 注册/组合（ADR-005）；L1–L3 不 import 具体 Extension。
2. Extension 的可用性通过 `CapabilityView`/Profile 声明；缺失时调用方得到 `capability_unavailable`，不得回退到“猜一个平台分支”。
3. Extension 不持有 Agent/Graph/Checker；只提供设备语义和策略数据。
4. 一个 Profile 可组合多个 Extension（例如 iOS Keyboard + iOS Launcher）；Extension 之间不得隐式依赖平台名。

### D2 — Ports 与职责

| Port | 方法 | 现有实现 |
|---|---|---|
| `AppLifecyclePort` | `launch(identifier)`、`stop`、`current()` | Android package manager、iOS Spotlight（`reflex/ios_apps.py`）、TV app list |
| `NavigationPolicy` | `back`、`home`、`app_switch` 的语义映射 | iOS 左边缘手势（`control/hid.py`）、Android keyevent、TV remote |
| `TextInputPolicy` | 编码/转写（ASCII/pinyin）、clear 策略 | iOS `to_ascii_input`（`adb.py:255`）、Android 直输 |
| `TargetAdjuster` | 定位后几何修正（标签→图标） | iOS `icon_above_label`（`perception/ios_targets.py`） |
| `TargetLocator` | `locate(frame, query) -> Target` | LocateAnything（`reflex/locate_anything.py`）、OCR |
| `PromptGuidance` | 结构化 guidance 段 | `IOS_KEYBOARD_GUIDANCE`（`agents/ios_shortcuts.py`） |
| `PermissionChecks` | 权限/校准/健康提示 | macOS Screen Recording/Accessibility、iOS AssistiveTouch |

### D3 — 语义动作映射

- Action 层使用 `ActionIntent.kind`（`back`/`home`/`app_switch`/`launch`/`tap`/`text`），不把平台字符串传给 Driver。
- Profile 的 `NavigationPolicy` 把 `back` 映射为：
  - iOS：`control.go_back()`（左边缘手势）；
  - Android：`keyevent BACK`；
  - TV：Remote Back key/CEC。
- 映射结果写入 `ActionCommand.adapter_id`/`parameters` 与 Receipt（ADR-004），调用方不得假设物理键相同。
- `press_key("BACK")` 的兼容路径保留，但新代码使用语义 `back`。

### D4 — App Lifecycle

- `AppLifecyclePort.launch(identifier)` 返回结构化结果（accepted/needs_observation/error），不直接宣称 App 已打开。
- iOS Spotlight 实现保留在 iOS Extension；Android package manager；TV Remote/App list。
- 启动后必须由 Observation/Verification 确认前台 App（ADR-004），不能只信 `Opened`（2026-11-06 假成功教训）。

### D5 — Locator 插件与 Grounding

- LocateAnything 作为通用 `TargetLocator` 实现，不再由 iOS 分支创建；Profile 注册它并声明 `grounding.locate` 能力。
- **新增能力 ID `grounding.locate`**（ADR-001 catalog 扩展）：`declared` 由 Profile 注册，`available` 由 HTTP 健康探测，`verified` 由 Smoke/Contract Test 产出。
- 启用顺序：Profile 注入的 locator → OCR/视觉兜底；失败按 ADR-001 记录 `target_unverified`/fallback，不静默当作已验证。
- macOS/TV 可以复用同一个 HTTP Locator；不需要平台分支。

### D6 — 文本输入

- `TextInputPolicy.prepare(text)` 返回可发送文本与编码（ASCII/pinyin/UTF-8），失败时返回结构化 `encoding_unavailable`。
- `mcp/actuators/adb.py` 不再判断 `is_ios_device`；它通过 Session/TextInputPort 获取策略。
- 中文输入在 iOS 上必须走 pinyin（保留现有 `to_ascii_input` 行为），但由 Extension 提供，不由 Executor 判断。

### D7 — Prompt Guidance 注入

- `PromptGuidance` 返回结构化段落（id/title/body），由 L4 Profile 在 Agent 启动时注入一次。
- L2 不 import `agents/ios_shortcuts.py`；guidance 内容视为数据，可缓存/版本化。
- Guidance 不得包含凭据/用户屏幕内容；只放平台操作规则（如“BACK 是左边缘手势”）。

### D8 — 权限与诊断

- `PermissionChecks` 返回 `available/unavailable/unknown` + 修复提示（例如 macOS Screen Recording、iOS AssistiveTouch）。
- 权限失败映射 `capability_unavailable`，不得退化为“假装成功”。

### D9 — 迁移与删除

- Phase 4.3：
  1. 定义 Extension/Port DTO（core/devices）。
  2. iOS Extension 适配现有 `ios_apps`/`ios_targets`/`control/hid`/`adb` 逻辑。
  3. Profile 注册 Extension；Agent/Prompt 改从 Profile 读取 guidance。
  4. 删除 `mcp/action_executor.py`/`adb.py` 的 `is_ios_device` 分支；保留一个兼容开关。
  5. 测试：Fake Extension + iOS/Android 回归 + LocateAnything off/down。
- Phase 9-A 删除旧 `ios_shortcuts` 入口（调用方清零后）。

### D10 — 非目标

- 不做第三方动态 Extension 市场。
- 不把所有 iOS 代码搬到同一个目录；只移动语义/策略边界。
- 不改变 iOS 左边缘 BACK/Spotlight 的行为。
- 不让 Extension 直接调用 LLM 或 Planner。

---

## 5. Consequences

### Positive

- L1–L3 不再出现 `is_ios_device`；新增 macOS/TV 只加 Extension/Profile。
- BACK/Launch/Text/Locator 的语义与物理实现分离，可测试。
- LocateAnything 变成可复用 Grounding，而不是 iOS 隐藏分支。

### Negative / trade-offs

- Extension/Port 增加一层间接；迁移期旧分支与新 Port 并存。
- Prompt Guidance 需要版本/缓存管理。
- 新增 `grounding.locate` 能力需要契约/ADR-001 catalog 同步。

### Follow-up work

- Phase 4.3：iOS Extension + Ports。
- Phase 5：Action/Verification 使用语义动作与 locator 证据。
- Phase 7/8：macOS/TV Extension（权限/Remote/CEC）。

---

## 6. Contract and dependency impact

- **Layers**：L4 Platform Extension；L3 消费 Port；L2 只接收结构化 guidance。
- **Ports / DTOs**：`PlatformExtension`、`AppLifecyclePort`、`NavigationPolicy`、`TextInputPolicy`、`TargetAdjuster`、`TargetLocator`、`PromptGuidance`、`PermissionChecks`；新增 `grounding.locate` capability ID。
- **Adapters/Profiles**：iOS Profile 组合 UVC/HID + iOS Extension；Android 复用 package manager；TV Remote/App list。
- **Import direction**：Extension 不 import Agent/Graph/Checker/MCP；L1–L3 不 import 具体 Extension。
- **Compatibility**：`press_key("BACK")`、`launch_app`、`is_ios_device` 旧入口保留到 Phase 9-A；行为等价。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| 4.3a | Extension/Port DTO + Fake Extension 测试 | 无 |
| 4.3b | iOS AppLifecycle/Navigation/Text/TargetAdjuster 适配 | 无（等价） |
| 4.3c | LocateAnything 注册为 TargetLocator + `grounding.locate` capability | 无（开关控制） |
| 4.3d | Agent/Prompt guidance 从 Profile 注入；删除 MCP `is_ios_device` | 无（等价） |
| Phase 9-A | 删除 `ios_shortcuts`/旧分支 | 有（清理） |

**Non-goals**：动态插件、跨平台统一 Prompt、改变 BACK/launch 行为。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/devices/test_platform_extension.py`（新增）：
  - Fake Extension 提供 back/launch/text/locator；Action Executor 不出现平台字符串；
  - 缺失 Port → `capability_unavailable`；
  - guidance 只通过 Profile 注入一次。
- iOS 回归：BACK 左边缘、Spotlight 启动、pinyin、图标抬升、LocateAnything off/down。
- Android 回归：无 iOS import/分支；back keyevent、package launch、中文输入。
- Locator：HTTP 健康/超时/回退；`grounding.locate` 状态与 evidence。
- Hardware：iOS 真机 smoke + 最小任务；Android smoke。

### Observability

- 日志/Trace：`extension_id`、端口选择、locator 结果、fallback 原因、guidance 版本。

---

## 9. Rollback

- Extension/Port additive；开关 `ARTEMIS_PLATFORM_EXTENSIONS=0` 走旧 `is_ios_device` 分支。
- 不删除旧 `ios_shortcuts`/MCP 分支，直到 Phase 9-A 验收。
- 不改 MCP schema/CLI 参数；Prompt 注入可关闭。

---

## 10. Security and safety

- Extension 是受信代码；不得接受用户任意脚本/动态导入。
- Guidance 不进入日志/凭据；避免把屏幕内容放进 prompt guidance。
- 权限失败显式拒绝；不伪造 UI Tree/App Launch 成功。
- Locator 失败不得静默当作已验证；保留 `target_unverified` 证据。

---

## 11. Resolved / open questions

已提议：

- Extension + 可选 Port；语义动作/物理输入分离。
- LocateAnything 作为通用 TargetLocator，新增 `grounding.locate` 能力。
- iOS BACK/Spotlight/pinyin/图标抬升行为保持。
- 旧 `is_ios_device` 分支保留到 Phase 9-A。

待定（不阻塞 4.3a）：

1. `grounding.locate` 的正式命名与 domain（Observation vs 新 Grounding 域）。
2. `NavigationPolicy` 是返回 Command 还是直接调用 ControlSink（建议返回 Command，保持 Action 层可审计）。
3. PromptGuidance 的 schema/版本（纯文本 vs 结构化段落）。
4. macOS/TV Extension 是否共用 `PermissionChecks` 还是各自实现。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial proposal | iOS 知识散落 L1–L3；BACK/launch/text/locator 用平台分支；Phase 4.3/7/8 需要 Extension 契约 |
