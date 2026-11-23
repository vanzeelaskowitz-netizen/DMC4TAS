// Run the shipped practice callbacks against typed fixtures, without a game or Frida.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tas = path.join(__dirname, '..', 'tas');
const source = fs.readFileSync(path.join(tas, 'combat.js'), 'utf8');
function buildProfile(id) {
    return JSON.parse(fs.readFileSync(path.join(tas, 'builds', id + '.json'), 'utf8'),
        (_, v) => typeof v === 'string' && /^0x/.test(v) ? Number(v) : v);
}
const profile = buildProfile('dx9_21360');
const memory = new Map(), writes = [];
class Pointer {
    constructor(n) { this.n = n; }
    add(off) { return new Pointer(this.n + off); }
    equals(p) { return this.n === p.n; }
    isNull() { return this.n === 0; }
    toString() { return String(this.n); }
    readU32() { return memory.get(this.n) ?? 0; }
    readU8() { return this.readU32(); }
    readPointer() { return new Pointer(this.readU32()); }
    readCString() { return memory.get(this.n); }
    readFloat() { return memory.get(this.n) ?? NaN; }
    writeFloat(v) { memory.set(this.n, v); writes.push(this.n); }
}
const base = 0x400000, mediator = 0x2000000, unit = 0x2100000, area = 0x2200000;
const player = 0x2300000, enemy = 0x2400000, boss = 0x2500000;
const game = 0x2600000, mission = 0x2700000, room = 0x2800000;
let next = 0x4000000;
function typed(addr, ...names) {
    const vt = next, getter = next + 32;
    memory.set(addr, vt); memory.set(vt + 16, getter);
    memory.set(getter, 0xb8); memory.set(getter + 5, 0xc3);
    next += 64;
    memory.set(getter + 1, next);
    for (const [i, name] of names.entries()) {
        memory.set(next + 24, 0x20000);
        memory.set(next + 4, next + 32); memory.set(next + 32, name);
        memory.set(next + 16, i + 1 < names.length ? next + 64 : 0);
        next += 64;
    }
}
function load(build) {
    function NativeFunction() { assert.equal(build.game, 'se'); }
    return vm.runInNewContext(source + '\ncombat', {
        build, Process:{mainModule:{base:new Pointer(base)}}, NativeFunction
    });
}
const dx = load(profile), cfg = profile.practice;
for (const [key, addr, name] of [['mediator', mediator, 'sMediator'], ['area', area, 'sArea'], ['unit', unit, 'sUnit']]) {
    memory.set(base + profile.roots[key], addr); typed(addr, name);
}
for (const [i, addr] of [game, mission, room].entries()) memory.set(area + profile.scene.slots[i+1], addr);
typed(game, 'aGame'); typed(mission, 'aBloodyPalace', 'aMission'); typed(room, 'aRoom000', 'aRoom');
typed(player, 'uPlayerNero', 'uPlayer'); typed(enemy, 'uEm000', 'uEnemy'); typed(boss, 'uEm025', 'uEnemy');
memory.set(mediator + 0x24, player); memory.set(unit + 0x194, enemy);
memory.set(enemy + 8, boss); memory.set(boss + 8, enemy); // corrupted cycle
for (const e of [enemy, boss]) {
    memory.set(e + 0x152c, base + cfg.damageVtableRva);
    memory.set(e + 0x1544, 40); memory.set(e + 0x1548, 100);
}
memory.set(player + 0x15cc, 10); memory.set(player + 0x15d0, 60);
memory.set(player + 0x1f24, 1); memory.set(player + 0x1f28, 10);
memory.set(mediator + 0x250, 1000); memory.set(mediator + 0x164, 123);
for (const switchName of ['hp','dt','time','kill']) dx.setCheat(switchName, true);
assert.equal(writes.length, 0);
dx.cheat();
assert.deepEqual(writes.splice(0), [player+0x15cc, player+0x1f24, enemy+0x1544]);
assert.equal(memory.get(enemy+0x1544), 1); assert.equal(dx.cheatStatus().enemiesCovered, 1);
assert.equal(dx.cheatStatus().enemies, 2); assert.equal(dx.cheatStatus().skipped.join(), 'uEm025');
memory.set(mediator+0x250, 999); dx.cheat(); assert.equal(memory.get(mediator+0x250), 1000);
memory.set(mediator+0x250, 1100); dx.cheat();
memory.set(mediator+0x250, 1099); dx.cheat(); assert.equal(memory.get(mediator+0x250), 1100);
typed(mission, 'aMission02', 'aMission'); typed(player, 'uPlayerDante', 'uPlayer');
dx.cheat(); memory.set(mediator+0x164, 124); dx.cheat(); assert.equal(memory.get(mediator+0x164), 123);
dx.cheatRestored(); memory.set(mediator+0x164, 5); dx.cheat(); assert.equal(memory.get(mediator+0x164), 5);
dx.setCheat('kill', false); dx.cheat(); assert.equal(memory.get(enemy+0x1544), 100);
dx.setCheat('kill', true); memory.set(mediator+cfg.enemyNoDeath, 1); dx.cheat();
assert.equal(dx.cheatStatus().enemiesCovered, 0); assert.equal(memory.get(enemy+0x1544), 100);
memory.set(mediator+cfg.enemyNoDeath, 0); memory.set(enemy+0x152c+0x84, 0x100);
dx.cheat(); assert.equal(dx.cheatStatus().enemiesCovered, 0); assert.equal(memory.get(enemy+0x1544), 100);
memory.set(enemy+0x152c+0x84, 0); memory.set(enemy+0x1500, base + cfg.damageVtableRva);
dx.cheat(); assert.equal(dx.cheatStatus().enemiesCovered, 0);
memory.set(enemy+0x1500, 0); memory.set(mediator+0x24, 0); dx.cheat();
assert.equal(memory.get(enemy+0x1544), 100); assert.equal(dx.cheatStatus().enemies, 0);
memory.set(mediator+0x24, player); typed(mediator, 'sUnit');
memory.set(player+0x15cc, 10); dx.cheat(); assert.equal(memory.get(player+0x15cc), 10);
const se = load(buildProfile('se_24329835'));
memory.set(0x1154b34, mediator); memory.set(mediator+0x24, player);
typed(mediator, 'sMediator'); memory.set(0x118d450, unit); memory.set(enemy+0x14, 0);
memory.set(enemy+0x1a50, 0xf2d184); memory.set(enemy+0x1a80, 20); memory.set(enemy+0x1a84, 60);
memory.set(player+0x1b00, 5); memory.set(player+0x1b04, 80); memory.set(player+0x2508, 9);
memory.set(mediator+0x384, 300);
for (const name of ['hp','dt','time','kill']) se.setCheat(name, true);
se.cheat();
assert.equal(memory.get(player+0x1b00), 80); assert.equal(memory.get(player+0x2504), 9);
assert.equal(memory.get(enemy+0x1a80), 1); assert.equal(se.cheatStatus().enemiesCovered, 1);
memory.set(mediator+0x384, 299); se.cheat(); assert.equal(memory.get(mediator+0x384), 300);
console.log('PASS DX9 guards, coverage, vitals, timers, restore and all four SE aids');
se.setCheat('kill', false);
for (const loss of [7, 9, 0]) {
    se.cheat();
    se.begin(1);
    memory.set(enemy+0x1a80, 60-loss);
    const sample = se.finish();
    assert.equal(sample.hpBefore[enemy], 60);
    assert.equal(sample.enemies[0].hp, 60-loss);
}
console.log('PASS consecutive enemy hits with HP lock: 60->53, 60->51, 60->60');
const controls = buildProfile('se_24329835').controls;
memory.set(base + controls.save, game);
memory.set(player + controls.character, controls.tables.length);
assert.equal(se.controls(new Pointer(player), new Map()), null);
memory.set(player + controls.character, 0);
memory.set(game + controls.tables[0] + controls.slots.jump, 75);
assert.equal(se.controls(new Pointer(player), new Map([[75, 'K']])).jump, 'K');
