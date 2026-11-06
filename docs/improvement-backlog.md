# 改进项台账（唯一事实源）

两份复盘报告是**历史快照**，不再回改（它们是本地未入库文件：`reports/复盘-20261009-获取电池信息测试.html`、
`reports/复盘-20261009-搜狐视频搜索大鹏.html`，质量门 `~/.dsh/skills/writing-run-retrospectives/tools/verify_report.py`）。
**状态只在本文件更新**：每落地一项就改状态、写证据（commit / tag / 路径）、补缺口；报告里的决策列可能滞后，以本文件为准。

- 项数：搜狐复盘 8 项 + 电池复盘 10 项，去重 2 项（搜狐 I3 ≡ 电池 I3、搜狐 I7 ≡ 电池 I8，两对内容完全相同）= **16 个唯一改进项**
- 现状：已完成 **6** / 部分完成 **5** / 未开始 **5**
- 口径提醒：搜狐 I8 与 电池 I5 **是重叠而非同一项**（电池 I5 只覆盖保活探测缓存，搜狐 I8 另含录屏等待），故两者各自计一项、共享同一段剩余工作；同理 电池 I4 的 I4b/c/d 是它的剩余缺口，不另立条目
- 知识沉淀 6 项：已沉淀 **2** / 待定 **4**
- 状态词：`已完成`（验收通过且已并入整合分支）｜`部分`（列出剩余缺口）｜`未开始`｜`不采纳`｜`已实现·待提交`（代码/测试/复算通过，但仍是未提交的工作区改动；不满足「已并入整合分支」口径，提交后须改成 `已完成` 并补 commit/tag）

## 1. 已完成（6）

| ID | 改进项 | 落地证据 |
|---|---|---|
| 搜狐 I1 | 分支基线纪律 | 364853f（`docs/branch-plan.md`，命名规范 + 基线纪律，后续 533e486 补 fork 工作流 / d250275·c367ace 补标签表）· skill `knowledge/branch-base-regression.md` |
| 搜狐 I2 | **运行开始即声明代码身份**（原方案"make 断言目标 + 关键修复清单"已改形，见 §4） | 6a04113（tag `10101507-run-code-identity`）· `artemis/runtime/run_identity.py` · 真机：flash/pro 会话目录均有 `run_identity.json`（flash 此前为零记录）、严格模式 2 s 拒绝且无设备动作 |
| 搜狐 I3 ≡ 电池 I3 | 静默降级写入运行结论 | 7b2a0e9（tag `10101422-stale-observation-and-degradations`）· `artemis/runtime/degradations.py::build_run_environment` · 真机 verdict `environment.degradations` + `code` |
| 搜狐 I5 | 陈旧观察度量（观察→派发间隔） | 7b2a0e9 · `artemis/runtime/observation_clock.py` · 真机 `observation_skew_s` step1 3.144 s / step2 6.364 s |
| 搜狐 I7 ≡ 电池 I8 | trace 归档可复算 | e5b624b（tag `10101444-trace-archive-export`）· `artemis/runtime/trace_export.py` · 真机归档 = `recording.mp4` + `trace.gif` + `steps.json`（3 行 = DB 3 步）+ `images/`（3 硬链接）+ `notes/`（3 文件），无悬空引用；`trace list` 显示 `📋 Steps`、`trace view` 打出真实动作 |
| 电池 I1 | provider 感知 lens | 72e0c5d（tag `10091400-provider-aware-lenses`）· 66 个用例转绿 |

## 2. 部分完成（5）

| ID | 改进项 | 已完成 | 剩余缺口 |
|---|---|---|---|
| 电池 I4 | adb 探测短超时 / 轻量命令 / "慢≠不存在"（I4a），另含 I4b/c/d | I4a：f7e2844（tag `10091400-helper-probe-deadline`）· `PROBE_TIMEOUT_SECONDS = 6.0` + `pm list packages --show-versioncode` + 探测无答不再触发重装 | **I4b** 固定 30 s 不重试窗口未做；**I4c** 口径未定（`run_outcome.environment` 已有 `backend`/`switches`，但"探测超时"这一事件没有专门计数种类）；**I4d** 一次运行内层级来源切换上限上报未做 |
| 搜狐 I8 | 环境降级缓存 + 录屏条件等待 | 录屏**早已**是条件等待：`artemis/utils/video.py::await_scrcpy_first_frame`（等 `Recording started` 标记、6 s 上限、超时按首帧回落），该函数来自 351ca84，**早于复盘** | ① 报告"固定等待 6 s"的前提需更正（真因：该 ROM 下标记从不打印，于是每次都吃满 6 s 上限）；② 保活能力探测缓存未做（与 电池 I5 同一段工作） |
| 电池 I5 | 保活能力探测一次并缓存（`set-wakelock` / USB stay-awake 不支持即跳过并记录） | 现仅每次会话重试后回落 5 s 心跳（`artemis/runtime/awake_service.py`，无缓存）——本项即 搜狐 I8 的第二半 | 探测结果按设备 + 系统版本缓存；失效条件（换机/升级）需明确 |
| 电池 I9 | 用例模板两个必填字段（前置状态 + 验收口径） | 用例侧：`tests/e2e/test_device_battery_level.py`（tag `10091711-e2e-battery-level-case`，`GOAL`/`PRECONDITION`/`ACCEPTANCE` 三件套） | 产品侧（非测试入口）未强制 |
| 电池 I10 | 运行前状态复位 | 用例侧：同一文件 `_reset_to_home()` 运行前按 HOME | 产品侧未做 |

## 3. 未开始（5）

| ID | 改进项 | 现状证据 | 成本/风险 | 建议 |
|---|---|---|---|---|
| 电池 I7 | 裁决匹配归一化；无裁决不得静默填 `inconclusive` | `artemis/agents/checker/checker.py:462` 仍是 `(kind, item_text)` 精确匹配 | 低 / 低 | **建议下一批**：直接影响每次运行的 passed/inconclusive 口径 |
| 搜狐 I4 | 障碍类目标"已不存在"的判定 + 校验预算绑定目标类型 | 只有分类 `TARGET_DISAPPEARED`（`artemis/agents/validator/categories.py:27`），无判定策略 | 中 / 中 | 建议做，**但语义必须是"取消"**，见 §4 修正 #1 |
| 搜狐 I6 | 空步不入库或显式标 `no_action` | 全仓无 `no_action` | 低 / 中（步号变化需过回放断言） | 建议做 |
| 电池 I2 | 启动前 provider 凭据预检，缺失即快速失败 | 无预检；缺失时白付 helper 安装 + 录屏（10-09 那次 12 s 内崩） | 低 / 低 | 建议做 |
| 电池 I6 | plan ledger gate 与操作器原子化 | `artemis/agents/operator/operator.py:768` 仍退回动作重发 | 中 / 中 | 建议观察 |

### 3.1 新增候选（2026-10-10 观察，待决策）

| ID | 观察 | 证据 | 影响 |
|---|---|---|---|
| N2 | **两条 lens 的角色命名空间错配**：`is_utils=True` 传给了 agent 角色 `summarizer`（4 处调用点），导致视觉步骤摘要与步骤胶囊自 2026-10-09 17:11 起对所有运行失效、且不进结论 | 复盘 `reports/复盘-20261010-字体与显示大小两次运行.html`；18 个会话 / 40 条 trace 命中 `LLMConfigUtils`；已修：见 §7 | 已落地（I1/I2/I4），见 §7 |

| ID | 观察 | 证据 | 影响 |
|---|---|---|---|
| N1 | **裁决证据与设备捕获矛盾**：终检 `passed=3/failed=0`，evidence 写"Final screen shows the Settings Battery page"，而当轮捕获只有 6 个节点（仅状态栏时钟），电池页从未打开；47 s 后重跑同一用例即通过 | 会话 `f9e2eedb`：三次捕获节点数 32（桌面）→ 31（设置主页）→ **6（仅状态栏）**，全程无百分比；对照通过那次 `78488e8f` 为 32 → 31 → **21（电池页 + 83%）**；两次 run_outcome 均为 completed | 结论可信度：裁决不能与设备捕获矛盾，evidence 应可对照当步 UI 树复核；用例判据目前只看最后一次捕获 |

## 4. 修正记录（决策被后续证据或人工判断推翻）

| 日期 | 对象 | 原表述 | 修正后 | 依据 |
|---|---|---|---|---|
| 2026-10-10 | 搜狐 I4（及知识 K3） | "目标已不存在时视为**已完成**而非失败重试" | "目标已不存在 → **取消（moot / 退役）**：不是完成，也不再失败重试" | 计划语法只有四态 `[ ]`/`[/]`/`[x]`/`[!]` 且"任务终止仅当所有顶层里程碑为 `[x]`"（`artemis/utils/plan_grammar.py:42-46,444`）——标 `[x]` 会**伪造完成并喂给终止判定**；仓库已有第三态先例 `retire_check_items`（计入 `retired`、撤回 findings、从不计 passed，`artemis/graph/checkpoints.py:549`）。用户 2026-10-10 决定 |
| 2026-10-10 | 搜狐 I8（前提） | "保活失败 + 录屏固定等待 6.0 s" | 录屏自 351ca84 起即为 **marker 条件等待**（6 s 上限 + 首帧回落）；真因是该 ROM 下标记不到达，故每次吃满上限 | `artemis/utils/video.py::await_scrcpy_first_frame`（`git log -S await_scrcpy_first_frame` → 351ca84，早于复盘） |
| 2026-10-10 | 搜狐 I2（方案改形） | "make 断言目标 + 关键修复在场性清单" | "运行开始记录身份（版本 / 未提交路径 / provider 档位），两种 profile 都写；清单交给测试" | 清单随下一个修复腐烂（报告自身风险列）；且会与 `run_outcome.environment.code` 形成第二事实源。落地 6a04113 |

## 5. 知识沉淀（6）

| ID | 内容 | 目标文件 | 状态 |
|---|---|---|---|
| 搜狐 K1 | 分支基线回归的识别与预防 | `knowledge/branch-base-regression.md` | ✅ 已沉淀 |
| 搜狐 K3 | 陈旧观察竞态：障碍类目标"已消失"如何判定 | `knowledge/stale-target-races.md` | ✅ 已沉淀（2026-10-10，语义为"取消"版） |
| 搜狐 K2 | 运行前代码身份：只留一套机制、清单会腐烂 | `knowledge/assert-code-identity.md` | ⏳ 内容已定（I2 改形版），待写 |
| 电池 K1 | 无凭据环境下的静默降级谱系（启动崩 / transcript 回退 / 视觉摘要失败） | `knowledge/no-google-provider-degradation.md` | ⏳ 待写 |
| 电池 K2 | 运行取证速查（`payload.message` 才是真相；`check_ledger` inconclusive 语义；归档为空时读 DB） | `knowledge/artemis-run-forensics.md` | ⏳ 待写 |
| 电池 K3 | 时间线静默间隔判据（>15 s 或 >3× 中位数即单列成条） | `knowledge/timeline-gap-detection.md` | ⏳ 待写 |

知识目录索引：`~/.dsh/skills/writing-run-retrospectives/knowledge/INDEX.md`

## 6. 数据缺口与口径问题

1. `archive_incomplete`（I7 的降级路径）**只有单测覆盖**，真机未触发过——需要一次"有步骤但无截图"的运行才能观测。
2. I7 的**历史回填未做**（用户决定暂缓）：10-09 那两个归档仍无法自证。
3. 电池 I4c 的计数口径未定：`backend`/`switches` 已在 verdict 里，但"探测超时"是否算一次降级、算哪种，未定。
4. Flash profile **按设计不写 `run_outcome`**（响应式循环），因此它的代码身份只能来自 `run_identity.json`（I2 已覆盖）；凡涉及"结论里的字段"的改进项，都要单独确认 Flash 是否有落点。
5. `reports/` 为本地未入库文件，报告质量门需本地运行；本台账入库，二者引用关系记在 `docs/branch-plan.md` 与本文开头。

## 7. 2026-10-10 复盘（字体与显示大小两次运行）落地记录

来源：`reports/复盘-20261010-字体与显示大小两次运行.html`（本地未入库）。该轮 I1/I2/I4 经用户决定落地，I3/I5/I6/I7 仍待决策。

| 报告项 | 内容 | 状态 | 证据 |
|---|---|---|---|
| 报告 I1 | 修 4 处 `is_utils` 命名空间错配 + 用真实 `LLMConfig` 的单测 + `_resolve_endpoint` 报出命名空间 | ✅ 已完成 | 6791459（tag `10101728-lens-namespace-and-visibility`）· `artemis/services/llm.py`、`artemis/agents/flash/summarizer.py`、`artemis/memory/chunking.py` |
| 报告 I2 | 构造期记录 `lens_unavailable` 降级 + 会话目录落 `run_capabilities.json`（收尾 `finally` 刷新） | ✅ 已完成 | 6791459 · `artemis/runtime/run_capabilities.py`、`artemis/runtime/degradations.py`、`artemis/sdk/agent.py` |
| 报告 I4 | lens 不可用即短路：`job_can_succeed` 钩子 + `StepCapsuleLens.usable` 一次探测缓存 | ✅ 已完成 | 6791459 · `artemis/memory/step_memory.py`、`artemis/memory/chunking.py`、`artemis/agents/flash/summarizer.py` |
| 报告 I3 | 把 WARNING 计数纳入真机验收 | ⏳ 待决策 | — |
| 报告 I5 | 运行前置状态复位（产品侧，与 电池 I10 同一项） | ⏳ 待决策 | — |
| 报告 I6 | 保活探测缓存 + scrcpy 等待策略（与 搜狐 I8 / 电池 I5 合并） | ⏳ 待决策 | — |
| 报告 I7 | Flash 写最小结论文件 | ⏳ 待决策 | — |
| 复盘外发现 | e2e 用例按 mtime 通配归档目录会撞上未 rename 的临时目录 → 改为「必须带 STATUS 标记 + 有界等待」 | ✅ 已完成 | ff2aec0 · `tests/e2e/test_device_battery_level.py` |

真机验收（PEMM00，提交版 `6791459`+`ff2aec0`，`make test-case-battery` 两 profile 全绿，116 s）：
* 摘要恢复：`steps.summary` + `summary_status=ready` / `source=visual_transition`（flash 2/3、pro 5/6；末步无动作属预期）；
* 两会话目录均有 `run_capabilities.json`：`step_summarizer.available = true`；
* `LLMConfigUtils` / `no step-summarizer lens` / `Giving up on Step` 在两次日志中计数均为 0（复盘分析的那两次为 8 / 60 / 16）。

## 8. 2026-11-03 复盘（美团珍珠奶茶询价）落地记录

来源：`reports/复盘-20261103-美团珍珠奶茶询价.html`（本地未入库快照）。状态以本节为准，报告里的决策列保持交付时的“待决策”。

| 报告项 | 内容 | 状态 | 证据 |
|---|---|---|---|
| I1 | 失败分支补齐证据并透出 incident | ✅ 已完成 | 0ec5c07 · `artemis/mcp/actuators/adb.py`（click / long_press / click_sequence 失败分支带 `metrics`）· `third_party/mobile_use/controllers/unified_controller.py`（错误串附 `· incident=<code>`）· `tests/unit/mcp/test_actuators.py`（失败 ActionResult 同时有 incident + metrics，消息含 incident） |
| I2 | 启动验收改成“打开的是不是目标 App” | ⏳ 待决策 | — |
| I3 | 中文应用名自动映射（中文→拼音表） | ⏳ 待决策 | — |
| I4 | 键盘输入后回读校验（`type_text` 后核对搜索框文本） | ⏳ 待决策 | — |
| I5 | 失败点击返回“落点 + 最近可点元素” | ⏳ 待决策 | — |
| I6 | 跑业务任务前强制四段门禁并把证据写进运行目录 | ⏳ 待决策 | — |
| I7 | 比价类目标加结账口径 | ⏳ 待决策 | — |
| I8 | “可复现”成为交付前门禁 | ⏳ 待决策 | — |

说明：I1 之前，失败步的 `action_metrics` 恒为空（`incident` 有、数字没有），模型只看到一句 “Tap failed”；现在两者都在，模型侧消息形如 `Tap failed at (540, 1200) · incident=pointer_not_centered`。

## 9. 2026-11-04 复盘（淘宝闪购珍珠奶茶，失败运行）落地记录

来源：`traces/milk-tea_FAIL_2026-11-04T15-52-33/steps.json`（34 步，16:02 报告失败；数据库 `steps` 表含每步 tool result/metrics）。

### 发现

1. **manage_app 乐观成功**：step 1/15 返回 `Opened 'taobao' through the iOS keyboard launcher.`，屏幕并未变化；step 30 才返回 `no_screen_change`。启动器只按"整帧变化"判定，而 Spotlight 打开/关闭本身就构成变化。
2. **绝对路径被误判不可用**：step 5/6/7 的 tap 全是相对分段（`ballistic,approach,fine...`）；能力探测用第一次观测的误差判定，一次假阳性就让整个会话退回相对路径。
3. **指针检测假阳性**：step 11/18 各 12 段/13 次观测（撞 `max_segments`），两次检测到的"指针"位置完全相同（screen `618.04,486.65`），最终误差 234/379px——蓝环检测器锁在图标上的静态蓝环/FKA 焦点框上。
4. **坐标偏**：step 5 目标 `[79,165]`（归一化）→ 帧 x≈35，而淘宝图标在帧 x≈45–100，点在了图标左侧/指针上。工具已支持目标框，Agent 用了自由坐标。
5. **锁屏后盲跑**：15:56 锁屏，15:58 画面冻结（时钟停在 15:58），16:02 黑帧；`amove` 返回 `ERR abs mouse not ready`，后续 move/swipe 全部无效。

### 落地

| 发现 | 修复 | 证据 |
|---|---|---|
| 2、3 | 绝对路径验证通过后不再依赖指针检测：`_absolute_click` 一步移动 + 点击 + **目标区域变化**验证；探测失败保持"未探测"（不再整会话禁用绝对）；相对路径加静止幻影守卫（连续两次观测位置不变 → `pointer_static`，不再烧 12 段） | `artemis/reflex/mouse.py`；`tests/unit/reflex/test_pointer_session.py`（快路径 `observations=0`、`pointer_static` ≤4 次观测） |
| 1 | 启动器先按 Escape 收掉残留 Spotlight，再要求 ≥2% 像素变化；新增"同屏不算启动"测试 | `artemis/reflex/ios_apps.py`；`tests/unit/reflex/test_ios_apps.py` |
| 4 | 记录为 Agent 坐标问题：Siri 建议行应取图标中心（工具已支持 target 对象）；未改代码 | 本次 trace |
| 5 | 记录为后续项：iOS 运行保活（无副作用按键）与 `suspended/锁屏/黑帧` 快速失败 | 待办 |

说明：第 8 节的 I2（启动验收）由本节的启动器修复覆盖。

## 10. 2026-11-05 复盘（milk-tea-locate，Flash / 49 步 / cancelled）落地记录

来源：`reports/复盘-20261105-milk-tea-locate.html`（本地未入库快照，8 问题 / 4 根因 / 9 改进项，决策列交付时全为「待决策」）；原始轨迹 `traces/milk-tea-locate_FAIL_2026-11-05T15-37-55/`（49 步 / 7m54s / `cancelled`）；`traces/data_engine.db` session `4bcb2cc0-0c67-4a79-8001-3aa24907107e`（739 条 trace，其中 470 条 `type=log`）。状态以本节为准。

**提交状态**：本节 3 项的代码与测试已并入 `1ec5348`（2026-11-05，`feat/new-project`），故按文首口径记 `✅ 已完成`；本台账的登记为随后一笔仅文档提交。`config/artemis.jsonc` 的改动在该运行发生前就存在，与本节无关，未纳入提交。

### 10.1 决策校正（逐帧 + 原始轨迹核对后，含一条用户更正）

| 报告项 / 结论 | 核对结论 | 依据 |
|---|---|---|
| I1「服务点与模型点距离 >300 时记录冲突并保留模型点」 | ❌ **不采纳**：本次 27 次 `target_semantics.locate_anything` 中距离 >300 的 14 次，仅 2 次 `no_effect`（step6 488、step17 452）+ 1 次选错行（step47 536），其余 11 次是正确纠正（搜索按钮 481、筛选 502）。按距离自动回退会把正确纠正退回旧问题 | `traces/data_engine.db`；改形为「记录冲突字段 + 候选 box / 描述唯一性」，见 ④ |
| I3「`press_named_key(BACK)` 直接返回不支持」 | ⚠️ **前提错误，别重复实现**：`artemis/reflex/control.py` 已 raise `ValueError`，`artemis/control/hid.py::press_key` 已转 `incident=unsupported_key`，playbook §1 也已登记。真因是 `AdbActuator.press_key` 丢弃 `go_back()` 的 `bool` 并无条件 `ActionResult.success`——DB 里 4 步结果都是 `Pressed key 'BACK'.` | 见 ① |
| I4「pre/post dhash 相同即 no-op」 | ⚠️ **判据需改形**：整帧 dhash 会漏掉 step17 这类「整帧微变、目标 crop 不变」；现成判据是 `incident=no_effect` + `action_metrics.crop_change`（step6=0.0 / step17=0.009 / step30=0.53 / step45=0.39）。另需 wait/加载白名单（step43 等 3 s 是合法静止）与「帧已变则否决 `crop_change`」（弹层可在别处打开） | 见 ⑤ |
| P4「蓝环是选中标记」/「观察帧去掉蓝环污染」 | ✅ **用户 2026-11-05 更正，硬断言撤回**：蓝环是 AssistiveTouch 鼠标指针，iOS 操作逻辑是先把蓝环移到目标 UI 再点击；`BluePointerDetector` / `check_point` / `travel_verified` 都依赖它，不能从观察链路去掉，也不能禁止描述它 | 真缺口是 `verify_effect` 缺「输入 overlay 不算 UI 变化」的概念：`DEFAULT_MIN_CHANGED_FRACTION=0.01`，而 240×240 crop 里蓝环占 1.03%–1.28%；step6 crop diff 0.0404 → 屏蔽环后 0.0001、step17 0.0074→0.0013。见 ③ |
| 「Flash 不写 `run_outcome`」「scrcpy 失败」当缺陷 | ❌ 不采纳：§6.4 已记 Flash 按设计不写；iOS 无 ADB 导致 scrcpy 失败亦非缺陷 | 不为这两项写代码 |
| 归档自证缺口（复盘外发现） | ✅ 采纳：`Task cancelled` 与两处 LocateAnything 超时（10017 ms / 4003 ms）只存在于 `step_id=None` 的 `type=log` DB 行；归档既无 `status.json` 也无取消原因，`incident` / `action_metrics` 虽在 DB 却未进 `steps.json`。`status.json` 走 `_finalize_tracing`，Flash 同样命中，正是 §6.4 要求「单独确认 Flash 落点」的落点 | 见 ⑥ |

### 10.2 首批落地（3 项）

| 批次项 | 覆盖的报告项 | 内容 | 状态 | 证据（路径 · `1ec5348`） |
|---|---|---|---|---|
| ① `go_back()` 返回值透传 | 11-05 I3 · 11-04 I2 | back/home/enter 分支保留 `bool` 返回值；仅 `back` + `is_ios_device(ctx)` 失败映射 `ActionCode.UNSUPPORTED` / `incident=unsupported_key`，并给出 escape / 左上返回箭头 / home 三条替代路径；HOME/ENTER 与 Android BACK 失败仍是通用 `DEVICE_ERROR`。理由：`IosUvcHidDriver.press_key` 把 `HidControlSink` 的 `ActionResult` 压成 `.ok`，`incident/detail` 已丢，只能在 actuator 边界恢复 | ✅ 已完成 | `artemis/mcp/actuators/adb.py::press_key` · `tests/unit/mcp/test_actuators.py`（+4 例，用真实 `HidControlSink` + 真实 `UnifiedMobileControllerBase`，只 double 固件 `press_named_key`）· TDD RED 3 failed/3 passed → GREEN 25 passed |
| ⑤ Flash 跨步 no-op 守门 | 11-05 I4 · 11-04 I4 | 纯逻辑 `NoOpTracker` 折叠 `status` / `incident` / `crop_change` / pre-post dHash：2 次 warn（换策略提示）、3 次有界中止；`WAIT_ACTIONS` 既不计入也不清除已有停滞；`SAME_SCREEN_MAX_DHASH_DISTANCE=2`。runner 把 `no_op_consecutive` 盖进 step、warn 走临时 tail notice、abort 给 failed 终报 | ✅ 已完成 | 新增 `artemis/agents/flash/no_op.py`、`tests/unit/agents/test_flash_no_op.py`；改 `artemis/agents/flash/runner.py`、`tests/unit/agents/test_flash_runner.py` |
| ⑥ 归档证据链（复盘外发现） | §6 归档自证缺口 | `get_traces_for_session(..., latest_first=True)` 读到 `step_id=None` 日志行并从尾部扫 WARNING；`steps.json` 每行补 `evidence`（`incident` / `action_metrics` / `no_op_consecutive`，兼容 dict 与 Pro 的 list 两种形状）；`write_run_status()` 写 `status.json`（终态 / 取消原因 / 起止 / 时长 / 步数 / WARNING / degradations，全程 best-effort）；`_finalize_tracing` 在 manifest 之后调用（该处在 `finally` 里，`CancelledError` 路径同样走到） | ✅ 已完成 | `artemis/data_engine/storage.py`、`artemis/runtime/trace_export.py`、`third_party/mobile_use/sdk/agent.py`；`tests/unit/data_engine/test_engine.py`（真实 SQL 尾部顺序）、`tests/unit/runtime/test_trace_export.py`、`tests/unit/sdk/test_agent_trace_finalization.py` |

⑥ 先于 ⑤ 落地：⑤ 消费的 `incident` / `action_metrics` 正是 ⑥ 才开始落盘的证据。§9 未枚举 11-04 报告的 I1–I9，故 11-04 I2 / I4 的覆盖关系在此登记（该报告 `reports/复盘-20261104-milk-tea-locate.html` 的决策列仍为「待决策」）。**① 的失败语义随后被 §10.5 取代**：BACK 已升级为可执行手势，不再是 `unsupported_key`；上表保留为 `1ec5348` 当时的历史快照。

### 10.3 验收与真实数据复算

- 定向 6 个测试文件 **96 passed**（含测试改名后的最终版本复跑）；`ruff check` 全过，`ruff format` 只作用于本次改动的文件。`artemis/agents/flash/runner.py:339` 的 format 偏差在 HEAD 里就存在，未顺手改以免混入无关 diff。
- 全量 `uv run pytest tests/unit -q`：**14 failed / 3476 passed / 9 skipped**；14 项与 P0 ① 后基线完全相同（`admin_console/test_model_service` 2、`agents/test_explorer` 9、`agents/test_video_analyzer` 3），无新增回归。
- 仓库质量门 `scripts/quality_ratchet.py` 在**提交前的 HEAD 就已失败**：`broad_exception_handlers 761 > 754`、`type_ignore_comments 20 > 18`（`.quality-baseline.json` 最后更新于 `351ca84` / 2026-09-28）。逐个核对本次 6 个生产文件，两项计数相对 HEAD 增量均为 **0**，故未改基线、未绕过该门禁；如 CI 因它变红，属既有仓库级欠账，与本节三项无关。
- ⑥ 用真实 DB 端到端复算 `write_run_status`：`status=cancelled / steps=49 / duration_s=474 / 6 条 WARNING`，含 `Task cancelled`、两处 LocateAnything 超时、scrcpy 失败、run_identity 未提交路径。此前这些只存在于 DB，归档无法自证。
- ⑤ 的真实效果**如实记录**：4 次 BACK 的 pre/post dHash 距离 = 0/1/0/1，真实画面变化 = 24–32，容差 2 标定正确；**但那 4 次被真实变化隔开，全 49 步最长连续簇只有 2（step 5–6、39–40）⇒ ⑤ 只会 warn 两次，不会中止、救不回 11-05 那次运行**。测试 `test_a_stalled_loop_of_silent_backs_stops_on_the_third_turn` 是假设场景而非回放；原夸大测试名已改。
- 验收中一并修掉：abort 终态说明覆盖已排队的换策略提示；`status.json` 原本按 `ASC LIMIT 500` 只扫最旧日志（15:37 已有 470 条 log，更长运行会丢掉尾部取消 WARNING），改为从尾部读并补 1000 条 log 回归用例。

### 10.4 待决策 / 未落地

| 批次项 / 报告项 | 内容 | 状态 |
|---|---|---|
| ② | LocateAnything 超时/预热统一：`mcp/action_executor.py` 10 s（本次 10017 ms 仍超时）vs `devices/ios_uvc.py` / `perception/uvc.py` / `tools/explorer_tool.py` 默认 4 s；连续 2 次失败触发 60 s cooldown；预热后稳定 830–988 ms | ⏳ 待决策（对应 I2） |
| ③ | 保留蓝环语义的 `verify_effect(..., pointer_point=...)` + `POINTER_RADIUS_PX=20` 膨胀圆掩码，产出 `crop_change_excl_pointer`；配套基线时序对齐（`_absolute_click` 的 `before` 取在 `move_abs()` 之前会混入指针就位）。`append_ios_shortcuts` 固定声明改为「蓝环是鼠标指针、不代表 UI 选中态；选中态只认橙色文字 / `查看(已选N)` 计数 / 勾选框填充」+ 结构化 `pointer_at` | ⏳ 待决策（用户更正后的形态）。影响面：step28/30/45/47 排除环后仍 0.121/0.594/0.548/0.067，判定不翻；只影响 step6/17 这类边界 case，属边界正确性 + 证据可信度，救不回本次任务 |
| ④ | `action_executor.py` 定位冲突字段（`point` vs `model_point`、候选 box、描述唯一性）+ 重复按钮按 OCR 商品名行绑定（step47：服务点 y=500 是蜜桃四季春、模型点 y=648 才是珍珠奶茶，x 都对） | ⏳ 待决策（对应 I1 / I6） |
| I5 / I7 | 任务定义澄清（起送价 / 配送费 / 券后预估哪个算「便宜」、是否允许凑单到起送价、什么算走到支付页、起始页面）；筛选 apply 后校验 chip 与结果是否真变化 | ⏳ 待决策。**§8 I7、11-04 I7、11-05 I5 已三次提出同类项，不要开第四份散文台账**；起送价知识已写在 `skills/ios-taobao/README.md` 但全仓无代码加载，活通道是 `append_ios_shortcuts` |
| I6 | 选规格定位优先点商品名/行（唯一），描述里带商品名 | ⏳ 待决策（与 ④ 合并） |
| I8 / I9 | `manage_app` 后校验前台/页面、任务开始先回首页或清理搜索页（本次继承了上次的「鸡排」状态）；闪购搜索落到盒马店铺列表时先返回首页再搜 | ⏳ 待决策 |
| K1–K3 / D1 | LocateAnything 边界、模型服务冷启动、带选择状态控件校验、定位/重复动作审计工具 | ⏳ 未沉淀 |
| 观察项 | 视觉摘要幻觉（step3 post 明显是 iOS 主屏，摘要写 "Android home screen"）→ 摘要输出不宜直接当状态判据；106 次 LLM 调用 / 854,569 prompt tokens / 80.3% 命中，省时关键是减少步数而非优化抓帧 | 记录，未落地 |

### 10.5 追加（同日）：iOS BACK 从「拒绝键」升级为左边缘右滑手势

用户更正 / 新需求：「back 可以通过鼠标放在左边向右侧滑动来实现，补齐 iOS 这个操作能力」。① 只做到**不撒谎**（把静默失败报成 `unsupported_key`），并未补齐能力；本节把 BACK 做成真动作，并**取代 10.2 ① 的失败语义**。状态：`已实现·已真机验证·待提交`（真机证据见下，代码尚未入库）。

| 落点 | 内容 |
|---|---|
| `artemis/control/hid.py` | 新增 `BACK_SWIPE_END_RATIO=0.75` / `BACK_SWIPE_Y_RATIO=0.5` / `BACK_SWIPE_DURATION_MS=500`；`go_back()` 从物理左边缘 `x=0, y=0.5h` 起手向右拖过 1/3 宽度完成阈值；抽出 `_drag()` 与 `swipe()` 共用按下 / 分段 / 松开；`_place_for_drag(..., y=...)` 让边缘手势绕过「安全锚点」规则（其它拖动仍避边缘）；`frame_size` 未知直接 `incident=swipe_failed` 拒绝盲发 |
| `artemis/drivers/ios/ios_uvc_hid_driver.py` | `press_key("back")` 大小写 / 空白不敏感，绕过 HID 键盘直接派发 `sink.go_back()`；其余按键仍走 `sink.press_key()`；无 `go_back` 的 sink 保留旧回退 |
| `artemis/mcp/actuators/adb.py` | ① 的 `bool` 返回值透传保留；成功文案改为 `Went back with the iOS left-edge swipe gesture.`（不再说 `Pressed key`）；失败改为 `DEVICE_ERROR` + escape / 返回箭头 / home 三条替代路径、`incident=None`（能力已存在，不再是 `UNSUPPORTED`） |
| `artemis/agents/ios_shortcuts.py` · `docs/ios-rig-playbook.md` | 活提示词与 canonical 手册同步：BACK 是左边缘手势、不是键盘命令；新增决策规则 8；playbook §1 / §2 / §2.5 三处修正；`tests/unit/test_ios_rig_playbook.py` 同时钉住代码常量与两个活通道，防止只写散文 |

**验收**：定向 4 文件 RED `12 failed / 69 passed` → GREEN **81 passed**；全量 `uv run pytest tests/unit -q` = **14 failed / 3487 passed / 9 skipped**，14 项与 10.3 基线逐项相同、无新增回归（仍由 `config/artemis.jsonc` 既有改动引起：`provider` 被本地改成 `openai`，而 `test_model_service` 断言 `google`）；`ruff check` 全过，`ruff format --check` 仅 `tests/unit/drivers/test_ios_uvc_hid_driver.py:294` 的 HEAD 既有偏差未顺手改动。质量门两项计数相对 HEAD 增量仍为 0（`_drag` 的两个 `except Exception` 由 `swipe` 中原有两个平移而来）。

**真机证据（2026-11-05 22:46）**：`traces/ios-back-gesture_PASS_2026-11-05T22-46-02`（session `27e9ccc3-5043-4e28-a50a-a3a905bd8c75`，7 步 / `53.379s` / `completed` / 2 WARNING / 0 degradations）。step 6 `action_taken={"action":"press_key","args":{"key":"BACK"}}`，`last_execution_result={"status":"dispatched","result":"Went back with the iOS left-edge swipe gesture."}`；截图「通用」子页 → 「设置」主页，pre/post dHash `696d55f25ac87e78 → 6971747262747265`。固件日志 `amove 0 500` OK → `mswipe 337 0 500` OK（819ms），帧 449×974：`337 = round(449×0.75)`、`500ms` 与 `BACK_SWIPE_END_RATIO` / `BACK_SWIPE_DURATION_MS` 完全对上。全 session `unsupported_key` / `swipe_failed` / `Pressed key 'BACK'` 旧失败标记计数 = 0。**这是未提交工作树上的验证**（run identity WARNING：23 个未提交路径 / revision `23a927a` … exists in no commit），不是已发布 commit 的验证；剩余置信缺口是 1/1 成功、未做 10 次重复。本场顺带反证 ⑤/⑥：step2 `evidence.incident=pointer_static` / `no_op_consecutive=1`；step5 `action_metrics.crop_change=0.2880`；`status.json.warnings` 正是 ⑥ 的 `get_traces_for_session(..., latest_first=True)` 从 `step_id=None` 的 `type=log` 行扫出来的两条。step2 的 `pointer_static` 是 §9 已登记的蓝环检测假阳性（静止幻影守卫按设计拦下、未点击，run 随后自行恢复），不新增代码。

环境备注：根目录 10 个 4 字节、内容为 `blat` 的探针残留（`1vkf_dgm`、`4l2_qjgg`、`63ygnb24`、`cs07j09y`、`em_nw6pk`、`epqcwxa1`、`evofy2cm`、`k6ea2xp9`、`xrw87kg5`、`y4zxwbul`）是 Security Center 间歇性拦截 TEMP 时 Python `tempfile` 写探针产生的，非业务文件，**未清理**（destructive 操作待确认）；未通过改 `TMPDIR` 等方式绕过该限制。

### 10.6 追加（同日）：iOS 平台不再尝试 scrcpy 录屏

来源：10.5 真机轨迹里的两条 WARNING 之一（`scrcpy failed to start … Could not find any ADB device`）。iOS rig 走 UVC + ESP32 HID，本就没有 ADB，`start_video_recording()` 不该为它起 scrcpy 子进程。

| 落点 | 内容 |
|---|---|
| `artemis/controllers/unified_controller.py` | `start_video_recording()` 在 `device_id` 之后先判 `mobile_platform == "ios"`，直接返回 `VideoRecordingResult(success=False, message="iOS is reached over UVC + HID, not ADB; scrcpy recording is unavailable")`；不再进入 mock/ADB 分支，也不会 spawn 子进程 |
| `tests/unit/test_unified_controller_video.py` | 新增 `test_unified_controller_skips_scrcpy_on_ios`：非 mock driver + iOS 平台，断言不 spawn `asyncio.create_subprocess_exec`、返回失败信息、无活动 session |

**验收**：`tests/unit/test_unified_controller_video.py` **23 passed**（新增 1 例）。
