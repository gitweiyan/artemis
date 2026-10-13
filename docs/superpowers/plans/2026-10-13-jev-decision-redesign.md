# Jev 决策层重构方案（参考 Agent-JEV-Tetris 后的整体优化）

**日期**：2026-10-13
**对象**：`skills/tetris/`（Jev 报文 / 策略 / 控制）+ `scripts/`（离线基准与真机脚本）
**参考实现**：`third_party/Agent-JEV-Tetris-main/`（`index.html`、`tetris.js`、`server.mjs`）
**依据**：本仓库 2026-10-12 的真机与离线实测（下文每条都给出可复查来源）

---

## 1. 结论先行

参考实现与我们做的是**两个不同的问题**，必须分开对待：

| 产品线 | 目标 | 报文形态 | 是否可给"计划/评估特征" |
|---|---|---|---|
| **A. 打得动**（play） | 真机上把局面走好 | **动作空间 + 算好的后果 + 单一计划 + 时间预算** | 可以（它的 114 行 / 0 洞就是这么来的） |
| **B. 测得准**（benchmark） | 量化 Jev 的决策质量 | **同一状态/同一候选集，只动一个变量** | **不可以**（给了计划就等于测"会不会照抄"） |

参考实现的战绩（README：363 块 / 114 行 / 0 洞）证明的是 **"Jev + 算好的后果 + 单一计划"这套混合体**能打，
**不是** Jev 的裸判断力好（`index.html:166-169`、`tetris.js:142-160` 里 `bestPlan` 是代码算的）。两条产品线共用一套报文是我们此前**结构性问题**的来源。

## 2. 我们与它的差距（全部为本仓库实测）

| # | 差距 | 我们的实测 | 它的做法 | 后果 |
|---|---|---|---|---|
| G1 | 选项单位是落点不是动作 | 34 个落点（`captures/jev-modes/four-arms.json`：候选 18/9/36/34） | ≤7 个动作（`index.html:143-168`） | 报文 3.4 KB（A/B）/**14.3 KB**（C/D 带棋盘）；执行要跨多帧 |
| G2 | 模型自己做物理 | A/B 只给 id，C/D 给整张 20×10 棋盘 | `then_lands` 5 个数由游戏算（`tetris.js:88-103`），并在 `important` 里明说"别自己数格子"（`index.html:260-263`） | C/D 把它最不擅长的算术丢给它 |
| G3 | 执行粒度 | 一次决策 = 旋转 + 横移，跨多帧；实测"每块约 2 个可控帧"（`captures/play/20261012T141809Z/trace.jsonl`） | 一次决策 = 一次动作 = 一次点击 | "方案来不及执行"是我们 0 消行的主因之一 |
| G4 | 无时间预算 | 报文里没有任何"你还剩几次动作" | `actions_left` 由**实测延迟**推算（`index.html:285`、`374` 滚动 `thinkMs`） | 模型不知道自己在打时间仗 |
| G5 | 无单一计划、无历史 | 无 `done_sofar`、无计划字段 | `done_sofar` + `best_placement_found`（含第一步）（`index.html:199-207`） | 我们靠代码（相位单调）兜抖，模型侧无信息 |
| G6 | 不广告可达目标 | `well_sums` 在权重里，但从不展示 | `dips_you_can_fill`（**哪些块还塞得进**）+ `rows_near_completing`，且过滤被封死的行（`tetris.js:157-175`） | 容易追"不可能消的行" |
| G7 | 权重里有它实测有害的项 | `aggregate_height: -0.35`（`calibration.json`） | 实测 `tallest_column_after` 有害 → 以 `lands_on_row` 为主（`tetris.js:134-137`） | 填谷不改善该指标，会压正确走法 |

## 3. 设计：报文 v2（play 线）

**结构分四块**，各管一件事（这是参考实现最值得抄的组织方式）：

```
{ task, goal, rules, encoding,
  board_matrix,                     // 保留：我们自己要做感知一致性核对
  piece   { mask, positions, orientations, in_columns,
            lands_on_row_if_untouched, rows_of_fall_left, actions_left, done_so_far },
  stack   { column_heights, tallest, holes, roughness,
            dips_you_can_fill[], rows_near_completing[] },
  plan    { found, columns, lands_on_row, clears_lines, new_holes, actions_needed, next_action },
  candidate_actions[ { id, does, then_lands{in_columns,on_row,clears_lines,new_holes,roughness_after},
                       warning?, starts_the_plan? } ],
  instructions { task, priorities_in_order[], where_to_aim, tie_breakers[], important } }
question: { "move": { type: "choice", instructions: …, criteria: {left:{…}, right:{…}, rotate:{…}, hold:{…}} } }
```

要点与本地适配：

1. **动作集合**：`rotate` / `left` / `right` / `drop` / `hold`（"不动，让它落"）。**`drop` 已启用**（操作者 2026-10-13 取消硬降禁用规则，`calibration.gestures.drop.enabled=true`），与参考实现的 `hard_drop` 对应；`hold` 对应它的 `none`。
2. **`then_lands` 由我们算**：对每个动作，模拟"执行该动作后让方块自然落到底"的结局（`simulator.drop` + `clear_lines`），给出落点列区间、落地行、消行、新洞、粗糙度。**这是"环境算后果、模型只比较"的分工**（G2）。
3. **`plan`**：用 `bestPlan` 式单目标（旋转 + 双向滑动的可达枚举，含**原地转向**——参考实现的"零滑动落点"教训，`tetris.js:118-132`），成本函数改用 **`lands_on_row` 优先**、去掉 `tallest_column_after`（G7）。
4. **`actions_left` / `rows_of_fall_left`**：`rows_of_fall_left` 由模拟器算；`actions_left = rows_of_fall_left × (落行间隔 / 单次决策耗时)`，其中耗时用**滚动实测**（抓帧 520 ms + Jev 实测，见 `captures/play/*/trace.jsonl` 的 `frame_ms` 与 `captures/jev-modes/calls-*.jsonl` 的 `seconds`）。**不要写死数字**。
5. **`warning` / tie_breakers**：反向动作标 `warning`；tie_breakers 明写"Commit：接着做，别来回改""影子到位就落，不奖励没用掉的回合""全都糟时先比 new_holes 再比 on_row，选定就做完"（G5）。与已有的代码级相位单调（`policy_jev` 的 `rotate→move→hold`）叠加，不替代。
6. **`important`**："报文里每个数字都是游戏从棋盘量出来的；只判断这些数字，不要自己数格子。"

## 4. 设计：基准四臂 → 六臂（benchmark 线）

保留现有 A/B，替换 C/D，新增 E/F（每次只动一个变量）：

| 臂 | 选项单位 | 每项内容 | 测什么 |
|---|---|---|---|
| A | 落点 | 只有 id | `noul` baseline（已有 ✓） |
| B | 落点 | 只有 id | `choice`（已有 ✓） |
| **C′** | 落点 | **紧凑后果 5 个数**（不含整张棋盘） | 可观测性（低体积版，替代原 C/D 的 14.3 KB ✗） |
| **D′** | 落点 | 紧凑后果，`noul` | 可观测性 × 题型 的交叉项 |
| **E** | **动作** | `does` + 紧凑后果 | 选项粒度（G1） |
| **F** | 动作 | 只有 `does` | 选项粒度、无后果 |

- 判据器不变：`scripts/tetris_jev_modes.py` 的 `judge`（模拟器 + search 权重，**只在给过 Jev 的候选集内**排名）；指标：regret / top-1 / top-3 / 延迟 / 报文字节。
- **状态集必须先扩容**：现在是同一段 30 秒录像去重出的 4 个状态（4 个里 3 个四臂全同 ✗）。目标 **60–100 个状态**，来自 ≥3 段不同录像 + 用模拟器合成的局面；`--states` 已支持，取样函数需支持多来源。
- **成本**：每臂每状态 1 次调用；6 臂 × 60 状态 = 360 次 ≈ 1.5–6 s/次 → 9–36 分钟、需付费。**待决策**（见 §8）。

### T7 结果（2026-10-13 首跑：5 个合成状态 × 6 臂 = 30 次真实调用）

判据 = search 策略对"所选落点"的评分（**强启发式偏好，不是 ground truth**）；regret = 最优 − 所选。

| 臂 | 选项单位 | 题型 | 紧凑后果 | 平均 regret | 中位 | 命中启发式最优 | 进前三 | 耗时中位 | 报文字节 |
|---|---|---|---|---|---|---|---|---|---|
| A | 落点 | noul | — | 25.99 | 16.0 | 0/5 | 0/5 | 2.72 s | 2155 |
| B | 落点 | choice | — | 25.39 | 17.4 | 0/5 | 0/5 | 2.60 s | 2155 |
| **C′** | 落点 | choice | ✓ | **0.22** | **0.0** | **4/5** | **5/5** | 2.26 s | 4004 |
| D′ | 落点 | noul | ✓ | 1.36 | 0.0 | 1/5 | 4/5 | 2.10 s | 4004 |
| E | 动作 | choice | ✓ | 10.63 | 0.0 | 1/5 | 3/5 | 2.52 s | 6291 |
| F | 动作 | choice | — | 20.13 | 13.95 | 0/5 | 0/5 | 1.16 s | 5851 |

**三条结论（每条都在同一批状态上、只动一个变量）**：

1. **决定性因素是"可观测性"，不是题型**：给上"这个候选执行后会变成什么"的 5 个数（+1.8 KB），C′ 从 B 的 25.39 降到 **0.22**、命中率从 0/5 升到 **4/5** ✓✓；题型的影响小得多（C′ 0.22 vs D′ 1.36 ✓；没有后果时 A≈B 且都很差 ✗）——这正是参考实现 README 里那句"游戏用代码量出每个后果，jev 只负责判断" ✓✓，也是本轮最大的单点收益。
2. **动作空间在我们的场景里更差**：E（10.63 / 中位 0.0 / 3-5 进前三）不如 C′，F 最差 ✗ —— 选一个按键无法表达"落点"，而且它每块要多次调用 ✗（与操作者"下落速度会变、要尽快决策执行"的裁定冲突 ✗）。参考实现的形状不适用于本游戏节奏 ✓。
3. **延迟没有明显代价**：六臂耗时都在 1–7 s、中位 1.2–2.7 s ✓，报文从 2.1 KB 增到 8.1 KB 并没有让哪一臂显著变慢 ✓（F 反而最快 1.16 s ✓）。

**必须声明的限制**：N=5（小样本）✗；判据是 search 启发式而非游戏结果 ✗；状态是合成棋盘（种子固定、形状取自游戏实测形状集 ✓，但不是真实分布 ✗）。**结论方向清楚、幅度不可当定论** ✓；下一轮应把状态扩到 60–100（`--states` + `--source both` 已支持 ✓）与加一条真机验收。

**据此的默认配置建议**：play 线用 **C′ 形态**（落点 + `choice` + 紧凑后果）；动作空间仅保留实验位；`then_lands` 的 5 个数**算不算"泄漏"**（D3）现在有了实测依据——它带来 25.4 → 0.22 的差距，若基准线仍要求"零泄漏"，则必须接受 A/B 那一档的准确度 ✗，这个取舍由操作者定 ✓。

## 5. 代码改动清单

| 文件 | 改动 | 性质 |
|---|---|---|
| `artemis/services/systemone.py` | `choice()` 的 `criteria` 从 `dict[str,str]` 放宽到 `dict[str, Any]`（参考实现的 criteria 是对象，`index.html:151-160`，线上可用） | 小、有测试 |
| `skills/tetris/simulator.py` | 新增 `outcome(board, piece, action)`（动作后的结局）、`best_plan(board, piece)`（含原地转向与零滑动）、`row_gaps()`（过滤被封死的行，`tetris.js:157-175`）、`dips_you_can_fill()`（用模拟判断哪些块塞得进） | 中、纯函数、可 TDD |
| `skills/tetris/jev_state.py` | 新增 `build_action_state(...)`（报文 v2，play 线）与 `build_placement_state(..., consequences=True/False)`（基准线）；`build_state` 保持不破坏旧调用 | 中、键集用测试钉死 |
| `skills/tetris/policy_actions.py` | 新策略：一次决策 = 一个动作；复用现有 `_ask`/遥测/落盘；`hold` 时无手势 | 中 |
| `skills/tetris/control.py` | 动作 → 手势：`rotate`/`left`/`right` 各**恰好一次点击**；`hold` 无手势；`to_gestures` 对动作型 action 走新分支 | 小、有测试 |
| `skills/tetris/calibration.json` | `jev.option_space: "action"\|"placement"`、`play.backend`、`benchmark.arms`、滚动延迟的初值与来源注释 | 小 |
| `scripts/tetris_jev_modes.py` | 加 C′/D′/E/F 臂、多来源状态取样、报文体积统计 | 中、`--self-check` 扩展 |
| `scripts/tetris_play.py` | 支持动作型策略（一次决策一次点击），并把滚动实测延迟写进报文 | 小 |
| `scripts/jev_probe.py` | 支持动作空间输入，便于手工试报文 | 小 |

## 6. 验证与验收

**离线（必做，零真机成本）**
1. 单元测试：`outcome`/`best_plan` 的四个"坑"各一条用例——零滑动落点算数、单一计划（不给每方向各一条提示）、被封死的行不广告、`lands_on_row` 与"最高列"的区别（G1/G6/G7）；
2. `--self-check` 覆盖全部六臂（假答案、不联网）；
3. 六臂跑 60–100 状态，出 regret/top-3/延迟/字节表。

**真机（play 线，≥2 局）**
| 验收项 | 判据 | 现状基线 |
|---|---|---|
| 每次决策恰好一次点击 | trace 的 `gestures` 长度恒为 1 | 现在常为 2–3（旋转+横移） |
| 无 `rotate→move→rotate` | `tetris_analyze.py --clicks` 相位序列 | 已修 ✓（代码级）需保持 |
| 方案落地率 | "决策的目标列 == 方块最终落点列"的比例 | **未测** → 先测基线再定阈值，不预设数字 |
| 消行 | 60 秒内 ≥1 行（低门槛） | **0 行**（多局实测） |
| 延迟 | 单次决策耗时中位数、每块可控帧数 | 1.35–5.97 s / 约 2 帧 |

## 7. 不可移植 / 需要单独确认

- **重力墙钟**：设备上改不了游戏节奏（`index.html:178-179, 283-285`）。替代：把时间预算写进报文 + 每帧用当前最优计划行动。
- **`next_piece`**：真机不显示（实测 `next_preview: null`），报文里留 `null` 并说明。
- **`hard_drop`**：操作者规则禁用，不提供该动作。
- **`rotate_ccw`**：设备上只有像素点击，旋转方向**未验证**（见 T1）。
- **`bestPlan` 进入 play 线 = 混合策略**：会改变"纯 Jev 决策"的口径，需操作者确认（§8）。

## 8. 待决策项（本报告只提方案）

| # | 待决策 | 选项 | 影响 |
|---|---|---|---|
| D1 | play 线是否允许 `plan` 字段（= 允许 search 先给计划） | 允许 / 不允许 | 允许则更像参考实现、更可能打得动；不允许则保持"纯 Jev"口径 |
| D2 | 基准线预算 | 6 臂 × 30 / 60 / 100 状态 | 直接决定结论可信度与付费调用数（30 状态 ≈ 180 次） |
| D3 | 是否把 `then_lands` 的 5 个数也认定为"泄漏" | 是（基准线不给）/ 否 | 若认定为泄漏，基准线只能用 C/D 的棋盘或裸 id，取样成本更高 |
| ~~D4~~ | ~~是否给 `hold` 之外的"软降"动作~~ **已决策（2026-10-13）**：取消硬降禁用，`drop` 进入动作集合（`calibration.gestures.drop.enabled=true`，测试 `test_the_shipped_calibration_uses_the_drop_button`） | — | 每块可用动作数 +1；落地时机由策略决定 |

## 9. 任务拆解（TDD + 现有门禁）

| 任务 | 内容 | 产出/证据 |
|---|---|---|
| T1 | 量一次旋转方向：真机点 rotate 后读掩码，确认是否顺时针（操作者口径） | trace + 结论写进 `calibration.gestures.rotate.note` |
| T2 | `systemone.choice` 支持富 criteria | 测试 + 客户端改动 |
| T3 | `simulator.outcome/best_plan/row_gaps/dips_you_can_fill`（先写失败测试：四个坑各一条） | `tests/unit/reflex/test_simulator_outcomes.py` |
| T4 | `jev_state.build_action_state` + 基准线 `consequences` 开关（键集钉死；"无泄漏"变体不含评估特征） | `tests/unit/reflex/test_jev_state.py` 扩展 |
| T5 | `policy_actions` + `control` 动作→单手势 | `tests/unit/reflex/test_policy_actions.py`、`test_runtime_wiring.py` 加一条"一次决策恰一次点击" |
| T6 | 六臂基准（多来源状态、字节/延迟统计、`--self-check`） | `scripts/tetris_jev_modes.py`、`captures/jev-modes/*.json` |
| T7 | 离线跑六臂，出表并写回本方案（结果与结论） | 基准报告 |
| T8 | 真机 2 局，按 §6 表测"落地率/延迟/点击粒度" | `captures/play/<stamp>/`（trace+frames+jev+summary+analysis） |
| T9 | 提交 + 更新 `2026-10-12-...pipeline.md` 的断点节 | git |

**每任务门禁**（沿用现有口径）：先写失败测试 → 绿；`ruff format --check`、`ruff check`、`scripts/quality_ratchet.py`、全量 `pytest` 保持基线（当前 14 failed / 2933 passed，为既有失败）。

## 10. 风险与回退

| 风险 | 缓解 |
|---|---|
| 动作空间使**每块调用变多**（一次决策一个动作 ≠ 一块一次） | 复用 `plan` + `done_so_far`：计划未变则不重复提问；`policy_actions` 记录 `calls/piece` 并在基准里报出 |
| 紧凑后果仍可能被认定为"泄漏" | 基准线保留"裸 id"的 A/B 作对照；D3 由操作者裁定 |
| 真机点击方向/精度不足导致动作型策略失效 | T1 先量；`control` 为动作型路径加"点击后复核列号"的可选校验 |
| 报文变大反而更慢 | 基准里同时报**字节数**与**延迟**（现在 14.3 KB 对应 5.97 s，7 选项/1 KB 待测） |
| 改用动作空间后原地打转 | 已有代码级相位单调 + 新增 `warning`/Commit 提示，双保险 |

**回退**：`calibration.jev.option_space` 一行切回 `"placement"`，其余改动（`outcome`/`best_plan`/基准臂）不影响现有链路。

---

## 11. 交接（2026-10-13 会话末，给新窗口）

### 11.1 现在处于什么状态

| 项 | 状态 |
|---|---|
| T1–T5 | ✅ 完成（旋转顺时针入标定；`outcome`/`best_plan`/`row_gaps`/`dips_you_can_fill`；`systemone.choice` 支持富 criteria；报文 v2 `build_action_state`；动作型决策 → 恰好一次点击；`policy_actions` + `option_space` 开关） |
| T6/T7 | ✅ 完成：六臂基准 + 合成状态集，结果见 §4（5 状态 × 6 臂） |
| play 线默认 | ✅ 已按实测改成 **C′ 形态**（落点 + `choice` + 紧凑后果）：`jev.consequences: true`、`jev.play_message: "placement+consequences"`，`policy_jev` 已接线，两条测试双向钉住 |
| T8 真机验收 | ❌ 未完成：只拿到 6.4 s / 17.6 s / 60.5 s 三次短跑；**消行仍为 0**；"决策延迟中位"未提取 |
| 旋转空转 | ❌ **当前最频繁的结束原因**（`the piece is not turning` ×2、`game over` ×2、`time_budget`） |
| 抓帧路径 | ✅ 已量并否掉："改 helper APK 压到 250 ms"不成立（helper 489 ms vs screencap 536 ms，尾部反而 2538 ms） |

### 11.2 下一轮的第一件事：下落块生命周期跟踪器（**尚未动代码**）

现状：帧差检测**每帧都要重新推导**"什么在动" ✗，而实测每块只有 1–2 帧可见 → **永远慢一帧** ✗；且每帧重读的掩码可能差一格/被堆积吞并 → 形状身份不稳定 → `_taps_to_reach` 回退成"还差 N 拍" → **旋转空转** ✗。

设计（操作者提出，见会话末）：**一次识别，整个生命周期跟踪**。

| 阶段 | 做什么 | 关键细节（都来自实测） |
|---|---|---|
| acquire | 用现有帧差确认"这是新的一块"，记下形状 + 格子 + 生命周期 id | 形状与 `mechanics.shapes`（**8 个实测形状，1–4 格**）比对一次 |
| track | 后续每帧只在上一帧位置附近找匹配，更新位置/朝向 | **不要比对固定位图**（旋转会换形）✓；位移窗口按实测：最快 3.7 行/秒 × 0.5 s/帧 ≈ **每帧 ≤2 行** |
| release | 连通块与堆积合并、或找不到 → 结束生命周期 | **硬降 0.1 s** 可能让方块两帧之间直接落地 → 允许"从未 track 到就 release" |

三个必须注意：①不用固定位图跟踪；②必须能 release（否则身份黏住堆积）；③跟踪器是跨帧状态，`reset_history()`（会话 `on_start`）必须一并清。

验证（零设备成本）：用 `skills/tetris/samples/session-play/`（43 帧有图有时间戳）+ 各次运行 trace 的矩阵序列离线回放，断言 (a) 每块只产生一个身份 ✓、(b) 报告帧数显著高于帧差路径（现状见块帧占比 19–41%）✓、(c) 落定时正确 release ✓；修好后旋转空转应一并消失 ✓。

### 11.3 操作者的长期裁定（规则，改之前先看这里）

| 规则 | 落在哪 |
|---|---|
| 旋转是**顺时针** | `calibration.gestures.rotate.note` |
| **硬降已启用**（约 0.1 s 落到最底） | `calibration.gestures.drop`（`enabled: true` + note） |
| 下落速度**不固定、会穿插快速模式**；策略要**尽快决策、尽快执行** | `calibration.mechanics.fall_speed`（含实测：中位 1.78–2.05 行/秒、最快 3.75） |
| 方块**不固定 4 格**，1/2/3/4 格都会有 | `calibration.mechanics.shapes`（8 个具名形状）+ note |
| 点击键位：⬅️13% ⬇️33% ➡️53% 🔄81%（y=84%） | `calibration.gestures` |
| **一次点击 = 移一列**；**一块最多一次翻转、一次移位** | `calibration.gestures.move_*.note` + `policy_jev` 相位单调（`max_rotate_frames` 护栏） |
| 不需要颜色识别 | `calibration.note` / 判定用 HSV 阈值 |
| 正确性验证由人工判断，不做自动化验证 | **仅在本会话里说过，未落盘** ⚠️（见 §11.4） |

### 11.4 新窗口怎么拿到背景（以及拿不到什么）

**能拿到（仓库内，可复查）**

1. 本文件（决策层重构：差距 G1–G7、报文 v2、六臂、T7 结果、T1–T9 拆解、本轮交接）；
2. `docs/superpowers/plans/2026-10-12-perception-to-jev-pipeline.md`（T2–T9 的落地与断点、真机行为边界）；
3. `skills/tetris/calibration.json`：所有设备/游戏常数与**每条 note 里的实测来源**（几何、HSV、面板、静止阈值、形状集、下落速度、抓帧、超时、开关）；
4. `git log`：每个提交的信息都写了"为什么"（含两次我承认的错误论断如何被纠正）；
5. `captures/play/*/`：trace（逐帧矩阵/掩码/决策/每次点击/静止读数）+ frames + jev.jsonl（Jev 原始输入输出）+ summary.json + analysis.json；
6. `reports/复盘-20261012-tetris内环真机联调.html`（六节复盘：时间线 27 条、问题 8 个、根因 4 类、改进 6 条）；
7. `scripts/` 各脚本的 docstring（`tetris_play/analyze/probe/restart/jev_modes/jev_probe/piece_detect_rate`）——**里面写的是方法与坑**（例如"底图必须先清空，否则会误判 T/S 检不出"）。

**拿不到（只在本会话里，必须重新问或重新推）** ⚠️

- 上面 §11.2 的跟踪器设计细节（**已在本文件补齐** ✓）；
- 若干口头裁定："正确性验证由人工、不做自动化验证"、"不需要颜色识别"、"一次点击=一列"等——后两条落盘了 ✓，第一条**没有** ✗；
- 会话中途被我撤回的错误结论（"识别残缺"✗、"T/S 检不出"✗、"抓帧 0.51 s 是瓶颈"✗"helper 不可用"✗）——**纠正结果在标定/提交信息里** ✓，但"曾经错过什么"只有复盘报告的时间线里有 ✓；
- 我的推理过程与权衡（未落盘的部分）✗。

**新窗口的开场建议**（照抄即可）：

> 读 `docs/superpowers/plans/2026-10-13-jev-decision-redesign.md` 的 §11 交接一节与 §4 的 T7 结果，以及 `skills/tetris/calibration.json` 的全部 note；先跑 `tests/unit/reflex` + `ruff` + `scripts/quality_ratchet.py` 确认基线（282 passed / 14 failed 为既有）；然后按 §11.2 做下落块生命周期跟踪器（TDD，先写失败测试）。

### 11.5 未落盘事项登记（2026-10-13 登记）

**A. 已在本轮补落盘的**（此前只存在于会话里）

| 事项 | 现在落在哪 |
|---|---|
| 操作者长期裁定 8 条（含"不需要颜色识别""正确性验证由人工、不做自动化"） | `calibration.json#operator_rulings`（逐条给 rule + recorded 指向） |
| 下落块生命周期跟踪器设计（acquire/track/release、三个陷阱、位移窗口、验证方法） | 本文件 §11.2 |
| 本轮实测：helper 489 ms vs screencap 536 ms；下落速度 1.78–2.05 行/秒（最快 3.75）；形状集 8 个 | `calibration.json#capture` / `#mechanics.fall_speed` / `#mechanics.shapes` |

**B. 已撤回的错误结论**（新窗口**不要**重新推导，也**不要**沿用）

| 曾说过 | 状态 | 纠正落在哪 |
|---|---|---|
| "硬降把感知打瞎了，读到的是残块" | ✗ 撤回：1–2 格是**真实方块** | `mechanics.shapes` note + 提交 `642c404` |
| "T 形/S 形系统性检不出（0/9、1/9）" | ✗ 撤回：**合成测试被底图污染**；清理后 72/72 全检出 | `scripts/tetris_piece_detect_rate.py` docstring + 提交 `71b4f55` |
| "抓帧 0.51 s/帧 是最大瓶颈" | ✗ 撤回：screencap 单独 309 ms；循环的 510 ms = 抓帧 + 该帧决策 + fps_cap 等待 | `capture` note + 提交 `03d2224` |
| "helper 在本设备不可用" | ✗ 撤回（操作者启动后可用）；但**更快也不成立**：489 vs 536 ms、尾部 2538 ms | `capture` note + 提交 `03d2224` |
| "7.63 s 的 Jev 决策装得下（因为一块约 20 s 落地）" | ✗ 撤回：实测 0.5 s/行、快速模式 0.27 s/行；超时已改 2.5 s | `systemone_timeout_note` + 提交 `1a1c45c` |

**C. 仍未解决/未测量（下一轮要做的）**

| 项 | 状态 |
|---|---|
| 旋转空转（`the piece is not turning`） | 机制收窄到"观测位图不在形状集 → `_taps_to_reach` 回退成目标值" ✗；触发者（设备没转 / 掩码被吞并 / 部分掩码）**未分因**；§11.2 的跟踪器预期一并解决 |
| T8 验收表 | 只拿到 6.4 s / 17.6 s / 60.5 s 三次短跑；**消行 0**；"决策延迟中位"与"方案落地率"**未测** |
| T7 样本量 | N=5，方向清楚、幅度不是定论；扩到 60–100 用 `--states 60 --source both` |
| D1/D2/D3 | 见 §8：play 线是否允许 `plan`；基准预算；`then_lands` 是否算泄漏（现有实测幅度 25.4 → 0.22） |
| T9 | 未做：把本轮结果回填 `2026-10-12-...pipeline.md` 的断点节 |

**D. 只存在于会话、无法登记的部分** ⚠️

我的推理过程、被放弃的中间方案、以及未被写进任何文件的权衡 ✗ —— 这些随会话消失 ✓。**这正是"每做一个决定就写进 note/提交信息/方案"这条纪律存在的理由** ✓。

### 11.6 新窗口开场（复制即用）

```
请先读这三处，再动手：
1. docs/superpowers/plans/2026-10-13-jev-decision-redesign.md（§4 T7 结果、§8 待决策、
   §11.1 状态、§11.2 跟踪器设计、§11.5 登记表）
2. docs/superpowers/plans/2026-10-12-perception-to-jev-pipeline.md（T2–T9 落地与行为边界）
3. skills/tetris/calibration.json 的全部 note（尤其 operator_rulings、mechanics.shapes、
   mechanics.fall_speed、capture、panel、stall、jev、gestures.drop）

先确认基线再改东西：
  .venv/bin/python -m pytest tests/unit/reflex -q      # 期望 282 passed
  .venv/bin/ruff check skills/ scripts/ tests/unit/reflex
  .venv/bin/python scripts/quality_ratchet.py          # 全量 pytest 的既有基线是 14 failed / 2933 passed

然后做本轮的既定第一件事（TDD，先写失败测试）：
  按 §11.2 实现下落块生命周期跟踪器（acquire 用帧差一次 → track 整个生命周期 → 落定 release），
  用 skills/tetris/samples/session-play/（43 帧）+ captures/play/*/trace.jsonl 的矩阵序列做离线回归，
  断言：每块一个身份、报告帧数显著高于帧差路径、落定时被 release；并观察旋转空转是否一并消失。

真机纪律（§11.3 的裁定）：一台设备 serial EUWSOBXCZLPFNJF6；不要硬降以外的臆断；
  真机跑用 scripts/tetris_play.py（会自己开新局并在卡死时重启 App），
  分析用 scripts/tetris_analyze.py <trace> 与 --clicks；
  每次运行都会落盘 trace/frames/jev.jsonl/summary.json/analysis.json 到 captures/play/<stamp>/。
```

---

## 11.7 下落块生命周期跟踪器已落地（2026-10-13 续）

§11.2 的跟踪器**已实现并有离线回归**，先写失败测试再实现（TDD）。改动如下：

| 文件 | 改动 |
|---|---|
| `skills/tetris/tracker.py` | 新增 `PieceTracker`：acquire（严格帧差一次，失败则用"整块新出现"的连通块）→ track（回到 acquire 时记下的地形，取"现在有、当时地形没有"的格子，按实测位移窗口就近匹配，快落由帧差回退接住同形状的块）→ release（找不到 / 与堆积合并 / 一帧不动 = 已落定） |
| `skills/tetris/perception.py` | `_measured_state` 改用 `PieceTracker.push(matrix)`；`reset_history()` 与网格变化时一并 `reset()` |
| `skills/tetris/calibration.json` | 新增 `tracker` 块（`max_piece_cells / max_idle_frames / max_row_delta / max_col_delta` + 每条 note 与实测来源） |
| `tests/unit/reflex/test_tracker.py` | 新增 9 条：一块一身份（含 spawn 逐行显形与贴堆的落定帧）、报告帧数超过帧差、terrain 不含块、落定 release、下一块新身份、reset 清状态、真实语料回归 |

**实测（离线，零设备成本）**

| 语料 | 跟踪器报块帧 | 帧差报块帧 | 生命周期 | 释放 |
|---|---|---|---|---|
| `samples/session-play`（43 帧真实对局） | **38/43 = 88%** | 28/43 = 65% | **4**（时长 10 / 11 / 9 / 8 帧） | 全部 `locked` |
| `captures/play/20261012T141809Z`（98 个棋盘帧） | 73/98 | 41/98 | 16 | 逐块 release |

断言钉住：`tracked >= diffed + 5` 且 `tracked >= 0.8 * frames`；每个身份只出现一段（`test_each_identity_is_one_contiguous_lifecycle`）；真实 trace 喂完再喂一帧静止后必须 `piece_id is None`。

**踩到并修掉的坑（都在 note 里）**：第一版没有位移窗口，把"上一块刚落、新块刚生成在顶部"误当成同一块继续跟（`20261012T141809Z` 的 f44，`motion=[-3,0]`）。加 `max_row_delta=2 / max_col_delta=4` 后，越窗的候选只允许**帧差回退且形状相同**才续跟（真实快落仍能续），否则 release。

**旋转空转**：跟踪器去掉了**感知侧**的触发者——同一生命周期内掩码在（一帧的）逐行显形之后保持同一个形状，`_taps_to_reach` 不会再因为"位图不在形状集"而回退成目标值（回退只可能在 `_piece_key` 相同的复用帧发生，而 `_piece_key` 由掩码导出，掩码稳定则键稳定）。**设备侧**的触发者（点旋转但游戏没转）无法由感知消除，仍由 `max_rotate_frames` 护栏兜住——这一条要靠 T8 真机才分得清。

**门禁**：`tests/unit/reflex` → **291 passed**（旧 282 + 新 9）；`ruff format --check` / `ruff check` 通过；`scripts/quality_ratchet.py` 通过（全量既有 14 failed / 2933 passed 基线不变，本次未改全量用例数）。

**仍未做**：T8 真机验收（点击粒度 / 无 rotate→move→rotate / 落地率 / 消行）；T9 回填 `2026-10-12-...pipeline.md`；`captures/` 为未跟踪的现场证据，未纳入提交。

