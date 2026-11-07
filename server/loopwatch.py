"""The server's event loop over the last minute, sampled by earthd's lag-watch thread, to say what the server was
doing during one of the game's hitches: a game hitch whose time went to the network part waits on the server, and
the server's loop busy in one call at that moment is its cause (the 8 s stall of 2026-11-07's run was logged without
the hitches it made). Pure: the caller adds samples and asks for a window.
"""
import collections
import threading

KEEP = 900                  # samples kept (90 s at earthd's 0.1 s tick)
HITCH_MS = 500              # game hitches this long or longer get the server's side logged


class Ring:
    def __init__(self, keep=KEEP):
        self.q = collections.deque(maxlen=keep)
        self.lock = threading.Lock()

    def add(self, t, busy, stack=""):
        """One sample: wall time t (time.time), whether the loop was late (busy), and its stack when busy."""
        with self.lock:
            self.q.append((t, bool(busy), stack if busy else ""))

    def window(self, t0, t1):
        with self.lock:
            return [s for s in self.q if t0 <= s[0] <= t1]

    def summary(self, t0, t1, top=2):
        """'event loop busy 80 % (6 of 8 samples): 5x earthd.py:f:12 < ...' or None (no samples in the window)."""
        got = self.window(t0, t1)
        if not got:
            return None
        busy = [s for s in got if s[1]]
        if not busy:
            return "event loop idle (%d samples): the hitch was not the server's" % len(got)
        c = collections.Counter(" < ".join(s[2].split(" < ")[:4]) for s in busy)
        where = "; ".join("%dx %s" % (n, k) for k, n in c.most_common(top))
        return "event loop busy %.0f %% (%d of %d samples): %s" % (100.0 * len(busy) / len(got), len(busy), len(got),
                                                                 where)


RING = Ring()
