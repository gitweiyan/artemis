# ADR-001: Capability Model, Evidence, Fingerprint and Invalidation

- **Status:** Proposed
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-2（Capability Catalog & Evidence）；Phase 2（Registry/Profile 选择）；Phase 4.2（固件能力握手）
- **Related:** 契约 §5.2（Profile）、§5.3（Capability Model）、§5.3.1（Evidence/Fingerprint）、§6.1（Adapter）、§9（ADR 触发条件）；[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted）、[ADR-008](ADR-008-data-engine-evidence-ownership.md)、ADR-009（固件协议）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §8–§12、[`IMPORT_DEPENDENCY_MATRIX.md`](../architecture/IMPORT_DEPENDENCY_MATRIX.md)、[`TEST_BASELINE.md`](../architecture/TEST_BASELINE.md)

> 状态说明：Proposed。批准前只允许盘点、原型和测试设计；不得合入改变公共契约的实现。

---

## 1. Context and problem

### 1.1 现状事实（已核对源码）

**能力是 8 个 Boolean，且没有来源。**

```python
# artemis/devices/capabilities.py
@dataclass(frozen=True)
class CapabilitySet:
    supports_ui_tree: bool = False
    supports_absolute_tap: bool = False
    supports_relative_pointer: bool = False
    requires_pointer_precheck: bool = False
    supports_ocr: bool = False
    supports_text_input: bool = False
    supports_shell: bool = False
    supports_app_launch: bool = True  # 默认 True
```

- 没有 capability ID、域、来源、状态、前置条件、探测时间、证据引用或指纹。
- `requires_pointer_precheck` 是**安全策略要求**，被混进能力集合（`control/hid.py:50`、`ios_uvc_hid_driver.py:65`、`selector.py:64`）。
- `supports_app_launch` 默认 `True`：未声明的 Adapter 会被当成“支持启动 App”，这是能力真实性的反例（`tests/unit/devices/test_capabilities.py` 只断言默认值，没有来源校验）。

**同一组 Boolean 同时承担三种语义。**

| 用途 | 位置 | 需要的语义 |
|---|---|---|
| 组合选择（连接前） | `orchestration/selector.py:70-74`、`drivers/factory.py:113-126` | Adapter **声明**的能力 |
| Session 汇总 | `devices/session.py:28-34`（OR） | Source/Sink 的声明 |
| 运行时派发 | `devices/session.py:50-53`、`control/composite.py:43-46`、`tools/reflex_tool.py:88`、`mcp/actuators/adb.py:368` | 当前实例**实际可用**的能力 |

连接前 factory 用的是类级声明（`drivers/factory.py:114` 的 `IOSUvcHidDriver.capabilities`）；iOS driver 在 `__init__` 里再用 session 能力覆盖（`drivers/ios/ios_uvc_hid_driver.py:78-84`）。因此“声明”和“本次可用”从未被分开表达。

**没有任何验证证据或指纹。**

- 全仓 `grep verified|fingerprint|evidence` 只命中 UX 文案、Memory 的 `verified_facts` 与 Checker 文本，没有能力证据模型。
- 能力“已验证”只存在于文档与人工记忆（例如 [iOS Rig Playbook](../ios-rig-playbook.md) 的实机结论），`Smoke` 也不产出结构化证据（`drivers/ios/smoke.py` 仅生成 `SmokeStep` 报告）。
- `artemis ios-smoke` 的六阶段（connect/snapshot/frame_fit/pointer_motion/pointer_precheck/tap）是最接近契约测试的资产，但没有把结果写成 evidence。

**契约已经要求，但实现没有落点。**

契约 §5.3 要求规范能力 ID（`frame.capture`、`pointer.relative` 等）与五态；§5.3.1 要求 evidence reference、指纹、失效规则，并写明证据存储由 ADR-001/ADR-008 决定。Phase 1 PR-2 与 Phase 2 的 Profile 选择都依赖这一决定；ADR-003 已定义 `device_id`/`endpoint_id`，指纹可以直接使用。

### 1.2 为什么现在必须决策

- 没有状态区分，Profile 组合会把“声明支持”当成“本次可用”，错误在第一次动作时才暴露，而且无法解释。
- 没有指纹，任何“已验证”都会跨设备/固件/OS 继承，违反契约对 `verified` 的定义。
- 若先写 Registry/Profile（Phase 2）再补能力模型，会出现两套能力来源（Boolean 与 Catalog），迁移成本更高。

---

## 2. Decision drivers

1. **真实性**：没有证据不得 `verified`；没有探测不得 `available`；缺能力必须显式，不得默认 `True`。
2. **决策时点不同**：连接前只能基于 `declared` 组合；执行前必须基于 `available`/`verified`。
3. **可追溯**：每条状态能回答“谁声明的、何时探测的、哪次测试验证的、适用哪个设备/固件/OS”。
4. **可失效**：固件/Adapter/OS/权限/配置变化时，旧证据自动失效，但历史不被删除。
5. **可迁移**：PR-2 先加视图与证据，不改变 Android/iOS 行为；执行切换到 Phase 3。
6. **跨平台**：TTV/Remote 等未来能力复用同一 Catalog，不在 Agent/Selector 写平台分支。

---

## 3. Considered options

### Option A — 继续用 Boolean，追加几个状态字段

- Benefits：改动最小。
- Costs / risks：无法表达来源/证据/指纹；`requires_pointer_precheck` 继续混在能力里；新平台能力继续改 dataclass。

### Option B — 规范 Catalog + 每能力一条带状态/来源/证据的 Record（推荐）

- Benefits：能力 ID 稳定；声明/探测/证据分离；兼容层集中；可跨平台。
- Costs / risks：需要转换层与 Session 探针；PR-2 新增代码但不改行为。

### Option C — 由 Adapter 输出自由字符串能力

- Benefits：扩展零成本。
- Costs / risks：拼写漂移（`touch.abs`/`touch.absolute`）；无法静态校验；违反契约 §5.3 的规范 ID。

### Option D — 证据由 Adapter 自己持有（存本地的 `verified=True`）

- Benefits：实现简单。
- Costs / risks：Adapter 重启/换机/升级即失效；无法审计；与契约 §5.3.1 冲突。

**选择：Option B。**

---

## 4. Decision (proposed)

### D1 — 规范 Catalog

位置：`artemis/core/capabilities.py`（PR-2 新建，只依赖标准库）。

- `CapabilityDomain`：`observation` / `actuation` / `transport` / `diagnostics`。
- `CapabilityId`：契约 §5.3 的规范 ID 全集（`StrEnum`），初始集合：

| 域 | Capability ID |
|---|---|
| Observation | `frame.capture`、`ui_tree.read`、`ocr.read`、`window.inspect`、`log.read` |
| Actuation | `touch.absolute`、`pointer.relative`、`pointer.absolute`、`keyboard.key`、`text.input`、`remote.dpad`、`app.launch`、`window.focus` |
| Transport | `usb.hid`、`serial.command`、`adb`、`ble`、`network.rpc`、`hdmi.cec` |
| Diagnostics | `device.health`、`input.status`、`capture.health`、`firmware.info`、`shell.exec` |

- 每个 ID 有 `CapabilitySpec(domain, description, evidence_kind)`；`evidence_kind ∈ {contract_test, smoke, manual}`。
- **新增/删除能力**：必须更新契约 §5.3 表 + 本 ADR 的 Catalog 清单 + 测试；按契约 §9 需要 ADR（新增能力可提交一个一页的 ADR 增量，不必重写本文件）。
- `requires_pointer_precheck` **不进入 Catalog**：它是由 `pointer.relative` + 平台扩展推导出的安全策略（契约 §5.3 映射表已如此规定），只保留在兼容视图/Policy 层。

### D2 — Capability Record 与状态解析

```python
# 伪代码
@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: CapabilityId
    state: CapabilityState  # declared|available|verified|unavailable|unknown
    source: str  # "manifest:<adapter>" / "probe:<probe>" / "evidence:<id>"
    preconditions: tuple[str, ...] = ()
    observed_at: float | None = None
    reason: str | None = None
    evidence_ref: str | None = None  # 指向 ADR-008 的 evidence_id
    fingerprint: str | None = None  # 本记录适用的指纹（见 D5）
```

状态解析（Session 打开时一次性计算，之后由探测/断连更新）：

| 输入 | 结果 `state` |
|---|---|
| Adapter Manifest 未声明 | `unknown` |
| Manifest 声明，Session 未打开 | `declared` |
| 探测成功 + 无可用证据 | `available` |
| 探测成功 + 当前指纹下有 `verified` 证据 | `verified` |
| 探测成功 + 证据为 `failed`/`stale` | `available`（`reason=verification_failed` / `revalidation_required`） |
| 探测失败 / 权限缺失 / 端口打不开 | `unavailable`（`reason` 必填） |
| 设备断开、无法探测 | `unknown`（`reason=device_disconnected`） |

- 断连**不会**把历史 `verified` 删除；它只把当前 `state` 变 `unknown`。
- 证据 `failed` 只阻止 `verified`，不等于 `unavailable`。
- `unknown`/`unavailable` 必须带原因；禁止返回伪造的空 UI Tree / 假成功的动作。

### D3 — 视图语义：planning 与 execution 分离

```python
class CapabilityView(Mapping[CapabilityId, CapabilityRecord]):
    def supports(self, cid: CapabilityId, *, for_execution: bool = False) -> bool: ...
```

- `for_execution=False`（planning/composition）：`declared` / `available` / `verified` 为 True。
- `for_execution=True`（执行前检查）：只有 `available` / `verified` 为 True。
- `DeviceSession` 增加 `capability_view`（additive）；`capability_view` 是 Session 快照，不是全局注册表。
- **兼容**：`DeviceSession.capabilities` 继续返回 `CapabilitySet`，由唯一转换层从 View 推导（planning 语义），PR-2 行为不变。
- `control_for()`、`CompositeControlSink`、`reflex_tool`、`AdbActuator` 的执行判断在 **Phase 3** 切换到 `for_execution=True`；PR-2 不改这些调用点。
- 新代码禁止直接读 `CapabilitySet` 做执行决策；`CapabilitySet` 仅作为迁移期兼容视图。

### D4 — Capability Manifest（Adapter 声明）

```python
# 伪代码
@dataclass(frozen=True)
class CapabilityDeclaration:
    capability_id: CapabilityId
    preconditions: tuple[str, ...]  # e.g. "helper.attached", "uvc.frame_healthy"
    probe: str | None  # 注册的探针 ID；None 表示无需探测
    verification_test: str | None  # 产生 verified 证据的测试 ID


@dataclass(frozen=True)
class CapabilityManifest:
    adapter_id: str
    adapter_version: str
    declarations: tuple[CapabilityDeclaration, ...]
```

- Adapter 只声明 ID 与前置条件，不自带 `verified`。
- 现有 `CapabilitySet` 到 Manifest 的映射见 D6；各 Adapter 在 PR-2 补 Manifest，不依赖平台字符串。
- Profile 的 `requirements` 使用 `CapabilityId`；缺失时组合失败必须指名缺哪个能力/端点。

### D5 — Evidence Record 与 Fingerprint

**Evidence（不可变、追加式；存储与查询归 ADR-008）：**

```python
# 伪代码
@dataclass(frozen=True)
class CapabilityEvidence:
    evidence_id: str  # ULID/UUID，全局唯一
    capability_id: CapabilityId
    conclusion: Literal["verified", "failed"]
    device_id: str  # ADR-003
    endpoint_ids: tuple[str, ...]  # 适用的端点（UVC/串口等）
    fingerprint: str  # sha256(规范化 JSON)
    fingerprint_fields: Mapping[str, str]
    test_id: str  # 稳定的 Contract/Smoke 测试 ID
    test_version: str
    evidence_kind: Literal["contract_test", "smoke", "manual"]
    executed_at: float
    environment: Mapping[str, str]  # host_id、artemis 版本、OS、固件/协议、Adapter 版本
    artifact_refs: tuple[str, ...]  # 非空；Data Engine Artifact 引用（报告/日志/脱敏截图）
    operator: str | None  # 仅 manual
    notes: str | None = None
    revalidate_after: float | None = None
```

规则：

1. **只有 `contract_test` / `smoke` 能产生 `verified`**；`manual` 最多产生 `available`（并保留记录用于诊断）。这与契约 §5.3 对 `verified` 的定义一致。
2. 每条 evidence 必须绑定 `device_id + endpoint_ids + fingerprint`；**不同设备不得继承**。跨相同型号设备的证据共享是未来优化，不在本 ADR。
3. **最新结论优先**：同一 `(capability_id, device_id, endpoint_ids, fingerprint)` 的新 `failed` 证据使旧 `verified` 失效（不删除旧记录，只标记 superseded）。
4. 没有 `artifact_refs` 或 `fingerprint` 的证据不得被读取为 `verified`。
5. 证据是 append-only；修订通过新增记录 + 标记 superseded 表达，不 UPDATE 历史结论。

**Fingerprint 字段（按能力选取最小充分集）：**

| 类别 | 字段 | 适用 |
|---|---|---|
| 设备 | `platform_family`、`device_model`（未知写 `unknown`）、`os_build` | 全部 |
| Adapter | `adapter_id`、`adapter_version`（含 ARTEMIS 版本） | 全部 |
| 固件 | `firmware_build`、`protocol_version` | `usb.hid`、`serial.command`、`firmware.info`、`pointer.*`、`keyboard.key` |
| 权限/前置 | `permission.<name>`、`helper.version`、`helper.protocol` | `ui_tree.read`、`shell.exec`、`window.*` |
| 配置 | 只包含会改变能力语义的键（例如 `ios.capture_mode` 不影响能力，不纳入） | 按能力 |

- 规范化：键小写、排序、`unknown` 统一；`fingerprint = sha256(canonical_json(fields))`；同时保存 `fingerprint_fields` 供诊断。
- 失效：指纹不匹配、权限撤销、Adapter/Driver 语义变化、固件/协议变化、OS/Build 变化 → 证据 `stale`，`state` 回退为 `available`（有探测）并带 `revalidation_required`；**不得**误报 `unavailable`。
- 断连不使证据失效（只影响当前 `state`）。
- 重新验证窗口：默认 30 天（可按测试覆盖），过期 → `stale`，证据保留。

### D6 — 兼容映射（唯一转换层）

| 现有 `CapabilitySet` 字段 | 规范 ID | 兼容期说明 |
|---|---|---|
| `supports_ui_tree` | `ui_tree.read` | |
| `supports_absolute_tap` | `touch.absolute` | |
| `supports_relative_pointer` | `pointer.relative` | |
| `supports_ocr` | `ocr.read` | |
| `supports_text_input` | `text.input` | |
| `supports_shell` | `shell.exec` | |
| `supports_app_launch` | `app.launch` | **新模型不得沿用默认 True**；未声明即 `unknown` |
| `requires_pointer_precheck` | —（不是能力） | 由 `pointer.relative` + Platform Extension 推导，留在 Policy |
| — | `frame.capture` | 由 Session 在组装 Source 时补声明 |
| — | `window.*`、`remote.*`、`usb.hid` 等 | 新能力，无兼容字段 |

- 转换只允许出现在 `artemis/core/capabilities_compat.py`（PR-2 新建）；调用方不得手写布尔→ID 映射。
- 兼容视图的布尔值采用 **planning** 语义；执行语义切换在 Phase 3。

### D7 — Registry Port 与派生 Snapshot

- `CapabilityRegistry`（Port，`artemis/core/ports.py` 或同层）提供：
  - `resolve(descriptor, manifest, probe_results) -> CapabilityView`；
  - `record(evidence) -> None`（只由验证运行器调用）；
  - `lookup(capability_id, device_id, endpoint_ids, fingerprint)`。
- Registry 只维护**派生 Snapshot 与引用**，证据权威在 ADR-008 的全局证据存储；缓存可重建，缓存丢失不得导致证据丢失。
- Registry 不 import 任何 Adapter/Driver/传输库；探针通过显式注册表注入。

---

## 5. Consequences

### Positive

- 组合与执行使用不同语义，消除“声明即可用”的错误。
- `verified` 可追溯、可失效；换固件/OS/权限会自动要求重新验证。
- 新平台能力只需加 Catalog 条目 + Manifest + 测试，不改 Selector/Agent。

### Negative / trade-offs

- PR-2 增加 Catalog、View、Manifest、Evidence、Fingerprint、转换层六块代码；必须用测试锁住兼容映射的完备性。
- 需要为每个 Adapter 补 Manifest 与探针；短期内声明可能比现在更“保守”（未声明即 unknown），需要用测试/日志确认没有误伤。
- 证据的重新验证需要真实设备，无法完全 Mock。

### Follow-up work

- ADR-008：证据/Artifact 的存储、保留、查询。
- ADR-009：固件握手提供 `firmware_build`/`protocol_version` 与能力清单。
- Phase 4.2：`ios-smoke` 的六阶段产出结构化 evidence。

---

## 6. Contract and dependency impact

- **Layers**：L3/L4 共享 Catalog DTO；L2 只读 `CapabilityView`（Phase 3 起）。
- **Ports / DTOs**：`CapabilityId`、`CapabilitySpec`、`CapabilityRecord`、`CapabilityView`、`CapabilityManifest`、`CapabilityEvidence`、`fingerprint()`；`CapabilitySet` 保留为兼容视图。
- **Capability IDs**：以 §4 D1 表为准；`requires_pointer_precheck` 不进 Catalog。
- **Adapters / Profiles**：Adapter 提供 Manifest；Profile 只组合 ID 与前置条件；组合失败必须报告 `capability_unavailable`。
- **Import direction**：`artemis/core/capabilities*.py` 只依赖标准库；`devices/`、`perception/`、`control/` 可以 import core；`core` 不得 import `drivers/devices/reflex/third_party`。
- **Compatibility**：`CapabilitySet` 字段与默认值在 PR-2 不变；执行路径切换单独 PR。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-2a | Catalog + `CapabilityView` + `CapabilitySet` 兼容转换 + Manifest DTO；Android/iOS/Mock 填 Manifest | 无（只读视图） |
| PR-2b | `CapabilityEvidence`/fingerprint + Registry Port + 内存/测试实现；证据读取用于 View | 无（无证据时状态为 declared/available） |
| PR-2c | Session 打开时的探针（UVC 帧健康、串口 open、helper attach、pointer 检测器可用） | 只增加状态精度，不拒绝现有路径 |
| Phase 3 | 执行判断切到 `for_execution=True`；`control_for`/Composite/Reflex 使用 View | 有（缺能力时明确拒绝） |
| Phase 4.2 | 固件握手与 `ios-smoke` 产出 evidence；固件/协议进入指纹 | 证据从“无”变“可验证” |

**Non-goals**：跨设备证据共享、能力市场/插件、动态能力探测调度、为所有 ID 立刻实现探针。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/core/test_capability_catalog.py`（新增）：ID 唯一、域合法、`requires_pointer_precheck` 不在 Catalog、未知 ID 拒绝。
- `tests/unit/core/test_capability_view.py`（新增）：状态解析矩阵（D2 表逐行）、planning vs execution、断连不改历史、failed 阻止 verified。
- `tests/unit/core/test_capability_compat.py`（新增）：现有 8 个字段与 ID 双向映射完备；未声明 `app.launch` 不是 True；`CapabilitySet.from_view` 默认语义。
- `tests/unit/core/test_capability_fingerprint.py`（新增）：字段排序稳定、`unknown` 归一、权限变化改变指纹、断连不改变指纹。
- `tests/unit/core/test_capability_evidence.py`（新增）：evidence 必须带 artifact/fingerprint；manual 不能 verified；新 failed 使旧 verified 失效。

### Integration / hardware

- 临时目录证据存储 + 进程重启后仍能 `verified`（依赖 ADR-008 实现）。
- iOS 实机：`ios-smoke` 产出 evidence（Phase 4.2）；拔掉串口后 `state=unknown`、证据仍存在；换固件 Build 后 `stale`。

### 判据

- 无真实设备证据的能力不得出现在 `verified`；Mock 只证明状态机与存储，不证明平台能力。

---

## 9. Rollback

- 新模块 additive；回滚 = 不接线 `capability_view`/Registry，保留 `CapabilitySet` 旧路径。
- 配置开关 `ARTEMIS_CAPABILITY_VIEW=0`（PR-2 引入）让 Session 只返回旧 `CapabilitySet`。
- 证据存储回滚见 ADR-008；不涉及现有表结构、不删除旧字段。

---

## 10. Security and safety

- `verified` 是决策输入：不得由 Adapter 自报、不得伪造 artifact、不得把 manual 当自动化验证。
- 权限撤销必须立即使相关证据失效（指纹包含权限项）。
- 缺能力时返回 `capability_unavailable` 并拒绝动作，不得静默降级成另一种输入方式。
- 证据/日志不得包含凭据、Token、账号或未脱敏屏幕内容（存储规则见 ADR-008）。
- 不制造 `low_confidence`：没有数值 confidence 就记录 `unknown`。

---

## 11. Resolved / open questions

已定：

- 能力状态与证据分离；`verified` 仅由 contract/smoke 产生。
- 证据按 `device_id + endpoint_ids + fingerprint` 限定，不跨设备继承。
- `requires_pointer_precheck` 不是能力，留在 Policy。
- `app.launch` 不再默认 True。

待定（不阻塞 PR-2 骨架）：

1. 重新验证窗口默认 30 天是否合适（可按硬件/OS 分档）。
2. 探针注册表放 `core` 还是 `devices/runtime`（建议 `devices`，core 只放 DTO）。
3. 是否允许“同型号设备”共享证据（建议推迟到真实第二台设备接入后）。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | 现状 Boolean 无法表达声明/可用/已验证；契约 §5.3.1 要求证据与指纹；PR-2/Phase 2 被阻塞 |
