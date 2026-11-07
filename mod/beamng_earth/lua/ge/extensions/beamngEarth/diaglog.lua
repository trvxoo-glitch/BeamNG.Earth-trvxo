-- The mod's diagnostics log (the user, 2026-11-06: "improve the logging and debug system, expand the logging and what
-- it logs"). Lines for the world server's log (server.log, the file players send, as "game: ...") and the game's
-- beamng.log, about what the performance log (perflog.lua) does not cover: what the car drives on.
--
-- Paved roads that drove like dirt (Adelaide, 2026-11-03) could not be traced: nothing logged which ground materials
-- the block had when the car was on it, when its collision came, or what the tyres touched. These lines say it:
--   ground block   a block committed (swap: the .ter swapped in, import: importMaps): how long it took, its layers
--                  and each layer's ground model as the engine has it (a layer whose TerrainMaterial is missing or
--                  has another ground model is named: the engine draws and drives it as something else), and
--                  "partial" when the server built it before its map cells came (its roads drive as the land cover
--                  under them until it is built again). Logged when the materials change, on a problem, and the
--                  first block of a connection; the rest are counted into the minute line.
--   ground issue   a fallback in terrain.lua (the layers unusable: the single dirt layer; importMaps without its
--                  material), with the reason.
--   wheels         the ground the player's wheels touch (vehicle Lua: each wheel's contactMaterialID1, the ground
--                  model's id) changed while driving: from what to what, where, how fast, and how long since the last
--                  block commit and collision rebuild (a road that turns to dirt right after a block swap is the
--                  block's layers; one that does on a block committed long ago is its paint).
--   ground minute  every SUMMARY_S: the block commits (swaps, imports, slowest), the collision rebuilds (count,
--                  slowest, the longest gap between two), the wheels' grounds by share of the samples.
-- Pure but for the clock and the callers' data: core.lua feeds it, sends the lines (net 'diag_log') and asks the
-- vehicle for its wheels (M.WHEELS_CMD, answered into M.onWheels).
local M = {}

M.SUMMARY_S = 60
M.WHEEL_EVERY_S = 1.0       -- the wheels sampled this often while the car moves
M.WHEEL_MIN_MS = 2.0        -- m/s: slower, no samples (parked, held, a menu)
M.WHEEL_LINE_GAP_S = 3.0    -- a wheels line at most this often (the changes between are counted into the next)
M.MAX_QUEUE = 40            -- lines kept for the server at most (the oldest go)
M.MAX_MATERIALS = 16        -- layers named in a ground block line at most

local q = {}                -- lines not yet taken (M.drain)
local probes = {}           -- { from, to } ground changes for the server's ground_probe (M.takeProbes)
local sumAt, blocks, rebuilds, wheelN, wheelBy
local lastBlockAt, lastRebuildAt, lastSig, firstBlock
local wheelAt, wheelLast, wheelLineAt, wheelSkipped, wheelOff
local ctxFn

local function r0(v) return math.floor(v + 0.5) end
local function r1(v) return math.floor(v * 10 + 0.5) / 10 end

function M.reset()
  q = {}
  probes = {}
  sumAt = nil
  blocks = { n = 0, swap = 0, import = 0, slowMs = 0, partial = 0 }
  rebuilds = { n = 0, slowMs = 0, gapMs = 0, urgent = 0 }
  wheelN, wheelBy = 0, {}
  lastBlockAt, lastRebuildAt, lastSig, firstBlock = nil, nil, nil, true
  wheelAt, wheelLast, wheelLineAt, wheelSkipped, wheelOff = nil, nil, -math.huge, 0, nil
end
M.reset()

-- A new connection: its first block gets its line again (the server's log is a new file after a restart).
function M.newConnection() firstBlock = true; lastSig = nil end

-- The ground changes since the last call, { from = '10', to = '20' } each: core.lua sends them with the car's place and
-- block for the server to say what the block's files hold there (ground_probe).
function M.takeProbes()
  local out = probes
  probes = {}
  return out
end

-- The caller's context for the lines: ctx() -> {lat, lon, mph, block = 'cx,cy'} (core's perfContext).
function M.setContext(fn) ctxFn = fn end

local function push(line)
  q[#q + 1] = line
  while #q > M.MAX_QUEUE do table.remove(q, 1) end
end

-- The lines since the last call (for the server and beamng.log).
function M.drain()
  local out = q
  q = {}
  return out
end

local function where(noBlock)
  local c = (ctxFn and ctxFn()) or {}
  local s = {}
  if c.lat and c.lon then s[#s + 1] = string.format('at %.5f,%.5f', c.lat, c.lon) end
  if c.mph then s[#s + 1] = string.format('%d mph', r0(c.mph)) end
  if c.block and not noBlock then s[#s + 1] = 'block ' .. c.block end   -- (a commit's line names its own block)
  return table.concat(s, ', ')
end

local function ago(now, t)
  if not t then return 'none yet' end
  return string.format('%.1fs ago', now - t)
end

-- A block commit. b = { method = 'swap' | 'import', ms, cx, cy, partial, materials = { {name, wanted gm} ... } (the
-- server's), actual = { [name] = { gm = engine's groundmodelName or nil, cls = class or nil } } (terrain.slotReport),
-- issues = { text, ... } (terrain.takeIssues) }; now: s.
function M.blockCommit(b, now)
  blocks.n = blocks.n + 1
  if b.method == 'swap' then blocks.swap = blocks.swap + 1 else blocks.import = blocks.import + 1 end
  if (b.ms or 0) > blocks.slowMs then blocks.slowMs = b.ms or 0 end
  if b.partial then blocks.partial = blocks.partial + 1 end
  lastBlockAt = now
  local mats, bad = {}, {}
  for i, nm in ipairs(b.materials or {}) do
    local name, want = nm[1], nm[2]
    local a = (b.actual or {})[name]
    local got = a and a.gm
    local item = tostring(name):gsub('^bngEarthGround', ''):gsub('^beamngEarth', '') .. '=' .. tostring(want)
    if not a or (a.cls and a.cls ~= 'TerrainMaterial') then
      bad[#bad + 1] = string.format('%s: %s', tostring(name), a and ('a ' .. tostring(a.cls) .. ', not a TerrainMaterial')
        or 'no TerrainMaterial')
    elseif got ~= nil and want ~= nil and tostring(got) ~= tostring(want) then
      bad[#bad + 1] = string.format('%s drives as %s, not %s', tostring(name), tostring(got), tostring(want))
    end
    if i <= M.MAX_MATERIALS then mats[#mats + 1] = item end
  end
  if #(b.materials or {}) > M.MAX_MATERIALS then mats[#mats + 1] = '+' .. (#b.materials - M.MAX_MATERIALS) end
  local sig = table.concat(mats, ' ') .. (b.partial and ' partial' or '')
  local issues = b.issues or {}
  if firstBlock or sig ~= lastSig or #bad > 0 or #issues > 0 then
    firstBlock = false
    lastSig = sig
    local line = string.format('ground block %s,%s %s %d ms, %s', tostring(b.cx), tostring(b.cy), tostring(b.method),
      r0(b.ms or 0), #mats > 0 and ('layers ' .. table.concat(mats, ' ')) or 'the single dirt layer (no surface layers)')
    if b.partial then line = line .. '; partial: built before its map cells (its roads drive as the land cover until built again)' end
    if #bad > 0 then line = line .. '; WRONG: ' .. table.concat(bad, '; ') end
    push(line .. ' ' .. where(true))
  end
  for _, s in ipairs(issues) do push('ground issue: ' .. tostring(s) .. ' ' .. where(true)) end
end

-- A collision rebuild (roads.lua's be:reloadCollision): ms it took, gapMs since the one before, urgent (a collider the
-- car reaches soon).
function M.collisionRebuild(ms, gapMs, urgent, now)
  rebuilds.n = rebuilds.n + 1
  if (ms or 0) > rebuilds.slowMs then rebuilds.slowMs = ms end
  if (gapMs or 0) > rebuilds.gapMs then rebuilds.gapMs = gapMs end
  if urgent then rebuilds.urgent = rebuilds.urgent + 1 end
  lastRebuildAt = now
end

-- The vehicle Lua command asking the wheels' ground (its answer: M.onWheels(vehicle id, text)). Each wheel
-- "name=material id/second material id/contact depth", '' when none touches, 'none' when this game's vehicle Lua has
-- no such fields (the probe then stops), 'error ...' on an error.
M.WHEELS_CMD = [[
local ok, r = pcall(function()
  local src = wheels and (wheels.wheelRotators or wheels.wheels)
  if type(src) ~= 'table' then return 'none' end
  local out, any = {}, false
  for k, wd in pairs(src) do
    if type(wd) == 'table' and type(wd.contactMaterialID1) == 'number' then
      any = true
      out[#out + 1] = string.format('%s=%d/%d/%.2f', tostring(wd.name or k), wd.contactMaterialID1,
        tonumber(wd.contactMaterialID2) or -1, tonumber(wd.contactDepth) or 0)
    end
  end
  if not any then return 'none' end
  table.sort(out)
  return table.concat(out, ' ')
end)
obj:queueGameEngineLua(string.format("local m = package.loaded['beamngEarth/diaglog'] if m then m.onWheels(%d, %q) end",
  obj:getID(), ok and tostring(r) or ('error ' .. tostring(r))))
]]

-- Whether to ask the wheels now (mps: the car's speed); the caller then queues WHEELS_CMD on the player's vehicle.
function M.wantWheels(now, mps)
  if wheelOff or (mps or 0) < M.WHEEL_MIN_MS then return false end
  if wheelAt and now - wheelAt < M.WHEEL_EVERY_S then return false end
  wheelAt = now
  return true
end

-- "FL=10/4/0.00 FR=..." -> the touching wheels' ground ids (a wheel in the air, id < 0, left out), and their summary
-- "10" or "10 x2, 15 x2" (most wheels first). Pure.
function M.parseWheels(text)
  local ids, n = {}, 0
  for name, id in tostring(text or ''):gmatch('([^%s=]+)=(%-?%d+)/') do
    id = tonumber(id)
    if id and id >= 0 then ids[id] = (ids[id] or 0) + 1; n = n + 1 end
  end
  local arr = {}
  for id, c in pairs(ids) do arr[#arr + 1] = { id, c } end
  table.sort(arr, function(a, b) if a[2] ~= b[2] then return a[2] > b[2] end return a[1] < b[1] end)
  local parts = {}
  for _, a in ipairs(arr) do parts[#parts + 1] = (#arr == 1) and tostring(a[1]) or (a[1] .. ' x' .. a[2]) end
  return table.concat(parts, ', '), n, arr[1] and arr[1][1] or nil
end

-- The vehicle's answer (WHEELS_CMD). now: s (the caller's clock: M.now when not given).
function M.onWheels(vid, text, now)
  now = now or (M.now and M.now()) or 0
  text = tostring(text or '')
  if text == 'none' or text:find('^error') then
    if not wheelOff then
      wheelOff = text
      push('wheels: ground probe off (' .. (text == 'none' and "this game's vehicle Lua has no wheel contact materials"
        or text) .. ')')
    end
    return
  end
  local summary, n, main = M.parseWheels(text)
  if n == 0 then return end         -- (all in the air)
  wheelN = wheelN + 1
  wheelBy[main] = (wheelBy[main] or 0) + 1
  if wheelLast == nil then wheelLast = summary; return end
  if summary == wheelLast then return end
  local from = wheelLast
  wheelLast = summary
  if now - wheelLineAt < M.WHEEL_LINE_GAP_S then wheelSkipped = wheelSkipped + 1; return end
  wheelLineAt = now
  local more = wheelSkipped > 0 and string.format(' (+%d changes since the last line)', wheelSkipped) or ''
  wheelSkipped = 0
  if #probes < 4 then probes[#probes + 1] = { from = from, to = summary } end
  push(string.format('wheels: ground %s -> %s %s; last block commit %s, last collision rebuild %s; wheels %s%s',
    from, summary, where(), ago(now, lastBlockAt), ago(now, lastRebuildAt), text, more))
end

-- Every frame (now: s): the minute line when due.
function M.tick(now)
  if not sumAt then sumAt = now; return end
  if now - sumAt < M.SUMMARY_S then return end
  local span = now - sumAt
  sumAt = now
  if blocks.n == 0 and rebuilds.n == 0 and wheelN == 0 then return end
  local w = {}
  local arr = {}
  for id, c in pairs(wheelBy) do arr[#arr + 1] = { id, c } end
  table.sort(arr, function(a, b) return a[2] > b[2] end)
  for i = 1, math.min(4, #arr) do w[#w + 1] = string.format('%s %d %%', tostring(arr[i][1]), r0(100 * arr[i][2] / wheelN)) end
  push(string.format('ground minute (%ds): blocks %d (%d swaps, %d imports, %d partial, slowest %d ms), collision ' ..
    'rebuilds %d (%d urgent, slowest %s ms, longest gap %.1fs), wheels %s %s', r0(span), blocks.n, blocks.swap,
    blocks.import, blocks.partial, r0(blocks.slowMs), rebuilds.n, rebuilds.urgent, r1(rebuilds.slowMs),
    rebuilds.gapMs / 1000, #w > 0 and table.concat(w, ', ') or 'not sampled', where()))
  blocks = { n = 0, swap = 0, import = 0, slowMs = 0, partial = 0 }
  rebuilds = { n = 0, slowMs = 0, gapMs = 0, urgent = 0 }
  wheelN, wheelBy = 0, {}
end

return M
