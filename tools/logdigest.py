#!/usr/bin/env python3
"""Read a BeamNG.Earth server.log without scrolling it.

  python3 tools/logdigest.py                          a page-sized summary of the latest server.log
  python3 tools/logdigest.py path/to/server.log.1     of another log
  python3 tools/logdigest.py --around 18:01:07 --secs 20          the lines in a window of time
  python3 tools/logdigest.py --area grading --level warning       one area, from a level up
  python3 tools/logdigest.py --near -34.9954,138.7003 --radius 200  lines naming a place
  python3 tools/logdigest.py --grep hitch

Any filter switches from the summary to the matching lines (line number, time, level, area, message).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, ".."))
import logtools  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", help="a server.log (default: the app's logs folder)")
    ap.add_argument("--around", help="HH:MM:SS or a full timestamp")
    ap.add_argument("--secs", type=float, default=30)
    ap.add_argument("--area")
    ap.add_argument("--level")
    ap.add_argument("--grep")
    ap.add_argument("--near", help="lat,lon")
    ap.add_argument("--radius", type=float, default=300)
    a = ap.parse_args(argv)
    path = a.log
    if not path:
        import apppaths
        path = os.path.join(apppaths.data_dir(), "logs", "server.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print("cannot read %s: %s" % (path, e), file=sys.stderr)
        return 1
    near = tuple(float(x) for x in a.near.split(",")) if a.near else None
    if any((a.around, a.area, a.level, a.grep, near)):
        out = logtools.window(lines, a.around, a.secs, a.area, a.level, a.grep, near, a.radius)
    else:
        out = logtools.digest(lines)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
