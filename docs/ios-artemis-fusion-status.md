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

### 2.3 控制与安全

- `HidControlSink`：标准异步 ControlSink，保留 `SessionTapController` 预检
- `PointBelief`（`PointerSession`）：home 原点、相对位移、clamp
- `RedPointerDetector`：红环 HSV
- `RingShapeDetector`：Hough 圆环兜底
- 移动后立即抓帧，无额外 sleep
- 点击前检测指针，≤8px 才点击
- 点击后 diff 验证

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

### 2.5 验证

当前相关单测：

```bash
pytest tests/unit/devices tests/unit/perception tests/unit/control \
       tests/unit/drivers/test_ios_uvc_hid_driver.py -q
# 41 passed
```

已提交：

```text
18ef62a feat: expose structured iOS tap result
a830843 feat: validate iOS pointer against target
9e01cb9 feat: inject PaddleOCR into iOS driver
05302cb feat: register iOS UVC HID driver
f7e54b9 feat: add shape-only pointer fallback and immediate capture
```

## 3. 未完成

### 3.1 Validator 尚未消费结构化结果

问题：

```text
IOSUvcHidDriver.tap_with_result()
  → ActionResult(incident=...)
  → action executor 目前只拿 bool
  → Validator 看不到 pointer_missing / pointer_off_target
```

需要：

1. action executor 调用 `tap_with_result`；
2. 写入 `action_item["action_result"]` / `action_item["incident"]`；
3. Validator 后置读取并分类；
4. 覆盖 `pointer_missing` / `pointer_off_target` / `no_effect` 测试。

### 3.2 iOS runtime context/config 未验证

`create_driver()` 已有 iOS 分支，但还缺少：

- `ctx.device.mobile_platform == "ios"` 的真实设备描述；
- `ARTEMIS_ESP32_PORT`、`ARTEMIS_UVC_DEVICE`、crop 配置；
- iOS 任务启动入口；
- `flash` / `pro` profile 在 iOS 下的实际运行证据。

### 3.3 真机 flash/pro 未闭环

目前真机验证的是：

- UVC 抓帧；
- PaddleOCR 定位“闪购”；
- pointer precheck；
- HID 点击“闪购”；
- 点击后 diff。

但还没有：

```text
artemis run --profile flash
artemis run --profile pro
```

在 iOS 真机上的完整执行记录。

### 3.4 淘宝下单任务未完成

- 未完成规格选择、领券、结算、提交订单；
- 未付款；
- 搜索词曾停留在“太犇牛手作茶铺”，多次未改回“珍珠奶茶”；
- 商品卡误点、滚动误触 Home 等问题已记录到 `skills/ios-taobao/README.md`。

## 4. 关键阻塞

| 阻塞 | 影响 | 处理 |
|---|---|---|
| Validator 未消费 `tap_with_result` | iOS pointer incident 不进安全网 | action executor + Validator 改造 |
| iOS device context 不存在 | `create_driver` 可能走 Android | 增加 iOS device/config |
| profile 未真机验证 | 不能声称 flash/pro 可用 | 最小 smoke + 固定用例 |
| 支付需生物识别 | 无法自动完成最终付款 | 到支付页停止，用户确认 |
| 淘宝自绘控件/手势不稳定 | 任务易中断 | OCR bbox + 键盘滚动 + 点击后验证 |

## 5. 后续计划

### P5：Validator 结构化 incident

- [ ] `_exec_action` 使用 `tap_with_result`（如果 driver 支持）
- [ ] 写入 `action_item["action_result"]`
- [ ] Validator 后置读取 incident
- [ ] incident 分类：
  - `pointer_missing`
  - `pointer_off_target`
  - `pointer_not_centered`
  - `no_effect`
- [ ] fake driver 集成测试
- [ ] 不改变 Android 行为

验收：

```text
pointer_missing 时 tap 不执行；
Validator 能记录 incident；
Android 测试全绿。
```

### P6：iOS runtime config

- [ ] iOS device descriptor：
  - `mobile_platform=ios`
  - `device_width/height`
- [ ] env：
  - `ARTEMIS_ESP32_PORT`
  - `ARTEMIS_UVC_DEVICE`
  - `ARTEMIS_UVC_CROP`
- [ ] `create_driver(ctx)` 返回 `IOSUvcHidDriver`
- [ ] 启动日志打印 driver/capabilities
- [ ] 失败时明确报“iOS 配置缺失”，不落到 Android

验收：

```text
create_driver(ctx).device_id == "ios-uvc-hid"
capabilities.supports_ui_tree == False
capabilities.requires_pointer_precheck == True
```

### P7：flash 真机 smoke

- [ ] `flash` profile 创建 iOS driver；
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
| 无指针 | `clicks == 0` |
| 点击后 | diff 验证；无变化返回 `no_effect` |
| flash | 真机完成一次安全任务 |
| pro | 真机完成同一任务，incident 可追溯 |
| 测试 | 相关单测全绿，Android 回归不破 |
| 支付 | 只到确认页，用户完成生物识别 |

## 7. 当前结论

- **能力组件、driver、pointer precheck 已完成**；
- **Validator/runtime/profile 还未闭环**；
- 现在可以离线验证 `UVC → OCR → Target → HID`；
- 还不能声称“iOS 真机 flash/pro 已可运行”。

下一步优先级：

```text
P5 Validator incident
→ P6 iOS runtime config
→ P7 flash smoke
→ P8 pro smoke
→ P9 淘宝业务
```
