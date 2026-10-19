# iOS UVC 执行计划

Status: Proposed  
Parent: [Artemis 目标架构 v1](artemis-target-architecture.md)  
Scope: iOS + UVC 感知 + ESP32 HID 控制，接入 Artemis 统一编排

## 1. 目标

把 iOS UVC 从 `scripts/ios_*.py` 的旁路流程，改造成 Artemis 的标准能力组合：

```text
PerceptionSource = UvcSource
ControlSink      = HidControlSink
CapabilitySet:
  ui_tree = False
  absolute_tap = False
  relative_pointer = True
  requires_pointer_precheck = True
```

验收目标：

- `flash` / `pro` / `skill` 都能复用同一套 iOS 能力；
- Planner / Explorer / Validator 不再感知“这是 iOS”；
- 点击前必须确认目标存在且指针一致；
- 点击后必须验证画面变化；
- UVC 只做按需单帧，不做 30fps 常驻流；
- 失败有 incident，不静默成功。

## 2. 现状基线

已具备：

- `UvcCapture`：按需单帧，`pixel_format=nv12`，约 **850ms/帧**；
- `Esp32HidControl`：串口命令 + `OK` ack；
- `PointerSession`：`home` 原点、相对 move、gain、clamp；
- `RedPointerDetector`：红环 HSV + 尺寸/面积/中心镂空过滤；
- `SessionTapController`：点击前检测指针、≤8px 才点击、点击后 diff 验证；
- `PointerSession` 单测与 reflex 测试（416 passed）；
- 真机验证：88VIP X 闭环点击成功；广告 X 坐标可定位；指针移动/键盘可用。

已知缺口：

- 没有 `IOSUvcHidDriver`，上述能力未进入 Artemis 工具链；
- `BaseDeviceDriver` 未拆分 perception/control；
- OCR `OCR_API_KEY` 当前 403；
- `object_detector` 配置为 `deepseek-flash`，与“精确 bbox 需要 Gemini ER”的注释冲突；
- UVC 可能黑帧/HDCP；指针可能淡出；
- 淘宝部分自绘 tab 忽略 HID click；
- 当前 iOS 脚本绕过 flash/pro 和 Validator。

## 3. 非目标

- 不实现 iOS 越狱/私有触控注入；
- 不把 Android UI XML 模拟到 iOS；
- 不让 DeepSeek 直接回归精确 bbox；
- 不做 30fps UVC 常驻 + 拆帧；
- 不为 iOS 复制 Planner/Validator；
- 不在无信号、低置信度、指针不一致时点击。

## 4. 目标接口

### 4.1 Perception

```python
class UvcSource(PerceptionSource):
    async def snapshot(self) -> Frame: ...
    async def ui_tree(self) -> None: return None
    async def ocr(self, frame: Frame) -> list[TextBox] | None: ...
    async def locate(self, frame: Frame, target: Target) -> Target | None: ...
```

能力：

```python
supports_ui_tree = False
supports_ocr = depends_on_key
supports_locate = depends_on_provider
is_external = True
```

### 4.2 Control

```python
class HidControlSink(ControlSink):
    async def tap_abs(self, x: int, y: int) -> ActionResult:
        # 转成 PointerSession.tap(x, y)
        # 内部强制 pointer precheck
        ...
    async def tap_rel(self, dx: int, dy: int) -> ActionResult: ...
    async def swipe(self, ...) -> ActionResult: ...
    async def input_text(self, text: str) -> ActionResult: ...
    async def press_key(self, key: Key) -> ActionResult: ...
```

能力：

```python
supports_absolute_tap = False  # HID 本身是相对；tap_abs 只是上层目标语义
supports_relative_pointer = True
requires_pointer_precheck = True
```

### 4.3 Session

```python
IOSUvcSession(
    perception=UvcSource(...),
    control=HidControlSink(...),
    geometry=ScreenGeometry(...),
    capabilities=...,
)
```

## 5. 分阶段执行

### Phase 0：契约与兼容层（P0）

目标：不改行为，先把接缝建好。

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-01 | 定义 `PerceptionSource` / `ControlSink` / `CapabilitySet` | `artemis/perception/base.py`, `artemis/control/base.py` |
| IOS-UVC-02 | 定义 `DeviceSession` / `ScreenGeometry` | `artemis/devices/session.py`, `geometry.py` |
| IOS-UVC-03 | 给 `BaseDeviceDriver` 加 capability 属性 | 兼容 façade |
| IOS-UVC-04 | 为 UVC/HID 写 contract tests | 单测 |

验收：

- 旧 Android 驱动测试全绿；
- 新接口不依赖平台名；
- 旧 `BaseDeviceDriver` 行为不变。

### Phase 1：UVC PerceptionSource（P1）

目标：把 `UvcCapture` 变成标准感知源。

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-11 | 单帧抓取封装 `snapshot()` | `perception/uvc.py` |
| IOS-UVC-12 | 帧健康检查：黑帧/全黑/尺寸异常 | `Frame.health` |
| IOS-UVC-13 | `ScreenGeometry`：crop、letterbox、归一化 | 几何单测 |
| IOS-UVC-14 | 明确 `pixel_format=nv12` 与超时 | 硬件脚本 |
| IOS-UVC-15 | 失败分类：`uvc_no_signal` / `uvc_blocked` / `capture_timeout` | incident |

验收：

- 449×974 crop 正确；
- 黑帧返回 `uvc_no_signal`，不返回“成功截图”；
- 单帧 p95 < 1s；
- 不产生常驻 ffmpeg。

### Phase 2：HID ControlSink + Pointer（P2）

目标：把相对 HID 控制收进 control 层，点击前预检。

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-21 | `HidControlSink` 包装 `Esp32HidControl` | `control/hid.py` |
| IOS-UVC-22 | `PointerBelief`：位置、置信度、可见性、last_seen | `control/hid.py` |
| IOS-UVC-23 | `home` 原点 + gain 校准 | session 单测 |
| IOS-UVC-24 | 点击前 precheck：目标容差内才点 | `safety/pointer.py` |
| IOS-UVC-25 | 点击后 diff 验证 / no_effect | incident |
| IOS-UVC-26 | 指针隐藏时：move 后立即抓帧，不额外 jiggle | 时序测试 |

验收：

- 检测不到指针 → `clicks == 0`；
- 指针偏差 > 8px → 先修正，不点击；
- 点击后无变化 → `no_effect`；
- 指针隐藏时不会误点。

### Phase 3：目标感知（P3）

目标：中文小字不再依赖 VLM 目测。

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-31 | OCR source 接口；处理 403/超时 | `perception/ocr.py` |
| IOS-UVC-32 | ER/object detector 接口；配置校验 | `perception/locate.py` |
| IOS-UVC-33 | 目标 crop/zoom + 归一化框 | `Target` |
| IOS-UVC-34 | 多候选 + confidence + source | Explorer 候选 |
| IOS-UVC-35 | DeepSeek 只做候选选择/语义判断 | `DecisionProvider` |
| IOS-UVC-36 | 目标 crop 存档，供 pixel safety net | telemetry |

验收：

- “闪购”这类小字：OCR/ER 至少一个来源能给出框；
- 低置信度不点击；
- 目标框中心误差 ≤ 8px（测试集）；
- 不再用整图目测坐标。

### Phase 4：SafetyNet 与 Validator 接入（P4）

目标：iOS 动作走 Artemis 统一安全网。

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-41 | `capabilities.ui_tree=False` → XML bypass | Validator |
| IOS-UVC-42 | pixel safety：决策时 crop vs 执行时 crop | `safety/pixel.py` |
| IOS-UVC-43 | pointer safety：预检 | `safety/pointer.py` |
| IOS-UVC-44 | incident taxonomy：shifted/covered/disappeared/pointer_missing/no_effect | `safety/incidents.py` |
| IOS-UVC-45 | 重试策略：一次修正，仍失败上报 | Validator |

验收：

- iOS tap 不再绕过 Validator；
- XML 不可用自动走 pixel；
- 失败有分类，不静默成功。

### Phase 5：Profile / Skill / Telemetry（P5）

任务：

| ID | 任务 | 产出 |
|---|---|---|
| IOS-UVC-51 | `flash` profile 接 UVC 单帧 + precheck | orchestration |
| IOS-UVC-52 | `pro` profile 接 OCR/ER + pixel safety | orchestration |
| IOS-UVC-53 | Tetris skill：UVC/Camera + HID + CV 闭环 | `skills/tetris/` |
| IOS-UVC-54 | 遥测：帧/目标/指针/动作/incident/latency | telemetry |
| IOS-UVC-55 | 回放：失败步骤可复现 | replay |

验收：

- flash 简单任务可跑；
- pro 复杂任务有 safety/retry；
- Tetris 不走逐帧 LLM；
- 每次动作有完整证据链。

### Phase 6：真机验收（P6）

固定用例：

1. 主屏 → 设置（pointer precheck）；
2. 88VIP 弹窗 X（正确坐标 + 点击后关闭）；
3. 广告 X（区分关闭/入口）；
4. 淘宝搜索框 → 拼音 → 珍珠奶茶；
5. 闪购 tab（OCR/ER 定位）；
6. 商品卡 → 规格 → 购物车；
7. 指针隐藏/黑帧/HDCP 异常；
8. flash/pro 两种 profile 各跑一遍。

验收指标：

| 指标 | 目标 |
|---|---|
| 单帧 p95 | < 1s |
| 黑帧识别 | 100% |
| 可见指针召回 | > 99% |
| 指针误检率 | < 1% |
| 目标中心误差 | ≤ 8px |
| 无检测点击 | 0 |
| 点击成功率 | > 95% |
| 失败 incident 覆盖率 | 100% |

## 6. 代码落位

```text
artemis/perception/
  base.py
  uvc.py
  ocr.py
  locate.py
artemis/control/
  base.py
  hid.py
  pointer_belief.py
artemis/safety/
  pointer.py
  pixel.py
  incidents.py
artemis/devices/
  session.py
  geometry.py
artemis/drivers/ios/
  ios_uvc_hid_driver.py
artemis/skills/
  tetris/
tests/
  unit/perception/test_uvc_source.py
  unit/control/test_hid_sink.py
  unit/safety/test_pointer_precheck.py
  integration/ios/test_uvc_flow.py
```

现有代码吸收：

- `UvcCapture` → `perception/uvc.py`
- `Esp32HidControl` → `control/hid.py`
- `PointerSession` → `control/pointer_belief.py`
- `SessionTapController` → `safety/pointer.py`
- `RedPointerDetector` → `perception/uvc.py` 或 `safety/pointer.py`
- `ios_pointer_servo.py` / `ios_mouse_probe.py` → 诊断脚本，不进入任务流程

## 7. 测试策略

### 单元测试

- 帧健康：黑帧/正常/尺寸异常；
- 几何：crop、letterbox、0–1000 换算；
- pointer：检测不到不点、偏差修正、≤8px 才点；
- safety：shifted/covered/disappeared/no_effect；
- profile：flash/pro 选择能力组合。

### 集成测试

Fake UVC + Fake HID：

```text
snapshot → target → move → snapshot → pointer ≤8px → click → diff
```

断言：

- 无检测时 click 次数为 0；
- 失败返回 incident；
- 不产生常驻进程。

### 真机测试

- 固定用例矩阵（Phase 6）；
- 记录帧、目标 crop、指针、动作、incident、延迟；
- 每次失败必须能回放。

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| UVC 黑帧/HDCP | 健康检查 + Camera/WDA 降级 |
| 指针淡出 | move 后立即抓帧；异常重锚 |
| gain 非线性 | 分段校准 + 闭环修正 |
| 中文 OCR 弱 | OCR/ER 候选 + crop/zoom + confidence |
| OCR key 403 | 可选 source；不阻塞主流程 |
| App 忽略 HID click | 点击后验证；失败上报；换路径 |
| 模型坐标弱 | 专用 ER/OCR 出框；VLM 只选择 |
| iOS 无 UI 树 | XML bypass → pixel safety |
| 脚本旁路复发 | 所有任务只走 `IOSUvcSession` |

## 9. 发布与回滚

发布：

- 先 shadow：iOS session 与旧脚本并行，只记录不执行；
- 再灰度：flash 简单任务；
- 再 pro：复杂任务；
- 最后移除旁路脚本入口。

回滚：

- 保留 `BaseDeviceDriver` 兼容 façade；
- 关闭 iOS UVC source 即回退旧路径；
- 不删除诊断脚本。

## 10. 开放问题

1. iOS 是否有可用的 WDA/XCUITest 签名方案？若有，是否作为首选绝对控制？
2. OCR 服务是否修复？还是改用 ER/本地 OCR？
3. `object_detector` 是否改回 Gemini ER？当前 `deepseek-flash` 与注释冲突。
4. UVC 采集卡是否支持更稳定的 1080p/nv12？是否需要 Camera 兜底？
5. 淘宝自绘 tab 忽略 HID click，是否接受“失败上报 + 用户手动”？
6. 指针颜色/大小是否固定为高对比红环？需要配置化。

## 11. 里程碑

| 里程碑 | 内容 |
|---|---|
| M0 | 契约/兼容层完成，旧测试全绿 |
| M1 | UVC source + geometry + 黑帧检测 |
| M2 | HID sink + pointer precheck |
| M3 | OCR/ER 目标候选 + confidence |
| M4 | Validator/SafetyNet 接入 |
| M5 | flash/pro + Tetris skill + telemetry |
| M6 | 真机验收通过 |

每个里程碑必须满足：单测通过、无旁路新增、失败可解释、可回滚。
