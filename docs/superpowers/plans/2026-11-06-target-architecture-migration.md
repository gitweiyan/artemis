# 目标架构迁移计划（2026-11-06）

> **Superseded（2026-11-08）**：本计划的 B0–B6 批次已由
> [`docs/architecture/REFACTOR_EXECUTION_PLAN.md`](../../architecture/REFACTOR_EXECUTION_PLAN.md) 的 Phase 0–9 取代；
> 它引用的 `docs/artemis-target-architecture.md`（v1）也已标记 Superseded。
> 本文仅作历史记录，**不要按本文的批次或“吸收表”实施**。其基线数字
> （`14 failed / 3535 passed / 9 skipped`）是 2026-11-06 的历史值，不代表当前分支。
>
> **For agentic workers:** 此段规则已失效；新工作请遵守 `AGENTS.md` 与 `docs/architecture/` 下的契约与计划。

**Goal:** 把 artemis 从"`BaseDeviceDriver` + Android 专属实现 + iOS 旁路"迁移到
`docs/artemis-target-architecture.md` §3 的六层结构（PerceptionSource / ControlSink /
DecisionProvider / Orchestrator / SafetyNet / Telemetry），按 spec 自己的 §10 迁移顺序分批落地，
**每批独立可合并且不新增回归**。

**Architecture:** 平台降级为能力组合（§2 原则 1）；安全校验收敛成横切层、只读 capability（原则 3）；
坐标全部走 `ScreenGeometry`（原则 6）；失败必须可解释（原则 7）。迁移不是重写：先补能力位与安全规则，
再做"纯搬家"把实现归位，最后层化 SafetyNet 与遥测。

**Tech Stack:** Python 3.12、pytest、frozen dataclass、Protocol（`artemis/perception|control/base.py`）、
TDD、`uv run pytest tests/unit -q` / `ruff` / `scripts/quality_ratchet.py`。

**Spec:** `docs/artemis-target-architecture.md`（v1，Status: Proposed）＋ `docs/ios-uvc-execution-plan.md`。
本计划同时**修订 spec 中已经与代码脱节的部分**（见 §0.3），修订随对应批次提交。

## Global Constraints

- 全量基线：`uv run pytest tests/unit -q` = **14 failed / 3535 passed / 9 skipped**（`admin_console` 2、
  `agents/test_explorer` 9、`agents/test_video_analyzer` 3）。任何批次不得新增失败。
- `uv run ruff check` 全过；`quality_ratchet` 的 `broad_exception_handlers` / `type_ignore_comments`
  相对 HEAD **增量 0**（当前 773 / 20；754 / 18 是既有欠账，不动基线）。
- 能力位默认 `False`；**不新增** `if platform == "ios"`（现有 6 处要逐步换成能力位，见 B2）。
- 一个事实只写一次：`requires_pointer_precheck` 只由 `orchestration/selector.py::requires_precheck()` 推导。
- 纯搬家批次（B3）的验收是"**行为零变化**"：`git diff --stat` 只出现 import/文件移动，全量回归不变。
- 每个批次一个分支（`<type>/<mmddhhmm>-<topic>`）、一组提交、一个 tag，按 `docs/branch-plan.md` §4 收尾。

## Review Focus（最可能咬人的五种输入；每条都落到某批的测试）

1. **老固件没有 `amove`**（`move/click` only）：能力位必须是 `False`，点击必须仍走指针环并预检（B1）。
2. **能力谎报**（声称绝对指针、控制层无方法）：必须拒绝而不是 `AttributeError`（B1）。
3. **无置信度来源的目标**（LA 只回点、无 score）：`low_confidence` 规则**不能**凭空断言"低置信"，
   只能记录"无置信度来源"（B2 的测试钉住这个区别）。
4. **模型坐标弱**（实测中位 108 px 偏差）：候选化之前不能把它当精确坐标用（B6）；B1–B5 期间靠
   `target_unverified` + LA 兜底，不新增"盲点"路径（§11 非目标）。
5. **搬家期间的重复事实源**：`reflex/*` 与 `perception|control|safety` 共存时，同一能力不得两处推导（B3 验收）。

---

## 0. 现状盘点（逐条对照 spec）

### 0.1 已落位（不要重做）

| spec | 代码 | 备注 |
|---|---|---|
| §4.1 `Frame/Target` | `perception/base.py:12,22` | 字段与 spec 一致（`Frame.source/health` 齐） |
| §4.2 `PerceptionSource` + 四种实现 | `perception/{base,app_automation,uvc,camera,composite}.py` | 协议齐（`snapshot/ui_tree/ocr/locate`） |
| §4.3 `ControlSink` + 三种实现 | `control/{base,app_control,hid,composite}.py` | 协议齐 |
| §4.4 `DeviceSession` | `devices/{session,geometry,app_session}.py` | façade 由 `BaseDeviceDriver` 承担 |
| §5 选择表 / §6.3 编排选择 | `orchestration/selector.py::select_composition`、`profiles.py::select_profile` | 纯函数、带 reason |
| §10 迁移顺序 1/2/3/5/6 | 同上 + `drivers/factory.py` + `mcp/action_executor.py` + `tools/reflex_tool.py` | 见 §10 表格 |

### 0.2 差距（本计划要关掉的）

| # | 差距 | spec | 现状（证据） | 批次 |
|---|---|---|---|---|
| G1 | 能力模型没有"绝对指针"这一维；`requires_pointer_precheck` 声明了但**无人消费** | §4.3/§7/§13 | `devices/capabilities.py:14-24`；`selector.py:64`；点击路径靠 `hasattr`（`reflex/mouse.py::_absolute_ready`） | **B1** |
| G2 | `low_confidence` 不存在；"pixel 不可用 → 不点击"没有落点 | §2 原则 5、§7 规则 | `agents/validator/categories.py:26-50` 无该枚举；`safety/pixel.py` 只算 diff | B2 |
| G3 | 6 处平台分支替代能力位 | §7 规则"不写 platform == ios" | `mcp/action_executor.py:311,862`、`mcp/actuators/adb.py:252,326`、`drivers/factory.py`、`agents/ios_shortcuts.py` | B2 |
| G4 | 采集/传输/检测器/控制器仍住在 `reflex/` | §10 吸收表 5 行 | `reflex/{capture,control,mouse,vision}.py`（5+3+5+6 类）；6 个生产模块直接 import（factory、3 个 CLI、`tools/reflex_tool`、`drivers/ios/crop.py`） | B3 |
| G5 | 没有 SafetyNet 这一层（三段校验散在三处） | §7 | pre_target 在 `safety/xml.py`+`perception/ios_targets.py`；pre_pointer 在 `reflex/mouse.py::_verified_tap`；post_action 在 `safety/pixel.py`，由 `_absolute_click` 调用 | B4 |
| G6 | 遥测字段缺 5 项 | §9 | 动作记录无 `target box/confidence`、`precheck/postcheck verdict`、`explorer_tier`、`perception_sources`、`latency`（`agents/flash/runner.py:729-760`、`runtime/trace_export.py:243`） | B5 |
| G7 | 目标定位只接受"模型给坐标"（或 index 需 UVC 树） | §12 风险"模型坐标弱 → ER/OCR 出框，VLM 只选择" | `mcp/action_executor.py::_translate` 的 index 分支要求 `index_elements`（Android 树）；iOS 无树 | B6 |
| G8 | `DecisionProvider` 没有协议 | §3 图 | 只有 skills 内部 policy（`skills/tetris/policy_*`），无统一接口 | 决策 D3 后再定 |

### 0.3 spec 自身需要修订的地方（随批次提交）

| spec 位置 | 现状 | 修订 |
|---|---|---|
| §4.1 `ActionResult` | 代码有 `code/metrics/normalized_coordinates/incident` | 补齐字段（B5） |
| §4.3 `capabilities` 列表 | 缺 `supports_absolute_pointer`（HID 绝对报告） | 加字段（B1） |
| §4.3 `press_key(key: Key)` | 代码是 `Any`（驱动再解释） | 改为 `str`（B2 或 B5） |
| §5 "HID 控制：相对…需状态/预检" | 现在默认一步绝对定位 + `aclick` | 改为"相对指针 + 可选绝对 pointer collection"（B1） |
| §7 incident 列表 | 代码多出 `screen_changed/stale_frame/uvc_no_signal/capture_timeout`；缺 `low_confidence` | 列表与实现对齐（B1 标状态、B2 补 `low_confidence`） |
| §10 路径表 `artemis/skills/tetris` | skill 实际在仓库顶层 `skills/` | 决策 D2 后修（搬目录或改文档） |
| §10 "现状（2026-10-21）"注 | 已过时（iOS 已有 session/组合/能力转述） | 刷新（B1） |
| §13 能力块 | 缺 `absolute_pointer`；`requires_pointer_precheck` 仍手写 | 见 B1 |

---

## 1. 批次路线图

| 批次 | 范围 | 依赖 | 验收（除全量基线外） | 风险 |
|---|---|---|---|---|
| **B0** | 登记已落位项（§0.1） | — | 无代码 | — |
| **B1** | 能力位 `supports_absolute_pointer` + `requires_pointer_precheck` 推导与消费 + spec §4.3/§5/§7/§13 | B0 | 组合打印里 `requires_pointer_precheck=False`；老固件仍走指针环 | 低 |
| **B2** | SafetyNet 的"不执行"规则：`low_confidence` incident + 置信度门槛；平台分支 → 能力位 | B1 | 无置信度来源时**不**伪造 low_confidence；6 处平台分支减到 0 | 中（行为变化） |
| **B3** | 纯搬家：`reflex/{capture,control,vision}` → `perception|control|safety` | B1 | `git diff --stat` 无逻辑改动；旧 import 全 0；全量回归不变 | 中（面广） |
| **B4** | `SessionTapController`/`PointerSession` 归位 + **SafetyNet 层化**（pre/post/incident 一个入口，读 capabilities） | B2, B3 | 三段校验只由 SafetyNet 调用；`grep` 无第二处 `verify_effect` 直接调用 | 高（核心路径） |
| **B5** | §9 遥测/回放字段补齐 | B4 | 一份 step 记录能回答"用哪只眼、置信度多少、预检/后检判决" | 低-中 |
| **B6** | 目标候选化：iOS 候选表 + index 选择（模型只选，不给坐标） | B2, B5 | 同一目标两次运行给出同一候选集；模型坐标不再直接进点击 | 中-高 |

B1 的详细计划已写：`docs/superpowers/plans/2026-11-06-ios-absolute-pointer-capability.md`。

---

## 2. B0：登记（无代码）

- [ ] 在 `.superpowers/sdd/2026-11-06-target-architecture-migration/progress.md` 记录 §0.1 已落位项与基线数字。
- [ ] 不提交代码；仅确认 `git status` 只有本地覆盖 `config/artemis.jsonc`。

## 3. B1：能力位与预检语义

见 `2026-11-06-ios-absolute-pointer-capability.md`（4 个任务，每任务 TDD + 独立提交）。
本计划对 B1 只补一条：Task 4 的文档同步除 §5/§7/§13 外，**§4.3 的 capabilities 列表**也要加
`supports_absolute_pointer`（§0.3 第 2 行）。

**验收（B1 结束）**

- [ ] `uv run python -c "…select_composition(iOS rig with absolute pointer…)"` 打印
      `requires_pointer_precheck=False`，`reason` 提到绝对指针。
- [ ] `tests/unit/reflex/test_pointer_session.py::test_a_sink_without_absolute_reports_keeps_the_pointer_loop` 仍绿。
- [ ] `grep -rn "requires_pointer_precheck" artemis/ | grep -v selector.py | grep -v capabilities.py`
      只剩 sink 的实现与日志（无第二处推导）。
- [ ] 全量回归 14 failed；`ruff` 全过；ratchet 773/20。

## 4. B2：SafetyNet 的"不执行"规则（low_confidence + 能力位化）

**Files:**
- Modify: `artemis/agents/validator/categories.py`（+ `LOW_CONFIDENCE`）
- Modify: `artemis/agents/validator/action_execution.py`（incident → category 映射）
- Modify: `artemis/agents/operator/prompts.py`（提示文案：置信度不足时不点击、先重新定位）
- Modify: `artemis/perception/uvc.py`（`locate()` 在目标 confidence 低于门槛时返回 `None` + 记录）
- Modify: `artemis/mcp/action_executor.py`（`_locate_coordinate_target`：LA 只回点 → 记录"无置信度来源"，
  **不**伪装成 low_confidence）
- Modify: `artemis/devices/capabilities.py` + 6 处平台分支（§0.2 G3）→ `supports_ascii_keyboard`、
  `supports_app_launch_by_name`

**Tasks**

- [ ] **T1 词汇与映射**：`ValidationErrorCategory.LOW_CONFIDENCE = "low_confidence"`；`_INCIDENT_CATEGORIES`
      加 `low_confidence`；operator 提示一句"证据不足时先重新定位，不要重试同一坐标"。测试：分类映射 +
      提示渲染（`test_validator.py`、`test_execution_incident_prompt.py`）。
- [ ] **T2 置信度门槛**：`UvcSource.locate()` 接受 `min_confidence`（默认 0.5）：命中但低于门槛 →
      返回 `None`，并在 `Frame`/结果上记录 `locate_low_confidence`。测试：构造低置信 `TextBox` →
      `locate` 返回 `None`；高置信 → 返回框中心。
- [ ] **T3 没有置信度来源时如实记录**：`_locate_coordinate_target` 的 LA 记录保留 `"confidence": None`，
      并在 step 记录写入 `target_confidence: null`（不伪造）。测试：LA 成功时记录里 `confidence is None`
      且**不**产生 `low_confidence` incident。
- [ ] **T4 平台分支 → 能力位**：`CapabilitySet` 加 `supports_ascii_keyboard`、`supports_app_launch_by_name`；
      `IOSUvcHidDriver`/`AndroidAdbDriver` 声明；替换 §0.2 G3 的 6 处。测试：`test_capability_flags.py`
      两个平台各断言一次；`grep -rn 'is_ios_device' artemis/ | grep -v ios_shortcuts` 为空。

**验收（B2）**：`grep -rn "is_ios_device" artemis/` 只剩 `agents/ios_shortcuts.py`（提示词注入本身是 iOS 专属知识）；
`low_confidence` 至少有一个真实来源（OCR 置信度）与一条"无来源时不伪造"的测试。

## 5. B3：模块归位（纯搬家，行为零变化）

**顺序（每步一提交，先小后大）**

- [ ] **T1 `reflex/vision.py` 拆分**：`changed()` → `safety/pixel.py`；三个检测器 + `PointerTracker` +
      `corridor_roi/detect_in_roi` → `perception/pointer_detect.py`（新文件）；`reflex/vision.py` 变成
      re-export shim（保留一版，下一批删）。
- [ ] **T2 `reflex/capture.py` → `perception/uvc.py`**：`UvcCapture`/`UvcStream`/`default_capture` 搬过去，
      `UvcSource` 自持 grabber；更新 6 个 import（factory、3 个 CLI、`drivers/ios/crop.py`、`tools/reflex_tool.py`）。
- [ ] **T3 `reflex/control.py` → `control/hid.py`**：`Esp32HidControl`（串口/HID 命令）搬过去；
      `AdbInputControl`/`HelperControl` → `control/app_control.py`（绝对控制）；`HidControlSink._control`
      改为构造参数而不是 `session.control` 透传。
- [ ] **T4 收敛 shim**：删除 re-export shim，`grep -rn "from artemis.reflex.vision import\|from artemis.reflex.capture import\|from artemis.reflex.control import" artemis/` 为空。

**搬家的硬要求**：只移动定义、改 import；**不改逻辑**（`git diff -M` 应显示 rename）。每步后全量回归
必须仍是 14 failed。任一步若出现逻辑改动，拆成独立 commit 并说明。

## 6. B4：控制器归位 + SafetyNet 层化

**决策 D1 先定**（见 §9）：`SessionTapController` 整体进 `safety/pointer.py`，还是拆成
`control/pointer.py`（运动规划：`_plan_segment/_advance/motion`）+ `safety/pointer.py`（预检/后检查）。
**建议拆**：SafetyNet 只做校验，运动规划属于 ControlSink 的实现细节。

**Tasks（以"拆"为例）**

- [ ] **T1 归位运动规划**：`PointerSession`（belief/home/move_abs/gain）+ `MotionConfig/MotionCalibration`
      → `control/pointer.py`；`reflex/mouse.py` 保留 shim。
- [ ] **T2 归位校验**：`_absolute_click` 的目标 crop 校验、`_verified_tap` 的指针判据 →
      `safety/pointer.py::verify_pre_pointer` / `safety/pixel.py::verify_post_action`。
- [ ] **T3 SafetyNet 入口**：`artemis/safety/net.py::SafetyNet`，方法
      `pre_target(session, target)` / `pre_pointer(session, target)` / `post_action(session, before, after, target)`
      / `incident(...)`；只读 `session.capabilities`；三个调用点（`HidControlSink.tap_abs`、
      兼容相对环、`AppControlSink.tap_abs` 复用同一入口）。
- [ ] **T4 删 shim**：`grep -rn "SessionTapController\|PointerSession" artemis/ | grep -v control/ | grep -v safety/` 为空。

**验收（B4）**：`grep -rn "verify_effect(" artemis/ | grep -v safety/` 为空；`safety/net.py` 是唯一
pre/post/incident 入口；`tests/unit/reflex` 里旧用例迁移到 `tests/unit/safety` 后数量不减。

## 7. B5：遥测/回放（§9）

- [ ] **T1 动作记录的五个字段**：`target box/confidence/source`、`precheck/postcheck verdict`、
      `explorer_tier`、`perception_sources`、`latency`（`agents/flash/runner.py::_build_action_record`、
      Pro 侧同一形状、`runtime/trace_export.py::_action_evidence`）。
- [ ] **T2 回放同源**：`runtime/action_replay.py::compare_evidence` 消费新字段（预检/后检判决），
      不再只看 `crop_change`。
- [ ] **T3 spec §4.1/§9 同步**：`ActionResult` 字段表与代码一致。

**验收（B5）**：任取一份新归档的 `steps.json`，能回答"用了哪只眼 / 置信度 / 预检判决 / 后检判决 / 延迟"；
旧归档仍能读（缺字段为 `null`）。

## 8. B6：目标候选化（§12 风险行、§2 原则 4）

- [ ] **T1 候选协议**：`PerceptionSource.locate()` 增加 `candidates()`（返回 `list[Target]`，带 source/confidence），
      `UvcSource` 用 OCR 行 + `icon_above_label` + LA 点组成候选。
- [ ] **T2 观察层暴露候选**：`mcp/observation.py` 把候选编号写进 UI list（与 Android 的 `[3] Text: …` 同形）。
- [ ] **T3 index → 坐标**：`action_executor._translate` 的 index 分支接受 iOS 候选（不再要求 UI 树）。
- [ ] **T4 提示词**：Flash/Pro 的点击指令改为"优先给 index；给坐标时必须给 target_description"。

**验收（B6）**：同一帧重复观测给出同一候选集（编号稳定）；点击带 index 时 `normalized_coordinates`
来自候选框中心而不是模型数字；`target_unverified` 在该路径上不再出现。

---

## 9. 需要你决定的事（阻塞对应批次）

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| **D1** | `SessionTapController` 归位形态 | (a) 整体搬 `safety/pointer.py`（spec 字面）／(b) **拆**：运动规划→`control/pointer.py`，校验→`safety/pointer.py` | B4 的任务边界；影响 SafetyNet 是否只做校验 |
| **D2** | `skills/` 位置 | (a) 搬进 `artemis/skills/tetris`（spec 路径表）／(b) 改 spec 指向仓库顶层 `skills/` | 打包与 import；影响 B3 的 T1（skill 加载路径） |
| **D3** | 是否引入 `DecisionProvider` 协议 | (a) 引入（把 skills 的 policy 抽成接口）／(b) 不做，改 spec §3 图 | B6 之后是否还有一批 |
| **D4** | 平台差异字段化 | 新增 `supports_ascii_keyboard` / `supports_app_launch_by_name` 是否可接受 | B2 的 T4 是否做 |
| **D5** | B6 的时机 | 现在做／B2 后立刻／放到 B5 之后（按本计划） | 直接影响"模型坐标弱"这一实测风险 |

## 10. 进度台账与验收口径

- 进度文件：`.superpowers/sdd/2026-11-06-target-architecture-migration/progress.md`（每任务一行：commit / 测试数字 / 回滚点）。
- 每批收尾：全量回归 + `ruff` + ratchet，打 tag `<mmddhhmm>-<batch-topic>`，按 `docs/branch-plan.md` §4 并入
  `integration/10101205-all-fixes`。
- 复盘改进项仍只在 `docs/improvement-backlog.md` 维护；本计划只负责架构迁移，不重复登记复盘结论。
