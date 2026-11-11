"""Canonical capability catalog, view and evidence DTOs (ADR-001, PR-2).

``CapabilitySet`` (``artemis.devices.capabilities``) remains the compatibility
view; this module is the target model:

- ``CapabilityId`` is a closed enum: callers never type capability strings;
- a ``CapabilityRecord`` carries state, source, preconditions and evidence ref;
- ``CapabilityView`` separates planning (declared) from execution (available);
- ``CapabilityEvidence`` is the immutable record stored by the evidence store.

Only the standard library is imported here (core contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from types import MappingProxyType
from typing import Iterable, Iterator, Literal, Mapping, Sequence

__all__ = [
    "CATALOG",
    "CapabilitySet",
    "CapabilityDeclaration",
    "CapabilityDomain",
    "CapabilityEvidence",
    "CapabilityId",
    "CapabilityManifest",
    "CapabilityRecord",
    "CapabilitySpec",
    "CapabilityState",
    "CapabilityView",
    "canonical_endpoint_ids",
    "capability_fingerprint",
    "fingerprint_fields",
    "spec",
]

_UNKNOWN = "unknown"


@dataclass(frozen=True)
class CapabilitySet:
    """Compatibility capability view shared by L3 sources/sinks and L4 drivers."""

    supports_ui_tree: bool = False
    supports_absolute_tap: bool = False
    supports_relative_pointer: bool = False
    requires_pointer_precheck: bool = False
    supports_ocr: bool = False
    supports_text_input: bool = False
    supports_shell: bool = False
    #: Launch/stop an app by name. Android resolves a package; iOS uses the
    #: Spotlight/keyboard launcher because it has no package manager to query.
    supports_app_launch: bool = True


class CapabilityDomain(StrEnum):
    OBSERVATION = "observation"
    ACTUATION = "actuation"
    TRANSPORT = "transport"
    DIAGNOSTICS = "diagnostics"


class CapabilityId(StrEnum):
    FRAME_CAPTURE = "frame.capture"
    UI_TREE_READ = "ui_tree.read"
    OCR_READ = "ocr.read"
    GROUNDING_LOCATE = "grounding.locate"
    WINDOW_INSPECT = "window.inspect"
    LOG_READ = "log.read"

    TOUCH_ABSOLUTE = "touch.absolute"
    POINTER_RELATIVE = "pointer.relative"
    POINTER_ABSOLUTE = "pointer.absolute"
    KEYBOARD_KEY = "keyboard.key"
    TEXT_INPUT = "text.input"
    REMOTE_DPAD = "remote.dpad"
    APP_LAUNCH = "app.launch"
    WINDOW_FOCUS = "window.focus"

    USB_HID = "usb.hid"
    SERIAL_COMMAND = "serial.command"
    ADB = "adb"
    BLE = "ble"
    NETWORK_RPC = "network.rpc"
    HDMI_CEC = "hdmi.cec"

    DEVICE_HEALTH = "device.health"
    INPUT_STATUS = "input.status"
    CAPTURE_HEALTH = "capture.health"
    FIRMWARE_INFO = "firmware.info"
    SHELL_EXEC = "shell.exec"


class CapabilityState(StrEnum):
    DECLARED = "declared"
    AVAILABLE = "available"
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


_ALL_CAPABILITIES = {member.value for member in CapabilityId}
_ALL_STATES = {member.value for member in CapabilityState}


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str
    domain: str
    description: str
    evidence_kind: Literal["contract_test", "smoke", "manual", "none"] = "contract_test"


#: Closed catalog: adding/removing an entry needs a contract update + ADR increment.
_CATALOG_ROWS = (
    (CapabilityId.FRAME_CAPTURE, CapabilityDomain.OBSERVATION, "capture one frame", "smoke"),
    (
        CapabilityId.UI_TREE_READ,
        CapabilityDomain.OBSERVATION,
        "read the UI hierarchy",
        "contract_test",
    ),
    (CapabilityId.OCR_READ, CapabilityDomain.OBSERVATION, "OCR text from a frame", "contract_test"),
    (
        CapabilityId.GROUNDING_LOCATE,
        CapabilityDomain.OBSERVATION,
        "locate a target through a grounding service",
        "contract_test",
    ),
    (
        CapabilityId.WINDOW_INSPECT,
        CapabilityDomain.OBSERVATION,
        "inspect a window",
        "contract_test",
    ),
    (CapabilityId.LOG_READ, CapabilityDomain.OBSERVATION, "read device logs", "smoke"),
    (CapabilityId.TOUCH_ABSOLUTE, CapabilityDomain.ACTUATION, "absolute touch", "smoke"),
    (CapabilityId.POINTER_RELATIVE, CapabilityDomain.ACTUATION, "relative pointer", "smoke"),
    (CapabilityId.POINTER_ABSOLUTE, CapabilityDomain.ACTUATION, "absolute pointer", "smoke"),
    (CapabilityId.KEYBOARD_KEY, CapabilityDomain.ACTUATION, "press a key", "smoke"),
    (CapabilityId.TEXT_INPUT, CapabilityDomain.ACTUATION, "type text", "smoke"),
    (CapabilityId.REMOTE_DPAD, CapabilityDomain.ACTUATION, "D-pad navigation", "manual"),
    (CapabilityId.APP_LAUNCH, CapabilityDomain.ACTUATION, "launch an app", "smoke"),
    (CapabilityId.WINDOW_FOCUS, CapabilityDomain.ACTUATION, "focus a window", "manual"),
    (CapabilityId.USB_HID, CapabilityDomain.TRANSPORT, "USB HID transport", "smoke"),
    (CapabilityId.SERIAL_COMMAND, CapabilityDomain.TRANSPORT, "serial command channel", "smoke"),
    (CapabilityId.ADB, CapabilityDomain.TRANSPORT, "ADB transport", "contract_test"),
    (CapabilityId.BLE, CapabilityDomain.TRANSPORT, "BLE transport", "manual"),
    (
        CapabilityId.NETWORK_RPC,
        CapabilityDomain.TRANSPORT,
        "network RPC transport",
        "contract_test",
    ),
    (CapabilityId.HDMI_CEC, CapabilityDomain.TRANSPORT, "HDMI-CEC transport", "manual"),
    (CapabilityId.DEVICE_HEALTH, CapabilityDomain.DIAGNOSTICS, "device health probe", "smoke"),
    (CapabilityId.INPUT_STATUS, CapabilityDomain.DIAGNOSTICS, "input state inspection", "smoke"),
    (CapabilityId.CAPTURE_HEALTH, CapabilityDomain.DIAGNOSTICS, "capture health probe", "smoke"),
    (CapabilityId.FIRMWARE_INFO, CapabilityDomain.DIAGNOSTICS, "firmware info/handshake", "smoke"),
    (
        CapabilityId.SHELL_EXEC,
        CapabilityDomain.DIAGNOSTICS,
        "shell command execution",
        "contract_test",
    ),
)
CATALOG: Mapping[str, CapabilitySpec] = MappingProxyType(
    {row[0]: CapabilitySpec(*row) for row in _CATALOG_ROWS}
)
assert set(CATALOG) == _ALL_CAPABILITIES


def spec(capability_id: str) -> CapabilitySpec:
    try:
        return CATALOG[str(capability_id)]
    except KeyError as exc:
        raise ValueError(f"unknown capability id {capability_id!r}") from exc


def _text(value: object, *, field_name: str, max_length: int = 256) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return text


def _freeze(values: Mapping[str, str] | None) -> Mapping[str, str]:
    return MappingProxyType({str(k): str(v) for k, v in (values or {}).items()})


@dataclass(frozen=True)
class CapabilityDeclaration:
    capability_id: str
    preconditions: tuple[str, ...] = ()
    probe: str | None = None
    verification_test: str | None = None

    def __post_init__(self) -> None:
        spec(self.capability_id)
        object.__setattr__(self, "capability_id", str(self.capability_id))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        for name in self.preconditions:
            _text(name, field_name="precondition")


@dataclass(frozen=True)
class CapabilityManifest:
    adapter_id: str
    adapter_version: str
    declarations: tuple[CapabilityDeclaration, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _text(self.adapter_id, field_name="adapter_id"))
        object.__setattr__(
            self, "adapter_version", _text(self.adapter_version, field_name="adapter_version")
        )
        seen: set[str] = set()
        for declaration in self.declarations:
            if declaration.capability_id in seen:
                raise ValueError(f"duplicate capability {declaration.capability_id!r}")
            seen.add(declaration.capability_id)
        object.__setattr__(self, "declarations", tuple(self.declarations))

    @property
    def declared_ids(self) -> tuple[str, ...]:
        return tuple(item.capability_id for item in self.declarations)


@dataclass(frozen=True)
class CapabilityRecord:
    capability_id: str
    state: str
    source: str
    preconditions: tuple[str, ...] = ()
    observed_at: float | None = None
    reason: str | None = None
    evidence_ref: str | None = None
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        capability_id = CapabilityId(self.capability_id)
        state = CapabilityState(self.state)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "source", _text(self.source, field_name="source"))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        if self.state in {CapabilityState.UNAVAILABLE, CapabilityState.UNKNOWN} and not self.reason:
            raise ValueError(f"{self.state} capability records must carry a reason")
        if self.evidence_ref is not None:
            _text(self.evidence_ref, field_name="evidence_ref")


class CapabilityView(Mapping[str, CapabilityRecord]):
    """Immutable per-session capability snapshot."""

    def __init__(self, records: Iterable[CapabilityRecord]) -> None:
        values: dict[str, CapabilityRecord] = {}
        for record in records:
            values[record.capability_id] = record
        self._records = MappingProxyType(values)

    def __getitem__(self, key: str) -> CapabilityRecord:
        return self._records[str(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)

    @property
    def records(self) -> tuple[CapabilityRecord, ...]:
        return tuple(self._records.values())

    def supports(self, capability_id: str, *, for_execution: bool = False) -> bool:
        record = self._records.get(str(capability_id))
        if record is None:
            return False
        allowed = {CapabilityState.AVAILABLE, CapabilityState.VERIFIED}
        if not for_execution:
            allowed = allowed | {CapabilityState.DECLARED}
        return record.state in allowed

    def by_domain(self, domain: str) -> tuple[CapabilityRecord, ...]:
        return tuple(
            record
            for record in self._records.values()
            if spec(record.capability_id).domain == domain
        )


def fingerprint_fields(
    *,
    platform_family: str,
    adapter_id: str,
    adapter_version: str,
    device_model: str = _UNKNOWN,
    os_build: str = _UNKNOWN,
    firmware_build: str = _UNKNOWN,
    protocol_version: str = _UNKNOWN,
    permissions: Mapping[str, str] | None = None,
    config: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """The minimal capability-scoping fingerprint (ADR-001 D5)."""

    fields = {
        "platform_family": platform_family or _UNKNOWN,
        "device_model": device_model or _UNKNOWN,
        "os_build": os_build or _UNKNOWN,
        "adapter_id": adapter_id or _UNKNOWN,
        "adapter_version": adapter_version or _UNKNOWN,
        "firmware_build": firmware_build or _UNKNOWN,
        "protocol_version": protocol_version or _UNKNOWN,
    }
    for key, value in (permissions or {}).items():
        fields[f"permission.{str(key).lower()}"] = str(value)
    for key, value in (config or {}).items():
        fields[f"config.{str(key).lower()}"] = str(value)
    return MappingProxyType(fields)


def capability_fingerprint(fields: Mapping[str, str]) -> str:
    canonical = {
        str(key).strip().lower(): str(value).strip() if value is not None else _UNKNOWN
        for key, value in fields.items()
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityEvidence:
    """One immutable verification result (storage lives in ADR-008's store)."""

    evidence_id: str
    capability_id: str
    conclusion: Literal["verified", "failed"]
    device_id: str
    endpoint_ids: tuple[str, ...]
    fingerprint: str
    fingerprint_fields: Mapping[str, str]
    test_id: str
    test_version: str
    evidence_kind: Literal["contract_test", "smoke", "manual"]
    executed_at: float
    environment: Mapping[str, str] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    operator: str | None = None
    notes: str | None = None
    revalidate_after: float | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        spec(self.capability_id)
        if self.conclusion not in {"verified", "failed"}:
            raise ValueError(f"unknown evidence conclusion {self.conclusion!r}")
        if self.evidence_kind not in {"contract_test", "smoke", "manual"}:
            raise ValueError(f"unknown evidence kind {self.evidence_kind!r}")
        if self.evidence_kind == "manual" and self.conclusion == "verified":
            raise ValueError("manual evidence cannot mark a capability verified")
        if not self.artifact_refs:
            raise ValueError("capability evidence must reference at least one artifact")
        object.__setattr__(self, "evidence_id", _text(self.evidence_id, field_name="evidence_id"))
        object.__setattr__(self, "capability_id", str(self.capability_id))
        object.__setattr__(self, "device_id", _text(self.device_id, field_name="device_id"))
        object.__setattr__(self, "fingerprint", _text(self.fingerprint, field_name="fingerprint"))
        object.__setattr__(self, "endpoint_ids", tuple(self.endpoint_ids))
        object.__setattr__(self, "fingerprint_fields", _freeze(self.fingerprint_fields))
        object.__setattr__(self, "environment", _freeze(self.environment))
        object.__setattr__(self, "artifact_refs", tuple(str(ref) for ref in self.artifact_refs))
        object.__setattr__(self, "test_id", _text(self.test_id, field_name="test_id"))
        object.__setattr__(
            self, "test_version", _text(self.test_version, field_name="test_version")
        )
        if self.operator is not None:
            object.__setattr__(self, "operator", _text(self.operator, field_name="operator"))
        if self.revalidate_after is not None and self.revalidate_after <= 0:
            raise ValueError("revalidate_after must be a positive epoch timestamp")

    def is_stale(self, now: float) -> bool:
        return self.revalidate_after is not None and now > self.revalidate_after


def canonical_endpoint_ids(endpoint_ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in endpoint_ids if str(item).strip()}))
