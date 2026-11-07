"""gradelog.py: the graded roads' rail crossings and humps in the log, with coordinates (the Adelaide rail hump's
log had nothing to find it by)."""
import logging
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gradelog  # noqa: E402
from test_roads import FRAME, features  # noqa: E402


class P:
    def __init__(self, xy, z):
        self.xy, self.z = np.asarray(xy, np.float64), np.asarray(z, np.float64)


def to_ll(e, n):
    la, lo, _ = FRAME.enu_to_geodetic(np.asarray(e), np.asarray(n), np.zeros(len(e)))
    return la, lo


class ProfileTest(unittest.TestCase):
    def test_resample_even_steps(self):
        s, xy, z = gradelog.resample([(0, 0), (10, 0), (10, 10)], [0, 1, 2], 2.0)
        self.assertAlmostEqual(s[-1], 20.0)
        np.testing.assert_allclose(np.diff(s), 2.0)
        self.assertAlmostEqual(z[5], 1.0)
        np.testing.assert_allclose(xy[-1], (10, 10))

    def test_flat_and_steady_grade_have_no_hump(self):
        x = np.linspace(0, 200, 101)
        for z in (np.zeros(101), 0.05 * x):
            st = gradelog.profile_stats(np.c_[x, 0 * x], z)
            self.assertLess(abs(st["hump"]), 1e-9)
        self.assertAlmostEqual(gradelog.profile_stats(np.c_[x, 0 * x], 0.05 * x)["grade"], 0.05)

    def test_hump_found_where_it_is(self):
        x = np.linspace(0, 200, 101)
        z = 0.6 * np.exp(-((x - 120.0) / 3.0) ** 2)
        st = gradelog.profile_stats(np.c_[x, 0 * x], z)
        self.assertGreater(st["hump"], 0.5)
        self.assertAlmostEqual(st["at"][0], 120.0, delta=2.0)
        st = gradelog.profile_stats(np.c_[x, 0 * x], -z)
        self.assertLess(st["hump"], -0.5, "a dip is negative")

    def test_short_profiles(self):
        self.assertIsNone(gradelog.profile_stats([(0, 0)], [0]))
        st = gradelog.profile_stats([(0, 0), (3, 0)], [0, 0.1])
        self.assertEqual(st["hump"], 0.0)


class CrossingTest(unittest.TestCase):
    def test_crossing_point_and_heights(self):
        road = ("r1", np.array([(-50.0, 0.0), (50.0, 0.0)]), np.array([10.0, 12.0]))
        rail = ("rail:t1", np.array([(0.0, -50.0), (0.0, 50.0)]), np.array([11.5, 11.5]))
        (fid, rfid, x, y, zr, zt, i, t, rxy), = gradelog.crossings([road], [rail])
        self.assertEqual((fid, rfid), ("r1", "rail:t1"))
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertAlmostEqual(zr, 11.0)
        self.assertAlmostEqual(zt, 11.5)
        self.assertEqual(i, 0)
        self.assertAlmostEqual(t, 0.5)
        self.assertEqual(len(rxy), 2)

    def test_parallel_and_apart_do_not_cross(self):
        road = ("r1", np.array([(-50.0, 0.0), (50.0, 0.0)]), np.zeros(2))
        self.assertEqual(gradelog.crossings([road], [("rail:a", np.array([(-50.0, 5.0), (50.0, 5.0)]), np.zeros(2))]),
                         [])
        self.assertEqual(gradelog.crossings([road], [("rail:b", np.array([(80.0, -5.0), (80.0, 5.0)]), np.zeros(2))]),
                         [])
        self.assertEqual(gradelog.crossings([road], []), [])

    def test_across_clipped_at_ends(self):
        xy = np.array([(0.0, 0.0), (100.0, 0.0)])
        s, pts = gradelog.across(xy, 0, 0.1)
        self.assertAlmostEqual(s[0], -10.0)
        self.assertAlmostEqual(s[-1], 20.0)
        self.assertAlmostEqual(pts[0, 0], 0.0)


class ReportTest(unittest.TestCase):
    def plans(self, rail_z):
        x = np.linspace(-100, 100, 101)
        return [("road1", P(np.c_[x, 0 * x], np.full(101, 10.0))),
                ("rail:track9", P([(0.0, -100.0), (0.0, 100.0)], [rail_z, rail_z])),
                ("road1:tun", P([(0, 5), (10, 5)], [0, 0]))]

    def test_step_at_a_crossing_is_named_with_coordinates(self):
        la, lo = to_ll(np.array([0.0]), np.array([0.0]))
        with self.assertLogs("earthd.grading", logging.INFO) as cm:
            got = gradelog.report_cell("14/1/2", self.plans(10.6), to_ll)
        self.assertEqual(got["crossings"], 1)
        self.assertEqual(got["steps"], 1)
        line, = [m for m in cm.output if "rail crossing" in m]
        self.assertIn("%.5f,%.5f" % (la[0], lo[0]), line)
        self.assertIn("road -0.60 m", line)
        self.assertIn("track9", line)
        self.assertIn("cell 14/1/2", line)

    def test_level_crossing_is_debug_only(self):
        lg = logging.getLogger("earthd.grading")
        with self.assertLogs("earthd.grading", logging.DEBUG) as cm:
            lg.setLevel(logging.DEBUG)
            try:
                got = gradelog.report_cell("k", self.plans(10.05), to_ll)
            finally:
                lg.setLevel(logging.NOTSET)
        self.assertEqual(got["steps"], 0)
        self.assertTrue(all(m.startswith("DEBUG") for m in cm.output if "rail crossing" in m))
        self.assertTrue(any("road road1" in m for m in cm.output), "--debug grading: one line per road")

    def test_graded_profile_across_the_track(self):
        def base(e, n):
            return np.full(len(e), 10.0)

        def graded(e, n, b):            # a 0.5 m hump over the track
            return b + 0.5 * np.exp(-(np.asarray(e) / 3.0) ** 2)
        with self.assertLogs("earthd.grading", logging.INFO) as cm:
            gradelog.report_cell("k", self.plans(10.0), to_ll, base, graded)
        line, = [m for m in cm.output if "rail crossing" in m]
        self.assertIn("graded hump 0.", line)
        self.assertTrue(line.startswith("INFO"), "a graded hump makes it INFO even when the plans agree")

    def test_quiet_and_errors(self):
        lg = logging.getLogger("earthd.grading")
        with self.assertLogs("earthd.grading", logging.DEBUG) as cm:
            lg.setLevel(logging.DEBUG)
            try:
                gradelog.report_cell("k", self.plans(11.0), to_ll, quiet=True)
                self.assertIsNone(gradelog.report_cell("k", [("x", object()), ("y", None)], to_ll) and None)
                self.assertIsNone(gradelog.report_cell("k", 5, to_ll), "an error is caught")
            finally:
                lg.setLevel(logging.NOTSET)
        self.assertFalse([m for m in cm.output if m.startswith("INFO")], "quiet: the plan cache's cells at DEBUG")

    def test_a_road_in_pieces_sharing_one_id(self):
        """Two plans of one feature (a road cut at a cell's edge): the crossing's profile is read from its own piece
        (the field's IndexError: the longer piece's index into the shorter one's vertices)."""
        x = np.linspace(-100, 100, 101)
        short = P(np.c_[np.linspace(300, 320, 2), [0, 0]], [10, 10])
        long_ = P(np.c_[x, 0 * x], np.full(101, 10.0))
        plans = [("road1", long_), ("road1", short), ("rail:t", P([(60.0, -50.0), (60.0, 50.0)], [10.6, 10.6]))]
        with self.assertLogs("earthd.grading", logging.INFO) as cm:
            gradelog.report_cell("k", plans, to_ll, lambda e, n: np.full(len(e), 10.0), lambda e, n, b: b)
        line, = [m for m in cm.output if "rail crossing" in m]
        self.assertNotIn("unavailable", line)
        self.assertIn("graded", line)

    def test_humps_capped_per_cell(self):
        x = np.linspace(0, 200, 101)
        bump = 10 + 0.8 * np.exp(-((x - 100.0) / 3.0) ** 2)
        plans = [("r%d" % i, P(np.c_[x, 0 * x + 10 * i], bump)) for i in range(5)]
        with self.assertLogs("earthd.grading", logging.INFO) as cm:
            got = gradelog.report_cell("k", plans, to_ll)
        self.assertEqual(got["humps"], 5)
        self.assertEqual(len([m for m in cm.output if "has a 0." in m]), gradelog.HUMP_LINES)
        self.assertTrue(any("3 more roads" in m for m in cm.output))


class GraderIntegrationTest(unittest.TestCase):
    """A street crossing a railway on an embankment the street's own smoothing does not follow: the planner logs the
    crossing with its coordinates when it plans the cell."""

    def test_planner_logs_the_crossing(self):
        import grading
        import railways
        from test_grading import FakeMD
        railways._GROUND_MEMO.clear()
        f = features([[(-300.0, 0.0), (300.0, 0.0)], [(0.0, -300.0), (0.0, 300.0)]],
                     ["standard_gauge", "residential"], subtypes=["rail", "road"])

        def ground(la, lo):
            e, _n, _ = FRAME.geodetic_to_enu(np.asarray(la), np.asarray(lo), 0.0)
            return 10.0 + 1.5 * np.exp(-(np.asarray(_n) / 4.0) ** 2) + 0 * np.asarray(e)
        g = grading.Grader(FRAME, FakeMD(f), ground)
        la, lo, _ = FRAME.enu_to_geodetic(np.array([0.0, 5.0]), np.array([0.0, 0.0]), 0.0)
        with self.assertLogs("earthd.grading", logging.DEBUG) as cm:
            lg = logging.getLogger("earthd.grading")
            lg.setLevel(logging.DEBUG)
            try:
                g.apply(la, lo, ground(la, lo))
            finally:
                lg.setLevel(logging.NOTSET)
                railways._GROUND_MEMO.clear()
        lines = [m for m in cm.output if "rail crossing at" in m]
        self.assertTrue(lines, "\n".join(cm.output[-20:]))
        self.assertIn("graded", lines[0])


if __name__ == "__main__":
    unittest.main()
