# 测试与质量门禁基线（2026-11-09）

## 2026-11-11 当前基线（最新）

> 审计对象：`integration/10101205-all-fixes` @ `2e5b07c`
> 环境：macOS 26.4 (Darwin arm64)、`uv 0.7.13`、项目 Python `3.12.11`
> 工作区：`config/artemis.jsonc` 仍有用户未提交修改（provider=openai）；`docs/SHOWCASE_RESULT_20261111.md` 未纳入提交

| 门禁 | 结果 |
|---|---|
| `uv run pytest tests/unit -q` | **0 failed, 3869 passed, 9 skipped**, 88 warnings, 144.84s |
| `uv run ruff check` | All checks passed |
| `uv run ruff format --check .` | **16 files would be reformatted**（既有文件；非新增） |
| `quality_ratchet` | broad_exception_handlers=773, silent=0, type_ignore_comments=18（当前基线 774/18） |
| 真实设备 | iOS smoke/Flash/Pro、Android Flash 已通过；详见 `POST_MIGRATION_ARCHITECTURE_AUDIT.md` |

### 当前失败清单

无。原 12 条 Explorer/VideoAnalyzer 失败是测试夹具问题：测试 mock 了
`genai.Client`，但没有把 client 挂到 `ctx._genai_client`，于是 fallback 到
universal LangChain 路径并解析 MagicMock provider。现在测试挂载 mock client，
走预期的 native Gemini 分支。

### 真实设备证据

- iOS：`ios-smoke` 四段 + LocateAnything（`source=locate-anything`）通过；Flash/Pro 任务完成；ActionEvidence/Telemetry 已产生；
- Android：`EUWSOBXCZLPFNJF6` Flash 任务完成，ActionEvidence 可查询；
- UVC identity：当前仍为 `CONFIG_HINT`（采集卡无可用 USB serial/topology），不得写成稳定 USB identity。
- P0 解锁后复测（2026-11-11 19:06）：`ios-smoke --target 快捷指令` 通过全部阶段；Flash `回到主屏` 会话 `41b6d9a8-47e9-4fee-a8f8-288133e69e46` 成功（2 steps），UVC/serial lease 均已释放。该次只验证单动作 Flash 与 Session close，未触发多动作 Burst 拒绝路径。
- Observation 迁移后复测（2026-11-11 19:15）：`ios-smoke` 在 snapshot 阶段因黑帧失败（`uvc_no_signal`），未进入指针/点击；该失败是采集无信号，不是 Observation 服务异常。迁移后尚无新的真机观察证据。
- Observation 迁移后复测 2（2026-11-11 20:28）：重新连接机台后 `ios-smoke --target 快捷指令` 七阶段全绿；Flash `回到主屏` 会话 `39fd9ed9-5857-48e2-9597-52afacc382a6` 成功（2 steps），action evidence `3d85695e55f...` 为 `adapter_acknowledged`，UVC/serial lease 均释放。
- Adapter 迁移后复测（2026-11-11 20:38）：`ios-smoke` 在 connect 前因手机自动锁定失败（`suspended=1`），未产生新的真机证据；之前的 smoke/Flash 仍有效，但不能冒充 adapter 迁移后的结果。4 条新增 skip 是未连接 Android 设备时的诊断测试。
- Adapter 迁移后复测 2（2026-11-11 20:40）：解锁后 `ios-smoke --target 快捷指令` 七阶段全绿；Flash `回到主屏` 会话 `b9cb2860-76dd-4f2d-ada7-7d1adae260ce` 成功（2 steps，8.2s），action evidence `6fdde1e03a4...` 为 `adapter_acknowledged`，UVC/serial lease 均释放。4 条 skip 是未连接 Android 设备时的诊断测试。
- Observation freshness 真机（2026-11-11 20:57）：Flash 会话 `6dc1c787-c5a2-456b-b448-f5ba93f6dba1` 的 3 次 `click` 因 `observation is 20.6s old (max 5.0s for click)` 被 `stale_frame` 拒绝，未派发；任务随后被既有 No-Op Guard 以 failed 结束，不能记作成功。
- Action receipt 真机（2026-11-11 21:02）：Flash `回到主屏` 会话 `6f0175e5-d187-4720-b252-4cce4971d840` 成功，但 action evidence 为 `device_ack=adapter_acknowledged`、`action_state=adapter_acknowledged`、`goal_verified=false`；任务成功与动作验证明确分离。
- Goal verification 真机（2026-11-11 21:07）：Pro `回到主屏` 会话 `b9e51024-0377-481e-a3c3-298350a01aca` 完成，Checker 3/3，`run_outcome.goal_verified=true`；同一会话 action evidence 仍是 `adapter_acknowledged`，证明 goal 验证只由最终 Checker 写入。
- Effect evidence 真机（2026-11-11 21:10）：Flash `回到主屏` 会话 `38ece0b8-7f4a-4da4-ae5e-4750246588a5` 的 HOME 动作在 screen changed 后记录 `action_state=observed_effect`、`goal_verified=false`；动作效果与目标验证仍分离。
- R3.4 双平台验收（2026-11-11 21:38–21:49）：iOS smoke/Flash/Pro/cancel/lease 全绿；Android Flash `ad0ef70a...`、Pro `4f06fa3c...`（`goal_verified=true`）、cancel `318bf58c...`、helper forward lease 释放均通过。物理拔插后 iOS smoke 与 Android Flash `9858ede3...` 也通过；任务中拔线仍未执行。详见 `R3_ACCEPTANCE_REPORT.md`。
- Firmware unsupported command（2026-11-11）：`FirmwareBridge` 将 `ERR unknown command`/`usage:` 映射为 `FirmwareUnsupportedCommand(incident=command_unsupported)`；absolute click 单次返回 `TapResult(command_unsupported)`，不重试、不降级。
- R4 Adapter Contract（2026-11-11）：新增 `tests/unit/contracts/test_adapter_contracts.py`，覆盖 UVC/App/Composite Source 和 App/Composite/HID Sink 的 Port 形状、DTO、能力选择与 unsupported 语义。
- Android Observation parity（2026-11-11）：helper JSON fallback 与 XML/UIAutomator element schema 对齐（`bounds`/`parsed_bounds`/`accessibilityText`），fallback 客户端保持同一 `UIAutomatorScreenData` 形状。启用 Helper 后，强制 Helper 的 Flash `5eef9e4b...`、Pro `19bce48e...` 均成功；稳定屏幕 parity 91% recall / 95% precision。
- R4 Public Contract Freeze（2026-11-11）：新增 `test_public_contract_freeze.py`，冻结 ActionResult/ActionReceipt/ActionEvidence/Frame/ScreenData/CapabilitySet/CapabilityId 字段与 Protocol 方法。
- Pointer precheck receipt（2026-11-11）：Flash precondition 仍为 delegated；动作后按 sink metrics 记录 verified/failed/delegated，无 `error_px` 不升级为 verified，写入 ActionEvidence.pre_verification。
- Pointer receipt 真机（2026-11-11 22:51）：iOS session `895239e6-...` 的两次 click 分别为 `failed`（screen_changed）和 `verified`（error_px=0.0）；任务被 No-Op Guard 以 failed 结束，不能记作成功。
- R3.6 Action Service（2026-11-12）：新增 `ActionService`/`ActionExecutionPort`；`DeviceActionService` 接管 translation/target/observation 语义，Agent 使用注入服务，MCP transport 在 L1；`ActionReceipt` metadata 进入生产结果。Agent 的完整传递依赖仍受 legacy `tools` 路径阻塞。
- ActionReceipt 状态（2026-11-12）：生产 receipt 现在保留 `observed_effect`，不再把每个成功 dispatch 压成 ACK。
- ActionEvidence receipt（2026-11-12）：`ActionEvidence.action_receipt` 持久化生产 receipt；Flash 在 frame diff 证据存在时将其升级为 `observed_effect`。iOS Flash 新语义路径已跑通（`293ddbd1...`、`ab50a46a...`）。
- Command-shaped Action Port（2026-11-12）：`ActionDispatchPort`/`McpActionPort` 落地；iOS smoke/Flash `c9bacf28...`、Android Flash `ced4441e...` 的 `action_receipt` 为 `observed_effect`。Pro 与完整 Agent 传递闭包仍未完成。
- Task 4（2026-11-12）：`get_action_service` 主入口、`ActionService` command port + helper_port；iOS Flash `db7991de...`、Android Flash `c1a7c835...` 通过；Pro 因黑帧阻塞，完整 Agent 传递闭包仍未清零。
- Task 4 Pro 复测（2026-11-12）：iOS Pro `005aed2c-20d1-4e33-85d9-1939ad3fd1a4` 完成，Checker 3/3，`goal_verified=true`；6 条 action evidence（一条 tap 为 unknown，其余 acknowledged）。完整 Agent 传递闭包仍未清零。
- Legacy tool 传递清理（2026-11-12）：video analyzer 通过 context 解析 controller，Flash 的 Reflex 声明由 L1 composition 注入；完整 Agent 传递闭包测试已启用并通过。
- iOS Pro（2026-11-12）：`7f12bdaf-8d50-414f-adb5-7c25c5aacf54` 在 Service 路径完成，`goal_verified=true`（Checker 1 passed / 1 inconclusive）；该次没有 action 记录。完整 Agent 传递闭包仍未完成。

> 历史记录（2026-11-09，`a82d58e`）保留在下方，仅作基线对照，不代表当前 HEAD。

---

> 审计对象：`integration/10101205-all-fixes` @ `a82d58e`
> 环境：macOS 26.4 (Darwin arm64)、`uv 0.7.13`、项目 Python `3.12.11`
> 工作区状态：`config/artemis.jsonc` 存在**未提交修改**（provider 改为 `openai`），本页单独标注其影响
> 关联：[`BASELINE_2026-11-09.md`](BASELINE_2026-11-09.md)、[`CURRENT_CALL_FLOWS.md`](CURRENT_CALL_FLOWS.md)

本页只记录**本次实际运行**的结果。历史文档中的数字（例如 2026-11-06 的 `14 failed / 3535 passed / 9 skipped`）仅作为对照，不当作现值。

---

## 1. 结果总览

| 门禁 | 命令 | 结果 | 判定 |
|---|---|---|---|
| 单元测试 | `uv run pytest tests/unit -q` | **14 failed, 3535 passed, 9 skipped**, 76 warnings, 165.69 s | ❌ 红色（2 条为本地配置引起） |
| CI 等价测试 | `uv run pytest -q` | **14 failed, 3568 passed, 10 skipped, 8 deselected**, 164.25 s | ❌ 红色（同上） |
| Lint | `uv run ruff check` | `All checks passed!` | ✅ 绿色 |
| 格式 | `uv run ruff format --check .` | **14 files would be reformatted**, 876 already formatted | ❌ 红色（全部为既有文件） |
| 质量 Ratchet | `uv run python scripts/quality_ratchet.py` | 通过（774 / 0 / 18）；债务与退出条件见 `QUALITY_RATCHET_DEBT.md` | ✅ |

CI 等价口径来自 `.github/workflows/ci.yml:69`（`uv run pytest`）与 `pyproject.toml:199-204` 的 `testpaths`（`tests/unit`、`tests/tools`、`packages/artemis-client/tests`）。

---

## 2. 失败清单（14）

### 2.1 分支级失败（12）：LLM provider 校验与测试 Mock 不匹配

| 测试文件 | 数量 | 失败形态 |
|---|---|---|
| `tests/unit/agents/test_explorer.py` | 9 | `ValueError: Unknown LLM provider <MagicMock ...>` |
| `tests/unit/agents/test_video_analyzer.py` | 3 | 同上；`status` 从 success 变为 error |

根因（已确认）：

- `artemis/llm/router.py:82` 对 provider 做白名单校验并 `raise ValueError`；
- 测试用 `MagicMock()` 充当 `llm_config.get_agent().provider`（例如 `tests/unit/agents/test_video_analyzer.py:40-43`），字符串化后不在白名单；
- 该校验由 `351ca84`（2026-09-28，`fix: server status, CLI entrypoint, replay and test issues`）引入，之后未同步更新这些测试的 mock 形状。

结论：**与本轮架构文档改造无关**（本轮只改 `.md` 与 `AGENTS.md`），属于分支既有回归。

### 2.2 本地配置引起（2）：admin_console 模型信息

| 测试 | 期望 | 实际 |
|---|---|---|
| `tests/unit/admin_console/test_model_service.py::test_get_active_model_info_pro_architecture` | `provider == "google"` | `"openai"` |
| `...::test_get_active_model_info_flash_architecture` | 同上 | 同上 |

原因（已确认）：本地 `config/artemis.jsonc` 未提交修改把 `default.provider` 改为 `openai`（`git diff config/artemis.jsonc`），而测试直接读取工作区配置。

交叉验证（已确认）：把该文件临时替换为 `git show HEAD:config/artemis.jsonc` 后运行同一文件，结果为 `4 passed in 0.07s`；随后已恢复用户原文件（md5 校验一致）。

因此**分支级基线**应为约 `12 failed / 3537 passed / 9 skipped`（推断：未用干净配置整体重跑；依据是上述 2 条已逐条验证）。

---

## 3. 质量门禁细节

### 3.1 格式检查（既有红色）

`uv run ruff format --check .` 报 14 个文件需要重排，全部为既有文件，例如：

```
artemis/agents/flash/runner.py
artemis/reflex/ios_apps.py
docs/artemis-target-architecture.md
docs/ios-uvc-execution-plan.md
docs/superpowers/plans/*.md（5 个）
scripts/ios_open_app.py
skills/tetris/policy_jev.py
tests/unit/reflex/test_blue_pointer_detector.py
```

说明：`docs/artemis-target-architecture.md` 与 `docs/ios-uvc-execution-plan.md` 是本次 P0 加过 Superseded 横幅的文件，被标记的是其中**既有的 Python 代码块**，不是横幅行。本轮不重排这些文件（避免在架构改造 PR 中混入无关 diff）。

### 3.2 质量 Ratchet（既有红色）

`.quality-baseline.json` 是 `351ca84`（2026-09-28）写入的，此后分支已有 301 个提交。

| 指标 | 基线 | 当前 | 差值 |
|---|---|---|---|
| `broad_exception_handlers` | 754 | 773 | **+19** |
| `type_ignore_comments` | 18 | 20 | **+2** |
| `silent_broad_exception_handlers` | 0 | 0 | 0 |

`uv run python scripts/quality_ratchet.py` 退出非零。按执行计划 §6.3，**不得**为了让 CI 变绿而在架构 PR 中顺手放宽基线；需要单独评估或单独提交。

---

## 4. 跳过与未执行

- 9 skipped（`tests/unit`）/ 10 skipped（CI 等价）：本次未用 `-rs` 枚举原因；复跑方式 `uv run pytest tests/unit -q -rs`。**未验证**其具体依赖。
- 8 deselected：默认 marker 过滤（`integration/e2e/cloud/manual/android`），见 `pyproject.toml:222-224`。
- **未执行**：`tests/integration/`、`tests/e2e/`、`tests/integration/ios/`、真实机台 Smoke、macOS/TV 平台测试。
- 因此本基线**不能**作为“设备能力已验证”的证据；它只覆盖 Mock/单元层。

---

## 5. 历史对照

| 时间 | 单元测试 | 说明 |
|---|---|---|
| 2026-11-06（历史计划记录） | `14 failed / 3535 passed / 9 skipped` | 失败集中在 Admin Console / Explorer / Video Analyzer |
| 2026-11-09（本次） | `14 failed / 3535 passed / 9 skipped`（含本地配置） | 区域相同；其中 2 条为本地配置，12 条为 provider 校验回归 |

数量巧合一致，但**失败构成不同**：不能把 2026-11-06 的逐条结论直接复用。后续 PR 以本页 §2 的逐条清单为准。

---

## 6. 后续 PR 的使用规则

1. **新增失败**：与 §2 的 12 条分支级失败逐条比对；出现新条目即为回归。
2. **配置隔离**：跑基线时使用仓库内 `config/artemis.jsonc`（HEAD 版本），或用干净 worktree；不要把本地 `openai` 配置下的 2 条 admin_console 失败算进分支基线。
3. **不要静默刷新 Ratchet 基线**：基线更新需要单独提交并说明偿还/新增原因。
4. **不要用整仓 `ruff format` 制造大 diff**：格式债务单独治理。
5. **硬件/Mock 区分**：本页所有结果均为 Mock/单元层；涉及 iOS/Android 真机的结论必须另附 Smoke 或实机证据。

---

## 7. 复现命令

```bash
# 单元基线（计划要求）
uv run pytest tests/unit -q

# CI 等价
uv run pytest -q

# 逐条复现
uv run pytest tests/unit/agents/test_explorer.py -q
uv run pytest tests/unit/agents/test_video_analyzer.py -q
# 用 HEAD 配置验证配置相关失败（不要覆盖用户工作区文件时可改用干净 worktree）
uv run pytest tests/unit/admin_console/test_model_service.py -q

# 门禁
uv run ruff check
uv run ruff format --check .
uv run python scripts/quality_ratchet.py --print-current
```
