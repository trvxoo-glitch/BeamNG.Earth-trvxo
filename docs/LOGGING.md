# Logs and debugging

Where BeamNG.Earth writes what it does, what the lines mean, and how to get more detail for one area without
flooding the log. Players send these files with a bug report. Developers read them first when something goes wrong.

## The files

| File | Written by | Where |
| --- | --- | --- |
| `server.log` (+ `.1` .. `.10`: the runs before, 40 MB at most) | the world server and its worker processes, plus the game's `game: ...` lines | `<data dir>/logs/` (Windows `%LOCALAPPDATA%\beamng-earth\logs`, Linux `~/.cache/beamng-earth/logs`) |
| `launcher.log` | the launcher | the same folder |
| `beamng.log` | the game, including the mod's `beamngEarth` lines | the game's user folder (`.../BeamNG.drive/current/beamng.log`) |
| session folder | the tiles, blocks and meshes the server made for the game | the game's user folder, `temp/beamngEarth/` |
| reports | the in-game report tool (Ctrl+Shift+E) and profiler (Ctrl+Shift+Y) | the game's user folder, `beamngEarth/reports/` |
| `crash-<role>-<pid>.txt` | each server process: a native crash's stacks (read into `server.log`, then removed) | the logs folder |
| `dumps/*.dmp` | Windows Error Reporting: a minidump of a crashed server process (the native frames) | the logs folder |
| hitch captures | the mod: the last 10 s of frames around a hitch of 1 s or more (hitchcap.lua) | the game's user folder, `beamngEarth/hitches/` |
| profiler snapshot | the profiler window's Export | the game's user folder, `settings/beamngEarth-profiler.json` |

The mod's own lines go to both `beamng.log` and `server.log`, so `server.log` alone usually tells the whole story. In
`server.log` they start with `game:`.

Every line has the same shape, in the server and in every worker process:

```
2026-11-06 01:05:42,035 INFO earthd.grading: grading: rail crossing at 39.28340,-76.59340 ...
```

The launcher's console echoes only the warnings and errors (`launcher.echo_lines`). Tools that read `server.log`
depend on this format, so it stays as it is.

## What a log says about its session

The first lines of `server.log` (`logsetup.session_lines`) say what made the log:

```
INFO earthd: session: BeamNG.Earth 1.8.1-beta (packaged app), Python 3.12.7 (CPython, 64bit), pid 9120
INFO earthd: session: Windows 10 (10.0.19045), AMD64, 16 logical CPUs, 31.9 GB RAM (18.2 GB free)
INFO earthd: session: options: cells=64, user_dir='C:\\Users\\...\\BeamNG.drive\\current'
INFO earthd: session: environment: BNGE_DEBUG=grading
INFO earthd: session: debug logging on for earthd.grading
INFO earthd: game hello: mod 0.4.0, protocol 4, format 4
```

- `options` lists only the choices that differ from the defaults.
- `environment` lists the mod's own switches (`BNGE_*`, `BNE_*`, `BNGEARTH_*`). Values with KEY, TOKEN, SECRET,
  PASS, AUTH or COOKIE in their names are hidden.
- `game hello` names the mod version that connected. A protocol mismatch says which protocol this server speaks.
- The game also sends its machine lines (`game: machine: ...`: the GPU, the game version and the graphics settings) and
  `perf ...` lines (perflog.lua: hitches, frame-rate drops and the minute line).

## Crashes and stalls

- An uncaught exception in any thread is logged as `uncaught ... in thread <name>` with its traceback. So is an
  error in an asyncio task that nothing awaited. These used to reach stderr only, with no time or name.
- A native crash (Rust, DuckDB, numpy, a driver) writes every thread's stack to the crashing process's own file,
  `logs/crash-<role>-<pid>.txt`, with a header naming the process (server or blockworker) and when it started. The
  server reads these every 20 s and at its start, and the launcher reads them when the server ends. Each one goes into
  `server.log` as one CRITICAL line, with the dump below it:
  `native crash: process blockworker pid 812 started ...: Windows fatal exception: access violation, on a thread
  outside Python (...) (42 Python threads)`. "On a thread outside Python" means the dump has no `Current thread`:
  the fault was on a thread the native code started itself, and the Python stacks below are only the threads that
  were waiting. "In Python thread 0x..., at File ..." names the Python frame that called into the crashing code.
  Before, every process wrote its dump into `server.log` itself, so a dump said neither which process crashed nor
  when. A server run by hand, without the launcher's `BNGE_LOG_DIR`, still writes its dump to stderr.
  On Windows, faulthandler also dumps an exception that the native code may catch itself. If the process carries on,
  the line is a WARNING, `native exception (its process was still running when read ...)`, reported once. If the process then dies, a CRITICAL `native crash` line follows.
- A crash file with only its header line is a process that was killed. A normal exit removes its file. When the
  launcher stops the server (the game closed), it reads the crash files and writes
  `server process exited ... (stopped by the launcher: ...)` itself, because the kill gives the server no time to.
- **Minidumps.** At its start the launcher asks Windows Error Reporting to keep a minidump when one of its processes
  crashes, in `logs/dumps/` (the last 3). This is a per-user setting for this exe's name only (`HKEY_CURRENT_USER\
  Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\<exe>`), and needs no administrator.
  `BNGE_CRASH_DUMPS=0` leaves it alone. A minidump holds the native frames that faulthandler's text cannot: open it in
  WinDbg or Visual Studio with the matching build's `earthfast.pdb`. The server logs `native crash: Windows kept a
  minidump, ...` when one appears, and the exit record names this run's dumps. Only a crash that nothing handled
  makes one, so it also tells a real crash from an exception the native code caught.
- **How a run ended**: see "Reading a log" below.
- A block, road tile or far tile still building after a while logs its worker's stacks (blockworker `_stack_watch`).
  A late heartbeat logs the main thread's stack (`earthd` lag report).

## Debug lines for one area

`-v` turns on every debug line in every area, which is too much to read. Name the areas instead:

```
python server/earthd.py --debug grading,surface
BNGE_DEBUG=grading,surface   (in the environment: the launcher passes it on)
BNGE_SERVER_ENV="BNGE_DEBUG=grading"   (the launcher: for the server alone, not the game)
```

Each area is the logger `earthd.<area>`: grading, surface, roads, blockworker, worker, pools, mapdata, dem, imagery,
net, cache, crossings, vegetation, buildings, colormatch, weather, or any other `earthd.<name>` in the code. `all`
is the same as `-v`. The worker processes inherit the setting, so the block and road tile processes log their debug
lines too.

## How the game ended, and its own errors

- When the game closes, the launcher looks for crash dumps the game left since this start (`*.dmp`, `*.mdmp`, or
  crash zips and texts in the game's user folder and the two folders above it). It also notes the game's exit code
  when the launcher started the game directly. Both go into the exit record:
  `server process exited ... (stopped by the launcher: the game closed; its exit code -1073741819 (0xC0000005); it
  left a crash dump: ...)`. Before, a game crash looked the same as closing it.
- The server follows the game's `beamng.log` (`gamelog.py`, every 2 s, only lines written after it started). Error
  and warning lines that name the mod in their text go into `server.log` as `earthd.gamelog: beamng.log <tag>: ...`,
  with up to 12 traceback lines after an error. That covers a runtime error in one of the mod's extensions (the
  game reports it under its own tag, so modlog.lua cannot) and an engine error about one of the mod's files. The
  mod's own `log()` lines are left out here, because modlog.lua already sent them.
- A Rust panic in the native module logs its backtrace (`RUST_BACKTRACE=1`, set by the launcher unless already set).

## When the game's lines were written

The game's lines are stamped when the server reads them. Each batch now carries the game's clock (`t`, whole
seconds). A batch read 3 s or more after the game sent it says so: `game: perf hitch ... (sent 9 s before the server
read it)`. That happens after a stall on either side, and it means the line's time in `server.log` is late.

## The server's side of a game hitch (loopwatch.py)

The lag-watch thread samples the server's event loop every 0.1 s and keeps the last 90 s: busy or idle, and the call
stack when busy. For a game hitch of 500 ms or more, the next line says what the server's loop was doing during it:

```
game: server during the 1202 ms hitch: event loop busy 80 % (12 of 15 samples): 9x imagery.py:warm:990 < earthd.py:warm_imagery:2739 ...
game: server during the 743 ms hitch: event loop idle (14 samples): the hitch was not the server's
```

A busy loop holds every message the game waits for (the hitch line's `net` part). An idle loop means the cause was in
the game. The window is widened by a second on each side, because the game's clock is in whole seconds.

## Fallbacks: the places that carry on with a stand-in (`fallback: ...`)

Some steps catch any error and carry on with a stand-in: a neighbour cell that cannot be read is left out, a ground
height that is not known means not alpine, a bridge's clearance that is not known means none. These used to be
silent, so a step that failed looked like one with nothing in it. Each such place now logs
`fallback: <what was assumed>: <the exception>` at INFO the first time in each process. Later ones are DEBUG lines
(`--debug <area>`), and a process logs `fallbacks this process: ... 41x, ...` when it ends. New code that catches
broadly and carries on should call `logsetup.fallback(log, "what is assumed", exc)`.

## Size caps

One logger writes at most 400 INFO and DEBUG lines a minute (4000 when its debug lines were asked for). Warnings and
errors always pass. The rest of a capped minute is counted, and the logger's next line comes after
`log cap: earthd.<area> held back N lines in a minute`. Three runs are kept and players send files of a few hundred
KB, so one area stuck in a loop used to push everything else out. `BNGE_LOG_CAP=0` turns the cap off.

## Health, settings and blocks (`earthd.health`, `settings:`, `block ... b<n>:`)

```
INFO earthd.health: health: server 812 MB; pools blocks 2/4 +3 queued, road tile 1/2, ...; workers 9 using 3.1 GB (largest 690 MB); restarts road tile 1; event loop busy 7 %; disk 112 GB free (cache); games 1; fallbacks 4
INFO earthd: settings: graphics: photoPx 2048 (was 1024)
INFO earthd: block (1, 512, 1024) b17: built 2.3 s after its request, committed 0.8 s after it was sent (swap 11 ms)
```

- **health** (health.py), every minute: each process's resident memory (the server's own, the worker processes' sum
  and the largest), each pool's busy workers and queue, the pools made again after a worker died, how much of the
  minute the event loop was busy, the cache folder's free disk, the games connected, the fallbacks taken. A leak
  shows as a rising number across the lines, a stall as a busy share near 100 %, a growing queue before it.
- **settings**: what the player changed on the settings page (and the profiler switch), each time it differs from
  the last on that connection, with its old value. The first one of each kind is logged in full.
- **block pipeline**: each block sent carries an id (`bid`). When the game commits it, the server logs how long it
  took to build after the request and how long it waited for the commit. A long wait is normal for a block sent ahead
  of the car (it waits for the car). The game's own `ground block` line gives its swap or import time.

## The mod's own warnings and errors (`earthd.game.<area>`, modlog.lua)

Every `log('W'|'E', 'beamngEarth...', ...)` the mod writes also goes to `server.log`, at its own level, under the
logger `earthd.game.<area>`. The area is the tag after `beamngEarth.`, or `core` when there is none. So
`--area game.trees` finds the tree code's warnings. Before, these lines were only in `beamng.log`. The same line,
with its numbers made alike, is sent at most 3 times a minute; the next one carries `(+N more like this before)`.
The mod's `D` lines are sent only for the areas the server's `BNGE_DEBUG` names (the server's hello passes it on) or
from the game's console: `beamngEarth_core._modlog.setDebug('roads,trees')` (`'all'` for every area).
The multiplayer modules' `I` lines go too (mpsession, beammp, mpanchor: who joined, which anchor, the plugin's
answers), since a BeamMP problem is read from them.

## Graded roads: humps and rail crossings (`earthd.grading`, gradelog.py)

When a map cell's road plans are made, the grader checks them (`gradelog.report_cell`):

```
INFO earthd.grading: grading: rail crossing at -34.97012,138.66001 (cell 14/14590/9878): road 08b2... at 41.20 m,
     track 08b3... at 41.95 m (road -0.75 m), graded hump 0.48 m +2 m from the track, steepest 16 %
INFO earthd.grading: grading: road 08b2... (cell 14/14590/9878) has a 0.62 m hump over 12 m at -34.97101,138.65873
     (104 m along it), steepest 11 %
```

- **Rail crossings.** At every point where a road's plan crosses a railway bed's plan, the line gives the two
  planned heights there. When the road and the track disagree by 0.15 m or more, the ground is blended between them,
  which makes a hump or dip across the track. That case, or an as-graded hump of 0.2 m or more within 20 m of the
  track, is an INFO line. Crossings that match are DEBUG lines.
- **Humps.** A road's planned profile is checked for its largest rise or dip over the chord of plus or minus 6 m.
  One of 0.5 m or more is an INFO line, at most two per cell, with a count of the rest.
- With `--debug grading`, every road gets one line: its length, steepest grade, and largest hump and where it is.
- Coordinates are `lat,lon` to five decimals (about 1 m). The place search takes them as they are (places.parseLatLon),
  so you can paste one in to teleport there.
- Plans that come from the plan cache (a return visit, or another process's plans) are logged only with
  `--debug grading`, so a cell is not logged once per process. To see the lines for a place you have visited before,
  run with `--debug grading`.

## What the car drives on (`game: ground ...`, `game: wheels ...`; diaglog.lua)

The car's grip comes from the physics block's layers. Each 2 m sample has a TerrainMaterial whose ground model
(ASPHALT_OLD, GRASS, DIRT, ...) sets the grip. The DecalRoads drawn on top have no physics. A paved road that drives
like dirt therefore means one of three things: the block's layer under it was not asphalt, the layer's material did
not drive as its ground model, or the car was on another block. These lines tell them apart:

```
game: ground block 512,1024 swap 11 ms, layers Asphalt=ASPHALT_OLD Grass=GRASS Dirt=DIRT at -34.97012,138.66001, 61 mph
game: ground block 514,1024 import 27 ms, layers ...; partial: built before its map cells (its roads drive as the
      land cover until built again) at ...
game: ground block 516,1024 swap 9 ms, layers ...; WRONG: bngEarthGroundAsphalt drives as DIRT, not ASPHALT_OLD at ...
game: ground issue: surface layers unusable (a mask missing or a material not made): the whole block is the single
      dirt layer at ...
game: wheels: ground 10 -> 15 at -34.97012,138.66001, 61 mph, block 512,1024; last block commit 0.4s ago, last
      collision rebuild 3.1s ago; wheels FL=15/4/0.00 FR=15/4/0.00 RL=15/4/0.00 RR=15/4/0.00
game: ground minute (60s): blocks 14 (12 swaps, 2 imports, 1 partial, slowest 31 ms), collision rebuilds 9
      (2 urgent, slowest 24 ms, longest gap 6.1s), wheels 10 82 %, 15 18 % at ...
```

- **`ground block`**: a block was committed, either swapped in from its `.ter` or imported. The line gives how long
  that took, and each layer with the ground model the server asked for. Each layer is checked against the
  TerrainMaterial the engine actually has. A missing layer, one taken by another kind of object, or one with another
  ground model is listed under `WRONG`. `partial` means the server built the block before its map cells arrived (the
  server logs `block ... built before its map cells were here` and, later, `built again with its roads`). Its roads
  drive as the land cover under them until it is built again. A line is written when the layers change, on a problem,
  and for the first block of a connection. Other commits are counted in the minute line.
- **`ground issue`**: terrain.lua fell back to something. The surface layers were unusable, so the block got the
  single dirt layer, or importMaps failed with its materials.
- **`wheels`**: the ground under the player's wheels changed while driving. The mod asks the vehicle's Lua for each
  wheel's `contactMaterialID1` once a second, above 2 m/s. The numbers are the game's ground model ids, not names. A
  change from the usual paved id to another on a paved road is the event to look for. The ages of the last block
  commit and the last collision rebuild show whether the change came right after a block swap (the block's layers)
  or not (the paint under that spot). A game whose vehicle Lua has no such fields logs `ground probe off` once and
  stops asking.
- **`ground files`** (server): when the wheels change ground, the server says what the block's own files hold under the car (the material there and within 4 m, the asphalt share). Grass under a road means the server wrote the block wrong. Asphalt means the game applied it wrong (the swap). Where the files hold no asphalt under the car, a second line lists the nearest mapped roads within 25 m and whether each is painted into the ground (a path or track has no road spec; one that is all bridge or tunnel there has no ground part). The positions are 4 m samples, so a car at a road's edge reads mixed.
- **`ground minute`**: block commits, collision rebuilds (be:reloadCollision from roads.lua: how many, the slowest,
  the longest gap between two) and the wheels' ground ids by share of the samples.

## Reading a log and sending one (`tools/logdigest.py`, `tools/collect_logs.py`)

```
python3 tools/logdigest.py                              a page-sized summary of the latest server.log
python3 tools/logdigest.py --around 18:01:07 --secs 20  the lines within 20 s of a time
python3 tools/logdigest.py --area grading --level warning
python3 tools/logdigest.py --near -34.9954,138.7003 --radius 200   lines naming a place (any lat,lon in the line)
python3 tools/logdigest.py --grep hitch
python3 tools/collect_logs.py [--user-dir <the game's user folder>] [-o bug.zip]
```

- The summary gives when the run started and ended, the session header, the count per level, warnings and errors
  grouped by shape (numbers and ids made alike) with the first line number, tracebacks, the worst hitches, and the
  notable ground and grading lines. Start there, then use the filters on the line numbers and times it names.
- **How a run ended.** A normal stop logs `shutdown: server process ending normally after N s`. The launcher then adds
  `launcher: server process exited with code N (normal | killed by signal | crashed (Windows status 0x...) | failed)`.
  A log with neither stopped without a trace: a hard kill, a power loss, or a build from before this was added.
- **The bundle** (logtools.collect) holds every server.log run, launcher.log, the crash files and the two newest
  minidumps, the game's beamng.log, the profiler snapshot, the newest hitch captures and in-game reports, app.json,
  a list of the session folder's files, and SUMMARY.txt. Your user name, home folder and the value after any name
  containing key, token, secret, password, cookie or auth are hidden in the text files. Each file is cut to its last
  8 MB. Nothing is sent anywhere: attach the zip yourself. Three ways to make it:
  - in the game: the report window (Ctrl+Shift+E) shows **Add logs** after you save, and the zip goes into that
    report's folder beside issue.md;
  - the packaged app: `BeamNG.Earth.exe --collect-logs [path.zip]` (on the desktop by default);
  - from the code: `python3 tools/collect_logs.py`.

## Profiling: hitch captures, timelines, baselines

- **Hitch captures** (hitchcap.lua). The mod always keeps the last 10 s of frame times (the profiler window does
  this only while it is open), and the mod's parts for each frame of 50 ms or more. A frame of 1 s or more saves,
  2 s later, `beamngEarth/hitches/hitch_<date>_<time>.json`: every frame of the last 10 s, the hitch's engine
  subsystems and GPU time, where the player was and what the mod was doing. At most one a minute, the newest 10
  kept, and `server.log` gets `perf capture of the N ms hitch saved: ...`.
- **GPU time** is in each hitch line (`| gpu 14.2 ms (CPU waited 9.8 ms for it)`) when the engine reports it. So a
  hitch "mostly outside the mod and engine timers" can be told apart as the GPU's.
- **Timelines** (`tools/profile_trace.py`, tracefmt.py): `python3 tools/profile_trace.py server.log hitch_....json
  beamngEarth-profiler.json -o trace.json`, then open trace.json in https://ui.perfetto.dev or chrome://tracing.
  From server.log: the game's hitches, the event loop's stalls, each block's build and its wait for the commit,
  warnings and errors as markers, memory from the health lines as a graph. From a capture: every frame, with the
  slow ones named by their biggest mod part. From the profiler's Export: every worker job on its pool's track, with
  its queue wait before it and its steps.
- **Baselines** (`tools/perfbase.py`, perfbase.py): `python3 tools/perfbase.py server.log --save base.json` keeps a
  run's numbers: fps and 1 % low, hitches a minute and the worst, event loop stall time, block build median and
  95th percentile, the server's memory. Later, `--compare base.json` prints each one, `WORSE` past its threshold,
  and exits with status 1. Drive the same route on both builds for a fair comparison (`tools/autodrive_perf.py
  --from ... --to ...`).
- The live profiler window (docs/RUNTIME_PROFILER.md) is unchanged: its Export is what profile_trace.py reads.

## For developers adding code

- Log through `logging.getLogger("earthd.<area>")` in the server, and `log('I'|'W'|'E'|'D', 'beamngEarth', ...)` in
  the mod. Never `print` in a server module: `server.log` is the server's stdout, and a print has no time, level or
  area. `server/tests/test_logcalls.py` fails on a new print and on a log call whose placeholders don't match its
  arguments (logging only reports that when the line is written, and drops the line).
- A broad `except` that carries on calls `logsetup.fallback(log, "what is assumed", exc)`.
- A new message type is added to `server/tests/test_protocol_agreement.py` on its side.

## Where to look first

| What went wrong | Start with |
| --- | --- |
| The server stopped or the game lost it | the end of the summary, then `--around` its last time; `uncaught` and `Current thread` lines |
| A stall or hitch in the game | `--grep hitch` (cause and place), then `--around` that time for the server's lines |
| A road is the wrong height or has a hump | `--area grading`, `--near` the place; add `--debug grading` to see revisited cells |
| A paved road drives like dirt | the `ground block` and `wheels:` lines near the place, then `ground files` and the nearest-roads line |
| Something slow on the server | `slow code` and lag reports; `--debug` the area it names |
| A message the game or server ignores | `game hello` (versions), then warnings by how often |
| The game crashed | the exit record (`stopped by the launcher: the game closed; ... crash dump`), then `earthd.gamelog` errors before it |
| A worker process died | `native crash` (which process, which thread), `dumps/` (a minidump), `pool made again` |
| Memory grows or the server slows over a session | the `health:` lines one after another; `tools/profile_trace.py` draws them |
| A block took long to appear | `terrain_ready ... after` and its `block ... b<n>:` line; the game's `ground block` line |
| Something got slower in a new build | `tools/perfbase.py --compare` on the same route |
| Multiplayer | `earthd.game.mpsession` lines, `share:` lines |

## BeamNG's own tools while developing

- The console (`~`) shows `beamng.log` live. `dump(obj)` prints a table. Our extensions are `beamngEarth_core`,
  `beamngEarth_roads`, and so on: `beamngEarth_roads.status()` gives the road queue.
- Mod Lua logs with `log('I'|'W'|'E'|'D', 'beamngEarth', text)`.
- Vehicle Lua is a separate Lua state per vehicle. Code runs there through `veh:queueLuaCommand(...)` and answers
  back with `obj:queueGameEngineLua(...)` (diaglog's wheels probe, mpsession).
- The in-game dev tools (docs/DEVTOOLS.md: LOD, loading and collision overlays), the runtime profiler
  (docs/RUNTIME_PROFILER.md) and the report tool (Ctrl+Shift+E) are in the BeamNG.Earth tab of the pause menu.
- Offline: `python tools/run_lua_tests.py` (the mod's Lua against stubs) and `python tools/run_tests.py` (the server).
