# Flash 模式问题记录

> 记录时间：2026-10-09
> 环境：macOS 主机 · 设备 `EUWSOBXCZLPFNJF6`（OPPO PEMM00 / Android 13 / SDK 33）
> 模型：`config/artemis.jsonc` 的 `default` = `openai` + `deepseek-flash`（经 `OPENAI_BASE_URL=https://api.deepseek.com`）
> 关键环境事实：`GOOGLE_API_KEY` / `GEMINI_API_KEY` **为空**；`OCR_API_KEY` 为空；`.env` 的 `ARTEMIS_DEFAULT_PROFILE=flash`

## 0. 摘要

- **Flash 在当前环境下完全不可用**：一次实测运行在 **12.4 秒**即失败，从未进入第一步。
- 根因是 **F1**（下）——不是模型能力、不是设备、不是网络。
- Flash 与 Pro 共享同一批底层缺陷，但**同一缺陷在两个 profile 下的致命性不同**：Pro 是后台降级（warning），Flash 是初始化期硬失败。
- 本文件同时记录 Flash 专属问题（F*）与跨模式共享问题（S*）。

### 四次运行对照（同一设备、同一模型配置）

| session | 时间 | profile | 结果 | 备注 |
|---|---|---|---|---|
| `e5e072bd` | 15:27 | pro | completed | 4×inconclusive（终审 400） |
| `f6e2418b` | 21:59 | pro | completed | 1×inconclusive（结构化输出 400） |
| `b48e668e` | 22:14 | pro | completed | **2×passed** |
| `52a8f964` | 22:44 | **flash** | **failed（12.4s）** | **F1：初始化即失败** |
| `563f990c` | 22:49 | pro | completed | **4×passed**（过程中命中 S1/S4/S6） |

---

## F1【致命】FlashRunner 初始化即失败：step summarizer 硬编码 Google

**现象**（`traces/52a8f964-.../stdout.log`）：

```
ℹ [web_1791470693_52a8f964] Invoking FlashRunner reactive loop...
❌ Error running automation: 1 validation error for ChatGoogleGenerativeAI
   Value error, API key required for Gemini Developer API…
   input_value={'model': 'gemini-3.5-fla...e', 'google_api_key': ''}
```

**代码链**：

1. `config/artemis.jsonc` → `agent.flash.step_summarizer = {enabled: true, model: "gemini-3.5-flash-lite"}`
2. `artemis/agents/flash/runner.py:163-173`（在 `__init__` 内，**无 try/except**）：
   ```python
   self.summarizer = (
       VisualStepSummarizer(ctx, model_name=self.step_summarizer_cfg.model, ...)
       if self.step_summarizer_cfg.enabled      # 默认 true
       else None
   )
   ```
3. `artemis/agents/flash/summarizer.py:165-173`：
   ```python
   target_model = model_name or "gemini-2.5-flash-lite"
   if model_name:
       self._llm = get_google_llm(model_name=target_model, temperature=0.0)   # ← 必然走这条
   else:
       self._llm = get_llm(ctx, name="summarizer", is_utils=True)
   except Exception:
       self._llm = get_google_llm(model_name=target_model, temperature=0.0)   # ← 兜底也是 Google
   ```
   `model_name` 恒非空 → **无条件 `get_google_llm`** → `ChatGoogleGenerativeAI(api_key="")` → pydantic 校验错 → 异常逸出 `FlashRunner.__init__`。

**为什么 Pro 不受影响**：Pro 的摘要由 `SummarizerNode` → **后台 dispatch** 到 step-memory lens，失败只记 warning（实测 5/3/6 次失败，run 全部 completed）。Flash 是**同步急切构造**，失败即致命。

**上游对照**：ISSUE #138 / #95 / #167（修复 PR #168 / #141 / #137 均未合并）。

**修复方向**：
- 让 lens 走 provider-aware 路由（`get_llm(ctx, name="summarizer", is_utils=True)`，把 `step_summarizer.model` 当**模型名覆盖**而非"强制 Google"）；
- `FlashRunner` 的构造失败**不致命**：`try/except` + warning 降级，或改惰性构造；
- 单测：provider=openai 且无 Google key 时，`FlashRunner` 必须能初始化成功。

**临时绕行**：
- 用 Pro；或 `agent.flash.step_summarizer.enabled = false`（`TranscriptLedger(step_memory=None)` 是受支持配置）；或补 `GOOGLE_API_KEY`。

---

## F2【潜在致命】`_init_llm` 的兜底同样硬编码 Google

`artemis/agents/flash/runner.py:280-287`：

```python
def _init_llm(self):
    try:
        return get_llm(self.ctx, name="operator")
    except Exception as e:
        logger.warning(f"Failed to get operator LLM from config, using default: {e}")
        return RobustChatModelWrapper(get_google_llm(model_name="gemini-2.5-flash"), self.ctx)
```

若 `get_llm(..., name="operator")` 因任何原因抛错，兜底会**直接要求 Google key** → 同样在初始化路径上致命，且日志把真实原因（配置解析失败）淹没在 warning 里。建议兜底改为"抛出可诊断的错误"或随配置 provider 解析。

---

## F3【已修复】tool 结果交错破坏 OpenAI 兼容协议

- 缺陷：`tool_result_messages` 在 `carrier=="human"` 时返回 `[ToolMessage, HumanMessage]`，而各 agent 循环**逐调用** `extend` → 同一轮的 tool 消息之间被插入 `HumanMessage` → 严格 provider 报
  `An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`。
- Flash 路径同样暴露（`flash/runner.py` 原 861-867 行逐调用发射）。
- 已修：新增 `tool_result_batch_messages` / `order_tool_result_messages`（`artemis/tools/tool_wrapper.py`），并把 Flash 的发射改为**整轮收集后一次发射**（`flash/runner.py` `tool_reply` 收集器 + 循环末尾 flush）。已验证：Pro 运行 `insufficient tool messages` 计数从 1 → 0。

---

## F4【设计限制，非缺陷】Flash 缺少 Pro 的护栏

按设计，Flash **没有**：Planner/`task_plan`、执行前 Safety Net、Checker（checkpoints/final）、ADB 指令、最终报告。因此：

- 没有计划 → 长任务只能靠"历史压缩 + 备忘录"维持方向；
- 没有 Safety Net → 目标漂移不会被拦截；
- 没有 Checker → **不会产出机器可读的验证结论**（`run_outcome.tests` 恒为空）。

选型原则：需要"计划 / 校验 / 报告 / ADB 诊断"时用 Pro，**任务长度本身不是理由**（Flash 默认不限步数）。

---

## 跨模式共享问题（同样影响 Flash）

### S1【已在生产命中】`hopper` 的 provider 被 `default` 污染

`config/artemis.jsonc` 的 `hopper` 只写了 `model: gemini-3.5-flash-lite`，未写 provider → 继承 `default.provider = openai` → **把 Gemini 模型名发给 DeepSeek**。

`563f990c` 实测（22:50:21）：

```
ℹ LLM call failed permanently (bad_request): … 'The supported API model names are deepseek-flash,
   deepseek-v4-pro, but you passed gemini-3.5-flash-lite.'
❌ Hopper LLM invocation failed: … you passed gemini-3.5-flash-lite
ℹ Action failed, capturing failure screenshot...
```

一次 `launch_app`（走 `find_package` → hopper）因此失败。`object_detector` 同型（`openai/gemini-robotics-er-2-preview`）。

**修复**：在 `nodes` 里给这些节点显式写 `provider`（或把 Google 依赖整体改为不依赖 Google）。上游对照：PR #151 / #105 / #157 / #158。

### S2 history chunker 硬编码 Google（影响两种 profile 的长任务压缩）

`artemis/memory/chunking.py:326-335` 的 `_get_llm` / `_get_fallback_llm` 亦用 `get_google_llm`。L2/L3 历史分块由它生成 → 在无 Google key 时，**长任务的上下文压缩会静默降级**（Pro 与 Flash 共用）。

### S3 helper 的两道阻断（本机）

1. `is_service_enabled`（`helper_manager.py:405-411`）用**短组件名精确匹配**（`com.artemis.helper/.ArtemisAccessibilityService`），而 ROM 存的是**展开形式**（`com.artemis.helper/com.artemis.helper.ArtemisAccessibilityService`）→ 永远判为未启用；`_enable_service` 会把短名**追加**进设置，且回读仍失败 → 每次运行都会**打开无障碍设置页**并报 "rejected enabling…"。上游对照：ISSUE #109 / PR #169。
2. **token 投递被 ROM 拒绝**：`Permission Denial: broadcasting … to com.artemis.helper/.TokenReceiver requires android.permission.WRITE_SECURE_SETTINGS`（发送者 shell uid 2000）→ `TokenStore` 是纯内存的，永不置位 → 所有需鉴权端点 401。而 `push_token()` 以 `"Broadcast completed" in output` 判成功，**误报成功**（`am` 在权限被拒时照样打印该行）。
   `563f990c` 实测：attach 成功（`v1.2.0, protocol 2`）→ 紧接着 `/snapshot` 401 → 降级 UIAutomator2（见 `run_outcome.json` 的 `environment.switches`）。

### S4【新】`log_analyzer` 未纳入批量发射，且过滤不对称

`artemis/agents/log_analyzer/log_analyzer.py:232` 与 `286-295`：

```python
current_messages.append(response)                     # ← assistant 带【全部】tool_calls 进入
active_tool_calls = [tc for tc in response.tool_calls if name != "google_search"]   # ← 过滤
tool_outputs = await asyncio.gather(*(run_tool(tc) for tc in active_tool_calls))
for tm in tool_outputs:
    current_messages.append(tm)                       # ← 只为过滤后的调用追加结果
```

只要模型在同一轮里调用了被过滤的 `google_search`，就会出现"**声明的 tool_calls 多于随后的 tool 消息**" → 与 F3 同一个 400 家族。该文件**不在**已改用共享批量发射器的 8 个文件之列。

### S5【新】Operator 原地改写 `response.tool_calls`

`artemis/agents/operator/operator.py:563`：`response.tool_calls = [tool_call]`。把多调用响应改写成单调用后，**任何按"原始 tool_calls"发射结果的路径都会与实际不符**。与本轮 S7 的未定因相关，建议排查。

### S6【新】`search_logs` 的时间格式契约过窄（单次运行失败 18 次）

`artemis/tools/mobile/log_utils.py:24-39` 的 `parse_log_time` 要求 **`MM-DD HH:MM:SS[.ffffff]`**（内部拼当前年份）；`resolve_time` 另接受**相对秒数**。模型传入裸时钟时间 `22:50:30` → `ValueError: Unsupported time format: 22:50:30`。

`563f990c` 实测：`search_logs` 在 22:53:30 与 22:53:54 各失败 9 次（共 18 次），错误原样返回给模型后模型**仍重复同一格式**。建议：接受 `HH:MM:SS`（按当天补齐）并在错误文本中列出可接受格式。

### S7【未定因】"insufficient tool messages" 出现在守卫之后

`563f990c` 的 22:53:22 有一次同族 400（`log_analyzer` / `analyze_logs` 路径）。已确认的事实：

- 我的 L3 钩子在拒绝时刻打印了实时形状：4 / 6 / 6 / 4 个调用，**每个 assistant 后面紧跟同数量的 tool 消息**（表面平衡）；
- 我的 L2 守卫（`repair_tool_call_pairing`）报告 **0 次修复** → 交给 LangChain 之前是平衡的；
- DB 里该请求的记录中，`AIMessageChunk` **没有 `tool_calls` 字段**（序列化产物，不能作为实发内容判据）。

**结论**：失衡若存在，产生于**守卫之后、provider 之前**（LangChain 的 message→request 转换层）。最可疑的三条线索：`AIMessageChunk` 与 `tool_call_chunks` 的转换、S5 的原地改写、S4 的过滤不对称。
**下一步定位**：在 LangChain 序列化**前后各打印一次形状**对比；或用最小复现直接向 provider 发一次。

---

## 建议动作（按优先级）

| 优先级 | 动作 | 类型 |
|---|---|---|
| P0 | 修 F1（provider-aware lens + 构造失败不致命）——否则 Flash 完全不可用 | 代码 |
| P0 | 补 `GOOGLE_API_KEY` **或** 统一关掉所有 Google 依赖（F1/S1/S2 一并缓解） | 配置 |
| P1 | 修 S1（节点 provider 显式化） | 配置 |
| P1 | 修 S3-1（按组件判定启用状态）+ S3-2（token 通道 / `push_token` 真伪判定） | 代码 |
| P1 | 修 S4/S5（统一走批量发射；不再原地改写 tool_calls） | 代码 |
| P2 | 修 S6（放宽时间格式 + 错误文本给出可接受格式） | 代码 |
| P2 | 定位 S7（序列化前后形状对比） | 诊断 |
| P2 | `.env` 设 `ARTEMIS_HIERARCHY_BACKEND=uiautomator` 以消除 helper 的 30–60s 无效等待与设置页弹窗 | 配置 |
