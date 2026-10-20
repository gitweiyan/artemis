"""Finding and validating reflex skills — the only place that knows their layout.

A skill is a directory with a ``skill.json`` manifest and up to three Python
modules. The layout is a contract, because the point of the layer is that adding
a capability never means editing the framework:

```
skills/tetris/
  skill.json        # manifest (below)
  perception.py     # parse(frame: Frame) -> state | None
  policy.py         # decide(state, ctx) -> Action
  control.py        # to_gestures(action) -> list[Gesture]
  calibration.json  # everything app-specific: geometry, colors, gesture map
  tests/            # offline cases, driven by tests/unit/reflex
```

Manifest (``skill.json``)::

    {
      "name": "tetris",
      "version": "0.1.0",
      "kind": "reflex",
      "entry": {"perception": "perception:parse",
                "policy": "policy:decide",
                "control": "control:to_gestures"},
      "calibration": "calibration.json",
      "plan_hint": "…one line a planner may read…",
      "requires": {"screenshot": true, "api_key_env": "TYPESAFE_API_KEY"}
    }

``requires`` is checked, not executed: the registry refuses to load a skill whose
declared needs are not met, and says which one is missing. A skill that cannot be
loaded must never fail the run that asked for it — the caller gets a
:class:`SkillError` with a fixable message.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import sys
import types
from typing import Any

from artemis.reflex.contract import SkillHandle
from artemis.skills.manifest import (
    CALIBRATION_NAME,
    MANIFEST_NAME,
    SkillManifest,
    SkillManifestError,
    read_manifest,
)
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


#: Where skills are looked up, in order. ``ARTEMIS_SKILLS_DIR`` lets a user keep
#: private skills outside the repository (and take precedence over it).
ENV_SKILLS_DIR = "ARTEMIS_SKILLS_DIR"
_DEFAULT_SUBDIR = "skills"


class SkillError(RuntimeError):
    """A skill is missing, malformed, or declares a need this run cannot meet."""


def skills_dir(explicit: str | Path | None = None) -> Path:
    """The directory skills are read from (first match wins)."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_SKILLS_DIR)
    if env and env.strip():
        return Path(env.strip()).expanduser()
    from artemis.config import ROOT_DIR

    return Path(ROOT_DIR) / _DEFAULT_SUBDIR


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SkillError(f"{path} cannot be read: {exc}") from exc
    except ValueError as exc:
        raise SkillError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SkillError(f"{path} must contain a JSON object")
    return payload


#: Prefix for the synthetic package each skill is imported under, e.g.
#: ``artemis_skills_tetris.policy``. Two skills may both ship a ``policy.py``
#: without shadowing each other, which a shared ``sys.path`` entry would not
#: survive.
_PACKAGE_PREFIX = "artemis_skills_"


def skill_package(name: str, skill_dir: Path) -> str:
    """Register ``skill_dir`` as an importable package and return its name."""
    package = _PACKAGE_PREFIX + re.sub(r"[^0-9A-Za-z_]", "_", name)
    existing = sys.modules.get(package)
    if existing is not None and getattr(existing, "__path__", None):
        return package
    module = types.ModuleType(package)
    module.__path__ = [str(skill_dir)]  # type: ignore[attr-defined]
    module.__package__ = package
    sys.modules[package] = module
    return package


def _load_callable(module_path: Path, dotted: str, *, skill: str, role: str) -> Any:
    """Import ``module:attr`` from a skill directory.

    Sibling modules are imported as submodules of the skill's synthetic package,
    so a skill writes ``from . import simulator`` (or
    ``from artemis_skills_tetris import simulator``) and never has to care about
    what else is on the machine.
    """
    if ":" not in dotted:
        raise SkillError(
            f"skill '{skill}': entry.{role} must look like 'module:function', got {dotted!r}"
        )
    module_name, attr = dotted.split(":", 1)
    file_path = module_path / f"{module_name}.py"
    if not file_path.is_file():
        raise SkillError(f"skill '{skill}': {file_path.name} is missing")
    package = skill_package(skill, module_path)
    try:
        module = importlib.import_module(f"{package}.{module_name}")
    except Exception as exc:  # noqa: BLE001 - a skill's import error is its own
        raise SkillError(f"skill '{skill}': importing {file_path.name} failed: {exc}") from exc
    func = getattr(module, attr, None)
    if not callable(func):
        raise SkillError(f"skill '{skill}': {file_path.name} has no callable '{attr}'")
    return func


def _check_requires(name: str, requires: dict[str, Any]) -> None:
    """Refuse to load a skill whose declared needs are not met — with the reason."""
    # `screenshot: true` is a declaration, not a probe: every reflex loop needs frames
    # and the runtime reports a dead capture path itself. It must not short-circuit the
    # other declarations, though — an unset API key is a skill that cannot run at all.
    api_key_env = requires.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env.strip():
        if not os.environ.get(api_key_env.strip()):
            raise SkillError(
                f"skill '{name}' needs {api_key_env} and it is not set "
                f"(put it in .env at the repository root, or export it where the "
                f"daemon runs)"
            )


def load_skill(name: str, *, directory: str | Path | None = None) -> SkillHandle:
    """Load and validate one skill by name."""
    root = skills_dir(directory)
    skill_dir = root / name
    if not skill_dir.is_dir():
        known = (
            ", ".join(sorted(p.name for p in root.iterdir() if p.is_dir()))
            if root.is_dir()
            else "none"
        )
        raise SkillError(f"skill '{name}' not found under {root} (available: {known})")

    try:
        manifest = read_manifest(skill_dir, name=name)
    except SkillManifestError as exc:
        raise SkillError(f"skill '{name}': {exc}") from exc

    requires = {
        "screenshot": manifest.requires.screenshot,
        "api_key_env": manifest.requires.api_key_env,
    }
    _check_requires(name, {k: v for k, v in requires.items() if v is not None})

    calibration: dict[str, Any] = {}
    calibration_path = skill_dir / (manifest.calibration_name or CALIBRATION_NAME)
    if calibration_path.is_file():
        calibration = _read_json(calibration_path)

    return SkillHandle(
        name=name,
        version=manifest.version,
        directory=skill_dir,
        parse=_load_callable(
            skill_dir, manifest.entry["perception"], skill=name, role="perception"
        ),
        decide=_load_callable(skill_dir, manifest.entry["policy"], skill=name, role="policy"),
        to_gestures=_load_callable(
            skill_dir, manifest.entry["control"], skill=name, role="control"
        ),
        calibration=calibration,
        plan_hint=manifest.plan_hint,
        requires=requires,
        on_start=_optional_hook(skill_dir, manifest.hooks.get("on_start"), name, "on_start"),
        on_stop=_optional_hook(skill_dir, manifest.hooks.get("on_stop"), name, "on_stop"),
        arena=_optional_entry(skill_dir, manifest.entry.get("arena"), name, "arena"),
    )


def _optional_entry(skill_dir: Path, dotted: Any, name: str, role: str) -> Any:
    """An entry point a skill may omit (nothing else depends on it)."""
    if not isinstance(dotted, str) or not dotted.strip():
        return None
    return _load_callable(skill_dir, dotted, skill=name, role=role)


def _optional_hook(skill_dir: Path, dotted: Any, name: str, role: str) -> Any:
    if not isinstance(dotted, str) or not dotted.strip():
        return None
    return _load_callable(skill_dir, dotted, skill=name, role=role)


def available_skills(*, directory: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """``{name: {version, ok, error, plan_hint}}`` for every skill directory.

    Loading is attempted so a broken skill is reported here instead of at the
    moment an agent tries to use it.
    """
    root = skills_dir(directory)
    found: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return found
    for skill_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if skill_dir.name.startswith((".", "_")):
            continue
        try:
            handle = load_skill(skill_dir.name, directory=root)
        except SkillError as exc:
            found[skill_dir.name] = {"ok": False, "error": str(exc)}
            continue
        found[skill_dir.name] = {
            "ok": True,
            "version": handle.version,
            "plan_hint": handle.plan_hint,
            "calibrated": bool(handle.calibration),
        }
    return found


__all__ = [
    "CALIBRATION_NAME",
    "ENV_SKILLS_DIR",
    "MANIFEST_NAME",
    "SkillError",
    "available_skills",
    "load_skill",
    "skill_package",
    "skills_dir",
]
