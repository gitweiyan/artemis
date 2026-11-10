# ADR-004: Action Intent / Command / Receipt / Verification Semantics

- **Status:** Accepted
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-4（Observation / Geometry / Action Contract）；Phase 3（执行边界）；Phase 5（Safety/Verification 收敛）
- **Related:** 契约 §5.7（Action Intent/Command/Result）、§7（Safety/Verification）；[ADR-002](ADR-002-coordinate-spaces-transform.md)（坐标引用）、[ADR-008](ADR-008-data-engine-evidence-ownership.md)（证据存储）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §5–§10、`artemis/control/base.py`、`artemis/mcp/action_types.py`、`artemis/mcp/action_executor.py`、`artemis/agents/validator/execution_loop.py`

> 状态说明：本 ADR 于 2026-11-10 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7。

---

## 1. Context and problem

### 1.1 现状：两个 `ActionResult` 和一个有歧义的 `ok`

| 类型 | 字段 | 位置 |
|---|---|---|
| `control.base.ActionResult` | `ok, action, incident, detail, metrics` | `artemis/control/base.py:12-20` |
| `mcp.action_types.ActionResult` | `ok, code, action, message, detail, metrics, incident, normalized_coordinates, duration_ms` | `artemis/mcp/action_types.py:34-71` |

`ok` 同时被当作：

- Driver/Sink 的“命令被接受/已派发”；
- Executor 的 `status = "success" if res.ok else "error"`（`mcp/action_executor.py:222`）；
- Validator 的“动作成功”判断（`agents/validator/execution_loop.py:147-150`）。

Validator 的注释已经写明这个歧义：

> “No effect polling: 'dispatched' only says the device accepted the command; whether it had the intended effect is the Operator's call.”（`execution_loop.py:145-146`）

但上层仍把 `exec_success`/`status=success` 当成功展示；`no_effect`/`target_unverified` 只在部分路径被解释（`flash/no_op.py`、`action_executor.py:249-258`）。

### 1.2 没有 action_id / attempt / 幂等语义

- `ActionResult` 没有 `action_id`、`attempt`、`receipt` 或 idempotency key（两个类型都没有）。
- Validator 本地重试最多 2 次（`execution_loop.py:138-150,217-220`），重试直接再发命令；没有任何“该动作是否可重试”的声明。
- Flash 的 `McpActionExecutor` 没有重试，但也没有 action id；Data Engine Step/Trace 无法把同一次意图的多次尝试关联起来。
- 非幂等动作（提交、支付、电源、输入文本）没有默认保护，只能靠上层零散判断。

### 1.3 Verification 分散且没有统一 Receipt

- 适配器 `incident`（`control/hid.py`、`drivers/ios/ios_uvc_hid_driver.py`）；
- Validator 的 `ValidationErrorCategory`（`agents/validator/categories.py`）；
- Flash 的 `no_effect`/dHash（`agents/flash/no_op.py`）；
- Checker 的 checkpoint/final 验收（`agents/checker/`）；
- 指针 metrics（`control/hid.py:71-103`）。

它们各自表达“失败”，但没有统一的“派发→ACK→观察效果→业务验收”记录；Check 结果与 Adapter ACK 可能被混读。

### 1.4 为什么现在必须决策

PR-4 要动 ActionResult/Geometry；ADR-002 定义 `PointRef/BoxRef` 后，Command/Receipt 必须携带坐标与 frame/transform 引用。如果先改 ActionResult 字段再定状态机，Legacy 兼容会变得不可控。

---

## 2. Decision drivers

1. **状态可区分**：派发、Adapter ACK、观察效果、业务验收是不同事实。
2. **可追溯**：一次意图的多次尝试共享 `action_id`，每次尝试有 `attempt`/`receipt`。
3. **安全重试**：非幂等动作默认不自动重试；重试必须有观察依据。
4. **兼容**：现有 `ActionResult.ok`、MCP Tool schema、Flash/Validator 行为不破坏。
5. **可验证**：ACK-only 永远不能变成 verified_success；unknown 不得伪装成功。
6. **可落库**：Receipt/Verification 可进入 Data Engine（ADR-008），不复制大对象。

---

## 3. Considered options

### Option A — 继续用 `ok` + incident

- Benefits：零改动。
- Costs / risks：ACK 与效果无法区分；重试无幂等语义；CI/回放无法判断真实成功。

### Option B — 把所有状态塞进一个巨型 ActionResult

- Benefits：一个对象。
- Costs / risks：Adapter 被迫理解业务验收；L3/L4 职责回混；字段永远只增不减。

### Option C — 分层 Intent / Command / Receipt / Verification + 兼容 ActionResult（推荐）

- Benefits：每层只写自己知道的事实；Receipt 可审计；旧 `ok` 明确为 accepted。
- Costs / risks：新增 DTO/转换；迁移期两套结果并存，需要严格测试。

### Option D — 事件溯源（Event Store）为唯一入口

- Benefits：完整历史。
- Costs / risks：超出 Phase 1 范围；Data Engine 已记录 step/trace，先做 DTO/Receipt 足够。

**选择：Option C。**

---

## 4. Decision (accepted)

### D1 — Action DTO（`artemis/core/actions.py`，标准库）

```python
class ActionState(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    DISPATCHED = "dispatched"
    ADAPTER_ACKNOWLEDGED = "adapter_acknowledged"
    OBSERVED_EFFECT = "observed_effect"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


class Idempotency(StrEnum):
    SAFE = "safe"  # retry allowed after transport/observation
    CONDITIONAL = "conditional"  # only with observation proving no effect
    UNSAFE = "unsafe"  # no automatic retry; new intent/approval required


@dataclass(frozen=True)
class ActionIntent:
    intent_id: str
    kind: str  # semantic action (tap, back, launch, key, swipe...)
    target: PointRef | BoxRef | None
    parameters: Mapping[str, Any]
    idempotency: Idempotency = Idempotency.UNSAFE
    risk: str = "normal"  # normal / sensitive (policy input)


@dataclass(frozen=True)
class ActionCommand:
    command_id: str
    intent_id: str
    attempt: int  # 1-based
    adapter_id: str
    parameters: Mapping[str, Any]  # device-pixel/pointer-frame payload
    target_ref: PointRef | BoxRef | None
    frame_id: str | None
    transform_id: str | None  # ADR-002 TransformRecord identity (content hash)


@dataclass(frozen=True)
class ActionReceipt:
    action_id: str
    intent_id: str
    command_id: str
    attempt: int
    adapter_id: str
    state: ActionState  # dispatched / adapter_acknowledged / rejected / cancelled / ...
    dispatched_at: float
    acknowledged_at: float | None = None
    incident: str | None = None
    metrics: Mapping[str, float | str] = field(default_factory=dict)
    retry_of: str | None = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationResult:
    action_id: str
    status: Literal["success", "failure", "unknown"]
    observed_at: float
    reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None  # null when the source has none
```

规则：

1. DTO 只表达事实，不持有句柄/Agent 状态。
2. `action_id` 在首次派发前生成；同一意图的所有 attempt 共享 `action_id`，`attempt` 递增，`retry_of` 指向上一 receipt。
3. `ActionCommand.parameters` 是 Adapter 空间（device pixel/pointer frame），必须带 `target_ref`/`frame_id`/`transform_id`（ADR-002）。
4. `VerificationResult` 由 Checker/Validator 写；Adapter 不得写 `success`。

### D2 — 状态机与写入者

| 状态 | 写入者 | 证据 |
|---|---|---|
| `proposed` | Agent/Executor | Intent |
| `validated` | Safety/Validator | precondition/geometry/capability 检查 |
| `dispatched` | Executor/Adapter | 命令已提交 |
| `adapter_acknowledged` | Adapter | Controller/固件 ACK（不表示 UI） |
| `observed_effect` | Observation | 新帧/树/焦点变化 |
| `verified_success` / `verified_failure` | Checker/Validator | 业务/任务验收 |
| `unknown` | 任意 | 证据不足；不得当成功 |
| `rejected` | Safety/Executor | 能力/坐标/权限拒绝 |
| `cancelled` / `deadline_exceeded` | Executor | 取消/超时 |

- 状态只能前进或进入终态；禁止从 `verified_*` 回退到 `dispatched`（新 attempt 才建新 receipt）。
- `adapter_acknowledged` 不是 `verified_success` 的同义词；`ActionResult.ok` 只允许映射到前两者（D4）。

### D3 — 重试与幂等

- 每个 ActionSpec 必须声明 `Idempotency`（默认 `UNSAFE`；未声明即不安全）。
- 建议分类（PR-4 在 action manifest 中落地）：

| 动作 | 默认 | 说明 |
|---|---|---|
| `swipe` / `scroll` / `wait` | `SAFE` | 重复执行通常无副作用，但仍要观察 |
| `tap` / `long_press` / `press_key` | `CONDITIONAL` | 仅当观察证明无效果且目标仍有效才可重试 |
| `input_text` / `clear_text` | `UNSAFE`（除非显式 clear） | 重复输入会叠加/覆盖 |
| `manage_app` / launch | `CONDITIONAL` | 需观察当前前台/页面 |
| 支付/提交/电源/切换输入源 | `UNSAFE` | 禁止自动重试，需新的 Intent/人审 |

- 非幂等动作失败后不得切换 Adapter 重放（契约 §5.2）。
- 重试前必须有 Observation 或 Receipt 证据；`retry_of` 记录链路；最大尝试次数由调用方策略决定。

### D4 — 兼容 `ActionResult`

- `control.base.ActionResult.ok` 语义固定为 **“所选 Adapter 接受/派发”**（契约 §5.7 已如此规定），不得解释为 UI/业务成功。
- `control.base.ActionResult` additive 字段：`action_id`, `attempt`, `state`（可选）；旧调用继续只读 `ok`/`incident`/`metrics`。
- `mcp.action_types.ActionResult` 增加可选 `action_id`/`attempt`/`state`；`ToolExecutionResult.metadata` 写入 `action_id`、`state`、`receipt` 摘要；MCP tool schema 不变。
- `status="success"` 在 Flash/执行器层只表示“命令被接受”，UI/验收仍由 Observation/Checker 给出；`no_effect`/`target_unverified` 继续作为 receipt 的 incident/evidence。
- 兼容期允许不产生完整 Receipt；但 Phase 3 起执行路径必须写 `action_id` 与 `state`。

### D5 — Verification 与证据

- Checker/Validator 写 `VerificationResult`；`success` 必须有 evidence_refs（frame/step/assert 结果），`unknown` 不得计成功。
- `confidence=None` 表示来源未提供；不生成 `low_confidence`（契约 §7.2）。
- Adapter ACK、`no_effect`、`pointer_*` 都是 receipt/incident 事实，不是业务验收。
- Verification 与 Receipt 通过 `action_id` 关联；Data Engine 记录引用（ADR-008）。

### D6 — Capability/坐标前置

- 执行前必须确认所需 Capability 为 `available/verified`（ADR-001）；否则 `rejected` + `capability_unavailable`。
- 坐标必须带 `PointRef/BoxRef` 与 frame/transform（ADR-002）；缺失/过期 → `rejected` + `coordinate_space_mismatch`/`stale_frame`。

---

## 5. Consequences

### Positive

- “ACK 成功”与“任务成功”在类型和状态上彻底分开。
- 每次尝试可审计、可回放；非幂等动作有明确重试边界。
- Checker/Validator 的验收可以统一成 `VerificationResult`，减少 incident 漂移。

### Negative / trade-offs

- 迁移期 `ActionResult` 与 Receipt 并存；Adapter 需要映射层。
- 现有重试路径（Validator 本地 2 次）必须改造成带 `attempt/retry_of` 的语义。
- 需要 ActionSpec 声明幂等性；遗漏默认 `UNSAFE`，可能降低自动恢复率（这是安全选择）。

### Follow-up work

- PR-4：DTO + 兼容映射 + Validator/Checker 写入。
- Phase 3：执行路径统一 Receipt；MCP metadata。
- Phase 5：Safety/Verification 收敛，`unknown` 处理统一。

---

## 6. Contract and dependency impact

- **Layers**：L3 定义 Intent/Verification；L4 Adapter 产生 Receipt；L2 只读状态与证据。
- **Ports / DTOs**：新增 `ActionIntent`、`ActionCommand`、`ActionReceipt`、`VerificationResult`、`ActionState`、`Idempotency`；`ActionResult` additive 字段。
- **Incident**：继续使用契约 §7.2 既有集合（`capability_unavailable`、`stale_frame`、`coordinate_space_mismatch`、`no_effect` 等），不新增同义状态名。
- **Import direction**：`core/actions.py` 只依赖标准库 + `core/coordinates.py`；Adapter 不 import Checker；core 不 import 设备实现。
- **Compatibility**：MCP Tool schema、`ActionResult.ok`、Flash/Validator 现有行为保持；新字段可选。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-4a | `core/actions.py` DTO + 状态机/幂等测试；无接线 | 无 |
| PR-4b | `ActionResult`/MCP 类型 additive 映射；Executor 填 `action_id`/`state` | 无（只多字段） |
| PR-4c | Validator 本地重试加 `attempt`/`retry_of`；非幂等动作默认不重试 | 有（安全收紧） |
| PR-4d | Checker/Validator 写 `VerificationResult`；Data Engine 关联 | 有（验收可追溯） |
| Phase 3 | 执行链统一 Receipt；旧 `ok` 仅作兼容 | 有 |
| Phase 5 | 与 Safety/Incident 统一 | 有 |

**Non-goals**：重写 MCP Tool schema；实现事件溯源；改变现有动作工具名/参数。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/core/test_actions.py`（新增）：
  - `adapter_acknowledged` 不得映射为 `verified_success`；
  - `unknown` 不计成功；
  - `UNSAFE` 动作在无观察时拒绝重试；`CONDITIONAL` 需要 no-effect 证据；
  - `attempt`/`retry_of`/`action_id` 关联；
  - 取消/超时进入 `cancelled`/`deadline_exceeded`。
- `tests/unit/mcp/test_action_*`：MCP 结果仍返回原字段；metadata 增加 state/action_id；schema 不变。
- `tests/unit/agents/validator`：本地重试生成 attempt；非幂等不重试；incident 映射不变。
- `tests/unit/agents/checker`：`VerificationResult` success 必带 evidence；failure/unknown 不误报。
- `tests/unit/data_engine`：step/trace 记录 action_id/verification refs，不复制截图。

### Integration / hardware

- iOS 真机：一次成功点击 = dispatched→ack→observed_effect→verified_success；一次 `no_effect` 不得记 verified。
- 取消/断连：receipt 为 cancelled/unknown，输入释放。
- 非幂等动作（如提交）在测试设备上只做 mock/受控验证，不触发真实支付。

### Observability

- 日志/Trace：`action_id`、`attempt`、`state`、`adapter_id`、`incident`、`evidence_refs`；不记录凭据。

---

## 9. Rollback

- DTO/映射 additive；开关 `ARTEMIS_ACTION_RECEIPTS=0`（PR-4 引入）关闭新字段写入，保留旧 `ActionResult` 行为。
- 不修改 API/schema/历史数据；删除新模块不影响 Flash/Validator。

---

## 10. Security and safety

- 非幂等动作默认 `UNSAFE`，禁止盲目重试/切换 Adapter 重放。
- 取消/超时/断连必须释放按下的键、按钮、触点（ADR-003）。
- `VerificationResult.success` 必须有证据；`unknown` 不得当成功，避免“命令成功=任务成功”。
- 敏感动作（支付/电源/权限）不得由自动重试绕过人工策略。

---

## 11. Resolved / open questions

已定：

- 分层 DTO；`ActionResult.ok` 只表示 Adapter 接受/派发。
- `action_id` 在首次派发前生成；attempt 递增。
- 未声明幂等的动作默认 `UNSAFE`。
- Verification 只能由 Checker/Validator 写，success 必须有 evidence。

2026-11-10 Owner 确认：

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | 幂等分类由谁维护 | ActionSpec/Manifest 声明；未声明默认 `UNSAFE` | PR-4c |
| 2 | `action_id` 生成方 | Executor 生成并在首次派发前写入；ActionSession 只透传 | PR-4b |
| 3 | Validator 本地重试上限 | 保持 2 次；Phase 5 再评估 | Phase 5 |
| 4 | Receipt 落库粒度 | Step 存引用 + 摘要；完整 receipt 进 Trace payload/Artifact | PR-4d |

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | `ok` 混淆派发/成功；无 action_id/幂等/统一 Verification；PR-4 与 ADR-002 需要 Action 边界 |
| 2026-11-10 | Accepted | 评审通过；确认 ActionSpec 声明幂等、Executor 生成 action_id、重试上限 2、Receipt 摘要+Artifact | 四项剩余细节由 Owner 确认；PR-4 可进入实现 |
