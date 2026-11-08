"""The drives' free space (diskguard.py): the cache gives room back before a drive fills, whatever its cap, and the
player is told once when a drive stays short (the 'disk_low' message, core.lua)."""
import collections
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cache as cachemod  # noqa: E402
import diskguard  # noqa: E402

GB = diskguard.GB
Usage = collections.namedtuple("Usage", "total used free")


class FakeDisk:
    """shutil.disk_usage over one fake volume: `free` goes up by what is deleted from the cache root."""

    def __init__(self, total, free, root=None):
        self.total, self.base_free, self.root = total, free, root

    def __call__(self, path):
        return Usage(self.total, self.total - self.free(), self.free())

    def free(self):
        return self.base_free - (self._bytes() if self.root else 0)

    def _bytes(self):
        n = 0
        for d, _, names in os.walk(self.root):
            for f in names:
                n += os.path.getsize(os.path.join(d, f))
        return n


class ReserveTest(unittest.TestCase):
    def test_share_with_floor_and_ceiling(self):
        self.assertEqual(diskguard.reserve_bytes(30 * GB, env={}), diskguard.RESERVE_MIN)     # 3 % = 0.9 GB: the floor
        self.assertEqual(diskguard.reserve_bytes(200 * GB, env={}), int(6 * GB))
        self.assertEqual(diskguard.reserve_bytes(4000 * GB, env={}), diskguard.RESERVE_MAX)  # 120 GB: the ceiling

    def test_env_sets_it(self):
        self.assertEqual(diskguard.reserve_bytes(500 * GB, env={diskguard.ENV: "1.5"}), int(1.5 * GB))
        self.assertEqual(diskguard.reserve_bytes(500 * GB, env={diskguard.ENV: "0"}), 0)
        with self.assertLogs("earthd.disk", "WARNING"):
            self.assertEqual(diskguard.reserve_bytes(500 * GB, env={diskguard.ENV: "lots"}), diskguard.RESERVE_MAX)
        with self.assertLogs("earthd.disk", "WARNING"):
            self.assertEqual(diskguard.reserve_bytes(500 * GB, env={diskguard.ENV: "-3"}), diskguard.RESERVE_MAX)


class CacheGuardTest(unittest.TestCase):
    """A real Cache under a temp folder on a fake volume: 10 files of 1 MB, a reserve of 5 MB."""
    MB = 1_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "cache")
        self.t = [time.time()]
        self.cache = cachemod.Cache(self.root, cap_gb=0, now=lambda: self.t[0], start=False)   # unlimited cap
        old = self.t[0] - 2 * cachemod.PROTECT_S        # (last used before this run: evictable)
        for i in range(10):
            p = os.path.join(self.root, "imagery", f"{i}.jpg")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"x" * self.MB)
            os.utime(p, (old + i, old + i))             # 0 is the least recently used
        self.cache.scan()
        self.extra = mock.patch.object(diskguard, "RESUME_EXTRA", self.MB)
        self.extra.start()
        self.addCleanup(self.extra.stop)

    def guard(self, free_now, reserve_mb=5):
        """A guard whose volume has free_now bytes free (the cache's 10 MB on it; deleting them frees them)."""
        disk = FakeDisk(1000 * self.MB, free_now + 10 * self.MB, root=os.path.join(self.root, "imagery"))
        env = {diskguard.ENV: str(reserve_mb * self.MB / GB)}
        return diskguard.DiskGuard({"cache": self.root}, self.cache, usage=disk, env=env), disk

    def left(self):
        return sorted(os.listdir(os.path.join(self.root, "imagery")))

    def test_room_given_back_oldest_first_whatever_the_cap(self):
        g, disk = self.guard(3 * self.MB)           # 3 MB free: 2 short of the 5 MB reserve, +1 MB extra
        self.assertEqual(g.check(), [])             # enough came back: nothing to tell
        self.assertEqual(self.left(), [f"{i}.jpg" for i in range(3, 10)])
        self.assertEqual(g.freed, 3 * self.MB)
        self.assertGreaterEqual(disk.free(), 5 * self.MB)
        self.assertEqual(g.check(), [])             # fine now: nothing more goes
        self.assertEqual(len(self.left()), 7)

    def test_auto_clear_off_only_warns(self):
        self.cache.auto_clear = False
        g, _ = self.guard(3 * self.MB)
        with self.assertLogs("earthd.disk", "WARNING"):
            reps = g.check()
        self.assertEqual(len(self.left()), 10)
        self.assertEqual(reps[0]["freedBytes"], 0)

    def test_plenty_free_touches_nothing(self):
        g, _ = self.guard(100 * self.MB)
        self.assertEqual(g.check(), [])
        self.assertEqual(len(self.left()), 10)
        self.assertEqual(g.freed, 0)

    def test_told_once_when_the_cache_cannot_help(self):
        g, _ = self.guard(1 * self.MB, reserve_mb=50)   # the whole cache (10 MB) is not enough
        with self.assertLogs("earthd.disk", "WARNING") as logs:
            reps = g.check()
        self.assertEqual(self.left(), [])           # all of it went first
        self.assertEqual(len(reps), 1)
        rep = reps[0]
        self.assertEqual(rep["labels"], ["cache"])
        self.assertEqual(rep["freedBytes"], 10 * self.MB)
        self.assertEqual(rep["reserveBytes"], 50 * self.MB)
        self.assertLess(rep["freeBytes"], rep["reserveBytes"])
        self.assertIn("nothing more it may delete", logs.output[0])
        self.assertEqual(g.check(), [])             # still short: not again
        self.assertEqual(len(g.reports()), 1)       # (a new connection is still told)

    def test_this_runs_files_are_kept(self):
        for i in range(10):                         # all used by this run within the hour
            self.cache.used(os.path.join(self.root, "imagery", f"{i}.jpg"))
        g, _ = self.guard(3 * self.MB)
        with self.assertLogs("earthd.disk", "WARNING"):
            reps = g.check()
        self.assertEqual(len(self.left()), 10)
        self.assertEqual(reps[0]["freedBytes"], 0)

    def test_not_ready_frees_nothing(self):
        c = cachemod.Cache(os.path.join(self.tmp.name, "other"), cap_gb=0, start=False)     # no walk yet
        self.assertEqual(c.free_space(5 * self.MB), 0)


class VolumesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = os.path.join(self.tmp.name, "a")
        self.b = os.path.join(self.tmp.name, "b")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def test_one_check_per_volume(self):
        calls = []

        def usage(p):
            calls.append(p)
            return Usage(100 * GB, 99 * GB, 1 * GB)
        g = diskguard.DiskGuard({"cache": self.a, "session": self.b}, None, usage=usage, env={})
        self.assertEqual([labels for _, labels, _ in g.volumes()], [["cache", "session"]])   # (one temp volume)
        with self.assertLogs("earthd.disk", "WARNING") as logs:
            reps = g.check()
        self.assertEqual(len(calls), 1)
        self.assertEqual(reps[0]["labels"], ["cache", "session"])
        self.assertIn("free some space", logs.output[0])

    def test_session_only_volume_is_reported_and_recovers(self):
        free = [1 * GB]
        g = diskguard.DiskGuard({"session": self.b}, None, usage=lambda p: Usage(100 * GB, 0, free[0]), env={})
        with self.assertLogs("earthd.disk", "WARNING"):
            self.assertEqual(len(g.check()), 1)
        free[0] = diskguard.RESERVE_MIN + 0.5 * GB          # over the reserve, under reserve + RESUME_EXTRA: still short
        self.assertEqual(g.check(), [])
        self.assertEqual(len(g.reports()), 1)
        free[0] = diskguard.RESERVE_MIN + 2 * GB            # back
        self.assertEqual(g.check(), [])
        self.assertEqual(g.reports(), [])
        free[0] = 1 * GB                                    # short again: told again
        with self.assertLogs("earthd.disk", "WARNING"):
            self.assertEqual(len(g.check()), 1)

    def test_unreadable_volume_is_skipped(self):
        def usage(p):
            raise FileNotFoundError(p)
        g = diskguard.DiskGuard({"session": self.b}, None, usage=usage, env={})
        self.assertEqual(g.check(), [])

    def test_a_failing_cache_does_not_stop_the_watch(self):
        class Broken:
            def free_space(self, n):
                raise RuntimeError("boom")
        g = diskguard.DiskGuard({"cache": self.a}, Broken(), usage=lambda p: Usage(100 * GB, 0, 1 * GB), env={})
        with self.assertLogs("earthd.disk", "WARNING") as logs:
            reps = g.check()
        self.assertEqual(len(reps), 1)
        self.assertTrue(any("could not free space" in line for line in logs.output))


class ServerMessageTest(unittest.TestCase):
    def test_disk_low_message_and_hello(self):
        sys.path.insert(0, os.path.join(HERE, "..", ".."))
        import earthd
        rep = {"path": "/x", "labels": ["cache", "session"], "freeBytes": 1, "reserveBytes": 2, "freedBytes": 3}
        self.assertEqual(earthd.disk_low_message(rep), {"type": "disk_low", "path": "/x", "cache": True,
                                                        "session": True, "freeBytes": 1, "reserveBytes": 2,
                                                        "freedBytes": 3})
        srv = mock.Mock(disk_guard=None)
        self.assertEqual(earthd.Server.disk_reports(srv), [])
        srv.disk_guard = mock.Mock(reports=lambda: [rep])
        self.assertEqual(earthd.Server.disk_reports(srv), [rep])


if __name__ == "__main__":
    unittest.main()
