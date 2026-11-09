"""Stable host identity for resource scoping (ADR-003).

Resource leases and evidence are scoped by ``(host_id, resource_id)``. The host id
must not change between runs on one install and must not be a PID or temp path.

Resolution order:

1. ``ARTEMIS_HOST_ID`` (containers, CI, operator-managed rigs);
2. a deterministic digest of hostname + user + interpreter prefix.

There is deliberately no file write here: an implicit id file would make tests and
read-only installs mutate the user's app directory. Operators that need a portable
id set ``ARTEMIS_HOST_ID``.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import socket
import sys
from collections.abc import Mapping

__all__ = ["ENV_HOST_ID", "host_id"]

ENV_HOST_ID = "ARTEMIS_HOST_ID"

_VALID_HOST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _sanitize(candidate: str) -> str | None:
    text = candidate.strip()
    return text if _VALID_HOST_ID.fullmatch(text) else None


def _user() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):  # no passwd entry / locked-down container
        return "unknown"


def _derived() -> str:
    seed = "|".join((socket.gethostname(), _user(), sys.prefix))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def host_id(env: Mapping[str, str] | None = None) -> str:
    """The stable id of the host running ARTEMIS (never the target device id)."""

    values = os.environ if env is None else env
    override = values.get(ENV_HOST_ID)
    if override is not None:
        cleaned = _sanitize(override)
        if cleaned is not None:
            return cleaned
    return _derived()
