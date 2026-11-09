# 测试与质量门禁基线（2026-11-09）

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
| 质量 Ratchet | `uv run python scripts/quality_ratchet.py` | 2 项超基线（见 §3.2） | ❌ 红色（既有债务） |

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
