"""Load the drum samples into channels 1-4 after a rebuild (GUI: FL comes
forward for ~1 s per sample - ask Nik first). FL's browser is driven through
the API (ui.browser); the focused file sits in the browser's bottom row at
(144, 834) pt with this window layout; it is dragged with a real mouse drag
onto the channel button. Expects Packs > Drums (ModeAudio) > Kicks loaded
first by hand or the same way. Coordinates: MacBook 1512x982 pt, FL full
screen, Channel rack visible (ui.showWindow 1, piano roll hidden)."""
import sys, subprocess, time
sys.path.insert(0, 'tracks')
import fl, fl_io, fl_browser as b
c = fl_io.get_connection(); c.ensure_connected()
FL = 'tell application id "com.image-line.flstudio" to activate'
# (folder to open, file, target y of the channel button in points)
jobs = [(None, "Power Clap 01.wv", 207), ("Hi Hats", "Power CHat 01.wv", 238),
        ("Snares", "MA Trauma Snare 01.wv", 267)]
prev = fl.osa('tell application "System Events" to get name of first application process whose frontmost is true')
for i, (folder, f, y) in enumerate(jobs):
    if folder:
        cur = jobs[i - 1][0] or "Claps"
        b.find(c, cur, direction="up"); b.step(c, "toggle")       # close the previous folder
        b.find(c, folder, limit=50); b.step(c, "toggle")          # open the next one
        b.find(c, f, limit=400)
    fl.osa(FL); time.sleep(0.7)
    fl.drag(144, 834, 522, y)
    time.sleep(0.8)
    fl.osa(f'tell application "System Events" to set frontmost of application process "{prev}" to true')
    names = [x["name"] for x in fl_io.send(c, "channels.getAll")["channels"]]
    print(f, "->", names[:4], len(names))
