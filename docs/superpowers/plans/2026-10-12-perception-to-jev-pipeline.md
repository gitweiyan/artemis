# 截图 CV → Jev → 操作 链路实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把真机截图稳定转换成一枚可复核的 10×20 棋盘状态（含下落块与运动方向），喂给 TypeSafe Jev 取回"选哪个落点"，再由本地手势层执行——每一环都有独立证据。

**Architecture:** 沿用已落地的 reflex 层：`artemis/reflex`（通用闭环，不含游戏知识）+ `skills/tetris`（自包含技能）。本计划只改**技能侧**的感知与策略，并把"能测的先测"写进代码：网格计数不预设列数，阈值来自 `calibration.json`，合法性由本地 `simulator` 保证，Jev 只在合法候选内排序。

**Tech Stack:** Python 3.12、numpy、Pillow、既有 `artemis/reflex` 运行时、`artemis/services/systemone.py`（Jev 客户端）、pytest。

**Spec:** 本轮对话（2026-10-12）确认的口径与判据：棋盘 `x[35,465] y[320,1180]`、10×20；`reports/reflex-真机观察.html` 里的 3 张真机帧为参考帧；`~/.dsh/skills/writing-run-retrospectives/knowledge/claim-vs-evidence.md` 为验证纪律的依据。

## Global Constraints

- 抓帧 p50 330–490 ms（helper 原子快照 489 ms、`screencap` 330 ms）→ 内环 2–3 fps，**任何"每帧"成本都必须可忽略**；不得在感知路径里加网络调用或落库。
- 内环**不写数据库**、**不进安全网**、**不调模型**（`play_reflex` 期间的唯一模型调用是 Jev 决策，频率=每块一次）。
- 所有设备/应用相关数字（ROI 比例、HSV 阈值、手势词表、预览区、机制开关）**只放 `skills/tetris/calibration.json`**，代码里不得硬编码。
- Jev 只能吃文本；`current_piece` 用**几何掩码**而非字母；非法落点由 `simulator` 排除，模型不可能产出非法动作。
- 每任务结束必须：`ruff format --check`、`ruff check`、`scripts/quality_ratchet.py` 通过，且全量单测不高于既有 14 条基线失败。
- 测试不得依赖真实 key/网络：`systemone.api_key` 与 `systemone.ask` 必须可 monkeypatch（`.env` 查找已抽成 `dotenv_path()` 接缝）。

## Review Focus

1. **抗锯齿/发光边缘**：40×40 归一化后，方块边缘有渐变——单点采样会误判；期望中心 5×5 中位数稳定。
2. **下落块与堆积块同色**：颜色无法区分二者，只有帧差能；期望分离不依赖颜色。
3. **对局中途切换重力**（`切换掉落方向`）：运动矢量反向，合法候选集合必须跟着变，否则策略会给出无效动作。
4. **自定义方块**（`定义方块`）：形状集不是标准 7 种；期望形状取自连通域几何，模拟器接受任意掩码。
5. **广告/弹窗覆盖棋盘**：ROI 仍存在但内容是 UI 文字；期望被识别为"非棋盘"并停手（盲帧），而不是把文字当方块堆。

---

### Task 1: 参考帧语料与加载器

**Files:**
- Create: `skills/tetris/samples/`（3 张参考帧）、`skills/tetris/frames.py`
- Test: `tests/unit/reflex/test_frames_corpus.py`

**Interfaces:**
- Produces: `load_reference_frames(directory: Path | None = None) -> list[tuple[str, bytes, tuple[int, int]]]`（名称、原始字节、尺寸）；`REPO_SAMPLES: Path`。

- [ ] **Step 1: 写失败测试** — 断言至少 3 帧、尺寸为 `(720, 1600)`、字节为 JPEG/PNG 魔数开头。
- [ ] **Step 2: 运行确认失败** — `.venv/bin/python -m pytest tests/unit/reflex/test_frames_corpus.py -q`；预期 `ImportError: frames`。
- [ ] **Step 3: 从报告提取帧并实现 `frames.py`** — 从 `reports/reflex-真机观察.html` 的 `data:image/jpeg;base64,` 片段解出 3 张写入 `samples/`；加载器按文件名排序返回。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交** — `feat(tetris): ship the reference frames as a calibration corpus`。

---

### Task 2: 比例 ROI 与归一化裁剪

**Files:**
- Create: `skills/tetris/roi.py`；Modify: `skills/tetris/calibration.json`
- Test: `tests/unit/reflex/test_roi.py`

**Interfaces:**
- Consumes: `calibration.json` 新字段 `board_roi_ratios: [x0, y0, x1, y1]`（0–1）、`normalized_size: [400, 800]`。
- Produces: `board_crop(image: np.ndarray, calibration: dict) -> np.ndarray`（返回 `400×800×3` float32 RGB）；`roi_pixels(shape, calibration) -> tuple[int,int,int,int]`。

- [ ] **Step 1: 写失败测试** — 合成 720×1600 图，在比例矩形内画已知图案；断言 `board_crop` 的输出形状为 `(800,400,3)` 且图案落在预期格子；断言 `roi_pixels` 返回 `(35,320,465,1180)`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现 `roi.py` + 写入标定字段** — 钳制到图像边界；`board_roi_ratios = [0.0486, 0.2000, 0.6458, 0.7375]`（由 `(35,320,465,1180)` 除以 `720×1600` 得出）。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交**。

---

### Task 3: 无假设网格计数（本计划的关键判据）

**Files:**
- Create: `skills/tetris/grid_probe.py`
- Test: `tests/unit/reflex/test_grid_probe.py`

**Interfaces:**
- Produces: `measure_grid(field: np.ndarray) -> dict`，键为 `pitch_x:int|None, pitch_y:int|None, columns:int|None, rows:int|None, extent_ok:bool`；内部 `pitch(profile) -> int|None`。

- [ ] **Step 1: 写失败测试** — 参数化合成网格 `(columns, rows) in [(10,20),(8,16),(12,24)]`，**测试不告诉被测函数列数**，断言 `measure_grid` 还原出的 `columns/rows` 正确且 `extent_ok is True`；再断言对纯色/文字图像返回 `columns is None`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 对灰度做 `np.diff` 取边缘能量，`pitch` 取峰间距中位数，`columns = round(width / pitch_x)`，`extent_ok = abs(columns*pitch_x - width) <= 2`。
- [ ] **Step 4: 运行确认通过**；并对 3 张参考帧断言 `columns == 10 and rows == 20`（真机证据钉住）。
- [ ] **Step 5: 提交** — 提交信息写明"该测试会在 8×16/10×20 之间做出独立判断，不依赖任何断言"。

---

### Task 4: 格子分类（HSV 中心中位）

**Files:**
- Create: `skills/tetris/cells.py`；Modify: `skills/tetris/calibration.json`（新增 `hsv: {v_min: 65, s_min: 45}`）
- Test: `tests/unit/reflex/test_cells.py`

**Interfaces:**
- Produces: `read_matrix(field: np.ndarray, calibration: dict) -> list[str]`（20 行 × 10 字符，`"0"`/`"1"`）；`cell_hsv(field, calibration) -> list[list[tuple[int,int,int]]]`（供 `calibrate` 逐格复核）。

- [ ] **Step 1: 写失败测试** — 合成 400×800 图：背景 `(26,68,88)`，在 (r,c) 集合涂 `(220,40,40)` 等 7 色；断言 `read_matrix` 精确等于期望矩阵；再断言低于阈值的格子恒为 `"0"`（防"文字即方块"回归）。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 每格取中心 5×5 的 HSV 中位数，`V > v_min and S > s_min` 判 1。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交**。

---

### Task 5: 帧差分离下落块与堆积块

**Files:**
- Create: `skills/tetris/pieces.py`
- Test: `tests/unit/reflex/test_pieces.py`

**Interfaces:**
- Consumes: `read_matrix`。
- Produces: `split_pieces(matrices: list[list[str]]) -> dict`，键 `board_matrix: list[str]`（已抽离下落块）、`current_piece_positions: list[list[int]]`、`current_piece_mask: list[list[int]]`、`motion_vector: list[int] | None`。

- [ ] **Step 1: 写失败测试** — 构造两帧：底部堆叠不变 + 一个 L 形块下移一行 → 断言 `board_matrix` 不含该块、`motion_vector == [1,0]`、`current_piece_positions` 为第二帧位置；再构造"只有堆叠"的两帧 → `motion_vector is None`。
- [ ] **Step 2: 运行确认失败**。
- [ ] **Step 3: 实现** — 取多帧逐格众数为地形；地形外且跨帧连通的 1 为下落块；质心位移按 (行,列) 归一化。
- [ ] **Step 4: 运行确认通过**。
- [ ] **Step 5: 提交**。

---

### Task 6: Jev 报文组装

**Files:**
- Create: `skills/tetris/jev_state.py`
- Test: `tests/unit/reflex/test_jev_state.py`

**Interfaces:**
- Produces: `build_state(board_matrix, pieces, mechanics, next_preview, candidates) -> dict`，键固定为 `board_matrix / current_piece_mask / current_piece_positions / motion_vector / mechanics{gravity_direction,piece_source} / next_preview / candidate_moves[{id,rotation,column}]`。

- [ ] **Step 1: 写失败测试** — 断言键集合精确相等、`board_matrix` 是 20 个长度 10 的字符串、`candidate_moves` 与传入候选一一对应。
- [ ] **Step 2–4: 红 → 实现 → 绿**。
- [ ] **Step 5: 提交**。

---

### Task 7: 模拟器泛化（重力为参数 + 任意形状掩码）

**Files:**
- Modify: `skills/tetris/simulator.py`、`skills/tetris/calibration.json`（`mechanics.gravity_options`、`mechanics.shapes`）
- Test: `tests/unit/reflex/test_tetris_skill.py`（追加）

**Interfaces:**
- Produces: `drop(board, mask, column, *, gravity="down") -> Board | None`；`legal_placements(board, mask, *, gravity=...) -> list[tuple[Placement, mask]]`；`orientations_from_mask(mask) -> tuple[mask, ...]`。

- [ ] **Step 1: 写失败测试** — 断言 `gravity="up"` 时方块停在顶部、`gravity="left"` 时停在左边界；断言自定义掩码（如 2×3 矩形）在四种重力下都能落位且不越界；断言原标准场景结果不变（回归）。
- [ ] **Step 2–4: 红 → 实现 → 绿**；ruff + ratchet 通过。
- [ ] **Step 5: 提交**。

---

### Task 8: Jev 策略（合法候选内排序，含 choice 变体）

**Files:**
- Create: `skills/tetris/policy_jev.py`；Modify: `skills/tetris/policy_dispatch.py`（后端名 `"jev"`）、`skills/tetris/calibration.json`（`policy_backend`、`jev: {mode: "noul"|"choice", min_confidence: 0.05, options_cap: 40}`）
- Test: `tests/unit/reflex/test_policy_jev.py`

**Interfaces:**
- Consumes: `build_state`、`legal_placements`、`artemis.services.systemone.ask/probability`、`policy.choose`（回退）。
- Produces: `decide(state, calibration) -> Action`；`telemetry() -> dict`（`calls/fallbacks/last_error`）。

- [ ] **Step 1: 写失败测试** — 用 `monkeypatch` 假 `systemone.ask`：① 返回指定候选高概率 → 选中该候选；② 全低概率 → 回退搜索且 counters 标 `source=search`；③ `SystemOneUnavailable` → 回退且 `telemetry()["fallbacks"]==1`；④ **属性测试**：随机 200 个棋盘，返回的 `(rotation, column)` 必须属于 `legal_placements`（非法即失败）；⑤ `mode="choice"` 时 criteria 数量等于候选数。
- [ ] **Step 2–4: 红 → 实现 → 绿**。
- [ ] **Step 5: 提交**。

---

### Task 9: `calibrate` 输出升级（逐格复核 + 网格校验）

**Files:**
- Modify: `skills/tetris/calibrate.py`、`artemis/interfaces/cli/commands/reflex.py`
- Test: `tests/unit/reflex/test_calibrate_report.py`

**Interfaces:**
- Produces: `verify_report(image_bytes) -> dict`，键 `grid{columns,rows,extent_ok}`、`matrix`、`cell_hsv`、`motion_vector`、`suspected_sleep_screen: bool`（均值亮度 < 10）。

- [ ] **Step 1: 写失败测试** — 用参考帧断言 `columns==10, rows==20, extent_ok is True`、`suspected_sleep_screen is False`；用全黑图断言 `suspected_sleep_screen is True`；断言 `extent_ok` 为假时 `calibrate` 命令退出码非 0。
- [ ] **Step 2–4: 红 → 实现 → 绿**（`--verify` 开关，默认打印表格）。
- [ ] **Step 5: 提交**。

---

### Task 10: 真机验收用例（存活 ≥60 s、消行 ≥10）

**Files:**
- Create: `tests/e2e/test_device_tetris_reflex.py`；Modify: `skills/tetris/calibration.json`（`restart_flow` 坐标与等待 5 s）
- Test: 自身（`e2e` + `manual` 标记）

**Interfaces:**
- Consumes: `play_reflex`、`calibration.restart_flow`、`read_matrix`（用于独立复核消行）。

- [ ] **Step 1: 写用例（先在设备上手动跑一次确认坐标）** — 流程：唤醒 → 若出现"游戏结束"则按 `restart_flow`（点一次、等 5 s、再点一次）→ 校验棋盘可读（Task 3/4）→ `play_reflex(skill="tetris", seconds=60)` → 断言 ① 会话 `stop_reason != "lost"`；② 由**独立复核**（逐帧 `read_matrix` 统计消行）得到 `lines >= 10`；③ 存活 ≥60 s；④ 不变量：本会话动作记录里 `target_resource_id` 命中 0 次、会话期间 `traces/images/` 新增 0。
- [ ] **Step 2: 运行** — `.venv/bin/python -m pytest tests/e2e/test_device_tetris_reflex.py -m e2e -q`。
- [ ] **Step 3: 失败即按 `systematic-debugging` 定位**（先分清是 CV 读错、手势没生效、还是 Jev 选择被回退吞掉——三者由 `telemetry()` 与逐格 HSV 区分）。
- [ ] **Step 4: 通过后记录数据** — 帧率、抓帧 p50/p95、决策延迟、`source=jev/search` 比例、消行曲线。
- [ ] **Step 5: 提交**。

---

## Self-Review

**Spec coverage:** 参考帧语料（T1）、ROI 与归一化（T2）、棋盘计数不带假设（T3）、格子判定（T4）、下落块分离与运动矢量（T5）、Jev 报文（T6）、重力/自定义形状（T7）、Jev 排序与回退（T8）、可复核报告（T9）、真机验收（T10）——本轮确认的口径全部有归属。

**Step scan:** 每步一个动作 + 一个可检查结果；代码块只出现在测试与算法处，实现步骤给签名与判据。

**Type consistency:** `board_matrix: list[str]`（20×10）、`current_piece_mask: list[list[int]]`、`motion_vector: list[int] | None`（`[drow, dcol]`）在 T5/T6/T8 中一致；`legal_placements(board, mask, *, gravity)` 在 T7/T8 中一致；`Placement(rotation, column)` 沿用既有定义。

**Review Focus 落点:** ①抗锯齿 → T4 的 5×5 中位；②同色块 → T5 不依赖颜色；③重力切换 → T7 + T5 的 `motion_vector`；④自定义形状 → T7 掩码；⑤弹窗覆盖 → T9 的 `extent_ok`/非棋盘判定 + T3 的"纯色/文字返回 None"。

**Proportion:** 计划 ≈ 规格长度，未复述实现代码。

---

## 执行进度与断点（2026-10-12 第二轮：T2–T9 已落地，T10 待真机）

**方式**：Native（会话内逐任务实现，TDD：先写失败测试 → 跑红 → 最小实现 → 跑绿）。
**状态**：T2–T9 全部完成并通过整体评审；**T10 真机验收留到设备阶段**。

| 任务 | 状态 | 提交 / 证据 |
|---|---|---|
| T1 参考帧语料 + 加载器 | ✅ | `d68c4d6`；`skills/tetris/samples/reference-{1,2,3}.jpg` |
| T2 比例 ROI + 归一化 400×800 | ✅ | `c14615e` → `skills/tetris/roi.py`；`board_roi_ratios` + `normalized_size` |
| T3 无假设网格计数 | ✅ | `c38842b` → `skills/tetris/grid_probe.py`；3 张真机帧实测 **10×20，pitch 40，extent 0 px 偏差** |
| T4 格子分类（HSV 中心中位） | ✅ | `4aacea9` → `skills/tetris/cells.py`；真机帧读出 11/15/17 个格，0.29 ms/帧 |
| T5 帧差分离下落块 | ✅ | `aecf4c1` → `skills/tetris/pieces.py`（形状一致性判定） |
| T6 Jev 报文组装 | ✅ | `44a3747` → `skills/tetris/jev_state.py` |
| T7 模拟器泛化（重力为参数 + 任意掩码） | ✅ | `884a519` → `simulator.py` 四种重力 + 掩码，旧调用形式保持可用 |
| T8 Jev 策略（合法候选内排序） | ✅ | `469fb86` → `skills/tetris/policy_jev.py`；`noul`/`choice` 两式，回退=掩码搜索 |
| 集成（P5） | ✅ | `8abb655` → `perception.parse` 接入新链路（比例裁剪 → 测网格 → 格子 → 帧差） |
| T9 `calibrate` 输出升级 | ✅ | `ad95a1d` → `verify_report`/`render_verification` + `--verify`（不合格即非 0 退出） |
| 整体评审后的修复 | ✅ | `592fb89` → 见下「评审三类 Critical」 |

**全量门禁（T9 之后、评审修复之后各跑一次）**：`.venv/bin/python -m pytest -q` → **14 failed, 2848 passed**（= 既有 14 条基线，无新增）；`ruff format --check` / `ruff check` / `scripts/quality_ratchet.py` 通过；`tests/unit/reflex` 167 passed。

**整体评审（fresh reviewer，review-197ffe9..ad95a1d.diff）发现的 Critical，已修并有回归测试**

1. **下落块与堆积块连通**：连通域会把贴住堆积的方块并成"13 格下落块 + 空地形"（每次落定前的那一帧）。改为**形状一致性**判定（可旋转，不可吞并），判不出来就报"无下落块"。
2. **两帧网格尺寸不同**：真实 trace 帧（视频 App 画面实measure 出 67×133）会让 `parse` 抛 `IndexError` → 运行时报 skill_error 而非盲帧停手。现在尺寸不同即不读运动，且网格变化时清空历史。
3. **"是不是棋盘"太松**：`traces/` 里 149 张真实 720×1600 帧中有 **47 张**（视频 App、Android 设置列表）能通过周期性与 extent 判据（pitch 6–8 px）；它们的周期性 0.395–0.802，而三张真棋盘帧只有 0.554–0.561——区间重叠，**没有任何阈值能分开**。因此身份改为**校验**：测出的行列数必须与标定一致（`grid_probe.identity_problem`），不一致即盲帧；`calibrate` 打印同一理由并非 0 退出。

**T10 真机阶段开工前必做**

1. `make preflight` + `ARTEMIS_REQUIRE_CLEAN_TREE=1`（真机前身份断言，见 `branch-base-regression.md`）；
2. 把 `calibration.json: policy_backend` 改为 `"jev"`（默认仍是 `"search"`，出厂默认不依赖网络；`test_the_manifest_switches_backend_by_calibration_file` 钉住这一点）；
3. 先跑 `artemis reflex calibrate --skill tetris --verify`：它现在会**测量**网格并与标定对照、逐格打印 HSV、屏幕太暗会报"设备在睡"；
4. 需要答复的遗留决策（未变）：`restart_flow` 两个"重新开始"按钮坐标 + "点一次 → 等 5 s → 再点一次"的验证；helper APK 是否可改（加"仅截图"端点把反应压到 250 ms 以内）。

**T10 之前值得先量一次的三个数（评审遗留，均未实测）**

| 待量 | 为什么 |
|---|---|
| `systemone_timeout_s: 1.0` 能否覆盖一次带 40 个 `noul` 问题的调用 | 若不够，Jev 永远赢不了，且失败是静默的（counters 只显示 `source=search`） |
| 每块一次决策是否真的成立（`telemetry()["reuses"]` / `calls`） | 计划的内环预算：唯一模型调用 = 每块一次 |
| 参考帧里 3 个贴阈值的空格（row 18 col 9、row 19 col 8–9，V≈50–51 < v_min 65） | 决定 `hsv.v_min/s_min` 是否要下调，否则光照变化时这些格子会翻面 |

**真机阶段已知的行为边界（不是 bug，是设计选择）**

- 贴着堆积的下落块、以及两帧网格尺寸不同时，`parse` 报"无下落块"→ 该帧不决策（宁可不走，也不喂给策略一个不存在的棋盘）；
- 覆盖棋盘的广告/弹窗、尺寸不符的整屏纹理 → `parse` 返回 `None`，运行时按盲帧计数并在超阈值后停手；
- `perception.parse` 返回的状态同时带新链路键（`board_matrix`/`current_piece_mask`/`motion_vector`）与旧的字母棋盘（标准七种才有字母，自定义形状为 `None`）；
- 每次会话开始由 `skill.json: hooks.on_start → session:start` 清空帧历史与上一次决策缓存。
