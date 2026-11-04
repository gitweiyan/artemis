# iOS 机台操作手册（canonical）

> **谁、什么时候读**：任何要动 iOS 机台的人或 agent——UVC 看屏、ESP32 HID 指针、点击、校准、
> 排障——**动手前先读这里，再跑 `artemis ios-smoke`**。
>
> 这是唯一事实源。其他位置（`superpowers/.../knowledge/ios-hid-pointer.md`、
> `skills/ios-taobao/README.md`）只保留指针与业务专属规则，不再复述本文件内容。

## 0. 30 秒门禁（必须按顺序，禁止跳步）

```text
1) 板子/系统就绪      → artemis ios-smoke 的 connect + snapshot（或手工 usb 状态）
2) 指针真的动          → smoke 的 pointer_motion 阶段（HID 有 ACK 但画面不变 = 没在画指针）
3) 目标看得见          → smoke 的 locate（注意 source=icon-above-label 才是可点的图标）
4) 一次带验证的点击     → smoke 的 tap（指针 ≤8px + 点击后目标 crop 变化）
```

四条全绿才允许跑业务任务（`artemis run --platform ios ...`）。任何一条红，先按 §5 排障，
**不要**用业务任务去“试试看”。

## 1. 触发条件索引（症状 → 根因 → 现在由谁保证）

| 症状 | 根因 | 已自动化的落点（先看这里，再读下文） |
|---|---|---|
| 指针在画面上不动 | iOS 隐藏指针；小位移唤不醒 | `SessionTapController` 首次观测为空时用 60 单位唤醒；smoke `pointer_motion` |
| 指针位置“看着对”，点击没反应 | **点的是标签不是图标**（桌面文字在图标下方，只有图标可点） | `perception/ios_targets.py::icon_above_label` + `UvcSource.locate`；smoke 报告的 `source=` |
| 闭环总差一点、点在目标左侧 | 指针有**加速度**（实测小位移 1.48、大位移 1.78 px/单位），固定 gain 会短走 | `SessionTapController` 在线估计 gain（EMA，钳制 0.3–4.0）；起点值用 `artemis ios-calibrate` 实测 |
| 大位移后指针反向跳 | 固件把 `move` 每轴强转 `int8`，>127 回绕 | `Esp32HidControl.mouse_move` 分段（≤127）+ `write_line` 写满 |
| 滑动/拖拽在 iOS 无效 | 固件的 `swipe` 是**触摸屏**手势，iOS 不消费；iOS 的滑动＝鼠标拖拽 | 固件 `mswipe <dx> <dy> [ms]`（按下→分段插值→松开）；主机 `swipe_mouse()` 在旧固件上用 `mdown`+分段 `move`+`mup` 复现，`dispatch()` 按 `mode=ios`/`touch=0` 路由 |
| 滑动没反应 / 弹出控制中心 / 直接回桌面 | 指针没先移到安全起点：停在顶部向上拖没有行程；从顶部向下拖是控制中心/通知中心，从底部向上拖是 Home | `HidControlSink.swipe` 先试方向键（画面没动才降级）；拖拽前软件 home，再按方向放到 0.65h / 0.35h / 0.5h 的安全锚点（§2.5） |
| `home` 之后位置不可预测 | 固件 `home` 2ms 连发 50 报文，被 USB 10ms 轮询丢弃 | `mouse_home()` 走**分段软件 home**；`firmware_home()` 只作诊断 |
| 抓帧总是同一张 | 常驻 AVFoundation 会话在本采集卡上给冻结帧；或残留 ffmpeg 占卡 | 默认 `ARTEMIS_IOS_CAPTURE=frame`（单帧）；残留时 `pkill -9 -f "USB Video"` |
| 黑帧/超时/帧太旧 | 采集卡无信号、设备没了、抓帧进程卡死 | `uvc_no_signal` / `capture_timeout` / `stale_frame` incident（见 §4） |
| 点击无效，但指针在动、键盘也失灵 | **前台有 Spotlight/搜索层**（`Cmd+Space` 误触），它吞掉点击和 `Cmd+H` | 先按 **Esc** 再看屏幕（§5 第 1 步） |
| 重启后“按键全废” | 疑似 iOS 丢了指针设备的按键映射 | 先排除 Spotlight（Esc）；仍不行才插拔 ESP32 OTG 线 |
| `press_key back` 报 usage 错误 | 键名原样下发，固件只认数字 usage | `press_named_key`（Home→Cmd+H；BACK 明确拒绝并提示点返回箭头） |
| 串口里 `key 8 11` 没反应 | `key` 只取**一个** usage（8='e'），多给的数字被忽略；Cmd+H 是 `keymod 8 11` | 固件现在对多余参数直接报错并提示 `keymod <mod> <usage>`；用法见 §2.5 表 |
| 串口里同一条命令时有 `ERR line too long` | 收到 ≥127 字节仍没有行终止符：终端只发 **CR**、粘贴过快丢了换行、或单行超 126 字节 | 固件现在认 LF/CRLF/**裸 CR** 并在超长时立即重同步（只丢一条命令）；CoolTerm 里把 Enter 设为 LF、一次一条、等 `OK` |
| 中文小字定位不到 | 裁剪帧太小、OCR 引擎置信度口径不同 | `locate_text` 整串精确匹配门槛 0.25、部分匹配 0.5；`ARTEMIS_IOS_OCR` 可关 |
| 图标明明在，却不肯抬到图标上 | 抬升判据曾用“整帧边缘密度”，纹理壁纸/相邻图标都会否掉 | `icon_above_label` 改判**较安静的那一侧邻居** + 内部细节（`_MIN_INNER_DENSITY`） |

## 2. 输入模型（为什么 iOS 与 Android 不是同一件事）

- iOS **不消费外部 HID digitizer**：绝对坐标 `tap` 不产生触摸。只有键盘 + 鼠标（相对指针）。
- 鼠标指针是 AssistiveTouch 的 overlay：**开辅助触控**且屏幕常亮才看得到；会淡出。
- 指针有加速度、`move` 是 int8、`home` 是不可靠的连发——这三点决定了“必须闭环 + 分段 + 软件 home”。
- 键盘：`Enter/Space/Tab/方向键` 可用；没有 Back 键（用屏幕返回箭头或 Cmd+H）。
- 应用启动只能点图标（没有包名可查）；`manage_app` 在本平台直接失败并提示点图标。

## 2.5 键盘优先（先走这条路，再考虑指针）

指针有加速度、会淡出、要预检；键盘没有这些问题。**能用键盘就别用鼠标。**

| 目的 | 命令（`press_named_key` / 固件） | 备注 |
|---|---|---|
| 回主屏 | `home` → `keymod 8 11`（Cmd+H） | 主屏/App 内都有效 |
| 搜索/打开 App | `spotlight` → `keymod 8 44`（Cmd+Space），`type_text("taobao")`，`down`，`enter` | **已验证**：输入英文/拼音即可命中中文 App（`taobao` → 淘宝） |
| 关闭弹层/搜索页 | `escape` → `key 41` | “点击无效”的第一反应也是它 |
| 切 App | `app_switch` → `keymod 8 43`（Cmd+Tab） | |
| 关闭当前弹窗/页面 | `close` → `keymod 8 26`（Cmd+W） | 不适用于所有 App |
| 光标/翻页 | `down/up/left/right` → `key 81/82/80/79` | Spotlight 选中首条结果要 `down` 再 `enter` |
| 滑动/拖拽（iOS） | `HidControlSink.swipe` 先试方向键；没滚动才走 `mswipe <dx> <dy> [ms]`（固件）或 `mdown` → 分段 `move` → `mup`（主机） | 触摸屏的 `swipe` 在 iOS 构建里被拒绝；方向键路径见下 |

拖动（swipe）在 iOS 上**优先走键盘**（`HidControlSink.swipe`，`artemis/control/hid.py`）：上滑＝
按 `down`、下滑＝按 `up`（约每 200px 一次，最多 6 次），然后抓帧比对——画面确实滚动了（变化面积
>5%，排除只是焦点框）就算成功。**画面没动或按键报错才降级到鼠标拖拽**。拖拽前先软件 home（真实
原点），再放固定锚点——向上拖 0.65h（避开底部 Home 带、留出向上行程），向下拖 0.35h（避开顶部
控制中心/通知中心），水平拖 0.5h；x 用模型给的起点。锚点离两侧边缘都远，配置 gain 有 ±20% 误差
也不会落进手势带。代价：拖拽兜底**不再按模型给的精确起点起手**，滑杆/子容器内拖动需要精确起点，
得改成相机闭环（`verified_travel`，未实现）。

`type_text` **只支持 ASCII**（固件限制）：中文要输入时用拼音（淘宝 → `taobao`）、或用 App 内搜索框的中文候选。

## 3. 点击落点（最贵的一课：2026-10-21）

1. **桌面文字不是可点区域**。OCR 命中的是图标下方的标签；点击标签在 iOS 上完全不生效。
   目标必须抬到标签上方的“忙碌块”（Canny 边缘密度判据 → 灰色图标也认，壁纸不算）。
   实现：`artemis/perception/ios_targets.py`，`UvcSource.locate` 默认应用，
   smoke 的 `locate` 报告里会标 `source=icon-above-label`。
2. **先闭环再点**：`移动 → 抓帧 → 检测 → 误差 ≤8px → 点击 → 验证目标 crop 变化`；
   任何一步不满足都**不点**（`pointer_missing` / `pointer_off_target` / `pointer_not_centered` / `no_effect`）。
3. **gain 靠测量不靠配置**：配置里的 `ARTEMIS_IOS_POINTER_GAIN` 只是起点，控制器在线估计。
4. 点击后验证优先看**目标 crop**（按压高亮/弹层就在那里），整帧变化作为兜底；
   状态栏时钟变化不算。

### 3.1 指针运动模型（2026-11-03，V1+V2）

- **分阶段**：按剩余距离相对“目标有效宽”（`MotionConfig.target_width_px=96`）的倍数选阶段——
  `>4W` ballistic、`≥1.5W` approach、其余 fine；每段走剩余距离的 80% / 70% / 60%，每段之后
  抓帧、观测、重规划（`max_segments=12`），不再用固定的 100 单位步长。
- **分阶段增益**：每个阶段一份 px/HID 单位，从观测在线学习（EMA 0.4，钳制 0.3–4.0，越界比值视为
  误检丢弃）；`ARTEMIS_IOS_POINTER_GAIN` / `ios-calibrate` 只提供起点。规划与信仰积分共用同一个估计。
- **走廊观察**：从“上次观测点 → 本次预测点”建 ROI（半径 `max(24, 2.5R+3σ)`，R=20、σ 为预测误差
  EMA），先裁图检测；ROI 没找到才全屏重捕获（计入 `motion_global_recoveries`），所以走廊不会把
  可见指针判成缺失。
- **指标**：step 的 `action_metrics` 里看 `motion_segments` / `motion_observations` /
  `motion_phases` / `motion_prediction_error_px` / `motion_global_recoveries`。
- **基准**：`scripts/ios_move_bench.py --target X,Y --runs 10`（只移动、不点击；命中率 <90% 或
  中位观察数 >4 时退出码 1，同时打印每阶段学到的增益）。

### 3.2 绝对坐标指针（2026-11-04）

- 固件在 boot mouse 接口上追加了一个绝对 pointer collection（Report ID 2，X/Y 0..32767）；串口命令
  `amove <x> <y>`（归一化 0..1000）与 `aclick`；原有 `move/click/home` 与键盘命令不变。
- 实测（iPhone 14 Pro Max，iOS 18）：随机 20 点中 18 点可检出，误差中位 **1.6 px**、最大 **3.1 px**；
  `aclick` 在绝对位置点击生效；相对鼠标回归正常。
- 上位机：`SessionTapController` 默认先走一步绝对定位（`PointerSession.move_abs`），第一次观测兼作能力
  探测（误差 > `max(3×tolerance, 24)` 则本会话标记不支持并改用相对分段）；点击在绝对路径用 `aclick`，
  回退路径用 `click`。绝对路径不需要增益标定，单次点击约 1 s（含一次抓帧验证）。
- 固件的 `hid_mouse_abs_move` 对"报告未就绪"重发最多 5 次（每次 5 ms），避免单条报文丢失导致指针停在
  上一个点。

## 4. 采集与健康

- 默认 `ARTEMIS_IOS_CAPTURE=frame`：每次抓帧一个 ffmpeg（~0.9s，帧一定是活的）。
  `stream` 只在确认采集卡支持常驻会话时用（本卡实测会给冻结帧）。
- 抓帧失败分类：`uvc_no_signal`（帧到了但全黑：HDMI/HDCP/转接/息屏）、
  `capture_timeout`（预算内没帧）、`stale_frame`（验证帧超龄）。三者走同一条
  `ActionResult.incident → Validator category → Operator 提示` 通道。
- 残留进程会让采集卡“活着但冻结”：`pkill -9 -f "USB Video"` 后重试。

## 5. 排障顺序（照这个顺序，别跳）

```text
1) 按 Esc            # 关掉 Spotlight/弹层（最常见的“点击无效”假象）
2) 按 Cmd+H          # 回主屏（键盘能用，说明 HID 键盘通道 OK）
3) usb 状态          # mounted=1 suspended=0 mouse=1 keyboard=1
4) 抓一帧 OCR        # 确认现在到底在哪一屏
5) artemis ios-smoke # 四段门禁，看是哪一段红
6) 仍红 → 插拔 ESP32 的 OTG 线（不是 Mac 那根 UART），再跑 5)
```

经验：**不要**用“连续盲点”去验证点击是否恢复；用 smoke 的 `tap` 阶段（带指针校验与 diff）。

## 6. 校准（已落地）

```bash
ARTEMIS_ESP32_PORT=/dev/cu.usbmodemXXXX artemis ios-calibrate
# gain: suggested=1.48,1.49 (small 1.48, large 1.78, acceleration×1.20)
# env: ARTEMIS_IOS_POINTER_GAIN=1.48,1.49
```

- 做法：软件 home → 已知相对 move（25 / 75 单位各 2 次）→ 抓帧检测 → 按位移量分两档求
  每轴比值中位数（丢弃 ±0.2–5.0 之外的误检）。
- **验收数字**：`suggested` 与 `small` 相差 ≤0.1；`acceleration×` < 1.5 说明这台机加速度温和，
  ≥1.5 会在 notes 里提示“必须闭环”。
- 本机实测（2026-10-21）：small 1.48 / large 1.78 / acceleration ×1.20 →
  `ARTEMIS_IOS_POINTER_GAIN=1.48,1.49`（已成为代码默认值）。
- 换手机 / 换采集卡 / 改指针速度后**必须重做**；`ARTEMIS_IOS_POINTER_GAIN` 只是起点，
  控制器仍会在线估计。

## 7. 沉淀规则（防止知识再次散落/失效）

**一事实一文件**：本手册是 iOS 机台事实的唯一来源；其他文档只允许指针 + 业务专属规则。

**每条教训必须有一个落地形态**，按强度排序：

| 强度 | 形态 | 例子 |
|---|---|---|
| 1 | 代码/测试 | 标签→图标、自适应 gain、int8 分段 |
| 2 | 检查阶段 | `ios-smoke` 的 `pointer_motion` / `tap` |
| 3 | 断言/证据字段 | `no_effect` 明细里的“插拔”提示、`action_metrics` |
| 4 | 散文（`prose-only`，记为欠账） | 暂无（校准已由 `artemis ios-calibrate` 落地） |

复盘产出的新教训：先在 §1 表里加一行（症状/根因），**同一 PR 内**尽量把它变成 1–3 形态；
做不到就把 `Enforced by` 写成 `prose-only（原因）`，下次动这块代码时优先还债。

**触发索引优先于主题目录**：写新知识时先问“什么症状会让人需要它”，把这一行加进 §1；
按主题建目录会因为没有人按主题浏览而失效。

## 8. 速查

```bash
# 机台自检（四段门禁）
ARTEMIS_ESP32_PORT=/dev/cu.usbmodemXXXX artemis ios-smoke --target 设置
# 业务任务（iOS 强制 standalone，不进 ADB daemon 队列）
ARTEMIS_ESP32_PORT=/dev/cu.usbmodemXXXX artemis run --platform ios --profile flash "打开微信"
# 清残卡进程
pkill -9 -f "USB Video"
```

| 环境变量 | 作用 |
|---|---|
| `ARTEMIS_ESP32_PORT` | ESP32 板的 UART 串口（必填） |
| `ARTEMIS_UVC_DEVICE` / `ARTEMIS_UVC_CROP` | 采集卡名 / 手机在卡中的裁剪框 |
| `ARTEMIS_IOS_SCREEN_SIZE` | 手机屏幕像素（默认 1080x2400） |
| `ARTEMIS_IOS_POINTER_GAIN` | 指针 gain 起点（控制器会在线修正） |
| `ARTEMIS_IOS_CAPTURE` | `frame`（默认）/ `stream` |
| `ARTEMIS_IOS_OCR` | `1`/`0`，关闭 OCR（无文本目标时） |
