"""The skill manifest: one schema, one set of validation errors.

A skill is a directory with a ``skill.json``. The *schema* lives here (the target
architecture's ``artemis/skills/manifest.py``) so that the reflex loader, a future
planner-facing catalogue and any tooling validate the same document the same way. The
loader in ``artemis/reflex/registry.py`` keeps the job of importing entry points; this
module owns what a manifest may say and how it fails.

Manifest::

    {
      "name": "tetris",                 # must equal the directory name
      "version": "0.1.0",
      "kind": "reflex",                 # reflex | flow
      "entry": {"perception": "perception:parse",
                "policy": "policy:decide",
                "control": "control:to_gestures"},
      "calibration": "calibration.json",
      "plan_hint": "one line a planner may read",
      "requires": {"screenshot": true, "api_key_env": "TYPESAFE_API_KEY"},
      "hooks": {"on_start": "session:start", "on_stop": "session:stop"}
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "CALIBRATION_NAME",
    "MANIFEST_NAME",
    "SKILL_KINDS",
    "SkillManifest",
    "SkillManifestError",
    "SkillRequirements",
    "read_manifest",
]

MANIFEST_NAME = "skill.json"
CALIBRATION_NAME = "calibration.json"
#: ``reflex`` = inner-loop skill (parse/decide/gesture); ``flow`` = a fixed task script.
SKILL_KINDS = ("reflex", "flow")
#: Entry points a reflex skill must provide.
REQUIRED_ENTRIES = ("perception", "policy", "control")
OPTIONAL_ENTRIES = ("arena", "calibrate")


class SkillManifestError(RuntimeError):
    """The ``skill.json`` is missing, malformed, or declares something impossible."""


@dataclass(frozen=True)
class SkillRequirements:
    """What a skill declares it needs before it may load."""

    screenshot: bool = True
    api_key_env: str | None = None
    capabilities: tuple[str, ...] = ()

    def missing_api_key(self) -> str | None:
        """The declared API-key variable, when it is unset for this run."""
        if not self.api_key_env:
            return None
        value = os.environ.get(self.api_key_env)
        return None if value and value.strip() else self.api_key_env


@dataclass(frozen=True)
class SkillManifest:
    name: str
    version: str
    kind: str
    entry: dict[str, str]
    calibration_name: str = CALIBRATION_NAME
    plan_hint: str = ""
    requires: SkillRequirements = field(default_factory=SkillRequirements)
    hooks: dict[str, str] = field(default_factory=dict)

    def entry_for(self, role: str) -> str | None:
        return self.entry.get(role)


def _fail(path: Path, reason: str) -> SkillManifestError:
    return SkillManifestError(f"{path}: {reason}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail(path, f"cannot be read ({exc})") from exc
    except ValueError as exc:
        raise _fail(path, f"is not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise _fail(path, "must contain a JSON object")
    return payload


def _requirements(path: Path, raw: Any) -> SkillRequirements:
    if raw is None:
        return SkillRequirements()
    if not isinstance(raw, dict):
        raise _fail(path, "'requires' must be an object")
    capabilities = raw.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = (capabilities,)
    if not isinstance(capabilities, (list, tuple)) or not all(
        isinstance(item, str) for item in capabilities
    ):
        raise _fail(path, "'requires.capabilities' must be a list of strings")
    api_key_env = raw.get("api_key_env")
    if api_key_env is not None and not isinstance(api_key_env, str):
        raise _fail(path, "'requires.api_key_env' must be a string")
    return SkillRequirements(
        screenshot=bool(raw.get("screenshot", True)),
        api_key_env=api_key_env or None,
        capabilities=tuple(capabilities),
    )


def read_manifest(skill_dir: Path, *, name: str | None = None) -> SkillManifest:
    """Parse and validate ``skill_dir/skill.json``.

    ``name`` is the directory name the caller asked for; a manifest that names
    something else is a rename that forgot its directory, and it fails here rather than
    loading the wrong skill.
    """
    skill_dir = Path(skill_dir)
    path = skill_dir / MANIFEST_NAME
    if not path.is_file():
        raise _fail(path, "is missing")
    raw = _read_json(path)

    declared_name = raw.get("name")
    expected = name or skill_dir.name
    if not isinstance(declared_name, str) or not declared_name.strip():
        raise _fail(path, "'name' is required")
    if declared_name != expected:
        raise _fail(
            path,
            f"names {declared_name!r} but the directory is {expected!r}"
            " — the directory name is the skill name",
        )

    entry = raw.get("entry")
    if not isinstance(entry, dict):
        raise _fail(path, "needs an 'entry' object")
    for role in REQUIRED_ENTRIES:
        if not isinstance(entry.get(role), str) or not entry[role].strip():
            raise _fail(path, f"'entry.{role}' is required (e.g. 'module:function')")
    for role in OPTIONAL_ENTRIES:
        if role in entry and not isinstance(entry[role], str):
            raise _fail(path, f"'entry.{role}' must look like 'module:function'")

    kind = str(raw.get("kind") or "reflex")
    if kind not in SKILL_KINDS:
        raise _fail(path, f"'kind' must be one of {', '.join(SKILL_KINDS)}, got {kind!r}")

    hooks_raw = raw.get("hooks")
    if hooks_raw is None:
        hooks: dict[str, Any] = {}
    elif not isinstance(hooks_raw, dict):
        raise _fail(path, "'hooks' must be an object")
    else:
        hooks = hooks_raw

    return SkillManifest(
        name=declared_name,
        version=str(raw.get("version") or "0"),
        kind=kind,
        entry={role: str(value) for role, value in entry.items() if isinstance(value, str)},
        calibration_name=str(raw.get("calibration") or CALIBRATION_NAME),
        plan_hint=str(raw.get("plan_hint") or ""),
        requires=_requirements(path, raw.get("requires")),
        hooks={key: str(value) for key, value in hooks.items() if isinstance(value, str)},
    )
