# R3 Runtime Convergence — Acceptance Report

- **Date:** 2026-11-11
- **Branch:** `integration/10101205-all-fixes`
- **Code under test (historical R3.1–R3.4 matrix):** `998a0c4`; current implementation snapshot is in `CURRENT_ARCHITECTURE.md` (`e9914c8` code baseline). The matrix below is historical, not the current R3.6–R3.8 status.
- **Host:** macOS 26.4 (Darwin arm64)
- **Rigs:** iPhone via UVC + ESP32-S3 HID (`/dev/cu.usbmodem5B140947461`); Android `EUWSOBXCZLPFNJF6` (PEMM00, Android 13)
- **Android observation backend:** Accessibility Helper v1.3.1 (protocol 3) verified in `5eef9e4b...` / `19bce48e...`; UIAutomator2 verified earlier as the fallback

## Result matrix

| Scenario | iOS | Android | Evidence |
|---|---|---|---|
| Session create / close | ✅ | ✅ | iOS smoke/Flash/Pro; Android Flash/Pro logs |
| Screenshot / observation | ✅ | ✅ | iOS smoke; Android Helper (`UI hierarchy source: Artemis accessibility helper`) and UIAutomator2 fallback |
| Single action | ✅ | ✅ | iOS smoke tap; Android Flash HOME / Pro actions |
| Flash profile | ✅ | ✅ | `fc2976c3...`; `ad0ef70a...` |
| Pro profile + Checker | ✅ | ✅ | `d1744245...`; `4f06fa3c...` |
| Task cancellation | ✅ | ✅ | `19d39e17...`; `318bf58c...` |
| Lease release | ✅ | ✅ | iOS UVC/serial; Android helper forward lease |
| ActionEvidence | ✅ | ✅ | `trace actions` |
| Controller bypass | ◐ no fallback observed | ◐ Helper/UIAutomator2 paths observed | logs contain no `UnifiedMobileController` construction |
| Device disconnect / reconnect | ✅ reconnect after replug; ⏸ mid-task unplug | ✅ reconnect after replug; ⏸ mid-task unplug | iOS smoke; Android Flash `9858ede3...` |

## Evidence

### 1. iOS smoke

Command:

```bash
ARTEMIS_ESP32_PORT=/dev/cu.usbmodem5B140947461 \
ARTEMIS_IOS_LOCATE_URL=http://127.0.0.1:7863 \
uv run artemis ios-smoke --target 快捷指令
```

Result: all seven stages passed (`connect`, `snapshot`, `frame_fit`,
`pointer_motion`, `locate`, `pointer_precheck`, `tap`), 7 capability records.

### 2. Flash

Session `fc2976c3-72e5-46ca-9f46-a9d9ba193b9f` completed; both UVC and serial
leases were released. The archived trace is
`traces/fc2976c3-72e5-46ca-9f46-a9d9ba193b9f_PASS_2026-11-11T21-38-58`.

### 3. Pro + Checker

Session `d1744245-f6e6-47f3-afad-e46636f80e0f` completed with final Checker
verdicts. `run_outcome.json` records `task_status=completed` and
`goal_verified=true`; action evidence for the same session remains
`adapter_acknowledged`, demonstrating the action/goal separation.

### 4. Cancellation

The `滚动设置页面并读取选项` run was interrupted with SIGINT after ~12s:

```text
Task cancelled
Session ended: 19d39e17-f149-40e4-b6e3-f57023f993a0 with status: cancelled
Resource lease released: uvc:...
Resource lease released: serial:...
```

The archived trace is
`traces/19d39e17-f149-40e4-b6e3-f57023f993a0_FAIL_2026-11-11T21-41-09`.

### 5. Android

Flash session `ad0ef70a-c860-4ebb-b492-2db1fcab093f` completed; trace
`traces/web_1794404588_ad0ef70a_PASS_2026-11-11T21-43-09`; action evidence
`6ed7bf5...` is `observed_effect`.

Pro session `4f06fa3c-305f-4360-ba95-1e819d399728` completed; trace
`traces/web_1794404624_4f06fa3c_PASS_2026-11-11T21-43-45`;
`run_outcome.json` has `task_status=completed`, `goal_verified=true`, and the
helper forward lease was acquired/released. The accessibility helper was
unavailable, so observation used UIAutomator2 (`UI hierarchy source:
UIAutomator2`); this is a recorded warning, not a silent fallback.

After the Accessibility Helper was enabled, a forced-helper Flash run
`5eef9e4b-d649-4355-b622-3b11d36081ad` completed with trace
`traces/web_1794406583_5eef9e4b_PASS_2026-11-11T22-16-24`; logs record
`UI hierarchy source: Artemis accessibility helper v1.3.1`. The forced-helper Pro
run `19bce48e-27b2-4c0f-bcfe-cec64895024e` completed with `goal_verified=true`
and trace `traces/web_1794406613_19bce48e_PASS_2026-11-11T22-16-53`; the helper
tunnel was rebuilt once during the run, then served the hierarchy.

`artemis helper parity --serial EUWSOBXCZLPFNJF6 --rounds 3` passed on the stable
home screen (91% recall / 95% precision). A first attempt during a screen
transition reported a foreground-package mismatch (`systemui` vs `settings`);
that was a timing artifact of sequential backend dumps, not an element-shape
mismatch. Parity should be run on a stable screen.

Cancellation session `318bf58c-512f-4987-b4fb-2c7f9a0ea68c` ended `cancelled`
(12.3s) after an external cancel request; no action evidence was emitted because
no action had executed yet.

### 6. Pointer precheck receipt

iOS Flash session `895239e6-89f2-4a83-b4c7-4a3f67d910e8` exercised two clicks
against the same relative-pointer rig:

- first click: `pre_verification.pointer_precheck.state=failed`;
  incident `screen_changed`; the sink did not acknowledge alignment;
- second click: `pre_verification.pointer_precheck.state=verified`;
  `error_px=0.0`.

The task itself ended `failed` on the No-Op Guard, so this is evidence for the
receipt contract, not a successful task claim.

### 7. Replug / reconnect

After physically replugging both rigs (2026-11-11 21:49):

- iOS `ios-smoke --target 快捷指令` passed all seven stages again; UVC and
  serial leases were re-acquired and released.
- Android Flash `回到主屏` completed in session
  `9858ede3-ccf6-492e-8c18-a17e847cf344`; action evidence is
  `observed_effect`, and the trace is
  `traces/web_1794404992_9858ede3_PASS_2026-11-11T21-49-54`.

This verifies **reconnect after replug**, not a mid-task unplug. The in-flight
disconnect path still needs a deliberate fault-injection run.

### 8. Action evidence

`uv run artemis trace actions --session <id> --json` returned action records with:

```text
device_id=ios-uvc | EUWSOBXCZLPFNJF6
device_ack=adapter_acknowledged
action_state=adapter_acknowledged | observed_effect
goal_verified=false
```

Task-level `goal_verified=true` is stored in `run_outcome.json`, not in an action record.

## Gates

```text
tests/unit: 0 failed / 3835 passed / 9 skipped
ruff check: All checks passed
quality_ratchet: passed (774 / 0 / 18)
ruff format --check .: 16 files would be reformatted (pre-existing)
```

## Gaps

1. **Mid-task device disconnect** was not exercised on either platform; normal
   reconnect after replug is verified, but unplug during a running task and
   adapter-failure recovery still need a dedicated fault-injection run.
2. **Controller bypass** is supported by log absence and unit/architecture tests,
   not by a deliberate controller-failure experiment.
3. Android Helper and UIAutomator2 observation paths are both verified. A helper
   tunnel rebuild occurred once in the Pro run; repeated instability would merit
   a tunnel-recovery test.
4. `quality_ratchet` is green against an explicit debt checkpoint; it does not mean
   the 774 broad handlers were removed.

## Exit decision

R3.1–R3.3 runtime code paths are exercised on iOS and Android, including reconnect
after replug and both Android observation backends. R3.4 remains **partial** only
until a mid-task disconnect is verified.


---

## 2026-11-13 Update

The historical matrix above is retained as the R3.1–R3.4 evidence snapshot. Since
then the following were implemented and verified:

- strict L1 `ObservationPort` injection for Planner/Checker/Graph/Flash/Validator;
- no Agent/Graph `get_screen_data` compatibility call;
- no ActionService/McpActionPort/McpActionTransport/Flash Controller passthrough;
- real-dispatch ActionReceipt and helper/legacy no-ACK semantics;
- Session cleanup state/report/retry, external owner barrier, lease retention;
- DeviceFault classification (`device_disconnected`, `transport_error`,
  `capture_unhealthy`, processing errors);
- UVC inventory schema/tri-state, profile `ios.uvc_port_hint`, exact port hint;
- iOS UVC/ESP32 and Android USB/Helper fault injection.

Current automated baseline: `3926 passed, 5 skipped`; architecture `16 passed`.
Current real-device details: `R3_FAULT_INJECTION_REPORT.md`.

R3.8 remains **partial** only where the environment cannot provide the scenario
(TCP/IP ADB, multiple UVC cards, real unlink failure). It is not marked fully
passed.
