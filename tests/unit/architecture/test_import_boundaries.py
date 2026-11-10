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
