"""The server's health every minute in server.log ('health: ...'): each process's memory, the worker pools' queues
and restarts, how busy the event loop was, the cache folder's free disk, the game connections. Before, memory was
logged only with BNGE_TRACEMALLOC and the rest only when it had already gone wrong, so a log could not show a leak
building or a queue growing before a stall. A line is written at most every EVERY_S, and when nothing but the clock
changed it still is (its absence would mean the server was stuck). Pure but for the process and disk reads.
"""
import os
import shutil
import sys
import time

EVERY_S = 60.0
_RSS_CACHE = {}


def rss_mb(pid=None):
    """A process's resident memory in MB (this one by default), or None when it cannot be read."""
    pid = pid or os.getpid()
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.OpenProcess(0x1000 | 0x0010, False, int(pid))     # QUERY_LIMITED_INFORMATION | VM_READ
            if not h:
                return None
            try:
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                if not k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                    return None
                return pmc.WorkingSetSize / 1e6
            finally:
                k32.CloseHandle(h)
        except (OSError, AttributeError, ValueError):
            return None
    try:
        with open(f"/proc/{int(pid)}/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _gb(mb):
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"


def pools_part(pools):
    """'blocks 2/4 queued 3, road tile 0/2, ...; workers 6 using 3.1 GB (largest 690 MB); restarts: road tile 1'."""
    busy, mem, restarts, n = [], [], [], 0
    for p in pools:
        name = getattr(p, "name", "?")
        inflight, workers = getattr(p, "inflight", 0), getattr(p, "workers", 1)
        low = len(getattr(p, "_low", ()) or ())
        busy.append(f"{name} {min(inflight, workers)}/{workers}" + (f" +{inflight - workers + low} queued"
                                                                     if inflight + low > workers else ""))
        for pid in (p.pids() if hasattr(p, "pids") else []):
            r = rss_mb(pid)
            if r is not None:
                mem.append(r)
            n += 1
        if getattr(p, "respawns", 0):
            restarts.append(f"{name} {p.respawns}")
    out = "pools " + (", ".join(busy) if busy else "none")
    if n:
        out += "; workers %d using %s%s" % (n, _gb(sum(mem)) if mem else "?",
                                              f" (largest {_gb(max(mem))})" if mem else "")
    if restarts:
        out += "; restarts " + ", ".join(restarts)
    return out


def line(pools=(), cache_dir=None, ring=None, window_s=EVERY_S, connections=None, extra=(), now=None):
    """One health line (without its 'health: ' prefix). Pure but for the reads."""
    now = time.time() if now is None else now
    parts = []
    r = rss_mb()
    parts.append("server %s" % (_gb(r) if r is not None else "?"))
    parts.append(pools_part(pools))
    if ring is not None:
        got = ring.window(now - window_s, now)
        if got:
            busy = sum(1 for s in got if s[1])
            parts.append("event loop busy %.0f %%" % (100.0 * busy / len(got)))
    if cache_dir:
        try:
            parts.append("disk %.0f GB free (cache)" % (shutil.disk_usage(cache_dir).free / 1e9))
        except OSError:
            pass
    if connections is not None:
        parts.append(f"games {connections}")
    parts.extend(x for x in extra if x)
    return "; ".join(parts)
