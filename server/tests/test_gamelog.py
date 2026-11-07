"""gamelog.py, loopwatch.py and their use in earthd: the game's errors about the mod from beamng.log, the server's
event loop during a game hitch, the game's late lines; the launcher's game end reason."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import gamelog  # noqa: E402
import loopwatch  # noqa: E402

LOG = [
    "  1.00000|I|GELua.main|started",
    "  2.00000|E|GELua.extensions|beamngEarth_trees: attempt to index a nil value",
    "stack traceback:",
    "\tlua/ge/extensions/beamngEarth/trees.lua:120: in function 'update'",
    "  3.00000|W|GELua.beamngEarth_core.beamngEarth|the mod's own line (modlog sent it)",
    "  4.00000|E|libbeamng.TSShapeConstructor|failed to load /temp/beamngEarth/x/slot_1.cdae",
    "  5.00000|E|GELua.otherMod|not ours",
    "  6.00000|W|Engine|no mention",
]


class PickTest(unittest.TestCase):
    def test_pick(self):
        got = gamelog.pick(LOG)
        self.assertEqual(got[0], ("E", "GELua.extensions", "beamngEarth_trees: attempt to index a nil value"))
        self.assertEqual(got[1][0], "")
        self.assertIn("trees.lua:120", got[2][2])
        self.assertEqual(got[3][2], "failed to load /temp/beamngEarth/x/slot_1.cdae")
        self.assertEqual(len(got), 4, got)

    def test_traceback_without_the_name_on_its_error_line(self):
        got = gamelog.pick(["  1.0|E|GELua.x|stack traceback: something", "\tbeamngEarth/a.lua:1"])
        self.assertEqual(len(got), 2)


class FollowerTest(unittest.TestCase):
    def test_new_lines_only_and_a_new_game(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "beamng.log")
            with open(p, "w") as f:
                f.write("\n".join(LOG) + "\n")
            fo = gamelog.Follower(p)
            self.assertEqual(fo.poll(), [], "what was there before: not read")
            with open(p, "a") as f:
                f.write("  9.0|E|GELua.x|beamngEarth broke\n  9.1|E|GELua.x|beamngEarth half")
            self.assertEqual(fo.poll(), [("E", "GELua.x", "beamngEarth broke")], "a line not ended yet waits")
            with open(p, "w") as f:
                f.write("  0.1|W|GELua.x|beamngEarth after a restart\n")
            self.assertEqual(fo.poll(), [("W", "GELua.x", "beamngEarth after a restart")])
            fo2 = gamelog.Follower(os.path.join(d, "missing"))
            self.assertEqual(fo2.poll(), [])


class LoopTest(unittest.TestCase):
    def test_summary(self):
        r = loopwatch.Ring()
        for i in range(10):
            r.add(100 + i * 0.1, i < 8, "earthd.py:warm:12 < imagery.py:get:3" if i < 8 else "")
        got = r.summary(100, 101)
        self.assertIn("busy 80 %", got)
        self.assertIn("8x earthd.py:warm:12", got)
        self.assertIsNone(r.summary(200, 201))
        r2 = loopwatch.Ring()
        r2.add(1, False)
        self.assertIn("idle", r2.summary(0, 2))


class EarthdTest(unittest.TestCase):
    def test_late_and_hitch_side(self):
        import earthd
        self.assertEqual(earthd.game_lateness({"t": 100}, now=101), "")
        self.assertIn("sent 9 s before", earthd.game_lateness({"t": 100}, now=109))
        self.assertEqual(earthd.game_lateness({}, now=1), "")
        r = loopwatch.Ring()
        for i in range(30):
            r.add(98 + i * 0.1, True, "earthd.py:stall:1")
        got = earthd.hitch_server_side("perf hitch 1200 ms at x", {"t": 100}, ring=r)
        self.assertIn("server during the 1200 ms hitch: event loop busy 100 %", got)
        self.assertIsNone(earthd.hitch_server_side("perf hitch 120 ms", {"t": 100}, ring=r), "short hitches: none")
        self.assertIsNone(earthd.hitch_server_side("perf minute", {"t": 100}, ring=r))


class LauncherTest(unittest.TestCase):
    def test_crash_dumps_and_reason(self):
        import launcher
        with tempfile.TemporaryDirectory() as d:
            user = os.path.join(d, "BeamNG.drive", "current")
            os.makedirs(os.path.join(user, "temp"))
            old = os.path.join(user, "old.dmp")
            open(old, "w").close()
            os.utime(old, (1, 1))
            t0 = time.time() - 1
            new = os.path.join(d, "BeamNG.drive", "BeamNG.drive_crash.dmp")
            open(new, "w").close()
            open(os.path.join(user, "temp", "skip.dmp"), "w").close()
            self.assertEqual(launcher.game_crash_files(user, t0), [new])
            why = launcher.game_end_reason(user, t0)
            self.assertIn("crash dump: " + new, why)

            class P:
                def poll(self):
                    return -1073741819
            self.assertIn("0xC0000005", launcher.game_end_reason(user, time.time() + 9, P()))
            self.assertEqual(launcher.game_end_reason(user, time.time() + 9), "the game closed")


if __name__ == "__main__":
    unittest.main()
