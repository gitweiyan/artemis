# ADR-008: Data Engine / Telemetry / Runtime Trace 数据权威与 Capability Evidence 归属

- **Status:** Proposed
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-2（证据存储与查询）；Phase 6（数据/遥测/回放职责收敛）
- **Related:** 契约 §8（数据所有权）、§5.3.1（Evidence）；[ADR-001](ADR-001-capability-model-evidence-fingerprint.md)（能力/证据/指纹定义）、[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted，`device_id`/`endpoint_id`）、ADR-004（Action 状态）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §10、`artemis/data_engine/`、`artemis/telemetry/`、`artemis/runtime/trace_store.py`

> 状态说明：Proposed。批准前只允许盘点、原型和测试设计；不得合入改变公共契约的实现。

---

## 1. Context and problem

### 1.1 现状事实（已核对源码）

**Data Engine 已经是事实上的执行证据库。**

- SQLite 表：`sessions`、`images`、`steps`、`traces`、`failed_outputs`、`background_tasks`、`history_chunks`、`video_recordings`（`artemis/data_engine/storage.py:106-243`）。
- 图片按内容寻址：`sha256(image_bytes)` 作为 `image_name`，文件写在 `global_base_dir/images/<hash>.jpg`（`engine.py:811-852`）。
- `record_trace()` 写 `traces` 表，payload 记录 action/tool 的输入输出（`engine.py:1009-1059`），写入通过 `_run_in_background` **异步**执行。
- 默认数据库是全局 `DATA_ENGINE_DB_PATH`；一旦传 `--traces-path`，DB 与图片会切到该次运行的目录（`engine.py:399-407`）。
- 读写分离已有先例：`StorageManager(read_only=True)` 支持只读打开（`storage.py:67-92`），MCP 离线查看复用该能力（`tests/unit/data_engine/test_offline_reader.py`）。

**Telemetry 目前是“未接线的库”，不是数据源。**

- `TelemetryTracer` 把 span 追加到 `traces/<trace_id>/telemetry_spans.jsonl`（`artemis/telemetry/tracer.py:34-56`）。
- 生产代码**没有任何** `TelemetryTracer(...)` 实例化或 `from artemis.telemetry` 的调用（仅包内自引用与 `tests/unit/test_telemetry.py`）。
- 因此当前所有 latency/token/调用链事实要么在 Data Engine `traces.payload`，要么在日志里；Telemetry 不能被称为权威。

**Runtime Status Store 只负责进程状态。**

- `traces/<trace_id>/status.json` 由多个独立进程并发读写，采用原子替换 + PID-aware 文件锁（`artemis/runtime/trace_store.py:1-40,196-229`）。
- 它不记录动作证据，也不应成为 Replay 数据源。

**Memory 的持久化落在 Data Engine。**

- History Chunk 由 `memory/chunking.py` 计算并写回 Data Engine（`memory/chunking.py:1837-1842`）；Memory 负责渲染/检索视图，不是原始事实的权威。

**Capability Evidence 完全不存在。**

- 没有 evidence 表、没有 artifact 目录、没有跨 session 的验证记录（详见 ADR-001 §1.1）。
- 一旦把 evidence 写进“每次运行一个”的 `--traces-path` DB，跨运行 `verified` 会随目录切换丢失；这是本 ADR 必须解决的核心问题。

**现有写入模型不适合“已验证”这种决策事实。**

- `record_trace`/`record_step` 异步落盘；进程在写入前退出会丢记录。
- `record_trace` 要求存在 active session（`engine.py:1021-1022` 抛 `ValueError`）；`ios-smoke`、Contract Test 没有 Artemis 任务 session，无法复用该 API。

### 1.2 为什么现在必须决策

ADR-001 定义了证据“长什么样”，但没有回答“存在哪里、谁写、保留多久、如何查询、与 telemetry/status 如何区分”。PR-2 需要存储与查询；Phase 6 需要收敛三个相邻模块。如果不先定权威边界，会出现“Data Engine 一份、Telemetry 一份、Registry 一份”的三写冲突。

---

## 2. Decision drivers

1. **单一权威**：同一事实只能有一个权威存储；其它模块只能引用，不得复制结论。
2. **跨运行**：`verified` 必须跨 session/`--traces-path` 存活；执行证据则按运行归档。
3. **可审计**：证据能回答“哪次测试、什么环境、什么指纹、产出什么 artifact”。
4. **不可变**：证据 append-only，失败/过期用新记录或状态标记表达，不改写历史。
5. **持久性**：标记 `verified` 前必须已提交；不能依赖异步队列。
6. **隐私**：证据应尽量是报告/日志/脱敏截图；敏感屏幕内容不得进入普通 Trace Payload。
7. **可迁移**：不重建 Data Engine、不改现有表结构即可落地；Telemetry 保持可插拔。

---

## 3. Considered options

### Option A — 证据写进每次运行的 Data Engine DB

- Benefits：复用现有 schema/读取器；实现最快。
- Costs / risks：`--traces-path` 隔离会让证据随运行消失；`ios-smoke` 无 session 无法写；跨运行查询要扫描所有 DB。

### Option B — 独立全局 Capability Evidence Store + 全局 artifact 目录（推荐）

- Benefits：跨运行稳定；与任务证据解耦；验证器无需 Artemis session；查询简单；可独立备份/清理。
- Costs / risks：多一个 SQLite 文件/端口；需要定义与 Data Engine 的 ID 关联；备份策略要覆盖两个库。

### Option C — 让 Telemetry 成为证据/执行事实权威

- Benefits：概念上“观测”统一。
- Costs / risks：Telemetry 目前未接线、无 session/step 模型、JSONL 不适合按 task/action 查询；会把大量改造压进 Phase 6。

### Option D — JSON 文件 + 目录扫描

- Benefits：无 schema。
- Costs / risks：并发写、索引、去重、事务都需自己实现；现有 Data Engine 已经证明 SQLite 更合适。

**选择：Option B。**

---

## 4. Decision (proposed)

### D1 — 权威矩阵

| 数据 | 权威模块 | 内容 | 不承担 |
|---|---|---|---|
| Task/Session/Step/Action 执行证据、截图/视频 Artifact 引用 | Data Engine | 可回放的运行证据；按 run 归档 | 能力验证结论 |
| Capability Evidence + 验证 Artifact | **Capability Evidence Store（Data Engine 负责的全局证据库，独立 SQLite）** | 跨运行的验证记录、指纹、测试环境、artifact 引用 | 运行步骤/计划 |
| Capability Snapshot | Capability Registry（派生缓存） | 由 evidence + 本次 probe 计算的 View | 证据权威 |
| Span / Latency / Token / Metrics | Telemetry（观测层） | 性能与调用链观察；可导出 | 逐步执行证据、证据结论 |
| Agent Transcript / 压缩视图 | Memory | 下一轮上下文 | 原始截图/证据 |
| 后台任务进程状态 | Runtime Status Store | `status.json`、PID/lease 关联 | 动作证据、Replay |

规则：跨模块只允许通过 ID 引用；同一事实不得在两个权威模块各存一份“结论”。

### D2 — Capability Evidence Store（全局）

- 位置：`get_app_dir()/capabilities/evidence.db`（可被 `ARTEMIS_CAPABILITY_DB` 覆盖，测试用临时目录）。
- **不使用** `settings.TRACES_PATH` / `--traces-path`：证据必须跨运行；`--traces-path` 只影响运行证据。
- 表（建议，PR-2 落地）：

```sql
CREATE TABLE capability_evidence (
  evidence_id       TEXT PRIMARY KEY,
  capability_id     TEXT NOT NULL,
  conclusion        TEXT NOT NULL CHECK (conclusion IN ('verified','failed')),
  device_id         TEXT NOT NULL,
  endpoint_ids      TEXT NOT NULL,          -- JSON array，按 ADR-003
  fingerprint       TEXT NOT NULL,
  fingerprint_fields TEXT NOT NULL,         -- JSON
  test_id           TEXT NOT NULL,
  test_version      TEXT NOT NULL,
  evidence_kind     TEXT NOT NULL CHECK (evidence_kind IN ('contract_test','smoke','manual')),
  executed_at       REAL NOT NULL,
  environment       TEXT NOT NULL,          -- JSON
  artifact_refs     TEXT NOT NULL,          -- JSON array，非空
  operator          TEXT,
  notes             TEXT,
  revalidate_after  REAL,
  superseded_by     TEXT,                   -- 新证据取代旧结论
  created_at        REAL NOT NULL
);
CREATE INDEX capability_evidence_lookup
  ON capability_evidence (capability_id, device_id, fingerprint, executed_at);
```

- **append-only**：不 UPDATE 结论；用 `superseded_by` 标记被新结论取代。
- **最新结论优先**：同一 `(capability_id, device_id, endpoint_ids, fingerprint)` 的 `failed` 使旧 `verified` 失效（见 ADR-001 D5）。
- **同步写入**：`record()` 必须在返回前完成 commit（可 `fsync`），不得走 `DataEngine._run_in_background`；失败必须抛错给验证器，而不是“稍后重试”。
- 无需 Artemis session：`ios-smoke`/Contract Test 可直接调用。
- Registry 的 Snapshot 缓存不落库或落派生表均可，但必须可重建；删除缓存不得丢证据。

### D3 — Artifact 规则

- Capability 证据的 artifact 目录：`get_app_dir()/capabilities/artifacts/<sha256>`（与运行截图目录分离）。
- 内容寻址：`sha256(bytes)`；相同内容只存一份；`artifact_refs` 保存 hash（可选 `kind`/`size`/`content_type`）。
- 每个 artifact 必须有元数据：sha256、大小、MIME、创建时间、来源测试。
- **不把二进制塞进 JSON/SQLite BLOB**；JSON 只放引用。
- 大小上限（建议 5 MB/artifact，超出拒绝并报错）；只允许证据需要的格式（JSON 报告、日志、脱敏截图）。
- 敏感内容：默认只存测试报告 + 日志；截图必须裁剪到目标区域或脱敏；禁止账号、Token、通知内容、输入框文本。

### D4 — ID 与关联

| ID | 创建方 | 用途 |
|---|---|---|
| `task_id` / `session_id` | Data Engine（`start_session`） | 运行证据主键 |
| `trace_id` | Data Engine（`record_trace`） | 步骤/动作调用链 |
| `step_id` | Data Engine | 步骤证据 |
| `action_id` | ADR-004 定义（Phase 1 PR-4） | 非幂等/重试/验证关联 |
| `frame_id` | PR-4 定义 | Frame/Target 证据 |
| `evidence_id` | Capability Evidence Store | 能力验证记录 |
| `artifact_ref` | Data Engine / Evidence Store | 内容寻址引用 |

- 证据记录 `environment` 里记录验证运行的 `session_id`（如果有），**但不是外键**：验证可以独立于任务存在。
- 跨库查询用 `capability_id + device_id + endpoint_ids + fingerprint`；不使用自增行号/文件路径作为身份。

### D5 — 写入与持久性

- 运行证据：允许保持异步（`record_trace`/`record_step`），Phase 6 再评估关键路径同步化；不得把异步记录当作“已验证”。
- 能力证据：同步、事务提交后才可返回 `verified`；失败必须显式报错。
- 读取：提供只读 API/连接（复用 `StorageManager(read_only=True)` 的模式），MCP/Admin Console 只能读。
- 并发：SQLite WAL + 短事务；同一 `evidence_id` 重复写入必须幂等（按主键拒绝/忽略）。

### D6 — Telemetry 的角色

- Telemetry 是**观测/导出层**，不是执行证据权威；当前未接线，保持 dormant，直到有真实消费者。
- 若启用：span 必须引用 Data Engine 的 `trace_id/step_id/action_id`，只记录耗时、token、重试、错误码等指标；不得复制截图、动作 payload 或证据结论。
- 不要求 Phase 6 之前完成 Telemetry 接线；也不允许为了“看起来统一”把 `traces` 表迁移到 JSONL。
- 如果未来采用 OTel，导出从 Data Engine/Telemetry 适配，不改变权威矩阵。

### D7 — Memory 与 Runtime Status Store

- Memory 继续从 Data Engine 读 steps/chunks 并生成上下文视图；它不得成为证据或原始截图唯一存储。
- Runtime Status Store 只写进程/后台任务状态；不得存动作结果或证据；`status.json` 损坏隔离逻辑保留（`trace_store.py`）。
- `verify`/`replay` 工具只读 Data Engine（+ 必要时的 evidence store），不读 `status.json` 作为事实。

### D8 — 查询、保留与隐私

- 查询：
  - 运行证据：按 `session_id`/`trace_id`/`step_id`/`device_id`/时间；
  - 能力证据：按 `capability_id + device_id + fingerprint`，返回最新未 superseded 的结论；
  - 回放：现有 `inspect_trace`/`trace_export` 继续读 Data Engine。
- 保留（建议，可在 Phase 6 调参）：
  - 运行证据：沿用现有 traces 清理策略；
  - 能力证据：不自动过期删除；`revalidate_after` 只影响“是否可用”，不删历史；
  - artifact：与 evidence 同生命周期；提供显式删除（按 device 或 capability）。
- `--traces-path`、`ARTEMIS_TRACES_DIR` 不得改变证据库位置；只有 `ARTEMIS_CAPABILITY_DB`/app dir 决定。
- 备份/迁移：证据库与 artifact 目录一起备份；不依赖单次运行目录。

---

## 5. Consequences

### Positive

- `verified` 跨运行稳定，不受 `--traces-path`/单次运行清理影响。
- 证据写入是同步的，进程崩溃不会留下“口头 verified”。
- Data Engine 继续承担运行证据，不需要重建；Telemetry 可以独立演进。
- 查询/回放/证据三条路径的权威边界清晰，Phase 6 有明确落点。

### Negative / trade-offs

- 多一个 SQLite 库与 artifact 目录，需要备份/清理/权限管理。
- 证据与运行证据的关联是“软引用”（ID 记录在 environment），不是外键；跨库一致性靠写入顺序与测试保证。
- 目前 Telemetry 仍 dormant，Phase 6 不会立即产生“统一观测面板”。

### Follow-up work

- PR-2：端口 + 全局 store + 同步写入 + 指纹查询。
- Phase 6：索引、保留策略、查询 API、Admin Console 只读视图。
- ADR-004：`action_id` 的创建与关联。
- 未来 ADR：OTel/Telemetry 接线（如有真实消费者）。

---

## 6. Contract and dependency impact

- **Layers**：横向数据层；L1/L2/L3 只通过 Port 查询，不直接连库。
- **Ports / DTOs**：`CapabilityEvidenceStore`（record/lookup/list）、`ArtifactStore`（put/get/metadata）；DTO 使用 ADR-001 的 `CapabilityEvidence`。
- **存储位置**：Data Engine（运行证据，现有 SQLite）；Capability Evidence Store（全局 SQLite + artifact 目录）；Telemetry（JSONL，导出层）；Runtime Status Store（`status.json`）。
- **Import direction**：`core` 定义 Port/DTO；`data_engine` 提供实现；`mcp_server` 只读；`telemetry` 不得反向依赖 `drivers/devices`。
- **Compatibility**：现有 `data_engine.db` 表结构不改；新增证据库是 additive；`--traces-path` 语义不变。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-2 | `CapabilityEvidenceStore` Port + 全局 SQLite 实现 + artifact store；`record` 同步；`lookup` 供 ADR-001 使用 | 无（默认无证据时行为不变） |
| Phase 6 | 索引/保留/查询 API；MCP/Admin Console 只读视图；清理命令 | 查询能力增强，无权威迁移 |
| Phase 9 | 清理兼容入口；确认没有代码直接读证据文件/`status.json` 当事实 | 无 |

**Non-goals**：重建 Data Engine schema、迁移历史 traces、Telemetry 接线/OTel、跨主机同步、PII 自动识别、把证据上传到云端。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/data_engine/test_capability_evidence_store.py`（新增）：
  - append-only：重复 `evidence_id` 幂等；不修改历史结论；
  - supersede：新 `failed` 使旧 `verified` 查询不到；
  - 同步持久化：`record()` 返回后新连接可读到；
  - 查询按 `capability_id + device_id + fingerprint`；
  - 无 artifact/无 fingerprint 的记录被拒绝。
- `tests/unit/data_engine/test_artifact_store.py`（新增）：sha256 去重、大小上限、元数据、路径不穿越。
- `tests/unit/data_engine/test_evidence_isolation.py`（新增）：`--traces-path` 指向临时 run 目录时，证据库位置不变；删除 run 目录不影响证据。
- `tests/unit/runtime/test_trace_store.py`（扩展）：`status.json` 不承载证据字段的回归断言。
- `tests/unit/test_telemetry.py`（保持）：Telemetry 仍是独立 JSONL，不产生证据。

### Integration

- 进程重启后 `verified` 仍可查询（真实文件系统）。
- 两个进程并发写不同 evidence：都成功；同一 evidence：幂等。
- 只读打开证据库；MCP `inspect_trace` 不受证据库影响。
- 备份/恢复：复制 DB + artifacts 后可查询。

### 判据

- “verified”只能来自证据库查询结果 + ADR-001 状态解析；Mock 测试不产生平台 `verified`。
- 任何“从 `status.json`/日志推断验证通过”的路径都视为缺陷。

---

## 9. Rollback

- 证据库 additive：回滚 = 不接线 Registry 的 evidence lookup，`CapabilityView` 退化为 declared/available；删除 `capacities/evidence.db` 与 artifacts 不影响运行证据。
- 开关：`ARTEMIS_CAPABILITY_EVIDENCE=0`（PR-2 引入）禁用 evidence 读取/写入；`ARTEMIS_CAPABILITY_DB` 可指向临时库。
- 不修改现有 `data_engine.db` 表结构；不迁移/删除历史 traces。

---

## 10. Security and safety

- 证据库/artifact 目录权限 0600（目录 0700）；路径必须规范化，防止 `artifact_ref` 穿越。
- 不写凭据、Token、账号、通知内容、输入框文本；截图默认裁剪/脱敏。
- `record()` 失败必须让验证器失败，不允许“吞掉错误并标 verified”。
- 删除证据必须显式执行并记录审计日志；不得由普通任务自动清理。
- 读路径只读；写入只允许验证器/维护命令。

---

## 11. Resolved / open questions

已定：

- 证据跨运行、全局存储；`--traces-path` 不影响证据。
- 证据同步写入；运行证据保持异步。
- Telemetry 不是权威，当前保持 dormant。
- Memory/Status Store 不承担证据。

待定（不阻塞 PR-2 骨架）：

1. artifact 大小上限（建议 5 MB）与保留天数默认值。
2. 证据库与 artifacts 的备份/清理入口放在 CLI 还是 Admin Console（Phase 6）。
3. 是否需要把 `history_chunks` 的表从运行库拆出（建议不做；它属于运行证据）。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | 现有 Data Engine 是运行证据权威；Telemetry 未接线；Capability Evidence 无落点且不能放进按运行隔离的 `--traces-path` |
