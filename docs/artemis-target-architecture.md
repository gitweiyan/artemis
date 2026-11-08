# Artemis 目标架构 v1（已废弃）

> **Superseded（2026-11-08）**：当前架构以
> [`docs/architecture/ARCHITECTURE_CONTRACT.md`](architecture/ARCHITECTURE_CONTRACT.md) 为唯一规范，
> 迁移顺序以 [`docs/architecture/REFACTOR_EXECUTION_PLAN.md`](architecture/REFACTOR_EXECUTION_PLAN.md) 为准。
> 本文的 §4 数据模型、§10 代码落位与“现有实现吸收”表**已被取代**：例如
> `reflex/control.py::Esp32HidControl` 与 `reflex/mouse.py::PointerSession` 不再迁往 `control/hid.py`，
> 而是下沉到 L4 Actuation/Pointer Adapter。本文只作历史背景，不得作为实现依据。

Status: Superseded (v1, replaced 2026-11-08)  
Scope: 统一 Android / iOS / Mac / 平板 / 外接摄像头的感知、控制、决策与编排  
Related: [iOS UVC 执行计划](ios-uvc-execution-plan.md)

## 1. 背景

当前 Artemis 的核心编排能力（Planner / Operator / Explorer / Validator、
flash/pro profile、skill、memory）是平台无关的，但设备能力仍被
`BaseDeviceDriver` 和 Android 专属实现绑在一起。iOS 被做成了
`scripts/ios_*.py` 的旁路流程，导致：

- Planner / Validator / Explorer 看不到 iOS 的动作；
- 同一套“定位 → 点击 → 验证”逻辑被重复实现；
- iOS 的 UVC 感知、ESP32 HID 控制无法复用现有安全网；
- flash/pro 编排档位无法作用于 iOS。

目标是把“平台”降级为能力组合，把 iOS 变成 Artemis 的一个
`PerceptionSource + ControlSink`，而不是另一条产品线。

## 2. 设计原则

1. **平台不是流程，平台是能力组合。**
2. **感知、控制、决策、编排、技能相互正交。**
3. **安全校验是横切层**，只依赖 capability，不依赖平台名。
4. **模型做擅长的事**：VLM 负责语义/选择，CV/OCR/ER 负责精确框，
   HID 状态负责相对控制。
5. **没有验证就不执行**：目标不存在、指针不一致、置信度低时不点击。
6. **所有坐标归一化**：内部统一 0–1000，平台实现负责像素换算。
7. **失败必须可解释**：shifted / covered / disappeared / pointer_missing /
   low_confidence / no_effect。

## 3. 架构总览

```text
                         ┌─────────────────────┐
                         │      Task / Goal     │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │     Orchestrator     │
                         │ profile: flash/pro   │
                         │ explorer: f/p/ultra  │
                         │ skill: generic/xxx   │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
     ┌────────▼────────┐   ┌────────▼────────┐   ┌────────▼────────┐
     │ PerceptionSource│   │   ControlSink   │   │ DecisionProvider│
     │ app/uvc/camera  │   │ app/hid         │   │ deepseek/jev/qwen│
     └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      SafetyNet       │
                         │ XML / pixel / pointer│
                         │ pre / post / incident│
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ Telemetry / Replay   │
                         └─────────────────────┘
```

## 4. 核心接口

### 4.1 数据模型

```python
@dataclass(frozen=True)
class Frame:
    image: bytes
    captured_at: float
    width: int
    height: int
    source: str
    health: str          # ok / black / blocked / stale

@dataclass(frozen=True)
class Target:
    label: str
    box: tuple[float, float, float, float]  # normalized 0..1
    confidence: float
    source: str          # ocr / er / template / xml

@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: str
    incident: str | None
    detail: str
```

### 4.2 PerceptionSource

```python
class PerceptionSource(Protocol):
    capabilities: PerceptionCapabilities

    async def snapshot(self) -> Frame: ...
    async def ui_tree(self) -> list[UIElement] | None: ...
    async def ocr(self, frame: Frame) -> list[TextBox] | None: ...
    async def locate(self, frame: Frame, target: Target) -> Target | None: ...
```

实现：

- `AppAutomationSource`：Android automation2 / Artemis helper；
  iOS WDA/XCUITest（若可用）；Mac accessibility。
- `UvcSource`：HDMI 采集卡，跨 Android / iOS / Mac / 平板。
- `CameraSource`：屏幕前物理摄像头。
- `CompositeSource`：app 树提供语义，UVC 提供像素验证。

能力：

```python
supports_ui_tree: bool
supports_ocr: bool
supports_locate: bool
is_external: bool
```

### 4.3 ControlSink

```python
class ControlSink(Protocol):
    capabilities: ControlCapabilities

    async def tap_abs(self, x: int, y: int) -> ActionResult: ...
    async def tap_rel(self, dx: int, dy: int) -> ActionResult: ...
    async def swipe(self, ...) -> ActionResult: ...
    async def input_text(self, text: str) -> ActionResult: ...
    async def press_key(self, key: Key) -> ActionResult: ...
```

实现：

- `AppControlSink`：ADB / helper / WDA，绝对坐标。
- `HidControlSink`：ESP32 HID，相对鼠标 + 键盘。
- `CompositeControlSink`：优先 app 控制，HID 兜底。

能力：

```python
supports_absolute_tap: bool
supports_relative_pointer: bool
requires_pointer_precheck: bool
supports_text_input: bool
```

### 4.4 DeviceSession

```python
class DeviceSession:
    perception: list[PerceptionSource]
    control: list[ControlSink]
    geometry: ScreenGeometry
    capabilities: CapabilitySet
```

上层只依赖 `DeviceSession`。`BaseDeviceDriver` 保留为兼容 façade，
内部组合 `DeviceSession`，不再承担平台分支。

> 现状（2026-10-21）：iOS 驱动持有 `session`；`AndroidAdbDriver.session` 惰性构建
> `build_app_session(self)`（app 组合）。工厂对两种平台都会记录所选组合与理由。

## 5. 能力矩阵

| 能力 | Android | iOS | Mac/平板 | 说明 |
|---|---|---|---|---|
| App 感知 | automation2 / helper / UI XML | WDA/XCUITest（需签名）；否则无 | accessibility | 语义最强，平台受限 |
| UVC 感知 | HDMI/采集卡 | HDMI/采集卡 | 采集卡 | 通用；可能 HDCP/letterbox |
| Camera 感知 | 物理摄像头 | 物理摄像头 | 物理摄像头 | 完全黑盒；标定/遮挡/延迟 |
| App 控制 | ADB / helper 绝对点击 | WDA 绝对点击（需签名） | accessibility | 精确 |
| HID 控制 | ESP32 相对 | ESP32 相对 | ESP32 相对 | 通用；需状态/预检 |

选择原则：

```text
有 app 树 + 绝对点击 → app 感知 + app 控制（最快最稳）
无 app 树 + 有 UVC → UVC + HID（需 pointer precheck）
无 UVC → Camera + HID
有 WDA → WDA + UVC 验证
```

## 6. 编排模型

### 6.1 Execution Profile

| Profile | 结构 | 适用 |
|---|---|---|
| `flash` | 单 Agent 轻量循环 | 简单、低风险、确定性任务 |
| `pro` | Planner + Operator + Validator 多节点图 | 复杂、高风险、需验证 |
| `skill` | 固定闭环，可绕过逐帧 LLM | 俄罗斯方块、固定流程 |

### 6.2 Explorer Tier

| Tier | 感知深度 |
|---|---|
| `flash` | 单次视觉/OCR 检测 |
| `pro` | 3 轮 UI 树/坐标审计 |
| `ultra` | 8 轮 zoom/OCR/pixel 处理 |

### 6.3 选择逻辑

```text
latency_budget + risk + observability + task_novelty
→ execution profile
→ explorer tier
→ perception/control composition
→ skill override
```

示例：

```text
俄罗斯方块：profile=skill, perception=uvc/camera, control=hid, decision=none
淘宝下单：profile=pro, explorer=pro/ultra, perception=uvc+ocr, control=hid
设置页切换：profile=flash, explorer=flash, perception=uvc, control=hid
```

`flash/pro` 与 `explorer flash/pro/ultra` 都是编排档位，不是模型，
也不是平台。

## 7. SafetyNet

统一安全层：

```text
pre_target:
  有 UI 树 → XML/bounds 校验
  无 UI 树 → pixel crop / OCR / ER 置信度

pre_pointer:
  相对 HID → 指针与目标 ≤ 容差
  绝对点击 → 不适用

post_action:
  画面 diff / 目标出现 / 状态变化

incident:
  shifted / covered / disappeared / pointer_missing /
  low_confidence / no_effect
```

规则：

- SafetyNet 读 `session.capabilities`，不写 `if platform == "ios"`。
- `xml` 不可用 → `pixel`；`pixel` 不可用 → `low_confidence`，不点击。
- 相对 HID 的动作必须经过 pointer precheck。
- 点击后无变化必须返回 `no_effect`，不能静默成功。

## 8. 坐标与时序

- 内部坐标 0–1000；`ScreenGeometry` 负责：
  - 原始帧 / crop / letterbox；
  - 设备像素 / 归一化；
  - 目标框与指针位置换算。
- 感知帧带 `captured_at`；动作执行前检查帧龄。
- 相对控制使用 `PointerBelief`：
  - `home` 作为已知原点；
  - move 是唤醒动作，不需要额外 jiggle；
  - move 后立即抓帧；
  - 指针与目标一致才点击。
- gain 分段/自适应，不假设常数。

## 9. 观测与回放

每次动作记录：

```text
task_id / step_id
profile / explorer_tier / skill
perception_sources
frame metadata + target crop
target box + confidence + source
action + pointer position + error
precheck verdict
postcheck verdict / incident
latency
```

用途：失败分析、回放、模型/参数评估、回归测试。

## 10. 代码落位与迁移

目标路径：

```text
artemis/orchestration/
  profiles.py    # 已落位：profile/explorer 决策（带 reason），CLI 使用并记录
  selector.py    # 已落位：DeviceProfile -> Composition 纯函数
artemis/perception/
  base.py
  uvc.py          # 已落位
  camera.py       # 已落位（可注入 grabber；黑帧降级）
  app_automation.py  # 已落位（UI 树源）
  composite.py    # 已落位（UVC 黑屏落到相机）
artemis/control/
  base.py
  hid.py          # 已落位
  app_control.py  # 已落位（绝对控制）
  composite.py    # 已落位（绝对优先，失败不换面重试）
artemis/safety/
  xml.py          # 已落位（bounds/IoU/最具体元素；validator 只留阈值）
  pixel.py        # 已落位（目标 crop 优先，整帧兜底）
  pointer.py      # 已落位（指针/目标一致性）
artemis/devices/
  session.py
  geometry.py
  app_session.py  # 已落位（app 组合）
artemis/skills/
  manifest.py
  tetris/
```

现有实现吸收：

| 现有 | 目标 |
|---|---|
| `artemis/reflex/capture.py::UvcCapture` | `perception/uvc.py` |
| `artemis/reflex/control.py::Esp32HidControl` | `control/hid.py` |
| `artemis/reflex/mouse.py::PointerSession` | `control/hid.py` 的状态 |
| `SessionTapController` | `safety/pointer.py` |
| `RedPointerDetector` | `perception/uvc.py` 或 `safety/pointer.py` |
| `ios_pointer_servo.py` / `ios_mouse_probe.py` | 仅诊断脚本，不进入任务流程 |

迁移顺序：

1. 定义 interfaces / capability / session；
2. `BaseDeviceDriver` façade 包装旧驱动；
3. iOS UVC + HID 组合；
4. pointer / pixel safety；
5. flash/pro 接入；
6. skill 接入；
7. telemetry / replay。

## 11. 非目标

- 不把 Android UI XML 强行模拟到 iOS；
- 不让 VLM 直接回归像素级 bbox；
- 不把 UVC 做成 30fps 常驻流；
- 不为 iOS 复制一套 Planner/Validator；
- 不在无信号/低置信度时盲点。

## 12. 风险与决策

| 风险 | 处理 |
|---|---|
| iOS 无第三方 UI 树 | 用 UVC/OCR/ER；WDA 可选 |
| iOS 无绝对触控 | 相对 HID + 状态 + 预检 |
| 指针淡出 | move 后立即抓帧；异常时 home 重锚 |
| gain 非线性 | 分段校准 + 闭环修正 |
| UVC 黑帧/HDCP | 健康检查；Camera/WDA 降级 |
| OCR key 不可用 | OCR 作为可选 source；低置信度拒绝 |
| 模型坐标弱 | 专用 ER/OCR 出框，VLM 只选择 |
| App 自绘控件忽略 click | 点击后验证；失败上报/换路径 |

## 13. 与 iOS UVC 的关系

iOS UVC 不是单独流程，而是：

```text
PerceptionSource = UvcSource
ControlSink = HidControlSink
CapabilitySet:
  ui_tree = False
  absolute_tap = False
  relative_pointer = True
  requires_pointer_precheck = True
```

`flash` / `pro` 都复用这套组合，只是编排深度不同。具体落地方案见
[iOS UVC 执行计划](ios-uvc-execution-plan.md)。
