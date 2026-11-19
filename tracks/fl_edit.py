"""Edits on top of a generated track: short text operations in FL bars.

The track script (e.g. dark_808.py) generates the notes; the edits list
(tracks/<track>.edits.json) is applied on top. So a change like "remove the
clap in FL bars 36-38" is one line, not new Python - and every edit can be
switched off, reordered or tried as variant B (fl ab).

Positions are FL bars (what FL's big counter shows), inclusive: "bars 36-38"
= beats 140 .. 152 at the base tempo. One FL bar = 4 beats.

Operations (CH = channel name or several: "kick,clap", or "all"):
  del   CH bars A-B                  remove notes
  vel   CH bars A-B x1.2 | =0.8      velocity (scale or set), clipped to 0.05..1
  len   CH bars A-B x0.5 | =0.25     length in beats (scale or set)
  tr    CH bars A-B +12 | -5         transpose
  pitch CH bars A-B to 65,77         every onset plays these pitches instead
  shift CH bars A-B +0.25            move in time (beats)
  copy  CH bars A-B to N             copy the notes so bar A lands on bar N
  move  CH bars A-B to CH2           move notes to another channel
  add   CH bar N beats 0,1.5 [m 60] [v 0.9] [len 0.25]   new notes
"""

from __future__ import annotations

import json
from pathlib import Path

EPS = 1e-3
DIR = Path(__file__).resolve().parent


def bar_range(spec: str) -> tuple[float, float]:
    """'36-38' or '36' -> (start beat, end beat) of those FL bars."""
    a, _, b = spec.partition("-")
    a, b = float(a), float(b or a)
    return (a - 1) * 4, b * 4


def _num(s: str) -> float:
    return float(s.lstrip("x=+"))


class Op:
    def __init__(self, text: str, names: dict[str, int]):
        self.text = text.strip()
        t = self.text.split()
        if len(t) < 3:
            raise ValueError(f"too short: {text!r}")
        self.verb = t[0]
        self.chs = list(names.values()) if t[1] == "all" else [names[n] for n in t[1].split(",")]
        self.names = names
        kw = {}
        i = 2
        while i < len(t):
            if i + 1 < len(t) and t[i] in ("bars", "bar", "beats", "to", "m", "v", "len"):
                kw[t[i]] = t[i + 1]
                i += 2
            else:
                kw.setdefault("arg", t[i])
                i += 1
        self.kw = kw
        rng = kw.get("bars") or kw.get("bar")
        if not rng:
            raise ValueError(f"no 'bars A-B' in {text!r}")
        self.t0, self.t1 = bar_range(rng)
        if self.verb not in ("del", "vel", "len", "tr", "pitch", "shift", "copy", "move", "add"):
            raise ValueError(f"unknown operation {self.verb!r}")

    def inside(self, n) -> bool:
        return self.t0 - EPS <= n["time"] < self.t1 - EPS

    def apply(self, P: dict) -> int:
        """Change P in place; return how many notes were touched."""
        v, kw = self.verb, self.kw
        if v == "add":
            beats = [float(b) for b in kw["beats"].split(",")]
            m, vel = int(kw.get("m", 60)), float(kw.get("v", 0.9))
            dur = float(kw.get("len", 0.25))
            for ch in self.chs:
                P[ch].extend({"midi": m, "time": round(self.t0 + b, 4), "duration": dur,
                              "velocity": vel} for b in beats)
            return len(beats) * len(self.chs)
        touched = 0
        for ch in self.chs:
            inn = [n for n in P[ch] if self.inside(n)]
            out = [n for n in P[ch] if not self.inside(n)]
            touched += len(inn)
            if v == "del":
                P[ch] = out
                continue
            if v == "move":
                P[ch] = out
                P[self.names[kw["to"]]].extend(inn)
                continue
            if v == "copy":
                d = (float(kw["to"]) - 1) * 4 - self.t0
                P[ch] = out + inn + [{**n, "time": round(n["time"] + d, 4)} for n in inn]
                continue
            if v == "pitch":
                pitches = [int(p) for p in kw["to"].split(",")]
                hits = {}
                for n in inn:
                    o = hits.get(n["time"])
                    hits[n["time"]] = n if o is None else {
                        **o, "velocity": max(o["velocity"], n["velocity"]),
                        "duration": max(o["duration"], n["duration"])}
                inn = [{**n, "midi": p} for n in hits.values() for p in pitches]
                P[ch] = out + inn
                continue
            arg = kw["arg"]
            new = []
            for n in inn:
                n = dict(n)
                if v == "vel":
                    x = n["velocity"] * _num(arg) if arg[0] == "x" else _num(arg)
                    n["velocity"] = round(max(0.05, min(1.0, x)), 3)
                elif v == "len":
                    x = n["duration"] * _num(arg) if arg[0] == "x" else _num(arg)
                    n["duration"] = round(max(0.02, x), 4)
                elif v == "tr":
                    n["midi"] = n["midi"] + int(arg)
                elif v == "shift":
                    n["time"] = round(n["time"] + float(arg), 4)
                new.append(n)
            P[ch] = out + new
        return touched


def path(track: str) -> Path:
    return DIR / f"{track}.edits.json"


def load(track: str) -> list[dict]:
    p = path(track)
    return json.loads(p.read_text()) if p.exists() else []


def save(track: str, edits: list[dict]) -> None:
    path(track).write_text(json.dumps(edits, indent=1, ensure_ascii=False) + "\n")


def apply_all(P: dict, edits: list[dict], names: dict[str, int], extra=()) -> list[str]:
    """Apply the enabled edits (+ extra op strings), then sort and drop
    stacked duplicates. Returns a log line per op."""
    log = []
    for e in [e for e in edits if e.get("on", True)] + [{"op": x} for x in extra]:
        n = Op(e["op"], names).apply(P)
        log.append(f"{e['op']}  ({n} notes)")
    for ch, notes in P.items():
        seen = {}
        for n in notes:
            k = (n["midi"], round(n["time"], 4))
            o = seen.get(k)
            seen[k] = n if o is None or n["velocity"] > o["velocity"] else o
        P[ch] = sorted(seen.values(), key=lambda n: (n["time"], n["midi"]))
    return log


def check_syntax(text: str, names: dict[str, int]) -> str | None:
    try:
        Op(text, names)
        return None
    except (ValueError, KeyError, IndexError) as e:
        return str(e)


