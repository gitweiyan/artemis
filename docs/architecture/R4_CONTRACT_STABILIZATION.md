# R4 Device Runtime Contract Stabilization

- **Status:** Started 2026-11-11
- **Scope:** freeze the Ports and DTOs that Android, iOS and future macOS/TV Profiles
  share; do not add a new platform-specific branch to Agent Core.
- **Authority:** `ARCHITECTURE_CONTRACT.md` remains the specification; this file
  records the current contract-test evidence and remaining gaps.

## Frozen interfaces

| Contract | Owner | Evidence |
|---|---|---|
| `PerceptionSource` | `perception/base.py` | adapter contract suite; source tests |
| `ControlSink` / `ActionResult` | `control/base.py` | adapter contract suite; control tests |
| `DeviceSession` lifecycle | `devices/session.py` | session close/failure/lease tests |
| `CapabilitySet` / `CapabilityView` | `core/capabilities.py` | capability catalog/registry tests |
| `ScreenGeometry` / coordinate spaces | `devices/geometry.py`, `core/coordinates.py` | geometry/coordinate tests |
| `ActionState` / `ActionReceipt` | `core/actions.py` | action semantics tests |
| `Observation` service | `perception/observation.py` | observation/session tests |

## Public contract freeze

`tests/unit/contracts/test_public_contract_freeze.py` pins the public DTO fields and
closed enums: `ActionResult`, `ActionReceipt`, `ActionState`, `ActionEvidence`,
`DataIds`, `ArtifactRef`, `Frame`, `Target`, `TextBox`, `UIElement`, `ScreenData`,
`ObservationEvidence`, `CapabilitySet`, `CapabilityId`, plus the required
`PerceptionSource` / `ControlSink` methods. Adding or reordering a public field is
now a deliberate contract change with a test diff, not an incidental refactor.

## Adapter contract suite

`tests/unit/contracts/test_adapter_contracts.py` exercises actual adapter classes, not
only hand-written protocol stubs:

- `UvcSource`
- `AppAutomationSource`
- `CompositePerception`
- `CompositeControlSink`
- `AppControlSink`
- `HidControlSink` shape/capability contract

It checks:

1. `PerceptionSource` / `ControlSink` runtime protocol compliance.
2. A `CapabilitySet` (not an ad-hoc dict) on every adapter.
3. `snapshot()` returns the shared `Frame` DTO with geometry, source and health.
4. Composite sources report which source supplied the frame.
5. App automation exposes the tree after observation.
6. Composite sinks choose by capability and return `unsupported` rather than
   pretending an unavailable action succeeded.
7. `ActionResult.ok` remains an adapter acceptance signal, not UI/goal verification.

This suite deliberately uses fakes for device I/O; real-device coverage lives in
`R3_ACCEPTANCE_REPORT.md` and the smoke/Flash/Pro runs.

## Existing contract coverage

- `tests/unit/devices/test_session.py`: lifecycle, failure terminal state, cleanup,
  cancellation and lease release.
- `tests/unit/devices/test_geometry.py`, `tests/unit/core/test_coordinates.py`:
  coordinate spaces and transforms.
- `tests/unit/core/test_actions.py`: intent/command/receipt/verification state
  transitions.
- `tests/unit/core/test_capability_catalog.py`, `test_capability_registry.py`:
  declared/available/verified capability semantics.
- `tests/unit/control/test_control_contracts.py`,
  `tests/unit/perception/test_perception_contracts.py`: minimal protocol guards.
- `tests/unit/clients/test_accessibility_client.py` and
  `test_screen_client_factory.py`: helper/XML element-shape parity and
  helper/UIAutomator fallback screen-data parity.

## Not yet frozen

- A macOS Adapter Contract Test is blocked by ADR-012 (Proposed); Phase 7 remains
  design-only.
- TV/Remote semantics (D-pad, CEC, IR) are not in R4 until Phase 8.
- The Accessibility Helper and UIAutomator2 paths have unit parity coverage:
  helper JSON elements are normalized to the same `bounds`/`parsed_bounds`/
  `accessibilityText` shape as the XML parser, and the fallback client preserves
  the screen-data shape. A live helper-enabled Android Flash/Pro run also passed
  after the helper was enabled; see `R3_ACCEPTANCE_REPORT.md` for session IDs.
- Physical disconnect/input-release fault injection is deferred (see R3 report).

## Exit criteria

R4 is complete only when:

1. Android, iOS and a real non-mobile Profile run the same contract suite.
2. Capability evidence, Session/Lease, Geometry and Action Receipt semantics are
   frozen in tests.
3. Remaining platform knowledge is expressed by Profile/Adapter, not Agent branches.


## R3.6/R3.7 contract additions (2026-11-13)

- `ObservationPort` now has `screen_data() -> ScreenData` and
  `observe() -> (ObserveResult, bytes)`; Agent/Graph observe only through the
  L1-injected instance.
- `DeviceFaultKind` adds `capture_unhealthy`; `ObservationProcessingError` is
  explicitly non-fatal for Session lifecycle.
- `SessionCloseReport`/`CleanupState` define released-now/total, retained,
  failed participants and retryability.
- `IosProfileConfig.uvc_port_hint` and `UsbInventoryStatus` are the UVC config
  and inventory boundary.
- Current test baseline and real-device evidence are linked from
  `CURRENT_ARCHITECTURE.md`; fixture-only adapter tests do not claim hardware
  verification.

## Final R4 freeze — error responsibility and retryability

| Error | Owner / boundary | Retryable | Session effect |
|---|---|---|---|
| `DeviceFault` / `DeviceDisconnectedError` | L4 adapter/session I/O | yes (new attempt/session after recovery) | `FAILED` |
| `DeviceFaultKind.TRANSPORT_FAILED` | L4 transport/firmware | yes | `FAILED` |
| `DeviceFaultKind.CAPTURE_UNHEALTHY` | L4 capture health policy | yes after bounded unhealthy threshold | `FAILED` |
| `ObservationProcessingError` | L3/L4 OCR/XML/decode/model processing | yes at observation layer | not automatically `FAILED` |
| `SessionClosedError` | L4 lifecycle | no | closed session |
| `ActionReceiptContractError` | L3 action contract | no | no device receipt |

`DeviceSession.record_fault()` owns the transition to `FAILED`; adapters do not
mutate Session state directly. A recovered device is a **new Session**, not a
reset of the failed one.

## Evidence correlation

```text
ObservationEvidence.frame_id
        |
        +-- ActionEvidence.ids.frame_id
        +-- ActionEvidence.post_verification
        +-- ActionReceipt.evidence_refs

ActionReceipt.state:
  dispatched -> adapter_acknowledged -> observed_effect
  observed_effect is effect evidence, not goal completion

ActionEvidence.goal_verified:
  false for an action record
RunOutcome.goal_verified:
  written only by the final Checker path
```

`tests/unit/contracts/test_error_and_evidence_contracts.py` pins these links.

## Compatibility exits

| Compatibility surface | Owner | Exit condition |
|---|---|---|
| `McpActionExecutor` legacy execute path | L1 | after all helper/device callers use `ActionService` + explicit ports; remove with test migration |
| `BaseDeviceDriver` / `UnifiedMobileController` | L4/L1 | after Android/iOS non-Agent callers use Session/Ports; P9-B blocked on real non-mobile Profile |
| `get_screen_data()` helper | L3/L1 | only external/legacy callers; Agent/Graph already fail if `ctx.observation_port` is absent |
| `DeviceSession.pointer` | L4 compatibility | after iOS pointer adapter callers use the Session adapter state |
| `devices.capabilities` / `actions.session` shims | L3/L2 | after no imports remain; allowlist stays empty |

## Unified Adapter Contract Test

`tests/unit/contracts/test_adapter_contracts.py` now includes a platform-neutral
`DeviceSession` composition test in addition to UVC, App Automation, Composite
Perception/Control, and HID/App sink checks. A new platform must supply
source/sink fixtures and Profile wiring; it must not add Agent platform branches.

## Completion decision

R4 contract surfaces are frozen for the current mobile runtime. macOS and TV
remain explicitly **not started**; their future Profiles must pass this suite
without changing Planner, Operator, Checker, or Flash/Pro Graph.
