"""Application-service facade for the iOS rig CLI commands.

The command modules stay thin; platform/reflex construction is imported here so
the CLI does not reach into L4/Reflex implementation modules directly.
"""

from __future__ import annotations

from artemis.adapters.uvc import UvcCapture, UvcStream
from artemis.drivers.ios.calibration import calibrate_pointer
from artemis.drivers.ios.config import IosConfigError, IosRuntimeConfig
from artemis.drivers.ios.evidence import record_smoke_evidence
from artemis.drivers.ios.ios_uvc_hid_driver import IOSUvcHidDriver
from artemis.drivers.ios.smoke import run_smoke
from artemis.drivers.ios.uvc_identity import resolve_uvc_identity
from artemis.adapters.legacy_control import Esp32HidControl

__all__ = [
    "Esp32HidControl",
    "IOSUvcHidDriver",
    "IosConfigError",
    "IosRuntimeConfig",
    "UvcCapture",
    "UvcStream",
    "calibrate_pointer",
    "record_smoke_evidence",
    "resolve_uvc_identity",
    "run_smoke",
]
