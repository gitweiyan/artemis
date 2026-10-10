"""I2 · what a run states about itself the moment it starts.

On 2026-10-09 a device run "verified" zero degradations (19:38–19:43) while
executing a workspace whose fix had been reverted by a branch operation at 18:10:
the branch was *named* after the fix it did not contain, and no artifact said
which revision had actually run (the incident's P7). Three mechanisms now cover
that ground, and they are deliberately different things:

* branch discipline (I1, ``docs/branch-plan.md``) stops *creating* the state;
* ``run_outcome.environment.code`` (I3) reports it *afterwards* — for Pro runs
  only, because the Flash reactive loop settles no verdict at all;
* this module reports it *before* the run touches the device, for every profile.

What it records is data, not a verdict: revision, uncommitted paths, the provider
tier per node. It deliberately does **not** assert "fix X is present". Such a list
goes stale the moment the next fix lands — the retrospective flagged exactly that
risk — and a branch name has never been evidence. Whether a fix is in place is
what its own tests answer.

Recording never changes a run's semantics, with one opt-in exception: with
``ARTEMIS_REQUIRE_CLEAN_TREE`` set (verification runs), a dirty workspace is
refused before any device work, because a conclusion computed on a revision that
exists in no commit cannot be reproduced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any

from artemis.runtime.degradations import code_identity
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: Written next to the session (``traces/<session_id>/``), so a Flash run — which
#: has no verdict file — still answers "which code ran?".
IDENTITY_FILENAME = "run_identity.json"
#: Set to a truthy value to refuse a dirty workspace instead of warning about it.
STRICT_ENV = "ARTEMIS_REQUIRE_CLEAN_TREE"
#: How many uncommitted paths the warning names before it stops listing.
DIRTY_PATHS_SHOWN = 5


def provider_tiers() -> dict[str, str]:
    """Node → provider for this workspace's configured LLM tiers.

    Provider *names* only: no model, no endpoint, no credential leaves the config.
    Best-effort by design — a run that cannot read its own tiers must not fail.
    """
    try:
        from artemis.config.llm import get_default_llm_config

        dumped = get_default_llm_config().model_dump()
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        # A config that cannot be parsed (pydantic raises ValueError) is exactly the
        # kind of workspace whose identity a reader needs; recording must not turn
        # that into a failed run.
        logger.debug(f"Provider tiers unavailable: {exc}")
        return {}

    tiers: dict[str, str] = {}

    def _collect(node: str, block: Any) -> None:
        if not isinstance(block, dict):
            return
        provider = block.get("provider")
        if isinstance(provider, str) and provider:
            tiers[node] = provider

    for node, block in dumped.items():
        if node == "utils" and isinstance(block, dict):
            for util, util_block in block.items():
                _collect(util, util_block)
            continue
        _collect(node, block)
    return tiers


def startup_identity(profile: str | None = None) -> dict[str, Any]:
    """What this run is about to execute: revision, local state, provider tiers."""
    return {
        "recorded_at": time.time(),
        "profile": profile,
        "code": code_identity(),
        "providers": provider_tiers(),
    }


def _provider_line(tiers: dict[str, str]) -> str:
    """``openai`` when every node shares a tier, else a compact per-tier count."""
    if not tiers:
        return "unknown"
    counts: dict[str, int] = {}
    for provider in tiers.values():
        counts[provider] = counts.get(provider, 0) + 1
    if len(counts) == 1:
        provider = next(iter(counts))
        return f"{provider} ({len(tiers)} nodes)"
    grouped = ", ".join(f"{name} ({count})" for name, count in sorted(counts.items()))
    return grouped


def _dirty_sentence(code: dict[str, Any]) -> str:
    paths = list(code.get("dirty_paths") or [])
    shown = ", ".join(paths[:DIRTY_PATHS_SHOWN])
    if code.get("dirty_paths_truncated") and len(paths) > DIRTY_PATHS_SHOWN:
        shown = f"{', '.join(paths[:DIRTY_PATHS_SHOWN])}, …"
    return (
        f"{code.get('dirty_files')} uncommitted path(s) [{shown}] — revision "
        f"{code.get('head')} on {code.get('branch')} exists in no commit"
    )


def record_start_identity(ctx: Any, *, profile: str | None = None) -> dict[str, Any]:
    """Log the run's identity, leave it in the session directory, honour strict mode.

    Called once the session directory exists and before any device work, so a
    refusal costs nothing but the queue slot.
    """
    identity = startup_identity(profile)
    code = identity["code"]

    logger.info(
        f"Run identity: branch={code.get('branch')} head={code.get('head')} "
        f"dirty={code.get('dirty_files')} providers={_provider_line(identity['providers'])}"
    )
    if code.get("dirty_files"):
        logger.warning(f"Run identity: this run executes {_dirty_sentence(code)}")

    engine = getattr(ctx, "data_engine", None)
    base_dir = getattr(engine, "base_dir", None)
    # ``str``/``Path`` only: a mocked engine satisfies ``os.PathLike`` and would
    # otherwise be written to as a literal directory in the working tree.
    if isinstance(base_dir, (str, Path)):
        try:
            path = Path(base_dir) / IDENTITY_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            # The warning above already carried the finding; the file is a convenience.
            logger.warning(f"Run identity: could not write {IDENTITY_FILENAME}: {exc}")

    if code.get("dirty_files") and os.environ.get(STRICT_ENV):
        raise RuntimeError(
            f"{STRICT_ENV} is set but this run would execute {_dirty_sentence(code)}. "
            f"Commit or stash the work, or unset {STRICT_ENV} to run it as-is."
        )
    return identity


__all__ = [
    "DIRTY_PATHS_SHOWN",
    "IDENTITY_FILENAME",
    "STRICT_ENV",
    "provider_tiers",
    "record_start_identity",
    "startup_identity",
]
