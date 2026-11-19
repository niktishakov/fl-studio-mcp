"""Rules from the lessons in CLAUDE.md, checked by code before anything is
written to FL (fl check). A track adds its own rules as CHECKS = [(name, fn)],
fn(P) -> list of problems.

Also: a channel x FL-bar map of note counts (fl map) - a quick look at the
structure without listening.
"""

from __future__ import annotations

from collections import Counter

from fl_io import MARK_STEP

EPS = 1e-3


def generic(P: dict, names: dict[str, int]) -> list[tuple[str, str]]:
    """[(level, message)]: level 'ERROR' blocks writing, 'warn' does not."""
    rev = {v: k for k, v in names.items()}
    out = []
    for ch, notes in P.items():
        nm = rev.get(ch, f"ch{ch}")
        # stacked duplicates: same pitch at the same time = +6 dB on that hit
        dup = [k for k, c in Counter((n["midi"], round(n["time"], 3)) for n in notes).items()
               if c > 1]
        if dup:
            out.append(("ERROR", f"{nm}: {len(dup)} stacked duplicates, first {dup[0]}"))
        bad = [n for n in notes if n["time"] < 0 or n["duration"] <= 0
               or not 0 < n["velocity"] <= 1]
        if bad:
            out.append(("ERROR", f"{nm}: {len(bad)} notes with bad time/length/velocity"))
        if any(n["time"] * 4 >= MARK_STEP - 1 for n in notes):
            out.append(("ERROR", f"{nm}: notes reach the marker step {MARK_STEP}"))
        # same pitch overlapping itself (a re-hit before the old note ends)
        last = {}
        over = 0
        for n in sorted(notes, key=lambda n: n["time"]):
            e = last.get(n["midi"])
            if e is not None and n["time"] < e - 0.01:
                over += 1
            last[n["midi"]] = max(e or 0, n["time"] + n["duration"])
        if over:
            out.append(("info", f"{nm}: {over} notes overlap the same pitch"))
    return out


def run(track, P: dict) -> list[tuple[str, str]]:
    out = generic(P, track.NAMES)
    for name, fn in getattr(track, "CHECKS", []):
        out += [("ERROR", f"{name}: {m}") for m in fn(P)]
    return out


def bar_map(P: dict, names: dict[str, int], a: int, b: int) -> str:
    """Note onsets per channel per FL bar, bars a..b inclusive."""
    rows = [" " * 7 + "".join(f"{x:>4}" for x in range(a, b + 1))]
    for nm, ch in names.items():
        cnt = Counter(int(n["time"] // 4) + 1 for n in P[ch])
        cells = [cnt.get(x, 0) for x in range(a, b + 1)]
        if any(cells):
            rows.append(f"{nm:<7}" + "".join(f"{c or '.':>4}" for c in cells))
    return "\n".join(rows)


def steps_fit(notes: list[dict]) -> bool:
    """True if a channel can go through the step sequencer API (no GUI):
    at most one note per 16th step and a single fixed length is fine."""
    steps = Counter(int(round(n["time"] * 4 * 24)) // 24 for n in notes)
    return all(c == 1 for c in steps.values())
