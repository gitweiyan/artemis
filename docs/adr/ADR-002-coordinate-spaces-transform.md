# ADR-002: Coordinate Spaces, Transforms and Compatible Migration

- **Status:** Accepted
- **Date:** 2026-11-09
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 1 PR-4（Observation / Geometry / Action Contract）；Phase 3 坐标链路；Phase 7（macOS 多屏/窗口）
- **Related:** 契约 §5.5（Frame/Target）、§5.6（Coordinate Space/Geometry）、§5.7（Action）；[ADR-001](ADR-001-capability-model-evidence-fingerprint.md)（Accepted）、[ADR-004](ADR-004-action-intent-command-receipt.md)
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §3–§8、`artemis/devices/geometry.py`、`artemis/utils/coordinates.py`、`artemis/mcp/actuators/adb.py`、`artemis/control/hid.py`

> 状态说明：本 ADR 于 2026-11-10 经评审 **Accepted**；§4 是可执行契约。实现顺序见 §7。

---

## 1. Context and problem

### 1.1 现状：至少六种坐标表达并存（已核对源码）

| 表达 | 单位/范围 | 证据 |
|---|---|---|
| Agent Point | 0–1000 | `ScreenGeometry` docstring（`devices/geometry.py:20-24`）、`utils/coordinates.py:315-329`、`mcp/action_specs.py` 的工具描述 |
| Target Box | 0–1 | `perception/base.py:22-26`（`Target.box`）、`ScreenGeometry.normalized_box_to_screen`（`geometry.py:62-75`） |
| Frame Pixel | 像素 | `Frame.width/height`（`perception/base.py:12-19`）、`geometry.py:48-55` |
| Device Pixel | 像素 | `ScreenData.width/height`（`drivers/base.py:54-69`）、`AdbActuator._to_px`（`mcp/actuators/adb.py:126-133`） |
| Pointer Frame Pixel | 裁剪帧像素 | `HidControlSink._frame_point`（`control/hid.py:54-61`）→ `ScreenGeometry.screen_to_frame`（`geometry.py:96-107`） |
| Window/Display | 未建模 | 目前只有 Host PAL；桌面 Adapter 尚未实现 |

同一事实存在**四套换算实现**：

1. `ScreenGeometry`：`frame ↔ 0-1000 ↔ screen`，含 `content_rect`（crop/letterbox）；
2. `utils/coordinates.py:315-329`：`normalize_point` / `denormalize_point`（0-1000 ↔ 像素）；
3. `AdbActuator._to_px`：内联 `int(nx * width / 1000)`（`actuators/adb.py:126-133`）；
4. `HidControlSink`：先 `screen_to_normalized` 再 `normalized_to_frame`（`control/hid.py:54-61`）。

**记录格式也在混用。** `utils/coordinates.py:331-338` 明确写着 “Pro records physical pixels; Flash records normalized 0–1000 coordinates”；`normalize_action_dict` 靠 `coordinate_space` 标记防重入（`:340-378`），但 `normalize_step_actions` 仍用启发式判断（`target_bounds` 或任一坐标 `>1000`，`:447-460`）。这意味着同一条历史动作可能被二次解释。

**旋转、缩放、窗口偏移没有进入契约。**

- `ScreenData` 没有 rotation/display scale 字段（`drivers/base.py:54-69`）；`utils/video.py:332-351` 只在录像路径解析 `mRotation`，动作路径不使用；
- `ScreenGeometry` 只有 `frame_size`、`screen_size`、`content_rect`，没有 rotation、letterbox 之外的 scale、window origin（`geometry.py:15-45`）；
- `ctx.device.device_width/height` 既被观察层写回（`mcp/observation.py:87-88`），又被动作层当屏幕像素使用（`actuators/adb.py:126-133`），没有版本或来源。

**Frame/Target 没有可关联的版本。**

- `Frame` 只有 `captured_at`，没有 `frame_id`；`Target` 没有引用产生它的 Frame（`perception/base.py:12-26`）。
- 动作前只能用“新鲜度”间接判断；无法回答“这个 box 是在哪一帧、哪一版裁剪下算出来的”。

真实后果：iOS Playbook §4 记录过“归一化坐标永远看着对，只有点击整体偏移”的案例（crop 比例 2.4%；错误 crop 时 30%+）。这类错误在现有 DTO 中不可检测。

### 1.2 为什么现在必须决策

PR-4 要修改 Frame/Target/Geometry 和 Action 边界；ADR-004 定义 Action Command/Receipt 时需要坐标引用。如果先改 Action 再定坐标空间，Receipt 只能携带裸 `(x, y)`，无法审计和回放。

---

## 2. Decision drivers

1. **可审计**：每个点/框都能回答 space、frame、transform、单位。
2. **拒绝而不是猜测**：坐标空间/变换不明时拒绝动作，不做 `>1000` 式推断。
3. **兼容**：不破坏现有 0–1000 Agent 输入、0–1 Target Box、MCP schema、Prompt 与历史数据。
4. **跨平台**：iOS crop/pointer、Android 像素、macOS Retina/窗口、TV 画面都能用同一 Transform 图表达。
5. **可测试**：所有变换有 roundtrip/错误测试；不依赖真实设备。
6. **渐进**：PR-4 只加 DTO/Transform 与边界适配，不一次性重写 UI/Agent。

---

## 3. Considered options

### Option A — 全部统一到 0–1

- Benefits：只有一个归一化尺度。
- Costs / risks：Agent Prompt、MCP 工具 schema、历史 step、模型坐标习惯全部要改；PR-4 会变成大爆炸；0–1 与 0–1000 的整数取整差异会影响点击。

### Option B — 全部用像素

- Benefits：无归一化。
- Costs / risks：设备/分辨率/窗口变化即失效；Prompt 与模型不稳定；违反现有 Agent 契约。

### Option C — 保留 0–1000（Agent）与 0–1（Target Box），加显式 CoordinateSpace + 单一 Transform 图（推荐）

- Benefits：兼容；每个边界显式；桌面/多屏可扩展；可拒绝歧义。
- Costs / risks：两套归一化尺度继续存在，需要转换层与命名纪律；历史记录需要兼容读取。

### Option D — 继续用启发式（现状）

- Benefits：零改动。
- Costs / risks：`>1000`/`target_bounds` 推断必然误判（例如 0–1 box 或小屏像素）；无法发现 crop/rotation 错误。

**选择：Option C。**

---

## 4. Decision (accepted)

### D1 — 规范 CoordinateSpace

新增 `artemis/core/coordinates.py`（core，只依赖标准库）：

```python
class CoordinateSpace(StrEnum):
    AGENT_POINT = "agent_point"  # 0..1000 integers (prompt/tool schema)
    TARGET_BOX = "target_box"  # 0..1 floats (OCR/vision boxes)
    FRAME_PIXEL = "frame_pixel"  # captured image pixels
    DEVICE_PIXEL = "device_pixel"  # target screen pixels (rotation applied)
    POINTER_FRAME_PIXEL = "pointer_frame_pixel"  # cropped pointer detector frame
    WINDOW_POINT = "window_point"  # desktop window-local points
    DISPLAY_POINT = "display_point"  # desktop display/global points


@dataclass(frozen=True)
class PointRef:
    space: CoordinateSpace
    x: float
    y: float
    frame_id: str | None = None
    transform_id: str | None = None


@dataclass(frozen=True)
class BoxRef:
    space: CoordinateSpace
    left: float
    top: float
    right: float
    bottom: float
    frame_id: str | None = None
    transform_id: str | None = None
```

规则：

1. 新代码不得传递裸 `(x, y)` 跨层；现有 `Target.box`、`ScreenGeometry` 参数在 PR-4 保持兼容，但新 Port/Receipt 使用 `PointRef`/`BoxRef`。
2. **v1 保留两个归一化尺度**：Agent Point 0–1000（Prompt/工具/DTO 默认）与 Target Box 0–1（视觉模型输出）。两者的换算只允许在 Transform/兼容层发生；ADR 不得静默合并。
3. `frame_id` 为产生该点/框的 Observation 标识；`transform_id` 为所用变换的不可变版本。缺失时只能用于“同帧、同变换”的即时计算，不得跨动作/回放使用。
4. 单位：frame/device/pointer 均为整数像素；window/display 允许浮点（Retina/缩放）。取整规则集中在 Transform。

### D2 — 单一 CoordinateTransform

```python
TRANSFORM_SCHEMA = "coordinate-transform/v1"


@dataclass(frozen=True)
class TransformParams:
    source: CoordinateSpace
    target: CoordinateSpace
    frame_size: tuple[int, int]
    screen_size: tuple[int, int]
    content_rect: tuple[int, int, int, int] | None = None  # crop/letterbox in frame
    rotation_degrees: int = 0
    scale: tuple[float, float] = (1.0, 1.0)  # frame->screen
    window_origin: tuple[float, float] = (0.0, 0.0)  # desktop only
    display_scale: float = 1.0  # Retina only


def transform_id(params: TransformParams, *, schema: str = TRANSFORM_SCHEMA) -> str:
    """xform-<sha256(canonical params + schema)[:24]>; frame_id is NOT hashed."""
    ...


@dataclass(frozen=True)
class TransformRecord:
    transform_id: str
    params: TransformParams
    schema: str = TRANSFORM_SCHEMA
    #: Human label only (e.g. "ios-rig-449x974/2026-11-09"); never identity.
    revision: str | None = None


@dataclass(frozen=True)
class CoordinateTransform:
    record: TransformRecord
    frame_id: str

    def apply(self, point: PointRef) -> PointRef: ...
    def invert(self, point: PointRef) -> PointRef: ...
    @classmethod
    def frame_to_device(cls, frame_size, screen_size, content_rect=None, rotation=0): ...
    @classmethod
    def device_to_pointer_frame(cls, geometry): ...
```

**`transform_id` 规则（内容 hash，不是 revision）：**

1. 规范化 payload 固定包含：`schema`、`source`、`target`、`frame_size`、`screen_size`、`content_rect`、`rotation_degrees % 360`、`scale`、`window_origin`、`display_scale`。
2. 整数保持整数；浮点统一 `round(v, 6)`；键排序、紧凑 JSON、ASCII；`sha256` 取前 24 位 hex，前缀 `xform-`。
3. **`frame_id` 不参与 hash**：帧身份和数学变换身份是两件事；同一套 crop/rotation 在不同帧上得到同一个 `transform_id`，记录里分别引用两者。
4. `revision` 只做人读标签，不参与 hash；日志/UI 可以展示它，但比较、存储、证据匹配只用 `transform_id`。
5. 参数或 `schema` 任一变化 → 新 ID（crop 重测、旋转、屏幕/Retina 变化、算法/取整语义变化都会自动失效旧 ID）。
6. **存参数，不只存 ID**：`TransformRecord`（ID + 规范化 params + schema）随 Step/Trace 持久化；读取时重算 hash 校验；不匹配 → `coordinate_space_mismatch`。只存 hash 无法在代码语义变化后还原当时的数学。


- 变换顺序固定：`crop/letterbox → rotation → scale → window/display offset`（逆向反序）。
- `ScreenGeometry` 成为兼容 Facade：现有方法（`frame_to_normalized`、`normalized_to_frame`、`normalized_to_screen`、`normalized_box_to_screen`、`screen_to_frame` 等）委托给 Transform，签名不变。
- 新增 `ScreenGeometry.transform(frame_id=None) -> CoordinateTransform` 与 `from_transform`；`Frame` 增加 `frame_id`、`geometry_revision`（PR-4 additive）。
- 旋转不明确时（例如 `ScreenData` 没有 rotation）不得假设 0：**拒绝需要旋转的跨空间动作**并报 `coordinate_space_mismatch`（见 D4）。
- 桌面：`window_origin` + `display_scale` + 多屏 origin 只在 L4 Adapter 组合；L3 只消费 `WINDOW_POINT`/`DISPLAY_POINT`。

### D3 — 边界与记录

- **Agent 输入边界**：`mcp/action_executor` 把 `[0..1000]` 标为 `AGENT_POINT`；Target Box 标为 `TARGET_BOX`；`AdbActuator`/`HidControlSink` 通过 Transform 得到 device/pointer-frame 坐标。
- **记录**：所有新 Action/Step 记录必须写 `coordinate_space`（`agent_point` / `device_pixel` / `frame_pixel` / `pointer_frame_pixel`），并带 `frame_id`/`transform_id`（可为 null 的历史行除外）。
- **历史兼容**：`utils/coordinates.normalize_action_dict` 保留，作为**只读兼容层**；新记录不得依赖 `>1000`/`target_bounds` 启发式。PR-4 增加 `coordinate_space` 的写入测试；旧行仍可渲染。
- **禁止**：跨 Frame 复用 Target Box、跨 transform 复用像素、把 pointer-frame 像素直接当 device 像素、在 Agent/Driver 中手写 `x * width / 1000`。

### D4 — 失败与新鲜度

- `Target` 必须引用产生它的 Frame；动作前校验 `frame_id` 与当前 Observation 一致，且 `age <= freshness_budget`（已有 `observation_clock`）。过期 → `stale_frame`（契约 §7.2 已有）。
- 坐标空间/变换缺失、未知旋转、box 越界、无法反变换 → **拒绝动作**，incident `coordinate_space_mismatch`（新增；ADR 批准后写入契约 §7.2）。不得 clamp 后继续点击。
- 越界判断在目标空间进行；允许边界像素夹到屏幕边缘，但必须记录 `clamped=true` 指标。
- 同一 Action 的 Command/Receipt 必须引用同一个 `frame_id`/`transform_id`；不匹配则 `coordinate_space_mismatch`。

### D5 — 兼容与迁移边界

- `Frame`/`Target`/`ScreenGeometry` 旧字段保持；新增字段可选。
- `Target.box` 仍为 0–1；`ScreenGeometry.normalized_box_*` 仍接受 0–1。
- `ActionResult`/MCP 工具参数仍用 0–1000；PR-4 不改 schema。
- `utils/coordinates` 保留旧函数；新代码禁止新增调用（由 Architecture Test/评审约束）。
- 历史 Action 记录不重写；读取时按 `coordinate_space` 或 legacy 标记解释。

---

## 5. Consequences

### Positive

- crop/rotation/scale 错误会在动作前暴露，而不是“看着对、点偏了”。
- 桌面/多屏/窗口有明确空间，不需要把 `ctx.device` 当全局。
- Receipt/Replay 能回答“这个点属于哪一帧、哪一版变换”。

### Negative / trade-offs

- 两套归一化尺度继续存在，团队需要遵守命名；PR-4 增加转换层与测试。
- 旧记录仍需 legacy 解释；完全清理要等 Phase 9。
- 旋转来源（Android/UVC）在 Adapter 未提供前，相关跨空间动作会被拒绝——这是有意的安全降级。

### Follow-up work

- PR-4：DTO/Transform/Facade/Frame-Target 关联。
- Phase 3：执行链全部走 Transform，删除内联换算。
- Phase 7：窗口/多屏/Retina 变换。
- ADR-004：Command/Receipt 引用 `PointRef`/`BoxRef`。

---

## 6. Contract and dependency impact

- **Layers**：L3 定义/消费坐标 DTO；L4 Adapter 提供 frame/device/window 参数；L2 只使用 Agent Point。
- **Ports / DTOs**：新增 `CoordinateSpace`、`PointRef`、`BoxRef`、`CoordinateTransform`；`Frame`/`Target` additive 字段；`ScreenGeometry` 保持签名。
- **Capability**：新增 incident `coordinate_space_mismatch`（批准后写契约 §7.2）；`stale_frame` 已有。
- **Import direction**：`core/coordinates.py` 只依赖标准库；`devices/geometry.py` 可 import core；`drivers/control/perception` 使用 Transform；core 不 import 设备实现。
- **Compatibility**：MCP schema、Prompt、`Target.box` 0–1、Agent 0–1000 不动；历史 step 可读。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| PR-4a | `core/coordinates.py`（space/PointRef/BoxRef/Transform）+ roundtrip 测试；`ScreenGeometry` 委托 | 无 |
| PR-4b | `Frame.frame_id`/`geometry_revision`、`Target` frame 引用（additive），Observation 写入 | 无（旧调用仍可用） |
| PR-4c | ADB/HID 边界改用 Transform；删除新增内联换算；记录 `coordinate_space` | 行为不变，错误可拒绝 |
| PR-4d | 动作前 frame/transform 校验；`coordinate_space_mismatch`/`stale_frame` | 有（歧义时拒绝） |
| Phase 3 | Agent/Validator/Receipt 全链路引用 PointRef/BoxRef | 有 |
| Phase 7 | 窗口/多屏/Retina | 新平台 |

**Non-goals**：一次性统一 0–1、重写 Prompt/工具 schema、迁移历史 step、实现桌面 Adapter。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/core/test_coordinates.py`（新增）：
  - frame↔agent↔device roundtrip（crop/letterbox、2.4% 误差样例）；
  - rotation 0/90/180/270 roundtrip + 反向；
  - device↔pointer-frame（iOS crop）与越界拒绝；
  - `PointRef`/`BoxRef` space 不匹配抛错；
  - transform 逆变换不可用时拒绝；
  - window origin/display scale（桌面样例）；
  - `transform_id` 跨实例/跨进程稳定；crop/rotation/screen/scale/schema 任一变化即变化；`frame_id`/`revision` 变化不改变 ID；篡改 params 后重算校验失败。
- `tests/unit/devices/test_geometry.py`（扩展）：旧 `ScreenGeometry` API 数值不变（回归）。
- `tests/unit/mcp/test_*`：Agent Point 输入仍按 0–1000；记录带 space。
- `tests/unit/agents/validator`：stale frame / mismatch 拒绝，不产生点击。

### Integration / hardware

- iOS 真机：`ios-smoke` 的 `frame_fit` 2.4% 样例；人为设置错误 crop，动作前拒绝而非点偏。
- Android：旋转/分辨率变化时坐标一致性（若 rotation 来源未接，明确拒绝跨空间动作）。
- macOS（未来）：窗口 origin/Retina。

### Observability

- Action/Step 记录 `frame_id`、`transform_id`、`coordinate_space`、`clamped`；失败带 incident。

---

## 9. Rollback

- DTO/Transform additive；回滚 = 不接 `ScreenGeometry.transform`，保留旧方法实现。
- 开关 `ARTEMIS_TYPED_COORDINATES=0`（PR-4 引入）让边界继续走旧换算；记录仍带 space。
- 不修改历史数据/schema；删除新模块不影响旧行为。

---

## 10. Security and safety

- 歧义/过期坐标一律拒绝，不盲点；这是安全边界，不是可用性优化。
- 裁剪/缩放错误不允许 clamp 掩盖；必须显式记录并拒绝。
- 坐标记录不含屏幕内容；与 ADR-008 的敏感数据处理一致。

---

## 11. Resolved / open questions

已定：

- v1 保留 0–1000 Agent Point 与 0–1 Target Box；不做一次性统一。
- 单一 Transform 图；`ScreenGeometry` 仅作兼容 Facade。
- 无 frame/transform 引用时拒绝跨动作复用。
- 旋转不明时拒绝，不假设 0。
- **`transform_id` = 规范化参数 + schema 的内容 hash**；`frame_id` 不参与；`revision` 仅人类标签；`TransformRecord` 随记录持久化并可在读取时重算校验。

2026-11-10 Owner 确认：

| # | 问题 | 决定 | 落地点 |
|---|---|---|---|
| 1 | Android rotation 权威来源 | 由 Phase 3 Adapter 决定（`dumpsys`/`window_size`/scrcpy 对比后选一）；在此之前 rotation 未知时拒绝跨空间动作 | Phase 3 |
| 2 | 是否统一到 0–1 | Phase 9 前不统一；如未来统一，另提 ADR | 未来 ADR |

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-09 | Proposed | Initial proposal | 六种空间、四套换算、无 frame/transform 关联；PR-4/ADR-004 需要坐标引用 |
| 2026-11-09 | Proposed | `transform_id` 定为参数内容 hash；`frame_id` 排除、`revision` 仅标签、`TransformRecord` 持久化 | 跨进程无中心权威；参数变化自动失效；证据/回放可重算校验 |
| 2026-11-10 | Accepted | 评审通过；rotation 来源由 Phase 3 Adapter 定（未知则拒绝），Phase 9 前不统一 0–1 | 两项剩余细节由 Owner 确认；PR-4 可进入实现 |
