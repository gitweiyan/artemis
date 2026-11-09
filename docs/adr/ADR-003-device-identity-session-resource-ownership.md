# ADR-003: Device/Target/Endpoint Identity, Session Lifecycle and Resource Ownership

- **Status:** Accepted
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-1（Identity & Descriptor）、PR-3（Session Lifecycle & Resource Lease）；Phase 2；Phase 3
- **Related:** 契约 §5.1（Identity）、§5.4（DeviceSession）、§5.4.1（Lease）、§6.1（Adapter）、§10（依赖方向）；ADR-001（Capability Evidence/Fingerprint）、ADR-008（数据权威）、ADR-011（Resource Lease 机制）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §3–§4、[`IMPORT_DEPENDENCY_MATRIX.md`](../architecture/IMPORT_DEPENDENCY_MATRIX.md)、[`TEST_BASELINE.md`](../architecture/TEST_BASELINE.md)

> 状态说明：本 ADR 于 2026-11-09 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7，未在 §8 验证的能力不得标记为已验证。

---

## 1. Context and problem

### 1.1 现状事实（已核对源码）

**身份模型当前被压缩成一个字符串。**

- `DeviceContext` 只有 `host_platform`、`mobile_platform`、`device_id`、`device_width/height`（`third_party/mobile_use/context.py:61-73`），没有物理端点、显示表面、模型/OS/固件指纹，也没有“同一 Target、多个 Endpoint”的表达。
- `device_id` 在不同平台上的含义不一致：
  - Android：ADB serial（`artemis/runtime/device_pool.py:505-511`）；
  - iOS：固定别名 `ios-uvc`（`artemis/runtime/device_pool.py:482,501`）；
  - 云：`cloud_device`（`third_party/mobile_use/sdk/agent.py:160-165`）；
  - 真实物理设备信息（UVC 端口、ESP32 串口）只存在于环境变量：`ARTEMIS_UVC_DEVICE`、`ARTEMIS_ESP32_PORT`（`artemis/drivers/ios/config.py:40-41`）。
- `DeviceContext` 本身定义在 vendored 第三方包中，被 `artemis/context.py:31-36` 直接复用；目标身份 DTO 与 Agent 上下文耦合。
- ADB 端点已有局部先例：`AdbEndpoint.identity`（`artemis/runtime/adb_endpoint.py:83-87`）与 `AdbTarget.lock_scope/lock_key`（`:117-129`）已经用“端点 + serial”做锁键；UVC/串口没有对应概念。

**`DeviceSession` 没有生命周期。**

- `DeviceSession` 只有 `snapshot()` 与 `control_for()`，没有 `open/connect/close/health`（`artemis/devices/session.py:16-53`）。
- 它持有 `pointer: object | None`，由 iOS builder 写入（`artemis/devices/ios_uvc.py:92`），只有 `IOSUvcHidDriver` 读取（`artemis/drivers/ios/ios_uvc_hid_driver.py:99,121,138`）——这是 Adapter 私有状态，不是 Session 状态。
- 能力视图是 Boolean OR 汇总（`devices/session.py:28-34`），没有来源、状态或指纹（由 ADR-001 处理）。

**资源所有权没有唯一 Owner，正常路径不释放设备资源。**

- `ArtemisContext.__aexit__` 关闭 action session、后台任务、MCP client 与 Data Engine（`artemis/context.py:201-284`），**但没有关闭 driver / DeviceSession / UVC / 串口**。
- `UnifiedMobileController.cleanup()` 是唯一调用 `driver.disconnect()` 的地方（`artemis/controllers/unified_controller.py:656-658`），但生产代码没有调用者（仅 `tests/integration/test_physical_actions.py:98` 调用）。
- iOS `disconnect()` 只把 `pointer.connected` 置 False（`artemis/drivers/ios/ios_uvc_hid_driver.py:137-140`）；`UvcCapture.close()`（`artemis/reflex/capture.py:117`）与 `Esp32HidControl.close()`（`artemis/reflex/control.py:743`）也没有生产调用者。
- 结果：CLI 进程退出时靠 OS 回收句柄；但在长驻 daemon / 同一 Python 进程连续跑多任务时，UVC 与串口句柄会一直占用，外部进程无法复用。
- `HelperManager.detach_all()`（`artemis/runtime/helper_manager.py:1162-1167`）无人调用；per-device detach 只发生在 Accessibility client 关闭时（`artemis/clients/accessibility_client.py:196`）。

**设备级互斥已经存在，但范围容易误读。**

- `DeviceExecutionLock` 是 PID + process-created-at + token + session_id 的跨进程 FIFO 锁（`artemis/runtime/device_lock.py:71-120,602-760`），已有测试 `tests/unit/runtime/test_device_lock.py`（冲突、陈旧 owner 回收、ADB endpoint 隔离）。
- 它由 `AgentBase.run_task` 获取/释放（`third_party/mobile_use/sdk/agent.py:490-516,786-789`），所以 CLI/standalone、MCP、SDK 的任务路径都受保护（cloud 模式例外，按设计跳过本机锁：`:512`）。
- **不受保护的是不经 Agent 的路径**：`artemis reflex`（`artemis/interfaces/cli/commands/reflex.py`）、`artemis ios-smoke`、`artemis ios-calibrate` 直接构造 UVC/串口/驱动，没有任何 lease。
- `DeviceExecutionLock` 只证明“同一逻辑设备没有第二个 ARTEMIS 任务”，**不证明 UVC 采集卡或串口被独占**；同一台机器上两个不同 `device_id` 也可能指向同一个串口。

### 1.2 为什么现在必须决策

Phase 1 PR-3 要求“Session 生命周期 + 资源 Lease”，而 Phase 2 的 Registry/Profile Builder 和 Phase 3 的 `BaseDeviceDriver` 退役都依赖同一套所有权语义。若先实现 Registry 再补生命周期，会出现两套 owner；若先补资源锁再定身份，会把路径/枚举 index 写进锁键，日后无法迁移。

---

## 2. Decision drivers

1. **一个 Target 可以有多个 Endpoint**：iOS 机台 = 1 个逻辑 Target + UVC 观察端点 + ESP32 串口执行端点；不能因此变成两台设备，也不能共用一个 ID 做资源锁。
2. **Device 级互斥与 Host 资源独占是两种语义**：前者按任务、后者按句柄；混用会导致“设备空闲但资源被别的进程占用”或“资源空闲但设备排队”。
3. **正常结束、异常、取消、进程崩溃都必须有确定的释放路径**；不能依赖进程退出。
4. **身份必须对日志、Trace、锁、能力证据稳定**；不能使用会变化的 `/dev/videoN` 序号作为唯一身份。
5. **不破坏现有 Android/iOS 行为与第三方边界**：`DeviceContext`、`BaseDeviceDriver`、`io` 兼容层保留。
6. **可测试**：身份/生命周期/锁冲突都能用 Mock + 文件锁单测覆盖，真机只验证“端口确实释放”。

---

## 3. Considered options

### Option A — 继续保持现状（driver 自己打开、自己持有，进程退出回收）

- Benefits：零改动；CLI 单次运行看不出问题。
- Costs / risks：daemon/长驻进程泄漏 UVC 与串口；`reflex`/`smoke` 与任务并发时争抢同一端口；没有任何可测试的释放语义；违反契约 §5.4.

### Option B — 用一个全局进程锁同时覆盖设备与资源

- Benefits：实现简单，单机单设备下“够用”。
- Costs / risks：不同设备无法并行；跨进程仍需要文件锁；无法表达“设备空闲但 UVC 被另一个 Target 使用”；与现有 `DeviceExecutionLock` 的 per-device FIFO 冲突。

### Option C — Device Lease + Resource Lease 双层 + Session 作为 Owner（推荐）

- Benefits：语义分层清晰；复用现有 `DeviceExecutionLock` 的 owner/陈旧判定；资源锁可独立测试；可扩展到 macOS 窗口/Camera/BLE。
- Costs / risks：多一层文件 I/O；需要固定获取顺序、回滚与幂等关闭；迁移期要同时兼容 driver 与 session 两套入口。

### Option D — TTL 自动过期回收

- Benefits：无需实现崩溃恢复。
- Costs / risks：会抢占仍在运行的长任务（iOS 任务可达 20+ 分钟）；PID 复用会误杀；契约 §5.4.1 明确禁止仅凭 TTL 抢占活进程。

**选择：Option C。**

---

## 4. Decision (accepted)

> 以下条目在 ADR 批准后成为 MUST/SHOULD；实现细节（文件格式、FIFO/公平性、Lease Manager 抽取方式）由 ADR-011 决定，但不得违反本节语义。

### D1 — 五层身份，禁止折叠

| 概念 | 含义 | 示例 | 用途 |
|---|---|---|---|
| `host_id` | 运行 ARTEMIS 的宿主实例 | `macbook-xf` / 主机 UUID | 资源锁、Trace 归属 |
| `device_id` | **逻辑 Target**（用户视角的一台设备） | `emulator-5554`、`ios-uvc`、`cloud_device` | 任务互斥、证据归属、日志 |
| `endpoint_id` | 物理/逻辑连接端点 | `adb:tcp:127.0.0.1:5037/emulator-5554`、`uvc:<usb-serial>`、`serial:/dev/cu.usbmodem...` | 资源锁、诊断 |
| `resource_id` | 可被独占的 Host 侧资源 | `uvc:<usb-serial>/capture`、`serial:<usb-serial>/command` | Resource Lease 键 |
| `surface_id` | 观察/操作的逻辑表面 | `display:primary`、`window:<id>`、`tv:hdmi1` | 坐标/窗口/输入源 |

规则：

1. 一个 `device_id` 可以拥有 0..N 个 `endpoint_id`；新增 Endpoint **不得**改变 `device_id`。
2. `device_id` 不得由端点路径派生；现有取值保持不变（Android serial、`ios-uvc`、`cloud_device`、`mock-device`）。同一 Host 接多台同类机台时，必须为每台配置稳定别名：优先级为显式 `ARTEMIS_DEVICE_ALIAS` → 平台默认（Android serial、`ios-uvc`、`cloud_device`）；默认 `ios-uvc` 只适用于单机台。
3. `endpoint_id` 必须包含 `host_id` 或端点稳定硬件标识，避免多机/多卡冲突；优先使用 USB 硬件序列号。
4. `resource_id` 必须与访问模式绑定（`capture` / `command` / `control`）；UVC/Camera 等无硬件序列号的端点使用 **`host_id` + USB port path**（例如 `uvc:host-7f3a:usb-1-2.3/capture`），禁止使用会变化的 `/dev/videoN` 序号作为唯一身份。
5. 禁止把 `platform_family` 编入 `device_id` 再做字符串解析；平台是独立字段（`ios-uvc` 只是历史别名）。
6. `DeviceDescriptor` 是**纯数据**，不得持有 ADB/UVC/Serial 句柄；句柄只存在于 Session/Adapter。
7. `host_id` 默认取平台稳定主机标识（machine UUID、安装 ID 或“主机名 + 安装 ID”），不得使用 PID/临时目录等易变值。
8. PR-1 起有屏幕的 Profile 必须提供 `DeviceDescriptor.display`，其中 `surface_id` 必填（默认 `display:primary`）；多屏/窗口 surface 的枚举留到 Phase 7，不在 PR-1 实现。

建议 DTO（Phase 1 PR-1 落地，位置 `artemis/core/identity.py`）：

```python
# 伪代码；字段名以 PR-1 实现为准
@dataclass(frozen=True)
class EndpointRef:
    kind: Literal["adb", "uvc", "serial", "camera", "ble", "network", "cec", "ir"]
    endpoint_id: str  # host-stable
    resource_ids: tuple[str, ...]  # 可独占资源（含访问模式）
    direction: Literal["observe", "actuate", "transport"]
    metadata: Mapping[str, str]


@dataclass(frozen=True)
class DeviceDescriptor:
    device_id: str
    platform_family: Literal["android", "ios", "macos", "windows", "linux", "tv", "mock"]
    profile_id: str  # e.g. "ios.uvc_hid"
    endpoints: tuple[EndpointRef, ...]
    display: DisplayInfo | None  # pixels/rotation; surface_id required
    fingerprint: Mapping[str, str]  # model/os_build/firmware/app（ADR-001 使用）
    metadata: Mapping[str, str]
```

兼容：`DeviceContext`（third_party）不迁移、不改字段；新增 `descriptor_from_device_context(ctx) -> DeviceDescriptor` 适配器（`artemis/core/identity.py`），PR-1 只读，不改 Driver 选择。

### D2 — Session 生命周期与所有权

状态机（`DeviceSession`）：

```text
new --open()--> acquiring --(leases ok)--> connecting --(adapters ok)--> ready
                  |                            |                          |
                  +--------(failure)-----------+-----> failed            +--> degraded
new/ready/... --close()--> closing --(released)--> closed
```

规则：

1. `open()` 幂等：只执行一次；重复调用返回当前状态。`close()` 幂等：重复调用不抛错、不重复释放。
2. `observe()/execute()/snapshot()` 只允许在 `ready` 或 `degraded` 状态调用；否则抛 `SessionNotReady/SessionClosed`（不得返回伪造空结果）。
3. Session 拥有：Perception Source、Control Sink、Geometry、Capability View、**Resource Lease 集合**、Adapter 私有状态（`state[adapter_id]`）、`session_id`、`generation`。
4. Session 不拥有：Task/Plan/数据、Agent 对象、其他任务的 `DeviceExecutionLock`、Prompt/Memory。
5. 一个 `(device_id, session_id)` 只有一个 Session；工具/子 Agent 共享上下文中的同一 Session；同一设备并发第二个 Session 必须 `SessionBusy`，不得隐式嵌套。
6. **指针状态迁移**：`DeviceSession.pointer` 降级为只读兼容属性，转发到 `state["pointer"]`（或 Pointer Adapter 的状态对象）；新代码禁止写入该字段，Phase 9 删除。
7. **关闭 Owner**：`ArtemisContext.__aexit__` 是任务级 composition root，必须在 action session 关闭之后、Data Engine 关闭之前调用 `session.close()`；Driver facade 的 `disconnect()` 在完成既有清理（录像、UI client）后转发到 `session.close()`。
8. `close()` 顺序固定：释放按键/鼠标按钮/触点 → 关闭 Input/Capture/Transport 句柄（逆获取顺序）→ 释放 Resource Lease（逆获取顺序）→ 标记 `closed`。任何一步失败都必须继续后续步骤并聚合日志，不得静默跳过。
9. `failed` 是终态；重试必须新建实例，复用旧实例会导致半开句柄。

### D3 — Device Lease 与 Resource Lease 分离

| lease | 语义 | 键 | Owner | 现有实现 |
|---|---|---|---|---|
| Device Lease | 同一逻辑 Target 同时只跑一个任务 | `(host_id, device_id, lock_scope)` | 任务/Agent run | `DeviceExecutionLock`（保留） |
| Resource Lease | 同一 Host 资源同时只有一个 Owner | `(host_id, resource_id)` | DeviceSession | 新增（ADR-011 定机制） |

规则：

1. **复用而非重写**：Resource Lease 复用 `DeviceExecutionLock` 的 `pid + process_created_at + token + session_id` 陈旧判定与原子文件创建思路；不得通过“传伪 device_id”复用设备锁语义（计划 §0.2 D2）。
2. **获取顺序固定**：Device Lease（由任务层持有）→ `session.open()` → 按 `resource_id` 字典序获取 Resource Leases → 打开句柄。禁止先开句柄再取 lease。
3. **释放顺序与获取相反**；释放 lease 必须在句柄关闭之后（否则另一进程可能在句柄仍打开时拿到资源）。
4. **部分失败回滚**：任一 lease 失败，关闭已打开句柄、释放已取得的 lease，Session 进入 `failed`，错误必须指出冲突的 `resource_id` 与 owner 摘要。
5. **同 Owner 可重入**：同一 `(pid, token/session_id)` 重复获取同一资源视为幂等成功；不同 session 即使同进程也必须 `ResourceBusy`。
6. **陈旧回收**：只有 owner 进程死亡、`process_created_at` 不匹配或显式释放才可回收；**禁止 TTL 抢占活进程**；心跳只用于诊断/监督（计划 §0.2 D2）。
7. **锁内容最小化**：lease 文件只写 id/pid/时间/owner token，不写屏幕内容、凭据或用户数据；临时目录权限与文件名做归一化（复用 `DeviceExecutionLock._normalize_device_id` 的清洗思路）。
8. **覆盖范围**：UVC capture、ESP32 serial、Camera、BLE adapter 为独占资源；`ScreenAwakeLease`（`artemis/runtime/awake_lease.py:47-...`）是 ref-counted 共享资源，继续由现有实现管理，不并入独占 lease。**Helper 端口转发也使用独立 Resource Lease**（键形如 `helper:<adb-endpoint>/<serial>/<local-port>`）：`HelperManager` 的 per-serial registry 只是进程内缓存，不能证明跨进程独占；Session 在 attach 前取 lease，关闭时先 `detach` 再释放 lease。
9. **无 Agent 路径必须补锁**：`artemis reflex`、`artemis ios-smoke`、`artemis ios-calibrate` 在打开 UVC/串口前必须获取同一套 Resource Lease；`DeviceExecutionLock` 仍只由 Agent 任务路径持有（`third_party/mobile_use/sdk/agent.py:490-516`），两者不互相替代。
10. **非目标**：不保证对非 ARTEMIS 进程（相机 App、ffmpeg、OBS）的操作系统级独占；这一限制必须写进文档与错误信息。

### D4 — 与现有代码的边界

| 现有对象 | 决策 |
|---|---|
| `DeviceContext`（third_party） | 保持原样；通过 `descriptor_from_device_context` 适配。迁移到 `core` 留到 Phase 9，不在 PR-1 破坏第三方 import。 |
| `BaseDeviceDriver` | 兼容期 facade：`connect/disconnect` 转发到 Session；`disconnect()` 先保留现有清理（停止录像、关闭 UI client），再调用 `session.close()`；录像控制不搬进 Session。保留旧方法，不新增平台字段。 |
| `UnifiedMobileController` | 保留录像/日志；`cleanup()` 在 context exit 中经由已缓存的 controller 调用，或把清理职责移入 Session 后删除该方法；不得继续做无调用者的孤儿 API。 |
| `get_driver(ctx)` | 保留缓存；Session 的关闭由 context 负责，不以 driver 为 owner。 |
| `DeviceExecutionLock` | 继续作为 Device Lease 实现；不改其 FIFO/并发语义。 |
| `ActionSession` | 已由 `ArtemisContext.__aexit__` 关闭（`context.py:201-210`），作为 Session 之外的独立任务资源保留。 |
| `DeviceSession.pointer` | 只读兼容属性，Phase 9 删除；新状态放 Adapter。 |
| `drivers/factory.py` | 未来只做 Composition Root：先建 Session、再取 lease、再开句柄；PR-3 之前保持现状。 |

---

## 5. Consequences

### Positive

- 正常/异常/取消/崩溃四条路径都有确定的释放语义；daemon 不再需要重启释放 UVC/串口。
- `reflex`、`smoke`、`calibrate` 与任务运行互斥，避免“指针 belief 被另一个进程打断”。
- 身份模型支持“一个 iPhone + 两个 Endpoint”，为 Phase 2 Profile Composition 提供基础。
- 能力证据（ADR-001）可以按 `device_id + endpoint_id + fingerprint` 定位适用范围。

### Negative / trade-offs

- 每次运行增加少量文件 I/O（毫秒级）与清理逻辑；锁文件损坏需要有 grace 处理（沿用 `_MALFORMED_LOCK_GRACE_SECONDS` 思路）。
- 迁移期同时存在 driver 与 session 两套入口，必须用 facade 保证行为不变。
- 资源锁只覆盖 ARTEMIS 进程；外部程序仍可能抢占 UVC，需要明确的错误文案而非静默重试。

### Follow-up work

- ADR-011：Lease Manager 的模块落点、文件布局、FIFO/公平性、心跳与清理命令。
- ADR-001/008：指纹字段清单与证据存储。
- PR-1/PR-3 的 Contract Tests（见 §8）。

---

## 6. Contract and dependency impact

- **Layers / domains**：L4（Device Runtime）新增 Session 生命周期与 Lease；L1（CLI/reflex/smoke）只调用 Port，不直接开句柄；L2/L3 不感知 lease。
- **Ports / DTOs / Capability IDs**：新增 `DeviceDescriptor`、`EndpointRef`、`DisplayInfo`（`artemis/core/identity.py`）；`CapabilitySet` 不变（ADR-001 处理演进）；`DeviceSession` 增加 `open/close/health/state`（additive）。
- **Adapters / Profiles**：Profile 只组合 Adapter；Profile 构建必须报告缺失的 endpoint/resource，不得静默回退。
- **Import/dependency direction**：`core/identity.py` 只依赖标准库；不得 import `drivers/devices/third_party`；`devices/` 可以 import `core`；`drivers/` 不 import 新的 identity 实现细节，只消费 DTO。
- **Compatibility facade / public API**：`DeviceContext`、`BaseDeviceDriver`、`get_driver`、`UnifiedMobileController` 公开签名不变；`DeviceSession.pointer` 保留只读转发一阶段；拒绝任何需要调用方改 import 的破坏性变更。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-1 | `core/identity.py`：DTO + `descriptor_from_device_context`；Android/iOS/Mock 生成 descriptor（只读，不参与选择） | 无 |
| PR-3a | Session 状态机 + `close()` + `ArtemisContext.__aexit__` 接线；driver facade 转发；`cleanup()` 去留明确 | 退出时释放资源（行为修复） |
| PR-3b | Resource Lease（UVC/serial/camera）+ Reflex/Smoke/Calibrate 接入；`pointer` 状态迁移 + 兼容属性 | 并发冲突时明确 `ResourceBusy` |
| Phase 2 | Composition Root 先取 lease 再开句柄；Profile 报告缺失资源 | 组合失败原因更明确 |
| Phase 3 | `BaseDeviceDriver` 退役为 facade；单设备边界收敛 | 旧入口逐步删除 |

**Non-goals（本 ADR 不做）**：多主机分布式租约、lease TTL 抢占、跨设备任务调度、macOS/TV Adapter、把 `DeviceContext` 迁出 third_party、改变现有设备选择顺序。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/core/test_identity.py`（新增）：descriptor 字段、单 Target 双 Endpoint、无句柄、`device_id` 不因 endpoint 变化。
- `tests/unit/devices/test_session.py`（扩展）：`open` 幂等、未 ready 时 observe 报错、`close` 幂等、部分获取回滚、逆序释放、异常聚合、关闭后调用。
- `tests/unit/runtime/test_resource_lease.py`（新增，或扩展 `test_device_lock.py`）：独占冲突、同 owner 重入、陈旧 owner 回收、活进程不被 TTL 抢占、`resource_id` 排序无死锁。
- `tests/unit/drivers/test_ios_uvc_hid_driver.py`（扩展）：`disconnect` 关闭 capture/serial（spy 断言）、重复关闭、异常路径。
- `tests/unit/test_context.py`（扩展）：`__aexit__` 调用 `session.close()`，且 action session 先关闭、Data Engine 后关闭。

### Integration / hardware

- `tests/integration/ios/`（hardware marker）：`ios-smoke` 退出后立即重新打开同一串口成功；两个进程并发运行只有一个成功；按住键后取消，按键被释放。
- Android：任务结束后 helper `detach` 且 forward 被移除（`HelperManager.session(serial) is None`）。

### Observability

- Trace/日志字段：`session.opened`、`session.closed`、`session.close_reason`、`lease.acquired/released`、`resource_id`、`owner_pid`、`owner_started_at`、`session_id`。
- 释放失败必须产生 `release_failed` incident，不得静默吞掉。

### Verified 判据

- 本 ADR 的“资源释放”只有通过真机（iOS 串口/UVC）验证后才能标 `verified`；Mock 测试只能标 `available`/contract-tested。

---

## 9. Rollback

- 实现以 additive 模块 + 接线为主：回滚 = 移除 `ArtemisContext.__aexit__` 的 `session.close()` 调用与 factory 的 lease 接线（单点回退），保留 DTO 不影响旧代码。
- 配置开关：`ARTEMIS_DEVICE_RESOURCE_LEASE=0` 可跳过 Resource Lease（默认在 PR-3 后开启），用于现场排障；不得跳过输入释放与句柄关闭。
- Lease 文件位于 `temp_dir("device-locks")`，回滚后可用现有 `DeviceExecutionLock.cleanup_stale_locks()` 思路清理；不涉及数据库 schema、不改变公开 DTO。
- 不删除旧的 `cleanup()`/`disconnect()` 路径，直到 PR-3 验收通过。

---

## 10. Security and safety

- Lease 文件只含设备/资源标识、PID、进程启动时间、session id、时间戳；不写屏幕内容、账号、Token、串口数据。
- 文件名与 `resource_id` 必须归一化，防止路径注入（复用 `_normalize_device_id` 的字符白名单）。
- 取消/异常/断连路径必须先释放按键、鼠标按钮、触点，再关闭句柄；否则 iOS 会卡键。
- 非幂等动作不因资源恢复而自动重试；重试策略仍由 Action 层决定。
- 资源冲突的错误必须明确（`resource_busy` + owner 摘要），禁止自动“等一会儿再试”造成双击或重复提交。

---

## 11. Resolved questions (2026-11-09)

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | `device_id` 别名配置键 | 使用 `ARTEMIS_DEVICE_ALIAS`；未设置时按平台默认（Android serial / `ios-uvc` / `cloud_device`） | PR-1 |
| 2 | UVC 无硬件序列号时的 `resource_id` | `host_id` + USB port path（有硬件序列号时优先用序列号） | PR-3 + ADR-011 |
| 3 | `surface_id` 是否进 PR-1 descriptor | 是；`DisplayInfo.surface_id` 必填，默认 `display:primary`，多屏枚举留到 Phase 7 | PR-1 |
| 4 | Helper 端口转发是否独立 Resource Lease | 是；`helper:<adb-endpoint>/<serial>/<local-port>` 独立 lease，不依赖进程内 registry | PR-3 + ADR-011 |
| 5 | `DeviceContext` 迁出 third_party 的时机 | Phase 9；PR-1 只加适配器，不改 `DeviceContext` | Phase 9 |

**Owner：** ARTEMIS architecture maintainers（评审维持不变）。

尚未关闭的实现细节（不阻塞 PR-1/PR-3）：Lease 文件布局、FIFO/公平性、心跳与清理命令 → ADR-011。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | Phase 0 审计确认：Session 无生命周期、UVC/串口无 Owner、reflex/smoke 绕过设备锁；需要先定身份/所有权再实施 PR-1/PR-3 |
| 2026-11-09 | Accepted | 评审通过；确定 `ARTEMIS_DEVICE_ALIAS`、UVC port-path 身份、PR-1 引入 `surface_id`、Helper forward 独立 lease | 5 项开放问题已由 Owner 答复，PR-1/PR-3 可进入实现 |

---

## 13. Evidence index

| 主题 | 位置 |
|---|---|
| `DeviceContext` 字段 | `third_party/mobile_use/context.py:61-73` |
| 设备解析 | `artemis/runtime/device_pool.py:482,485-511` |
| ADB 端点身份先例 | `artemis/runtime/adb_endpoint.py:83-87,117-129` |
| Device 锁 | `artemis/runtime/device_lock.py:71-120,602-760`；`tests/unit/runtime/test_device_lock.py` |
| Agent 取锁 | `third_party/mobile_use/sdk/agent.py:490-516,786-789` |
| Session 现状 | `artemis/devices/session.py:16-53` |
| 指针状态 | `artemis/devices/ios_uvc.py:92`；`artemis/drivers/ios/ios_uvc_hid_driver.py:99,121,138` |
| 未释放的句柄 | `artemis/reflex/capture.py:117`、`artemis/reflex/control.py:743`、`artemis/controllers/unified_controller.py:656-658` |
| Context 退出 | `artemis/context.py:201-284` |
| 无锁入口 | `artemis/interfaces/cli/commands/reflex.py`、`ios_smoke.py`、`ios_calibrate.py` |
| Helper 生命周期 | `artemis/runtime/helper_manager.py:1144-1167`；`artemis/clients/accessibility_client.py:196` |
| Wake lock（共享资源反例） | `artemis/runtime/awake_lease.py:47-...` |
