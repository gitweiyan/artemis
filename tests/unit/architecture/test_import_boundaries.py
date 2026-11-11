"""Narrow import-boundary guards for Phase 9 (architecture ratchet)."""

from __future__ import annotations

import ast
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3] / "artemis"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _violations(
    layer: str, forbidden: tuple[str, ...], *, allow_prefixes: tuple[str, ...] = ()
) -> list[str]:
    hits: list[str] = []
    for path in (ROOT / layer).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if any(relative.startswith(prefix) for prefix in allow_prefixes):
            continue
        for module in _imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                hits.append(f"{path.relative_to(ROOT.parent)}: {module}")
    return hits


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        (
            "core",
            (
                "artemis.agents",
                "artemis.devices",
                "artemis.drivers",
                "artemis.mcp",
                "artemis.reflex",
                "artemis.interfaces",
            ),
        ),
        (
            "safety",
            (
                "artemis.agents",
                "artemis.drivers",
                "artemis.mcp",
                "artemis.reflex",
                "artemis.interfaces",
            ),
        ),
        ("drivers", ("artemis.agents", "artemis.graph", "artemis.mcp")),
        ("perception", ("artemis.drivers", "artemis.agents", "artemis.graph", "artemis.mcp")),
    ],
)
def test_layer_does_not_import_forbidden_modules(layer, forbidden):
    # core/diagnostics/device_smoke.py still lazily reaches the MCP ADB server;
    # it is a documented Phase 9 exception, not a new dependency path.
    allow = ("core/diagnostics/",) if layer == "core" else ()
    assert _violations(layer, forbidden, allow_prefixes=allow) == []


def test_agents_do_not_import_platform_adapters_or_actuators():
    assert (
        _violations(
            "agents",
            (
                "artemis.drivers.ios",
                "artemis.drivers.android",
                "artemis.adapters",
                "artemis.mcp.actuators",
            ),
        )
        == []
    )


#: Explicit, temporary legacy imports. Every entry has a migration owner and an
#: exit condition; new imports in these layers fail below instead of silently
#: becoming architecture debt.
LEGACY_ALLOWED = {
    "agents": {
        "artemis/agents/checker/checker.py": {"artemis.controllers.unified_controller"},
        "artemis/agents/flash/runner.py": {"artemis.controllers.unified_controller"},
        "artemis/agents/planner/planner.py": {"artemis.controllers.unified_controller"},
        "artemis/agents/validator/tool_declarations.py": {"artemis.controllers.unified_controller"},
        "artemis/agents/video_analyzer/video_analyzer.py": {
            "artemis.controllers.controller_factory"
        },
    },
    "graph": {
        "artemis/graph/perception.py": {"artemis.controllers.unified_controller"},
    },
    "drivers": {
        "artemis/drivers/factory.py": {
            "artemis.orchestration.selector",
            "artemis.reflex.capture",
        },
        "artemis/drivers/ios/crop.py": {"artemis.reflex.capture"},
        "artemis/drivers/ios/extension.py": {
            "artemis.reflex.ios_apps",
            "artemis.reflex.pinyin",
        },
        "artemis/drivers/ios/ios_uvc_hid_driver.py": {
            "artemis.reflex.ios_apps",
            "artemis.reflex.pointer_control",
        },
    },
    "interfaces": {
        "artemis/interfaces/cli/commands/ios_calibrate.py": {
            "artemis.drivers.ios.calibration",
            "artemis.drivers.ios.config",
            "artemis.drivers.ios.ios_uvc_hid_driver",
            "artemis.drivers.ios.uvc_identity",
            "artemis.reflex.capture",
            "artemis.reflex.control",
        },
        "artemis/interfaces/cli/commands/ios_smoke.py": {
            "artemis.drivers.ios.config",
            "artemis.drivers.ios.evidence",
            "artemis.drivers.ios.ios_uvc_hid_driver",
            "artemis.drivers.ios.smoke",
            "artemis.drivers.ios.uvc_identity",
            "artemis.reflex.capture",
        },
        "artemis/interfaces/cli/commands/reflex.py": {
            "artemis.reflex",
            "artemis.reflex.capture",
            "artemis.reflex.contract",
            "artemis.reflex.control",
            "artemis.reflex.runtime",
        },
    },
    "perception": {
        "artemis/perception/app_automation.py": {"artemis.devices.capabilities"},
        "artemis/perception/base.py": {"artemis.devices.capabilities"},
        "artemis/perception/camera.py": {"artemis.devices.capabilities"},
        "artemis/perception/composite.py": {
            "artemis.devices.capabilities",
            "artemis.devices.session",
        },
        "artemis/perception/uvc.py": {"artemis.devices.capabilities"},
    },
    "control": {
        "artemis/control/app_control.py": {"artemis.devices.capabilities"},
        "artemis/control/base.py": {"artemis.devices.capabilities"},
        "artemis/control/composite.py": {"artemis.devices.capabilities"},
        "artemis/control/hid.py": {"artemis.devices.capabilities"},
    },
}


def _legacy_hits(layer: str, forbidden: tuple[str, ...]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for path in (ROOT / layer).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT.parent).as_posix()
        for module in _imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                hits.append((relative, module))
    return hits


def test_known_legacy_imports_are_explicitly_allowlisted():
    rules = {
        "agents": ("artemis.controllers",),
        "graph": ("artemis.controllers",),
        "drivers": ("artemis.reflex", "artemis.orchestration"),
        "interfaces": ("artemis.drivers", "artemis.reflex"),
        "perception": ("artemis.devices",),
        "control": ("artemis.devices",),
    }
    unexpected = []
    for layer, forbidden in rules.items():
        allowed = LEGACY_ALLOWED.get(layer, {})
        for path, module in _legacy_hits(layer, forbidden):
            if module not in allowed.get(path, set()):
                unexpected.append(f"{path}: {module}")
    assert unexpected == []
