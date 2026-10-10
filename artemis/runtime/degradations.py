"""Runtime degradations that a run must not hide from its own conclusion.

``run_outcome.json`` is what a reader trusts. On 2026-10-09 the 18:32 run lost its
memory-compression prompt path 9 times and its visual step summaries 8 times,
finished ``completed`` with ``passed=2 / failed=0``, and recorded none of it; the
14:30 run did the same with 7 degradations. A run that keeps working while a
capability is missing is not the same object as a healthy run, and the difference
belongs in the verdict rather than in a log nobody greps.

Design: the counter rides the **run context** (like ``ctx.transcript_ledger``), so
each call site is a one-liner and no node has to thread extra state. Counting can
never raise: bookkeeping must not be able to break a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any

from artemis.clients.screen_client_factory import hierarchy_backend_summary
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

#: The operator's transcript prompt path failed and the turn fell back to the
#: legacy two-message build: memory compression is not what the model saw.
TRANSCRIPT_FALLBACK = "transcript_prompt_fallback"
#: A visual step summary could not be produced: the image history is not being
#: compressed for later turns.
VISUAL_SUMMARY_FAILURE = "visual_summary_failure"

#: Kinds that mean a capability was missing, as opposed to a retried call.
CAPABILITY_KINDS = frozenset({TRANSCRIPT_FALLBACK, VISUAL_SUMMARY_FAILURE})

_LOG_ATTR = "run_degradations"
_EXAMPLE_CHARS = 200


@dataclass
class DegradationLog:
    """Counts per kind, with one example each, for the run's conclusion."""

    counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, str] = field(default_factory=dict)

    def record(self, kind: str, detail: Any = None) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if detail is not None and kind not in self.examples:
            text = " ".join(str(detail).split())
            if text:
                self.examples[kind] = text[:_EXAMPLE_CHARS]

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def summary(self) -> dict[str, Any]:
        """The block that goes into ``run_outcome.environment``."""
        return {
            "counts": dict(self.counts),
            "total": self.total,
            "examples": dict(self.examples),
            "capability_degraded": any(kind in CAPABILITY_KINDS for kind in self.counts),
        }


def log_for(ctx: Any) -> DegradationLog:
    """The run's log, created on first use. Never raises."""
    existing = getattr(ctx, _LOG_ATTR, None)
    if isinstance(existing, DegradationLog):
        return existing
    created = DegradationLog()
    try:
        setattr(ctx, _LOG_ATTR, created)
    except (AttributeError, TypeError, ValueError):
        # A context that cannot hold attributes still gets a usable, if local, log.
        return created
    return created


def record(ctx: Any, kind: str, detail: Any = None) -> None:
    """Count one degradation. Safe to call from any failure handler."""
    try:
        log_for(ctx).record(kind, detail)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Degradation recording skipped: {exc}")


def code_identity(repo: Path | str | None = None) -> dict[str, Any]:
    """Which revision ran: branch, HEAD and how many files were modified.

    Best-effort by design — a workspace without git reports ``unknown`` rather
    than failing the run that is trying to describe itself.
    """
    if repo is None:
        try:
            from artemis.config import ROOT_DIR

            repo = ROOT_DIR
        except Exception:  # pylint: disable=broad-exception-caught
            repo = None
    identity: dict[str, Any] = {"head": "unknown", "branch": "unknown", "dirty_files": -1}
    if repo is None:
        return identity

    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=15
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    head = _git("rev-parse", "--short", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    status = _git("status", "--porcelain")
    if head:
        identity["head"] = head.strip()
    if branch:
        identity["branch"] = branch.strip()
    if status is not None:
        identity["dirty_files"] = len([line for line in status.splitlines() if line.strip()])
    return identity


def build_run_environment(ctx: Any, client: Any = None) -> dict[str, Any]:
    """``run_outcome.environment``: backend, degradations and code identity.

    The backend block is the existing hierarchy-source summary (which already
    carries the switch history); this only adds what was missing from the verdict.
    """
    if client is None:
        client = getattr(ctx, "ui_adb_client", None)
    try:
        environment = dict(hierarchy_backend_summary(client) or {})
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Backend summary unavailable: {exc}")
        environment = {}
    environment["degradations"] = log_for(ctx).summary()
    environment["code"] = code_identity()
    return environment


__all__ = [
    "CAPABILITY_KINDS",
    "TRANSCRIPT_FALLBACK",
    "VISUAL_SUMMARY_FAILURE",
    "DegradationLog",
    "build_run_environment",
    "code_identity",
    "log_for",
    "record",
]
