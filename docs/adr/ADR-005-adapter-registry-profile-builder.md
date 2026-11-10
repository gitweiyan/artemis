# ADR-005: Static Adapter Registry and Profile Builder

- **Status:** Proposed
- **Date:** 2026-11-10
- **Owners:** ARTEMIS architecture maintainers
- **Blocks:** Phase 2（Device Runtime / Adapter Registry / Profile Composer）；Phase 3/4 的 Composition Root
- **Related:** 契约 §5.2（Profile）、§6.1–§6.2（Adapter/Profile）、§10（依赖方向）、§11（目录归属）；[ADR-001](ADR-001-capability-model-evidence-fingerprint.md)（Accepted）、[ADR-003](ADR-003-device-identity-session-resource-ownership.md)（Accepted）
- **Audit evidence:** [`CURRENT_CALL_FLOWS.md`](../architecture/CURRENT_CALL_FLOWS.md) §3–§8、`artemis/orchestration/selector.py`、`artemis/drivers/factory.py`、`artemis/devices/app_session.py`、`artemis/devices/ios_uvc.py`

> 状态说明：Proposed。批准前只允许盘点、原型和测试设计；不得合入改变设备选择行为的实现。

---

## 1. Context and problem

### 1.1 现状：选择逻辑纯、但构建逻辑混在 Factory

**组合选择已经是一个纯函数，但放错层。**

- `DeviceProfile`（platform/device_id/capabilities/screen/uvc/camera/capture_mode）与 `Composition` 定义在 `artemis/orchestration/selector.py:40-96`；
- `select_composition()` 是纯函数，不读环境、不开设备（模块 docstring 明确说明）；
- 但它位于 L2 的 `orchestration/`，按契约 §11 应归 L4 Device Composition。

**Factory 同时承担五件事。**

`drivers/factory.py:52-300` 当前同时负责：

1. 平台分支（`_is_ios`、cloud、mock、Android）；
2. 读取/校验配置（`IosRuntimeConfig.from_env`、crop 测量、ADB host/port）；
3. 打开 UVC/串口/ADB 句柄；
4. 构建 Session（`build_ios_uvc_session`/惰性 `build_app_session`）；
5. 包装 Driver 并缓存（`get_driver`）。

同一套“选择 + 配置 + 硬件 + Session”也散落在：
`ios-smoke`（`interfaces/cli/commands/ios_smoke.py:90-160`）、`ios-calibrate`、`reflex`（`tools/reflex_tool.py`、`commands/reflex.py`）、`play_reflex`。它们各自重复构造，Phase 2 的 Profile 必须成为唯一构建入口。

**两个 “Profile” 同名。**

- L2 执行档位：`orchestration/profiles.py:33` `ProfilePlan`（flash/pro/skill）；
- L4 设备组合：`selector.DeviceProfile`（android/ios/…）。
计划 §Phase 2 已要求区分；当前命名会让 Agent 与设备代码互相误读。

**没有注册表，也没有失败原因模型。**

- 新平台只能继续加 `if platform` 分支（factory 已有 iOS/Android/cloud/mock 四路）；
- 构建失败消息是字符串拼接，没有 `missing_adapter`/`missing_capability`/`missing_config` 的结构化分类；
- 不支持的能力只能靠 `getattr`/`False` 猜测，无法在组合阶段拒绝。

### 1.2 为什么现在必须决策

Phase 2 要把 `create_driver` 拆成 Router + Builder；Phase 3 的 Session 边界和 Phase 4 的 iOS Adapter 抽取都依赖一个稳定的组合契约。若先拆文件再定注册/失败语义，会出现多套“Profile”定义。

---

## 2. Decision drivers

1. **单一构建入口**：所有平台/诊断/reflex 路径通过同一 Registry/Profile Builder 构建；不再各自 new 硬件。
2. **平台分支只在 L4**：Agent/Graph/Perception/Action 不 import 平台或 Registry。
3. **可诊断失败**：缺 Adapter/能力/配置/句柄必须结构化报告，禁止静默回退。
4. **静态可测**：第一版静态注册，无 entry points/插件市场；纯函数选择可用 Mock 测试。
5. **兼容**：`create_driver`/`get_driver`/CLI/MCP 行为不变；Android/iOS 回归样本可复现。
6. **命名无歧义**：执行档位与设备组合各有唯一术语。

---

## 3. Considered options

### Option A — 继续在 Factory 里加平台分支

- Benefits：零新抽象。
- Costs / risks：Factory 持续膨胀；每个诊断/reflex 路径复制构建；失败原因不可分类；Phase 3/4 无法复用。

### Option B — 动态 Entry Points / 插件市场

- Benefits：第三方可扩展。
- Costs / risks：版本/安全/依赖注入成本超出 Phase 2；当前没有第三方消费者（与 ADR-001 “第二个真实消费者才抽象”一致）。

### Option C — 静态 AdapterRegistry + ProfileDefinition + 纯选择函数（推荐）

- Benefits：注册显式、可测；平台分支集中；失败原因结构化；兼容现有 Factory。
- Costs / risks：需要一个新模块与迁移期双层入口；Profile 定义需要维护能力清单。

### Option D — 一个 ProfileLoader 读 YAML

- Benefits：配置驱动。
- Costs / risks：Profile 含 Builder/Adapter 组合逻辑，YAML 无法表达；会引入第二套配置真相。

**选择：Option C。**

---

## 4. Decision (proposed)

### D1 — 命名与归属

| 概念 | 新名称 | 位置 | 兼容 |
|---|---|---|---|
| 执行档位（flash/pro/skill） | `ExecutionProfile` | L2 `orchestration/profiles.py` | `ProfilePlan` 保留为别名 |
| 设备组合请求（选择前输入） | `CompositionRequest` | L4 `devices/composition.py` | `selector.DeviceProfile` 保留 re-export |
| 设备组合定义（已注册） | `DeviceProfile` | L4 `devices/profiles.py` | 旧 `DeviceProfile` 改名后 re-export，避免混用 |
| 组合结果 | `Composition` | L4 `devices/composition.py` | 原样 |

- `select_composition()` 从 `orchestration/selector.py` 迁到 `devices/composition.py`；旧路径 re-export，调用方逐步迁移。
- 本文中的 “Profile” 默认指 **DeviceProfile**；L2 文档必须写 `ExecutionProfile`。

### D2 — 静态 Registry

新增 `artemis/devices/registry.py`：

```python
@dataclass(frozen=True)
class AdapterFactory:
    adapter_id: str
    version: str
    manifest: CapabilityManifest
    build: Callable[[CompositionRequest], Any]  # returns adapter/session part


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    platform_family: PlatformFamily
    required_capabilities: tuple[CapabilityId, ...]
    optional_capabilities: tuple[CapabilityId, ...] = ()
    adapter_ids: tuple[str, ...] = ()
    build: Callable[[CompositionRequest, AdapterRegistry], ProfileBuild] = ...


@dataclass(frozen=True)
class ProfileBuild:
    session: DeviceSession | None
    adapters: Mapping[str, Any]
    leases: LeaseSet | None = None
    driver: Any | None = None  # compatibility façade only


class AdapterRegistry:
    def register_adapter(self, factory: AdapterFactory) -> None: ...
    def register_profile(self, profile: DeviceProfile) -> None: ...
    def adapter(self, adapter_id: str) -> AdapterFactory: ...
    def profile(self, profile_id: str) -> DeviceProfile: ...
    def profiles(self) -> tuple[DeviceProfile, ...]: ...


def builtin_registry() -> AdapterRegistry: ...
```

规则：

1. **静态注册**：`builtin_registry()` 在 Composition Root 显式调用；模块 import 不得打开硬件、不得读环境。
2. **唯一 ID**：重复 `adapter_id`/`profile_id` 注册抛错；ID 是稳定字符串，不允许按类名动态生成。
3. **无平台条件在 L2/L3**：平台只出现在 Profile Definition/Builder 与 L4 Registry。
4. **Profile 只组合**：Profile 不得包含 Prompt、Planner、Checker 或 Agent 逻辑；只声明必需/可选能力、Adapter 组合、配置需求和 Builder。
5. **版本**：Adapter 与 Profile 都带 `version`；构建结果记录版本，便于故障定位。

### D3 — 纯选择函数

```python
def select_device_profile(
    request: CompositionRequest,
    registry: AdapterRegistry,
) -> ProfileSelection: ...
```

- 若 `request.profile_id` 指定：必须存在且平台/能力匹配，否则 `ProfileSelectionError`。
- 否则按 `platform_family` + 必需能力筛选；评分顺序：显式 profile → 能力覆盖数 → 稳定 profile_id 字典序（确定性）。
- 选择结果包含 `profile_id`、`reason`、`missing_capabilities`、`adapter_ids`。
- 选择函数不读环境、不开句柄、不构建 Adapter；纯函数可用 Mock 测试。
- 无可用 Profile → `ProfileSelectionError`，原因区分：`missing_profile` / `missing_capability` / `platform_mismatch` / `adapter_unavailable`。

### D4 — Profile Builder 与失败语义

- `DeviceProfile.build(request, registry) -> ProfileBuild`；Builder 负责：
  1. 校验配置（UVC device/crop、串口、屏幕）；
  2. 从 Registry 取 Adapter；
  3. 获取 Resource Lease（ADR-011）；
  4. 组装 DeviceSession/兼容 Driver；
  5. 失败时释放已取得资源。
- 失败必须抛 `ProfileBuildError(reason, profile_id, missing=..., adapter_id=..., cause=...)`；**禁止**静默回退到 ADB/其他设备。
- 构建结果可以暴露兼容 Driver，但新代码只依赖 Session/Ports。

### D5 — Factory 变成 Router

- `drivers/factory.create_driver(ctx)` 保留签名，内部改为：
  1. 由 `ArtemisContext`/配置构造 `CompositionRequest`；
  2. `registry = builtin_registry()`；
  3. `select_device_profile` + `build`；
  4. 返回 `ProfileBuild.driver`（兼容 Driver）。
- `get_driver` 缓存行为不变。
- 平台分支、配置读取、crop 测量、句柄打开从 Factory 移到对应 Profile Builder/Adapter（Phase 4 完成 iOS 的 I/O 下沉）。
- 诊断/reflex 路径（`ios-smoke`/`ios-calibrate`/`reflex`）在 Phase 2 后通过同一 Builder 构建；迁移期保留直接构造作为兼容路径。

### D6 — 配置、身份与句柄分离

- `DeviceDescriptor`（ADR-003）只放身份/端点/显示/指纹，不含句柄。
- `CompositionRequest` 携带选择所需能力与 **非敏感** 选择参数；设备配置（UVC 名/crop、串口、screen、capture mode）由 Profile 的 Config Adapter 读取（环境/配置来源），不进入 Descriptor 日志。
- 句柄只存在于 Adapter/Session；Profile/Registry 不持有句柄。

### D7 — Mock Profile 与契约测试

- 内置 `mock.screen`：录制的 Frame Source + Mock Control，不打开硬件；用于 Profile Contract Test：
  - 相同 Descriptor + 能力 → 相同 Profile 选择；
  - 替换 Adapter 不修改 Operator/Planner/Checker；
  - 构建失败报告缺失项；
  - Session 关闭顺序（control→perception→lease）。
- Android/iOS 各有一个 Regression Profile，选择与构建行为与当前 Factory 等价。

### D8 — 非目标

- 不做动态插件/Entry Points/热加载。
- 不做跨设备并发调度/资源池（属于 Device Runtime）。
- 不把 `third_party/mobile_use` 的 Agent/Config 纳入 Registry。
- 不在本 ADR 删除 `BaseDeviceDriver`（ADR-006）。

---

## 5. Consequences

### Positive

- 新平台只注册 Adapter + Profile；不再改 Factory/Agent。
- 构建失败可解释；诊断/reflex 与任务共用同一路径。
- 选择纯函数可测；Profile 能力清单可审查。

### Negative / trade-offs

- 迁移期 Factory 与 Registry 双路径，必须用回归测试锁住等价性。
- Profile/Adapter 版本元数据需要维护。
- 静态注册意味着新平台需要代码改动（这是有意选择）。

### Follow-up work

- ADR-006：Driver/Controller 退役与 Ports。
- Phase 2：Registry/Builder + Mock Profile。
- Phase 4：iOS Builder 接管 UVC/serial/crop 配置与句柄。

---

## 6. Contract and dependency impact

- **Layers**：L4 新增 Registry/Profiles；L2 只调用 Application Service；L3 只消费 Ports。
- **Ports / DTOs**：`CompositionRequest`、`DeviceProfile`、`ProfileSelection`、`ProfileBuild`、`ProfileBuildError`、`AdapterFactory`；`DeviceDescriptor` 复用 ADR-003。
- **Capability**：Profile 的 required/optional 使用 ADR-001 的规范 ID；缺少时返回 `capability_unavailable`。
- **Import direction**：`devices/registry.py`/`profiles.py` 不得 import `agents/graph/mcp/reflex`；`drivers/factory` 只做 Router；L2/L3 不 import registry。
- **Compatibility**：`create_driver`/`get_driver`、CLI 参数、MCP schema 不变；`selector` 路径 re-export。

---

## 7. Migration plan

| 步骤 | 内容 | 行为变化 |
|---|---|---|
| Phase 2a | Registry/Profile DTO + `builtin_registry` + Mock Profile；纯选择测试 | 无 |
| Phase 2b | `select_composition` 迁到 `devices/composition.py`，旧路径 re-export | 无 |
| Phase 2c | Factory 改为 Router；Android/iOS/Mock 用现有构建逻辑包装成 Profile | 无（回归锁定） |
| Phase 2d | 诊断/reflex 改用 Builder；直接构造保留兼容开关 | 无 |
| Phase 4 | iOS Builder 接管配置/句柄；Factory 不再读 UVC/串口 | 行为不变，边界更清晰 |

**Non-goals**：动态插件、重命名公开 API、删除旧选择器。

---

## 8. Verification and evidence

### Unit / Contract tests

- `tests/unit/devices/test_registry.py`（新增）：
  - 重复 Adapter/Profile ID 抛错；未知 ID 抛错；
  - 显式 profile 优先；能力不足/平台不匹配/无 Adapter 的失败分类；
  - 选择确定性（同输入同结果）；不读环境、不开句柄。
- `tests/unit/devices/test_profiles.py`（新增）：
  - Mock Profile 构建 Session；关闭顺序；缺失配置报 `missing_config`；
  - Android/iOS Profile 与当前 Factory 等价（Mock 掉句柄）。
- `tests/unit/orchestration/test_selector.py`：旧路径 re-export 后全绿。
- `tests/unit/drivers/test_factory_*`：Factory Router 行为不变。
- Architecture test：L2/L3 不 import `devices/registry`、`drivers`。

### Integration / hardware

- Android/iOS 真机 Smoke 与一条最小任务：选择/构建/关闭与当前一致。
- Mock Profile 作为“新增平台不改 Agent”的证明。

### Observability

- 日志/Trace：`profile_id`、`adapter_id`、`version`、选择原因、构建失败原因。

---

## 9. Rollback

- Registry/Profile additive；回滚 = Factory 恢复直接分支，保留旧 `selector` 路径。
- 开关 `ARTEMIS_STATIC_PROFILES=0`（Phase 2 引入）走旧构建路径。
- 不删除旧构建器/CLI 行为，直到 Phase 9-A 验收。

---

## 10. Security and safety

- 配置（端口/UVC 名称）不进入 Descriptor/Trace 明文；敏感路径按现有配置保护。
- 构建失败必须拒绝，不得回退到“另一个看起来可用”的设备。
- Profile 不能绕过 Capability/Lease/Session 生命周期；Builder 必须先取 Lease 再开句柄（ADR-011）。

---

## 11. Resolved / open questions

已定：

- 静态注册，无动态插件/Entry Points。
- Profile 选择是纯函数；构建在 L4 Builder。
- 执行档位改名 `ExecutionProfile`，设备组合沿用 `DeviceProfile`（旧 `selector.DeviceProfile` re-export）。
- 失败必须结构化，不回退。

待定（不阻塞 Phase 2a）：

1. Registry 是模块级单例还是每次 Composition Root 新建（建议新建 + `builtin_registry()`）。
2. Adapter 物理目录：`devices/adapters/` vs 现有 `perception/control/reflex`（建议先注册现有实现，Phase 4 再迁移 I/O）。
3. Profile Definition 的能力清单是否由代码生成（建议显式字面量 + CapabilityManifest 校验）。

---

## 12. Decision log

| Date | Status | Change | Rationale |
|---|---|---|---|
| 2026-11-10 | Proposed | Initial proposal | Factory 混合选择/配置/硬件；两个 Profile 同名；无 Registry/结构化失败；Phase 2 需要单一构建入口 |
