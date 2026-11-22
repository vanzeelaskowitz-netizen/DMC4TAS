// Shared by TAS and the passive watcher. Preferred VAs refer to build 24329835.
const combat = (() => {
    const module = Process.mainModule;
    const at = va => module.base.add(va - 0x400000);
    const safe = fn => { try { return fn(); } catch (_) { return null; } };
    const root = va => safe(() => at(va).readPointer());
    // The predicate and its mask helper only read actor data. Never call the
    // successful action dispatcher. "none" excludes our own observation hooks.
    const predicate = new NativeFunction(at(0x5eeef0),'uint8',['pointer'],
        {abi:'thiscall', traps:'none', scheduling:'exclusive'});
    const field = (p, off, method) => safe(() => p.add(off)[method]());
    const u32 = (p, off) => field(p, off, 'readU32');
    const f32 = (p, off) => {
        const x = field(p, off, 'readFloat');
        return Number.isFinite(x) ? x : null;
    };
    const pointer = (p, off) => field(p, off, 'readPointer');
    const id = p => p && !p.isNull() ? p.toString() : null;
    const vec = (p, off, n=3) => {
        const v = Array.from({length:n}, (_, i) => f32(p, off + 4*i));
        return v.every(Number.isFinite) ? v : null;
    };
    const types = new Map();
    function type(p) {
        return safe(() => {
            const vt = p.readPointer(), key = vt.toString();
            if (types.has(key)) return types.get(key);
            const getter = vt.add(16).readPointer();
            if (getter.readU8() !== 0xb8 || getter.add(5).readU8() !== 0xc3) return null;
            let dti = getter.add(1).readPointer();
            const size = dti.add(0x18).readU32() & 0x7fffff;
            const names = [];
            while (!dti.isNull() && names.length < 32) {
                names.push(dti.add(4).readPointer().readCString());
                dti = dti.add(0x10).readPointer();
            }
            const result = {names, size};
            types.set(key, result);
            return result;
        });
    }
    function buttons(p, off) {
        return {held:u32(p,off), pressed:u32(p,off+4), released:u32(p,off+8)};
    }
    let active = null;
    function begin(tid) {
        active = {tid, checks:[], attempts:[], events:[], inputs:[], masks:new Map()};
    }
    function install(hook) {
        hook(at(0x8c1490), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.player = this.context.ecx; } },
            onLeave() {
                if (this.owner && this.owner === active)
                    active.inputs.push({player:id(this.player), ...buttons(this.player,0x1920)});
            }
        });
        hook(at(0x5eeef0), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.actor = id(this.context.ecx); } },
            onLeave(r) {
                if (this.owner && this.owner === active) active.checks.push({actor:this.actor, result:!!(r.toInt32() & 255)});
            }
        });
        hook(at(0x5ef0e0), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.actor = id(this.context.ecx); } },
            onLeave(r) { if (this.owner && this.owner === active) active.masks.set(this.actor, r.toUInt32()); }
        });
        hook(at(0x92a890), {
            onEnter() {
                if (active?.tid !== this.threadId) return;
                const p = this.context.ecx;
                // The exact moment the game decides: this answer wins over the one taken below.
                active.early = safe(() => early(p)) || active.early;
                this.attempt = {player:id(p), pressed:u32(p,0x1924), flags:u32(p,0x1a64),
                    enabled:field(p,0x25f5,'readU8'), cooldown:f32(p,0x25e4), result:null};
                active.attempts.push(this.attempt);
            },
            onLeave(r) { if (this.attempt) this.attempt.result = !!(r.toInt32() & 255); }
        });
        // The player's position is advanced here (velocity * step), after the frame's action logic
        // where Enemy Step is decided. On frames without a jump the game never asks, so the answer
        // is taken here, before the player moves: what a jump pressed on this frame would have got.
        hook(at(0x8c5570), {
            onEnter() {
                // Any thread: units are updated by the engine's job threads as well.
                if (!active || active.moveEarly) return;
                const p = root(0x1154b34) && pointer(root(0x1154b34),0x24);
                if (id(p)) {
                    active.moveEarly = safe(() => early(p));
                    if (active.moveEarly) active.moveEarly.where = [active.tid === this.threadId, this.context.ecx.equals(p)];
                }
            }
        });
        // Only the successful branch reaches this return site after action dispatch(0x19).
        hook(at(0x92a9ec), function () {
            if (active?.tid === this.threadId) active.events.push({player:id(this.context.edi), actor:id(this.context.esi), action:0x19});
        });
    }
    function early(p) {
        const pos = vec(p,0x40), limit = f32(at(0xf87bd4),0);
        const cooldown = f32(p,0x25e4);
        const result = {allowed: field(p,0x25f5,'readU8') === 1 && (u32(p,0x1a64) & 2) !== 0 &&
            cooldown !== null && limit !== null && cooldown <= limit, enemies:{}};
        const seen = new Set();
        let e = pointer(root(0x118d450),0x194);
        while (id(e) && !seen.has(id(e)) && seen.size < 256) {
            seen.add(id(e));
            if (type(e)?.names.includes('uEnemy')) {
                const spheres = geometry(e,pos);
                result.enemies[id(e)] = {can: !!predicate(e), inside: spheres ? spheres.map(s => s.inside === true) : null};
            }
            e = pointer(e,0x14);
        }
        return result;
    }
    function geometry(e, playerPos, mask) {
        let t = pointer(e,0x1818);
        if (!t || t.isNull() || !playerPos) return null;
        const remap = pointer(e,0x4c8), joints = pointer(e,0x4c4);
        const spheres = [];
        for (let i = 0; i <= 32; i++, t=t.add(32)) {
            const no = field(t,0,'readS32');
            if (no === -1) return spheres;
            if (no === null || i === 32) return null;
            const radius = f32(t,4), local = vec(t,16);
            const index = field(remap,no & 255,'readU8');
            let center = null, inside = null;
            if (index !== null && index !== 255 && id(joints) && local) {
                const m = vec(joints,index * 160 + 16,16);
                if (m) {
                    const v = [0,1,2,3].map(k => local[0]*m[k]+local[1]*m[4+k]+local[2]*m[8+k]+m[12+k]);
                    center = v[3] === 0 ? [0,0,0] : v.slice(0,3).map(k => k/v[3]);
                    if (radius !== null) {
                        const dy = center[1] - (playerPos[1]+85);
                        inside = (center[0]-playerPos[0])**2 + dy*dy + (center[2]-playerPos[2])**2 < radius*radius;
                    }
                }
            }
            spheres.push({joint:no, radius, local, center, inside, activeAtCheck:mask === undefined ? null : !!(mask & (1<<i))});
        }
    }
    const damageOffsets = [0x1a50,0x1a58,0x1a60,0x1a70,0x1a80,0x2c30,0x2d80,0x3e38,0x85e8];
    function enemy(e, t, pos, phase) {
        const key = id(e), xyz = vec(e,0x40);
        const spheres = geometry(e,pos,phase.masks.get(key));
        const checks = phase.checks.filter(item => item.actor === key);
        const damage = damageOffsets.filter(off => off+0x34 < t.size && pointer(e,off)?.equals(at(0xf2d184)));
        const table = pointer(e,0x1818);
        const readable = field(e,0x1820,'readU8') === 0 || (table && table.isNull()) ||
            (spheres && spheres.every(s => s.center !== null && s.radius !== null));
        const canStep = pos && readable ? safe(() => !!predicate(e)) : null;
        return {pointer:key, type:t.names[0], position:xyz, hp:damage.length===1 ? f32(e,damage[0]+0x30) : null,
            action:field(e,4,'readU8'), phase:field(e,5,'readU8'),
            motion:field(e,0x4e4,'readU16'), motionFrame:f32(e,0x530),
            distance:pos && xyz ? Math.hypot(...xyz.map((v,i)=>v-pos[i])) : null,
            spheres, inSphere:spheres?.some(s=>s.inside===true) ? true :
                (spheres && spheres.every(s=>s.inside===false) ? false : null),
            canStep, lastNaturalCheck:checks.length ? checks[checks.length-1].result : null,
            hitStopTimer:f32(e,0x1944), frozen:field(e,0x1948,'readU8'), unitDelta:f32(e,0x1c)};
    }
    function finish() {
        const phase = active;
        active = null;
        if (!phase) return null;
        const mediator = root(0x1154b34), unit = root(0x118d450), rates = root(0x1164b50);
        const p = pointer(mediator,0x24), t = type(p);
        const pos = t?.names.includes('uPlayer') ? vec(p,0x40) : null;
        const player = !t?.names.includes('uPlayer') ? null : {
            pointer:id(p), type:t.names[0], position:pos, quaternion:vec(p,0x50,4),
            velocity:vec(p,0x11f0), motionDelta:vec(p,0xf10),
            stepFlags:u32(p,0x1a64), stepEnabled:field(p,0x25f5,'readU8'), stepCooldown:f32(p,0x25e4),
            action:u32(p,0x1a00), motion:field(p,0x4e4,'readU16'), motionFrame:f32(p,0x530),
            grounded:field(pointer(p,0x2470),0x34,'readU8'), lockTarget:id(pointer(p,0x1aa0)),
            hp:f32(p,0x1b00), devilGauge:f32(p,0x2504), devilGaugeMax:f32(p,0x2508),
            // Nero's Exceed (found 2026-11-21 by pressing the key on every frame after a slash):
            // +0xF2D0 counts up while the EX-Act window of a sword move is open and is 0 otherwise;
            // the key works on exactly the frames that end with it above 0, the first of them is
            // MAX-Act. +0xF1C8 is the gauge, 0..3.
            exceed: t.names.includes('uPlayerNero') ? {timer:f32(p,0xf2d0), gauge:u32(p,0xf1c8)} : null,
            buttons:buttons(p,0x1920), peripheral:buttons(p,0x1888),
            direction:buttonsDirection(p), motionHitStop:field(p,0x3048,'readU8'),
            hitStopTimer:f32(p,0x2448), frozen:field(p,0x1531,'readU8'),
            hitSlowTimer:f32(p,0x19a0), hitSlowRate:f32(p,0x19a4), workDelta:f32(p,0x1010),
            unitSpeed:f32(p,0x1008), deltaUnits:f32(p,0x1c)
        };
        const enemies = [], seen = new Set();
        let e = pointer(unit,0x194);
        let complete = e !== null;
        while (id(e)) {
            if (seen.has(id(e)) || seen.size >= 256) { complete=false; break; }
            seen.add(id(e));
            const et = type(e);
            if (!et) complete=false;
            if (et?.names.includes('uEnemy')) enemies.push(enemy(e,et,pos,phase));
            e=pointer(e,0x14);
            if (!e) complete=false;
        }
        const engineDelta = f32(root(0x118d460),0x70);
        const timeMultiplier = engineDelta > 0 && player?.deltaUnits != null ? player.deltaUnits / engineDelta : null;
        return {player, inputUpdates:phase.inputs, enemies:complete || enemies.length ? enemies : null,
            enemiesComplete:complete, checks:phase.checks, attempts:phase.attempts, enemyStep:phase.events.length>0, events:phase.events,
            stepCheck:phase.early || phase.moveEarly || null, stepCheckAtMove:phase.moveEarly || null,
            turbo:field(mediator,0x5ec,'readU8'), turboCancel:field(mediator,0x5ed,'readU8'),
            turboMultiplier:f32(rates,0x38), globalRate:f32(rates,0x20), globalCombatRate:f32(rates,0x28),
            engineDelta, timeMultiplier,
            rng:[0,4,8,12].map(off=>u32(at(0x1150a40),off))};
    }
    function buttonsDirection(p) {
        return {held:field(p,0x1938,'readU8'), pressed:field(p,0x193c,'readU8'), released:field(p,0x1940,'readU8')};
    }
    function inspect() {
        // Called only at a parked frame boundary, never during a combat sample.
        begin(0);
        return safe(finish);
    }
    // Practice aids (the trainer functions the user asked for, at this build's addresses). They are
    // applied once per frame at the frame boundary, before the frame runs, so with the switches
    // left alone a timeline replays exactly the same with them as it was made.
    //   hp   player and enemy life kept at their maximum (P+0x1B00 <- P+0x1B04; enemies
    //        uDamage+0x30 <- +0x34) while alive
    //   dt   devil trigger gauge kept full (P+0x2504 <- P+0x2508)
    //   time Bloody Palace countdown (sMediator+0x384, found 2026-11-19: falls 1.2 a frame on
    //        turbo) does not run down; time bonuses still add
    //   kill every enemy whose life field is known is kept at 1, so any hit kills it
    const cheats = {hp:false, dt:false, time:false, kill:false};
    let timeHeld = null, killSeen = 0, killCovered = 0;
    function cheat() {
        if (!cheats.time) timeHeld = null;
        if (!cheats.hp && !cheats.dt && !cheats.time && !cheats.kill) return;
        const mediator = root(0x1154b34);
        if (!id(mediator)) return;
        if (cheats.hp || cheats.dt) {
            const p = pointer(mediator,0x24), t = id(p) ? type(p) : null;
            if (t?.names.includes('uPlayer')) {
                const hp = f32(p,0x1b00), hpMax = f32(p,0x1b04), dtMax = f32(p,0x2508);
                if (cheats.hp && hp > 0 && hpMax > 0 && hp < hpMax) safe(() => p.add(0x1b00).writeFloat(hpMax));
                if (cheats.dt && dtMax > 0) safe(() => p.add(0x2504).writeFloat(dtMax));
            }
        }
        if (cheats.time) {
            const left = f32(mediator,0x384);
            if (left !== null) {
                if (timeHeld !== null && left < timeHeld && timeHeld - left < 30) safe(() => mediator.add(0x384).writeFloat(timeHeld));
                else timeHeld = left;
            }
        }
        if (cheats.kill || cheats.hp) {
            // Enemies: life at uDamage+0x30, maximum at +0x34 (same layout as the player's uDamage
            // at P+0x1AD0). One-hit kill wins over the life lock when both are on.
            const unit = root(0x118d450), seen = new Set();
            let e = id(unit) ? pointer(unit,0x194) : null;
            killSeen = 0; killCovered = 0;
            while (id(e) && !seen.has(id(e)) && seen.size < 256) {
                seen.add(id(e));
                const t = type(e);
                if (t?.names.includes('uEnemy')) {
                    killSeen++;
                    const damage = damageOffsets.filter(off => off+0x34 < t.size && pointer(e,off)?.equals(at(0xf2d184)));
                    if (damage.length === 1) {
                        killCovered++;
                        const where = e.add(damage[0]+0x30);
                        const hp = f32(e,damage[0]+0x30), hpMax = f32(e,damage[0]+0x34);
                        if (cheats.kill) {
                            if (hp > 1) safe(() => where.writeFloat(1));
                        } else if (hp > 0 && hpMax > 0 && hp < hpMax) {
                            safe(() => where.writeFloat(hpMax));
                        }
                    }
                }
                e = pointer(e,0x14);
            }
        }
    }
    function setCheat(name, on) {
        if (!(name in cheats)) throw new Error('Unknown switch ' + name);
        cheats[name] = !!on;
        return cheatStatus();
    }
    const cheatStatus = () => ({...cheats, enemies:killSeen, enemiesCovered:killCovered});
    return {install, begin, inspect, finish:() => safe(finish), type,
            cheat:() => safe(cheat), setCheat, cheatStatus, cheatRestored() { timeHeld = null; }};
})();
