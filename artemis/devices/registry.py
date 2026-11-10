"""Static adapter/profile registry and pure profile selection (ADR-005).

Phase 2a registers metadata and the Mock profile; Android/iOS builders are
wired in Phase 2c/4. Registration is explicit (``builtin_registry()``), never an
import-time side effect, and duplicate IDs fail loudly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from artemis.core.capabilities import CapabilityId, CapabilityManifest
from artemis.devices.composition import CompositionRequest

__all__ = [
    "AdapterFactory",
    "AdapterRegistry",
    "DeviceProfile",
    "ProfileBuild",
    "ProfileBuildError",
    "ProfileSelection",
    "ProfileSelectionError",
    "builtin_registry",
    "select_device_profile",
]


class ProfileSelectionError(RuntimeError):
    """No profile matches the request; ``reason`` is a stable category."""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        missing_capabilities: Iterable[str] = (),
        profile_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.missing_capabilities = tuple(missing_capabilities)
        self.profile_id = profile_id


class ProfileBuildError(RuntimeError):
    """A selected profile could not be built (adapter/config/handle/capability)."""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        profile_id: str | None = None,
        adapter_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.profile_id = profile_id
        self.adapter_id = adapter_id


@dataclass(frozen=True)
class AdapterFactory:
    adapter_id: str
    version: str
    manifest: CapabilityManifest
    build: Callable[[CompositionRequest], Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.adapter_id).strip():
            raise ValueError("adapter_id must not be empty")
        if not str(self.version).strip():
            raise ValueError("adapter version must not be empty")


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    platform_family: str
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    adapter_ids: tuple[str, ...] = ()
    build: Callable[[CompositionRequest, AdapterRegistry], ProfileBuild] | None = None

    def __post_init__(self) -> None:
        if not str(self.profile_id).strip():
            raise ValueError("profile_id must not be empty")
        if not str(self.platform_family).strip():
            raise ValueError("profile platform_family must not be empty")
        for capability in (*self.required_capabilities, *self.optional_capabilities):
            # Raises for ids outside the ADR-001 catalog.
            from artemis.core.capabilities import spec

            spec(capability)
        object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))
        object.__setattr__(self, "optional_capabilities", tuple(self.optional_capabilities))
        object.__setattr__(self, "adapter_ids", tuple(self.adapter_ids))


@dataclass(frozen=True)
class ProfileBuild:
    profile_id: str
    session: Any | None = None
    driver: Any | None = None
    adapters: Mapping[str, Any] = field(default_factory=dict)
    leases: Any | None = None


@dataclass(frozen=True)
class ProfileSelection:
    profile_id: str
    reason: str
    profile: DeviceProfile
    missing_capabilities: tuple[str, ...] = ()


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, AdapterFactory] = {}
        self._profiles: dict[str, DeviceProfile] = {}

    def register_adapter(self, factory: AdapterFactory) -> None:
        if factory.adapter_id in self._adapters:
            raise ValueError(f"duplicate adapter_id {factory.adapter_id!r}")
        self._adapters[factory.adapter_id] = factory

    def register_profile(self, profile: DeviceProfile) -> None:
        if profile.profile_id in self._profiles:
            raise ValueError(f"duplicate profile_id {profile.profile_id!r}")
        self._profiles[profile.profile_id] = profile

    def adapter(self, adapter_id: str) -> AdapterFactory:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise ProfileSelectionError(
                "adapter_unavailable",
                f"adapter {adapter_id!r} is not registered",
            ) from exc

    def profile(self, profile_id: str) -> DeviceProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ProfileSelectionError(
                "missing_profile",
                f"profile {profile_id!r} is not registered",
                profile_id=profile_id,
            ) from exc

    @property
    def adapters(self) -> tuple[AdapterFactory, ...]:
        return tuple(self._adapters.values())

    @property
    def profiles(self) -> tuple[DeviceProfile, ...]:
        return tuple(self._profiles.values())


def _requested_capabilities(request: CompositionRequest) -> frozenset[str]:
    if request.capability_ids:
        return frozenset(request.capability_ids)
    # Legacy planning view: only the flags that map to catalog ids.
    from artemis.devices.capabilities_compat import declared_capability_ids

    return frozenset(declared_capability_ids(request.capabilities))


def _missing(profile: DeviceProfile, capabilities: frozenset[str]) -> tuple[str, ...]:
    return tuple(
        capability for capability in profile.required_capabilities if capability not in capabilities
    )


def _adapters_missing(profile: DeviceProfile, registry: AdapterRegistry) -> tuple[str, ...]:
    return tuple(
        adapter_id
        for adapter_id in profile.adapter_ids
        if adapter_id not in {adapter.adapter_id for adapter in registry.adapters}
    )


def select_device_profile(
    request: CompositionRequest,
    registry: AdapterRegistry,
) -> ProfileSelection:
    """Pick a profile without touching hardware or the environment."""

    capabilities = _requested_capabilities(request)
    platform = str(request.platform or "").strip().lower()

    if request.profile_id:
        profile = registry.profile(request.profile_id)
        if platform and profile.platform_family != platform:
            raise ProfileSelectionError(
                "platform_mismatch",
                f"profile {profile.profile_id!r} targets {profile.platform_family!r},"
                f" not {platform!r}",
                profile_id=profile.profile_id,
            )
        missing = _missing(profile, capabilities)
        if missing and capabilities:
            raise ProfileSelectionError(
                "missing_capability",
                f"profile {profile.profile_id!r} needs {', '.join(missing)}",
                missing_capabilities=missing,
                profile_id=profile.profile_id,
            )
        missing_adapters = _adapters_missing(profile, registry)
        if missing_adapters:
            raise ProfileSelectionError(
                "adapter_unavailable",
                f"profile {profile.profile_id!r} needs adapters {', '.join(missing_adapters)}",
                profile_id=profile.profile_id,
            )
        return ProfileSelection(profile.profile_id, "explicit profile", profile)

    candidates: list[tuple[DeviceProfile, tuple[str, ...]]] = []
    for profile in sorted(registry.profiles, key=lambda item: item.profile_id):
        if platform and profile.platform_family != platform:
            continue
        missing_adapters = _adapters_missing(profile, registry)
        if missing_adapters:
            continue
        missing = _missing(profile, capabilities)
        if capabilities and missing:
            continue
        candidates.append((profile, missing))

    if not candidates:
        raise ProfileSelectionError(
            "missing_profile",
            f"no profile matches platform {platform!r} and the requested capabilities",
        )

    # More required capabilities covered first, then stable profile id.
    candidates.sort(key=lambda item: (-len(item[0].required_capabilities), item[0].profile_id))
    profile, _ = candidates[0]
    return ProfileSelection(profile.profile_id, "capability match", profile)


def builtin_registry() -> AdapterRegistry:
    """Register the built-in adapters/profiles; safe to call per composition root."""

    from artemis.devices.capability_adapters import (
        android_adapters,
        build_mock_profile,
        ios_adapters,
        mock_adapters,
    )

    registry = AdapterRegistry()
    for adapter in (*android_adapters(), *ios_adapters(), *mock_adapters()):
        registry.register_adapter(adapter)

    registry.register_profile(
        DeviceProfile(
            profile_id="android.app",
            platform_family="android",
            required_capabilities=(CapabilityId.UI_TREE_READ, CapabilityId.TOUCH_ABSOLUTE),
            adapter_ids=("android.app",),
        )
    )
    registry.register_profile(
        DeviceProfile(
            profile_id="ios.uvc_hid",
            platform_family="ios",
            required_capabilities=(CapabilityId.FRAME_CAPTURE, CapabilityId.POINTER_RELATIVE),
            adapter_ids=("uvc.capture", "esp32_hid", "serial.command"),
        )
    )
    registry.register_profile(
        DeviceProfile(
            profile_id="mock.screen",
            platform_family="mock",
            required_capabilities=(CapabilityId.FRAME_CAPTURE,),
            adapter_ids=("mock.screen",),
            build=build_mock_profile,
        )
    )
    return registry
