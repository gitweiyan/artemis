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
