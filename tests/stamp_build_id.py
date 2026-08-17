#!/usr/bin/env python3
"""Stamp pine/nq_orb_s5b_v1.pine with a BUILD_ID derived from its own contents.

A TradingView alert is a SNAPSHOT of the script at alert-creation time. An alert
built from an older revision keeps running after the chart is updated, and
nothing in the payload distinguished the two. BUILD_ID does — but only if it
actually tracks the file, which is what this tool and lint rule L12 enforce
between them. "Remember to restamp" would be another human-memory rule, and
human-memory rules are what this whole exercise exists to remove.

Run:  python3 tests/stamp_build_id.py          # rewrite the constant
      python3 tests/stamp_build_id.py --check  # exit 1 if stale (what L12 uses)
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINE = os.path.join(REPO, "pine", "nq_orb_s5b_v1.pine")
PATTERN = re.compile(r'^(BUILD_ID\s*=\s*")([^"]*)(")', re.M)
REVISION = "r3"


def content_hash(src: str) -> str:
    """Hash of the script with the BUILD_ID line neutralised.

    Excluding the constant itself is what makes the hash a fixed point: stamping
    the file must not change the value being stamped.
    """
    neutral = PATTERN.sub(r'\1\3', src)
    return hashlib.sha256(neutral.encode()).hexdigest()[:12]


def expected(src: str) -> str:
    return f"{REVISION}-{content_hash(src)}"


def current(src: str) -> str | None:
    m = PATTERN.search(src)
    return m.group(2) if m else None


def main(argv: list[str]) -> int:
    src = open(PINE).read()
    if not PATTERN.search(src):
        print("no BUILD_ID constant in the Pine", file=sys.stderr)
        return 2
    want, have = expected(src), current(src)
    if "--check" in argv:
        if want == have:
            print(f"BUILD_ID is current: {have}")
            return 0
        print(f"BUILD_ID is STALE\n  file says   {have}\n  should be   {want}\n"
              f"Run: python3 tests/stamp_build_id.py", file=sys.stderr)
        return 1
    if want == have:
        print(f"BUILD_ID already current: {have}")
        return 0
    open(PINE, "w").write(PATTERN.sub(rf'\g<1>{want}\g<3>', src))
    print(f"BUILD_ID {have} -> {want}")
    print("The Pine changed, so any TradingView alert created from the previous "
          "build is now REJECTED by the receiver. Recreate both alerts.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
