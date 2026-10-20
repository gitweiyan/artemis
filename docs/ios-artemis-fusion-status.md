# iOS × Artemis 融合：现状与后续计划

Status: In progress  
Date: 2026-10-20  
Scope: iOS UVC 感知 + ESP32 HID 控制接入 Artemis，最终支持 `flash` / `pro` 真机运行

## 1. 目标

让 iOS 成为 Artemis 的标准能力组合，而不是 `scripts/ios_*.py` 旁路：

```text
PerceptionSource = UvcSource
ControlSink      = HidControlSink
Driver           = IOSUvcHidDriver
Profiles         = flash / pro
Safety           = pointer precheck + pixel/diff
```

最终验收：

- `create_driver(ctx)` 在 iOS 配置下返回 `IOSUvcHidDriver`；
- `flash` 和 `pro` 都能在 iOS 真机执行任务；
- 点击前指针与目标一致；
- 点击后验证画面变化；
- 只在支付/Face ID 步骤停止。

## 2. 已完成

### 2.1 架构与契约

- [目标架构](artemis-target-architecture.md)
- [iOS UVC 执行计划](ios-uvc-execution-plan.md)
- `CapabilitySet`
- `Frame` / `Target` / `TextBox` / `UIElement`
- `PerceptionSource`
- `ActionResult` / `ControlSink`
- `ScreenGeometry`
- `DeviceSession`

### 2.2 感知

- `UvcSource`：按需单帧、健康检查（ok/black/decode_error）
- `PaddleOcrEngine`：懒加载 PaddleOCR PP-OCRv6，解析文字/分数/多边形
- `locate_text`：精确/包含匹配、置信度门槛
- `UvcSource.ocr/locate`：OCR 注入
- `ScreenGeometry.normalized_box_to_screen/center`
- `ScreenGeometry.screen_to_normalized/screen_to_frame`：屏幕像素 ↔ 采集帧像素

### 2.3 控制与安全

- `HidControlSink`：标准异步 ControlSink，保留 `SessionTapController` 预检
- `PointBelief`（`PointerSession`）：home 原点、相对位移、clamp
- `RedPointerDetector`：红环 HSV
- `RingShapeDetector`：Hough 圆环兜底
- 移动后立即抓帧，无额外 sleep
- 点击前检测指针，≤8px 才点击
- 点击后 diff 验证
- `HidControlSink(geometry=...)`：屏幕像素目标先换算成采集帧像素，再进入指针闭环
- `TapResult.incident`：`pointer_missing` / `pointer_not_centered` / `no_effect`

### 2.4 iOS 组合与运行时

- `build_ios_uvc_session`
- `IOSUvcHidDriver`
  - `get_screen_data()` → UVC
  - `tap()` / `tap_with_result()`
  - `input_text` / `press_key` / `swipe`
  - 能力声明：relative pointer / pointer precheck / no UI tree / no absolute tap
  - `validate_pointer(target)`：
    - `ok`
    - `pointer_missing`
    - `pointer_off_target`
    - `pointer_not_centered`
- driver factory：`mobile_platform == "ios"` → `IOSUvcHidDriver`
- factory 注入 `PaddleOcrEngine`
- `tap_with_result` 暴露结构化 `ActionResult`
- `DevicePlatform.IOS`；`Agent.init()` 不再把显式 iOS 平台改写为 Android
- `artemis/drivers/ios/config.py`：`IosRuntimeConfig` / `IosConfigError` / `ios_device_context`
- `artemis run --platform ios`（`ARTEMIS_DEVICE_PLATFORM` 也可），iOS 不做 adb 预检、
  不查 ADB 设备池、不进 daemon 队列
- `artemis ios-smoke [--target 闪购] [--no-tap] [--json]`：不开 agent、不走 LLM，
  按 snapshot → health → OCR → pointer precheck → HID tap → diff 跑一遍机台
- driver 复用 session 的 pointer detector（一台机只有一个检测配置）

### 2.5 验证

当前相关测试：

```bash
pytest tests/unit/devices tests/unit/perception tests/unit/control \
       tests/unit/drivers tests/unit/reflex tests/unit/mcp -q
# 719 passed, 1 skipped

pytest tests/integration/ios/test_uvc_flow.py -m integration -q
# 5 passed（MCP → actuator → controller → driver → HID sink 全链路，假 UVC/假板子）

pytest tests/unit/drivers/test_ios_smoke.py -q
# 7 passed（机台冒烟的分支：全绿 / 找不到文字 / 指针不可见 / 黑帧 / JSON / 跳过点击 / 缺配置）
```

P5/P6 验证覆盖：

- 无指针 → `clicks == 0`，incident `pointer_missing`；
- 指针收敛但画面不变 → `no_effect`；
- pointer incident 不触发 Validator 重试（一次点击，不会变成多次抓帧）；
- 屏幕像素目标（1080x2400）在 449x974 采集帧内收敛到 ≤8px 后点击；
- Android 结果不带 `incident` 时 action item 与旧行为完全一致；
- iOS 缺 `ARTEMIS_ESP32_PORT` 直接报配置错误，不落 Android。

已提交（P5/P6）：

```text
feat: report a tap incident code from the pointer controller
feat: forward pointer incidents through the HID sink
feat: expose structured tap incidents from the controller
feat: carry tap incidents in the MCP action result
feat: record iOS tap incidents on the action item
feat: open pointer incidents from the Validator execution loop
feat: explain pointer incidents to the Operator
feat: add validated iOS runtime config and build the driver from it
feat: support an iOS device platform in the runtime
feat: add --platform ios to artemis run
feat: run ios tasks in-process instead of through the adb daemon scheduler
fix: convert screen-space taps into capture-frame pixels before the pointer check
```

更早：

```text
18ef62a feat: expose structured iOS tap result
a830843 feat: validate iOS pointer against target
9e01cb9 feat: inject PaddleOCR into iOS driver
05302cb feat: register iOS UVC HID driver
f7e54b9 feat: add shape-only pointer fallback and immediate capture
```

## 3. 未完成

### 3.1 真机 flash/pro 未闭环（P7/P8）

iOS driver、runtime config、incident 链路都已离线验证通过，机台冒烟也有独立入口，
但还没有在真机上跑过：

```text
# 1. 先确认机台本身
ARTEMIS_ESP32_PORT=/dev/tty.usbmodemXXXX \
ARTEMIS_UVC_DEVICE="USB Video" \
artemis ios-smoke --target 闪购

# 2. 再跑编排
ARTEMIS_ESP32_PORT=/dev/tty.usbmodemXXXX \
artemis run --platform ios --profile flash "打开设置"
ARTEMIS_ESP32_PORT=/dev/tty.usbmodemXXXX \
artemis run --platform ios --profile pro "打开设置"
```

需要在真机上确认：`get_screen_data` 成功、OCR 定位、pointer precheck、点击后 diff、
trace 里有 observation/decision/control/timestamps、incident 可在日志里追溯。

### 3.2 真机参数标定

`IosRuntimeConfig` 的默认值来自 2026-10-19/20 的实测机台（crop `736,53,1185,1027`、
frame 449x974、screen 1080x2400、gain 1.8）。换手机/采集卡/线材后需要重新标定 crop 与 gain。

### 3.3 淘宝下单任务未完成（P9）

- 未完成规格选择、领券、结算、提交订单；
- 未付款；
- 搜索词曾停留在“太犇牛手作茶铺”，多次未改回“珍珠奶茶”；
- 商品卡误点、滚动误触 Home 等问题已记录到 `skills/ios-taobao/README.md`。

## 4. 关键阻塞

| 阻塞 | 影响 | 处理 |
|---|---|---|
| profile 未真机验证 | 不能声称 flash/pro 可用 | P7/P8 固定用例真机跑一遍 |
| 支付需生物识别 | 无法自动完成最终付款 | 到支付页停止，用户确认 |
| 淘宝自绘控件/手势不稳定 | 任务易中断 | OCR bbox + 键盘滚动 + 点击后验证 |
| 指针/采集卡依赖单一机台 | 换设备需重新标定 | crop/gain 走 env，改机台先跑冒烟 |

## 5. 后续计划

### P5：Validator 结构化 incident

- [x] driver 结构化结果沿 `tap_at → AdbActuator → MCP ActionResult.incident` 上报
      （Ruling：不在 `_exec_action` 里直接调 `tap_with_result`，保持 MCP 单入口）
- [x] 写入 `action_item["action_result"]`（仅当 driver 报 incident）
- [x] Validator 读取 incident 并开对应 category 的 execution incident
- [x] incident 分类：
  - `pointer_missing`
  - `pointer_off_target`
  - `pointer_not_centered`
  - `no_effect`
- [x] fake driver 集成测试（`tests/integration/ios/test_uvc_flow.py`）
- [x] 不改变 Android 行为

验收：

```text
pointer_missing 时 tap 不执行；
Validator 能记录 incident；
Android 测试全绿。
```

### P6：iOS runtime config

- [x] iOS device descriptor：
  - `mobile_platform=ios`
  - `device_width/height`（`ARTEMIS_IOS_SCREEN_SIZE`，默认 1080x2400）
- [x] env：
  - `ARTEMIS_ESP32_PORT`（必填）
  - `ARTEMIS_UVC_DEVICE`（默认 `USB Video`）
  - `ARTEMIS_UVC_CROP`（默认 `736,53,1185,1027`）
  - `ARTEMIS_IOS_SCREEN_SIZE` / `ARTEMIS_IOS_POINTER_GAIN` / `ARTEMIS_IOS_OCR`
- [x] `create_driver(ctx)` 返回 `IOSUvcHidDriver`
- [x] 启动日志打印 driver/capabilities
- [x] 失败时明确报 `IosConfigError`（点名 `ARTEMIS_ESP32_PORT`），不落到 Android
- [x] `artemis run --platform ios`；adb 预检 / 设备池 / daemon 队列都不参与

验收：

```text
create_driver(ctx).device_id == "ios-uvc-hid"
capabilities.supports_ui_tree == False
capabilities.requires_pointer_precheck == True
```

### P7：flash 真机 smoke

- [x] driver 选择/能力声明可离线验证（factory 单测）：`flash` 走同一 `create_driver`
- [x] `artemis ios-smoke` 覆盖同一链路（UVC → OCR → precheck → HID → diff），无 LLM
- [ ] `flash` profile 真机创建 iOS driver；
- [ ] `get_screen_data` 成功；
- [ ] OCR 定位“设置”/“闪购”；
- [ ] pointer precheck；
- [ ] tap 并验证 diff；
- [ ] 记录 trace（观察/决策/控制/时间戳）。

验收：

```text
flash 能在 iOS 真机完成一次安全点击；
无 UI tree 时自动走 pixel/pointer 路径。
```

### P8：pro 真机 smoke

- [ ] `pro` profile 使用同一 iOS driver；
- [ ] Validator 读取 pointer/no_effect incident；
- [ ] 失败重试/重锚；
- [ ] 记录每次 incident；
- [ ] 对比 flash/pro 的步骤和延迟。

验收：

```text
pro 能在 iOS 真机完成同样任务；
pointer/incident 可在日志中追溯；
不盲点、不跳过支付确认。
```

### P9：淘宝业务闭环（可选）

- [ ] 搜索“珍珠奶茶”；
- [ ] 筛选总价 ≤10（含配送费/起送价）；
- [ ] 选规格；
- [ ] 领可用券；
- [ ] 加购/结算；
- [ ] 到支付确认页停止。

## 6. 验收标准

| 项 | 标准 |
|---|---|
| iOS driver 选择 | `mobile_platform=ios` 时必为 IOSUvcHidDriver |
| UI tree | 不支持时不阻塞，走 pixel/pointer |
| 点击前 | 指针检测到且在目标 8px 内 |
| 无指针 | `clicks == 0`，incident `pointer_missing` |
| incident 进入安全网 | Validator category = `pointer_*` / `no_effect` |
| 屏幕/帧坐标 | 目标在采集帧内收敛 ≤8px 才点击 |
| 点击后 | diff 验证；无变化返回 `no_effect` |
| flash | 真机完成一次安全任务 |
| pro | 真机完成同一任务，incident 可追溯 |
| 测试 | 相关单测全绿，Android 回归不破 |
| 支付 | 只到确认页，用户完成生物识别 |

## 7. 当前结论

- **P5（Validator 结构化 incident）已完成**：iOS 的 `pointer_missing` /
  `pointer_off_target` / `pointer_not_centered` / `no_effect` 会进入 Validator、
  incident 记录、trace 与 Operator prompt；
- **P6（iOS runtime config）已完成**：`artemis run --platform ios` 能选中
  `IOSUvcHidDriver`，缺配置时明确报错；
- 全链路（MCP → actuator → controller → driver → HID sink）有假 UVC/假板子的集成测试；
- 过程中修掉一个真实坐标 bug：`tap_abs` 用屏幕像素、指针闭环用采集帧像素，之前会
  拒绝或点偏；
- **仍未完成**：`flash` / `pro` 真机运行记录（P7/P8）与淘宝业务闭环（P9）。

下一步优先级：

```text
P7 flash 真机 smoke
→ P8 pro 真机 smoke
→ P9 淘宝业务
```
