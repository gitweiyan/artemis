# R3 Runtime Convergence — Acceptance Report

- **Date:** 2026-11-11
- **Branch:** `integration/10101205-all-fixes`
- **Code under test:** `998a0c4` (docs commits after it do not change runtime code)
- **Host:** macOS 26.4 (Darwin arm64)
- **Rigs:** iPhone via UVC + ESP32-S3 HID (`/dev/cu.usbmodem5B140947461`)
- **Android:** not connected during this run (`adb devices` was empty)

## Result matrix

| Scenario | iOS | Android | Evidence |
|---|---|---|---|
| Session create / close | ✅ | ⏸ not run | smoke + Flash/Pro logs |
| Screenshot / observation | ✅ | ⏸ not run | smoke `snapshot`; Flash/Pro traces |
| Single action | ✅ | ⏸ not run | smoke `tap`; Flash action evidence |
| Flash profile | ✅ | ⏸ not run | `fc2976c3-72e5-46ca-9f46-a9d9ba193b9f` |
| Pro profile + Checker | ✅ | ⏸ not run | `d1744245-f6e6-47f3-afad-e46636f80e0f` |
| Task cancellation | ✅ | ⏸ not run | `19d39e17-f149-40e4-b6e3-f57023f993a0` |
| Lease release | ✅ | ⏸ not run | all three runs released UVC + serial |
| ActionEvidence | ✅ | ⏸ not run | `trace actions` |
| Controller bypass | ◐ no fallback observed | ⏸ not run | logs contain no `UnifiedMobileController` construction |
| Device disconnect | ⏸ not performed | ⏸ not run | no safe disconnect fixture |

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

### 5. Action evidence

`uv run artemis trace actions --session <id> --json` returned action records with:

```text
device_id=ios-uvc
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

1. **Android is not verified** in this run: no device was attached. The same
   Session/Flash/Pro/cancel matrix must be repeated on `EUWSOBXCZLPFNJF6` or the
   current Android target.
2. **Device disconnect** was not exercised; cancellation and normal close are
   covered, but unplug/replug and adapter-failure recovery still need a dedicated
   fault-injection run.
3. **Controller bypass** is supported by log absence and unit/architecture tests,
   not by a deliberate controller-failure experiment.
4. `quality_ratchet` is green against an explicit debt checkpoint; it does not mean
   the 774 broad handlers were removed.

## Exit decision

R3.1–R3.3 runtime code paths are exercised on iOS. R3.4 remains **partial** until
Android and disconnect scenarios are run and the same evidence is recorded.
