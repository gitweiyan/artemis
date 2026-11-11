# ARTEMIS Architecture Decision Records

本目录是架构决策的唯一登记入口。规范性架构要求见 `docs/architecture/ARCHITECTURE_CONTRACT.md`；迁移顺序见 `docs/architecture/REFACTOR_EXECUTION_PLAN.md`。

## 状态语义

- **Proposed**：提案待审；只能做调研、Mock、原型和测试设计，不能合入改变公共契约的实现。
- **Accepted**：已批准；对应 Phase 可按 ADR 实施。
- **Rejected**：明确不采用；记录理由，避免以后重复讨论。
- **Superseded**：被后续 ADR 取代；保留历史，不回填新决策。

只有 Accepted ADR 是已生效决策。Execution Plan 中的推荐方案不能替代 ADR 的批准。

## 新建与变更规则

1. 新建文件使用 `ADR-NNN-short-title.md`，复制 [ADR_TEMPLATE.md](ADR_TEMPLATE.md)。
2. 一个 ADR 只解决一个相对完整的架构决策；复杂议题拆分并显式记录依赖。
3. Accepted ADR 不直接改写历史决策；改变方向时新建 ADR，并将旧 ADR 标记为 Superseded。
4. ADR 必须列出当前分支代码证据、受影响的 Port/DTO/Capability/Adapter、Phase 阻塞、测试和回滚。
5. 更新 ADR 状态时，同步更新执行计划 §8 的 Phase 阻塞表；不要在第三份文档复制完整架构规则。

## ADR → Phase 阻塞表

| ADR | 决策主题 | 阻塞的 PR / Phase | 当前状态 |
|---|---|---|---|
| ADR-001 | [Capability Model、Evidence、Fingerprint 与 Invalidation](ADR-001-capability-model-evidence-fingerprint.md) | Phase 1 PR-2；Phase 2；Phase 4.2 | **Accepted**（2026-11-09） |
| ADR-002 | [Coordinate Space、Transform 与坐标兼容迁移](ADR-002-coordinate-spaces-transform.md) | Phase 1 PR-4；Phase 3 坐标链路；Phase 7 | **Accepted**（2026-11-10） |
| ADR-003 | [Device/Target/Endpoint Identity、Session 生命周期与资源所有权](ADR-003-device-identity-session-resource-ownership.md) | Phase 1 PR-1/PR-3；Phase 2；Phase 3 | **Accepted**（2026-11-09） |
| ADR-004 | [Action Intent/Command/Receipt/Verification 状态语义](ADR-004-action-intent-command-receipt.md) | Phase 1 PR-4；Phase 3；Phase 5 | **Accepted**（2026-11-10） |
| ADR-005 | [Adapter Registry、Profile Builder、静态注册机制](ADR-005-adapter-registry-profile-builder.md) | Phase 2 | **Accepted**（2026-11-10） |
| ADR-006 | [BaseDeviceDriver / UnifiedMobileController 兼容期与退役条件](ADR-006-driver-controller-retirement.md) | Phase 3；Phase 9-B | **Accepted**（2026-11-10） |
| ADR-007 | [Reflex Runtime、SafetyNet 与高频闭环安全预算](ADR-007-reflex-runtime-safety-budget.md) | Phase 3；Phase 5 | **Accepted**（2026-11-10） |
| ADR-008 | [Data Engine / Telemetry / Runtime Trace 数据权威与 Capability Evidence Artifact 归属](ADR-008-data-engine-evidence-ownership.md) | Phase 1 PR-2；Phase 6 | **Accepted**（2026-11-09） |
| ADR-009 | [ESP32 Host-Firmware Protocol、版本协商、Capability Handshake、ACK 语义](ADR-009-firmware-protocol-handshake.md) | Phase 4.2 | **Accepted**（2026-11-10） |
| ADR-010 | [Platform Extension、语义动作映射、Prompt Guidance、App Lifecycle、Locator 插件](ADR-010-platform-extensions-semantic-actions.md) | Phase 4.3 | **Accepted**（2026-11-10） |
| ADR-011 | [UVC/Serial 等共享 Host Resource Lease、并发与回收策略](ADR-011-host-resource-lease.md) | Phase 1 PR-3；Phase 2 | **Accepted**（2026-11-09） |
| ADR-012 | [macOS Desktop Vertical Slice and P9-B Activation](ADR-012-macos-desktop-vertical-slice.md) | Phase 7 实现；P9-B | **Proposed**（2026-11-10） |

## 推荐决策顺序

1. ADR-003（身份、Session 和资源所有权）先于 Capability Evidence 与 Lease 实现。
2. ADR-001 与 ADR-008 一起评审，避免能力状态和证据存储各自为政。
3. ADR-002 与 ADR-004 在 Phase 1 PR-4 前完成。
4. ADR-005/006 在迁移 Factory 与 Android 主链前完成。
5. ADR-007 在统一安全入口前完成，但不得把慢速 Agent Safety 强加给 Reflex 高频循环。
6. ADR-009/010 在 Phase 4 对应固件/平台扩展改造前完成。
7. ADR-011 在 UVC/Serial 多进程并发接入前完成。

`Planned` 表示尚未创建记录；创建后先进入 Proposed，评审批准后才能改为 Accepted。
