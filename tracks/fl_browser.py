"""Load a sample into a channel through FL's browser - no mouse, FL stays
in the background (ui.navigateBrowser / toggleBrowserNode, controller command
ui.browser). Path = names of browser nodes from the ALL tab, e.g.
["Packs", "Drums (ModeAudio)", "Kicks", "Power CrunchKick 02"]."""

from __future__ import annotations

import fl_io


def step(c, what, n=1):
    return fl_io.send(c, "ui.browser", {"steps": [[what, n]]}, timeout=15)["steps"][0][2:4]


def find(c, name, limit=400, direction="down"):
    """Move until the focused node's caption is `name`."""
    for _ in range(limit):
        cap, typ = step(c, direction)
        if cap == name:
            return typ
    raise RuntimeError(f"browser: {name!r} not found")


def goto(c, path):
    """Open folders along `path`; leaves the last node focused."""
    for i, name in enumerate(path):
        find(c, name)
        if i < len(path) - 1:
            step(c, "toggle")  # open the folder
    return step(c, "down", 0)
