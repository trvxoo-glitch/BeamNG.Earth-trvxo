-- luajit mod/tests/test_diaglog.lua  (the diagnostics log: the ground's materials, collision, the wheels' ground)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local D = require('beamngEarth/diaglog')
D.setContext(function() return { lat = -34.97012, lon = 138.66001, mph = 61, block = '512,1024' } end)

local function has(lines, pat)
  for _, l in ipairs(lines) do if l:find(pat, 1, true) then return l end end
  return nil
end

local MATS = { { 'bngEarthGroundAsphalt', 'ASPHALT_OLD' }, { 'bngEarthGroundGrass', 'GRASS' }, { 'beamngEarthDirt', 'DIRT' } }
local OK = { bngEarthGroundAsphalt = { cls = 'TerrainMaterial', gm = 'ASPHALT_OLD' },
  bngEarthGroundGrass = { cls = 'TerrainMaterial', gm = 'GRASS' }, beamngEarthDirt = { cls = 'TerrainMaterial', gm = 'DIRT' } }

-- block commits
D.reset()
D.blockCommit({ method = 'swap', ms = 12.4, cx = 512, cy = 1024, materials = MATS, actual = OK }, 1)
local l = D.drain()
T.check(#l == 1 and l[1]:find('^ground block 512,1024 swap 12 ms, layers Asphalt=ASPHALT_OLD Grass=GRASS Dirt=DIRT')
  and l[1]:find('at -34.97012,138.66001', 1, true), 'the first block: its method, time, layers and where: ' .. tostring(l[1]))
D.blockCommit({ method = 'swap', ms = 10, cx = 514, cy = 1024, materials = MATS, actual = OK }, 3)
T.check(#D.drain() == 0, 'the same materials again: counted, not a line')
D.blockCommit({ method = 'import', ms = 30, cx = 516, cy = 1024, materials = { MATS[3] }, actual = OK }, 5)
T.check(#D.drain() == 1, 'other materials: a line')
local wrong = { bngEarthGroundAsphalt = { cls = 'TerrainMaterial', gm = 'DIRT' }, bngEarthGroundGrass = OK.bngEarthGroundGrass }
D.blockCommit({ method = 'swap', ms = 9, cx = 518, cy = 1024, materials = MATS, actual = wrong }, 6)
l = D.drain()
T.check(#l == 1 and l[1]:find('WRONG: bngEarthGroundAsphalt drives as DIRT, not ASPHALT_OLD', 1, true)
  and l[1]:find('beamngEarthDirt: no TerrainMaterial', 1, true), 'a layer driving as another ground model, a missing one')
D.blockCommit({ method = 'swap', ms = 9, cx = 518, cy = 1024, materials = MATS,
  actual = { bngEarthGroundAsphalt = { cls = 'Precipitation' }, bngEarthGroundGrass = OK.bngEarthGroundGrass,
    beamngEarthDirt = OK.beamngEarthDirt } }, 6.5)
T.check(has(D.drain(), 'a Precipitation, not a TerrainMaterial'), 'a name taken by another object')
D.blockCommit({ method = 'import', ms = 20, cx = 1, cy = 2, materials = MATS, actual = OK, partial = true,
  issues = { 'surface layers unusable' } }, 7)
l = D.drain()
T.check(#l == 2 and l[1]:find('partial: built before its map cells', 1, true) and l[2]:find('^ground issue: surface layers unusable'),
  'partial blocks and terrain.lua issues are named')
D.blockCommit({ method = 'import', ms = 20, cx = 1, cy = 2, materials = {}, actual = {} }, 8)
T.check(has(D.drain(), 'the single dirt layer'), 'no surface layers: said so')
D.newConnection()
D.blockCommit({ method = 'import', ms = 20, cx = 1, cy = 2, materials = {}, actual = {} }, 9)
T.check(#D.drain() == 1, 'a new connection: its first block has a line again')

-- wheels
D.reset()
local s, n, main = D.parseWheels('FL=10/4/0.00 FR=10/4/0.01 RL=15/4/0.00 RR=-1/-1/0.00')
T.check(s == '10 x2, 15 x1' and n == 3 and main == 10, 'parse: in the air left out, most wheels first: ' .. s)
s, n = D.parseWheels('')
T.check(s == '' and n == 0, 'parse: nothing')
T.check(D.wantWheels(0, 20) and not D.wantWheels(0.5, 20) and D.wantWheels(1.1, 20), 'asked every WHEEL_EVERY_S')
T.check(not D.wantWheels(5, 0.5), 'not while parked')
D.blockCommit({ method = 'swap', ms = 9, cx = 1, cy = 1, materials = MATS, actual = OK }, 9.5)
D.collisionRebuild(14, 6000, false, 9.8)
D.drain()
D.onWheels(1, 'FL=10/4/0.00 FR=10/4/0.00 RL=10/4/0.00 RR=10/4/0.00', 10)
T.check(#D.drain() == 0, 'the first sample: no line')
D.onWheels(1, 'FL=15/4/0.00 FR=15/4/0.00 RL=15/4/0.00 RR=15/4/0.00', 11)
l = D.drain()
T.check(#l == 1 and l[1]:find('^wheels: ground 10 %-> 15 at %-34%.97012,138%.66001') and l[1]:find('last block commit 1.5s ago', 1, true)
  and l[1]:find('last collision rebuild 1.2s ago', 1, true), 'a change: from, to, where, the commit and rebuild ages: ' .. tostring(l[1]))
local pr = D.takeProbes()
T.check(#pr == 1 and pr[1].from == '10' and pr[1].to == '15' and #D.takeProbes() == 0, 'a change: one probe for the server')
D.onWheels(1, 'FL=10/4/0.00 FR=10/4/0.00 RL=10/4/0.00 RR=10/4/0.00', 12)
T.check(#D.drain() == 0, 'another change within WHEEL_LINE_GAP_S: counted')
D.onWheels(1, 'FL=15/4/0.00 FR=15/4/0.00 RL=15/4/0.00 RR=15/4/0.00', 15)
T.check(has(D.drain(), '+1 changes since the last line'), 'the next line counts them')
D.onWheels(1, 'none', 16)
T.check(has(D.drain(), 'ground probe off'), 'no contact materials in this game: one line')
T.check(not D.wantWheels(100, 20), '... and the probe stops')
D.onWheels(1, 'none', 17)
T.check(#D.drain() == 0, '... once')

-- the minute line
D.reset()
D.tick(0)
D.blockCommit({ method = 'swap', ms = 12, cx = 1, cy = 1, materials = MATS, actual = OK }, 1)
D.blockCommit({ method = 'import', ms = 31, cx = 1, cy = 1, materials = MATS, actual = OK }, 2)
D.collisionRebuild(22.5, 6100, true, 3)
for i = 1, 10 do D.onWheels(1, i <= 8 and 'FL=10/4/0 FR=10/4/0' or 'FL=15/4/0 FR=15/4/0', 3 + i * 4) end
D.drain()
D.tick(30)
T.check(#D.drain() == 0, 'not before SUMMARY_S')
D.tick(61)
l = D.drain()
T.check(#l == 1 and l[1]:find('^ground minute %(61s%): blocks 2 %(1 swaps, 1 imports, 0 partial, slowest 31 ms%)')
  and l[1]:find('collision rebuilds 1 (1 urgent, slowest 22.5 ms, longest gap 6.1s)', 1, true)
  and l[1]:find('wheels 10 80 %, 15 20 %', 1, true), 'the minute: blocks, rebuilds, the wheels by share: ' .. tostring(l[1]))
D.tick(125)
T.check(#D.drain() == 0, 'a quiet minute: no line')

-- the queue
D.reset()
for i = 1, D.MAX_QUEUE + 5 do D.newConnection(); D.blockCommit({ method = 'swap', ms = i, cx = i, cy = 0, materials = {} }, i) end
l = D.drain()
T.check(#l == D.MAX_QUEUE and l[1]:find('ground block 6,0', 1, true), 'at most MAX_QUEUE lines kept, the oldest go')

-- the vehicle command compiles (it runs in the vehicle's Lua)
T.check(loadstring(D.WHEELS_CMD) ~= nil, 'WHEELS_CMD is valid Lua')
do  -- and runs against a vehicle-like environment
  local sent
  local env = setmetatable({ wheels = { wheelRotators = { [0] = { name = 'FL', contactMaterialID1 = 10, contactMaterialID2 = 4,
    contactDepth = 0 }, { name = 'FR', contactMaterialID1 = 15 } } },
    obj = { getID = function() return 7 end, queueGameEngineLua = function(_, c) sent = c end } }, { __index = _G })
  local f = loadstring(D.WHEELS_CMD)
  setfenv(f, env)
  f()
  T.check(sent and sent:find('m.onWheels(7, "FL=10/4/0.00 FR=15/-1/0.00")', 1, true), 'the answer: ' .. tostring(sent))
  env.wheels = nil
  f()
  T.check(sent and sent:find('"none"', 1, true), 'no wheels module: none')
end
T.done('test_diaglog')
