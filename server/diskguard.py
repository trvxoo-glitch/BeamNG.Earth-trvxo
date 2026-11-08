"""Free disk space: the server never fills the player's drive.

The download cache has a size cap (cache.py, 20 GB by default, 0 = unlimited) but no idea how much room the drive has
left: a 20 GB cap on a drive with 12 GB free fills it, and the session folder in the game's user folder (on C: for most
Windows players) grows on the same drive while the player drives (GitHub #599: 9 GB in two days). When a drive fills,
every write fails at once: tiles, the cache index, the game's own files and Windows itself.

DiskGuard watches the volumes the server writes to (the cache, the session folder; one check per volume, however
many of those folders it holds) and keeps a reserve free on each:
  - reserve_bytes(): RESERVE_SHARE of the volume, at least RESERVE_MIN and at most RESERVE_MAX (BNGE_DISK_RESERVE_GB
    sets it outright);
  - below it on the cache's volume, the cache gives back the difference and RESUME_EXTRA more (Cache.free_space: least
    recently used first, whatever the cap; the place round the player stays, cache.PROTECT_S);
  - a volume still short after that (the cache had nothing left to give, or the volume holds only the session folder)
    is reported once (check()'s return value: the server logs it and tells the player), and again only after it was
    back over the reserve and RESUME_EXTRA (no warning a minute while the free space hovers round the line).

Nothing here deletes anything but the cache's own evictable files.
"""
import logging
import os
import shutil

log = logging.getLogger("earthd.disk")

GB = 1e9
RESERVE_MIN = 2 * GB            # bytes kept free at least (Windows updates and the game's own shader cache need room)
RESERVE_MAX = 10 * GB           # ... and at most (a 4 TB drive's 3 % would be 120 GB)
RESERVE_SHARE = 0.03            # of the volume's size
RESUME_EXTRA = 1 * GB           # freed beyond the reserve, and needed beyond it before a volume counts as fine again
CHECK_S = 30.0                  # earthd: how often the volumes are checked
ENV = "BNGE_DISK_RESERVE_GB"


def reserve_bytes(total, env=None):
    """The free space kept on a volume of `total` bytes."""
    env = os.environ if env is None else env
    raw = env.get(ENV)
    if raw not in (None, ""):
        try:
            gb = float(raw)
            if gb >= 0 and gb == gb and gb != float("inf"):
                return int(gb * GB)
        except ValueError:
            pass
        log.warning("disk: %s=%r is not a number of GB: the default reserve", ENV, raw)
    return int(min(RESERVE_MAX, max(RESERVE_MIN, RESERVE_SHARE * max(0, total))))


def _volume(path):
    """An id of path's volume (st_dev), or the path itself when it cannot be read (each its own volume then)."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return ("path", os.path.abspath(path))


class DiskGuard:
    """paths: {label: folder} the server writes into ("cache": the cache root, "session": the session folder);
    cache: the cache.Cache whose files may go (None: report only); usage: shutil.disk_usage (the tests' stand-in)."""

    def __init__(self, paths, cache=None, usage=shutil.disk_usage, env=None):
        self.paths = {k: os.path.abspath(v) for k, v in paths.items() if v}
        self.cache = cache
        self.usage = usage
        self.env = env
        self.low = {}               # volume -> the last report (dict) while that volume is short
        self.freed = 0              # bytes the cache gave back for free space, this run

    def volumes(self):
        """[(volume, [labels], a path on it)] in the order of `paths`, one per volume."""
        out, at = [], {}
        for label, path in self.paths.items():
            v = _volume(path)
            if v in at:
                out[at[v]][1].append(label)
            else:
                at[v] = len(out)
                out.append((v, [label], path))
        return out

    def check(self):
        """One pass over the volumes. Returns the reports of volumes that went short this pass:
        [{"path", "labels", "freeBytes", "reserveBytes", "freedBytes"}] (empty while nothing new)."""
        new = []
        for vol, labels, path in self.volumes():
            try:
                u = self.usage(path)
            except OSError as e:            # (a folder deleted under us, a drive gone: nothing to measure)
                log.debug("disk: %s: %s", path, e)
                continue
            reserve = reserve_bytes(u.total, self.env)
            free, freed = u.free, 0
            if free < reserve and "cache" in labels and self.cache is not None:
                want = reserve - free + RESUME_EXTRA
                try:
                    freed = self.cache.free_space(want)
                except Exception as e:      # (the cache's own trouble must not stop the watch)
                    log.warning("disk: the cache could not free space: %s", e)
                if freed:
                    self.freed += freed
                    log.info("disk: %.1f GB free on %s, under the %.1f GB kept free: the cache gave back %.1f GB",
                             free / GB, path, reserve / GB, freed / GB)
                    try:
                        free = self.usage(path).free
                    except OSError:
                        free += freed
            if free < reserve:
                if vol not in self.low:
                    rep = {"path": path, "labels": list(labels), "freeBytes": int(free), "reserveBytes": int(reserve),
                           "freedBytes": int(freed)}
                    self.low[vol] = rep
                    new.append(rep)
                    log.warning("disk: only %.1f GB free on %s (%s; %.1f GB is kept free): %s", free / GB, path,
                                ", ".join(labels), reserve / GB,
                                "the cache has nothing more it may delete" if "cache" in labels and self.cache
                                else "free some space on that drive")
                else:
                    self.low[vol]["freeBytes"] = int(free)
            elif vol in self.low and free >= reserve + RESUME_EXTRA:
                del self.low[vol]
                log.info("disk: %.1f GB free on %s again", free / GB, path)
        return new

    def reports(self):
        """The volumes short now (a new connection is told about them)."""
        return [dict(r) for r in self.low.values()]
