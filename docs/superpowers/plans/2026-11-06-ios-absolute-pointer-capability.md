# iOS 绝对指针能力位（批次 1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让"这台设备能不能一步把指针放到绝对位置"成为一个**能力位**（`supports_absolute_pointer`），并让 `requires_pointer_precheck` 从"声明了没人读"变成"推导出来的、有唯一消费者"的字段。

**Architecture:** 能力真相来自控制层（`HidControlSink` 由它持有的 HID 控制实例推导），由 `DeviceSession` 聚合、由 driver 实例转述；`select_composition` 只做纯函数推导，不硬编码；点击路径用同一个推导结果决定走绝对点击还是指针环。批次 1 只动"接口与能力模型"，不搬模块、不做 SafetyNet 层化（那是批次 3/4）。

**Tech Stack:** Python 3.12、pytest、frozen dataclass（`CapabilitySet` / `Composition`）、TDD。

**Spec:** `docs/artemis-target-architecture.md`（§5 能力矩阵、§7 SafetyNet、§13 与 iOS UVC 的关系）；差距盘点见本次会话的对照表（§10 吸收表 = 批次 3/4，§9 遥测 = 批次 5）。

## Global Constraints

- 全量回归基线：`uv run pytest tests/unit -q` = **14 failed / 3535 passed / 9 skipped**（`admin_console` 2、`agents/test_explorer` 9、`agents/test_video_analyzer` 3）。本计划的任何一批都不得新增失败。
- `uv run ruff check` 必须全过；`scripts/quality_ratchet.py` 的 `broad_exception_handlers` / `type_ignore_comments` 计数相对 HEAD **增量为 0**（当前 773 / 20，基线 754 / 18 是既有欠账）。
- 能力位是**契约**：新增字段一律默认 `False`，任何"平台判断"都不能替代它（`if platform == "ios"` 不得新增）。
- 一个事实只写一次：`requires_pointer_precheck` 只允许由 `orchestration/selector.py::requires_precheck()` 推导，其它地方只能读。
- 提交风格沿用仓库：`<type>(<scope>): <subject>`，body 说明"为什么"并附测试数字。
- 每个任务结束都跑一次定向测试 + `uv run pytest tests/unit -q`（允许 165 s）。

## Review Focus（最可能咬人的五种输入，落在对应任务的测试里）

1. **老固件没有 `amove`**：控制层只有 `mouse_move`/`mouse_click` → 能力位必须为 `False`、`requires_pointer_precheck` 必须为 `True`，点击必须仍走指针环（Task 1 + Task 3）。
2. **`HidControlSink` 的类属性 vs 实例属性**：类属性是保守契约（相对指针 + 需要预检），实例必须按持有控制层改写；有测试在读类属性（`test_app_and_composite.py:55`），不能把它们改成实例才有的行为。
3. **Android（无指针）**：`supports_relative_pointer=False` 时，`requires_pointer_precheck` 必须是 `False`，且 `select_composition` 仍选 `app` 组合（Task 2）。
4. **能力谎报**：某 sink 声称 `supports_absolute_pointer=True` 但控制层没有 `mouse_move_abs`/`mouse_click_abs` → 点击必须**拒绝**（`absolute_unavailable`），而不是抛 `AttributeError`（Task 3）。
5. **聚合**：`DeviceSession.capabilities` 把多个 sink 的布尔值做 OR；只有一个 sink 时聚合值必须与 sink 完全一致（Task 1 的测试钉住这一点，避免将来加第二个 sink 时静默改变语义）。

---

### Task 1: 能力字段 + 控制层推导

**Files:**
- Modify: `artemis/devices/capabilities.py:12-25`
- Modify: `artemis/control/hid.py:48-60`（类属性 + `__init__`）
- Test: `tests/unit/drivers/test_capability_flags.py`
- Test: `tests/unit/devices/test_ios_uvc_session.py`
- Test: `tests/unit/control/test_hid_sink.py`

**Interfaces:**
- Produces: `CapabilitySet.supports_absolute_pointer: bool = False`
- Produces: `artemis.control.hid.has_absolute_pointer(control) -> bool`
- Produces: `HidControlSink.capabilities`（实例）按控制层推导：`supports_absolute_pointer`、`requires_pointer_precheck = not supports_absolute_pointer`

- [ ] **Step 1: 写失败测试（三类 sink）**

`tests/unit/control/test_hid_sink.py` 追加：

```python
def _sink(control):
    from artemis.control.hid import HidControlSink
    from artemis.reflex.mouse import PointerSession
    session = PointerSession(control, frame_size=(449, 974), gain=(2.0, 2.0))
    return HidControlSink(session, controller=None)


class _RelativeOnly:
    def mouse_move(self, dx, dy): ...
    def mouse_click(self): ...
    def mouse_home(self): ...
    def preflight(self): return {}


class _AbsolutePointer(_RelativeOnly):
    def mouse_move_abs(self, x, y): ...
    def mouse_click_abs(self): ...


def test_a_relative_only_control_advertises_a_precheck():
    caps = _sink(_RelativeOnly()).capabilities
    assert caps.supports_relative_pointer is True
    assert caps.supports_absolute_pointer is False
    assert caps.requires_pointer_precheck is True


def test_an_absolute_pointer_report_removes_the_precheck():
    caps = _sink(_AbsolutePointer()).capabilities
    assert caps.supports_absolute_pointer is True
    assert caps.requires_pointer_precheck is False


def test_the_class_attribute_stays_the_conservative_contract():
    from artemis.control.hid import HidControlSink
    assert HidControlSink.capabilities.supports_absolute_pointer is False
    assert HidControlSink.capabilities.requires_pointer_precheck is True
```

`tests/unit/drivers/test_capability_flags.py` 追加：

```python
def test_the_default_is_relative_pointer_without_absolute_positioning():
    from artemis.devices.capabilities import CapabilitySet
    caps = CapabilitySet()
    assert caps.supports_absolute_pointer is False
    assert caps.supports_absolute_tap is False
```

`tests/unit/devices/test_ios_uvc_session.py` 追加：

```python
def test_the_session_aggregates_the_sinks_absolute_pointer_capability():
    class Absolute(Control):
        def mouse_move_abs(self, x, y): ...
        def mouse_click_abs(self): ...

    absolute = build_ios_uvc_session(Capture(), Absolute())
    relative = build_ios_uvc_session(Capture(), Control())

    assert absolute.capabilities.supports_absolute_pointer is True
    assert absolute.capabilities.requires_pointer_precheck is False
    assert relative.capabilities.supports_absolute_pointer is False
    assert relative.capabilities.requires_pointer_precheck is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/control/test_hid_sink.py tests/unit/drivers/test_capability_flags.py tests/unit/devices/test_ios_uvc_session.py -q`
Expected: `AttributeError: 'CapabilitySet' object has no attribute 'supports_absolute_pointer'`（或 `TypeError: __init__() got an unexpected keyword`）。

- [ ] **Step 3: 实现能力字段**

`artemis/devices/capabilities.py`，在 `supports_relative_pointer` 之后加：

```python
    #: The control can place the pointer in one absolute report (`amove`/`aclick`) and
    #: click there: the position is deterministic, so a pre-click pointer check adds
    #: nothing. Distinct from ``supports_absolute_tap`` — that one is a digitizer that
    #: lands a tap at absolute coordinates without a pointer at all.
    supports_absolute_pointer: bool = False
```

- [ ] **Step 4: 控制层推导**

`artemis/control/hid.py`：`import` 区加 `from dataclasses import replace`（若未导入），模块内加：

```python
def has_absolute_pointer(control) -> bool:
    """True when the control can place the pointer with one absolute report."""
    return callable(getattr(control, "mouse_move_abs", None)) and callable(
        getattr(control, "mouse_click_abs", None)
    )
```

`HidControlSink.__init__` 末尾：

```python
        # The class attribute is the conservative contract (relative pointer, mandatory
        # precheck). This instance reports what its control can actually do, so the
        # capability cannot be "advertised but unused" again.
        absolute = has_absolute_pointer(self._control)
        self.capabilities = replace(
            type(self).capabilities,
            supports_absolute_pointer=absolute,
            requires_pointer_precheck=not absolute,
        )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/control tests/unit/devices tests/unit/drivers -q`
Expected: 全绿（既有用例不受影响；`test_app_and_composite.py` 仍读类属性）。

- [ ] **Step 6: 提交**

```bash
git add artemis/devices/capabilities.py artemis/control/hid.py \
        tests/unit/control/test_hid_sink.py tests/unit/drivers/test_capability_flags.py \
        tests/unit/devices/test_ios_uvc_session.py
git commit -m "feat(capabilities): report whether the pointer can be placed absolutely"
```

---

### Task 2: selector 单一推导 + 矛盾检测

**Files:**
- Modify: `artemis/orchestration/selector.py:56-130`
- Test: `tests/unit/orchestration/test_selector.py`

**Interfaces:**
- Consumes: `CapabilitySet.supports_absolute_pointer`
- Produces: `artemis.orchestration.selector.requires_precheck(capabilities: CapabilitySet) -> bool`
- `Composition.requires_pointer_precheck` 从此只由 `requires_precheck()` 赋值

- [ ] **Step 1: 写失败测试**

`tests/unit/orchestration/test_selector.py` 追加：

```python
def test_the_precheck_flag_is_derived_from_the_capabilities():
    relative = select_composition(_ios())                       # 相对指针，无绝对报告
    absolute = select_composition(_ios(capabilities=CapabilitySet(
        supports_relative_pointer=True,
        supports_absolute_pointer=True,
        supports_text_input=True,
    )))

    assert relative.requires_pointer_precheck is True
    assert absolute.requires_pointer_precheck is False
    assert "absolute pointer" in relative.reason.lower() or relative.kind == "uvc-hid"


def test_an_app_device_never_requires_a_pointer_precheck():
    profile = DeviceProfile(platform="android", device_id="adb", capabilities=ANDROID,
                            screen_size=(1080, 2400))
    assert select_composition(profile).requires_pointer_precheck is False


def test_an_absolute_pointer_without_a_pointer_is_a_contradiction():
    contradictory = CapabilitySet(supports_absolute_pointer=True)  # no relative pointer
    with pytest.raises(CompositionError) as excinfo:
        select_composition(_ios(capabilities=contradictory))
    assert "absolute pointer" in str(excinfo.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/orchestration/test_selector.py -q`
Expected: 两个新用例失败（`requires_pointer_precheck` 仍是硬编码的 `True`；矛盾用例没抛错）。

- [ ] **Step 3: 实现推导**

`artemis/orchestration/selector.py`：

```python
def requires_precheck(capabilities: CapabilitySet) -> bool:
    """A pointer the click path must verify first: relative, and not absolute.

    The single derivation. `Composition.requires_pointer_precheck` is read by smoke and
    by logs; the click path uses the same capability, so the flag can never claim a
    precheck the path does not perform.
    """
    return bool(capabilities.supports_relative_pointer) and not bool(
        capabilities.supports_absolute_pointer
    )
```

`select_composition()` 开头加矛盾检测：

```python
    if capabilities.supports_absolute_pointer and not capabilities.supports_relative_pointer:
        raise CompositionError(
            "capabilities claim an absolute pointer without a relative pointer: one of"
            " the two is mis-declared (a pointer belongs to a HID control sink)"
        )
```

三处 `requires_pointer_precheck=` 改为 `requires_pointer_precheck=requires_precheck(capabilities)`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/orchestration -q`
Expected: 全绿；既有用例（`IOS_RIG` 无绝对指针）仍得到 `True`。

- [ ] **Step 5: 提交**

```bash
git add artemis/orchestration/selector.py tests/unit/orchestration/test_selector.py
git commit -m "refactor(orchestration): derive the precheck flag from the capabilities"
```

---

### Task 3: 点击路径消费同一条推导（含谎报拒绝）

**Files:**
- Modify: `artemis/devices/ios_uvc.py:60-100`
- Modify: `artemis/control/hid.py::tap_abs`
- Test: `tests/unit/devices/test_ios_uvc_session.py`
- Test: `tests/unit/control/test_hid_sink.py`

**Interfaces:**
- Consumes: `has_absolute_pointer(control)`（Task 1）、`HidControlSink.capabilities`
- Produces: `build_ios_uvc_session(...)` 把 `absolute=has_absolute_pointer(control)` 传给 `SessionTapController`；`HidControlSink.tap_abs` 在能力谎报（声称绝对指针但没有方法）时返回 `absolute_unavailable`

- [ ] **Step 1: 写失败测试**

`tests/unit/devices/test_ios_uvc_session.py` 追加：

```python
def test_the_controller_is_told_whether_absolute_positioning_is_available():
    absolute = build_ios_uvc_session(Capture(), _Absolute())
    relative = build_ios_uvc_session(Capture(), Control())

    assert absolute.pointer is not None
    assert absolute.control[0].controller.absolute is True
    assert absolute.control[0].controller._absolute_ready() is True
    assert relative.control[0].controller.absolute is False
    assert relative.control[0].controller._absolute_ready() is False
```

`tests/unit/control/test_hid_sink.py` 追加（谎报：能力说有、控制层没有方法）：

```python
def test_a_claimed_absolute_pointer_without_the_methods_refuses_to_click():
    from artemis.control.hid import HidControlSink
    from artemis.devices.capabilities import CapabilitySet
    from artemis.reflex.mouse import PointerSession, SessionTapController
    from types import SimpleNamespace

    session = PointerSession(_RelativeOnly(), frame_size=(449, 974), gain=(2.0, 2.0))
    controller = SessionTapController(session, capture=None, detector=None, absolute=True)
    sink = HidControlSink(session, controller)
    sink.capabilities = CapabilitySet(supports_relative_pointer=True,
                                      supports_absolute_pointer=True)   # 谎报

    result = asyncio.run(sink.tap_abs(200, 300))
    assert result.ok is False
    assert result.incident == "absolute_unavailable"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/devices/test_ios_uvc_session.py tests/unit/control/test_hid_sink.py -q`
Expected: `AttributeError: ... has no attribute 'absolute'/-'absolute_ready'` 或 `build_ios_uvc_session` 未传 `absolute` 导致断言 False；谎报用例返回 `ok=True` 或抛 `AttributeError`。

- [ ] **Step 3: 实现（两处小改动）**

`artemis/devices/ios_uvc.py`：在 `controller = SessionTapController(` 之前：

```python
    from artemis.control.hid import has_absolute_pointer

    absolute_pointer = has_absolute_pointer(control)
```

并把 `absolute=absolute_pointer` 作为 `SessionTapController(...)` 的参数（它已有 `absolute: bool = True`）。

`artemis/control/hid.py::tap_abs` 开头（只挡"能力谎报"一种情况）：

```python
        # The capability is the contract, the methods are the truth: a sink that claims an
        # absolute report without them would otherwise drift into the pointer loop and
        # silently click somewhere else.
        if self.capabilities.supports_absolute_pointer and not has_absolute_pointer(self._control):
            return ActionResult(False, "tap_abs", "absolute_unavailable",
                                "capabilities claim an absolute pointer report but the"
                                " control has no mouse_move_abs/mouse_click_abs")
```

**不要**在这一批里加"要求预检但没有检测器就拒绝"——那会破坏 `test_hid_sink.py` 里
`controller=FakeController`（无 `detector`）的既有用例，而且它属于批次 4 的 SafetyNet 职责。

- [ ] **Step 4: 跑定向测试**

Run: `uv run pytest tests/unit/devices tests/unit/control tests/unit/reflex tests/unit/drivers -q`
Expected: 全绿（`test_a_sink_without_absolute_reports_keeps_the_pointer_loop` 仍走指针环）。

- [ ] **Step 5: 全量回归**

Run: `uv run pytest tests/unit -q`
Expected: **14 failed / 3535+ passed**（14 项为既有基线；新增用例计入 passed）。

- [ ] **Step 6: 提交**

```bash
git add artemis/devices/ios_uvc.py artemis/control/hid.py \
        tests/unit/devices/test_ios_uvc_session.py tests/unit/control/test_hid_sink.py
git commit -m "feat(ios): route the click path by the absolute-pointer capability"
```

---

### Task 4: 文档同步 + 验收

**Files:**
- Modify: `docs/artemis-target-architecture.md` §4.3（ControlSink 能力列表）、§5（能力矩阵）、
  §7（SafetyNet 规则）、§10（"现状 2026-10-21"注）、§13（iOS 能力块）
- Test: `tests/unit/test_ios_rig_playbook.py`（若加了 playbook 断言才需要）

**Interfaces:** 无代码接口变化。

- [ ] **Step 1: 更新 §13 能力块**

```text
CapabilitySet:
  ui_tree = False
  absolute_tap = False            # 没有 digitizer：不能"绝对坐标触摸"
  relative_pointer = True
  absolute_pointer = True         # 但有 HID 绝对报告（amove/aclick）：一步到点
  requires_pointer_precheck = False   # 由上面两行推导，不是手写
```

并加一句：`requires_pointer_precheck = relative_pointer and not absolute_pointer`，唯一推导在
`orchestration/selector.py::requires_precheck()`。

- [ ] **Step 1b: 更新 §4.3 的 ControlSink 能力列表**

```python
supports_absolute_tap: bool
supports_relative_pointer: bool
supports_absolute_pointer: bool      # 新增：一步到点的 HID 绝对报告
requires_pointer_precheck: bool      # 由上面两行推导
supports_text_input: bool
```

- [ ] **Step 2: 更新 §7 规则**

在 §7 的 `pre_pointer` 之下补两行：

```text
  绝对指针（HID absolute report）→ 不需要 precheck；点击后必须验证效果
  预检与否由 capabilities 推导（requires_pointer_precheck），不由平台判断
```

并把 incident 列表标注状态：`low_confidence` **尚未实现（批次 2）**，其余已实现。

- [ ] **Step 3: 更新 §5 能力矩阵**

iOS 的「HID 控制」单元格改为：`相对指针 + 可选绝对 pointer collection（amove/aclick）`。

- [ ] **Step 4: 验收断言（手工，记录在进度文件）**

```bash
grep -c "supports_absolute_pointer" artemis/*.py artemis/*/*.py | grep -v ":0"
grep -n "requires_precheck(" artemis/orchestration/selector.py
uv run python - <<'PY'
from artemis.devices.capabilities import CapabilitySet
from artemis.orchestration.selector import DeviceProfile, select_composition
rig = CapabilitySet(supports_relative_pointer=True, supports_absolute_pointer=True)
print(select_composition(DeviceProfile("ios", "x", rig, (1080, 2400), uvc_available=True)))
PY
```

Expected: 打印的 `Composition` 里 `requires_pointer_precheck=False`，`reason` 提到绝对指针。

- [ ] **Step 5: 全量回归 + lint + 质量门**

```bash
uv run pytest tests/unit -q                    # 14 failed（基线）
uv run ruff check artemis/ tests/unit          # 全过
uv run python scripts/quality_ratchet.py --print-current   # 773 / 20（增量 0）
```

- [ ] **Step 6: 提交并打标签**

更新 §10 的"现状（2026-10-21）"注：iOS 已有 `session`、组合与能力转述，
`AndroidAdbDriver.session` 惰性构建同样成立；把日期改成 2026-11-06 并补一句"能力位含绝对指针"。

```bash
git add docs/artemis-target-architecture.md
git commit -m "docs(architecture): describe the absolute-pointer capability"
git tag <mmddhhmm>-ios-absolute-pointer-capability
```

---

## 批次 1 之外（本计划**不做**，避免范围漂移）

- 模块搬家（`reflex/capture|control|mouse|vision` → `perception|control|safety`）= 批次 3/4。
- `low_confidence` incident 与"无树时按置信度决定不点击" = 批次 2。
- SafetyNet 层化、§9 遥测补齐 = 批次 4/5。
- `reflex_tool._pointer_control_for` 的判定语义（skills 是否需要 PointerControl）= 批次 3，等模块归位后一起改。

## 回滚

单个任务独立可回滚（每个任务一个 commit）。若 `supports_absolute_pointer` 的推导与真机不符（老固件仍报 True），只需把 `HidControlSink` 实例推导改成读控制层显式属性（`getattr(control, "supports_absolute_pointer", False)`），
Task 2/3 不受影响 —— 这也是把推导放在一层的原因。
