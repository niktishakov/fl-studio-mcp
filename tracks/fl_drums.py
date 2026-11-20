"""Load the track's drum samples into their channels (after an FL restart).

GUI: FL comes forward for ~1 s per sample - ask Nik first. FL's browser is
driven through the API (ui.browser); the focused file sits in the browser's
bottom row at (144, 834) pt and is dragged with a real mouse drag onto the
channel button in the Channel rack. Layout this was measured on: MacBook
1512x982 pt, FL full screen, Channel rack open at its default place, piano
roll hidden; the browser's ALL tab, folder Packs > Drums (ModeAudio) open.

Run: uv run python tracks/fl_drums.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dark_808 as track  # noqa: E402
import fl  # noqa: E402
import fl_browser as b  # noqa: E402
import fl_io  # noqa: E402

SRC = (144, 834)  # focused browser row
BUTTON_X = 522
BUTTON_Y = {0: 177, 1: 207, 2: 238, 3: 267}  # channel buttons 1-4 in the rack


def main():
    c = fl_io.get_connection()
    c.ensure_connected()
    fl_io.send(c, "ui.showWindow", {"window": 3, "hide": True})  # piano roll away
    fl_io.send(c, "ui.showWindow", {"window": 1})  # channel rack
    prev = fl.osa('tell application "System Events" to get name of first '
                  'application process whose frontmost is true')
    for ch, rel in track.DRUM_SAMPLES.items():
        folder, name = rel.split("/")
        b.find(c, folder, limit=2000)
        b.step(c, "toggle")  # open the folder
        b.find(c, name, limit=500)
        fl.osa(f'tell application id "{fl.FL_ID}" to activate')
        time.sleep(0.7)
        fl.drag(*SRC, BUTTON_X, BUTTON_Y[ch])
        time.sleep(0.8)
        fl.osa('tell application "System Events" to set frontmost of '
               f'application process "{prev}" to true')
        b.find(c, folder, direction="up", limit=600)
        b.step(c, "toggle")  # close it again: keeps the tree short
        got = fl_io.send(c, "channels.getAll")["channels"][ch]["name"]
        print(f"channel {ch}: {got}")


if __name__ == "__main__":
    main()
