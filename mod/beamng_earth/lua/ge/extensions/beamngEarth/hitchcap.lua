-- The last seconds of frames, kept always and saved when a big hitch happens (the profiler's window holds them only
-- while it is open, so a player's one bad hitch was never captured). Each frame's time goes into a ring (numbers
-- only); a slow frame also keeps the mod's parts in it. A frame of CAPTURE_MS or more saves, AFTER_S later (so the
-- frames after it are in too), /beamngEarth/hitches/hitch_<date>_<time>.json: every frame of the last KEEP_S with
-- its time and the mod's parts of the slow ones, the hitch's engine subsystems, GPU time, where the player was and
-- what the mod was doing. At most one capture every GAP_S, the newest KEEP_FILES kept. tools/profile_trace.py turns
-- a capture into a timeline (Perfetto / chrome://tracing). The collect_logs bundle takes the newest ones.
local M = {}

M.CAPTURE_MS = 1000
M.AFTER_S = 2
M.KEEP_S = 10
M.RING = 1200              -- frames kept (KEEP_S at 120 fps)
M.SLOW_MS = 50             -- frames this slow keep their mod parts
M.GAP_S = 60
M.KEEP_FILES = 10
M.DIR = '/beamngEarth/hitches/'

local ringT, ringMs, ringMod, at, n = {}, {}, {}, 0, 0
local pending, lastSaveAt = nil, -math.huge
M.write = function(path, data) return jsonWriteFile(path, data, true) end
M.list = function()
  local ok, files = pcall(function() return FS:findFiles(M.DIR, '*.json', 0, false, false) end)
  return ok and files or {}
end
M.remove = function(path) pcall(function() FS:removeFile(path) end) end
M.stamp = function() return os.date('%Y%m%d_%H%M%S') end

function M.reset()
  ringT, ringMs, ringMod, at, n = {}, {}, {}, 0, 0
  pending, lastSaveAt = nil, -math.huge
end

local function copyMarks(marks)
  if type(marks) ~= 'table' then return nil end
  local out, any = {}, false
  for k, v in pairs(marks) do
    if type(v) == 'number' and v >= 0.5 then out[k] = math.floor(v * 10 + 0.5) / 10; any = true end
  end
  return any and out or nil
end

-- One frame: its time ms, the clock now (s), the mod's parts in it (marks), the hitch record (core's noteHitch) when
-- it was a hitch, and a function for the context (perfContext). Returns a line for the log when a capture was saved.
function M.frame(ms, now, marks, hitch, ctx)
  if type(ms) ~= 'number' or ms ~= ms or ms <= 0 then return nil end
  at = at % M.RING + 1
  ringT[at], ringMs[at] = now, ms
  ringMod[at] = ms >= M.SLOW_MS and copyMarks(marks) or nil
  n = math.min(n + 1, M.RING)
  if ms >= M.CAPTURE_MS and not pending and now - lastSaveAt >= M.GAP_S then
    local c = ctx and ctx() or {}
    pending = { saveAt = now + M.AFTER_S, hitch = hitch, ms = ms, at = now, where = c }
  end
  if pending and now >= pending.saveAt then
    local p = pending
    pending, lastSaveAt = nil, now
    return M.save(p, now)
  end
  return nil
end

function M.frames(since)
  local out = {}
  for i = n - 1, 0, -1 do
    local k = (at - i - 1) % M.RING + 1
    if ringT[k] and ringT[k] >= since then
      out[#out + 1] = { t = math.floor(ringT[k] * 1000 + 0.5) / 1000, ms = math.floor(ringMs[k] * 100 + 0.5) / 100,
        mod = ringMod[k] }
    end
  end
  return out
end

function M.save(p, now)
  local name = M.DIR .. 'hitch_' .. M.stamp() .. '.json'
  local w = p.where or {}
  local data = { kind = 'beamngEarth hitch capture', version = 1, hitchMs = math.floor(p.ms + 0.5), hitchAt = p.at,
    savedAt = now, frames = M.frames(now - M.KEEP_S), hitch = p.hitch,
    where = { lat = w.lat, lon = w.lon, mph = w.mph, block = w.block, state = w.state, doing = w.doing, extra = w.extra } }
  local ok = pcall(M.write, name, data)
  if not ok then return 'perf capture: could not be saved (' .. name .. ')' end
  local files = M.list()
  table.sort(files)
  for i = 1, #files - M.KEEP_FILES do M.remove(files[i]) end
  return string.format('perf capture of the %d ms hitch saved: %s (%d frames)', data.hitchMs, name, #data.frames)
end

return M
