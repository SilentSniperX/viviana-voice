#!/usr/bin/env python3
"""Static checks for pine/nq_orb_s5b_v1.pine.

There is no Pine compiler in this environment, so these encode the failure modes
that have actually bitten this project. Every rule below exists because a real
TradingView error or a real zero-trade run was traced to it:

  L1  untyped user-function parameters        -> CE10189, reported by the editor
  L2  shorttitle longer than 10 characters    -> SHORT_TITLE_TOO_LONG
  L3  series expression in a const-only arg   -> plotshape `location`
  L4  same-bar read of a flag set this bar    -> the v1.0-v1.2 ZERO-TRADE bug
      (suppress a deliberate one with a `// lint:same-bar-ok` comment)
  L5  ta.* called inside a conditional block  -> unreliable values, silent
  L6  float(na)/int(na) cast expressions      -> not valid v6 syntax
  L7  line continuation at a 4-space multiple -> parsed as a new block, silent
  L8  more than 64 plots                      -> plot limit
  L9  declaration after first use             -> undeclared identifier
  L10 top-level binding read before defined   -> undeclared identifier
  L11 user function called before defined     -> undeclared identifier

L4 is the important one. Pine runs the whole script top to bottom on every bar,
so a `var` flag assigned early in the script is already true when a later block
reads it ON THE SAME BAR. The entry pipeline sets `pendingEntry` on the signal
bar and evaluates it further down; without a `time > signalBarTs` guard the
evaluation runs on the signal bar itself, takes the abort branch and cancels the
order that was just submitted — zero trades, forever, on every symbol.

Run:  python3 tests/lint_pine.py
"""

from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PINE = os.path.join(REPO, "pine", "nq_orb_s5b_v1.pine")

CONST_ONLY_ARGS = ("location", "style", "size")
FINDINGS: list[str] = []


def fail(rule: str, msg: str) -> None:
    FINDINGS.append(f"{rule}: {msg}")


def check(path: str) -> None:
    src = open(path).read()
    lines = src.split("\n")

    # L1 — untyped user-function parameters
    for i, l in enumerate(lines, 1):
        m = re.match(r"^(\w+)\(([^)]*)\)\s*=>", l)
        if m:
            untyped = [p.strip() for p in m.group(2).split(",")
                       if p.strip() and len(p.strip().split()) == 1]
            if untyped:
                fail("L1", f"line {i}: {m.group(1)}() has untyped parameters "
                           f"{untyped}; passing `na` to one is CE10189")

    # L2 — shorttitle length
    m = re.search(r'shorttitle\s*=\s*"([^"]*)"', src)
    if m and len(m.group(1)) > 10:
        fail("L2", f"shorttitle {m.group(1)!r} is {len(m.group(1))} chars, max 10")

    # L3 — series expressions in const-only arguments
    for i, l in enumerate(lines, 1):
        if not re.search(r"\b(plotshape|plotchar|plotarrow)\s*\(", l):
            continue
        block = " ".join(lines[i - 1:i + 4])
        head = block[:block.find(")")] if ")" in block else block
        for arg in CONST_ONLY_ARGS:
            if re.search(rf"\b{arg}\.\w+\s*\?|\?\s*\w*{arg}\.", head):
                fail("L3", f"line {i}: ternary feeding const-only argument "
                           f"'{arg}'")

    # L4 — a flag assigned this bar must not gate a later block on the same bar
    assigned = {}
    for i, l in enumerate(lines, 1):
        m = re.match(r"\s*(\w+)\s*:=\s*true\b", l)
        if m:
            assigned.setdefault(m.group(1), i)
    for flag, set_line in assigned.items():
        for i, l in enumerate(lines, 1):
            if i <= set_line:
                continue
            if not re.match(rf"\s*if\s+{flag}\b", l):
                continue
            # A bar-advance guard makes the same-bar read safe.
            if re.search(r"time\s*[<>]\s*\w+|\bbarssince\b|\[1\]", l):
                continue
            # Some same-bar reads are intended (an order submitted on the signal
            # bar; an S5b sequence that may complete on the bar the pullback
            # opens). Those must SAY SO in the source, so intent is reviewable
            # rather than inferred.
            context = " ".join(lines[max(0, i - 4):i])
            if "lint:same-bar-ok" in context:
                continue
            fail("L4", f"line {i}: `if {flag} ...` can run on the same bar the "
                       f"flag is set (line {set_line}) with no bar-advance "
                       f"guard — this is the zero-trade bug shape")

    # L5 — ta.* inside a conditional block
    for i, l in enumerate(lines, 1):
        if re.search(r"\bta\.\w+\s*\(", l) and (len(l) - len(l.lstrip())) > 0:
            fail("L5", f"line {i}: ta.* called inside an indented block")

    # L6 — float(na) / int(na) casts
    for i, l in enumerate(lines, 1):
        if re.search(r"\b(float|int|bool)\s*\(\s*na\s*\)", l):
            fail("L6", f"line {i}: `{l.strip()}` — declare `float x = na` instead")

    # L7 — continuation lines indented at a multiple of four
    for i in range(1, len(lines)):
        prev = lines[i - 1].split("//")[0].rstrip()
        cur = lines[i]
        if not cur.strip() or cur.strip().startswith("//") or not prev:
            continue
        if prev.endswith(("or", "and", "?", ":", "+", "(", ",", "*", "-", "=")):
            if (len(cur) - len(cur.lstrip())) % 4 == 0:
                fail("L7", f"line {i+1}: continuation indented at a multiple of "
                           f"4; Pine parses it as a new block")

    # L8 — plot budget
    n = sum(1 for l in lines if re.match(r"\s*plot(shape|char|arrow)?\s*\(", l))
    if n > 64:
        fail("L8", f"{n} plots, limit is 64")

    # L9 — identifier used before its `var` declaration
    declared: dict[str, int] = {}
    for i, l in enumerate(lines, 1):
        m = re.match(r"\s*var\s+\w+(?:<[^>]+>)?\s+(\w+)\s*=", l)
        if m:
            declared.setdefault(m.group(1), i)
    for name, decl_line in declared.items():
        for i, l in enumerate(lines[:decl_line - 1], 1):
            if re.search(rf"\b{name}\s*(:=|\+=|-=)", l):
                fail("L9", f"line {i}: `{name}` assigned before its declaration "
                           f"on line {decl_line}")
                break

    # L10 — plain top-level binding READ before the line that defines it.
    #
    # Pine resolves identifiers in source order, so this is an "Undeclared
    # identifier" compile error, not a runtime surprise. L9 only covered `var`
    # declarations, which is why `orbDirStr` — defined in section D and used by
    # the section-B close-exit alert 150 lines earlier — reached a copy/paste
    # handoff. Sections are ordered by narrative, not by dependency, so the
    # distance between definition and use is routinely large here.
    code = [strip_literals(l) for l in lines]
    bound: dict[str, int] = {}
    for i, l in enumerate(code, 1):
        m = re.match(r"^(\w+)\s*=(?!=|>)", l)
        if m and "=>" not in l:
            bound.setdefault(m.group(1), i)
    for name, def_line in bound.items():
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        for i, l in enumerate(code[:def_line - 1], 1):
            if re.match(rf"^{re.escape(name)}\s*=(?!=|>)", l):
                continue                      # an earlier binding of the name
            if pattern.search(l):
                fail("L10", f"line {i}: `{name}` is used before it is defined on "
                            f"line {def_line}; Pine reports an undeclared "
                            f"identifier")
                break

    # L11 — user function CALLED before it is defined. Same failure class as
    # L10; Pine has no forward declarations.
    funcs: dict[str, int] = {}
    for i, l in enumerate(code, 1):
        m = re.match(r"^(\w+)\s*\([^)]*\)\s*=>", l)
        if m:
            funcs.setdefault(m.group(1), i)
    for name, def_line in funcs.items():
        call = re.compile(rf"\b{re.escape(name)}\s*\(")
        for i, l in enumerate(code[:def_line - 1], 1):
            if call.search(l):
                fail("L11", f"line {i}: `{name}()` is called before it is defined "
                            f"on line {def_line}")
                break

    print(f"checked {path} ({len(lines)} lines, {n} plots)")


def strip_literals(line: str) -> str:
    """Blank out comments and string literals so identifier scans see only code."""
    out, quote = [], ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == quote:
                quote = ""
            out.append(" ")
        elif c in "'\"":
            quote = c
            out.append(" ")
        elif c == "/" and line[i + 1:i + 2] == "/":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def main() -> int:
    if not os.path.exists(PINE):
        print(f"missing {PINE}", file=sys.stderr)
        return 2
    check(PINE)
    if FINDINGS:
        print(f"\n{len(FINDINGS)} finding(s):")
        for f in FINDINGS:
            print(f"  {f}")
        return 1
    print("all static Pine checks pass")
    print("NOTE: a static lint is not a compiler. It cannot catch runtime "
          "behaviour, and it cannot replace a TradingView run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
