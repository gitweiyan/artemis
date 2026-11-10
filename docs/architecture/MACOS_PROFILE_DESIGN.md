# macOS Desktop Profile — Design (Phase 7)

- **Status:** Design-only / Backlog. No production macOS target adapter is implemented by this document.
- **Scope:** define the `macos.desktop` Profile shape, Ports, coordinate rules, permissions, capability evidence, and Mock contract fixtures.
- **Prerequisites:** Android/iOS share the core Ports; Capability Evidence, Session/Lease, Geometry and Action Receipt contracts are accepted.
- **Decision gate:** before a real macOS vertical slice, write and accept a separate ADR covering the native capture/accessibility/input stack and the first real Mac acceptance task.

> **Host ≠ Target:** `artemis/platform/darwin.py` is the ARTEMIS **Host OS PAL** (paths, temp dirs, process tree, Homebrew). It is not evidence that ARTEMIS can control a Mac. The controlled Mac is a separate Target Device with its own Observation/Actuation adapters.

---

## 1. Target identity and Profile

| Field | Value |
|---|---|
| Platform family | `macos` |
| Profile ID (proposed) | `macos.desktop` |
| Target | a user session on a Mac (physical or VM), not the host process |
| Observation | ScreenCaptureKit/window capture + Accessibility (AX) tree + window metadata |
| Actuation | native keyboard/mouse events (global or window-targeted), AX actions where safe |
| App lifecycle | NSWorkspace/Apple Events, with Observation verification |
| Navigation | native app semantics/keyboard shortcuts; no platform branch in Agent |
| Text input | Unicode/CGEvent with per-app policy; verify focus and composition |
| Grounding | AX target + OCR/Locator fallback; `grounding.locate` remains optional |

The profile is composed from generic adapters and a macOS `PlatformExtension`; the Agent/Operator/Checker must not import macOS-specific modules.

### Capability manifest (declared, not verified)

Required for the first slice:

- `frame.capture` — display/window frame
- `ui_tree.read` — Accessibility tree
- `window.inspect` — display/window/focus metadata
- `window.focus` — bring the target app/window forward
- `pointer.absolute` — click at a display/window point
- `keyboard.key` — key/shortcut
- `text.input` — Unicode text

Optional:

- `ocr.read` — OCR fallback when AX is unavailable/ambiguous
- `grounding.locate` — registered Locator plugin
- `log.read` / `device.health` — diagnostics

Every capability remains `declared`/`available`/`unknown` until a real Mac contract test or smoke writes evidence. Screen Recording or Accessibility permission failure is `capability_unavailable`, never a fake success.

---

## 2. Ports and adapter boundaries

```text
macos.desktop Profile (L4)
  ObservationPort
    ├─ ScreenCaptureKitObservation  (frame, display/window metadata)
    └─ AccessibilityObservation     (AX tree, focused element/window)
  ControlSink
    ├─ MacOSPointerSink             (CGEvent absolute move/click)
    ├─ MacOSKeyboardSink            (CGEvent keys/Unicode text)
    └─ MacOSAXActionSink            (AXPress/AXSetValue only where semantics are safe)
  WindowPort                        (enumerate/activate/move/resize)
  PlatformExtension
    ├─ AppLifecycle (NSWorkspace/Apple Events)
    ├─ NavigationPolicy (app shortcuts / menu items / BACK semantics)
    ├─ TextInputPolicy (compose/IME/Unicode policy)
    ├─ TargetAdjuster (AX box/window crop)
    ├─ TargetLocator (AX→OCR→registered Locator)
    ├─ PromptGuidance (macOS keyboard/AX rules)
    └─ PermissionChecks (TCC matrix)
```

Rules:

- L1/L2/L3 never import `artemis.drivers.macos` or PyObjC directly.
- `ScreenCaptureKit`/AX/CGEvent are wrapped behind Ports; no window IDs, CGEvent objects or raw display handles cross into Agent state.
- Window IDs are ephemeral; resolve them through `WindowPort` and bind them to a `frame_id`/`transform_id` when an action is planned.
- Focus changes and app activation must be observed, not assumed from a successful API call.

---

## 3. Coordinate spaces and transforms

The first slice must distinguish at least:

| Space | Unit/origin | Example producer |
|---|---|---|
| `HOST_LOGICAL` | global display points, multi-display origin | AX, CGEvent, window frames |
| `DISPLAY_POINT` | points local to one display | display metadata |
| `DISPLAY_PIXEL` | backing pixels (Retina scale 1/2/…) | ScreenCaptureKit |
| `WINDOW_POINT` | points relative to the window content/titlebar | AX/window API |
| `WINDOW_PIXEL` | pixels in a window crop | screenshot |
| `AGENT_POINT` | normalized 0..1000 | model target |

### Transform requirements

1. Every frame carries `frame_id`, capture display/window identity, display origin, backing scale, rotation, and a content hash `transform_id` (ADR-002). Never pass naked points between spaces.
2. Display coordinates may have negative origins in multi-display layouts. Formula for a display-local point:
   `global_points = display.origin_points + local_points`.
3. Screenshot pixels:
   - whole display: `display_px = round(local_points * backing_scale)` (after rotation);
   - window: first map window points to display points, then scale; titlebar/content offset is explicit.
4. Rotation must be applied by a named transform (0/90/180/270); a rotated display's logical size and capture size must be recorded separately. Do not infer rotation from image dimensions.
5. Retina/fractional scale changes invalidate the transform. If the current scale/origin/rotation differs from the frame's transform, the action is rejected with `coordinate_space_mismatch`/`stale_frame`, never clamped.
6. Window move/resize between planning and acting invalidates the target; re-observe or report `target_shifted`.
7. Multi-display: never assume a single origin `(0,0)` or that the primary display is leftmost. The target display is part of the action's evidence.
8. Clamp only inside a known display/window after transform; out-of-bounds is a rejection, not a nearest-point fallback.

Mock values in `tests/fixtures/macos/displays.json` cover primary Retina, secondary display, negative origin, and rotated display.

---

## 4. Permissions / TCC matrix

macOS permissions are part of capability state and must produce actionable remediation.

| Permission | Why | Required for | Failure state |
|---|---|---|---|
| Screen Recording | ScreenCaptureKit/display capture | `frame.capture`, `ocr.read` | `capability_unavailable` |
| Accessibility | AX tree, AX actions, window metadata | `ui_tree.read`, `window.inspect`, `window.focus` | `capability_unavailable` |
| Input Monitoring | listen/observe global input (if used) | diagnostics/observe input status | `capability_unavailable` |
| Apple Events/Automation | launching/controlling apps via Apple Events | `app.launch` if enabled | `action_rejected`/`capability_unavailable` |
| Screen Recording (window-only) | window capture may still need screen permission | `frame.capture` | `capability_unavailable` |

`PermissionChecks` returns `available`/`unavailable`/`unknown` with a remediation string and the macOS version. It must not return `available` from “the API exists”; only a successful probe does. TCC state is `unknown` until probed. Screen permission changes require restarting the capture process; the profile must surface that.

### Permission probe order

1. `PermissionChecks.check()` for Screen Recording, Accessibility, Input Monitoring, Automation.
2. Start observation only if required capture permission is available.
3. Start AX/window port only if Accessibility is available.
4. If a permission is unavailable, disable dependent capabilities and return a remediation; do not silently degrade to a different semantics.

---

## 5. Lifecycle, focus, and safety

- Session open: resolve display/window descriptor, check permissions, create capture handles, verify a frame; close in reverse order (input release → capture → AX handles).
- Window/app activation is an action: dispatch → observe foreground/window/focus → verify. A successful `NSRunningApplication.activate` is not UI proof.
- Pressed keys/buttons must be released on cancel/error/session close; use the shared input release contract.
- Non-idempotent actions (click/key/power-like) are not auto-retried; a stale frame/window rejects the action.
- Window changes/display sleep/permission revocation mark the session `degraded`/`failed` with an Incident.
- Do not trust AX `AXPress` success as business success; verify the resulting frame/tree when the action is high-value.
- No full-screen/credential content in normal Trace payloads; use artifact references and the existing retention/redaction policy.

---

## 6. Capability evidence and fingerprint

Fingerprint fields for macOS evidence:

- platform family and macOS build/version;
- display configuration hash (display IDs, origins, scales, rotations);
- window/app identity where relevant;
- adapter IDs/versions and permission states;
- protocol/extension version.

A permission grant, OS update, display reconfiguration, or adapter version change invalidates old `verified` evidence. Only a real Mac smoke (capture + AX + click + text + focus verification) may write `verified`; Mock fixtures never do.

---

## 7. Mock Observation/Input contract fixtures

Design-only fixtures live under `tests/fixtures/macos/`:

- `displays.json` — primary Retina, secondary, negative origin, rotated display.
- `ax_tree.json` — windows, roles, labels, frames, focused element.
- `input_events.json` — expected absolute click/key/text event shape and release.
- `permissions.json` — permission matrix and remediation states.

Contract tests (`tests/unit/devices/test_macos_design_fixtures.py`) validate:

- every declared capability exists in the ADR-001 catalog;
- multi-display/Retina/rotation transforms are explicit and reversible;
- the permission matrix distinguishes required/optional and never marks unknown as available;
- a mock observation/input adapter records an action with a frame/window/transform reference, without implementing a production adapter.

No production `MacOSObservationAdapter`/`MacOSInputAdapter` is added in Phase 7.

---

## 8. Activation and non-goals

**Activation conditions**

- Phase 1–6 core contracts stable and tested;
- a real Mac test machine with Screen Recording + Accessibility permission;
- an accepted macOS ADR choosing the native stack (ScreenCaptureKit, AX, CGEvent) and the first acceptance task;
- Mock contract fixtures passing.

**Non-goals**

- no App Store/MDM integration;
- no production capture/input adapter in this phase;
- no promise that `artemis/platform/darwin.py` provides target control;
- no TV/Remote semantics (Phase 8);
- no bypass of TCC permissions or hidden background control.
