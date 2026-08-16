#!/usr/bin/env python3
"""Rebuild the shareable handoff package from a checkout of this repo.

Produces `NQ_ORB_S5b_HANDOFF.zip` — everything a reviewer or another engineer
needs, with `HANDOFF_README.md` as the entry point.

Deliberately excluded:
  * `data/raw/`  — licensed vendor market data, and the reason the original
                   handoff was too large to share
  * `.git/`      — history is on GitHub
  * caches and virtualenvs

The result is a few MB, so it fits inside typical upload limits.

Usage:
    python3 tools/make_handoff_zip.py [--out PATH] [--include-generated]

By default the generated parity reports are refreshed first when the raw data
happens to be present, so the evidence in the package matches the code. Pass
`--no-refresh` to package whatever is already on disk.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "data", "__pycache__", ".venv", "venv", "node_modules",
             ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo")
ROOT_NAME = "NQ_ORB_S5b_HANDOFF"

# These must pass before a package is worth sending. They need no market data.
SELF_CHECKS = [
    ["python3", "reference/build_canonical.py"],
    ["python3", "tests/test_reference_engine.py"],
    ["python3", "tests/verify_s5b_reference.py"],
]


def run_self_checks() -> bool:
    ok = True
    for cmd in SELF_CHECKS:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
        tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        print(f"  {'PASS' if r.returncode == 0 else 'FAIL'}  {' '.join(cmd)}"
              f"  — {tail[:70]}")
        ok = ok and r.returncode == 0
    return ok


def collect() -> list[str]:
    files = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name.endswith(SKIP_SUFFIXES):
                continue
            rel = os.path.relpath(os.path.join(root, name), REPO)
            if rel.split(os.sep)[0] in SKIP_DIRS:
                continue
            files.append(rel)
    return sorted(files)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(REPO, "NQ_ORB_S5b_HANDOFF.zip"))
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the self-checks that regenerate the reference "
                         "artifacts before packaging")
    a = ap.parse_args(argv)

    if not a.no_refresh:
        print("self-checks (no market data needed):")
        if not run_self_checks():
            print("\nself-checks failed — not packaging. Fix them first.")
            return 1
        print()

    files = collect()
    if "HANDOFF_README.md" not in files:
        print("HANDOFF_README.md is missing — it is the entry point of the "
              "package.", file=sys.stderr)
        return 1

    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in files:
            z.write(os.path.join(REPO, rel), os.path.join(ROOT_NAME, rel))

    size = os.path.getsize(a.out)
    print(f"{len(files)} files -> {a.out}  ({size/1e6:.1f} MB)")
    print("excluded: data/raw (licensed market data), .git, caches")
    if size > 30e6:
        print("WARNING: over 30 MB — check that data/raw was not packaged.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
