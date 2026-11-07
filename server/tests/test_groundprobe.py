"""surface.paint_copy / paint_at and Connection.ground_probe: what a block's files say under the car where its tyres
changed ground (the paved road that drove as dirt), and live_busy with the loading line's network counts."""
import logging
import os
import sys
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import earthd  # noqa: E402
import heightmap  # noqa: E402
import surface  # noqa: E402

SIZE = 64
CX, CY = 1000.0, 2000.0


def block():
    """A 64² block (SIZE samples a side, 1 m apart) of grass with a 6 m asphalt road along x at y = CY."""
    idx = np.full((SIZE, SIZE), surface.GRASS, np.uint8)
    half = SIZE * heightmap.SQUARE / 2
    rows = [j for j in range(SIZE) if abs((j + 0.5) * heightmap.SQUARE - half) <= 3.0]
    idx[rows[0]:rows[-1] + 1, :] = surface.ASPHALT
    return idx


class PaintTest(unittest.TestCase):
    def test_copy_is_small_and_none_without_layers(self):
        self.assertIsNone(surface.paint_copy(None))
        g = surface.paint_copy(np.zeros((1024, 1024), np.uint8))
        self.assertEqual(g.shape, (1024 // surface.PAINT_STEP,) * 2)
        self.assertTrue(g.flags["C_CONTIGUOUS"])

    def test_paint_at_the_road_and_the_verge(self):
        old = surface.PAINT_STEP
        try:
            surface.PAINT_STEP = 1          # (the grid's step: the test block is small)
            g = surface.paint_copy(block())
            name, near, asphalt = surface.paint_at(g, SIZE, CX, CY, CX, CY)
            self.assertEqual(name, "bngEarthGroundAsphalt")
            self.assertGreater(asphalt, 0.5)        # (a 6 m road in a 9 m window)
            name, near, asphalt = surface.paint_at(g, SIZE, CX, CY, CX, CY + 20)
            self.assertEqual(name, "bngEarthGroundGrass")
            self.assertEqual(asphalt, 0.0)
            self.assertEqual(set(near), {"bngEarthGroundGrass"})
            self.assertIsNone(surface.paint_at(g, SIZE, CX, CY, CX + 500, CY), "off the block")
        finally:
            surface.PAINT_STEP = old

    def test_hole_is_none(self):
        g = np.full((16, 16), heightmap.TER_HOLE, np.uint8)
        self.assertIsNone(surface.paint_at(g, 64, 0.0, 0.0, 0.0, 0.0))


class ProbeTest(unittest.TestCase):
    def conn(self, info):
        c = earthd.Connection.__new__(earthd.Connection)
        c.s = types.SimpleNamespace(hm={(1, 5, 6): info} if info is not None else {},
                                    layout=types.SimpleNamespace(block=SIZE), surface=None, anchors={},
                                    road_widths=None)
        return c

    def probe(self, info, **over):
        msg = {"anchorId": 1, "cx": 5, "cy": 6, "x": 5.0, "y": 6.0, "from": "10", "to": "20"}
        msg.update(over)
        with self.assertLogs("earthd", logging.INFO) as cm:
            self.conn(info).ground_probe(msg)
        return cm.output[0]

    def test_files_say_grass(self):
        old = surface.PAINT_STEP
        try:
            surface.PAINT_STEP = 1
            idx = np.full((SIZE, SIZE), surface.GRASS, np.uint8)
            line = self.probe({"paint": surface.paint_copy(idx), "roadsComplete": False}, x=5.0, y=6.0)
        finally:
            surface.PAINT_STEP = old
        self.assertIn("ground 10 -> 20 at block 5,6", line)
        self.assertIn("files say Grass under the car", line)
        self.assertIn("0 % asphalt", line)
        self.assertIn("built before its map cells", line)

    def test_files_say_asphalt(self):
        old = surface.PAINT_STEP
        try:
            surface.PAINT_STEP = 1
            line = self.probe({"paint": surface.paint_copy(block())}, x=5.0, y=6.0)
        finally:
            surface.PAINT_STEP = old
        self.assertNotIn("files say Grass", line)
        self.assertNotIn("built before", line)

    def test_nearest_roads_named_where_the_files_hold_no_asphalt(self):
        from test_roads import FRAME, features
        f = features([[(-100.0, 3.0), (100.0, 3.0)]], ["secondary"])
        c = self.conn({"paint": surface.paint_copy(np.full((SIZE, SIZE), surface.GRASS, np.uint8))})
        c.s.surface = types.SimpleNamespace(roads=lambda la, lo: ([f], True))
        c.s.anchors = {1: types.SimpleNamespace(frame=FRAME)}
        c.loop = types.SimpleNamespace(run_in_executor=lambda ex, fn, *a: fn(*a))      # (inline)
        old = surface.PAINT_STEP
        try:
            surface.PAINT_STEP = 1
            c.s.hm[(1, 5, 6)]["paint"] = surface.paint_copy(np.full((SIZE, SIZE), surface.GRASS, np.uint8))
            with self.assertLogs("earthd", logging.INFO) as cm:
                c.ground_probe({"anchorId": 1, "cx": 5, "cy": 6, "x": 5.0, "y": 6.0, "from": "10", "to": "20"})
        finally:
            surface.PAINT_STEP = old
        text = "\n".join(cm.output)
        self.assertIn("files say Grass under the car", text)
        self.assertIn("the nearest mapped roads are secondary", text)
        self.assertIn("painted into the ground", text)

    def test_block_gone_and_bad_message(self):
        self.assertIn("no longer has", self.probe(None))
        self.assertIn("no longer has", self.probe({"paint": None}))
        c = self.conn(None)
        c.ground_probe({"anchorId": "x"})       # (nothing logged, nothing raised)


class RoadsNearTest(unittest.TestCase):
    def test_nearest_roads_and_whether_painted(self):
        from test_roads import LAT, LON, features
        f = features([[(-100.0, 0.0), (100.0, 0.0)], [(-100.0, 12.0), (100.0, 12.0)], [(-100.0, 80.0), (100.0, 80.0)]],
                     ["secondary", "footway", "residential"])
        got = surface.roads_near([f], LAT, LON)
        self.assertEqual([c for _d, _i, c, _t in got], ["secondary", "footway"], "within 25 m, nearest first")
        self.assertLess(got[0][0], 1.0)
        self.assertIn("painted into the ground", got[0][3])
        self.assertIn("not painted", got[1][3], "a footway has no road spec")
        self.assertEqual(surface.roads_near([f], LAT, LON, radius_m=5.0)[0][2], "secondary")
        self.assertEqual(surface.roads_near([None], LAT, LON), [])


class LiveBusyTest(unittest.TestCase):
    def test_network_counts_do_not_raise(self):
        s = types.SimpleNamespace(road_pool=None, far_bld_pool=None, far_pool=None, block_pools=[])
        c = types.SimpleNamespace(progress_sent=((1, 1), (3, 3), (0, 0), (10, 10), (0, 0.0, 0.0)))
        s.connections = [c]
        self.assertFalse(earthd.Server.live_busy(s))
        c.progress_sent = ((1, 1), (2, 3), (0, 0), (10, 10), (1, 5.0, 1.5))
        self.assertTrue(earthd.Server.live_busy(s))


if __name__ == "__main__":
    unittest.main()
