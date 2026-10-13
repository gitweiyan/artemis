# Tetris 真机内环：经验教训与断点

> 日期：2026-10-13（一整天）。对象：`skills/tetris` 的"截图 CV → Jev 决策 → 手势执行"闭环。
> 上游方案：`docs/superpowers/plans/2026-10-13-jev-decision-redesign.md`、`2026-10-12-perception-to-jev-pipeline.md`。
> 复盘路由（操作者约定）：**复盘用 deepseek-v4-pro，计划/执行用 deepseek-flash**（复盘通过 `pi --model deepseek-v4-pro --print` 以只读方式交给 v4-pro，本机当前 PI_MODEL 是 deepseek-flash）。

## 一句话总结

这套闭环的**感知、决策评分、报文内容都已经是"够用"的水平**；真正卡住消行的是**实时节奏**（Jev 1–6s vs 方块 ~0.5s/行）和反复出现的**"读数滞后"**类 bug。经过一天的修复，执行/一致性已经从"触顶 0 消行、落地 ~63%"推进到"跑满 180s 不触顶、落地 96%、偶发 1 消行"。

## 一、最重要的一条方法论教训：假设要被"专门实验"反驳

几乎每个结论都经历了"先猜 → 专门真机实验 → 数据打脸/证实 → 再改"：

| 假设 | 实验 | 结果 |
|---|---|---|
| "右移欠冲是丢拍/执行问题" | `scripts/tetris_move_probe.py`（连发 N 次移、带/不带 drop） | **证伪**：tap+drop 本身可靠（15/16） |
| "旋转按钮坏了" | 旋转探针 | **证伪**：竖/横条 6/6 都能转 |
| "评分器选得差" | 离线 rollout（同方块序列、完美执行） | **证伪**：确定性权重 49 块能消 13.5、高 3、0 洞 |
| "真实决策还行、执行差" | 把录下的决策放进 rollout（完美执行） | **证伪**：真实决策只消 0、堆到 10.5、18 洞——决策本身在退化 |

**结论性教训**：离线静态 sweep（在某盘面上比较单步落点）**测不到"塑造"效果**——它只看"这步选得好不好"，看不到"前几步把盘面堆坏了，导致后面无清行可消"。判断"差在哪一环"必须用**同输入序列、完美执行的 rollout** 做反事实。

## 二、技术教训（每条带证据）

1. **节奏墙是第一主因（约 50%）**。Jev 1–6s、抓帧 ~0.5s、下落 ~0.5s/行 → 每块只有 2–5 个可控帧、**每块不足 1 次有效决策**。参考实现 Agent-JEV 能消 114 行的前提是"gravity 300–800ms/行 + Jev 中位 310ms → 每行 1.7–2 次动作"；这个前提我们**没有**。所以参考实现的"动作空间 + 每行多次动作"抄不过来（T7 实测动作空间更差）。

2. **"读数滞后"是反复出现的同一类 bug**。三次都栽在"用还没稳定的读数做决策"：
   - verify-before-drop（`f19cbb4` 后回退）：verify 帧读到上一帧 tap 没生效的旧列号，把同一个 delta 又扣一遍 → 系统性多偏一格。
   - 旋转震荡（`a370989` 修）：mask 一帧没变就当"missed tap"立刻补 tap，但旋转动画比一帧慢 → 重复旋转、L/Z/I 左右来回翻。
   - 旋转后列号（`0a7fb4f` 修）：绕第二格旋转会让最左列变 1 格，第一帧读数不可信。
   **规则**：任何"纠偏/补拍"都要先等读数**连续两帧一致**（或给固定宽限期），不要等一帧就动。这与"一块一次位移"并不冲突——是"确认后再动"，不是"反复动"。

3. **评分器不是瓶颈，别在它身上过度投入**。静态 regret 极低（0.03–1.78）、missed_clear≈0，说明"意图落点"通常合理。把回退从 8 权重换成 `plan_cost`、或调高 `lines`，在 rollout 里只是 3.5→4.0 的边际差异；真机上 search-only（Jev 不可达）也一样触顶。

4. **部分显形是感知侧的第一坑**。游戏分多帧画出方块（1→2→3→4 格），tracker 第一帧就 acquire。在"半个块"上决策：**48% 落点正确**；等形状稳定后决策：**88%**。修复=形状稳定门控（`piece_settled`），且"一块只规划一次"用 tracker 的 `piece_id` 而不是 `_piece_key`（显形会改 key）。

5. **一块 = 一个身份、一次旋转、一次位移、一次 drop**。这一组不变量要靠 `piece_id` 强制，靠 `_piece_key` 会破（显形/棋盘变化改 key → 同块被重规划、重移位、重 drop）。逐一修：`0c2e81a`（一份计划）、`877c0b2/df4ee13/a283fad`（一次 drop 的三条路径）。

6. **参考实现的边界**：**报文内容可抄，节奏抄不了**。值得抄并已抄的：单一 `plan` + `starts_the_plan`、完整 instructions（priority/where_to_aim/tie_breakers/important）、"数字都是游戏量的、别自己数格"（`8ce198b`）。抄不了/不抄的：动作空间、每行多次动作、`next_piece`（真机不显示）、soft_drop/rotate_ccw/none（我们的游戏只有 4 个键）。

7. **护栏别太紧**。`max_rotate_frames` 3→6（`975bf6c`）：按钮可靠但 live 循环偶发丢 tap，3 帧太紧会提前结束会话；6 帧给足余地又不至于回到 8 帧空转。

## 三、过程教训

1. **TDD + 真机 A/B + 离线 replay 三件套**。每次改代码先写失败测试（红→绿），再用 `captures/play/*/trace.jsonl` 离线回放验证（比如"window=2 vs 8 丢块数 3→0"），最后真机 A/B。`tests/unit/reflex` 从 282 → 315 全程保持绿，`ruff`/`quality_ratchet` 每步通过。
2. **证据落盘**。每次真机运行落 `captures/play/<stamp>/`（trace/frames/jev.jsonl/summary/analysis）；每个 commit 信息写"为什么"；`calibration.json` 的每条 note 写实测来源（含被撤回的错误结论）。
3. **模型路由**：复盘用 deepseek-v4-pro、计划/执行用 deepseek-flash。复盘通过 `pi --model deepseek-v4-pro --print --no-session --no-tools` 以只读方式喂数据集；多次因 `--tools read` 卡死，改用"数据内联 + `--no-tools`"更稳。
4. **被撤回的错误结论要登记**（见方案 §11.5）：本会话至少两次误判——"右移欠冲是执行问题"（实为显形）、"verify-before-drop 能修欠冲"（实为读数滞后）——都靠专门实验纠正，别在新窗口重复推导。

## 四、当前状态与断点

**已修（按时间顺序，全部已提交、TDD）**
- 生命周期跟踪器（`e85c83b`）、旋转不是横向重力（`3c4c537`）、横移不改变重力 + 超时不重试（`fbde113`）
- 一块一次 drop（`877c0b2`/`df4ee13`/`a283fad`）、快落窗口 2→8（`b734e86`）、一块一份计划 piece_id（`0c2e81a`）
- 形状稳定门控（`19e1bee`）、旋转 settle（`129d7bd`）、列稳定门控（`0a7fb4f`）、旋转宽限期（`a370989`）
- 报文 plan + instructions（`8ce198b`）、max_rotate_frames 6（`975bf6c`）
- 回退 verify-before-drop（`5d66169`，因读数滞后有害）

**量化的改善**（Jev 线，跨 run8→run24）
- 落地命中：~63% → **96%**（27/28）
- 旋转震荡：有 → **0**；旋转空转：频繁结束会话 → 无
- 存活：91–110s 触顶 → **180s 跑满不触顶**
- 消行：0 → 偶发 1，棋盘保持低位（最高 10 行，不再是 20 的塔）

**仍未解决（断点）**
- **节奏墙（~50% 权重）**：Jev 1–6s 导致决策晚到。下一步方向 1：Jev 异步化/预取（一块只允许一次主决策、spawn 时发起、硬超时、超时用确定性 fallback），目标是保证每块 ≥2–3 个可控动作。方向 2（锁目标列 + 锁前单次校验）已基本等于现状。
- 报文还可补 stack.dips_you_can_fill / rows_near_completing / actions_left（参考实现的 3/4），但性价比已低（不解决节奏墙）。

## 五、新窗口怎么接手

1. 读本文件 + 方案 `2026-10-13-jev-decision-redesign.md` 的 §11（状态/跟踪器设计/裁定/复盘）。
2. 跑基线：`.venv/bin/python -m pytest tests/unit/reflex -q`（期望 315 passed）、`ruff check skills/ scripts/ tests/unit/reflex`、`.venv/bin/python scripts/quality_ratchet.py`。
3. 真机纪律：serial `EUWSOBXCZLPFNJF6`；跑 `scripts/tetris_play.py`（自己开新局、卡死重启 App），分析 `scripts/tetris_analyze.py <trace>`；复盘用 `pi --model deepseek-v4-pro`。
4. 下一步只做一件事：**方向 1（Jev 异步化）**，别在评分器/报文上继续堆。
