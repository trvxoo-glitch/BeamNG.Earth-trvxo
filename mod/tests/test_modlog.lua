-- luajit mod/tests/test_modlog.lua  (the mod's warnings, errors and debug lines into the server's log)
local T = dofile((arg[0]:match('^(.*)[/\\]') or '.') .. '/testlib.lua')
local L = require('beamngEarth/modlog')
local now = 0
L.now = function() return now end

T.check(L.area('beamngEarth') == 'core' and L.area('beamngEarth.trees') == 'trees' and L.area('other') == nil
  and L.area('beamngEarth_farfield.beamngEarth') == 'farfield' and L.area('beamngEarth_core.beamngEarth.x') == 'core',
  'areas from tags')
L.reset()
L.note('W', 'beamngEarth.trees', 'no forest at 12.5')
L.note('I', 'beamngEarth', 'info is not sent')
L.note('E', 'someOtherMod', 'not ours')
L.note('D', 'beamngEarth.roads', 'debug off')
local q = L.drain()
T.check(#q == 1 and q[1].l == 'W' and q[1].a == 'trees' and q[1].m == 'no forest at 12.5', 'only our W/E lines')
L.note('I', 'beamngEarth_mpsession.beamngEarth', 'joined: 3 players')
q = L.drain()
T.check(#q == 1 and q[1].l == 'I' and q[1].a == 'mpsession', 'multiplayer I lines go too')
L.setDebug('roads, game.trees')
L.note('D', 'beamngEarth.roads', 'debug on')
L.note('D', 'beamngEarth.trees', 'debug on too')
L.note('D', 'beamngEarth.sea', 'not this area')
T.check(#L.drain() == 2, 'debug lines of the areas asked for')
L.setDebug('all')
T.check(L.debugOn('sea'), 'all')
L.setDebug(nil)
T.check(not L.debugOn('roads'), 'cleared')

-- repeats
L.reset()
for i = 1, 10 do L.note('E', 'beamngEarth', 'tile ' .. i .. ' failed') end
q = L.drain()
T.check(#q == L.REPEAT_MAX and q[#q].m:find('repeating', 1, true), 'the same line with other numbers: limited: ' .. #q)
now = 61
L.note('E', 'beamngEarth', 'tile 99 failed')
q = L.drain()
T.check(#q == 1 and q[1].m:find('(+7 more like this before)', 1, true), 'the count goes with the next one: ' .. tostring(q[1] and q[1].m))

-- the queue and long lines
L.reset()
for i = 1, L.MAX_QUEUE + 10 do L.note('W', 'beamngEarth.a' .. i, 'x') end
T.check(#L.drain() == L.MAX_QUEUE, 'queue capped')
L.note('W', 'beamngEarth', string.rep('y', 2000) .. '\nz')
q = L.drain()
T.check(#q[1].m < 700 and not q[1].m:find('\n'), 'cut, one line')

-- the wrapper: the game's log still called, ours noted, installed once
L.reset()
local calls = 0
_G.log = function() calls = calls + 1 end
_G.__beamngEarthLogWrapped = nil
L.install()
local first = _G.log
L.install()
T.check(_G.log == first, 'wrapped once')
log('W', 'beamngEarth', 'through the global')
T.check(calls == 1 and #L.drain() == 1, 'the game logs it and so do we')
_G.__beamngEarthLogNote = function() error('boom') end
log('W', 'beamngEarth', 'a broken note')
T.check(calls == 2, 'an error in ours never stops the game log')
T.done('test_modlog')
