# ESP32-S3 复合 HID 固件（Touchscreen + Mouse + Keyboard）

让 ESP32-S3 在手机（Android / iOS）上枚举成一个 **USB 复合 HID 设备**，同时暴露 3 个接口：

| 接口 | 用途 | 目标设备 |
|---|---|---|
| Touchscreen（digitizer） | 绝对坐标 `tap` / `long_press` / `swipe` / `drag` | Android（直接识别成触摸屏） |
| Mouse（相对指针） | `home` / `move` / `click` / `mdown` / `mup` | iOS（开「辅助触控」后当鼠标指针） |
| Keyboard | `type` / `key` / `keymod` / `kdown` / `kup` | iOS / Android |

手机各自只启用它认识的接口：Android 可以直接用绝对触摸；iOS 不识别外部 digitizer，只走键盘和鼠标（相对指针）。固件使用 **ESP-IDF + TinyUSB**（`espressif/esp_tinyusb` 组件）。

> **移植说明**：本目录是 2026-10-19 从 `mobile-agent-demo/esp32-hid/firmware` 移植进 `artemis` 仓库的第一方固件源码。只带源码、`CMakeLists.txt`、`sdkconfig.defaults` 和构建说明；`build/`、`components/`、`sdkconfig` 不进入仓库。原目录保持不动。

## 与本仓库的关系

这块固件是 UVC + ESP32 链路的设备侧，仓库里对应的上位机代码：

- `artemis/reflex/capture.py` — `UvcCapture`，以及 `default_capture(..., prefer='uvc')`；
- `artemis/reflex/control.py` — `Esp32HidControl`，通过 UART 发 `tap/swipe/move/click/key/type` 等命令；
- `artemis/tools/reflex_tool.py` — `play_reflex` 工具，把 capture/control 接进 reflex 运行时；
- 环境变量 `ARTEMIS_ESP32_PORT` 指向本固件的 UART 口（如 `/dev/cu.usbmodemXXXX`）；`play_reflex` 的 `capture` 参数当前文档写的是 `helper`/`screencap`，底层 `default_capture` 已支持 `uvc`。

串口参数：**115200 8N1**，一行一条命令，固件在 UART0 控制台回显 `[cmd] ...` 和诊断信息。

## 硬件与接线

适用板子：**ESP32-S3-DevKitC-1**（或其他有独立 USB-OTG 口的 ESP32-S3 板）。

```text
[USB-C#1 "UART/COM"]  ──> Mac（烧录 + 串口命令/日志）
[USB-C#2 "USB" ]      ──> 手机（发 HID，走原生 USB-OTG）
```

两个口是两套独立 USB 控制器，互不干扰。

- **USB-C iPhone**：直接用 **数据线** 接 ESP32 的 OTG 口；如果要同时接 UVC（HDMI 采集）和 ESP32，必须用 **带 USB 数据口的扩展坞**（HDMI + USB-A/USB-C 数据 + PD）。只供电或只转 HDMI 的扩展坞会让 ESP32 出现 `mounted=0`。
- **Lightning iPhone**：必须用 Apple **Lightning→USB 相机套件**（Camera Adapter，最好带供电）接 ESP32，普通 Lightning 数据线不能让 iPhone 当 USB 主机。
- iPhone 需要 **设置 → 辅助功能 → 触控 → 辅助触控** 打开；没有辅助触控时，相对鼠标没有指针，`move/click` 不会产生可见效果。
- 测试期间建议把 **自动锁定设为“永不”**，锁屏会让 USB 总线挂起（`suspended=1`）。

## 环境要求

- ESP-IDF v5.0+（本工程在 v6.1 上编译验证）
- 目标芯片 `esp32s3`
- 依赖 `espressif/esp_tinyusb`，由 `main/idf_component.yml` 在首次构建时自动下载（需要联网）

本机若使用自定义 ESP-IDF 安装，`env.sh` 会依次尝试：

1. `$IDF_PATH/export.sh`（已设置 `IDF_PATH` 时）
2. `~/.espressif/tools/activate_idf_v6.1.sh`（本机 v6.1 布局）
3. `~/esp/v6.1/esp-idf/export.sh`

你也可以直接 `source <你的 ESP-IDF>/export.sh`，不使用这个脚本。

## 构建与烧录

```bash
cd firmware/esp32-hid

# 1. 载入 ESP-IDF 环境
source ./env.sh

# 2. 首次构建前设定目标芯片
idf.py set-target esp32s3

# 3. 构建（首次会自动下载 esp_tinyusb 到 managed_components/）
idf.py build

# 4. 烧录 + 串口监视器；PORT 用 UART/COM 口
idf.py -p /dev/cu.usbmodemXXXX flash monitor
```

找不到串口时先 `ls /dev/cu.*`。DevKitC-1 通常有 `usbmodem`（UART/COM）和 `usbserial` 两类，烧录/监视器选 **UART/COM** 那个。退出监视器：`Ctrl-]`。

串口被占用时：

```bash
lsof /dev/cu.usbmodemXXXX
kill <占用进程 PID>
```

### 等效的直接烧录命令

```bash
python -m esptool --chip esp32s3 -b 460800 --before default-reset --after hard-reset \
  write-flash --flash-mode dio --flash-size 8MB --flash-freq 80m \
  0x0 build/bootloader/bootloader.bin \
  0x8000 build/partition_table/partition-table.bin \
  0x10000 build/hid_touchscreen.bin
```

### iOS-only 构建（keyboard + mouse，无 digitizer）

默认 `sdkconfig.defaults` 是 **Android/复合** 配置（touch + mouse + keyboard）。iOS 不支持外部 HID digitizer，可以构建一个只暴露 keyboard + mouse 的版本：

```bash
cd firmware/esp32-hid
source ./env.sh

# 首次：用独立的 build-ios 目录和 sdkconfig，避免覆盖默认构建
idf.py -B build-ios -D SDKCONFIG=build-ios/sdkconfig \
  -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.ios.defaults" \
  set-target esp32s3

idf.py -B build-ios -D SDKCONFIG=build-ios/sdkconfig \
  -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.ios.defaults" \
  build

idf.py -B build-ios -p /dev/cu.usbmodemXXXX flash monitor
```

`sdkconfig.ios.defaults` 设置：

```text
CONFIG_ARTEMIS_HID_INCLUDE_TOUCH=n   # 不暴露 digitizer 接口
CONFIG_TINYUSB_HID_COUNT=2           # 只分配 mouse + keyboard
```

这个版本的差异：

- USB 产品名为 `HID Keyboard Mouse`，默认复合版为 `HID Touch Mouse KB`；
- `usb` 输出为 `touch=0 mode=ios`（默认版是 `touch=1 mode=composite`）；
- `tap` / `long_press` / `swipe` / `drag` 仍能被串口解析，但会打印 `touchscreen disabled` 并直接返回，不会发 HID 报文；
- keyboard/mouse 的 `home` / `move` / `click` / `type` / `key` / `keymod` 不受影响。

> 不要复用默认构建生成的 `sdkconfig`：`sdkconfig` 一旦生成，改 `sdkconfig.defaults` 不会自动重新应用。上面的命令用独立的 `build-ios/sdkconfig` 就是为了避免这个问题。

## 命令

在串口监视器里直接输入（touch 坐标用“手机像素”，参考分辨率 1080×2400）：

```text
# 触摸屏（Android）
tap        540 1200
long_press 540 1200 800
swipe      500 1600 500 500 500
drag       500 1600 500 500 500

# 鼠标（iOS 辅助触控指针）
home               # 指针推到左上角（归零）
move 10 0          # 指针相对移动 dx dy
click              # 左键点击
mdown / mup        # 按下 / 松开左键

# 键盘
key 40             # 按下并释放一个键（HID usage，十进制；40=Enter）
keymod 8 11        # 按住修饰键再按键；8=Cmd，11=H，即 Cmd+H
kdown 40 / kup     # 按下 / 松开一个键
type hello         # 输入字母/数字/空格/回车

# 诊断
usb                # 打印 mounted/suspended/mouse/keyboard/touch
wake               # USB remote wakeup，然后打印同一组状态
help
```

| 动作 | 含义 | 参数 |
|---|---|---|
| `tap` | 触摸点击 | `x y` |
| `long_press` | 长按 | `x y duration_ms` |
| `swipe` | 滑动 | `x1 y1 x2 y2 duration_ms` |
| `drag` | 长按后拖拽 | `x1 y1 x2 y2 duration_ms` |
| `home` | 鼠标指针归零到左上角 | — |
| `move` | 鼠标相对移动 | `dx dy`（每轴 −127..127） |
| `click` | 鼠标左键点击 | — |
| `mdown` / `mup` | 鼠标按下 / 松开 | — |
| `key` | 按下并释放一个键 | `usage`（十进制） |
| `keymod` | 带修饰键的按键 | `modifier usage` |
| `kdown` / `kup` | 按下 / 松开一个键 | `usage` |
| `type` | 输入 ASCII 文本 | 字母/数字/空格/换行 |
| `usb` | 打印 USB HID 挂载/就绪状态 | — |
| `wake` | USB remote wakeup 并打印状态 | — |
| `help` | 打印命令表 | — |

常用键盘 usage：Enter=40，Esc=41，Tab=43，Space=44，←=80，→=79，↑=82，↓=81。修饰键：左 Ctrl=1，左 Shift=2，左 Alt=4，左 GUI/Cmd=8（`keymod` 的 modifier 参数）。

`type` 只支持 ASCII；中文 App 名建议在 iPhone Spotlight 里用拼音输入（例如 `zhongxinzhengquan`）再选候选。

## USB 状态含义

`usb` 的典型输出：

```text
# 默认 Android/复合版
usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=1 mode=composite

# iOS-only 版
usb mounted=1 suspended=0 mouse=1 keyboard=1 touch=0 mode=ios
```

| 字段 | 含义 | 排查 |
|---|---|---|
| `mounted` | Host 是否枚举并配置了板子 | `0`：查 OTG 线、Hub 数据口、相机套件、`Allow` 弹窗 |
| `suspended` | USB 总线是否被 Host 挂起 | `1`：解锁/点亮手机，或发 `wake`；锁屏后会挂起 |
| `mouse` / `keyboard` / `touch` | 对应 HID 接口是否 mounted 且 ready | 挂起时为 `0`；`suspended=0` 后应变 `1`；iOS-only 版 `touch` 恒为 `0` |
| `mode` | `composite`（含 digitizer）或 `ios`（keyboard+mouse） | 用 `sdkconfig.ios.defaults` 构建时为 `ios` |

iOS 上默认复合版的 `touch=1` 只表示 digitizer 接口挂载，**iOS 不消费它**；绝对坐标 `tap` 不会产生触摸。要避免这个无效接口，使用上面的 iOS-only 构建（`touch=0 mode=ios`）。Android 默认复合版的 touch 才是可用的绝对触摸路径。

## 验证（跑通标准）

### Android

1. 手机插上后弹出「已连接 USB 输入设备」。
2. 开画图 App 或「开发者选项 → 显示点按操作 / 指针位置」。
3. 发 `tap 540 1200`，屏幕中心出现点击；`swipe 500 1600 500 500 500` 出现滑动轨迹。

### iPhone（需辅助触控）

1. 用 USB-C 数据线直连，或经带数据口的扩展坞接 ESP32；Lightning 机型用相机套件。
2. 打开 **设置 → 辅助功能 → 触控 → 辅助触控**，确认屏幕上出现圆点指针。
3. 串口发 `usb`，确认 `mounted=1 suspended=0 mouse=1 keyboard=1`。
4. 发 `move 20 0` 看指针移动；`click` 看点击；`type hello` 看打字。
5. 没有可见指针时不要盲点：先确认辅助触控已开，或改用能看到指针的采集路径（AirPlay 等）。

## 坐标映射（仅触摸屏接口）

```text
hid_x = round(x / 1080 * 65535)
hid_y = round(y / 2400 * 65535)
```

Android 把 HID 逻辑范围 0..65535 映射到整个屏幕，所以只要参考分辨率长宽比（9:20）与手机一致即可，与手机实际分辨率无关。改 `main/hid_touch.h` 里的 `TOUCH_REF_WIDTH/HEIGHT`。

## 已知限制

- **iOS 不支持外部 HID digitizer**：只走键盘+鼠标；没有绝对坐标 `tap`。iOS-only 构建会直接移除该接口（`touch=0 mode=ios`）。
- **iOS 相对鼠标有加速度**：`home` + 固定 `move` 不能保证精确落点；生产路径需要指针检测/两点校准/aim 闭环。
- **UVC 不总是显示辅助触控指针**：辅助触控未生效时抓帧里看不到指针；开启后部分采集路径可见，仍建议先做 preflight。
- 触摸屏当前为**单点触控**（multi-touch 未实现）；iOS-only 构建不包含触摸接口。
- `move` 每轴范围 −127..127；超过会按 HID 报文字节截断，上位机应分段发送。
- 串口命令是手动测试通道，不是 Wi-Fi/云端 LLM 接入。
- Hub/线缆兼容性：直连可用但经 Hub `mounted=0` 时，优先换 **支持 USB 数据的 Hub 或数据线**，不要用只供电/只 HDMI 的转接。

## 目录结构

```text
firmware/esp32-hid/
├── CMakeLists.txt            # ESP-IDF 工程入口
├── env.sh                    # 可移植的 ESP-IDF 环境加载
├── sdkconfig.defaults        # 默认复合版：HID_COUNT=3、touch、UART console、8MB flash
├── sdkconfig.ios.defaults    # iOS-only：HID_COUNT=2、touch 关闭
├── .gitignore                # build*/、managed_components/、sdkconfig、sdkconfig.ios、components/
├── README.md
└── main/
    ├── CMakeLists.txt
    ├── Kconfig.projbuild     # CONFIG_ARTEMIS_HID_INCLUDE_TOUCH 编译开关
    ├── idf_component.yml     # espressif/esp_tinyusb 依赖
    ├── main.c                # 初始化 TinyUSB + 启动命令任务
    ├── hid_descriptor.c/.h   # HID report descriptor（touch 可选）+ 回调
    ├── hid_touch.c/.h        # tap / long_press / swipe / drag（iOS 构建为 no-op）
    ├── hid_mouse_kb.c/.h     # 鼠标 + 键盘
    └── cmd.c/.h              # 串口命令解析
```

## 许可证

本目录的第一方代码跟随 `artemis` 仓库的 Apache-2.0 许可证。`espressif/esp_tinyusb` 与 TinyUSB 为第三方依赖，由 ESP-IDF Component Manager 拉取，分别遵循其上游许可证；不要把它们的源码复制进本目录。
