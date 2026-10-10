# 改进项台账（唯一事实源）

两份复盘报告是**历史快照**，不再回改（它们是本地未入库文件：`reports/复盘-20261009-获取电池信息测试.html`、
`reports/复盘-20261009-搜狐视频搜索大鹏.html`，质量门 `~/.dsh/skills/writing-run-retrospectives/tools/verify_report.py`）。
**状态只在本文件更新**：每落地一项就改状态、写证据（commit / tag / 路径）、补缺口；报告里的决策列可能滞后，以本文件为准。

- 项数：搜狐复盘 8 项 + 电池复盘 10 项，去重 2 项（搜狐 I3 ≡ 电池 I3、搜狐 I7 ≡ 电池 I8）= **16 个唯一改进项**
- 现状：已完成 **6** / 部分完成 **4** / 未开始 **6**
- 知识沉淀 6 项：已沉淀 **2** / 待定 **4**
- 状态词：`已完成`（验收通过且已并入整合分支）｜`部分`（列出剩余缺口）｜`未开始`｜`不采纳`

## 1. 已完成（6）

| ID | 改进项 | 落地证据 |
|---|---|---|
| 搜狐 I1 | 分支基线纪律 | 364853f（`docs/branch-plan.md`，命名规范 + 基线纪律，后续 533e486 补 fork 工作流 / d250275·c367ace 补标签表）· skill `knowledge/branch-base-regression.md` |
| 搜狐 I2 | **运行开始即声明代码身份**（原方案"make 断言目标 + 关键修复清单"已改形，见 §4） | 6a04113（tag `10101507-run-code-identity`）· `artemis/runtime/run_identity.py` · 真机：flash/pro 会话目录均有 `run_identity.json`（flash 此前为零记录）、严格模式 2 s 拒绝且无设备动作 |
| 搜狐 I3 ≡ 电池 I3 | 静默降级写入运行结论 | 7b2a0e9（tag `10101422-stale-observation-and-degradations`）· `artemis/runtime/degradations.py::build_run_environment` · 真机 verdict `environment.degradations` + `code` |
| 搜狐 I5 | 陈旧观察度量（观察→派发间隔） | 7b2a0e9 · `artemis/runtime/observation_clock.py` · 真机 `observation_skew_s` step1 3.144 s / step2 6.364 s |
| 搜狐 I7 ≡ 电池 I8 | trace 归档可复算 | e5b624b（tag `10101444-trace-archive-export`）· `artemis/runtime/trace_export.py` · 真机归档 = `recording.mp4` + `trace.gif` + `steps.json`（3 行 = DB 3 步）+ `images/`（3 硬链接）+ `notes/`（3 文件），无悬空引用；`trace list` 显示 `📋 Steps`、`trace view` 打出真实动作 |
| 电池 I1 | provider 感知 lens | 72e0c5d（tag `10091400-provider-aware-lenses`）· 66 个用例转绿 |

## 2. 部分完成（4）

| ID | 改进项 | 已完成 | 剩余缺口 |
|---|---|---|---|
| 电池 I4 | adb 探测短超时 / 轻量命令 / "慢≠不存在"（I4a），另含 I4b/c/d | I4a：f7e2844（tag `10091400-helper-probe-deadline`）· `PROBE_TIMEOUT_SECONDS = 6.0` + `pm list packages --show-versioncode` + 探测无答不再触发重装 | **I4b** 固定 30 s 不重试窗口未做；**I4c** 口径未定（`run_outcome.environment` 已有 `backend`/`switches`，但"探测超时"这一事件没有专门计数种类）；**I4d** 一次运行内层级来源切换上限上报未做 |
| 搜狐 I8 ≡ 电池 I5 | 环境降级缓存 + 录屏条件等待 | 录屏**早已**是条件等待：`artemis/utils/video.py::await_scrcpy_first_frame`（等 `Recording started` 标记、6 s 上限、超时按首帧回落），该函数来自 351ca84，**早于复盘** | ① 报告"固定等待 6 s"的前提需更正（真因：该 ROM 下标记从不打印，于是每次都吃满 6 s 上限）；② **wakelock / USB 保活能力探测缓存未做**：`artemis/runtime/awake_service.py` 每次会话仍重试并回落 5 s 心跳 |
| 电池 I9 | 用例模板两个必填字段（前置状态 + 验收口径） | 用例侧：`tests/e2e/test_device_battery_level.py`（tag `10091711-e2e-battery-level-case`，`GOAL`/`PRECONDITION`/`ACCEPTANCE` 三件套） | 产品侧（非测试入口）未强制 |
| 电池 I10 | 运行前状态复位 | 用例侧：同一文件 `_reset_to_home()` 运行前按 HOME | 产品侧未做 |

## 3. 未开始（6）

| ID | 改进项 | 现状证据 | 成本/风险 | 建议 |
|---|---|---|---|---|
| 电池 I7 | 裁决匹配归一化；无裁决不得静默填 `inconclusive` | `artemis/agents/checker/checker.py:462` 仍是 `(kind, item_text)` 精确匹配 | 低 / 低 | **建议下一批**：直接影响每次运行的 passed/inconclusive 口径 |
| 搜狐 I4 | 障碍类目标"已不存在"的判定 + 校验预算绑定目标类型 | 只有分类 `TARGET_DISAPPEARED`（`artemis/agents/validator/categories.py:27`），无判定策略 | 中 / 中 | 建议做，**但语义必须是"取消"**，见 §4 修正 #1 |
| 搜狐 I6 | 空步不入库或显式标 `no_action` | 全仓无 `no_action` | 低 / 中（步号变化需过回放断言） | 建议做 |
| 电池 I2 | 启动前 provider 凭据预检，缺失即快速失败 | 无预检；缺失时白付 helper 安装 + 录屏（10-09 那次 12 s 内崩） | 低 / 低 | 建议做 |
| 电池 I6 | plan ledger gate 与操作器原子化 | `artemis/agents/operator/operator.py:768` 仍退回动作重发 | 中 / 中 | 建议观察 |
| 电池 I4b / I4d | 30 s 不重试窗口 / 层级切换上限上报 | 同 电池 I4 | 低 / 低 | 随 电池 I4 一起排 |

### 3.1 新增候选（2026-10-10 观察，待决策）

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
