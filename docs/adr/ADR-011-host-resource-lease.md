# ADR-011: Shared Host Resource Lease (UVC / Serial / Camera / Helper Forward)

- **Status:** Accepted
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-3（Session Lifecycle & Resource Lease）；Phase 2（Composition Root 取 lease）
- **Related:** 契约 §5.4.1（Lease）、§6.1（Adapter）、§7.2（Incident）；[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted，语义与所有权）、[ADR-008](ADR-008-data-engine-evidence-ownership.md)（Accepted，证据存储）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §4、§12（N4）、`artemis/runtime/device_lock.py`、`artemis/runtime/helper_manager.py`、`artemis/drivers/factory.py`

> 状态说明：本 ADR 于 2026-11-09 经评审 **Accepted**；§4 是可执行契约。ADR-003 已确定“有哪些 lease、谁拥有、释放顺序”，本 ADR 只决定 **Host Resource Lease 的机制**（模块、文件格式、API、公平性、回收、诊断）。

---

## 1. Context and problem

### 1.1 现状事实（已核对源码）

**设备级互斥已存在，且语义明确。**

- `DeviceExecutionLock` 是按逻辑设备串行化的**全局 FIFO 任务队列 + 每设备互斥**：`DeviceLockOwner` 记录 `pid / process_created_at / token / device_id / session_id / ingress / lock_scope`（`artemis/runtime/device_lock.py:38-68`），文件位于 `get_temp_dir("device-locks")/artemis-device-<id>.lock`（`:122,132`）。
- 陈旧判定分两档：正常 payload 用 `pid_is_alive(pid, process_created_at)`（`:195-214`、`process_probe.py:40-70`）；损坏/旧 payload 先等 `_MALFORMED_LOCK_GRACE_SECONDS = 5`（`:83,230`）再回收。
- 清理入口已接入 `artemis doctor`（`doctor.py:107`）、daemon 生命周期（`server_lifecycle.py:342,371,417`）与 `diagnose` 工具（`mcp_server/tools/diagnose.py:890`）。
- 它的 acquire 是阻塞式 FIFO（`device_lock.py:602-708`），**不适合**作为 Host 资源锁：资源锁不应该让一个进程在“等 UVC”时占用设备队列位置，也不应该为了一个串口引入第二套全局排队。

**共享资源完全没有锁。**

- iOS 的 UVC 与串口在 composition root 直接打开：`UvcCapture/UvcStream`、`Esp32HidControl`（`drivers/factory.py:155-163`）；CLI `ios-smoke`（`:76,83`）、`ios-calibrate`（`:56,64`）、`artemis reflex`（`:102`）与 `play_reflex`（`tools/reflex_tool.py:164`）同样直接构造。
- 两个进程可以同时打开同一个采集卡/串口：`DeviceExecutionLock` 只锁 `device_id`，同机不同 `device_id`（或绕过 Agent 的 smoke/reflex）完全不受保护；ADR-003 §1.1 已确认这一缺口。
- `HelperManager` 的 `_create_forward` 用 `adb forward tcp:0`（`helper_manager.py:953-969`）让系统分配端口，再 `_wait_for_ping`；它只有**进程内** per-serial registry（`:608,1134`），跨进程仍可能绑定/复用同一隧道。

**已有可复用的邻近实现。**

- `ScreenAwakeLease` 是 ref-counted 共享资源（`awake_lease.py:44-67`），但语义不同（共享引用 vs 独占），且它自己实现了 PID/创建时间判定（`:71-105`）。
- `DeviceExecutionLock` 的原子创建、owner payload、`pid_is_alive`、`safe_unlink` 是可复用的底层原语；ADR-003 已明确“复用而非重写”。

### 1.2 为什么必须现在决策

PR-3 要落地 Session 生命周期与资源租约；如果不先定机制，会出现三种错误实现：

1. 给 `DeviceExecutionLock` 传伪 `device_id` 来锁 UVC/串口（ADR-003 明确禁止）；
2. 每个 Adapter 自己写一套文件锁（格式/回收不一致）；
3. 用 TTL/心跳直接抢占（会杀掉仍在运行的长任务）。

---

## 2. Decision drivers

1. **与设备锁解耦**：资源锁只在“打开句柄”前后生效，不参与任务排队；不改变 `DeviceExecutionLock` 的 FIFO/并发语义。
2. **跨进程、可恢复**：同机多进程（CLI、daemon worker、MCP runner、reflex）互斥；进程崩溃后可回收；活进程不被 TTL 抢占。
3. **可诊断**：能列出“谁占着哪个 UVC/串口”、owner PID/会话/时间；`doctor` 可清理陈旧记录。
4. **可测试**：临时目录 + 假 PID 即可覆盖冲突、陈旧、回滚；不依赖真实硬件。
5. **实现最小**：复用现有文件锁原语，不引入 fcntl/lockfile 依赖，不建全局队列。
6. **跨平台**：macOS/Linux/Windows 的 `os.open(O_CREAT|O_EXCL)` + JSON 行为一致。

---

## 3. Considered options

### Option A — 复用 `DeviceExecutionLock`，把 resource_id 当 device_id

- Benefits：零新代码。
- Costs / risks：两个不同范围的锁共用一个 FIFO 队列，任务会在“等串口”时占住设备队列；`cleanup_stale_locks` 会同时扫两类文件；ADR-003 明确禁止。

### Option B — 每个资源一条 FIFO 队列

- Benefits：严格公平。
- Costs / risks：两套 FIFO 与设备锁叠加会形成锁顺序/优先级反转；资源持有时间通常只有一次会话，排队收益低；实现与测试成本高。

### Option C — 独立 Resource Lease 注册表：PID + 创建时间 + token，轮询等待、无队列（推荐）

- Benefits：语义单一；与设备锁解耦；可按资源独立超时/取消；实现小、可测试。
- Costs / risks：公平性 best-effort（同一资源多个等待者时可能被后来者抢到）；需要固定获取顺序避免死锁（ADR-003 已规定）。

### Option D — 直接使用 OS 文件锁（flock/LockFileEx）

- Benefits：进程崩溃自动释放。
- Costs / risks：语义依赖平台/文件系统；无法在文件里记录 owner/会话用于诊断；Windows 与网络盘行为不同；现有代码已有一套可复用的原子文件租约。

**选择：Option C。**

---

## 4. Decision (accepted)

### D1 — 模块与 API

新增 `artemis/runtime/resource_lease.py`（与 `device_lock.py` 同级，L4/Host Runtime）：

```python
@dataclass(frozen=True)
class ResourceLeaseOwner:
    pid: int
    process_created_at: float
    token: str
    resource_id: str
    host_id: str
    description: str
    acquired_at: str
    session_id: str | None = None
    heartbeat_at: str | None = None


class ResourceBusyError(RuntimeError):
    resource_id: str
    owner: ResourceLeaseOwner | None


class ResourceLease:
    def __init__(
        self,
        resource_id: str,
        *,
        host_id: str = "",
        description: str = "",
        session_id: str | None = None,
        token: str | None = None,
        lease_dir: Path | None = None,
    ) -> None: ...
    def acquire(
        self,
        *,
        blocking: bool = True,
        timeout: float | None = 10.0,
        cancel_event: Any | None = None,
    ) -> "ResourceLease": ...
    def release(self) -> None: ...
    def touch(self) -> bool: ...  # 只更新 heartbeat_at，用于诊断
    def __enter__(self) -> "ResourceLease": ...
    def __exit__(self, *exc) -> None: ...


class LeaseSet:
    def acquire(self, resource_ids: Iterable[str], **kwargs) -> "LeaseSet": ...
    def release(self) -> None: ...
    def __enter__(self) -> "LeaseSet": ...
    def __exit__(self, *exc) -> None: ...
```

- 同步 API（文件 I/O 毫秒级）；异步调用方用 `asyncio.to_thread`，与现有 `DeviceExecutionLock` 的调用方式一致。
- `LeaseSet` 负责多资源排序获取、失败回滚、反向释放（ADR-003 D3）。
- 不在本模块解析平台/设备配置；只接受已经由 descriptor 生成的 `resource_id`（ADR-003/PR-1）。

### D2 — 目录、键与文件格式

- 目录：`get_temp_dir("resource-leases")`；可用 `ARTEMIS_RESOURCE_LEASE_DIR` 覆盖（测试、运维、容器）。
  - 与 `device-locks/` 分开，清理逻辑互不影响。
- 文件：`resource-<sha256(resource_id)[:24]>.lock`（全 ID 放 payload）。
  - 用哈希而不是可读 ID：避免 `/`、`:`、长度、路径穿越与大小写文件系统差异；同一 ID 在不同平台得到同名文件。
  - payload 带 `schema_version: 1`，便于以后迁移。
- 锁键：`resource_id` 本身（ADR-003 要求它已包含 `host_id` 与访问模式，如 `uvc:<host>:<key>/capture`）。
- payload：

```json
{
  "schema_version": 1,
  "resource_id": "uvc:host-a:usb-1-2.3/capture",
  "host_id": "host-a",
  "pid": 12345,
  "process_created_at": 1730000000.0,
  "token": "…",
  "session_id": "…",
  "description": "ios.uvc_hid",
  "acquired_at": "2026-11-09T12:00:00+00:00",
  "heartbeat_at": "2026-11-09T12:00:05+00:00",
  "metadata": {"local_port": "50000"}
}
```

- 文件权限 0600；目录由 `get_temp_dir` 创建（用户私有）。
- 不写屏幕内容、凭据、串口数据；`metadata` 只放诊断需要的端口/路径摘要。

### D3 — Owner、陈旧与回收

复用 ADR-003 的 owner 语义与现有原语：

1. owner = `pid + process_created_at + token (+ session_id)`；`token` 默认 `uuid4().hex`，可由调用方注入（测试/重入）。
2. 活进程判定：`pid_is_alive(pid, process_created_at)`；`process_created_at <= 0` 的旧 payload 沿用 `DeviceExecutionLock._owner_is_alive` 的保守策略（ADR-011 将它们抽到共享原语）。
3. 损坏/半写文件：`mtime` 小于 `MALFORMED_GRACE_SECONDS = 5` 时视为忙，超过后回收（与 `device_lock.py:83,230` 一致）。
4. **禁止 TTL 抢占**：`heartbeat_at` 只用于诊断；活进程的 lease 不会因时间到期被删除。
5. 回收只允许：owner 死亡、`process_created_at` 不匹配（PID 复用）、显式 `release`、显式 `cleanup_stale()`。
6. `cleanup_stale()`：扫描 `resource-*.lock`，删除死/损坏过期文件，返回数量；接入 `artemis doctor` 与 daemon 启动清理（与 `DeviceExecutionLock` 并列，不合并）。

### D4 — acquire / release / 重入语义

1. `acquire()` 幂等：同一 `(pid, token 或 session_id)` 重复获取同一资源成功，且不重复写文件；用进程内 refcount 管理（与 `ScreenAwakeLease` 的引用计数思路一致）。
2. 同进程不同 session 必须 `ResourceBusyError`（防止两个任务在同一 Python 进程里互踩）。
3. `blocking=False` 立即失败；`blocking=True` 按 `poll_interval=0.1s` 轮询，直到 `timeout`（默认 10s）或 `cancel_event` 置位。
4. `release()` 幂等：文件不存在视为已释放；只有匹配 owner 才能删除；文件已被别人回收/替换时记录 warning，不删除对方 lease。
5. `close()`/异常/取消路径必须调用 `release()`；`LeaseSet` 在异常时释放已取得的全部 lease（反向顺序）。
6. 释放顺序：**先关闭句柄，再释放 lease**（ADR-003 D3 rule 3）；本模块不关闭句柄，只提供顺序约束。

### D5 — LeaseSet：固定顺序与回滚

- 输入 `resource_ids` 去重后按字典序排序，再逐个 acquire。
- 任一失败：按**相反顺序**释放已取得项，抛 `ResourceBusyError`（附冲突资源与 owner 摘要）。
- 相同 `LeaseSet` 重复 acquire 同一 ID 只计一次。
- 不实现跨进程队列：多个等待者按轮询竞争；`timeout` 内未拿到即失败，调用方决定重试/降级（不得在非幂等动作后自动重试）。

### D6 — Helper Forward 的锁键

- ADR-003 §4 D3 示例为 `helper:<adb-endpoint>/<serial>/<local-port>`；但端口由 `adb forward tcp:0` 在 attach 时才分配（`helper_manager.py:953-969`），**无法在加锁前知道**。
- 本 ADR 细化为：锁键 `helper:<adb-endpoint>/<serial>/forward`，`local_port` 写入 payload `metadata`。
  - 语义不变（同一 serial 的 helper 隧道同一时刻只有一个 owner）；
  - 避免“先选端口再抢锁”的竞态与重绑定失败。
- 获取时机：`HelperManager.attach()` 选端口/建 forward **之前**；释放时机：`detach()` 移除 forward **之后**。
- `HelperManager` 的进程内 registry 保留；它不能替代跨进程 lease。

### D7 — 与现有模块的关系

| 模块 | 关系 |
|---|---|
| `DeviceExecutionLock` | 保持独立、FIFO、按逻辑设备；不改公开 API。共享的底层函数抽到 `artemis/runtime/lease_primitives.py`（纯搬迁）。 |
| `ScreenAwakeLease` | 不变；ref-counted 共享资源，不并入独占 lease。 |
| `HelperManager` | 增加 lease 获取/释放调用；进程内 registry 继续存在。 |
| `drivers/factory.py` | PR-3 在创建 UVC/serial 前 acquire，在 session close 时 release；本 ADR 不实现。 |
| `ios-smoke` / `ios-calibrate` / `reflex` | PR-3 包一层 `LeaseSet`；失败时报 `resource_busy`，不得静默继续。 |
| `doctor` / `diagnose` | 增加 `cleanup_stale()` 与 `get_active_leases()` 展示。 |

### D8 — 非目标

- 不做跨主机/分布式租约（`resource_id` 含 host_id，仅本机）。
- 不保证对非 ARTEMIS 进程（相机 App、OBS、ffmpeg）的 OS 级独占。
- 不改变 `DeviceExecutionLock` 的排队/并发策略。
- 不实现资源池/配额/优先级；不做自动重试。
- 不在本 ADR 实现句柄关闭与 Session 生命周期（PR-3/ADR-003）。

---

## 5. Consequences

### Positive

- UVC/串口/摄像头/helper 隧道获得跨进程独占，reflex/smoke 与任务不再互相打断。
- 与设备锁解耦：不会为了等资源占住设备 FIFO。
- 冲突可诊断（owner PID/会话/时间），可直接接 `doctor`。
- 复用现有原子文件原语，无新依赖；测试可在临时目录完成。

### Negative / trade-offs

- 无严格 FIFO：等待者可能被后来者抢到，最坏情况是超时失败；调用方需处理 `resource_busy`。
- 文件锁只覆盖 ARTEMIS 进程；外部程序仍可能抢占。
- 多资源按排序获取会“持有前面的锁等后面的锁”，但全局顺序消除了环；资源数很少（≤3），可接受。
- 多一个临时目录与清理入口。

### Follow-up work

- 契约 §7.2 增加 `resource_busy` incident（本 ADR 批准后）。
- PR-3：原语抽取、ResourceLease/LeaseSet、factory/CLI/reflex/helper 接线、句柄关闭。
- Phase 6：`doctor` / Admin Console 的 lease 展示与清理视图。

---

## 6. Contract and dependency impact

- **Layers**：Host Runtime（L4 邻接）；Adapter 只接收已租用的资源，不自行加锁。
- **Ports / DTOs**：新增 `ResourceLeaseOwner`、`ResourceBusyError`、`ResourceLease`、`LeaseSet`；`resource_id` 来自 PR-1 的 `DeviceDescriptor`（`artemis/core/identity.py`）。
- **Incident**：新增 `resource_busy`（含 `resource_id` 与 owner 摘要）；批准后写入契约 §7.2。
- **Import direction**：`resource_lease.py` 只依赖标准库 + `runtime/process_probe` + `lease_primitives`；不得 import `drivers/devices/reflex/third_party`。
- **Compatibility**：`DeviceExecutionLock` 公开行为不变；`ScreenAwakeLease` 不变；无 schema/DB 变更。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-3a | 抽取 `lease_primitives.py`（owner payload、pid 存活、原子写、safe unlink、grace）；`DeviceExecutionLock` 改为调用它，并保留 `_owner_is_alive` / `_read_owner` / `_safe_unlink` / `_atomic_write_json` 的类级薄包装（现有测试用 monkeypatch 这些 seam） | 无（纯搬迁 + 现有测试） |
| PR-3b | 新增 `resource_lease.py` + 单测（冲突/重入/陈旧/超时/取消/回滚/哈希文件名） | 无（未接线） |
| PR-3c | factory / smoke / calibrate / reflex / helper 接线；Session 持有 `LeaseSet`；close 释放 | 冲突时报 `resource_busy`；正常路径不变 |
| PR-3d | 真机验证：UVC/串口释放、并发拒绝、取消/异常释放 | 硬件验证 |

**Non-goals**：资源队列、跨主机、TTL 抢占、自动重试、把 `DeviceExecutionLock` 改成资源锁。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/runtime/test_resource_lease.py`（新增）：
  - 同一资源第二个 owner → `ResourceBusyError`（含 owner 摘要）；
  - 同 owner 重入（session/token）→ 幂等；释放一次仍持有，释放到 0 才删除；
  - 同进程不同 session → busy；
  - 活进程 lease 不因 `heartbeat_at` 过期被抢；
  - 死 PID / PID 复用 / 损坏文件 grace → 回收；
  - timeout、`blocking=False`、`cancel_event`；
  - `LeaseSet` 按序获取、失败回滚、反向释放；
  - `resource_id` 含 `../` 不产生路径穿越（哈希文件名）；
  - `release` 不删除别人的 lease；重复 release 无异常。
- `tests/unit/runtime/test_lease_primitives.py`（新增/扩展）：payload 序列化、`pid_is_alive` 复用、`safe_unlink`。
- 现有 `tests/unit/runtime/test_device_lock.py` 全绿（证明搬迁不改变行为）。

### Integration

- 子进程 A 持有 lease，父进程 B `timeout=0.5` 失败；A 被 kill -9 后 B 在 grace 后成功。
- 两个 `ios-smoke` 并发：一个成功、一个 `resource_busy`；退出后串口可立即重开。
- `doctor` 清理：手工写死 owner 文件 → `cleanup_stale()` 删除。

### Hardware / platform evidence

- iOS 真机：先 `ios-smoke` 再 `reflex`，第二个在第一个退出后能立即拿到 UVC/串口；第一个被 Ctrl-C 后仍能释放。
- 真机证据只能标 `verified`；Mock 只能证明状态机与文件语义。

### Observability

- 日志/Trace：`lease.acquired`/`lease.released`、`resource_id`、`owner_pid`、`owner_started_at`、`session_id`、`wait_ms`、`release_reason`。
- `doctor` 输出 active leases（资源、PID、会话、持有时间、心跳）。

---

## 9. Rollback

- 模块 additive；未接线前无行为影响。
- 接线后用 `ARTEMIS_RESOURCE_LEASE=0` 跳过 acquire/release（默认开启）；**不跳过**句柄关闭与输入释放。
- 回滚删除 `<temp>/resource-leases/` 即可；不影响 `device-locks/`、DB、公开 API。
- `lease_primitives` 抽取是纯搬迁，回滚只需恢复 `DeviceExecutionLock` 内部调用。

---

## 10. Security and safety

- lease 文件只含 ID/PID/时间/会话/端口；0600、用户临时目录；不含屏幕内容/凭据。
- `resource_id` 经过哈希文件名，拒绝路径穿越与超长文件名。
- 冲突错误必须显式（`resource_busy` + owner），禁止“等一会儿重试”造成重复点击/重复提交。
- 释放 lease 前必须先关闭句柄/释放输入；否则会出现“锁已释放但端口仍被本进程占用”。
- 心跳只用于诊断，不参与抢占，避免长任务被误杀。

---

## 11. Resolved questions (2026-11-09)

已定：

- 无 FIFO/无全局队列；轮询 + timeout + cancel。
- 文件用 `sha256(resource_id)` 命名，payload 带完整 ID 与 schema 版本。
- `DeviceExecutionLock` 保持独立；只抽取底层原语。
- Helper forward 键为 `helper:<adb-endpoint>/<serial>/forward`，端口入 metadata（细化 ADR-003 示例）。
- 心跳只诊断不抢占；`cleanup_stale` 接入 doctor/daemon。

Owner 确认的细节：

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | 默认 timeout | **10 s**；调用方按场景覆盖（UVC 冷启动可更长） | PR-3b |
| 2 | 清理/查看入口 | 先走 `artemis doctor` + `get_active_leases()`；Phase 6 再评估独立 `artemis resource-leases` 命令 | PR-3c / Phase 6 |
| 3 | metadata 字段 | 只允许诊断字段白名单（如 `local_port`）；拒绝业务任意字段 | PR-3b |

`resource_busy` 已作为规范 Incident 写入契约 §7.2。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | ADR-003 只定语义；PR-3 需要具体机制。现状 UVC/串口无跨进程锁，reflex/smoke 可绕过设备锁 |
| 2026-11-09 | Accepted | 评审通过；确认 10 s 默认 timeout、doctor 清理入口、metadata 白名单 | 3 项细节已由 Owner 确认；PR-3 可进入实现 |
