# 导入依赖矩阵（2026-11-09，historical）

> **Historical snapshot.** Current import boundaries are enforced by
> `tests/unit/architecture/`; current architecture is in
> [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md).

> 审计对象：`integration/10101205-all-fixes` @ `a82d58e`
> 范围：`artemis/`、`mcp_server/`、`third_party/mobile_use/`、`apps/admin_console/`
> 方法：AST 静态解析（见附录脚本），统计**包级**导入边（同包内导入不计）；动态 `importlib` / 字符串导入不计
> 判定依据：[`ARCHITECTURE_CONTRACT.md`](ARCHITECTURE_CONTRACT.md) §2、§10、§11
> 图例：✅ 允许；⚠️ 待迁移（当前有用途，但契约要求最终移走）；❌ 违反契约方向

---

## 1. 口径

- “包级”指 `artemis.<一级>.<二级>`（例如 `artemis.mcp`、`artemis.devices`）、`mcp_server`、`third_party.mobile_use`、`apps.admin_console`。
- 一条 `from a.b import c` 记一条边 `a.b → a.b.c 的包`；`import a.b.c` 同理。
- 只统计跨包边；`artemis.mcp.observation → artemis.mcp.action_types` 这类同包边不计。
- 硬件库（`adbutils`、`uiautomator2`、`pyserial`、`Quartz` 等）记为 `<hw:*>` 目标。
- 本审计只覆盖静态结构，**不代表运行时一定走到**；运行时调用链见 [`CURRENT_CALL_FLOWS.md`](CURRENT_CALL_FLOWS.md)。

---

## 2. 包级边清单

### 2.1 L1 接入层

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.interfaces` | `artemis.drivers` | 14 | ❌ | CLI `ios-smoke`/`ios-calibrate` 直接构造 Driver、UVC、串口（`ios_smoke.py:30-35`、`ios_calibrate.py:30-35`） |
| `artemis.interfaces` | `artemis.reflex` | 21 | ❌ | CLI 直接构造 Capture/Control（`reflex.py:28-30`、`ios_smoke.py:34-35`） |
| `artemis.interfaces` | `artemis.devices` | 4 | ⚠️ | 诊断命令组装 Session |
| `artemis.interfaces` | `artemis.mcp` | 5 | ⚠️ | CLI 与 MCP 执行器耦合 |
| `artemis.interfaces` | `third_party.mobile_use` | 31 | ⚠️ | `run` 直接调 `run_automation`（`commands/run.py:27-37`） |
| `mcp_server` | `artemis.runtime` | 30 | ✅ | 队列/锁/trace store |
| `mcp_server` | `artemis.sdk` | 6 | ✅ | 子进程调用 SDK |
| `mcp_server` | `artemis.clients` | 2 | ⚠️ | 设备状态探测 |
| `artemis.sdk` | `artemis.graph` | 2 | ✅ | SDK 入口选择 Pro 图 |
| `artemis.sdk` | `artemis.clients` | 4 | ⚠️ | 屏幕客户端/Hierarchy backend |
| `apps.admin_console` | `artemis.graph` | 2 | ⚠️ | 通过图读取状态 |
| `apps.admin_console` | `artemis.data_engine` | 13 | ✅ | 回放/查询 |

### 2.2 L2 Agent / 编排

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.agents` | `artemis.mcp` | 29 | ⚠️ | Flash/Validator 直接 import 执行器与 action manifest（`flash/runner.py:86-87`、`validator/validator.py:55`） |
| `artemis.agents` | `artemis.controllers` | 10 | ❌ | Flash/Planner/Checker 直接 import `UnifiedMobileController`（`flash/runner.py:81`、`planner.py:26`、`checker.py:755`） |
| `artemis.agents` | `artemis.tools` | 107 | ✅ | 工具层调用 |
| `artemis.agents` | `artemis.clients` | 2 | ⚠️ | `outputter.py:219` 读 hierarchy backend 文案 |
| `artemis.agents` | `artemis.graph` | 50 | ✅ | 图节点/State |
| `artemis.agents` | `third_party.mobile_use` | 85 | ⚠️ | 日志/类型/上下文基类 |
| `artemis.graph` | `artemis.controllers` | 2 | ❌ | `graph/perception.py:22`、`graph.py` 直接建控制器 |
| `artemis.graph` | `artemis.tools` | 25 | ✅ | 节点工具 |
| `artemis.graph` | `artemis.agents` | 14 | ✅ | 节点实现 |
| `artemis.orchestration` | `artemis.devices` | 2 | ⚠️ | `selector.py` 引用 `CapabilitySet`（L2→L4 DTO） |

### 2.3 L3 交互智能

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.perception` | `artemis.devices` | 12 | ❌ | Port 层反向依赖 L4 DTO：`base.py:8`、`app_automation.py:18` 等 import `devices.capabilities` |
| `artemis.perception` | `artemis.reflex` | 2 | ❌ | `uvc.py:84` 动态 import LocateAnything |
| `artemis.perception` | `<hw:Quartz>` | 1 | ❌ | `vision_ocr.py` 直接使用 macOS 框架 |
| `artemis.control` | `artemis.devices` | 8 | ❌ | `base.py:8`、`hid.py:8` 等 import `devices.capabilities` |
| `artemis.control` | `artemis.reflex` | 2 | ❌ | `hid.py:9` import `reflex.vision.changed` |
| `artemis.safety` | — | 0 | ✅ | 纯原语，无跨包出边（被 agents/drivers/reflex 单向依赖） |
| `artemis.tools` | `artemis.drivers` | 38 | ⚠️ | 工具直接持有 Driver（`tools/base.py` 等） |
| `artemis.tools` | `artemis.agents` | 27 | ⚠️ | Explorer/Diagnoser 工具包裹 Agent |
| `artemis.tools` | `artemis.graph` | 28 | ⚠️ | 工具读 State |
| `artemis.tools` | `artemis.reflex` | 15 | ⚠️ | `reflex_tool.py`、`explorer_tool.py` |
| `artemis.tools` | `third_party.mobile_use` | 58 | ⚠️ | 日志/类型 |

### 2.3.1 `artemis.mcp`（契约 §11 定义为混合目录）

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.mcp` | `artemis.agents` | 9 | ❌ | `action_executor.py:41,51,839,860`、`actuators/adb.py:34` 直接 import Agent 层 |
| `artemis.mcp` | `artemis.controllers` | 8 | ⚠️ | 执行器持有 `UnifiedMobileController` |
| `artemis.mcp` | `artemis.reflex` | 5 | ⚠️ | `actuators/adb.py:38` pinyin、`action_executor.py:313` LocateAnything |
| `artemis.mcp` | `artemis.tools` | 15 | ⚠️ | 工具注册（`action_executor.py:55`） |
| `artemis.mcp` | `artemis.drivers` | 2 | ⚠️ | 设备类型/诊断 |
| `artemis.mcp` | `<hw:adbutils>` | 2 | ⚠️ | `adb_server.py` 直接 ADB |
| `artemis.mcp` | `artemis.core` | 7 | ✅ | ToolDeclaration/ToolFailure |

### 2.4 L4 设备运行时

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.devices` | `artemis.control` | 8 | ⚠️ | Session builder 组装具体 Sink（`ios_uvc.py:10`） |
| `artemis.devices` | `artemis.perception` | 11 | ⚠️ | 组装具体 Source（`ios_uvc.py:14`） |
| `artemis.devices` | `artemis.reflex` | 11 | ❌ | `ios_uvc.py:11-19` 直接组装 `reflex.mouse/vision/locate_anything` |
| `artemis.drivers` | `artemis.devices` | 12 | ✅ | L4 内部 |
| `artemis.drivers` | `artemis.perception` | 17 | ⚠️ | 既有 Port（`base.py`）也有实现（`ocr_engine`、`text_locator`） |
| `artemis.drivers` | `artemis.control` | 2 | ✅ | iOS 驱动用 `ActionResult` |
| `artemis.drivers` | `artemis.reflex` | 14 | ❌ | `factory.py:82-83`、`ios_uvc_hid_driver.py:20-21,210`、`crop.py:45` |
| `artemis.drivers` | `artemis.orchestration` | 4 | ❌ | `factory.py:77,186` import 选择器（L4→L2） |
| `artemis.drivers` | `artemis.safety` | 2 | ⚠️ | `ios_uvc_hid_driver.py:22` `check_in_box` |
| `artemis.drivers` | `<hw:adbutils>` | 5 | ✅ | ADB Adapter 内部 |
| `artemis.clients` | `artemis.runtime` | 17 | ✅ | helper/唤醒 |
| `artemis.clients` | `third_party.mobile_use` | 12 | ⚠️ | 类型/日志 |

### 2.5 横向基础设施

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `artemis.memory` | `artemis.agents` | 6 | ❌ | 反向依赖 L2 压缩器（`transcript.py:356`、`chunking.py:257`） |
| `artemis.runtime` | `artemis.graph` | 2 | ❌ | `degradations.py:123` import checkpoints |
| `artemis.runtime` | `<hw:adbutils>` | 2 | ⚠️ | 设备池/诊断 |
| `artemis.core` | `artemis.clients` | 4 | ❌ | 诊断直接构造客户端 |
| `artemis.core` | `artemis.mcp` | 2 | ⚠️ | `device_smoke.py` 借用 `mcp.adb_server` |
| `artemis.core` | `artemis.runtime` | 15 | ⚠️ | 诊断/探针 |
| `artemis.core` | `artemis.toolchain` | 12 | ⚠️ | 依赖链探针 |
| `artemis.core` | `third_party.mobile_use` | 15 | ⚠️ | 类型 |
| `artemis.utils` | `artemis.config` | 4 | ❌ | 通用工具读配置 |
| `artemis.utils` | `artemis.context` | 2 | ❌ | 通用工具读上下文 |
| `artemis.utils` | `artemis.memory` | 2 | ❌ | `task_tree.py:651` |
| `artemis.utils` | `third_party.mobile_use` | 19 | ⚠️ | 日志/类型 |
| `artemis.data_engine` | `artemis.context` | 4 | ⚠️ | 读上下文 |
| `artemis.telemetry` | `third_party.mobile_use` | 2 | ⚠️ | 日志 |
| `artemis.config` | `artemis.llm` | 4 | ✅ | 配置构造 LLM |
| `artemis.config` | `artemis.platform` | 6 | ✅ | Host PAL |
| `artemis.llm` | `third_party.mobile_use` | 2 | ⚠️ | 类型 |

### 2.6 第三方与边界

| from | to | 次数 | 判定 | 说明 |
|---|---|---|---|---|
| `third_party.mobile_use` | `artemis.agents/graph/sdk/controllers/drivers/...` | 20+ 条 | ⚠️ | vendored 包反向依赖 `artemis`，形成双向耦合；`tests/unit/test_third_party_boundary.py` 只约束名字存在性，不约束方向 |
| `third_party.mobile_use` | `<hw:adbutils>`、`<hw:uiautomator2>` | 6 | ⚠️ | 第三方自带 ADB 依赖，落在 L1 入口路径上 |

说明：上表只列与架构判定相关的边；`config/context/services` 等横向边（例如 `agents → tools 107`、`tools → utils 68`）未全部展开，完整清单用附录脚本复跑。

---

## 3. 关键违规明细（file:line）

| 规则（契约） | 证据 |
|---|---|
| L2 不得依赖设备实现（§2 L2、§10.2） | `artemis/agents/flash/runner.py:81`、`agents/planner/planner.py:26`、`agents/checker/checker.py:755`、`graph/perception.py:22` → `controllers.unified_controller` |
| L3 不得依赖 L4 DTO/实现 | `perception/base.py:8`、`control/base.py:8`、`control/hid.py:8` → `devices.capabilities` |
| L3 不得依赖 Reflex 实现 | `control/hid.py:9` → `reflex.vision`；`perception/uvc.py:84` → `reflex.locate_anything` |
| L4 不得依赖 Agent/Graph | `drivers/factory.py:77,186` → `orchestration.selector` |
| L4 不得依赖 Reflex 特殊通道 | `devices/ios_uvc.py:11,15,19`、`drivers/factory.py:82-83`、`drivers/ios/ios_uvc_hid_driver.py:20-21,210`、`drivers/ios/crop.py:45` |
| L1 不得直接打开设备 | `interfaces/cli/commands/ios_smoke.py:30-35`、`ios_calibrate.py:30-35`（构造 UVC/串口/驱动） |
| 横向模块不得依赖 L2 | `memory/transcript.py:356`、`memory/chunking.py:257` → `agents.flash.context_compressor`；`runtime/degradations.py:123` → `graph.checkpoints` |
| `utils/` 不得含领域依赖 | `utils/coordinates.py`（坐标领域）、`utils/task_tree.py:651` → `memory`、`utils` → `config/context` |
| `core/` 只放稳定 DTO/Ports | `core/diagnostics/*` → `clients/runtime/toolchain/mcp`（诊断属于 Host 能力，不在 core 契约内） |

---

## 4. 与契约方向的对照结论

1. **L3 ↔ L4 的 DTO 归属是当前最大的结构性倒挂**：`CapabilitySet` 定义在 `artemis/devices/`，却被 `perception/base.py`、`control/base.py` 这类 L3 Port 反向导入。ADR-001 明确后应把 DTO 移到 `core`（或 `core/contracts`），转换层只保留一份。
2. **L4 对 `reflex/` 的依赖集中在 iOS 组合**：`devices/ios_uvc.py` 与 `drivers/factory.py` 把 `reflex.capture/control/mouse/vision` 当作实现直接组装。这是 G8 的具体形态，也是 Phase 4 的主要工作。
3. **真正的入口旁路是 `third_party/mobile_use`**：CLI 第一跳、`AgentBase`、`UnifiedMobileControllerBase` 都在其中，且反向 import `artemis.agents/graph`。Phase 3 若不处理这一层，“Session 成为唯一设备边界”无法达成。
4. **横向模块已完成大部分解耦，但仍有 3 条反向依赖**（memory→agents、runtime→graph、utils→memory/config/context），建议列入 Phase 6/9 的清理清单。
5. **当前没有任何 Architecture Test**：上述边全部没有任何测试或 CI 门禁阻止新增；`tests/unit/test_third_party_boundary.py` 只做名字存在性检查。Phase 0 的建议是为 `tests/architecture` 增加规则，见执行计划 §6.1。

---

## 5. 附录：可复现脚本

以下脚本只读，输出 JSON（边 + 违规候选）。保存为临时文件即可复跑：

```python
import ast, collections, json, pathlib, sys

PROJECT = pathlib.Path(".").resolve()
SOURCES = {
    "artemis": PROJECT / "artemis",
    "mcp_server": PROJECT / "mcp_server",
    "third_party.mobile_use": PROJECT / "third_party" / "mobile_use",
    "apps.admin_console": PROJECT / "apps" / "admin_console",
}
HW = {
    "adbutils",
    "uiautomator2",
    "serial",
    "usb",
    "hid",
    "uvc",
    "evdev",
    "pynput",
    "screeninfo",
    "mss",
    "Quartz",
    "AppKit",
    "win32api",
    "win32con",
    "pywinauto",
}


def pkg(mod):
    parts = mod.split(".")
    if parts[0] == "artemis" and len(parts) > 1:
        return ".".join(parts[:2])
    if parts[0] == "third_party":
        return "third_party.mobile_use"
    if parts[0] == "apps":
        return ".".join(parts[:2])
    return parts[0]


def modname(path, root, prefix):
    rel = path.relative_to(root).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join([prefix, *parts]) if parts else prefix


edges = collections.Counter()
for prefix, root in SOURCES.items():
    if not root.exists():
        continue
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        mod = modname(path, root, prefix)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                targets.add(node.module)
                targets.update(f"{node.module}.{a.name}" for a in node.names)
        for t in targets:
            a, b = pkg(mod), pkg(t)
            if a != b and b.split(".")[0] in {"artemis", "mcp_server", "third_party", "apps"}:
                edges[(a, b)] += 1
            if t.split(".")[0] in HW:
                edges[(a, f"<hw:{t.split('.')[0]}>")] += 1
print(
    json.dumps([{"from": a, "to": b, "count": c} for (a, b), c in sorted(edges.items())], indent=2)
)
```
