"""The server's logging set-up (server.log: the file players send): one format for earthd and its worker processes,
debug output per area, crash and stall evidence, and the session's header.

- FORMAT: every process's lines, "2026-10-17 07:04:50,488 INFO earthd.grading: ..." (launcher.echo_lines and the
  tools that read server.log parse it: keep it).
- Debug areas: `earthd --debug grading,surface` (or BNGE_DEBUG=grading,surface in the environment) turns on DEBUG for
  those loggers alone (earthd.grading, earthd.surface, ...), in the server and in every worker process it starts
  (they inherit the environment), so a problem in one area can be traced without -v's flood of every other area.
  "all" is -v. DEBUG_AREAS names the usual ones (the --help text); any earthd.<name> logger works.
- Crash evidence: an uncaught exception in any thread (and in an asyncio task nobody awaited) goes into the log with
  its traceback, and faulthandler writes every thread's stack on a native crash (Rust, DuckDB, numpy: GitHub #687's
  crashed session had only "Process finished" in its log).
- The session header (session_lines): the release, Python, the OS, the machine and the choices that differ from the
  defaults, first in every log, so a log from a player says what made it.
Pure but for the logging module, faulthandler and the environment.
"""
import atexit
import faulthandler
import logging
import os
import platform
import sys
import threading
import time

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
ENV = "BNGE_DEBUG"
# The areas a player or developer is likely to want (earthd --help); each is the logger "earthd.<area>".
DEBUG_AREAS = ("grading", "surface", "roads", "blockworker", "worker", "pools", "mapdata", "dem", "imagery", "net",
               "cache", "crossings", "vegetation", "buildings", "colormatch", "weather")
# Environment variables named like these have their values left out of the header (a key in BNGE_SERVER_ENV, ...).
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASS", "AUTH", "COOKIE")
ENV_VALUE_CHARS = 120


def parse_areas(spec):
    """The logger names a debug spec turns on: "grading, surface" -> ["earthd.grading", "earthd.surface"]; "all" or
    "*" -> [""] (the root logger); a full dotted name (earthd.foo.bar, or another package's) stays as it is. Pure."""
    out = []
    for part in str(spec or "").replace(";", ",").replace(" ", ",").split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part in ("all", "*"):
            name = ""
        elif "." in part:
            name = part
        else:
            name = "earthd" if part == "earthd" else "earthd." + part
        if name not in out:
            out.append(name)
    return out


def apply_debug(spec=None):
    """Turn DEBUG on for the areas of spec (default: $BNGE_DEBUG). Returns the logger names turned on."""
    names = parse_areas(os.environ.get(ENV, "") if spec is None else spec)
    for name in names:
        logging.getLogger(name or None).setLevel(logging.DEBUG)
    return names


CAP_PER_MIN = 400          # INFO/DEBUG lines a minute from one logger at most (warnings and errors always pass)
CAP_DEBUG_PER_MIN = 4000   # ... from a logger whose debug lines were asked for (--debug)
CAP_ENV = "BNGE_LOG_CAP"   # "0": no cap (a developer reading one area's flood on purpose)


class RateCap(logging.Filter):
    """One logger's INFO and DEBUG lines held to CAP_PER_MIN a minute, so one area in a loop cannot push the rest of
    a session out of server.log (three runs kept, the players' files are a few hundred KB). The first line after a
    capped minute is preceded by 'log cap: <logger> held back N lines ...'. Per handler (it writes that line)."""

    def __init__(self, handler, per_min=CAP_PER_MIN, debug_per_min=CAP_DEBUG_PER_MIN, clock=time.monotonic):
        super().__init__()
        self.handler, self.per_min, self.debug_per_min, self.clock = handler, per_min, debug_per_min, clock
        self.win = {}

    def filter(self, record):
        if record.levelno >= logging.WARNING:
            return True
        now = self.clock()
        w = self.win.get(record.name)
        if w is None or now - w[0] >= 60.0:
            held = w[2] if w else 0
            self.win[record.name] = w = [now, 0, 0]
            if held:
                note = logging.LogRecord(record.name, logging.WARNING, record.pathname, record.lineno,
                                         "log cap: %s held back %d lines in a minute (over %d a minute; "
                                         + CAP_ENV + "=0 for all)", (record.name, held, self._cap(record)), None)
                note._capnote = True
                try:
                    self.handler.handle(note)
                except Exception:
                    pass
        w[1] += 1
        if w[1] > self._cap(record):
            w[2] += 1
            return False
        return True

    def _cap(self, record):
        lg = logging.getLogger(record.name)
        return self.debug_per_min if lg.getEffectiveLevel() <= logging.DEBUG else self.per_min


def cap_handlers(root=None):
    """Put a RateCap on the root logger's handlers (once each) unless $BNGE_LOG_CAP is 0."""
    if os.environ.get(CAP_ENV, "").strip() == "0":
        return 0
    n = 0
    for h in (root or logging.getLogger()).handlers:
        if not any(isinstance(f, RateCap) for f in h.filters):
            h.addFilter(RateCap(h))
            n += 1
    return n


def configure(verbose=False, debug=None, export=True):
    """The server's logging: INFO (DEBUG with verbose) in FORMAT, plus the debug areas (debug: a spec, else
    $BNGE_DEBUG), and the per-logger cap. export: put the spec in the environment, for the worker processes."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=FORMAT)
    cap_handlers()
    if debug and export:
        os.environ[ENV] = str(debug)
    return apply_debug(debug)


def worker(level=logging.INFO):
    """A worker process's logging (blockworker.init, peerwork, sharehelper): the server's level and format, the
    debug areas it inherited in the environment, and the per-logger cap."""
    logging.basicConfig(level=level, format=FORMAT)
    cap_handlers()
    return apply_debug()


# ------------------------------------------------------------------------------------------- quiet fallbacks
FALLBACK_LINES = 1          # lines per fallback site at INFO; the rest are DEBUG and counted
_fallbacks = {}
_fallback_lock = threading.Lock()


def fallback(logger, what, exc=None):
    """A broad `except` that carries on with a stand-in value (no ground height: not alpine; a cell not readable:
    empty) said so: the first time per site in this process an INFO line with the exception, later ones at DEBUG,
    every one counted for the exit summary (fallback_summary). These sites were silent: a data stage that failed
    looked like one with nothing in it. Never raises."""
    try:
        name = getattr(logger, "name", str(logger))
        key = (name, what)
        with _fallback_lock:
            n = _fallbacks.get(key, 0) + 1
            _fallbacks[key] = n
        err = "" if exc is None else ": %s: %s" % (type(exc).__name__, str(exc)[:200])
        lg = logger if hasattr(logger, "log") else logging.getLogger(name)
        if n <= FALLBACK_LINES:
            lg.info("fallback: %s%s (the next ones at DEBUG, counted at exit)", what, err)
        else:
            lg.debug("fallback: %s%s (%d times)", what, err, n)
    except Exception:
        pass


def fallback_total():
    """How many fallbacks this process took (the health line)."""
    with _fallback_lock:
        return sum(_fallbacks.values())


def fallback_summary():
    """'fallbacks this run: earthd.grading road cell unreadable 41x, ...' or None."""
    with _fallback_lock:
        items = sorted(_fallbacks.items(), key=lambda kv: -kv[1])
    if not items:
        return None
    return "fallbacks this process: " + ", ".join("%s %s %dx" % (k[0], k[1], n) for k, n in items[:20])


def _log_fallbacks():
    line = fallback_summary()
    if line:
        try:
            logging.getLogger("earthd").info("%s (pid %d)", line, os.getpid())
        except Exception:
            pass


atexit.register(_log_fallbacks)


# ---------------------------------------------------------------------------------------------- crash evidence
_hooked = False


LOG_DIR_ENV = "BNGE_LOG_DIR"       # the launcher's logs folder: each process's native-crash dump in its own file there
CRASH_PREFIX = "crash-"
_fault_fh = None
_STARTED = time.time()
_reported = {}        # crash file -> characters already reported (sweep_crashes: a process that carried on)


def _crash_file(role):
    """This process's own file for a native crash's dump (crash-<role>-<pid>.txt in $BNGE_LOG_DIR), opened with a
    header naming the process: every process wrote into server.log, so a dump's 42 stacks said neither which process
    crashed nor when it started. None without the folder (a server run by hand: stderr as before)."""
    global _fault_fh
    d = os.environ.get(LOG_DIR_ENV)
    if not d or not os.path.isdir(d):
        return None
    try:
        path = os.path.join(d, "%s%s-%d.txt" % (CRASH_PREFIX, role, os.getpid()))
        fh = open(path, "w", encoding="utf-8")
        fh.write("process %s pid %d started %s, python %s\n" % (role, os.getpid(),
                 time.strftime("%Y-%m-%d %H:%M:%S"), sys.version.split()[0]))
        fh.flush()
    except OSError:
        return None
    _fault_fh = fh

    def tidy():         # a normal exit: no dump, no file
        try:
            faulthandler.disable()
            fh.close()
            os.remove(path)
        except OSError:
            pass
    atexit.register(tidy)
    return fh


def read_crash(text, name=""):
    """What a crash file says: (headline, dump lines) or None when it holds only its header (the process ended
    without a native crash: killed, or it is still running). The headline names the process, the fault and the
    thread it happened on: with no 'Current thread' in the dump the fault was on a thread Python does not run (the
    native code's own threads: Rust/rayon, DuckDB, numpy's BLAS, a driver). Pure."""
    lines = text.splitlines()
    if len(lines) < 2 or not any(x.strip() for x in lines[1:]):
        return None
    head = lines[0].strip() or name
    fault = next((x.strip() for x in lines[1:] if "fatal" in x.lower() or "exception" in x.lower()
                  or x.startswith("Fatal Python error")), lines[1].strip())
    cur = next((i for i, x in enumerate(lines) if x.startswith("Current thread")), None)
    if cur is not None:
        frame = next((x.strip() for x in lines[cur + 1:cur + 3] if x.strip().startswith("File")), "")
        where = "in Python thread %s, at %s" % (lines[cur].split()[2], frame or "?")
    else:
        where = ("on a thread outside Python (native code's own thread: Rust/rayon, DuckDB, BLAS, a driver); "
                 "the stacks below are the Python threads, waiting")
    n = sum(1 for x in lines if x.startswith(("Thread 0x", "Current thread")))
    return "native crash: %s: %s, %s (%d Python threads)" % (head, fault, where, n), lines[1:]


def sweep_crashes(log_dir=None, log=None):
    """Put the crash files of processes that crashed into the log (log.critical with the dump below it) and remove
    them; the empty ones of killed processes are removed quietly. A file still open by its running process cannot be
    renamed on Windows: left for the next sweep. Returns the headlines."""
    log = log or logging.getLogger("earthd")
    d = log_dir or os.environ.get(LOG_DIR_ENV)
    out = []
    if not d or not os.path.isdir(d):
        return out
    dumps = os.path.join(d, "dumps")    # Windows' minidumps (the launcher's crash_dumps_on): named once each
    try:
        for name in sorted(os.listdir(dumps)):
            p = os.path.join(dumps, name)
            if name.lower().endswith((".dmp", ".mdmp")) and name not in _reported and os.path.getmtime(p) >= _STARTED:
                _reported[name] = 1
                log.critical("native crash: Windows kept a minidump, %s (%.1f MB): open it in WinDbg or Visual Studio "
                             "with the matching build's symbols for the native frames", p, os.path.getsize(p) / 1e6)
                out.append(name)
    except OSError:
        pass
    mine = "-%d.txt" % os.getpid()
    for name in sorted(os.listdir(d)):
        if not name.startswith(CRASH_PREFIX) or not name.endswith(".txt") or name.endswith(mine):
            continue
        path = os.path.join(d, name)
        took = path + ".read"
        try:
            os.replace(path, took)
            with open(took, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            # still open by its process (Windows): faulthandler dumps on an exception the native code may catch
            # itself (its vectored handler sees it first), so a process with a dump can carry on. Read it, report
            # it once, and leave the file for the sweep after the process ends.
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            got = read_crash(text, name)
            if got is not None and _reported.get(name, 0) < len(text):
                _reported[name] = len(text)
                head, dump = got
                head = head.replace("native crash:", "native exception (its process was still running when read: a crash in progress, or one the native code "
                                    "handled itself):", 1)
                log.warning("%s\n%s", head, "\n".join(dump))
                out.append(head)
            continue
        got = read_crash(text, name)
        if got is None:
            # (the header only: still running elsewhere on Linux, or ended without a dump)
            if not _pid_running(name):
                _remove(took)
            else:
                try:
                    os.replace(took, path)
                except OSError:
                    pass
            continue
        head, dump = got
        if _reported.get(name):
            head += " (reported above while it ran; its process has ended since)"
            dump = []
        log.critical("%s\n%s", head, "\n".join(dump)) if dump else log.critical("%s", head)
        out.append(head)
        _remove(took)
    return out


def _pid_running(name):
    """A crash file's process still alive (POSIX; Windows cannot rename an open file, so it never gets here)."""
    if os.name == "nt":
        return False
    try:
        pid = int(name.rsplit("-", 1)[1].split(".")[0])
        os.kill(pid, 0)
        return True
    except (ValueError, IndexError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def install_crash_hooks(log=None, fault_file=None, role="server"):
    """Uncaught exceptions of the main thread and of every other thread into the log, with their tracebacks (they
    went to stderr only, unlabelled and without a time), and faulthandler on fault_file (default stderr: server.log
    under the launcher) for native crashes. Once per process; returns False when already installed."""
    global _hooked
    if _hooked:
        return False
    _hooked = True
    log = log or logging.getLogger("earthd")
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def sys_hook(et, ev, tb):
        if issubclass(et, KeyboardInterrupt):
            return prev_sys(et, ev, tb)
        log.critical("uncaught %s in the main thread: %s", et.__name__, ev, exc_info=(et, ev, tb))

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        log.error("uncaught %s in thread %s: %s", args.exc_type.__name__, name, args.exc_value,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook
    try:
        f = fault_file or _crash_file(role) or sys.stderr
        if f is not None and hasattr(f, "fileno"):
            f.fileno()                  # (no real file under some test runners: then no faulthandler)
            faulthandler.enable(file=f, all_threads=True)
    except (OSError, ValueError, AttributeError, RuntimeError):
        pass
    started = time.time()

    def at_exit():
        try:
            log.info("shutdown: server process ending normally after %.0f s", time.time() - started)
        except Exception:
            pass
    atexit.register(at_exit)
    install_crash_hooks.atexit = at_exit
    hooks = (prev_sys, prev_thread)
    install_crash_hooks.previous = hooks
    return True


def uninstall_crash_hooks():
    """The hooks before install_crash_hooks (tests)."""
    global _hooked
    at_exit = getattr(install_crash_hooks, "atexit", None)
    if at_exit is not None:
        atexit.unregister(at_exit)
    prev = getattr(install_crash_hooks, "previous", None)
    if prev is not None:
        sys.excepthook, threading.excepthook = prev
    _hooked = False


def asyncio_handler(log=None):
    """A loop exception handler (loop.set_exception_handler): a task's exception nobody retrieved, a callback's
    error, into the log with the task's name and traceback (asyncio printed them to stderr, at shutdown at best)."""
    log = log or logging.getLogger("earthd")

    def handler(loop, context):
        exc = context.get("exception")
        what = context.get("message") or "asyncio error"
        task = context.get("task") or context.get("future")
        name = ""
        if task is not None:
            try:
                name = " (task %s)" % task.get_name()
            except AttributeError:
                name = " (%r)" % (task,)
        if exc is not None:
            log.error("%s%s: %s: %s", what, name, type(exc).__name__, exc,
                      exc_info=(type(exc), exc, exc.__traceback__))
        else:
            log.error("%s%s", what, name)
    return handler


# ------------------------------------------------------------------------------------------------ session header
def _memory_gb():
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys / 2 ** 30, m.ullAvailPhys / 2 ** 30
            return None
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        avail = None
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        avail = int(line.split()[1]) / 2 ** 20
                        break
        except OSError:
            pass
        return total, avail
    except (OSError, ValueError, AttributeError):
        return None


def release():
    """The release in the repository's VERSION file ('' when none: a packaged app reads it the same way)."""
    for root in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), getattr(sys, "_MEIPASS", "")):
        try:
            with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            continue
    return ""


def changed_args(args, defaults):
    """{name: value} of the argparse Namespace's values that differ from defaults (a Namespace or dict of the parser's
    defaults), in name order. Pure."""
    d = vars(defaults) if not isinstance(defaults, dict) else defaults
    out = {}
    for k in sorted(vars(args)):
        v = getattr(args, k)
        if k in d and d[k] == v:
            continue
        out[k] = v
    return out


def env_lines(environ=None, prefixes=("BNGE_", "BNE_", "BNGEARTH_")):
    """'NAME=value' of the mod's own environment switches that are set (secrets masked, long values cut). Pure."""
    environ = os.environ if environ is None else environ
    out = []
    for k in sorted(environ):
        if not k.startswith(prefixes):
            continue
        v = str(environ[k])
        if any(w in k.upper() for w in SECRET_WORDS):
            v = "<set>"
        elif len(v) > ENV_VALUE_CHARS:
            v = v[:ENV_VALUE_CHARS] + "..."
        out.append(f"{k}={v}")
    return out


def session_lines(args=None, defaults=None, debug_names=(), environ=None, extra=()):
    """The header lines (without the logger's prefix): the release and Python, the OS and machine, the choices that
    differ from the defaults, the environment switches set, the debug areas on."""
    mem = _memory_gb()
    frozen = " (packaged app)" if getattr(sys, "frozen", False) else ""
    lines = [f"session: BeamNG.Earth {release() or '?'}{frozen}, Python {platform.python_version()} "
             f"({platform.python_implementation()}, {platform.architecture()[0]}), pid {os.getpid()}",
             f"session: {platform.system()} {platform.release()} ({platform.version()[:60]}), {platform.machine()}, "
             f"{os.cpu_count() or '?'} logical CPUs"
             + (f", {mem[0]:.1f} GB RAM" + (f" ({mem[1]:.1f} GB free)" if mem[1] is not None else "") if mem else "")]
    if args is not None and defaults is not None:
        ch = changed_args(args, defaults)
        if ch:
            lines.append("session: options: " + ", ".join(f"{k}={v!r}" for k, v in ch.items()))
    env = env_lines(environ)
    if env:
        lines.append("session: environment: " + ", ".join(env))
    if debug_names:
        lines.append("session: debug logging on for " + ", ".join(n or "everything" for n in debug_names))
    lines.extend("session: " + x for x in extra)
    return lines

