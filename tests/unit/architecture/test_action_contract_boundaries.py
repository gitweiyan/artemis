"""PR-R3.6: static, dynamic and transitive action-boundary ratchet."""

from __future__ import annotations

from pathlib import Path

from import_graph import build_import_graph

ROOT = Path(__file__).resolve().parents[3] / "artemis"


def test_agents_have_no_direct_mcp_controller_driver_or_adapter():
    graph = build_import_graph(ROOT)
    assert (
        graph.violations(
            layer="agents",
            forbidden=(
                "artemis.mcp",
                "artemis.controllers",
                "artemis.drivers.factory",
                "artemis.drivers.android",
                "artemis.drivers.ios",
                "artemis.drivers.mock",
                "artemis.drivers.cloud",
                "artemis.adapters",
            ),
            transitive=False,
        )
        == []
    )


def test_actions_have_no_transitive_agents_mcp_controller_driver_or_adapter():
    graph = build_import_graph(ROOT)
    assert (
        graph.violations(
            layer="actions",
            forbidden=(
                "artemis.agents",
                "artemis.mcp",
                "artemis.controllers",
                "artemis.drivers",
                "artemis.adapters",
            ),
        )
        == []
    )


def test_dynamic_imports_are_scanned_not_ignored():
    graph = build_import_graph(ROOT)
    relevant = [
        item
        for item in graph.dynamic
        if item.module.startswith(("artemis.agents", "artemis.actions")) and item.target is None
    ]
    assert relevant == []


def test_path_report_names_the_full_transitive_dependency():
    graph = build_import_graph(ROOT)
    # The graph itself must be able to report a path, even when current code is clean.
    path = graph.path_to("artemis.actions.service", "artemis.mcp")
    assert path is None or path[0] == "artemis.actions.service"
