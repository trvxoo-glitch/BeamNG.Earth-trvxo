"""The game's own log (beamng.log) followed by the server: the errors and warnings that concern the mod but that the
mod cannot forward itself, into server.log under earthd.gamelog. A runtime error in one of the mod's extensions is
reported by the game's extension manager under the game's tag, with a Lua traceback naming beamngEarth; an engine
error about one of the mod's shapes or materials names its path. modlog.lua forwards the mod's own log() lines, so
lines whose tag is the mod's are left out here (no doubles).

beamng.log lines: '  12.34567|E|GELua.extensions|text'. A traceback's lines follow its error line. Pure but for
Follower's file reads.
"""
import os
import re

LINE = re.compile(r"^\s*(\d+\.\d+)\|([IWEDA])\|([^|]*)\|(.*)$")
MARK = "beamngearth"
TRACE_LINES = 12            # traceback lines kept after a forwarded error
READ_MAX = 1 << 20          # bytes read per poll at most (a game in an error loop)


def pick(lines, state=None):
    """The lines worth forwarding: [(level, tag, text)] for W/E lines that mention the mod in their text (not their
    tag), and the traceback lines after them. state: {'tb': n} kept between calls (a traceback cut by a read)."""
    state = state if state is not None else {}
    out = []
    for raw in lines:
        m = LINE.match(raw)
        if not m:
            if state.get("tb", 0) > 0 and raw.strip():
                state["tb"] -= 1
                out.append(("", "", raw.rstrip()))
            continue
        state["tb"] = 0
        _t, level, tag, text = m.groups()
        if level not in ("W", "E"):
            continue
        if MARK in tag.lower():
            continue            # (the mod's own line: modlog.lua sent it)
        if MARK not in text.lower() and not (level == "E" and "stack traceback" in text.lower()):
            continue
        out.append((level, tag.strip(), text.strip()))
        if level == "E":
            state["tb"] = TRACE_LINES
    return out


class Follower:
    """New lines of a file that the game writes anew at each start (a shorter file: read from its start)."""

    def __init__(self, path, from_end=True):
        self.path, self.pos, self.state, self.buf = path, None if from_end else 0, {}, b""

    def poll(self):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if self.pos is None:
            self.pos = size
            return []
        if size < self.pos:
            self.pos, self.buf = 0, b""
        if size == self.pos:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(READ_MAX)
        except OSError:
            return []
        self.pos += len(data)
        data = self.buf + data
        cut = data.rfind(b"\n")
        if cut < 0:
            self.buf = data
            return []
        self.buf = data[cut + 1:]
        lines = data[:cut].decode("utf-8", "replace").splitlines()
        return pick(lines, self.state)
