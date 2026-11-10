# ADR-007: Reflex Runtime, SafetyNet 与高频闭环安全预算

- **Status:** Accepted
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 3（Reflex 边界）；Phase 5（SafetyNet/验证语义）
- **Related:** 契约 §7（Safety/Verification）、§8（数据所有权）；[ADR-004](ADR-004-action-intent-command-receipt.md)（Accepted）、[ADR-008](ADR-008-data-engine-evidence-ownership.md)（Accepted）、[ADR-010](ADR-010-platform-extensions-semantic-actions.md)（Accepted）
- **Code evidence:** `artemis/reflex/runtime.py:1-35,68-201`、`artemis/reflex/contract.py:15-35,100-190`、`artemis/reflex/mouse.py:215-278,680-970`、`artemis/safety/pointer.py:1-75`、`artemis/safety/pixel.py:1-125`、`tests/unit/reflex/test_reflex_core.py:215-320`

> 状态说明：本 ADR 于 2026-11-10 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7。

---

## 1. Context and problem

### 1.1 仓库里有两个安全闭环，但 Reflex 的边界还没有被写成契约

契约 §7.1 已明确区分：

- **任务式交互（Flash/Pro）**：动作前目标/Bounds/Frame 新鲜度/Pointer 与 Target 一致性/能力风险检查；动作后屏幕或树变化；Checker 做任务级验收。
- **高频 Reflex Skill**：每帧解析失败计数、Blind Frame 上限、动作派发失败计数、最大 FPS、最大运行时间、取消/停止、危险输入释放；不默认逐帧执行重量级 Vision/LLM/Trace/Checker。

当前 `ReflexRuntime` 已实现其中一部分：

- `DEFAULT_MAX_BLIND_FRAMES = 15`、`DEFAULT_MAX_CONTROL_FAILURES = 2`、`DEFAULT_FPS_CAP = 10.0`（`runtime.py:30-35`）；
- `seconds` 作为运行时间预算，`should_stop` 支持取消（`runtime.py:91-125`）；
- `StopReason` 区分 `SKILL_DONE`/`TIME_BUDGET`/`CANCELLED`/`LOST`/`CONTROL_LOST`/`SKILL_ERROR`（`contract.py:15-35`）；
- `ReflexResult` 输出 frames/actions/fps/blind/control failures/perf 分位数（`contract.py:100-160`）。

但仍有可见缺口：

1. **输入释放没有进入 Runtime 契约**：`runtime.run()` 的 `finally` 只调用 `skill.stopped()` 和 `capture.close()`（`runtime.py:190-201`），没有调用 control 的按键/按钮释放。实际 `Esp32HidControl` 有 `release_all()`（`control.py:627`），但 `PointerControl`、`HelperControl` 等路径没有统一保证；取消或异常时可能留下按下状态。
2. **预算分散且不可审计**：FPS/blind/control 在 Runtime；`max_attempts`/`max_steps`/`max_frame_age_s` 在 `SessionTapController`（`mouse.py:230-276`）；`seconds` 由 CLI/tool 传入。没有一个可传入、可记录、可测试的预算对象。
3. **StopReason 不是 Incident**：`LOST`、`CONTROL_LOST` 无法区分采集断连、采集超时、解析失败还是设备断连；跨层消费方只能读自然语言。契约 §7.2 要求跨层 Incident 使用唯一 canonical 表，而 Reflex 当前没有稳定的 `incident` 字段。
4. **deadline 不是可抢占的**：循环只在帧之间检查 `seconds`；一次阻塞的 capture/control 可以超出 deadline。契约没有说明 Reflex 的 deadline 是“观测式”还是“强制式中断”。
5. **安全预算容易再次和任务式 SafetyGate 混用**：Phase 5 会引入统一 `ActionGate`/`SafetyPolicy`；如果不先划清 Reflex 的预算边界，后续很容易把慢速 Checker、逐帧 LLM 或重量级 SafetyNet 塞进 5–10 Hz 循环。

### 1.2 为什么现在必须决策

Phase 5 要统一 Incident 和动作前/后验证语义。Reflex 是独立执行模式，不能为了“统一”而牺牲确定性、延迟或输入释放。必须先定义：

- 哪些检查属于 Reflex 的固定预算；
- 哪些检查只在任务式动作上运行；
- 取消/超时/断连时由谁释放输入；
- Reflex 如何用 canonical Incident 报告“为什么停”；
- 哪些数据逐帧保留、哪些只保留摘要。

---

## 2. Decision drivers

1. **确定性优先**：同一帧序列、同一预算应产生可复现的决策，不依赖网络、LLM 或数据库写入。
2. **延迟可预算**：每帧的额外开销必须可估算；不能把任务式 Checker 塞进高频循环。
3. **失败可行动**：停止原因必须能区分“没画面”“画面读不懂”“控制失败”“用户取消”“时间到”。
4. **不卡输入**：取消、异常、断连和正常结束都必须释放按键/鼠标按钮/触点。
5. **兼容现有 Skill**：现有 `run_reflex` 调用、Skill API 和默认预算不能因本 ADR 失效。
6. **跨层 Incident 一致**：Reflex 对外只使用契约 §7.2 的 canonical 值。
7. **可观测但不逐帧写库**：一次 Reflex session 只落一份摘要/证据，不产生逐帧 DB 行或图片。
8. **可测试**：Fake Capture/Control 能覆盖预算、取消、释放和 Incident 映射，无设备即可回归。

---

## 3. Considered options

### Option A — 每帧复用任务式 SafetyGate/Checker

- Benefits：只有一套安全逻辑，概念统一。
- Costs / risks：逐帧 XML/Pixel/LLM/DB 检查会把 5–10 Hz 循环拖垮；Reflex 的确定性被破坏；与契约 §7.1 明确冲突。**拒绝。**

### Option B — 保持现状，只加文档

- Benefits：零行为变化。
- Costs / risks：输入释放和 deadline 仍是隐式约定；StopReason 无法映射 Incident；Phase 5 会继续各自实现预算。**不足。**

### Option C — 独立 `ReflexBudget` + 轻量 `ReflexGuard`，共享 Incident 词汇（推荐）

- Benefits：保留高频确定性；预算显式、可记录、可测试；与任务式 `ActionGate` 只共享 Incident 和底层 Safety 原语，不共享执行频率。
- Costs / risks：多一个预算 DTO 和释放协议；需要迁移现有默认值和测试。

**选择：Option C。**

---

## 4. Proposed decision

### D1 — 两种执行模式，两个安全入口

| 模式 | 入口 | 频率 | 允许的检查 |
|---|---|---|---|
| Task（Flash/Pro） | `ActionGate`/`SafetyPolicy`（Phase 5） | 每动作/每步 | 目标/Bounds/Frame/Pointer/能力/权限/Checkpoint/Checker |
| Reflex | `ReflexGuard` + `ReflexBudget`（本 ADR） | 每帧/每动作，5–10 Hz | 预算计数、Frame 健康/年龄、Pointer 一致性（仅需要时）、控制失败、输入释放 |

**规则：**

1. Reflex 不得默认调用 LLM、完整 Checker、逐帧 Data Engine/Trace 写入。
2. Reflex 可以使用 `safety/pointer.py`、`safety/pixel.py` 的纯函数做**有界**校验；不得在每帧运行重量级 Vision 模型或网络定位，除非 Skill 自己显式选择并计入预算。
3. 任务式动作仍必须先通过 `ActionGate`；Reflex 不因“统一”而跳过自己的预算。
4. 两种模式对外报告同一套 canonical Incident（§7.2）。

### D2 — `ReflexBudget` 是显式、可记录的预算

新增冻结 DTO（位置在 `artemis/reflex/contract.py` 或 `artemis/core`，最终以不引入循环依赖为准）：

```python
@dataclass(frozen=True)
class ReflexBudget:
    max_seconds: float = 60.0
    fps_cap: float = 10.0
    max_blind_frames: int = 15
    max_control_failures: int = 2
    max_frame_age_s: float = 3.0
    max_action_attempts: int = 3
    max_move_steps: int = 6
    operation_timeout_s: float = 10.0
```

规则：

- 所有值必须 `> 0`；`max_seconds` 必须有有限上界，禁止无限循环。
- 默认值保持当前行为：`60s`、`10 FPS`、`15 blind`、`2 consecutive control failures`、`3 attempts`、`6 steps`、`3s frame age`。
- 调用方可以传入更小预算；是否允许提高由 CLI/Profile 的显式配置决定，不能静默无限提高。
- `run_reflex`/`ReflexRuntime.run` 增加可选 `budget` 参数；旧参数（`seconds`、`fps_cap`、`should_stop`）保留兼容，构造预算时合并。
- 预算快照写入 `ReflexResult.budget`，便于 Trace/复盘重建。

### D3 — `StopReason` 是会话结果，`incident` 才是跨层事实

保留现有 `StopReason` 枚举（兼容），新增：

```python
ReflexResult.incident: str | None       # canonical §7.2 value, or None
ReflexResult.incidents: tuple[str, ...] # bounded, de-duplicated observed incidents
```

映射规则：

| StopReason / 原因 | canonical incident | 说明 |
|---|---|---|
| `SKILL_DONE` | `None` | 正常完成，不是失败 |
| `TIME_BUDGET` | `deadline_exceeded` | 预算耗尽；不算安全失败，但可行动 |
| `CANCELLED` | `cancelled` | 上层/用户取消 |
| `LOST` + CaptureError | 取 `CaptureError.incident`；缺省 `capture_timeout` | `uvc_no_signal`/`decode_error`/`device_disconnected` 由捕获适配器提供 |
| `LOST` + 持续解析失败 | `target_unverified` | 不是 `target_disappeared`：没有足够证据证明目标消失 |
| `CONTROL_LOST` | `transport_error` 或 `device_disconnected` | 由 Control 适配器提供更具体的 incident；缺省 `transport_error` |
| `SKILL_ERROR` | `None` | Skill 自身缺陷；`skill_error` 文本保留，不伪装成设备安全事件 |

- `CaptureError` 增加可选 `incident`（只能取 §7.2 canonical）；没有时 Runtime 使用保守缺省。
- Control 适配器如需报告 `device_disconnected`/`command_unsupported`，通过结构化异常或结果携带，不允许 Runtime 解析自然语言。
- 新增 canonical 取值必须先改契约 §7.2，再改实现（本 ADR 不新增取值）。

### D4 — 输入释放是 Runtime 的 finally 义务

扩展 `Control` 协议为可选释放接口（兼容旧实现）：

```python
class Control(Protocol):
    def dispatch(self, gesture: Gesture) -> float: ...
    # optional:
    def release_inputs(self) -> None: ...
```

规则：

1. `ReflexRuntime.run()` 的 `finally` 顺序：`skill.stopped()` → `control.release_inputs()`（若存在）→ `capture.close()`。
2. `release_inputs()` 必须是 best-effort、幂等、不可抛出的异常穿透；实现应释放所有按下的键、鼠标按钮、触点和持续动作。
3. `Esp32HidControl.release_all()` 通过适配器暴露为 `release_inputs()`；`PointerControl` 委托底层 control；`HelperControl`/`AdbInputControl` 无持续按下状态时可为空实现。
4. 取消、deadline、控制失败、Skill 异常、正常完成、设备断连都必须走同一 finally 路径。
5. 不把 `capture.close()` 或 `control.close()` 放进 Reflex Runtime：它们的所有权属于 Session/Profile（ADR-003），Runtime 只释放输入，不关闭共享设备句柄。

### D5 — deadline 与取消的语义

1. `should_stop()` 在每帧开始时检查；Reflex 结果使用 `CANCELLED`/`cancelled`。
2. `max_seconds` 是**观测式 deadline**：Runtime 在每次 capture/action 返回后检查是否超时；不能抢占阻塞中的系统调用。
3. 因此所有 Capture/Control 适配器必须有自己的有限操作超时（`operation_timeout_s` 或适配器内部超时）；不允许无限阻塞。
4. 超时后不得自动重发非幂等动作（点击/按键/电源）；只允许 resync/停止/释放。
5. 取消延迟的上界 = 当前操作超时 + 释放时间；该上界应在 Trace 预算中可见。

### D6 — FPS、blind 和连续控制失败

- `fps_cap` 是上限而不是目标；循环用 `sleep_remaining` 限制，但 capture/action 本身更慢时不补帧。
- `blind_frames` 是**连续**无法解析帧的计数；成功解析后归零；达到上限停止 `LOST`。
- `control_failures` 同时记录总数和连续数；连续失败达到 `max_control_failures` 停止 `CONTROL_LOST`，成功一次归零。
- 解析失败和 capture 失败都计入 blind，但 `incident` 不同（见 D3）。
- 不允许 `while True` 无预算循环；Skill 自己的内部循环也必须受 `max_seconds`/取消约束。

### D7 — 逐帧数据与 Trace

1. 默认不在 Reflex 帧循环写 Data Engine/Telemetry 每帧记录；帧只留在内存。
2. 一次 session 只记录一条摘要，至少包含：
   `skill`、`budget`、`stop_reason`、`incident`、`seconds`、`frames`、`actions`、`fps`、`blind_frames`、`control_failures`、capture/action p50/p95、`skill_error`、counters。
3. 可选调试采样（例如每 N 帧保存一帧）必须显式开启，且不能改变 Skill 决策；生产默认关闭。
4. Reflex 的摘要可以关联 Task/Step/Trace ID，但 Reflex 不拥有这些 ID 的创建权；关联字段由调用方注入（ADR-008）。
5. Reflex 本身不产生 `verified` capability evidence；真实能力验证仍由 smoke/contract test 写入。

### D8 — Skill 的职责边界

- Skill 只做 `parse`/`decide`/`to_gestures`，不直接打开设备句柄、不写数据库、不调用 LLM。
- Skill 的 `parse` 返回 falsy 表示“这帧不可用”，Runtime 计入 blind；Skill 不得用异常控制正常状态。
- Skill 需要额外安全判断时，只能调用 `safety/` 的纯原语或显式返回 `Action` 的 `note`/计数；不得绕过 Runtime 直接点击。
- Skill 不拥有输入寿命；释放仍由 Runtime 的 finally 负责。

### D9 — 兼容与默认行为

- 现有 `run_reflex(skill, capture=..., control=..., seconds=..., fps_cap=..., should_stop=...)` 签名保持可用。
- 新的 `budget=` 参数是附加的；未传时使用当前默认值，行为与今天一致（除新增的输入释放）。
- 没有 `release_inputs()` 的旧 Control 不应崩溃；Runtime 跳过并记录一次 debug（不是安全成功）。
- `StopReason` 值不变；新增 `incident` 字段为向后兼容的附加字段。

### D10 — 非目标

- 不把 Reflex 改成异步/多线程抢占式运行时。
- 不引入硬实时保证；不承诺在阻塞系统调用中立即取消。
- 不在本 ADR 修改 Skill 算法、指针校准、固件协议或任务式 Checker。
- 不删除现有 `reflex` capture/control 兼容入口；Phase 9-A 再清理。
- 不新增 canonical Incident 取值；需要时先改契约 §7.2。

---

## 5. Consequences

### Positive

- Reflex 的停止原因可被机器消费，Phase 5 的 Incident 统一有明确边界。
- 取消/异常/断连的输入释放有唯一出口，降低卡键和持续触摸风险。
- 预算可记录、可回放、可测试，避免“跑多久、为什么停”只能看日志。
- 不会为了统一 SafetyNet 而把 LLM/Checker/DB 放进高频循环。

### Negative / trade-offs

- 需要给 Control 增加可选释放方法并为各实现接线。
- `CaptureError`/Control 错误需要携带结构化 incident，短期要改适配器。
- 观测式 deadline 不能保证硬取消；阻塞适配器仍可能超过预算。
- `StopReason` 与 `incident` 两个字段增加少量模型复杂度。

### Follow-up work

- Phase 5：实现 `ReflexBudget`/`ReflexGuard`/`release_inputs`，并与任务式 `ActionGate` 共享 Incident 枚举。
- 为 `CaptureError`/Control 错误补 canonical incident。
- 更新 CLI/`play_reflex` 的预算参数和结果渲染。
- 为真实 iOS/Android 各跑一次 smoke + 一次短 Reflex 会话，记录输入释放证据。

---

## 6. Contract and dependency impact

- **Layers / domains：** L3 Safety 原语与 Reflex Runtime；不改变 L4 设备协议。
- **Ports / DTOs：** `ReflexBudget`、`Control.release_inputs`（可选）、`ReflexResult.incident/incidents/budget`、`CaptureError.incident`。
- **Capability IDs：** 不新增；Reflex 的能力仍由 Profile/Adapter 声明。
- **Adapters / Profiles：** `Esp32HidControl`、`PointerControl`、`HelperControl`、`AdbInputControl`、Capture adapters 只需实现可选释放/结构化错误；不改变 Profile 选择。
- **Import direction：** Reflex Runtime 不 import Agent/Graph/MCP/Data Engine；Safety 纯函数可被 Runtime 使用；Runtime 不拥有 Session/Lease。
- **Compatibility facade / public API：** `run_reflex` 旧参数保留；`StopReason` 不变；新增字段可忽略；旧 Control 可运行但会记录缺少释放接口。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| 5a | 新增 `ReflexBudget` + `ReflexResult.budget/incident(s)`，默认值等价当前实现；Fake 测试覆盖预算与映射 | 无（除结果多字段） |
| 5b | `Control.release_inputs` 可选协议；`Esp32HidControl`/`PointerControl` 接线；Runtime finally 调用 | 取消/异常时释放输入（预期修复） |
| 5c | `CaptureError.incident` + Control 结构化 incident；`LOST`/`CONTROL_LOST` 映射 canonical | 错误报告更精确 |
| 5d | CLI/`play_reflex` 传入/展示预算；Trace 摘要记录 budget/incident | 可观测性增强 |
| 5e | iOS/Android 短 Reflex 真机回归 + 取消/断连释放测试 | 硬件证据 |

**Non-goals：** 不把 Reflex 改成任务式 Checker；不修改 Skill 算法；不合并 Data Engine 与 Telemetry。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/reflex/test_reflex_core.py`：
  - 预算默认值与非法值；
  - `TIME_BUDGET` → `deadline_exceeded`；
  - `CANCELLED` → `cancelled`；
  - `LOST`（capture vs parse）→ 不同 incident；
  - `CONTROL_LOST` → `transport_error`/`device_disconnected`；
  - `SKILL_ERROR` → `incident=None`；
  - `Control.release_inputs` 在正常/异常/取消/超时路径都被调用一次；
  - 旧 Control 无 `release_inputs` 不崩溃。
- `tests/unit/reflex/test_mouse_controller.py` / `test_pointer_session.py`：
  - `max_attempts`/`max_steps`/`max_frame_age_s` 不再无限重试；
  - 失败后指针/按钮释放。
- Capture/Control adapter tests：
  - 超时、断连、resync、释放幂等。

### Integration / hardware

- iOS：`ios-smoke` 四段通过后跑一个短 Reflex skill；取消运行并确认无按键/按钮残留。
- Android：同等短 Reflex + 取消/断连测试。
- 证据：Trace 摘要包含 budget/stop_reason/incident/counters；不保存逐帧图像。

### Observability fields

`skill`、`budget`、`stop_reason`、`incident`、`incidents`、`seconds`、`frames`、`actions`、`fps`、`blind_frames`、`control_failures`、capture/action p50/p95、`skill_error`、counters、session/task correlation（由调用方提供）。

### Marking capabilities `verified`

Reflex 运行本身不写 capability evidence；只有 smoke/contract test 通过并带 fingerprint 才能标记 `verified`。

---

## 9. Rollback

- `ReflexBudget`/`incident` 为附加字段；回滚时旧调用方忽略即可。
- `release_inputs` 为可选方法；移除调用即可恢复旧行为，但会重新暴露卡键风险，因此不作为默认回滚。
- 若新预算导致 Skill 行为回归，可保留 `budget=None` 走旧默认值，并单独回滚预算接线；不得删除释放测试。
- 不改变固件协议、Session/Lease 或 Data Engine schema，无需迁移数据库。

---

## 10. Security and safety

- 不记录凭据、完整屏幕内容或按键内容到 Reflex 摘要；只记录计数和结构化 incident。
- 非幂等动作（点击/按键/电源）超时后不自动重发；只 resync/停止/释放。
- 取消、断连、异常退出必须尝试释放所有按下输入；释放失败要记录但不掩盖原始错误。
- Reflex 不能绕过 Capability/Profile 的权限判断；无能力时返回 `capability_unavailable`，不猜测。
- `should_stop` 的取消请求不能被 Skill 吞掉；Runtime 在每帧边界检查。
- 阻塞操作的上界由适配器超时提供；无超时的适配器不得进入 Reflex 生产路径。

---

## 11. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial proposal | 为 Phase 5 定义 Reflex 独立安全预算、Incident 与输入释放 |
| 2026-11-10 | Accepted | 评审确认默认预算、incident 字段、可选 release_inputs、LOST 映射与 SKILL_ERROR=None | Phase 5 解锁 |
