# R3.8 Fault Injection Evidence — 2026-11-13

Scope: operator-assisted real-device fault injection on the current branch. These
are **REAL_DEVICE** observations, not Mock results.

## Environment

```text
Branch: integration/10101205-all-fixes
iOS:    iPhone via UVC + ESP32-S3 HID (/dev/cu.usbmodem5B140947461)
Android: EUWSOBXCZLPFNJF6 (PEMM00)
```

## IOS-01 — UVC unplug during an active Session

Runner: `scripts/qa/r3_fault_injection/ios_uvc_disconnect.py`.

Initial attempt exposed a real gap: after unplugging UVC, the capture source
returned a black frame rather than raising `device_disconnected`; the Session stayed
`READY`. Fixed in `10b514c`: three consecutive unhealthy observations now mark the
Session `FAILED`.

Final result:

```text
FAULT_DETECTED state=failed
fault_source=perception
reason=session_failed: no healthy perception frame after 3 attempts
SESSION_CLOSED state=closed cleanup=complete
released=[uvc:.../capture, serial:.../command]
retained=[]
```

Recovery: after reconnecting UVC, a new `ios-smoke --target 设置` passed all seven
stages and released both leases.

## IOS-02 — ESP32 control link unplug

Runner: `scripts/qa/r3_fault_injection/ios_esp32_disconnect.py`.

```text
SESSION_READY frame=ddf1cb68245748f592de4bb3373cbc2e
FAULT_DETECTED state=failed
fault_source=action:press_key
reason=transport_error: press_key: transport_error
SESSION_CLOSED state=closed cleanup=complete
released=[uvc:.../capture, serial:.../command]
retained=[]
```

The test also verified that the action failure is mapped to `transport_error` and
the Session is failed; it does not silently keep using the old link.

## AND-01 — Android USB unplug

Runner: `scripts/qa/r3_fault_injection/android_usb_disconnect.py`.

Initial attempt exposed a second real gap: `AndroidAdbDriver` used its headless
1x1-PNG fallback when ADB disappeared, so the source looked healthy. Fixed in
`a88ece8`: the fallback is marked `last_capture_error` and `AppAutomationSource`
raises `DeviceDisconnectedError`.

Final result:

```text
SESSION_READY frame=772992020d29474fac60a63f3df1623b
FAULT_DETECTED state=failed
fault_source=app
reason=device_disconnected: Android screenshot capture failed
SESSION_CLOSED state=closed cleanup=complete
released=[]
retained=[]
```

Recovery: after reconnecting Android, a fresh Flash session
`9dfaa352-3c0c-4b31-b8b6-2f2dcf622784` completed (BACK, launched Settings, report).

## AND-03 — Helper tunnel removal

Runner: `scripts/qa/r3_fault_injection/android_helper_disconnect.py`.

Helper service was enabled manually first. The runner obtained a helper-first
observation, removed the helper's `adb forward`, and observed the next observation:

```text
HELPER_READY backend='helper' port=56516
FAULT_DETECTED removed_forward=56516 backend='uiautomator'
history=[
  helper,
  uiautomator (HelperRequestError: HTTP 502)
]
RESULT: PASS
```

This is the explicit Profile/backend fallback, not a Controller fallback. The helper
tunnel lease was released and UIAutomator2 was stopped during disconnect.

## AND-04 — Helper process force-stop

Runner: `scripts/qa/r3_fault_injection/android_helper_process_fault.py`.

The helper answered first, then the helper package was force-stopped. The next
observation degraded to UIAutomator2:

```text
before='helper' after='uiautomator'
history=[helper, uiautomator (HelperRequestError: HTTP 502)]
RESULT: PASS
```

## AND-02 — TCP/IP ADB

**NOT AVAILABLE**: the attached Android target is USB-only; no network ADB endpoint
was available. No USB test is being presented as a TCP test.

## In-task Pro run — UVC unplug

Command: a Pro task (`打开设置，进入通用，再打开关于本机，最后回到主屏幕`) was
started on the live iOS rig, then the operator unplugged UVC while it was running.

```text
Session: 3cacd953-e4f9-4315-b50f-a5bc4b71a21c
Device session failed: device_disconnected
Session ended: failed
steps: 0
UVC/serial leases released
```

Graph Perception failed closed on the failed Session; the Agent did not dispatch
another action against the dead Session. Recovery: after reconnecting UVC,
`ios-smoke --target 设置` passed all seven stages again.

## In-task Android Pro run — USB unplug

Pro session `28b423a1-4956-4f6e-8847-c40bbec397e7` was started, then the Android
USB cable was unplugged while it was running.

```text
status: failed
steps: 0
reason: Device EUWSOBXCZLPFNJF6 is not available (adb does not list it)
```

No action was dispatched against the old device after loss. Recovery: after
reconnecting, Flash session `e00216ec-61b5-4ed8-abd1-6328eb464952` completed
(`Pressed key 'HOME'`, report task status).

## Not yet injected

- Real multi-capture-card `port_hint` selection.
- Real `safe_unlink` failure / residual lock retry.

## Automated evidence

```text
uv run pytest tests/unit -q
  3917 passed, 5 skipped

uv run pytest tests/unit/architecture -q
  15 passed

uv run ruff check: pass
quality_ratchet: 773 / 0 / 18
```

The two real-device gaps found here were fixed before the final runs; the earlier
failed attempts are retained as diagnostic evidence and not counted as passes.


## Post-strict-observation verification (2026-11-13)

After Agent/Graph observation was made strictly port-injected, the rig was
reconnected and rechecked:

```text
iOS smoke --target 设置: PASS (7 capability records)
iOS Flash c4c3deb8-125d-4907-9921-839ae311d9ac: completed
Android Flash ab1ca8f5-24a1-417f-97e6-42863a67ad8c: completed
UVC/serial leases released after each iOS run
```

The iOS Flash run encountered no_effect clicks first and then used the supported
`manage_app` path; this was a hardware/click-path result, not a fallback to a
Controller. The task completed on a new Session.
