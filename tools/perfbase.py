#!/usr/bin/env python3
"""Performance baselines from server.log: save a run's numbers, compare a later run with them.

  python3 tools/perfbase.py server.log                        the run's numbers
  python3 tools/perfbase.py server.log --save base.json       saved as the baseline
  python3 tools/perfbase.py server.log --compare base.json    compared: exit status 1 when something got worse

For a fair comparison drive the same route on both builds (tools/autodrive_perf.py --from ... --to ...) on the same
machine and settings. The thresholds are perfbase.METRICS (a share of the baseline, and at least an amount).
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
import perfbase  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("--save")
    ap.add_argument("--compare")
    a = ap.parse_args(argv)
    with open(a.log, encoding="utf-8", errors="replace") as f:
        now = perfbase.summarize(f.read().splitlines())
    print(json.dumps(now, indent=2))
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump(now, f, indent=2)
        print("saved", a.save)
    if a.compare:
        with open(a.compare, encoding="utf-8") as f:
            base = json.load(f)
        lines, worse = perfbase.compare(base, now)
        print("\n".join(lines) or "nothing to compare")
        if worse:
            print("worse: " + ", ".join(worse))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
