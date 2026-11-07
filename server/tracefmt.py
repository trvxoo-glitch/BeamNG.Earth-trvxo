"""Timelines for Perfetto (ui.perfetto.dev) and chrome://tracing, from what the logs and profilers save: a hitch
capture (hitchcap.lua: the game's frames and the mod's parts in the slow ones), the profiler's snapshot (the worker
jobs with their queue and run times and steps) and server.log (the game's hitches, the event loop's stalls, the
blocks' way from request to commit, the health lines). One file puts the game's frames, the server's loop and the
worker processes on one time axis. tools/profile_trace.py writes it. Pure.

The Chrome trace event format: {"traceEvents": [...]} with "ph" X (a span: ts and dur in microseconds), i (an
instant), C (a counter), M (names for processes and threads)."""
import re

import logtools

PID_GAME, PID_SERVER, PID_WORKERS = 1, 2, 3


def _meta(pid, name, tids=()):
    out = [{"ph": "M", "pid": pid, "name": "process_name", "args": {"name": name}}]
    for tid, tname in tids:
        out.append({"ph": "M", "pid": pid, "tid": tid, "name": "thread_name", "args": {"name": tname}})
    return out


def from_hitch_capture(cap, t0=None):
    """Events from a hitch capture: one span per frame on 'game frames' (the slow ones named by their biggest mod
    part, all parts in args), and the hitch as an instant with its engine subsystems and where it was."""
    frames = cap.get("frames") or []
    if not frames:
        return []
    base = frames[0]["t"] if t0 is None else t0
    ev = _meta(PID_GAME, "BeamNG.drive (the mod)", [(1, "game frames"), (2, "hitches")])
    for f in frames:
        ms = float(f.get("ms") or 0)
        end_us = (float(f["t"]) - base) * 1e6
        mod = f.get("mod") or {}
        name = "frame"
        if mod:
            name = "frame: " + max(mod.items(), key=lambda kv: kv[1])[0]
        ev.append({"ph": "X", "pid": PID_GAME, "tid": 1, "name": name, "ts": end_us - ms * 1000, "dur": ms * 1000,
                   "args": dict(mod, ms=ms)})
    h = cap.get("hitch") or {}
    at = cap.get("hitchAt")
    if at is not None:
        args = {"ms": cap.get("hitchMs"), **{"engine " + k: v for k, v in (h.get("engine") or {}).items()},
                **{k: v for k, v in (cap.get("where") or {}).items() if v not in (None, "")}}
        if h.get("gpu") is not None:
            args["gpu ms"] = h["gpu"]
        ev.append({"ph": "i", "pid": PID_GAME, "tid": 2, "name": "hitch %s ms" % cap.get("hitchMs"), "s": "p",
                   "ts": (float(at) - base) * 1e6, "args": args})
    return ev


def from_profiler_snapshot(snap):
    """Events from the profiler's saved snapshot (Export): each worker job as a span on its pool's track, starting
    when it began to run, with its queue wait and steps in args; its queue wait as a span before it."""
    srv = (snap or {}).get("server") or {}
    jobs = list(srv.get("recent") or []) + list(srv.get("active") or [])
    pools = sorted({str(j.get("pool") or "?") for j in jobs})
    tid = {p: i + 1 for i, p in enumerate(pools)}
    ev = _meta(PID_WORKERS, "world server workers", [(tid[p], p) for p in pools])
    for j in jobs:
        start = j.get("startOffsetMs")
        if start is None:
            continue
        run = j.get("runMs")
        if run is None and j.get("endOffsetMs") is not None:
            run = j["endOffsetMs"] - start
        if run is None:
            continue
        p = str(j.get("pool") or "?")
        steps = {s.get("name", "?"): round(float(s.get("totalMs") or 0), 1) for s in (j.get("steps") or [])}
        ev.append({"ph": "X", "pid": PID_WORKERS, "tid": tid[p], "name": str(j.get("name") or "job"),
                   "ts": float(start) * 1000, "dur": float(run) * 1000,
                   "args": {"outcome": j.get("outcome"), "queue ms": j.get("queueMs"), "worker": j.get("worker"),
                            **{"step " + k: v for k, v in steps.items()}}})
        q = j.get("queueMs")
        if q:
            ev.append({"ph": "X", "pid": PID_WORKERS, "tid": tid[p], "name": "queued", "ts": (float(start) - q) * 1000,
                       "dur": float(q) * 1000, "args": {"job": j.get("name")}})
    return ev


HITCH = re.compile(r"perf hitch (\d+) ms")
STALL = re.compile(r"event loop: the stall above lasted ([\d.]+) s")
READY = re.compile(r"terrain_ready (\(.*?\)) after (\d+) ms")
PIPE = re.compile(r"block (\(.*?\)) b(\d+): .*?committed ([\d.]+) s after it was sent")
HEALTH_MEM = re.compile(r"server ([\d.]+) (MB|GB).*?workers \d+ using ([\d.]+) (MB|GB)")


def _mb(v, unit):
    return float(v) * (1000 if unit == "GB" else 1)


def from_server_log(lines):
    """Events from server.log: the game's hitches (spans ending at their line), the event loop's stalls, the blocks'
    build times and their wait for the game's commit, warnings and errors as instants, memory as counters."""
    recs = logtools.parse(lines)
    if not recs:
        return []
    t0 = logtools._secs(recs[0]["ts"]) + int(recs[0]["ms"]) / 1000.0
    ev = _meta(PID_SERVER, "world server (server.log)", [(1, "game hitches"), (2, "event loop"), (3, "blocks"),
                                                         (4, "warnings and errors")])

    def us(r):
        return (logtools._secs(r["ts"]) + int(r["ms"]) / 1000.0 - t0) * 1e6
    for r in recs:
        m, t = r["msg"], us(r)
        h = HITCH.search(m)
        if h:
            d = int(h.group(1)) * 1000
            ev.append({"ph": "X", "pid": PID_SERVER, "tid": 1, "name": "game hitch %s ms" % h.group(1), "ts": t - d,
                       "dur": d, "args": {"line": m[:300]}})
            continue
        s = STALL.search(m)
        if s:
            d = float(s.group(1)) * 1e6
            ev.append({"ph": "X", "pid": PID_SERVER, "tid": 2, "name": "event loop stall", "ts": t - d, "dur": d,
                       "args": {"line": m[:300]}})
            continue
        b = READY.search(m)
        if b:
            d = int(b.group(2)) * 1000
            ev.append({"ph": "X", "pid": PID_SERVER, "tid": 3, "name": "build " + b.group(1), "ts": t - d, "dur": d})
            continue
        p = PIPE.search(m)
        if p:
            d = float(p.group(3)) * 1e6
            ev.append({"ph": "X", "pid": PID_SERVER, "tid": 3, "name": "wait for commit " + p.group(1), "ts": t - d,
                       "dur": d, "args": {"line": m[:300]}})
            continue
        mm = HEALTH_MEM.search(m) if m.startswith("health:") else None
        if mm:
            ev.append({"ph": "C", "pid": PID_SERVER, "name": "memory MB", "ts": t,
                       "args": {"server": _mb(mm.group(1), mm.group(2)), "workers": _mb(mm.group(3), mm.group(4))}})
            continue
        if r["level"] in ("WARNING", "ERROR", "CRITICAL"):
            ev.append({"ph": "i", "pid": PID_SERVER, "tid": 4, "name": "%s %s" % (r["level"], r["name"]), "s": "t",
                       "ts": t, "args": {"line": m[:300]}})
    return ev


def trace(events):
    return {"traceEvents": events, "displayTimeUnit": "ms"}
