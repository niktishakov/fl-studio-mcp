"""Extra tools (local extension): build a track without mouse or GUI.

Tempo, per-step velocity/pan/swing/pitch, piano roll targeting, notes for any
channel, pattern info and saving.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.piano_roll import (
    _clear_request_file,
    _get_response_file,
    _write_request,
)
from fl_studio_mcp.utils.connection import get_connection
from fl_studio_mcp.utils.fl_trigger import trigger_fl_studio

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _send(action: str, params: dict | None = None) -> dict:
    return get_connection().send_command(action, params or {})


def register_extra_tools(mcp: FastMCP) -> None:
    """Register extra tools with the MCP server."""

    @mcp.tool()
    def fl_get_tempo() -> dict:
        """Get the project tempo in BPM."""
        return _send("transport.getTempo")

    @mcp.tool()
    def fl_set_tempo(bpm: float) -> dict:
        """Set the project tempo in BPM (e.g. 80)."""
        return _send("transport.setTempo", {"bpm": bpm})

    @mcp.tool()
    def fl_save_project() -> dict:
        """Save the current project (same as File > Save)."""
        return _send("project.save")

    @mcp.tool()
    def fl_get_pattern_info() -> dict:
        """Get current pattern number, name, length in steps and pattern count."""
        return _send("patterns.info")

    @mcp.tool()
    def fl_set_steps(channel: int, steps: list[dict], length: int | None = None,
                     clear: bool = True) -> dict:
        """Write step-sequencer steps with per-step parameters.

        Clears the first `length` steps first (default: up to the last step,
        at least 64) so no stacked duplicate notes remain.

        Args:
            channel: Channel index (global).
            steps: List of {"pos": step index (16 per bar),
                "velocity": 0-127 (default 100), "pan": 0-127 (64 = center),
                "shift": delay inside the step in ticks (swing; 0 .. PPQ/4-1),
                "pitch": MIDI note (default 60)}.
            length: Number of steps to clear before writing.
            clear: Set False to add on top of existing steps.
        """
        params: dict = {"channel": channel, "steps": steps, "clear": clear}
        if length:
            params["length"] = length
        return _send("channels.setSteps", params)

    @mcp.tool()
    def fl_get_steps(channel: int, length: int = 64) -> dict:
        """Read active steps of a channel with velocity and shift."""
        return _send("channels.getSteps", {"channel": channel, "length": length})

    @mcp.tool()
    def fl_clear_steps(channel: int, length: int = 64) -> dict:
        """Clear all steps of a channel (repeats until none remain)."""
        return _send("channels.clearSteps", {"channel": channel, "length": length})

    @mcp.tool()
    def fl_focus_piano_roll(channel: int) -> dict:
        """Open the piano roll for a channel and give it keyboard focus."""
        return _send("ui.focusPianoRoll", {"channel": channel})

    @mcp.tool()
    def fl_write_notes(channel: int, notes: list[dict], replace: bool = True) -> dict:
        """Write piano-roll notes into any channel in one call.

        Focuses the piano roll on `channel`, queues the notes for the
        ComposeWithLLM script and triggers it. Times and durations are in
        quarter notes (beat = 1.0, bar = 4.0); velocity 0.0-1.0.

        Args:
            channel: Channel index (global).
            notes: List of {"midi", "time", "duration", "velocity"}.
            replace: Clear the channel's notes first (default True).
        """
        focus = _send("ui.focusPianoRoll", {"channel": channel})
        time.sleep(0.4)
        requests: list[dict] = [{"action": "clear"}] if replace else []
        for n in notes:
            n.setdefault("time", 0)
            n.setdefault("velocity", 0.8)
        requests.append({"action": "add_notes", "notes": notes})
        resp_file = _get_response_file()
        if resp_file.exists():
            resp_file.unlink()
        _clear_request_file()  # leftover queue would be appended -> duplicate notes
        _write_request(requests)
        triggered = trigger_fl_studio()
        result: dict = {"focus": focus, "triggered": triggered}
        for _ in range(20):
            if resp_file.exists():
                try:
                    result["response"] = json.loads(resp_file.read_text())
                    break
                except json.JSONDecodeError:
                    pass
            time.sleep(0.25)
        else:
            result["response"] = None
        return result
