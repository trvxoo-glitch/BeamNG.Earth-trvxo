#!/usr/bin/env python3
"""Turn BeamNG.Earth's saved timings into one timeline for Perfetto (https://ui.perfetto.dev: drag the file in) or
chrome://tracing.

  python3 tools/profile_trace.py server.log -o trace.json
  python3 tools/profile_trace.py hitch_20261107_142809.json -o trace.json     (a hitch capture: hitchcap.lua)
  python3 tools/profile_trace.py beamngEarth-profiler.json -o trace.json      (the profiler window's Export)

Several inputs go into one file, each on its own tracks (their clocks are not joined: a capture and a server.log line
up by eye). The kind of each file is read from its content.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import tracefmt  # noqa: E402


def load(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        if data.get("kind") == "beamngEarth hitch capture" or "frames" in data and "hitchMs" in data:
            return tracefmt.from_hitch_capture(data)
        return tracefmt.from_profiler_snapshot(data)
    return tracefmt.from_server_log(text.splitlines())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", default="trace.json")
    a = ap.parse_args(argv)
    events = []
    for p in a.inputs:
        got = load(p)
        print("%s: %d events" % (p, len(got)))
        events += got
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(tracefmt.trace(events), f)
    print("wrote %s: open it in https://ui.perfetto.dev or chrome://tracing" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
