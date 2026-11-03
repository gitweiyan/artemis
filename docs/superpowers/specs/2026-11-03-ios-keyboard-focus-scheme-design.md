# iOS 键盘焦点方案设计（2026-11-03）

## 1. 意图与为什么

iOS 机台现在的操控链是 `locate → move → click`：`move` 用相对 HID 指针、`click` 在验证过的坐标上点击。
指针方案慢（一次 841 px 的移动约 3.6 s）、依赖指针可见、且蓝环压在蓝色界面上会检测不到（2026-11-03
真机基准：淘宝页刷新后同一条路径 0/10）。

用户提出的第二套方案：**打开"全键盘控制"（Full Keyboard Access）后，用 Tab/方向键移动焦点、Space 激活**。
本质是给 `move + click` 增加一种操控方案，与指针方案并存，指针方案仍是回退。

真机 spike（2026-11-03，`/tmp/focus_*.png`、`/tmp/alipay_*.png`）确认了前提：

- 机台已开启全键盘控制；无焦点时按一次 Tab 就会出现焦点高亮。
- 高亮是**元素外的一圈细圆角描边**：主屏是白色（日历图标、搜索框、Dock 图标），支付宝首页是蓝色
  （搜索栏整条），没有高亮时同一位置无描边。**颜色随 App 变，不能写死**。
- Tab 走辅助功能顺序（首页：日历 → 搜索），方向键走空间跳转（搜索框 ↓ → Dock 的 Safari）。
- 单步耗时：按键 35–50 ms，抓帧约 0.95 s（frame 模式），合计约 1 s/步。
- **覆盖有限**：支付宝首页连按 5 次 Tab/方向键，焦点始终停在顶部（搜索栏、+ 按钮），正文宫格/商品卡片
  没有焦点。键盘只能到达"可聚焦元素"，不是所有点击目标都能走这条路。

## 2. 目标 / 非目标 / 反目标

**目标**

- 给 `move + click` 增加第二套实现：单步按键 → 抓帧 → 判断目标是否被焦点选中 → 是则 Space。
- 与现有 `locate` 结果（目标点/框）共用输入；与指针方案共用"动作是否生效"的验证语义。
- 失败时安全回退到指针方案；全程不产生盲点。

**非目标**

- 不替换指针方案，不改 `SessionTapController` 的移动模型。
- 不做拖拽/滑动（指针专属）；不改文本输入（已走键盘）；不改 `locate`。
- 不尝试用代码打开全键盘控制（系统设置项，只能由人开启）。

**反目标（技术上成立但不可接受）**

- 没有确认焦点在目标上就按 Space（可能触发支付、删除、开关切换）。
- 键盘尝试失败后自动按 Space 作为"补救"。
- 为没有焦点高亮的页面做盲目的 Tab 遍历点击。

## 3. 约束（实测）

| 约束 | 数值/事实 |
|---|---|
| 单步耗时 | 键 35–50 ms + 抓帧 ~0.95 s ≈ 1 s |
| 高亮样式 | 细圆角描边；主屏白、支付宝蓝；颜色不固定 |
| 焦点顺序 | Tab = 辅助功能顺序；方向键 = 空间跳转；文本域里方向键移光标 |
| 可聚焦范围 | 系统控件/表单/搜索栏；自绘宫格与卡片通常不可聚焦 |
| 帧 | 449×974，frame 模式每次抓帧都是一次新 ffmpeg 调用 |
| 设备开关 | 全键盘控制无 API 可查，只能行为探测 |
| 现有按键能力 | `Esp32HidControl.press_named_key` 已支持 `tab/space/up/down/left/right/enter/escape`（`artemis/reflex/control.py:539`） |

## 4. 已定的做法（用户决定）

1. **步数只用单步**：按一次键（Tab 或朝目标方向的箭头），抓帧检查是否选中目标。
2. **选中即 Space**；未选中继续用方向键单步调整。
3. **优先级**：键盘方案与指针方案并存；键盘失败退回指针方案。

## 5. 设计

### 5.1 两套方案，一个契约

调用方仍然给"目标点（可选目标框）+ 期望效果"。`HidControlSink` 增加一个方案选择：

```python
async def tap_abs(self, x, y, *, scheme: Literal["pointer", "keyboard", "auto"] = "pointer")
```

- `pointer`：现有路径，不动。
- `keyboard`：只走焦点方案；失败返回 incident，不悄悄改走指针。
- `auto`：先探测（见 5.5），可用则最多尝试 `focus_max_steps` 步，失败再回退指针。

### 5.2 焦点高亮检测（新模块）

新增 `artemis/reflex/focus.py`，纯函数 + 一个轻量检测器：

```python
@dataclass(frozen=True)
class FocusBox:
    x1: int; y1: int; x2: int; y2: int
    confidence: float
    style: str  # "outline" | "changed-region"（后者是弱证据）

def locate_focus(before: bytes, after: bytes, *, frame_size, ignore_top=0.06) -> FocusBox | None
```

算法（样式无关，颜色只作提示）：

1. 两帧逐像素差，阈值 25；忽略顶部 6%（状态栏时钟）。
2. 连通域；对每个连通域求外轮廓，要求"细、闭合、中空"：填充率 < 0.55、最小边 ≥ 24 px、周长/面积
   符合描边特征；矩形成分高的优先。
3. 返回得分最高的 bbox，`style="outline"`；没有合格轮廓时，若最大连通域 ≥ 一定面积，返回其 bbox 但
   `style="changed-region"`（只用于继续搜索，**不允许**据此按 Space）。
4. 允许传入 `hint`（如白色/支付宝蓝）提高优先级，但不作为唯一判据。

`selected(focus_box, target_point, margin_px) -> bool`：目标点落在 bbox + margin 内；有目标框时要求框与
bbox 的重叠比 ≥ 阈值。

### 5.3 焦点移动控制器

新增 `FocusTapController`（与 `SessionTapController` 平级，复用同一个 capture/control）：

```
state: focus: FocusBox | None, previous_frame: bytes
loop up to focus_max_steps (默认 8):
    key = "tab" if focus is None else direction(focus.center -> target)
    press(key); frame = grab(after=press, fresh)
    box = locate_focus(previous_frame, frame)
    if box is None or box.style != "outline":
        连续无高亮计数 +1；>=2 则返回 focus_not_found（FKA 关/页面不可聚焦）
        continue
    focus = box
    if selected(focus, target):
        press("space")
        verify_effect(前帧, 后帧, target) -> ok / activate_no_effect
        return
返回 focus_not_on_target
```

方向选择：以焦点 bbox 中心到目标点的向量取主轴（|dx|≥|dy| 则左/右，否则上/下）。焦点跳转是空间邻接，
不是连续移动；闭环负责纠偏。

**Space 的确认条件**：`focus.style == "outline"` 且 `selected(...)` 为真。任何弱证据（`changed-region`）
都不允许触发 Space。

### 5.4 错误与回退

| incident | 触发 | 回退 |
|---|---|---|
| `focus_unavailable` | 探测阶段 Tab 两次无高亮 | `auto` 下直接走指针；`keyboard` 下报错 |
| `focus_not_found` | 循环中连续 2 次无高亮 | `auto` 回退指针 |
| `focus_not_on_target` | 达到步数上限仍未选中 | `auto` 回退指针 |
| `activate_no_effect` | Space 后目标区域无变化 | **不回退**（可能已生效，避免二次触发）；返回 incident |

回退到指针时，指针位置不受键盘操作影响（键盘只移动焦点），指针路径会自行 home/移动，安全。

### 5.5 能力探测

无 API 可查"全键盘控制"开关，用行为探测：连接后（或首次使用键盘方案前）按一次 Tab、抓帧；无变化则再
按一次并抓帧；两次都无变化 → `keyboard_unavailable`，本次会话缓存。探测只按 Tab（不激活任何东西），
在任意页面安全。也可用配置 `ARTEMIS_IOS_KEYBOARD_SCHEME=auto|on|off` 覆盖（`on` 跳过探测）。

### 5.6 集成点

- `artemis/reflex/focus.py`：`locate_focus` / `selected` / `FocusBox`。
- `artemis/reflex/focus_controller.py`（或并入 `artemis/control/hid.py`）：`FocusTapController`，持有
  `capture`、`control`（`press_named_key`）、`verify_effect`。
- `HidControlSink.tap_abs(..., scheme=...)`；`ActionResult.metrics` 增加
  `scheme`、`focus_steps`、`focus_style`、`activate_effect`、`fallback_reason`。
- `build_ios_uvc_session` 把 capture/control 也交给 `FocusTapController`（或由 sink 懒构造）。
- 动作层：`tap` 动作透传 `scheme`（默认 `pointer`，保持现有行为）。

### 5.7 指标与测试

- 单元：`locate_focus` 的合成图（深底白描边、浅底蓝描边、无描边、动画干扰）；`selected` 的边界；
  控制器用假 capture/control 走完"第 2 步选中→Space→生效""始终无高亮→focus_not_found""Space 无效"。
- 真机：先做一次性脚本量 1–2 个页面（设置页开关列表、支付宝搜索栏）：Tab 到目标 → Space → 效果，
  记录步数/耗时/命中率；据此决定要不要做 `scripts/ios_focus_bench.py`。

## 6. 留给构建者的判断（我的默认值，可改）

- 高亮检测的具体阈值与轮廓判据（用 spike 存下的帧做回归样本）。
- 方向键 vs Tab 的优先级：默认"无焦点先 Tab，有焦点按主轴方向；方向无变化时试 Tab"。
- `auto` 的默认策略：默认先探测，可用则尝试键盘（最多 8 步），失败回退指针；调用方可用 `scheme` 强制。
- 每页覆盖记录放哪：默认不建表，靠运行时闭环判定；如果实测发现某页频繁浪费步数，再加页面级开关。
- 控制器放 `artemis/reflex/` 还是并入 `hid.py`：默认新文件，便于单测。

## 7. 未决问题（需要你拍板）

1. `auto` 模式默认是否对**所有**目标先试键盘？在支付宝首页这类只有搜索栏可聚焦的页面，每个不可聚焦
   目标会先浪费约 2 s 才回退；默认值是"先试最多 2 步再回退"，还是"只有调用方显式标了 focusable 才试"？
2. 键盘方案的成功判据除了"目标区域变化"，是否需要确认"变化发生在焦点 bbox 内"（更严格，但搜索栏这类
   激活后整页跳转的场景可能不满足）？
3. 首屏焦点状态未知（可能是上次遗留的焦点）：是否允许在开始前按 Escape 重置（Escape 在 iOS 可能关闭弹层，
   有副作用），还是接受"从当前焦点开始、靠闭环纠偏"？
