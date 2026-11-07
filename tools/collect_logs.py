#!/usr/bin/env python3
"""Collect everything a bug report needs into one zip, with your user name, home folder and any key or token values
hidden. (The packaged app does the same: BeamNG.Earth.exe --collect-logs, or the in-game report's "Add logs" button.)

  python3 tools/collect_logs.py                         -> beamng-earth-logs-<time>.zip in the current folder
  python3 tools/collect_logs.py --user-dir "C:/Users/me/AppData/Local/BeamNG.drive/current" -o bug.zip

It holds every server.log run, launcher.log, the crash files and the latest minidumps, the game's beamng.log, the
profiler's snapshot and hitch captures, the newest in-game reports, app.json, a list of the session folder, and
SUMMARY.txt (tools/logdigest.py's summary of server.log). Nothing is sent anywhere.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, os.path.join(HERE, ".."))
import logtools  # noqa: E402


def find_user_dir():
    import apppaths
    saved = getattr(apppaths, "saved_user_dir", None)
    cands = [saved()] if callable(saved) else []
    if apppaths.IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or ""
        cands += [os.path.join(base, "BeamNG", "BeamNG.drive", "current"), os.path.join(base, "BeamNG.drive", "current")]
    else:
        home = os.path.expanduser("~")
        cands += [os.path.join(home, ".local", "share", "BeamNG", "BeamNG.drive", "current"),
                  os.path.join(home, ".local", "share", "BeamNG.drive", "current")]
    return next((c for c in cands if c and os.path.isdir(c)), None)


def main(argv=None):
    import apppaths
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out")
    ap.add_argument("--user-dir", help="the game's user folder (the one with beamng.log)")
    a = ap.parse_args(argv)
    dd = apppaths.data_dir()
    out = a.out or "beamng-earth-logs-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
    names = logtools.collect(out, os.path.join(dd, "logs"), a.user_dir or find_user_dir(), dd)
    print("wrote %s (%d files)" % (out, len(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
