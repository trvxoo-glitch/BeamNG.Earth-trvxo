"""Where the graded roads have humps and steps, in the log (earthd.grading), with coordinates (the user, 2026-11-06:
"look back over what information that would've been [useful] when you were using logs for development"). An
Adelaide rail crossing threw the car into the air and the logs could not say where: nothing logged a road's profile.

Per map cell, once its plans are made (grading._Planner._plan_cell):
- rail crossings: every point where a road's plan crosses a railway bed's plan (railways.grade_plans), with the two
  plans' target heights there; the step between them is what grade_points blends into a hump or a dip across the
  track. A step of CROSSING_STEP_M or more is an INFO line ("grading: rail crossing ..."), the rest DEBUG. With the
  cell's base heights at hand (fresh plans), the road's as-graded profile across the track too: the hump's height over
  the chord of +-CHORD_M, the line's "graded hump".
- humps in a road's own profile: the largest rise (crest) or dip (sag) of its target heights over the chord of
  +-CHORD_M; HUMP_M or more is an INFO line, at most HUMP_LINES per cell (the rest counted in one line).
- with BNGE_DEBUG / --debug grading: one line per road (its length, steepest grade, largest hump and where).
Coordinates are lat,lon with 5 decimals (~1 m): paste them into the map search or a teleport.

Pure but for the logger and the callbacks (to_ll, base_fn, grade_fn); never raises into the grading (report_cell
catches everything: a diagnostic must not cost a road its grading).
"""
import logging
import math

import numpy as np

log = logging.getLogger("earthd.grading")

STEP_M = 2.0            # m between the resampled profile's points (grading.PROFILE_STEP)
CHORD_M = 6.0           # m either side of a point: its hump is its height over the chord between those two points
HUMP_M = 0.50           # m: a hump (or dip) this high in a road's target profile gets an INFO line (a minor dirt
                        # road follows the DEM's bumps on purpose, grading.DIRT_SIGMA_MINOR_M: 0.3 m named them all)
CROSSING_STEP_M = 0.15  # m between a road's and a railway's target heights where they cross: an INFO line
CROSSING_HUMP_M = 0.20  # m: the as-graded hump across a track worth naming (in the crossing's line)
HUMP_LINES = 2          # INFO hump lines per cell at most
ACROSS_M = 20.0         # m of road either side of a crossing sampled for its as-graded profile
RAIL_TAG = "rail:"      # (grading.RAIL_TAG)
SKIP_TAGS = (":tun",)   # tunnels' own plans (grading.TUNNEL_TAG): no road profile of their own


def resample(xy, z, step=STEP_M):
    """(s (m,), xy (m, 2), z (m,)) of a polyline with heights, every step metres along it (its last point kept).
    Pure."""
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    z = np.asarray(z, np.float64).reshape(-1)
    if len(xy) < 2:
        return np.zeros(len(xy)), xy, z
    seg = np.hypot(*np.diff(xy, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    if total <= 0:
        return np.zeros(1), xy[:1], z[:1]
    n = max(2, int(math.floor(total / step)) + 1)
    si = np.linspace(0.0, total, n)
    return si, np.stack([np.interp(si, s, xy[:, 0]), np.interp(si, s, xy[:, 1])], axis=1), np.interp(si, s, z)


def humps(s, z, chord_m=CHORD_M):
    """(h (m,), k): each point's height over the chord between the points chord_m before and after it (positive: a
    crest, negative: a sag; 0 within chord_m of either end), and the samples either side. Uniform s. Pure."""
    z = np.asarray(z, np.float64)
    h = np.zeros(len(z))
    if len(s) < 3:
        return h, 0
    step = float(s[1] - s[0]) or STEP_M
    k = max(1, int(round(chord_m / step)))
    if len(z) <= 2 * k:
        return h, k
    h[k:-k] = z[k:-k] - 0.5 * (z[:-2 * k] + z[2 * k:])
    return h, k


def profile_stats(xy, z, step=STEP_M, chord_m=CHORD_M):
    """{length, grade (the steepest, rise/run over step), hump (the largest |height over the chord|, signed), at (x,
    y of that point), s (m along the road)} of a plan's profile, None for fewer than two points. Pure."""
    s, pts, zz = resample(xy, z, step)
    if len(s) < 2:
        return None
    g = np.abs(np.diff(zz)) / np.maximum(np.diff(s), 1e-9)
    h, _k = humps(s, zz, chord_m)
    i = int(np.argmax(np.abs(h)))
    return {"length": float(s[-1]), "grade": float(g.max()) if len(g) else 0.0, "hump": float(h[i]),
            "at": (float(pts[i, 0]), float(pts[i, 1])), "s": float(s[i])}


def _seg_cross(a0, a1, b0, b1):
    """(t, u) where segment a0-a1 meets b0-b1 (0 <= t, u <= 1), None when they do not. Pure."""
    d1, d2 = a1 - a0, b1 - b0
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    w = b0 - a0
    t = (w[0] * d2[1] - w[1] * d2[0]) / den
    u = (w[0] * d1[1] - w[1] * d1[0]) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return t, u
    return None


def crossings(roads, rails):
    """[(road fid, rail fid, x, y, road z, rail z, i, t, road xy)] where a road plan's polyline crosses a railway plan's
    (i, t: the road's segment and the share along it; road xy: that piece's vertices: a road in pieces has one fid). roads / rails: [(fid, xy (k, 2), z (k,))]. Pure; bounding boxes
    first (a city cell has ~1000 road plans and a few rail ones)."""
    out = []
    rb = []
    for rfid, rxy, rz in rails:
        rxy = np.asarray(rxy, np.float64)
        if len(rxy) >= 2:
            rb.append((rfid, rxy, np.asarray(rz, np.float64), rxy.min(axis=0), rxy.max(axis=0)))
    if not rb:
        return out
    for fid, xy, z in roads:
        xy = np.asarray(xy, np.float64)
        if len(xy) < 2:
            continue
        z = np.asarray(z, np.float64)
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        for rfid, rxy, rz, rlo, rhi in rb:
            if (lo > rhi).any() or (hi < rlo).any():
                continue
            # the road's segments near the rail's box only
            for i in range(len(xy) - 1):
                a0, a1 = xy[i], xy[i + 1]
                if min(a0[0], a1[0]) > rhi[0] or max(a0[0], a1[0]) < rlo[0] or \
                        min(a0[1], a1[1]) > rhi[1] or max(a0[1], a1[1]) < rlo[1]:
                    continue
                for j in range(len(rxy) - 1):
                    hit = _seg_cross(a0, a1, rxy[j], rxy[j + 1])
                    if hit is None:
                        continue
                    t, u = hit
                    p = a0 + t * (a1 - a0)
                    out.append((fid, rfid, float(p[0]), float(p[1]), float(z[i] + t * (z[i + 1] - z[i])),
                                float(rz[j] + u * (rz[j + 1] - rz[j])), i, float(t), xy))
    return out


def across(xy, i, t, half_m=ACROSS_M, step=STEP_M):
    """(s (m,), pts (m, 2)): the road's centreline from half_m before to half_m after the point at segment i, share t,
    every step metres (clipped at the road's ends). Pure."""
    xy = np.asarray(xy, np.float64)
    seg = np.hypot(*np.diff(xy, axis=0).T)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    at = float(s[i] + t * seg[i])
    a, b = max(0.0, at - half_m), min(float(s[-1]), at + half_m)
    n = max(2, int(round((b - a) / step)) + 1)
    si = np.linspace(a, b, n)
    return si - at, np.stack([np.interp(si, s, xy[:, 0]), np.interp(si, s, xy[:, 1])], axis=1)


def _ll(to_ll, x, y):
    try:
        la, lo = to_ll(np.array([x], np.float64), np.array([y], np.float64))
        return "%.5f,%.5f" % (float(np.asarray(la).reshape(-1)[0]), float(np.asarray(lo).reshape(-1)[0]))
    except Exception:           # (a frame that cannot convert: the plane coordinates instead)
        return "x %.1f y %.1f" % (x, y)


def _short(fid):
    fid = str(fid)
    return fid if len(fid) <= 40 else fid[:37] + "..."


def report_cell(key, plans, to_ll, base_fn=None, grade_fn=None, logger=None, quiet=False):
    """Log a cell's rail crossings and humps (the module doc). plans: [(fid, Plan)] (xy, z); to_ll(e, n) -> (lat, lon)
    arrays; base_fn(e, n) -> the ungraded ground (m) and grade_fn(e, n, base) -> the graded ground at the points (the
    cell's own plans: grading.grade_points) for the as-graded profile across each track, both optional. quiet: its
    INFO lines at DEBUG (plans another process made and logged already: the plan cache). Returns
    {"crossings", "steps", "humps"} (counts) or None after an error (logged at DEBUG)."""
    logger = logger or log
    try:
        return _report(key, plans, to_ll, base_fn, grade_fn, logger, logging.DEBUG if quiet else logging.INFO)
    except Exception as e:      # (diagnostics only)
        logger.debug("grading: diagnostics of %s: %s: %s", key, type(e).__name__, e)
        return None


def _report(key, plans, to_ll, base_fn, grade_fn, logger, info=logging.INFO):
    roads, rails = [], []
    for fid, p in plans:
        fid = str(fid)
        if any(tag in fid for tag in SKIP_TAGS):
            continue
        xy, z = getattr(p, "xy", None), getattr(p, "z", None)
        if xy is None or z is None or len(xy) < 2:
            continue
        (rails if fid.startswith(RAIL_TAG) else roads).append((fid, np.asarray(xy, np.float64),
                                                              np.asarray(z, np.float64)))
    debug = logger.isEnabledFor(logging.DEBUG)
    out = {"crossings": 0, "steps": 0, "humps": 0}
    seen = set()
    for fid, rfid, x, y, zr, zt, i, t, rxy in crossings(roads, rails):
        k = (fid, rfid, round(x), round(y))
        if k in seen:           # (a road's plan split in pieces meets the track once)
            continue
        seen.add(k)
        out["crossings"] += 1
        step = zr - zt
        graded = ""
        hump = None
        if base_fn is not None and grade_fn is not None:    # (a few crossings a cell: ~20 points each)
            try:
                sa, pts = across(rxy, i, t)
                base = np.asarray(base_fn(pts[:, 0], pts[:, 1]), np.float64).reshape(-1)
                zg = np.asarray(grade_fn(pts[:, 0], pts[:, 1], base), np.float64).reshape(-1)
                h, _k = humps(sa, zg)
                j = int(np.argmax(np.abs(h)))
                hump = float(h[j])
                grade = float(np.max(np.abs(np.diff(zg)) / np.maximum(np.diff(sa), 1e-9))) if len(zg) > 1 else 0.0
                graded = ", graded %s %.2f m %+.0f m from the track, steepest %.0f %%" % (
                    "hump" if hump >= 0 else "dip", abs(hump), float(sa[j]), grade * 100)
            except Exception as e:
                graded = ", graded profile unavailable (%s)" % type(e).__name__
        bad = abs(step) >= CROSSING_STEP_M or (hump is not None and abs(hump) >= CROSSING_HUMP_M)
        if bad:
            out["steps"] += 1
        logger.log(info if bad else logging.DEBUG,
                   "grading: rail crossing at %s (cell %s): road %s at %.2f m, track %s at %.2f m (road %+.2f m)%s",
                   _ll(to_ll, x, y), key, _short(fid), zr, _short(rfid[len(RAIL_TAG):]), zt, step, graded)
    worst = []
    for fid, xy, z in roads:
        st = profile_stats(xy, z)
        if st is None:
            continue
        if debug:
            logger.debug("grading: road %s (cell %s): %.0f m, steepest %.1f %%, largest %s %.2f m at %s",
                         _short(fid), key, st["length"], st["grade"] * 100, "hump" if st["hump"] >= 0 else "dip",
                         abs(st["hump"]), _ll(to_ll, *st["at"]))
        if abs(st["hump"]) >= HUMP_M:
            worst.append((abs(st["hump"]), fid, st))
    worst.sort(key=lambda w: -w[0])
    out["humps"] = len(worst)
    for _a, fid, st in worst[:HUMP_LINES]:
        logger.log(info, "grading: road %s (cell %s) has a %.2f m %s over %.0f m at %s (%.0f m along it), steepest %.0f %%",
                    _short(fid), key, abs(st["hump"]), "hump" if st["hump"] >= 0 else "dip", 2 * CHORD_M,
                    _ll(to_ll, *st["at"]), st["s"], st["grade"] * 100)
    if len(worst) > HUMP_LINES:
        logger.log(info, "grading: cell %s has %d more roads with a hump or dip over %.2f m (--debug grading: each road)",
                    key, len(worst) - HUMP_LINES, HUMP_M)
    return out
