"""Pre-flight: which code is actually on disk, before a run whose result you trust.

The 2026-10-09 incident was not a bad fix — it was a run that executed a workspace
where the fix had been reverted by a branch operation, and nothing said so. Branch
discipline stops creating that state; this asserts it at the moment it matters.

Exit codes: 0 = reported (dirty warned), 2 = dirty while ARTEMIS_REQUIRE_CLEAN_TREE
is set (a verification run that demands a committed revision).
"""

from __future__ import annotations

import os
import sys

from artemis.runtime.run_identity import startup_identity

STRICT_ENV = "ARTEMIS_REQUIRE_CLEAN_TREE"


def main() -> int:
    identity = startup_identity()
    code = identity["code"]
    providers = sorted(set(identity["providers"].values()))
    print(f"branch={code['branch']} head={code['head']} dirty={code['dirty_files']}")
    for path in code.get("dirty_paths") or []:
        print(f"  uncommitted: {path}")
    print(f"provider tiers: {', '.join(providers) or 'unknown'}")
    if code["dirty_files"] > 0:
        print(
            "WARNING: this run executes code that exists in no commit — the revision"
            " above is not reproducible from the repository alone.",
            file=sys.stderr,
        )
        if os.environ.get(STRICT_ENV):
            print(f"{STRICT_ENV} is set: refusing to continue.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
