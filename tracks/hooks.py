"""Hook sketches - short, catchy motifs auditioned on their own.

Each hook lives in its own pattern (Pattern 1 keeps the full track), played on
PoiZone (channel 6). Notes: (midi, beat, length, velocity), F minor.

Run:  uv run python tracks/hooks.py            write the hook pattern
      uv run python tracks/hooks.py play CRY   play the hook alone
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import dark_808 as track  # noqa: E402  (reuse verified piano-roll writing)

SAW = 6
PIANO = 8  # "Hook Piano": a second FL Keys, clean and soft (channel 5 stays dirty)

# FL Keys param index -> value: no overdrive, soft hammer, a little muffle,
# longer release, a bit of stereo, slightly dark treble, no tremolo.
PIANO_PARAMS = [(14, 0.0), (2, 0.42), (4, 0.6), (5, 0.5), (1, 0.75),
                (7, 0.3), (11, 0.45), (12, 0.5), (0, 0.0)]

HOOKS = {
    # Chosen hook (2026-11-19): "Fate 3 - cry". A high cry that holds, then
    # collapses to the bottom. The other sketches (A-F, P1-P4, Fate 1/2/4)
    # were deleted. The track uses the same notes: dark_808.HOOK.
    "CRY": (15, "Hook - cry", [
        (73, 0.0, 1.5, 0.78), (72, 1.5, 0.45, 0.62), (65, 2.0, 0.45, 0.6),
        (53, 2.5, 4.0, 0.72), (41, 2.5, 4.0, 0.55),
    ], PIANO),
}


def setup_piano(c):
    track.send(c, "channels.setName", {"index": PIANO, "name": "Hook Piano"})
    for pi, v in PIANO_PARAMS:
        track.send(c, "plugins.setParamValue",
                   {"plugin_index": PIANO, "param_index": pi, "value": v})
    track.send(c, "channels.setVolume", {"index": PIANO, "volume": 1.0})
    track.send(c, "channels.routeToMixer", {"channel_index": PIANO, "mixer_track": 8})
    track.send(c, "mixer.setTrackName", {"track": 8, "name": "Hook Piano"})


def write_all(c, keys=None):
    # channel 8 is "Hook Keys" now, configured by dark_808.py - don't touch its sound
    for key, (pat, name, notes, *rest) in HOOKS.items():
        if keys and key not in keys:
            continue
        ch = rest[0] if rest else SAW
        track.send(c, "patterns.jump", {"index": pat, "name": name})
        time.sleep(0.3)
        r = track.write_notes(c, ch, [
            {"midi": m, "time": t, "duration": d, "velocity": v} for m, t, d, v in notes])
        info = track.send(c, "patterns.info")
        print(key, name, "notes", r["notes_added"], "length", info["length_steps"], "steps")


def play(c, key):
    pat, name = HOOKS[key][:2]
    track.send(c, "patterns.jump", {"index": pat})
    st = track.send(c, "transport.getStatus")
    if not st["is_playing"]:
        track.send(c, "transport.start")
    print("playing", name)


def main():
    c = track.get_connection()
    c.ensure_connected()
    if len(sys.argv) > 2 and sys.argv[1] == "play":
        play(c, sys.argv[2].upper())
        return
    keys = [k.upper() for k in sys.argv[1:]] or None
    track.send(c, "transport.stop")
    track.send(c, "patterns.jump", {"index": 1, "name": "Dark 808"})
    write_all(c, keys)
    play(c, (keys or ["CRY"])[0])


if __name__ == "__main__":
    main()
