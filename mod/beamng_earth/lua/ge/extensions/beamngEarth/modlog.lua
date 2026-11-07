-- The mod's own warnings and errors into the server's log (server.log: the one file players send), and debug lines
-- per area. Every log('W'|'E', 'beamngEarth...', text) the mod writes went to beamng.log alone, so server.log, read
-- first, did not show the mod failing; and the mod had no way to turn on one area's detail.
--
--   modlog.wrap(log)      the game's log function with the forwarding added (core.lua sets the global once)
--   modlog.setDebug(spec) 'trees,roads' (or 'all'): D lines of those areas forwarded too (the server's hello sends
--                         its BNGE_DEBUG; the console: beamngEarth_core._modlog.setDebug('roads'))
--   modlog.drain()        the lines for the server: { {l = 'W', a = 'trees', m = 'text'}, ... }
--
-- An area is the tag after 'beamngEarth.' ('beamngEarth' alone is 'core'). The same line (its numbers made alike)
-- more than REPEAT_MAX times in REPEAT_S is counted, not sent, and the count goes with its next line.
local M = {}

M.REPEAT_MAX = 3
-- areas whose I lines go to the server too: few, and the ones a multiplayer problem is read from (who joined, which
-- anchor, the plugin's answers); the rest of the mod's I lines stay in beamng.log
M.INFO_AREAS = { mpsession = true, beammp = true, mpanchor = true }
M.REPEAT_S = 60
M.MAX_QUEUE = 60
M.MAX_CHARS = 600

local q, seen, debugAll, debugAreas = {}, {}, false, {}
M.now = function() return os.clock() end

function M.reset() q, seen, debugAll, debugAreas = {}, {}, false, {} end

function M.area(tag)
  if type(tag) ~= 'string' or tag:sub(1, 11) ~= 'beamngEarth' then return nil end
  -- (the game's log names the extension first: 'beamngEarth_farfield.beamngEarth' is farfield)
  local a = tag:match('^beamngEarth[%._]([%w]+)')
  return a or 'core'
end

function M.setDebug(spec)
  debugAll, debugAreas = false, {}
  for part in tostring(spec or ''):gmatch('[^,; ]+') do
    part = part:lower()
    if part == 'all' or part == '*' then debugAll = true
    else debugAreas[(part:gsub('^game%.', ''))] = true end
  end
end

function M.debugOn(area) return debugAll or (area ~= nil and debugAreas[area] == true) end

local function shape(s) return (s:gsub('%-?%d+%.?%d*', '#')):sub(1, 120) end

function M.note(level, tag, text)
  local area = M.area(tag)
  if not area then return end
  if level ~= 'W' and level ~= 'E' and not (level == 'D' and M.debugOn(area))
    and not (level == 'I' and M.INFO_AREAS[area]) then return end
  text = tostring(text or ''):gsub('\n', ' ')
  if #text > M.MAX_CHARS then text = text:sub(1, M.MAX_CHARS) .. '...' end
  local now = M.now()
  local key = level .. area .. shape(text)
  local s = seen[key]
  if s and now - s.at < M.REPEAT_S then
    s.n = s.n + 1
    if s.n > M.REPEAT_MAX then s.skipped = s.skipped + 1; return end
  else
    local skipped = s and s.skipped or 0
    s = { at = now, n = 1, skipped = 0 }
    seen[key] = s
    if skipped > 0 then text = text .. ' (+' .. skipped .. ' more like this before)' end
  end
  if s.n == M.REPEAT_MAX then text = text .. ' (repeating: the next ones in ' .. M.REPEAT_S .. ' s counted)' end
  q[#q + 1] = { l = level, a = area, m = text }
  if #q > M.MAX_QUEUE then table.remove(q, 1) end
end

function M.drain()
  local out = q
  q = {}
  return out
end

-- The wrapper calls whichever modlog is loaded now (_G.__beamngEarthLogNote): an extension reload loads a new
-- modlog, and the global log is wrapped only once (install).
function M.wrap(orig)
  return function(level, tag, text, ...)
    local r = orig(level, tag, text, ...)
    local note = rawget(_G, '__beamngEarthLogNote')
    if note then pcall(note, level, tag, text) end
    return r
  end
end

function M.install()
  rawset(_G, '__beamngEarthLogNote', M.note)
  if type(rawget(_G, 'log')) == 'function' and not rawget(_G, '__beamngEarthLogWrapped') then
    rawset(_G, '__beamngEarthLogWrapped', true)
    rawset(_G, 'log', M.wrap(rawget(_G, 'log')))
  end
end

return M
