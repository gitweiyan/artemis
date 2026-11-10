"""Platform Extension ports and DTOs (ADR-010, Phase 4.3a).

Platform knowledge lives in an L4 Profile extension. L1-L3 only see the Ports and
structured values defined here; they never import a concrete iOS/macOS extension
or branch on a platform name.

The ports are deliberately small and synchronous: they describe *what* a platform
policy can do, not how a particular driver sends bytes. Concrete implementations
(Spotlight launcher, left-edge BACK gesture, pinyin encoder, LocateAnything) live
with their adapter/profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from artemis.core.capabilities import CapabilityManifest, CapabilityState

__all__ = [
    "AppLifecyclePort",
    "AppLifecycleResult",
    "ExtensionPortUnavailable",
    "NavigationPolicy",
    "PermissionChecks",
    "PermissionStatus",
    "PlatformExtension",
    "PromptGuidance",
    "PromptSection",
    "TargetAdjuster",
    "TargetLocator",
    "TextInputPlan",
    "TextInputPolicy",
]


@dataclass(frozen=True)
class AppLifecycleResult:
    """A semantic launch/stop request, not a claim that the UI changed."""

    accepted: bool
    detail: str = ""
    needs_observation: bool = True


@dataclass(frozen=True)
class TextInputPlan:
    """Text prepared by the platform policy, ready for the actuator."""

    text: str
    encoding: str = "utf-8"
    clear_first: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not str(self.encoding).strip():
            raise ValueError("encoding must not be empty")


@dataclass(frozen=True)
class PromptSection:
    section_id: str
    title: str
    body: str

    def __post_init__(self) -> None:
        for name in ("section_id", "title", "body"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class PromptGuidance:
    """Versioned, structured prompt text supplied by a Profile, never by the Agent."""

    guidance_id: str
    sections: tuple[PromptSection, ...]
    version: str = "1"

    def __post_init__(self) -> None:
        for name in ("guidance_id", "version"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "sections", tuple(self.sections))
        if not self.sections:
            raise ValueError("prompt guidance needs at least one section")


@dataclass(frozen=True)
class PermissionStatus:
    name: str
    state: CapabilityState
    detail: str = ""
    remediation: str | None = None

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name:
            raise ValueError("permission name must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "state", CapabilityState(self.state))
        object.__setattr__(self, "detail", str(self.detail or ""))


class ExtensionPortUnavailable(RuntimeError):
    """The extension does not provide a port; callers map this to capability_unavailable."""

    reason = "capability_unavailable"

    def __init__(self, extension_id: str, port_name: str) -> None:
        super().__init__(f"extension {extension_id!r} does not provide {port_name!r}")
        self.extension_id = extension_id
        self.port_name = port_name


@runtime_checkable
class AppLifecyclePort(Protocol):
    def launch(self, identifier: str) -> AppLifecycleResult: ...

    def stop(self, identifier: str | None = None) -> AppLifecycleResult: ...

    def current(self) -> str | None: ...


@runtime_checkable
class NavigationPolicy(Protocol):
    def back(self) -> None: ...

    def home(self) -> None: ...

    def app_switch(self) -> None: ...


@runtime_checkable
class TextInputPolicy(Protocol):
    def prepare(self, text: str) -> TextInputPlan: ...


@runtime_checkable
class TargetAdjuster(Protocol):
    def adjust(self, target: Any, *, frame: Any | None = None) -> Any: ...


@runtime_checkable
class TargetLocator(Protocol):
    def locate(self, frame: Any, query: str) -> Any | None: ...


@runtime_checkable
class PermissionChecks(Protocol):
    def check(self) -> tuple[PermissionStatus, ...]: ...


@dataclass(frozen=True)
class PlatformExtension:
    """A composition of optional platform policies, registered by an L4 Profile."""

    extension_id: str
    platform_family: str
    capabilities: CapabilityManifest
    app_lifecycle: AppLifecyclePort | None = None
    navigation: NavigationPolicy | None = None
    text_input: TextInputPolicy | None = None
    target_adjuster: TargetAdjuster | None = None
    grounding: TargetLocator | None = None
    prompt_guidance: PromptGuidance | None = None
    permissions: PermissionChecks | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("extension_id", "platform_family"):
            value = str(getattr(self, name) or "").strip().lower()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        if self.capabilities is None:
            raise ValueError("platform extension needs a CapabilityManifest")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @property
    def declared(self) -> tuple[str, ...]:
        return self.capabilities.declared_ids

    def supports(self, capability_id: str) -> bool:
        return str(capability_id) in self.declared

    def require(self, port_name: str) -> Any:
        """Return a port, or raise the stable capability_unavailable reason."""

        if not hasattr(self, port_name):
            raise ValueError(f"unknown platform extension port {port_name!r}")
        value = getattr(self, port_name)
        if value is None:
            raise ExtensionPortUnavailable(self.extension_id, port_name)
        return value
