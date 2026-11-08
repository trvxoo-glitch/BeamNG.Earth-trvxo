"""The player's velocity (posevel.py): the game's, unless the car's own motion says it is a physics glitch (a player's
log of 2026-11-06: "catch-up boost on (1051330 mph; ...)" with the car standing still)."""
import math
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import posevel  # noqa: E402
from posevel import VelocityFilter  # noqa: E402


class FilterTest(unittest.TestCase):
    def drive(self, f, poses):
        """poses: [(t, aid, x, y, vx, vy)] -> the filtered velocities."""
        return [f.update(*p) for p in poses]

    def test_plausible_speeds_are_the_games(self):
        f = VelocityFilter()
        out = self.drive(f, [(0.0, 1, 0.0, 0.0, 30.0, 0.0), (0.5, 1, 15.0, 0.0, 31.0, 0.0),
                             (1.0, 1, 30.5, 0.0, 32.0, 1.0)])
        self.assertEqual(out, [(30.0, 0.0), (31.0, 0.0), (32.0, 1.0)])
        self.assertEqual(f.count, 0)

    def test_a_fast_plane_is_believed(self):
        f = VelocityFilter()
        out = self.drive(f, [(0.0, 1, 0.0, 0.0, 300.0, 0.0), (0.5, 1, 150.0, 0.0, 300.0, 0.0)])
        self.assertEqual(out[-1], (300.0, 0.0))
        self.assertEqual(f.count, 0)

    def test_the_logged_glitch_standing_still(self):
        f = VelocityFilter()
        v = 1051330 / posevel.MPH                   # the player's log: 470 km/s
        with self.assertLogs("earthd.pose", "WARNING") as logs:
            out = self.drive(f, [(0.0, 1, 100.0, 50.0, 0.0, 0.0), (0.5, 1, 100.0, 50.0, v, 0.0),
                                 (1.0, 1, 100.0, 50.0, 0.0, v), (1.5, 1, 100.0, 50.0, 0.0, 0.0)])
        self.assertEqual(out, [(0.0, 0.0)] * 4)
        self.assertEqual(f.count, 2)
        self.assertEqual(len(logs.output), 1, "one line per run of glitched poses")
        self.assertIn("1051330 mph", logs.output[0])

    def test_a_spike_under_the_cap_uses_the_cars_motion(self):
        f = VelocityFilter()
        with self.assertLogs("earthd.pose", "WARNING"):
            out = self.drive(f, [(0.0, 1, 0.0, 0.0, 20.0, 0.0), (0.5, 1, 10.0, 0.0, 0.0, 600.0)])
        self.assertAlmostEqual(out[1][0], 20.0)
        self.assertAlmostEqual(out[1][1], 0.0)

    def test_accelerating_between_poses_is_believed(self):
        f = VelocityFilter()                        # 100 -> 160 m/s in half a second (a plane mod's boost)
        out = self.drive(f, [(0.0, 1, 0.0, 0.0, 100.0, 0.0), (0.5, 1, 65.0, 0.0, 160.0, 0.0)])
        self.assertEqual(out[1], (160.0, 0.0))

    def test_over_the_cap_is_never_believed(self):
        f = VelocityFilter()
        with self.assertLogs("earthd.pose", "WARNING"):
            out = f.update(0.0, 1, 0.0, 0.0, 5000.0, 0.0)      # (no motion known: standing)
        self.assertEqual(out, (0.0, 0.0))

    def test_no_motion_across_anchors_or_long_gaps(self):
        f = VelocityFilter()
        f.update(0.0, 1, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(f.update(0.5, 2, 9000.0, 0.0, 400.0, 0.0), (400.0, 0.0), "a rebase: another frame")
        f.update(10.0, 2, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(f.update(20.0, 2, 0.0, 0.0, 400.0, 0.0), (400.0, 0.0), "a paused game: nothing measured")

    def test_a_teleport_jump_measures_nothing(self):
        f = VelocityFilter()
        f.update(0.0, 1, 0.0, 0.0, 0.0, 0.0)
        # a 100 km/s jump is no motion: the game's 900 m/s stands (under the cap, nothing to say otherwise)
        self.assertEqual(f.update(0.5, 1, 50000.0, 0.0, 900.0, 0.0), (900.0, 0.0))
        self.assertEqual(f.count, 0)

    def test_a_teleport_with_a_calm_car_keeps_its_speed(self):
        f = VelocityFilter()
        f.update(0.0, 1, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(f.update(0.5, 1, 50000.0, 0.0, 2.0, 0.0), (2.0, 0.0))


class ConnectionTest(unittest.TestCase):
    """Connection.on_pose in an in-process server: a glitched pose moves nothing ahead of the car."""

    def test_glitched_pose_in_a_session(self):
        from test_server import ServerHarness, LAT, LON  # noqa: F401

        class H(ServerHarness):
            def runTest(self):
                pass
        h = H()
        h.setUp()
        try:
            h.hello()
            a = h.prepare_anchor(1, LAT, LON)
            aid = a["anchorId"]
            h.c.send({"type": "commit_anchor", "anchorId": aid})
            h.c.send({"type": "commit_terrain", "anchorId": aid, "cx": 0, "cy": 0})
            h.pose(aid, 0.0, 0.0)
            time.sleep(0.3)
            with self.assertLogs("earthd.pose", "WARNING"):
                h.pose(aid, 0.0, 0.0, vx=470000.0, vy=0.0)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and h.conn().vel_filter.count == 0:
                    time.sleep(0.02)
            conn = h.conn()
            self.assertEqual(conn.vel_filter.count, 1)
            self.assertEqual(conn.vel, (0.0, 0.0))
            self.assertEqual(h.server.speed, 0.0)
            self.assertFalse(conn.catchup)
            self.assertFalse(conn.predicted_chain)
            time.sleep(0.3)
            h.pose(aid, 10.0, 0.0, vx=25.0, vy=0.0)     # a real drive after it: the game's again
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and h.conn().vel != (25.0, 0.0):
                time.sleep(0.02)
            self.assertEqual(h.conn().vel, (25.0, 0.0))
            self.assertAlmostEqual(h.server.speed, 25.0)
        finally:
            h.tearDown()


if __name__ == "__main__":
    unittest.main()
