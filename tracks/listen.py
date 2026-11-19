"""Play a fragment for Nik and prove which part of the track sounded.

FL's own clock (transport.getPos) is read right after start and before stop,
then mapped to section / bar / beat of dark_808. Optionally the fragment is
recorded from Background Music and its loudness printed per 0.1 s.

FL's big counter shows BARS (B:S:T), not seconds: "13:01:16" is bar 13.
Nik reads it as his position - always name the FL bar.

Run:  uv run python tracks/listen.py bar 37         FL bars 37 .. 38.5
      uv run python tracks/listen.py 34            34 s .. 40 s
      uv run python tracks/listen.py 34 6 --rec    and record
      uv run python tracks/listen.py where 37.5    what is at 37.5 s
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dark_808 as track  # noqa: E402

REC = Path("/tmp/fl_listen.wav")


def where(seconds: float) -> str:
    """Track time in seconds (FL clock, base tempo) -> 'section bar.beat'."""
    beats = seconds * track.BASE_BPM / 60
    for name, tl in reversed(list(track.TL.items())):
        if beats >= tl[0] - track.EPS:
            i = max(k for k in range(len(tl)) if tl[k] <= beats + track.EPS)
            i = min(i, len(tl) - 2)
            beat = max(0.0, i + (beats - tl[i]) / (tl[i + 1] - tl[i]))
            fl_bar = int(beats // 4) + 1  # what FL's big counter shows (B:S:T)
            return (f"FL bar {fl_bar} = {seconds:.1f} s = "
                    f"{name} bar {int(beat // 4) + 1} beat {beat % 4 + 1:.1f}")
    return "before the track"


def play(start: float, dur: float = 6.0, rec: bool = False) -> None:
    c = track.get_connection()
    c.ensure_connected()
    track.send(c, "transport.stop")
    # stop() moves the cursor back to where play started a moment later and can
    # undo a setPosition sent right after it: set, read back, repeat until it holds
    for _ in range(10):
        track.send(c, "transport.setPosition", {"position": int(start * 1000), "mode": 0})
        time.sleep(0.15)
        if abs(track.send(c, "transport.getPos")["ms"] / 1000 - start) < 0.1:
            break
    else:
        raise RuntimeError(f"FL did not move to {start} s")
    proc = None
    if rec:
        proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "avfoundation",
                                 "-i", ":6", "-t", str(dur + 0.6), str(REC)])
        time.sleep(0.3)
    track.send(c, "transport.start")
    p0 = track.send(c, "transport.getPos")
    if abs(p0["ms"] / 1000 - start) > 0.5:
        track.send(c, "transport.stop")
        raise RuntimeError(f"FL started at {p0['ms'] / 1000:.1f} s, not {start} s")
    time.sleep(dur)
    p1 = track.send(c, "transport.getPos")
    track.send(c, "transport.stop")
    track.send(c, "transport.setPosition", {"position": 0, "mode": 0})
    if not (p0["playing"] and p1["playing"]) or p0["song_mode"]:
        print("WARNING: FL was not playing the pattern the whole time", p0, p1)
    a, b = p0["ms"] / 1000, p1["ms"] / 1000
    print(f"FL played {a:.2f} s -> {b:.2f} s")
    print(f"  start: {where(a)}\n  end:   {where(b)}")
    if proc:
        proc.wait()
        loudness()


def loudness() -> None:
    import numpy as np  # only for --rec: uv run --with numpy

    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(REC), "-ac", "1",
                          "-ar", "48000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    w = 4800
    print(" ".join(f"{i / 48000:.1f}:{20 * np.log10(np.sqrt(np.mean(x[i:i + w] ** 2)) + 1e-9):.0f}"
                   for i in range(0, len(x) - w, w)))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "bar":  # FL bar -> seconds at the base tempo
        args = [str((float(args[1]) - 1) * 4 * 60 / track.BASE_BPM)] + args[2:]
    if args and args[0] == "where":
        for s in args[1:]:
            print(s, "s:", where(float(s)))
    else:
        play(float(args[0]), float(args[1]) if len(args) > 1 and args[1][0] != "-" else 6.0,
             "--rec" in args)
