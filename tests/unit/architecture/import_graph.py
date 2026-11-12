"""AST import graph for the action-contract ratchet."""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class DynamicImport:
    module: str
    line: int
    target: str | None


class ImportGraph:
    def __init__(self, root: Path):
        self.root = root
        self.edges: dict[str, set[str]] = {}
        self.dynamic: list[DynamicImport] = []
        self.unresolved: list[DynamicImport] = []
        self._scan()

    def _module_name(self, path: Path) -> str:
        relative = path.relative_to(self.root.parent if self.root.name == "artemis" else self.root)
        return ".".join(relative.with_suffix("").parts)

    def _resolve(self, module: str, level: int, package: str) -> str | None:
        if not module:
            return package if level else None
        if level == 0:
            return module
        parts = package.split(".")
        if level > 1:
            parts = parts[: -(level - 1)]
        return ".".join([*parts, module])

    def _scan(self) -> None:
        for path in self.root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            module = self._module_name(path)
            self.edges.setdefault(module, set())
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            package = module.rsplit(".", 1)[0]
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.edges[module].update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    target = self._resolve(node.module or "", node.level, package)
                    if target:
                        self.edges[module].add(target)
                elif isinstance(node, ast.Call):
                    self._scan_dynamic(module, node)

    def _scan_dynamic(self, module: str, node: ast.Call) -> None:
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name not in {"import_module", "__import__"}:
            return
        target = None
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            target = node.args[0].value
        item = DynamicImport(module, node.lineno, target)
        self.dynamic.append(item)
        if target:
            self.edges[module].add(target)
        else:
            self.unresolved.append(item)

    def reachable(self, module: str, *, transitive: bool = True) -> set[str]:
        if not transitive:
            return set(self.edges.get(module, ()))
        seen: set[str] = set()
        queue = deque(self.edges.get(module, ()))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.edges.get(current, ()))
        return seen

    def violations(
        self, *, layer: str, forbidden: tuple[str, ...], transitive: bool = True
    ) -> list[str]:
        hits: list[str] = []
        prefix = f"artemis.{layer}"
        for module in sorted(self.edges):
            if not (module == prefix or module.startswith(prefix + ".")):
                continue
            targets = self.reachable(module, transitive=transitive)
            for target in sorted(targets):
                if any(target == item or target.startswith(item + ".") for item in forbidden):
                    hits.append(f"{module} -> {target}")
        return hits

    def path_to(self, module: str, forbidden: str) -> list[str] | None:
        queue: deque[list[str]] = deque([[module]])
        seen = {module}
        while queue:
            path = queue.popleft()
            current = path[-1]
            if current == forbidden or current.startswith(forbidden + "."):
                return path
            for target in sorted(self.edges.get(current, ())):
                if target not in seen:
                    seen.add(target)
                    queue.append([*path, target])
        return None


def build_import_graph(root: Path) -> ImportGraph:
    return ImportGraph(root)
