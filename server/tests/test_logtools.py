"""logtools.py: the log digest, the window filters and the scrubbed bundle; the launcher's exit record."""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import logtools  # noqa: E402

LOG = """2026-11-06 18:00:00,100 INFO earthd: session: BeamNG.Earth 1.8.1-beta (packaged app), Python 3.12
2026-11-06 18:00:01,000 INFO earthd: game hello: mod 0.4.0, protocol 4, format 4
2026-11-06 18:00:05,000 WARNING earthd.dem: tile 12/345 slow: 4.5 s
2026-11-06 18:00:06,000 WARNING earthd.dem: tile 12/399 slow: 9.1 s
2026-11-06 18:00:07,000 ERROR earthd.roads: boom
Traceback (most recent call last):
  File "x.py", line 1, in f
ValueError: bad
2026-11-06 18:00:08,000 INFO earthd: game: perf hitch 4808 ms at -34.97012,138.66001 net 4795
2026-11-06 18:00:09,000 INFO earthd: game: wheels: ground 10 -> 20 at -34.99539,138.70033, 60 mph
2026-11-06 18:00:30,000 INFO launcher: server process exited with code 0 (normal)
"""


class DigestTest(unittest.TestCase):
    def test_summary(self):
        out = "\n".join(logtools.digest(LOG.splitlines()))
        self.assertIn("ran 2026-11-06 18:00:00 .. 2026-11-06 18:00:30 (30 s)", out)
        self.assertIn("end: server process exited with code 0 (normal)", out)
        self.assertIn("session: BeamNG.Earth", out)
        self.assertIn("2x WARNING earthd.dem", out, "the same warning with other numbers counts together")
        self.assertIn("tracebacks: 1", out)
        self.assertIn("ValueError: bad", out)
        self.assertIn("hitch 4808 ms", out)
        self.assertIn("ground: 1 notable", out)

    def test_no_exit_record_is_said(self):
        out = "\n".join(logtools.digest(LOG.splitlines()[:-1]))
        self.assertIn("no exit record", out)

    def test_empty(self):
        self.assertEqual(logtools.digest(["junk"]), ["no log records found"])


class WindowTest(unittest.TestCase):
    def test_filters(self):
        lines = LOG.splitlines()
        near = logtools.window(lines, around="18:00:06", secs=2)
        self.assertEqual(len([x for x in near if not x.startswith("    ")]), 4, "04..08 s")
        self.assertTrue(any("ValueError" in x for x in near), "a record's traceback comes with it")
        self.assertEqual(len(logtools.window(lines, area="dem")), 2)
        self.assertEqual(len(logtools.window(lines, area="earthd.dem")), 2)
        self.assertTrue(logtools.window(lines, level="error")[0].endswith("boom"))
        self.assertEqual(len(logtools.window(lines, grep="HITCH")), 1)
        got = logtools.window(lines, near=(-34.9954, 138.7003), radius_m=100)
        self.assertEqual(len(got), 1)
        self.assertIn("wheels", got[0])
        self.assertIn("bad time", logtools.window(lines, around="soon")[0])


class BundleTest(unittest.TestCase):
    def test_scrubbed(self):
        text = "opened C:\\Users\\Jack\\AppData\\x api_key=abc123 and /home/jack/.cache/y token: zzz"
        s = logtools.scrub(text, user="Jack", home="C:\\Users\\Jack")
        self.assertNotIn("Jack", s)
        self.assertNotIn("abc123", s)
        self.assertNotIn("zzz", s)
        self.assertIn("~", s)

    def test_zip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "server.log")
            with open(p, "w") as f:
                f.write("x" * 100 + "\nsecret=hunter2\n")
            z = os.path.join(d, "b.zip")
            names = logtools.bundle(z, [(p, "logs/server.log"), (os.path.join(d, "missing"), "logs/m")],
                                    {"SUMMARY.txt": "ok"}, user="nobody", home=d, max_bytes=40)
            self.assertEqual(names, ["logs/server.log", "SUMMARY.txt"])
            with zipfile.ZipFile(z) as zf:
                t = zf.read("logs/server.log").decode()
            self.assertTrue(t.startswith("[cut:"))
            self.assertNotIn("hunter2", t)


class ExitLineTest(unittest.TestCase):
    def test_launcher_exit_line(self):
        import launcher
        self.assertIn("(normal)", launcher.exit_line(0))
        self.assertIn("killed by signal 9", launcher.exit_line(-9))
        self.assertIn("0xC0000005", launcher.exit_line(0xC0000005))
        self.assertIn("(failed)", launcher.exit_line(1))
        self.assertTrue(logtools.LINE.match(launcher.exit_line(0)), "in the server log's own format")


if __name__ == "__main__":
    unittest.main()
