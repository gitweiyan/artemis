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

### 2.5.1 采集异常与帧龄（P10，2026-10-21）

“画面不可用”以前只有一句 `capture failed`，现在有三个具名 incident，走同一条
`ActionResult.incident → Validator category → Operator 提示` 通道：

| 代码 | 含义 | 触发点 |
|---|---|---|
| `uvc_no_signal` | 帧到了但没画面（HDMI/HDCP/转接、手机息屏） | `UvcSource` health=`black`/`decode_error` |
| `capture_timeout` | 预算内没有任何帧（进程卡住、设备掉了、别的进程占卡） | 抓帧异常且信息含 timeout/no frame |
| `stale_frame` | 验证点击用的帧已超过新鲜度预算 | `SessionTapController.max_frame_age_s`（默认 3s） |

落点与约束：

- `artemis/perception/incidents.py`：`UVC_NO_SIGNAL / CAPTURE_TIMEOUT / UVC_DECODE_ERROR`、
  `incident_for_health()`、`incident_for_error()`、`UnhealthyFrame(incident, detail)`；
- `IOSUvcHidDriver.snapshot_frame()`：走 `DeviceSession.snapshot()`（多源+健康门控），
  黑帧/超时抛 `UnhealthyFrame`；`get_screen_data()` 与 `tap_with_result()` 都用它，
  **黑帧不再被当作“成功截图”**，抓帧失败也不再以异常穿过控制器；
- `ValidationErrorCategory` 增加 `UVC_NO_SIGNAL / CAPTURE_TIMEOUT / STALE_FRAME`，
  Operator prompt 对三类各给处置建议（查 HDMI/转接/息屏、查卡住的抓帧进程、重新观察）；
- 指针闭环拒绝用过期帧验证点击（无时间戳的帧不受限，测试替身不受影响）。

### 2.5.2 指针观测与锚点（P10 批次 2，2026-10-21）

| 改动 | 原因 | 落点 |
|---|---|---|
| 软件 `home`：按 ACK 节奏分段走回左上角 | 固件 `home` 2ms 连发 50 报文，USB 10ms 轮询会丢（知识库 2026-10-19 记录） | `Esp32HidControl.mouse_home()`（`firmware_home()` 仅留作诊断） |
| nudge/diff 指针兜底 | 指针是灰色、且可能被帧边缘切掉，颜色/形状模型抓不住 | `SessionTapController(tracker=PointerTracker(...))`，`build_ios_uvc_session` 默认开启；`menu_box` 可配置 |

`PointerTracker.locate()` 报的是**第一帧**里的位置，所以兜底路径会把 nudge 的位移加回去，
再交给 belief 校正（否则每次兜底都会少算一段位移）。

### 2.5.3 reflex skill 接入（P11，2026-10-21，离线）

iOS 没有 digitizer，skill 的绝对手势以前在 iOS 上等于空操作。现在：

- `artemis/reflex/pointer_control.py::PointerControl`：实现 reflex 的 `Control` 协议
  - `tap` → 设备像素→采集帧像素 → 走 `SessionTapController.tap`（移动+预检+点击+验证），
    被拒绝时抛 `RuntimeError`（runtime 的连续失败计数会接管）；
  - `swipe` → 先 `travel` 到起点（偏差 >24px 拒绝按下）→ `mdown` → int8 分段移动 → `mup`（`finally` 保证释放）；
- `IOSUvcHidDriver.pointer_control()` 把适配器绑到自己的 controller+geometry；
- `play_reflex` 按能力选择：`capabilities.supports_absolute_tap is False` 时用指针控制，
  Android 保持原路径；指针路径同样先 `preflight` 板子。

Tetris 等 skill 因此可以在 iOS 上跑（仍需真机确认帧率与手势成功率）。

### 2.5.4 架构落位与遥测（2026-10-21，离线）

| 项 | 现状 |
|---|---|
| `artemis/safety/pointer.py` | 「指针可见 + 在目标框内 + 距中心 ≤ 容差」的唯一定义；driver 的 `validate_pointer` 与点击控制器共用，incident 名不再各写一份 |
| `artemis/skills/manifest.py` | `skill.json` 的 schema/校验唯一来源；reflex registry 只负责导入入口。顺手修掉一个真漏洞：声明 `screenshot:true` 会短路 `api_key_env` 检查，导致缺 key 的技能先加载、后失败 |
| 动作遥测 | `ActionResult.metrics`：指针坐标/置信度、`error_px`、`frame_age_s`、`attempts`、目标在两个坐标系的点；一路经 `TapOutput → MCP ActionResult → action_item["action_metrics"] → incident evidence`，trace 可直接回答“当时指针在哪、差多少、帧多旧” |

仍未落位（B 组剩余）：`control/app_control.py`（Android 绝对控制做成 ControlSink）、
`control/composite.py`（绝对优先/HID 兜底）、`perception/app_automation.py`（Android UI 树源）、
`perception/camera.py` + `composite.py`（黑屏降级）、`orchestration/{profiles,selector}.py`
（能力驱动组合，目前硬编码在 factory）、`safety/{pixel,xml}.py`（逻辑仍在 validator 里）。

### 2.6 真机 flash / pro（P7 / P8，2026-10-21）

真实机台（iPhone + `USB Video` 采集卡 + ESP32-S3，`ARTEMIS_ESP32_PORT=/dev/cu.usbmodem5B140947461`）：

| 运行 | 结果 | 产物 |
|---|---|---|
| `artemis ios-smoke --target 通讯录` | 通过（切到微信通讯录标签） | — |
| `artemis ios-smoke --target 设置` | 通过（点击后进入设置，OCR 复核） | — |
| `artemis run --platform ios --profile flash "打开微信"` | **PASS** | `traces/ios-flash-wechat2_PASS_2026-10-20T12-37-18` |
| `artemis run --platform ios --profile pro "打开微信"` | **PASS**（final check 2/2） | `traces/ios-pro-wechat_PASS_2026-10-20T12-38-11` |

编排确实在 iOS 上跑通了：flash 6 轮、pro 5 步（Operator→Validator→Checker→Outputter），
点击前指针校验、点击后画面变化、trace/GIF 全部产出。

真机这一轮又修掉 4 个只有上机才暴露的问题：

1. **观察把采集帧尺寸写进了 `ctx.device`**：`get_screen_data()` 返回 449×974（裁剪帧）而不是
   屏幕 1080×2400，之后所有归一化坐标按 2.4 倍缩小 —— `点击微信` 落到 `日历`；
2. **`move` 超过 int8 回绕**：固件把每个轴强转 `int8_t`，`move 81 200` 是向后跳；主机必须分段；
3. **常驻 AVFoundation 会话读出冻结帧**：同一张卡上 one-shot 抓帧是活的、常驻流永远返回同一帧
   → 默认回到 `ARTEMIS_IOS_CAPTURE=frame`；
4. **`press_key back` 在 iOS 无效**：键名原样发给固件 → `usage: key <usage 0-255>`；
   现在键名映射 HID usage、Home 变 Cmd+H、BACK 明确拒绝并提示点屏幕返回箭头。

另外新增两个机台自检/可观测性能力：

- `ios-smoke` 增加 `pointer_motion` 阶段：HID 有 ACK 但画面不变时直接报
  “iOS 没在画指针”，并给出重新插拔 OTG / 重选辅助触控设备的处置建议；
- `ARTEMIS_FLASH_MAX_TURNS` 可给 flash 轮数设上限（首次上机不必改配置文件）。

## 3. 未完成

### 3.0 真机验证记录（2026-10-21，iPhone + USB Video 采集卡 + ESP32）

机台：`ARTEMIS_ESP32_PORT=/dev/cu.usbmodem5B140947461`，采集卡 `USB Video`，
crop `736,53,1185,1027` → frame 449x974，screen 1080x2400，gain 1.8。

已通过（见 2.6 的运行表）：

| 阶段 | 实测 |
|---|---|
| connect（`home` 锚定指针） | 306–338 ms |
| snapshot（UVC 单帧） | 首帧 0.6–1.1 s（含 ffmpeg 启动），warm ≈ 50 ms |
| locate（macOS Vision OCR） | 0.26–2.1 s（首帧冷启动 1.4 s，warm 0.3 s）；`设置` conf 1.00/0.30 |
| pointer precheck | 指针可见即通过（离目标 451 px 属正常，由 tap 驱动） |
| tap | 驱动指针到 screen (911, 739) → 偏差 ≤8 px → click → **画面变化验证通过** |

真机暴露并修掉的问题（都有单测锁定）：

1. `UvcStream.grab_latest()` 在持续 30 fps 流上永不返回（一直"drain 到没有数据"）→ 加 drain 预算；
2. 单帧 ffmpeg ≈0.87 s，指针环已淡出 → iOS session 默认改用常驻流
   （`ARTEMIS_IOS_CAPTURE=frame` 可退回）；
3. 指针淡出后 15 单位移动唤不醒，60 单位可唤醒 → 控制器在首次观测为空时先唤醒再判定；
4. 控制器在"移动之前"的旧帧上重新检测指针 → 自我追赶、永不收敛（`pointer_not_centered`）→
   移动后先等一帧反映该移动（有界），再检测；
5. `locate_text` 用 0.5 门槛丢掉了 Vision 的 `设置`（conf 0.30，完全正确）→
   整串精确匹配门槛降到 0.25，部分匹配仍要求 0.5；
6. `os.write` 短写丢掉换行 → 固件后续命令回 `ERR line too long`（真机上出现过一次）→
   `write_line` 循环写满；
7. PaddleOCR 本机未安装 → 新增 macOS Vision OCR 引擎与能力感知选择器
   （无引擎时 `supports_ocr=False`，不抛异常）。

已知环境约束（非代码问题）：

- 手机锁屏会让 iOS 挂起 HID：`usb mounted=1 suspended=1 mouse=0 keyboard=0`，
  UVC 同时变黑帧；`wake` 命令唤不醒，必须手动解锁。跑任务前把
  **设置 → 显示与亮度 → 自动锁定 = 永不**；
- `tests/unit/mcp/test_mcp_stdio_lifecycle.py::test_mcp_stdio_handshake_immediate_input`
  在本机失败（MCP server 启动约 8.3 s > 测试的 6 s 预算），在 `9e3cee6` 基线上同样失败，
  与 iOS 改动无关。

### 3.1 真机 flash/pro 记录（P7/P8，已完成）

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
| 指针增益未校准 | 大距离移动靠闭环修正，慢且可能过冲 | 两点校准 → `ARTEMIS_IOS_POINTER_GAIN`；离线可先做校准入口 |
| 无 Camera 兜底 | 采集卡黑屏/HDCP 时任务直接停 | `perception/camera.py` + `composite.py` |
| skill 在 iOS 只能绝对 tap | Tetris 等 reflex skill 在 iOS 不可用 | 新增 `PointerControl`（绝对 tap → 相对移动+预检+点击） |
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
- [x] `flash` profile 真机创建 iOS driver；
- [x] `get_screen_data` 成功；
- [x] OCR 定位“设置”/“闪购”；
- [x] pointer precheck；
- [x] tap 并验证 diff；
- [x] 记录 trace（观察/决策/控制/时间戳）。

验收：

```text
flash 能在 iOS 真机完成一次安全点击；   ← 2026-10-21 达成（打开微信 PASS）
无 UI tree 时自动走 pixel/pointer 路径。 ← 达成
```

### P8：pro 真机 smoke

- [x] `pro` profile 使用同一 iOS driver；
- [x] Validator 读取 pointer/no_effect incident；
- [x] 失败重试/重锚；
- [x] 记录每次 incident；
- [x] 对比 flash/pro 的步骤和延迟（flash 6 轮 / pro 5 步，trace 均归档）。

验收：

```text
pro 能在 iOS 真机完成同样任务；          ← 2026-10-21 达成（打开微信 PASS）
pointer/incident 可在日志中追溯；          ← 达成（trace + incident evidence）
不盲点、不跳过支付确认。                   ← 无指针时 clicks == 0
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
