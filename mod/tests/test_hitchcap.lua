-- luajit mod/tests/test_hitchcap.lua  (the last seconds of frames, saved on a big hitch)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local H = require('beamngEarth/hitchcap')
local written, removed, files = {}, {}, {}
H.write = function(path, data) written[#written + 1] = { path = path, data = data }; files[#files + 1] = path; return true end
H.list = function() return files end
H.remove = function(p) removed[#removed + 1] = p end
local stampN = 0
H.stamp = function() stampN = stampN + 1; return string.format('2026_%03d', stampN) end
H.reset()
local t = 0
local function run(frames, ms, marks, hitch)
  local line
  for _ = 1, frames do t = t + ms / 1000; line = H.frame(ms, t, marks, hitch, function() return { lat = 1, lon = 2, doing = 'far field loading' } end) or line end
  return line
end
run(600, 16)
T.check(#written == 0, 'no hitch: nothing saved')
T.check(run(1, 1500, { roads = 40, sea = 3 }, { ms = 1500, engine = { Ticking = 1400 }, gpu = 4 }) == nil, 'saved later, not at once')
local line = run(200, 16)
T.check(#written == 1 and line and line:find('perf capture of the 1500 ms hitch saved', 1, true), 'saved after AFTER_S: ' .. tostring(line))
local d = written[1].data
T.check(d.hitchMs == 1500 and d.where.doing == 'far field loading' and d.hitch.engine.Ticking == 1400, 'what and where')
local slow, last
for _, f in ipairs(d.frames) do if f.ms >= 1000 then slow = f end; last = f end
T.check(slow and slow.mod and slow.mod.roads == 40 and last.t > slow.t, 'the hitch frame with its parts, and the frames after it')
T.check(d.frames[1].t >= d.savedAt - H.KEEP_S, 'the last KEEP_S only')
run(1, 2000)
run(300, 16)
T.check(#written == 1, 'one capture per GAP_S')
t = t + H.GAP_S
for i = 1, 12 do run(1, 1200); run(200, 16); t = t + H.GAP_S end
T.check(#written == 13 and #removed >= 3, 'older files removed past KEEP_FILES: ' .. #removed)
T.check(H.frame(-1, t) == nil and H.frame(0 / 0, t) == nil, 'bad frame times ignored')
T.done('test_hitchcap')
