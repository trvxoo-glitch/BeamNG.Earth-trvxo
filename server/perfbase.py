"""A run's performance in numbers from its server.log, saved as a baseline and compared with a later run
(tools/perfbase.py): the game's frame rate and 1 % low from its minute lines, hitches a minute and the worst, the
server's event loop stalls, the blocks' build times (terrain_ready) and their wait for the game's commit, the server's
memory from its health lines. A fixed route (tools/autodrive_perf.py) driven on two builds gives two logs; the
comparison names what got worse past its threshold. Pure.
"""
import re

import logtools

MINUTE = re.compile(r"perf minute (\d+) fps, 1% low (\d+), worst (\d+) ms, (\d+) hitches")
HITCH = re.compile(r"perf hitch (\d+) ms")
STALL = re.compile(r"event loop: the stall above lasted ([\d.]+) s")
READY = re.compile(r"terrain_ready \(.*?\) after (\d+) ms")
HEALTH = re.compile(r"health: server ([\d.]+) (MB|GB)")
# metric: (what it is, higher is better, the change that counts as worse: a share of the baseline, and at least)
METRICS = {
    "fps": ("average fps", True, 0.10, 2.0),
    "low1": ("1 % low fps", True, 0.15, 2.0),
    "hitches_per_min": ("hitches a minute", False, 0.30, 2.0),
    "worst_hitch_ms": ("the worst hitch, ms", False, 0.50, 300.0),
    "stall_s_per_min": ("event loop stall seconds a minute", False, 0.50, 0.5),
    "block_p50_ms": ("block build median, ms", False, 0.30, 300.0),
    "block_p95_ms": ("block build 95th percentile, ms", False, 0.30, 500.0),
    "server_mb_max": ("server memory at most, MB", False, 0.20, 200.0),
}


def _pct(values, q):
    if not values:
        return None
    v = sorted(values)
    return float(v[min(len(v) - 1, int(round(q * (len(v) - 1))))])


def summarize(lines):
    """{metric: value} for a server.log (a metric with no lines for it is left out), and 'minutes' and 'records'."""
    recs = logtools.parse(lines)
    out = {"records": len(recs)}
    if not recs:
        return out
    try:
        minutes = max(1e-6, (logtools._secs(recs[-1]["ts"]) - logtools._secs(recs[0]["ts"])) / 60.0)
    except ValueError:
        minutes = None
    fps, low, hitches, stalls, ready, mem = [], [], [], [], [], []
    for r in recs:
        m = r["msg"]
        g = MINUTE.search(m)
        if g:
            fps.append(int(g.group(1)))
            low.append(int(g.group(2)))
            continue
        g = HITCH.search(m)
        if g:
            hitches.append(int(g.group(1)))
            continue
        g = STALL.search(m)
        if g:
            stalls.append(float(g.group(1)))
            continue
        g = READY.search(m)
        if g:
            ready.append(int(g.group(1)))
            continue
        g = HEALTH.search(m)
        if g:
            mem.append(float(g.group(1)) * (1000 if g.group(2) == "GB" else 1))
    if minutes:
        out["minutes"] = round(minutes, 1)
    if fps:
        out["fps"] = round(sum(fps) / len(fps), 1)
        out["low1"] = round(sum(low) / len(low), 1)
    if minutes and (hitches or fps):
        out["hitches_per_min"] = round(len(hitches) / minutes, 2)
        out["stall_s_per_min"] = round(sum(stalls) / minutes, 2)
    if hitches:
        out["worst_hitch_ms"] = max(hitches)
    if ready:
        out["block_p50_ms"] = _pct(ready, 0.5)
        out["block_p95_ms"] = _pct(ready, 0.95)
    if mem:
        out["server_mb_max"] = max(mem)
    return out


def compare(base, now):
    """(lines, worse): a line per metric in both runs, 'worse' the ones past their threshold."""
    lines, worse = [], []
    for key, (what, higher_better, share, least) in METRICS.items():
        if key not in base or key not in now:
            continue
        b, n = float(base[key]), float(now[key])
        change = n - b
        bad = (-change if higher_better else change)
        is_worse = bad > max(least, share * abs(b))
        mark = "WORSE" if is_worse else ("better" if bad < -max(least, share * abs(b)) else "same")
        lines.append(f"{what}: {b:g} -> {n:g} ({change:+g}) {mark}")
        if is_worse:
            worse.append(key)
    return lines, worse
