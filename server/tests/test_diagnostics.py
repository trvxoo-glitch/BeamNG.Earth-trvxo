"""The diagnostics around the log: health lines, settings changes, the block pipeline line, the bug report bundle,
Perfetto timelines, performance baselines, the launcher's minidump switch."""
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import health  # noqa: E402
import logtools  # noqa: E402
import loopwatch  # noqa: E402
import perfbase  # noqa: E402
import tracefmt  # noqa: E402

LOG = """2026-11-07 14:00:00,000 INFO earthd: session: BeamNG.Earth 1.8.2-beta
2026-11-07 14:00:10,000 INFO earthd: terrain_ready (1, 0, 0) after 2000 ms
2026-11-07 14:00:12,000 INFO earthd: block (1, 0, 0) b1: built 2.0 s after its request, committed 1.5 s after it was sent (swap 11 ms)
2026-11-07 14:00:20,000 INFO earthd: game: perf hitch 1200 ms at -34.97,138.67 | engine: Ticking 1100
2026-11-07 14:00:30,000 WARNING earthd: event loop stalled > 2.0 s (every game message waits) in x
2026-11-07 14:00:33,000 WARNING earthd: event loop: the stall above lasted 3.0 s
2026-11-07 14:01:00,000 INFO earthd.health: health: server 812 MB; pools blocks 1/2; workers 2 using 1.2 GB (largest 700 MB)
2026-11-07 14:01:00,500 INFO earthd: game: perf minute 50 fps, 1% low 20, worst 1200 ms, 3 hitches (worst: Ticking) at x
2026-11-07 14:02:00,000 INFO earthd: game: perf minute 60 fps, 1% low 30, worst 300 ms, 1 hitches at x
""".splitlines()


class FakePool:
    name, inflight, workers, respawns, _low = "road tile", 5, 2, 1, ()

    def pids(self):
        return [os.getpid()]


class HealthTest(unittest.TestCase):
    def test_line(self):
        r = loopwatch.Ring()
        now = time.time()
        for i in range(10):
            r.add(now - 5 + i * 0.1, i < 3)
        text = health.line([FakePool()], tempfile.gettempdir(), r, connections=1, extra=("fallbacks 3",), now=now)
        self.assertRegex(text, r"^server [\d.]+ [MG]B; pools road tile 2/2 \+3 queued; workers 1 using [\d.]+ [MG]B")  # (GB once the test process is big)
        self.assertIn("restarts road tile 1", text)
        self.assertIn("event loop busy 30 %", text)
        self.assertIn("GB free (cache)", text)
        self.assertIn("games 1", text)
        self.assertTrue(text.endswith("fallbacks 3"))
        self.assertIsNone(health.rss_mb(2 ** 30), "a process that is not there")


class EarthdLinesTest(unittest.TestCase):
    def test_settings_and_pipeline(self):
        import earthd
        seen = {}
        self.assertEqual(earthd.settings_change(seen, {"type": "gas_stations", "on": True}), "gas_stations: on True")
        self.assertIsNone(earthd.settings_change(seen, {"type": "gas_stations", "on": True}))
        self.assertEqual(earthd.settings_change(seen, {"type": "gas_stations", "on": False}),
                         "gas_stations: on False (was True)")
        self.assertEqual(earthd.settings_change(seen, {"type": "cache", "clear": True}), "cache: cleared by the player")
        sent = {7: ((1, 512, 1024), 100.0, 2300.0)}
        line = earthd.block_pipeline(sent, {"bid": 7, "ms": 11.4, "method": "swap", "ok": True}, now=100.8)
        self.assertEqual(line, "block (1, 512, 1024) b7: built 2.3 s after its request, committed 0.8 s after it was "
                               "sent (swap 11 ms)")
        self.assertIsNone(earthd.block_pipeline(sent, {"bid": 7}), "matched once")
        self.assertIsNone(earthd.block_pipeline({}, {"bid": "x"}))
        sent = {8: ((1, 0, 0), 0.0, None)}
        self.assertIn("(it waits for the car)", earthd.block_pipeline(sent, {"bid": 8, "ok": False}, now=30))


class BundleTest(unittest.TestCase):
    def test_collect(self):
        with tempfile.TemporaryDirectory() as d:
            logs = os.path.join(d, "logs")
            user = os.path.join(d, "user")
            os.makedirs(os.path.join(logs, "dumps"))
            os.makedirs(os.path.join(user, "temp", "beamngEarth", "s1"))
            os.makedirs(os.path.join(user, "beamngEarth", "hitches"))
            with open(os.path.join(logs, "server.log"), "w") as f:
                f.write("\n".join(LOG) + "\n")
            with open(os.path.join(logs, "server.log.1"), "w") as f:
                f.write("old\n")
            with open(os.path.join(logs, "crash-blockworker-9.txt"), "w") as f:
                f.write("process blockworker pid 9\n")
            with open(os.path.join(logs, "dumps", "x.dmp"), "wb") as f:
                f.write(b"MDMP\x00\x01")
            with open(os.path.join(user, "beamng.log"), "w") as f:
                f.write("  1.0|E|x|token=abc secret\n")
            with open(os.path.join(user, "temp", "beamngEarth", "s1", "slot_1.cdae"), "w") as f:
                f.write("x" * 10)
            with open(os.path.join(user, "beamngEarth", "hitches", "hitch_1.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(d, "app.json"), "w") as f:
                json.dump({"cacheDir": "x"}, f)
            out = os.path.join(d, "b.zip")
            names = logtools.collect(out, logs, user, d)
            for n in ("logs/server.log", "logs/server.log.1", "logs/crash-blockworker-9.txt", "logs/dumps/x.dmp",
                      "game/beamng.log", "game/hitches/hitch_1.json", "game/session-files.txt", "app.json", "SUMMARY.txt"):
                self.assertIn(n, names)
            with zipfile.ZipFile(out) as z:
                self.assertEqual(z.read("logs/dumps/x.dmp"), b"MDMP\x00\x01", "binary as it is")
                self.assertNotIn("abc", z.read("game/beamng.log").decode())
                self.assertIn("s1/slot_1.cdae", z.read("game/session-files.txt").decode())
                self.assertIn("session: BeamNG.Earth", z.read("SUMMARY.txt").decode())


class TraceTest(unittest.TestCase):
    def test_server_log(self):
        ev = tracefmt.from_server_log(LOG)
        names = [e["name"] for e in ev if e["ph"] != "M"]
        self.assertIn("game hitch 1200 ms", names)
        self.assertIn("event loop stall", names)
        self.assertIn("build (1, 0, 0)", names)
        self.assertIn("wait for commit (1, 0, 0)", names)
        mem = [e for e in ev if e["ph"] == "C"]
        self.assertEqual(mem[0]["args"], {"server": 812.0, "workers": 1200.0})
        hitch = [e for e in ev if e["name"] == "game hitch 1200 ms"][0]
        self.assertEqual(hitch["dur"], 1200 * 1000)
        self.assertEqual(hitch["ts"] + hitch["dur"], 20 * 1e6, "ending at its line")
        json.dumps(tracefmt.trace(ev))

    def test_capture_and_snapshot(self):
        cap = {"kind": "beamngEarth hitch capture", "hitchMs": 1500, "hitchAt": 10.0,
               "frames": [{"t": 9.0, "ms": 16}, {"t": 10.0, "ms": 1500, "mod": {"roads": 40, "sea": 2}}],
               "hitch": {"engine": {"Ticking": 1400}, "gpu": 4}, "where": {"doing": "far field loading"}}
        ev = tracefmt.from_hitch_capture(cap)
        slow = [e for e in ev if e.get("name") == "frame: roads"][0]
        self.assertEqual((slow["ts"], slow["dur"]), (1e6 - 1500e3, 1500e3))
        inst = [e for e in ev if e["ph"] == "i"][0]
        self.assertEqual(inst["args"]["engine Ticking"], 1400)
        snap = {"server": {"recent": [{"pool": "blocks", "name": "build", "startOffsetMs": 100, "runMs": 50,
                                       "queueMs": 20, "steps": [{"name": "grading", "totalMs": 30}]}],
                           "active": [{"pool": "roads", "name": "tile"}]}}
        ev = tracefmt.from_profiler_snapshot(snap)
        job = [e for e in ev if e.get("name") == "build"][0]
        self.assertEqual((job["ts"], job["dur"], job["args"]["step grading"]), (100000.0, 50000.0, 30.0))
        self.assertTrue(any(e.get("name") == "queued" and e["ts"] == 80000.0 for e in ev))
        self.assertEqual(tracefmt.from_hitch_capture({}), [])


class BaselineTest(unittest.TestCase):
    def test_summary_and_compare(self):
        s = perfbase.summarize(LOG)
        self.assertEqual((s["fps"], s["low1"], s["worst_hitch_ms"], s["block_p50_ms"], s["server_mb_max"]),
                         (55.0, 25.0, 1200, 2000.0, 812.0))
        self.assertAlmostEqual(s["hitches_per_min"], 0.5, places=1)
        worse_run = dict(s, fps=40.0, block_p95_ms=s["block_p95_ms"] + 5000)
        lines, worse = perfbase.compare(s, worse_run)
        self.assertEqual(sorted(worse), ["block_p95_ms", "fps"])
        self.assertTrue(any("average fps: 55 -> 40" in x and "WORSE" in x for x in lines))
        self.assertEqual(perfbase.compare(s, s)[1], [])
        self.assertEqual(perfbase.summarize([]), {"records": 0})


class MinidumpSwitchTest(unittest.TestCase):
    def test_registry_values(self):
        import launcher
        got = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class WR:
            HKEY_CURRENT_USER, KEY_SET_VALUE, REG_EXPAND_SZ, REG_DWORD = 1, 2, 3, 4

            def CreateKeyEx(self, root, path, res, access):
                got["path"] = path
                return Key()

            def SetValueEx(self, k, name, res, typ, value):
                got[name] = value
        with tempfile.TemporaryDirectory() as d:
            was = launcher.IS_WIN
            launcher.IS_WIN = True
            try:
                folder = launcher.crash_dumps_on(d, "BeamNG.Earth.exe", winreg=WR())
                self.assertEqual(folder, os.path.join(d, "dumps"))
                self.assertTrue(got["path"].endswith("LocalDumps\\BeamNG.Earth.exe"))
                self.assertEqual((got["DumpFolder"], got["DumpCount"], got["DumpType"]), (folder, 3, 1))
                os.environ["BNGE_CRASH_DUMPS"] = "0"
                self.assertIsNone(launcher.crash_dumps_on(d, "x.exe", winreg=WR()))
            finally:
                launcher.IS_WIN = was
                os.environ.pop("BNGE_CRASH_DUMPS", None)
            launcher.IS_WIN = False
            try:
                self.assertIsNone(launcher.crash_dumps_on(d), "not Windows: off")
            finally:
                launcher.IS_WIN = was
            self.assertIsNone(launcher.crash_dumps_on(d), "from source (no exe named): off, even on Windows")
            open(os.path.join(d, "dumps", "a.dmp"), "w").close()
            self.assertEqual(launcher.new_dumps(d, time.time() - 5), [os.path.join(d, "dumps", "a.dmp")])
            self.assertEqual(launcher.new_dumps(d, time.time() + 5), [])


if __name__ == "__main__":
    unittest.main()
