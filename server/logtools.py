"""Reading and collecting BeamNG.Earth logs (tools/logdigest.py, tools/collect_logs.py): a page-sized digest of a
server.log for a developer or for Claude, a window of lines around a time or a place, and a one-file bundle of
every log a bug report needs with the user's name and paths scrubbed. Pure but for the files it is given."""
import collections
import math
import os
import re
import zipfile

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) (\w+) ([\w.\-]+): (.*)$")
COORD = re.compile(r"(-?\d{1,3}\.\d{4,6}),\s?(-?\d{1,3}\.\d{4,6})")
NUM = re.compile(r"-?\d+(?:\.\d+)?")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def parse(lines):
    """Records from log lines: dicts {n, ts, level, name, msg, extra}; the lines that belong to the record above
    them (a traceback, a faulthandler dump) are its `extra`. Lines before the first record are dropped."""
    recs = []
    for n, raw in enumerate(lines, 1):
        raw = raw.rstrip("\r\n")
        m = LINE.match(raw)
        if m:
            recs.append({"n": n, "ts": m.group(1), "ms": m.group(2), "level": m.group(3), "name": m.group(4),
                         "msg": m.group(5), "extra": []})
        elif recs:
            recs[-1]["extra"].append(raw)
    return recs


def shape(msg):
    """A message with its numbers and ids made alike, to count the same line repeating with other values."""
    msg = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", msg)
    return NUM.sub("#", msg)[:160]


def _secs(ts):
    import datetime
    return datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()


def digest(lines, top=8):
    """A summary of one server.log as text lines: when it ran and how it ended, the session header, the levels,
    the warnings and errors grouped by shape with their first line number, tracebacks, the worst hitches and the
    ground and grading lines of note. Pure."""
    recs = parse(lines)
    if not recs:
        return ["no log records found"]
    out = []
    first, last = recs[0], recs[-1]
    try:
        out.append("ran %s .. %s (%.0f s), %d records, last line %d" % (first["ts"], last["ts"],
                   _secs(last["ts"]) - _secs(first["ts"]), len(recs), last["n"]))
    except ValueError:
        out.append("%d records" % len(recs))
    exit_ = [r for r in recs if "exited" in r["msg"] and ("server" in r["msg"] or "launcher" in r["name"])]
    ended = [r for r in recs if r["msg"].startswith("shutdown") or "exit:" in r["msg"]]
    if exit_:
        out.append("end: " + exit_[-1]["msg"])
    elif ended:
        out.append("end: " + ended[-1]["msg"])
    else:
        out.append("end: no exit record (the log just stops: a crash, a kill, or an older build)")
    for r in recs:
        if r["msg"].startswith("session:") or r["msg"].startswith("game hello"):
            out.append("  " + r["msg"])
    cnt = collections.Counter(r["level"] for r in recs)
    out.append("levels: " + ", ".join("%s %d" % (lv, cnt[lv]) for lv in LEVELS if cnt[lv]))
    groups = collections.OrderedDict()
    for r in recs:
        if r["level"] in ("WARNING", "ERROR", "CRITICAL"):
            g = groups.setdefault((r["level"], r["name"], shape(r["msg"])), [0, r])
            g[0] += 1
    if groups:
        out.append("warnings and errors, by how often:")
        for (lv, name, sh), (n, r) in sorted(groups.items(), key=lambda kv: -kv[1][0])[:top]:
            out.append("  %4dx %s %s: %s  (first at line %d, %s)" % (n, lv, name, r["msg"][:140], r["n"], r["ts"][11:]))
        if len(groups) > top:
            out.append("  ... %d more kinds" % (len(groups) - top))
    tbs = [r for r in recs if r["extra"] and any(x.startswith("Traceback") or x.startswith("  File") for x in r["extra"])]
    tbs += [r for r in recs if "Traceback" in r["msg"] and r not in tbs]
    if tbs:
        out.append("tracebacks: %d" % len(tbs))
        for r in tbs[:3]:
            tail = [x for x in r["extra"] if x.strip()][-1:] or [""]
            out.append("  line %d %s: %s -> %s" % (r["n"], r["name"], r["msg"][:100], tail[0].strip()[:120]))
    hitches = []
    for r in recs:
        m = re.search(r"hitch (\d+) ms", r["msg"])
        if m:
            hitches.append((int(m.group(1)), r))
    if hitches:
        out.append("worst hitches (%d in all):" % len(hitches))
        for ms, r in sorted(hitches, key=lambda t: -t[0])[:3]:
            out.append("  line %d %s: %s" % (r["n"], r["ts"][11:], r["msg"][:170]))
    notes = [r for r in recs if r["msg"].removeprefix("game: ").startswith(("ground issue", "ground files", "wheels: ground"))
             or "WRONG" in r["msg"]]
    if notes:
        out.append("ground: %d notable lines, e.g. line %d: %s" % (len(notes), notes[0]["n"], notes[0]["msg"][:150]))
    grad = [r for r in recs if "rail crossing" in r["msg"] or "hump over" in r["msg"]]
    if grad:
        out.append("grading: %d crossing/hump lines, e.g. line %d: %s" % (len(grad), grad[0]["n"], grad[0]["msg"][:150]))
    return out


def _dist_m(a, b):
    k = 111320.0
    dy = (a[0] - b[0]) * k
    dx = (a[1] - b[1]) * k * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def window(lines, around=None, secs=30, area=None, level=None, grep=None, near=None, radius_m=300):
    """The records matching all given filters, as text: around "HH:MM:SS" (or a full timestamp) within secs;
    area: a logger name or prefix; level: the least severe level to show; grep: text in the message; near:
    (lat, lon) within radius_m of a coordinate in the line. Pure."""
    recs = parse(lines)
    if around:
        a = around if len(around) > 8 else (recs[0]["ts"][:11] + around) if recs else around
        try:
            t0 = _secs(a)
        except ValueError:
            return ["bad time %r (HH:MM:SS or YYYY-MM-DD HH:MM:SS)" % around]
    out = []
    for r in recs:
        if around and abs(_secs(r["ts"]) - t0) > secs:
            continue
        if area and not (r["name"] == area or r["name"].startswith(area + ".") or r["name"].startswith("earthd." + area)):
            continue
        if level and LEVELS.index(r["level"]) < LEVELS.index(level.upper()):
            continue
        if grep and grep.lower() not in r["msg"].lower():
            continue
        if near:
            ok = False
            for m in COORD.finditer(r["msg"]):
                if _dist_m(near, (float(m.group(1)), float(m.group(2)))) <= radius_m:
                    ok = True
                    break
            if not ok:
                continue
        out.append("%d %s.%s %s %s: %s" % (r["n"], r["ts"][11:], r["ms"], r["level"], r["name"], r["msg"]))
        out.extend("    " + x for x in r["extra"][:12])
    return out


def scrub(text, user=None, home=None):
    """Text with the user's name, home folder and anything shaped like a key or token value hidden. Pure."""
    home = home or os.path.expanduser("~")
    for h in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if h and len(h) > 3:
            text = text.replace(h, "~")
    user = user or os.path.basename(home.rstrip("/\\"))
    if user and len(user) > 2:
        text = re.sub(r"(?i)(?<=[\\/])%s(?=[\\/])" % re.escape(user), "<user>", text)
    text = re.sub(r"(?i)\b(\w*(?:key|token|secret|password|cookie|auth)\w*)\s*[=:]\s*\S+", r"\1=<hidden>", text)
    return text


BINARY = (".dmp", ".mdmp", ".jpg", ".png")      # copied as they are (not scrubbed: no text in them to read)
DUMPS_IN_BUNDLE = 2


def collect(out, logs_dir, user_dir=None, app_dir=None, now=None):
    """The bug report bundle (tools/collect_logs.py, the launcher's --collect-logs, the in-game report's button):
    every server.log run and launcher.log, the crash files and the latest minidumps, the game's beamng.log, the
    profiler's snapshot and the hitch captures, the newest in-game reports, the app's remembered settings (app.json)
    and a list of the session folder, plus SUMMARY.txt (the digest of the latest server.log). Returns the names."""
    import glob
    import json
    import platform
    import sys
    import time
    files = []
    for p in sorted(glob.glob(os.path.join(logs_dir, "*.log*")) + glob.glob(os.path.join(logs_dir, "crash-*.txt"))):
        files.append((p, "logs/" + os.path.basename(p)))
    dumps = sorted(glob.glob(os.path.join(logs_dir, "dumps", "*.dmp")), key=os.path.getmtime)[-DUMPS_IN_BUNDLE:]
    files += [(p, "logs/dumps/" + os.path.basename(p)) for p in dumps]
    extra = {}
    if user_dir:
        files.append((os.path.join(user_dir, "beamng.log"), "game/beamng.log"))
        files.append((os.path.join(user_dir, "settings", "beamngEarth-profiler.json"), "game/profiler-snapshot.json"))
        for p in sorted(glob.glob(os.path.join(user_dir, "beamngEarth", "hitches", "*.json")))[-5:]:
            files.append((p, "game/hitches/" + os.path.basename(p)))
        reports = sorted(glob.glob(os.path.join(user_dir, "beamngEarth", "reports", "*", "")))[-3:]
        for d in reports:
            for p in sorted(glob.glob(os.path.join(d, "*")))[:12]:
                if os.path.isfile(p):
                    files.append((p, "game/reports/%s/%s" % (os.path.basename(d.rstrip("/\\")), os.path.basename(p))))
        sess = os.path.join(user_dir, "temp", "beamngEarth")
        rows = []
        for d, _dirs, names in os.walk(sess):
            for n in names[:4000]:
                try:
                    rel = os.path.relpath(os.path.join(d, n), sess).replace(os.sep, "/")   # (the same on Windows)
                    rows.append("%10d  %s" % (os.path.getsize(os.path.join(d, n)), rel))
                except OSError:
                    pass
            if len(rows) > 20000:
                break
        if rows:
            extra["game/session-files.txt"] = "\n".join(rows) + "\n"
    if app_dir:
        try:
            with open(os.path.join(app_dir, "app.json"), encoding="utf-8") as f:
                extra["app.json"] = json.dumps(json.load(f), indent=2)
        except (OSError, ValueError):
            pass
    summary = []
    try:
        with open(os.path.join(logs_dir, "server.log"), encoding="utf-8", errors="replace") as f:
            summary = digest(f.read().splitlines())
    except OSError:
        summary = ["no server.log in " + logs_dir]
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    info = ["collected %s" % when, "platform: %s" % platform.platform(), "python: %s" % sys.version.split()[0],
            "game user folder: %s" % (user_dir or "not known"), ""]
    extra["SUMMARY.txt"] = "\n".join(info + summary) + "\n"
    return bundle(out, files, extra)


def bundle(dest, files, extra=None, user=None, home=None, max_bytes=8 * 1024 * 1024):
    """Write a zip of the log files (path -> name inside), each scrubbed and cut to its last max_bytes, plus a
    SUMMARY.txt (the digest of server.log) and the extra {name: text} given. Returns the names written."""
    names = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for path, name in files:
            try:
                with open(path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - max_bytes))
                    raw = f.read()
            except OSError:
                continue
            if name.lower().endswith(BINARY):
                z.writestr(name, raw)
                names.append(name)
                continue
            text = scrub(raw.decode("utf-8", "replace"), user, home)
            if size > max_bytes:
                text = "[cut: the last %d of %d bytes]\n" % (max_bytes, size) + text
            z.writestr(name, text)
            names.append(name)
        for name, text in (extra or {}).items():
            z.writestr(name, scrub(text, user, home))
            names.append(name)
    return names
