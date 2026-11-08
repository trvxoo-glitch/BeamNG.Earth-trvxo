"""The player's velocity as the server uses it: the game's, unless the car's own motion says it is impossible.

The mod sends the car's velocity in every pose (core.lua: veh:getVelocity(), ~2 Hz), and the server steers its
look-ahead by it: the next blocks predicted along it (predict_request / predict_chain), the road plans, imagery and
map data fetched 10-30 s ahead on the straight path (Server.warm_roads / warm_imagery / download_ahead), the fast
mode, and the catch-up boost that lends the server the game's cores. A physics glitch (a car exploding, clipping into
the ground, a respawn or reset) can report a velocity of hundreds of km/s for a pose or two: a player's log of
2026-11-06 has "catch-up boost on (1051330 mph; ...)" while the car stood still, so the boost took the game's cores,
and the road plans and photos were asked for places thousands of km away while the player waited for the ground
round them.

VelocityFilter.update keeps the game's velocity when it is plausible, else uses the car's own motion between the last
two poses of the same anchor (or none):
  - over MAX_MS (1000 m/s, about Mach 3, far above any car or plane mod): never believed;
  - over SPIKE_MIN_MS and more than SPIKE_FACTOR times the car's motion (+ SPIKE_SLACK_MS) since the last pose: a
    spike (a real car that fast also moves that fast; a paused game's car does not move, and then nothing does);
  - the motion is measured only between poses of one anchor POSE_DT_MIN..POSE_DT_MAX s apart (a rebase changes the
    frame, a teleport jumps: a jump faster than MAX_MS is no velocity either).
Each run of distrusted poses is logged once.
"""
import logging
import math

log = logging.getLogger("earthd.pose")

MAX_MS = 1000.0             # m/s: nothing the player drives or flies goes faster
SPIKE_MIN_MS = 150.0        # m/s (335 mph): a reported speed under this is always believed (unless over MAX_MS)
SPIKE_FACTOR = 3.0          # ... over it, believed while at most this many times the car's measured motion
SPIKE_SLACK_MS = 50.0       # ... plus this (a car accelerating between two poses half a second apart)
POSE_DT_MIN = 0.05          # s: two poses closer than this measure nothing
POSE_DT_MAX = 5.0           # s: ... nor farther apart (a paused or minimised game)
MPH = 2.23694


class VelocityFilter:
    def __init__(self):
        self.last = None            # (t, anchor id, x, y) of the previous pose
        self.distrusted = False     # the last pose's velocity was not believed (one log line per run of them)
        self.count = 0              # poses whose velocity was not believed, this connection

    def update(self, t, aid, x, y, vx, vy):
        """The velocity (vx, vy) to use for this pose: the game's, or the car's measured motion, or (0, 0)."""
        last, self.last = self.last, (t, aid, x, y)
        v = math.hypot(vx, vy)
        motion = None
        if last is not None and last[1] == aid:
            dt = t - last[0]
            if POSE_DT_MIN <= dt <= POSE_DT_MAX:
                mx, my = (x - last[2]) / dt, (y - last[3]) / dt
                if math.hypot(mx, my) <= MAX_MS:
                    motion = (mx, my)
        if v <= MAX_MS and (v <= SPIKE_MIN_MS or motion is None or v <= SPIKE_FACTOR * math.hypot(*motion)
                            + SPIKE_SLACK_MS):
            self.distrusted = False
            return vx, vy
        self.count += 1
        out = motion if motion is not None else (0.0, 0.0)
        if not self.distrusted:
            log.warning("pose: the game reported the car at %.0f m/s (%.0f mph) but it moved %s: %s instead (a physics "
                        "glitch, a reset or respawn)", v, v * MPH,
                        f"{math.hypot(*motion):.0f} m/s" if motion is not None else "an unknown amount",
                        f"{math.hypot(*out):.0f} m/s" if motion is not None else "standing")
        self.distrusted = True
        return out
