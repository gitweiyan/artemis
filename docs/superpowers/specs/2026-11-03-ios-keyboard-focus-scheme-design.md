# iOS 键盘焦点方案设计（2026-11-04 修订版）

> **状态：暂缓（2026-11-04）**。绝对坐标 HID 的 spike 已成功（iOS 18 接受，中位 1.6 px、绝对点击生效），
> 先落地绝对 HID；本方案保留，等绝对 HID 集成稳定后再继续。

> 修订说明：按评审意见重写。P0 全部吸收（常驻采集/新鲜帧接口、容器过滤改为多维评分、输入框方向键保护、
> Space 验证三态、设备/页面组/目标三级能力缓存），P1 全部吸收（Shift+Tab、循环与无进展检测、步数与时间
> 双预算），P2 保留（Tab-F Find、键盘命令）。两处按机台实测做了限定：常驻采集在本采集卡上会冻结、
> `kind` 无 UI 树只能启发式——见 §3 与 §5.3。全文提到的"键盘"均指 iOS 全键盘控制（FKA）。

## 1. 意图与定位

iOS 机台现在有三种操控机制，它们不是替代关系：

| 机制 | 本质 | 能力 |
|---|---|---|
| Relative Mouse（现状） | 物理位移 | 点击、拖动、滚动；有增益标定与闭环 |
| Absolute Mouse（待验证） | 坐标直接定位 | 快速精确定位；ghosthand 实测 iOS 17+ 约 0.3 s/次 |
| Full Keyboard Access（本方案） | UI 焦点导航 | 遍历/激活可聚焦控件；语义化，不依赖像素坐标 |

**定位：FKA 是具有语义优势的补充通道，不是通用点击引擎。** 焦点覆盖取决于 App 是否让自定义视图参与
FKA（Apple WWDC 2021 "Support Full Keyboard Access in your iOS app"，session 10120；控件可用
`accessibilityUserInputLabels` 提供可搜索别名）。支付宝首页实测：搜索栏与 `+` 可聚焦，宫格与商品卡片
不可聚焦——这就是本方案的边界，不是缺陷。

## 2. 目标 / 非目标 / 反目标

**目标**

- 在不改 Agent 动作语义的前提下，让 harness 能给"点某个控件"自动选择键盘或指针。
- 键盘路径：单步导航 → 视觉确认目标被焦点选中 → Space → 效果验证；失败安全回退指针。
- 零误激活：没有确认焦点在目标上时，绝不按 Space。

**非目标**

- 不替换指针；不做拖拽/滑动/多击；不改 `locate`；不改 Agent schema。
- 不在 V1 做通用焦点规划器（不做全页面焦点图，不做 Tab-F Find，不做命令绑定）。
- 不尝试用代码打开 FKA 开关（系统设置，人工开启一次）。

**反目标**

- 未确认焦点就按 Space（支付/删除/开关不可逆）。
- Space 已发送后因"没看到效果"而回退指针再点一次（重复激活）。
- 页面内容已变化仍沿用旧坐标（滚动/跳转后盲点）。

## 3. 约束（实测，2026-11-03/04）

| 约束 | 事实 | 影响 |
|---|---|---|
| 单帧延迟 | frame 模式每次抓帧 ~0.95 s（ffmpeg 启动+UVC） | 单步 ≈1 s；150 ms 目标是**有条件**的 |
| 常驻采集 | 本采集卡上常驻 AVFoundation 会话返回**冻结帧**（playbook §4） | 先重测；不行就保持 frame 模式 + 时间预算 |
| 焦点框 | 蓝/白细圆角描边；可能罩住**整屏**；会 Auto-Hide | 容器过滤必须保留；按键后要立即抓帧 |
| 单步行为 | Tab 切组、方向键组内移动、文本域里方向键移光标 | 导航要区分元素类型 |
| 首键空转 | 第一次按键可能什么都不做（ghosthand 实测） | 连续 2 次无新焦点才算失败 |
| 危险组合 | `ctrl+alt+shift+cmd+s` = 紧急 SOS、`...r` = 重启 | 按键白名单，绝不发 |
| 键盘输入 | 只能发 US-ASCII（无中文 IME） | Tab-F Find 仅限拉丁名（P2） |
| 聚合 | App 内不同页面焦点能力差异大 | 能力缓存必须到"页面组/目标"级 |

## 4. 已定（用户决定）

1. Agent 只表达"点某个控件"，**不感知** keyboard/pointer；方案由 harness 配置决定。
2. 键盘优先、失败降级指针（可配置）。
3. 验收看**操作完成率**与**时长**。

## 5. 设计

### 5.1 契约：Agent 动作不变，harness 内部选路

```python
@dataclass(frozen=True)
class TapRequest:
    target_point: tuple[float, float]
    target_bbox: tuple[float, float, float, float] | None = None
    expected_effect: str | None = None      # "screen_change" | "region_change" | None

@dataclass(frozen=True)
class ActionResult:
    success: bool
    scheme: str                             # "pointer" | "keyboard"
    elapsed_ms: int
    incident: str | None
    metrics: dict
```

`HidControlSink.tap_abs(x, y)` 签名不变；`TapRequest`/`ActionResult` 是内部结构化结果。**scheme 只存在于
harness 配置层**（`ARTEMIS_IOS_KEYBOARD_SCHEME=pointer|keyboard|auto`），不进入动作 schema；测试用
`_scheme_override` 仅内部可见。

### 5.2 观测：新鲜帧接口（P0）

`Frame` 增加 `sequence: int`；`Capture` 增加：

```python
async def wait_next_frame(self, after_sequence: int, timeout_s: float) -> Frame
```

- `UvcStream`（常驻）：从环形缓冲取**序号大于** `after_sequence` 的最新完整帧；返回前不早于按键时刻。
- `UvcCapture`（frame 模式）：实现为"一次 `grab()` + `captured_at >= 按键时刻` 校验"（已有 `not_before` 逻辑）。
- 按键与抓帧之间不得复用按键前的缓存帧；拿不到新帧返回 `capture_timeout`，不计入焦点失败。

**本采集卡的现实**：常驻会话冻结是已知问题。V1 先重测 `ARTEMIS_IOS_CAPTURE=stream`；若复现冻结，保持
frame 模式，走时间预算（§5.7），性能目标相应放宽（§6）。不把"150 ms"当成未经验证的承诺。

### 5.3 焦点检测（P0 修正容器过滤）

新增 `artemis/reflex/focus.py`：

```python
@dataclass(frozen=True)
class FocusBox:
    x1: int; y1: int; x2: int; y2: int
    confidence: float
    kind: str          # "input" | "button" | "list_item" | "group" | "unknown"
    appeared: bool     # 必须是 after 帧新出现的；vacated 只作证据

def locate_focus(before: bytes, after: bytes, *, frame_size) -> FocusBox | None
```

- **appeared / vacated 分离**：一次按键的差异通常含两个描边（旧焦点消失、新焦点出现）。只有 appeared 能
  成为当前焦点；vacated 记录为"焦点确实转移"的证据，绝不用于 Space。
- **容器判定改为多维评分**（评审 P0，原单轴 60% 规则会误杀全宽搜索栏）：

| 特征 | 容器倾向 | 正常控件 |
|---|---|---|
| 同时占据大部分宽与高（宽 ≥85% 且高 ≥50%） | 是 | 否 |
| 与 ≥3 条屏幕边重合 | 是 | 否 |
| 内部包含 ≥2 个其他 appeared 候选 | 是 | 否 |
| 与目标框/目标点匹配 | 否 | 是 |

  判定为容器才丢弃；全宽但矮的搜索栏（如宽 100%、高 10%）是**有效焦点**。多个候选时取"包含目标点且
  面积最小"的那个。
- **kind 分类**（无 UI 树，启发式）：宽而矮且激活后底部出现键盘 → `input`；与已记录的列表行高度一致 →
  `list_item`；被多个候选包围的焦点 → `group`；其余 `unknown`。分类错误时按 `unknown` 走安全策略。
- 置信度不足（轮廓不闭合/面积异常/与目标框重叠为 0）→ 不返回 FocusBox，**不允许 Space**。

### 5.4 导航策略（P0/P1）

```python
def choose_key(focus: FocusBox | None, target, history) -> str:
    if focus is None:
        return "tab"                      # 首次建立焦点
    if focus.kind in ("input", "unknown"):
        return "tab"                      # 输入框里方向键会移光标/改选择
    if focus.kind in ("list_item", "group"):
        if last_arrow_made_no_progress:
            return "tab"                  # 方向无进展 → 换 Tab 组导航
        return direction(focus, target)   # 主轴方向
    return "tab"
```

- **Shift+Tab**：`press_named_key` 增加 `shift+tab`（固件 `keymod 2 43`），用于回退焦点。
- **无进展/循环检测**：同一方向连续 2 次无 appeared；或当前 bbox 与历史 bbox 重合（容差 8px）→ 判循环，
  先试 Tab，再失败则 `focus_not_on_target`。
- **输入框识别**：Space 后底部区域出现键盘/大幅变化 → 把该元素标为 `input` 并停止方向键。
- **焦点框 Auto-Hide**：每步按键后立即取新帧（§5.2），帧龄超预算按 `capture_timeout` 处理。

### 5.5 激活与验证（P0：三态，不再只看焦点框内部）

状态机：

```
UNPROBED → PROBING → FOCUSING → TARGET_CONFIRMED → ACTIVATION_PENDING → VERIFIED
                                                   ├→ NOT_CONFIRMED（UNKNOWN，不重试）
                                                   ├→ UNEXPECTED_TRANSITION（INCIDENT）
                                                   └→ 仅 TARGET_CONFIRMED 之前允许 FALLBACK
```

Space 后的验证：取 Space 前帧与之后 1 帧（必要时再 1 帧）：

- `SUCCESS`：目标框扩展区（target_bbox 外扩 8px）变化 ≥ 阈值，**或**全屏结构变化（键盘弹出、页面跳转）。
- `UNEXPECTED_TRANSITION`：目标区域没变但页面发生大幅变化（可能是**别的**元素被激活）→ 返回 incident，
  不重试。
- `NOT_CONFIRMED`：目标与全屏都无变化 → 返回 `activation_unknown`（UNKNOWN），**不重试、不回退指针**；
  等待下一次 Agent 观察再决策。

硬规则：Space 已发送后，任何路径都不允许再次点击同一目标。`expected_effect` 可选地要求"区域变化"，
缺省用上表。

### 5.6 能力缓存：设备 / 页面组 / 目标（P0）

```python
capability = {
  "device": "enabled" | "unavailable",
  "pages": {
     "<page_fingerprint>": {          # 帧降采样 dhash，排除焦点框区域
        "header": "reachable",
        "content_grid": "unreachable",
     }
  },
  "targets": {
     ("<page_fingerprint>", "<target_label 或量化坐标>"): "reachable" | "unreachable" | "unknown"
  }
}
```

- 页面身份用**稳定指纹**（降采样 dhash + 可选 OCR 标题），不能只用 App Bundle ID。
- 熔断按**目标组/目标**记录：支付宝搜索栏失败 3 次不影响宫格；宫格失败不封锁整页。
- 记录内容：失败分类（§5.7）+ 次数 + 最近一次时间；页面重新加载（指纹变化）时失效。

### 5.7 `auto` 策略：能力驱动，双预算

```python
AUTO_FOCUS_MAX_STEPS = 2          # 未知目标的低成本探测
AUTO_FOCUS_TIMEOUT_MS = 2500      # 步数或时间，先到即回退
KEYBOARD_DEBUG_MAX_STEPS = 8      # 仅 keyboard 模式/调试

async def tap_auto(req, capability):
    state = capability.for_target(req)
    if state == "unreachable": return await pointer_tap(req)
    if state == "reachable":   return await keyboard_tap(req, timeout=AUTO_FOCUS_TIMEOUT_MS)
    result = await keyboard_probe(req, max_steps=2, timeout_ms=2500)
    if result == "verified":  return result
    if result == "page_moved": return RELOCATE_REQUIRED      # 不回退，重新 locate
    if result == "activation_unknown": return UNKNOWN        # 不回退
    capability.record(req, "unreachable")
    return await pointer_tap(req)
```

失败分类（熔断按分类计数，不按单一 `focus_not_found`）：`focus_unavailable`、`focus_not_found`、
`focus_not_on_target`、`capture_timeout`、`focus_detection_error`、`page_moved`、`activation_unknown`、
`activate_unexpected`。只有确认为"目标不可聚焦/反复不可达"才降级该目标组。

### 5.8 集成点与预算

- 新文件：`artemis/reflex/focus.py`（检测/评分/kind）、`artemis/reflex/focus_controller.py`（状态机）。
- 改：`artemis/reflex/capture.py`（`Frame.sequence`、`wait_next_frame`）、`artemis/reflex/control.py`
  （`shift+tab`）、`artemis/control/hid.py`（harness 选路、`tap_abs` 不变）、`artemis/devices/ios_uvc.py`
  （构造 focus 控制器）。
- 配置：`ARTEMIS_IOS_KEYBOARD_SCHEME=pointer|keyboard|auto`（默认 `auto`）；预算常量见 §5.7。
- 指标：`scheme`、`focus_steps`、`focus_kind`、`focus_confidence`、`activation_state`、`elapsed_ms`、
  `fallback_reason`、`page_fingerprint`。

## 6. 性能目标（有条件）

| 指标 | 目标 | 前提 |
|---|---|---|
| 按键发送 | ≤50 ms | 现状满足（35–50 ms） |
| 按键→新帧 P50 / P95 | ≤150 / ≤300 ms | **仅当常驻采集可用**；本卡若仍冻结，V1 用 ~0.95 s，不承诺该值 |
| 焦点检测 P95 | ≤30 ms | 纯 CPU，可测 |
| 键盘单次 P50 | ≤4 s | 含 2–4 步、Space 与验证 |
| 回退额外耗时 | ≤2.5 s | 熔断后不再付 |

## 7. 业界实践与整体优先级

- **ghosthand**（<https://github.com/ghosthand-dev/ghosthand/blob/main/README.md>）：主力是**绝对 HID 指针**
  （iOS 17+ ~0.3 s，精确）；FKA 可选、默认关闭；确认焦点框蓝色/可罩整屏/Auto-Hide、首键可空转、危险组合键。
- **iEasyClick**（<https://blog.ieasyclick.net/blog/ios-shortcuts-keyboard-control/>）：用 FKA **命令**
  （`Cmd+字母` 绑快捷指令/系统命令），手机侧手工绑一次，确定性强，适合固定动作。
- **Apple**：FKA 的 Tab/Space 文档化（<https://support.apple.com/en-ie/guide/iphone/ipha4375873f/26/ios/26>）；
  WWDC 2021 session 10120 要求 App 主动让自定义视图参与 FKA 并支持 `accessibilityUserInputLabels`。

**优先级**（评审建议，采纳）：Absolute HID **先验证**（若 ESP32-S3 绝对报文被 iOS 接受，通用点击最直接）；
FKA 并行（列表/表单/搜索栏）；Relative HID 保留（拖动/滚动/绝对定位不支持的交互）。

## 8. 验收（三组，误激活为硬门槛）

**1. 基础能力**（每场景 ≥10 次）：Settings 列表（Tab/方向键）、支付宝搜索栏（单个大控件）、支付宝宫格
（不可聚焦 → 回退）、Safari 搜索框（文本域）、滚动列表（焦点导致滚动）、连续导航（Auto-Hide/首键空转）。

**2. 安全性**（硬门槛，任一不满足即不发布）：

| 场景 | 预期 |
|---|---|
| 目标附近多个焦点框 | 不误激活 |
| 旧焦点框残留 | 不误激活 |
| 页面跳转 | 不使用旧坐标（RELOCATE_REQUIRED） |
| Space 无可观察效果 | 不再次点击，返回 UNKNOWN |
| 焦点置信度不足 | 不激活 |
| 搜索框获得焦点 | 不误用方向键编辑文本 |
| **False Activation Rate** | **必须为 0** |

**3. 性能**（每类 ≥30 次，关键场景 100 次）：

| 指标 | V1 |
|---|---|
| 键盘命中率 | ≥ 同场景 Pointer 基线（分页面测，不混合） |
| 键盘 P50 / P95 | ≤4 s / 记录并逐步优化 |
| 回退额外耗时 | ≤2.5 s |
| 不可聚焦目标回退率 | 100% |

## 9. 分阶段与后续

| 优先级 | 项 | V1 |
|---|---|---|
| P0 | 常驻采集 + `wait_next_frame`（含本卡冻结的复测与降级） | 是 |
| P0 | 容器多维评分 + appeared/vacated | 是 |
| P0 | 输入框/未知元素不走方向键 | 是 |
| P0 | Space 三态验证 + 不重试/不回退 | 是 |
| P0 | 三级能力缓存 + 分类熔断 | 是 |
| P1 | Shift+Tab、循环/无进展检测、步数+时间双预算 | 是 |
| P2 | Tab-F Find（仅拉丁名）、FKA 命令绑定 | 否 |
| 前置 | **Absolute HID 可行性 spike**（ESP32-S3 绝对报文 → iOS） | 先做 |

## 10. 我的默认值（构建者可改，改前说一声）

- 容器评分阈值：宽 ≥85% 且高 ≥50%、或触边 ≥3、或包含 ≥2 个候选；其余按有效焦点。
- 置信度门槛：轮廓闭合且与目标框重叠 >0；低于此不 Space。
- 激活验证：目标框外扩 8px 变化 ≥0.5% 区域像素，或全屏变化 ≥2%（排除焦点描边带）。
- 能力缓存过期：页面指纹变化即失效；同一目标连续 3 次失败标记 unreachable。
- `auto` 探测预算：2 步 / 2500 ms；`keyboard` 调试预算 8 步。
