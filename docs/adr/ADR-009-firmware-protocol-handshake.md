# ADR-009: ESP32 Host–Firmware Protocol, Version Negotiation and Capability Handshake

- **Status:** Accepted
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 4.2（ESP32 HID Adapter + Firmware Bridge）；Phase 4.2 固件协议版本/能力握手
- **Related:** 契约 §6.3（ESP32/TinyUSB 边界）、§5.3（Capability）、§5.7（ACK 语义）；[ADR-001](ADR-001-capability-model-evidence-fingerprint.md)（Accepted）、[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted）、[ADR-011](ADR-011-host-resource-lease.md)（Accepted）
- **Audit evidence:** `firmware/esp32-hid/README.md:24-26,184-191`、`firmware/esp32-hid/main/cmd.c:78-120,282-330`、`firmware/esp32-hid/sdkconfig.ios.defaults:15`、`artemis/reflex/control.py:283-460,517-680`、`artemis/drivers/ios/config.py:38-44`

> 状态说明：本 ADR 于 2026-11-10 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7。

---

## 1. Context and problem

### 1.1 固件现在有三类能力不同的 Build Variant

- 默认复合构建：Touchscreen + Mouse + Keyboard（`sdkconfig.defaults`）；
- iOS-only 构建：`CONFIG_ARTEMIS_HID_INCLUDE_TOUCH=n`（`sdkconfig.ios.defaults:15`），只暴露 Mouse + Keyboard；
- BCI-only 实验构建：`CONFIG_ARTEMIS_HID_BCI_ONLY=y`（`sdkconfig.bci.defaults:1-9`）。

Build 目录/配置是独立的事实（`README.md:103-129` 明确要求用 `build-ios/` 自己的 `sdkconfig`）；Host 目前**无法从设备得知**当前跑的是哪个 Variant，只能从行为/Descriptor 猜。

### 1.2 协议是可靠的，但没有版本、ID 或能力清单

`README.md:24-26,184-191` 与 `cmd.c` 固化的事实：

- UART **115200 8N1**，一行一条命令；LF/CRLF/裸 CR 都接受；
- 每条命令执行完回一行 `OK`，错误回 `ERR ...`；Host 必须**一次只发一条**、收到 ACK 再发；
- 单行（含换行）**≤126 字节**（`cmd.c:282`，超长回 `ERR line too long`）；
- `usb` 返回 `mounted/suspended/mouse/keyboard/touch/mode`，iOS 版 `touch=0 mode=ios`；
- 命令包括 `tap/swipe/move/home/click/mdown/mup/mswipe/amove/aclick/key/keymod/kdown/kup/type/usb/wake/bci...`。

缺失：

- 没有 `PROTOCOL_VERSION`、`firmware_build`、board identity；
- 没有命令能力清单（`amove`/`aclick` 是否存在只能靠 ERR 探测）；
- ACK 没有命令 ID，Host 只能依赖“单飞行 + 超时”，一旦乱序/粘包只能 resync；
- 没有 Build Variant 的机器可读标识。

### 1.3 Host 端把协议事实散落在控制类里

`artemis/reflex/control.py::Esp32HidControl`：

- 硬编码波特率 `DEFAULT_ESP32_BAUDRATE=115200`（`:125`）、`ack_timeout_s=3.0`（`:288`）；
- `preflight()` 要求 `mounted=1/suspended=0/mouse=1/keyboard=1`（`:456-495`）；
- `_exchange()` 解析 `OK`/`ERR` 并 resync（`:398-448`）；
- `type_text` 分片（`:517`）、`mouse_move_abs/click_abs`（`:661-680`）、`mouse_move` 分段 `ESP32_MAX_MOVE=127`（`:146,646-660`）；
- 没有版本/能力对象；ADR-001 的 fingerprint 里 `firmware_build`/`protocol_version` 只能是 `unknown`。

### 1.4 为什么现在必须决策

Phase 4.2 要把 `Esp32HidControl` 抽成 L4 Firmware Bridge；如果不先定握手/版本/ACK 语义，抽离后仍会有“Host 猜固件能力”的隐式耦合，ADR-001 的 `verified` 也无法按固件 Build 失效。

---

## 2. Decision drivers

1. **ACK 不等于 UI**：协议层确认、HID Report 提交、目标系统接受、界面变化必须分开。
2. **旧固件可用**：现有 iOS 机台固件不能因为新 Host 直接失效；必须向后兼容。
3. **能力真实**：Variant/命令能力由设备声明 + 实测证据决定，不能由源码/Descriptor 推断。
4. **可诊断**：版本协商失败、未知命令、超时、连发保护都有明确错误。
5. **可测试**：Host 用假串口即可覆盖握手/超时/分片/释放；固件协议改动有 Host-Device 兼容测试。
6. **最小协议演进**：不重写成二进制协议；先加行级握手/可选 ID。

---

## 3. Considered options

### Option A — 保持现状（无版本，靠 `usb` + ERR）

- Benefits：零固件改动。
- Costs / risks：能力靠猜；固件升级/降级无法判断；`amove` 缺失只能在点击时失败；fingerprint 无法区分 Build。

### Option B — 升级为二进制/Protobuf 协议

- Benefits：强类型、ID、分片。
- Costs / risks：现有固件/工具/诊断全部重写；串口调试困难；超出 Phase 4.2。

### Option C — 行级协议 v2：`hello`/`info` 握手 + 可选命令 ID + 能力清单（推荐）

- Benefits：向后兼容 v1；Host 能在连接时知道版本/Variant/能力；ACK 可匹配；仍可用串口手工调试。
- Costs / risks：需要固件与 Host 同时演进；老固件回落 v1 的限制要写清楚。

### Option D — 只加 `version` 行，不加能力

- Benefits：改动小。
- Costs / risks：仍无法知道 `amove/aclick` 是否存在，Phase 4.2 的 Adapter 仍要试错。

**选择：Option C。**

---

## 4. Decision (accepted)

### D1 — 版本与握手

协议版本为整数 `protocol`；Host 支持 `[MIN_PROTOCOL, MAX_PROTOCOL]`（初始 `[1,2]`）。

握手命令：

```text
hello
OK hello proto=2 fw=<build> board=<id> mode=<ios|composite|bci> max_line=126 \
   features=usb,mouse,keyboard,touch,abs_pointer,abs_click,bci caps=...
```

- `features` 是机器可读的短名集合；`caps` 可选，用 ADR-001 的规范 ID 列表。
- v1 固件没有 `hello`：Host 收到 `ERR unknown command` 或超时后回落 v1，`protocol=1`，`features` 只能从 `usb` 的 `mouse/keyboard/touch/mode` 推断；`abs_pointer/abs_click` 标记 `unknown`。
- 握手失败不阻塞基本输入（v1 路径），但依赖新能力的 Action 必须 `capability_unavailable`。

### D2 — 版本兼容矩阵

| 设备协议 | Host 行为 |
|---|---|
| `1` | 当前行命令、单飞行 ACK；`amove/aclick` 仅在用户显式已知时尝试，失败报 `command_unsupported` |
| `2` | 使用 `hello` 的 features/caps；可选命令 ID；按能力启用绝对指针/BCI |
| `> MAX_PROTOCOL` | 进入只读/安全模式：只允许 `usb`/`hello`/`home` 诊断，不发送危险输入（电源/点击/长按） |
| malformed/未知 | 视同 v1，记录 `firmware_error`，不猜测新命令 |

- **不得**因为固件源码存在某个 Descriptor 就向 Host 宣布该能力；能力以握手 + Smoke 证据为准。

### D3 — 能力握手与证据

- `hello`/`usb` 给出的是 `declared`/`available`；`verified` 必须由 `ios-smoke`/Contract Test 产生（ADR-001/008）。
- 映射示例：`features=mouse` → `pointer.relative`；`abs_pointer` → `pointer.absolute`；`keyboard` → `keyboard.key`/`text.input`；`usb` → `usb.hid`；`mode` 进入 fingerprint。
- Fingerprint 至少包含 `firmware_build`、`protocol`、`mode`、`board`；换 Build/Variant 后旧 `verified` 不得继承。

### D4 — 命令 ID 与 ACK（v2）

- v2 命令可带可选前缀 `id=<n> `；ACK 为 `OK id=<n> ...` 或 `ERR id=<n> ...`。
- Host 单飞行策略保留；收到不匹配 ID 时丢弃并继续等待/超时 `firmware_error`。
- v1 没有 ID：Host 仍以单飞行 + `OK`/`ERR` 判定；不得并发。
- 命令 ID 只用于匹配，不表示动作成功；`OK` 只表示固件协议层完成/接受。

### D5 — 状态语义（与 ADR-004 对齐）

| 事实 | 证据 |
|---|---|
| Host 写入串口 | `dispatched` |
| 固件回 `OK` | `adapter_acknowledged`（协议层） |
| HID Report 已提交 | 固件内部状态；Host 不作成功推断 |
| 目标系统接受/界面变化 | 只能由 Observation/Verification 证明 |

- `OK` 绝不映射 `verified_success`；`ERR` 映射 `action_rejected`/`firmware_error`（按错误文本分类）。

### D6 — 超时、重试、流控

- 默认单命令 ACK 超时 3 s（可配置）；超时后 Host 只允许 **resync**，不得自动重发非幂等命令（点击、按键、电源）。
- 文本长行按 `max_line` 分片；`type` 分片仍是多条命令，逐条等 ACK。
- 同一时刻一个命令在飞；v2 可扩展有界队列，但不得无界并发。
- 连接/重连后必须重新握手；串口断开时 Host 视为 `device_disconnected`，并释放按键/按钮。

### D7 — 输入释放

- Host 跟踪 `kdown/mdown` 状态，`close/cancel/error` 前发送 `kup/mup`（ADR-003 D3/ADR-011）。
- v2 建议固件在 USB 断开/超时后自行释放 HID 按键状态；Host 不得依赖这一行为作为唯一保护。

### D8 — Firmware Bridge 边界

- Host 端新增 `FirmwareBridge`（L4）：握手、版本、能力、命令 ID、ACK、超时、分片、重连、释放。
- `Esp32HidAdapter` 只接受标准 Actuation Command（pointer/key/text/BCI），映射到 Bridge；Operator 不得见 `amove/aclick/usage`。
- UART 传输属于 Transport Adapter；UVC 采集与 HID 串口是同一 Target 的两个 Endpoint（ADR-003）。

### D9 — 非目标

- 不把协议改为二进制/无线；不实现 OTA；不在本 ADR 修改固件源码（先定契约，Phase 4.2 再实现 v2）。
- 不移除 v1 兼容；旧固件必须能继续跑现有 Smoke（能力受限时明确报错）。

---

## 5. Consequences

### Positive

- Host 能按 Build/Variant 决定可用动作，不再靠 ERR 试错。
- ACK 与 UI 效果彻底分离；fingerprint 可区分固件。
- v1 机台不因 Host 升级失效；v2 可增量加 ID/能力。

### Negative / trade-offs

- 双协议路径增加测试矩阵；固件/ Host 必须同步发布兼容说明。
- 命令 ID 增加一行协议复杂度；v1 仍需单飞行。
- 握手超时会增加连接延迟（可缓存/并行 usb）。

### Follow-up work

- Phase 4.2：FirmwareBridge + Esp32HidAdapter；固件 v2 需单独 PR。
- ADR-001/008：固件指纹与 evidence；`ios-smoke` 记录 proto/mode。

---

## 6. Contract and dependency impact

- **Layers**：L4 Firmware Bridge/Transport；L3 只发标准 Command。
- **Ports / DTOs**：`FirmwareInfo(protocol, build, board, mode, features, max_line)`、`CommandAck(id, ok, error)`；能力映射到 ADR-001 ID。
- **Firmware**：`hello`、可选 `id=`、`features`/`mode`；v1 保持不变。
- **Import direction**：Bridge 不 import Agent/Graph；L3 不 import bridge。
- **Compatibility**：v1 行协议、115200 8N1、`OK/ERR`、126 字节上限保持；CLI/MCP 不变。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| 4.2a | 抽象 `FirmwareBridge` + FakeTransport 测试（v1 语义等价） | 无 |
| 4.2b | Host 支持 `hello`（v2 有则用，无则回落 v1）；能力映射 | 无（旧固件） |
| 4.2c | 固件 v2（hello/ID/features）单独 PR + Host 兼容测试 | 固件升级后能力更精确 |
| 4.2d | `ios-smoke` 写入 proto/mode/firmware fingerprint；能力 evidence | 有（证据） |
| Phase 4.3 | iOS Extension 使用 Bridge 的能力，不猜命令 | 有 |

**Non-goals**：OTA、无线、二进制协议、删除 v1。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/reflex/test_firmware_bridge.py`（新增）：
  - v1 无 `hello` → 回落 proto=1，基本命令仍工作；
  - v2 `hello` 解析 features/mode/board/max_line；
  - 未知/更高版本 → 安全模式，不发危险命令；
  - ACK ID 匹配/乱序/超时/`ERR` 分类；
  - 126 字节分片、长文本、`type` 分片；
  - `kdown/mdown` 超时/取消/关闭释放。
- Host/Device 兼容：v1 固件 + 新 Host；v2 固件 + 旧 Host（旧 Host 不发 `hello`，固件保持 v1 命令）。
- 真实机台：`ios-smoke` 前后 `hello` 输出、点击/按键/释放。

### Observability

- 日志：`firmware_info`、`protocol`、`features`、命令 ID、ACK 时间、resync、release。
- Incident：`firmware_error`/`command_unsupported`/`transport_error`。

---

## 9. Rollback

- Host 新 Bridge 保留 v1 路径；回滚 = 关闭 `ARTEMIS_FIRMWARE_V2=0` 只发 v1 命令。
- 固件 v2 独立 Build；可烧回 v1，Host 回落。
- 不改 UART 参数/现有命令；无数据 schema 迁移。

---

## 10. Security and safety

- 未知协议只读/安全模式，不发送点击/电源/按键。
- 非幂等命令不自动重发；ACK 只代表协议层。
- 关闭/取消释放按键、鼠标按钮；不依赖 USB 断开清理。
- 握手/features 不得成为绕过 Capability/Safety 的入口；仍由 Action Gate 校验。

---

## 11. Resolved / open questions

已提议：

- 行级 v2 + v1 兼容；`hello`/features/mode；可选 ID；单飞行。
- ACK 语义与 ADR-004 对齐；能力由 handshake + evidence。

2026-11-10 Owner 确认：

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | `max_line` | 由 `hello` 声明；Host 默认 126，握手后以声明为准 | 4.2b |
| 2 | `board` | 固件芯片 UID/MAC（不依赖用户配置） | 4.2c |
| 3 | 命令 ID | 单调计数器，固件重启清零 | 4.2c |
| 4 | v2 默认启用 | 先独立 Build，`ios-smoke` 验证后切换默认 | 4.2c/4.2d |

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial proposal | 三类 Build、无版本/能力/ID；Host 靠 ERR 猜 `amove`；ADR-001 指纹无法区分固件 |
| 2026-11-10 | Accepted | 评审通过；确认 max_line 由 hello 声明、board 用芯片 UID、命令 ID 单调、v2 独立 Build | 四项细节由 Owner 确认；Phase 4.2 可开工 |
