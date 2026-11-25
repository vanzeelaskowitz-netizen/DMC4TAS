// Shared by TAS and the passive watcher.
const combat = (() => {
    const module = Process.mainModule;
    const at = va => module.base.add(va - 0x400000);
    const safe = fn => { try { return fn(); } catch (_) { return null; } };
    const root = va => safe(() => at(va).readPointer());
    // The predicate and its mask helper only read actor data. Never call the
    // successful action dispatcher. "none" excludes our own observation hooks.
    const dx9 = build.game === 'dx9', jc = build.step, battle = build.battle;
    const damageVtable = module.base.add(battle.damageVtableRva);
    const singleton = key => pointer(module.base, build.roots[key]);
    const predicate = new NativeFunction(module.base.add(jc.predicate),'uint8',['pointer'],
        {abi:dx9 ? 'stdcall' : 'thiscall', traps:'none', scheduling:'exclusive'});
    const filteredMask = jc.maskHelper !== null && new NativeFunction(module.base.add(jc.maskHelper),'uint32',['pointer'],
        {abi:'stdcall', traps:'none', scheduling:'exclusive'});
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
    function damageOffset(e, t, extent = battle.damageExtent) {
        const matches = battle.damageOffsets.filter(off => off + extent < t.size &&
            pointer(e,off)?.equals(damageVtable));
        return matches.length === 1 ? matches[0] : null;
    }
    function startingHp() {
        const hp = {};
        const p = pointer(singleton('mediator'),build.scene.player);
        if (id(p)) hp[id(p)] = f32(p,battle.playerHp);
        const head = pointer(singleton('unit'),battle.enemyLine);
        const seen = new Set();
        for (let e = head; id(e) && !seen.has(id(e)); e = pointer(e,build.scene.next)) {
            seen.add(id(e));
            const t = type(e);
            const off = t?.names.includes('uEnemy') ? damageOffset(e,t) : null;
            if (off !== null) hp[id(e)] = f32(e,off+battle.damageHp);
        }
        return hp;
    }
    function controls(p, names) {
        const cfg = build.controls, save = pointer(module.base, cfg.save);
        const character = u32(p, cfg.character);
        const offset = cfg.tables[character];
        if (!id(save) || offset === undefined) return null;
        const table = save.add(offset);
        return Object.fromEntries(Object.entries(cfg.slots).map(([name, offset]) =>
            [name, names.get(u32(table, offset)) ?? null]));
    }
    function cameraMatrices() {
        const cfg = build.camera, camera = pointer(module.base,cfg.slot);
        const view = vec(camera,cfg.view,16), projection = vec(camera,cfg.projection,16);
        return view && projection ? {view, projection} : null;
    }
    function begin(tid) {
        active = {hp:tid ? startingHp() : {}, tid, checks:[], attempts:[], events:[], inputs:[], masks:new Map()};
    }
    function install(hook) {
        hook(module.base.add(jc.attempt), {
            onEnter() {
                if (!active || (!dx9 && active.tid !== this.threadId)) return;
                const p = this.context[jc.attemptPlayer];
                // The exact moment the game decides: this answer wins over the one taken below.
                active.early = safe(() => early(p)) || active.early;
                if (dx9) return;
                this.attempt = {player:id(p), pressed:u32(p,battle.buttons+4), flags:u32(p,jc.flags),
                    enabled:field(p,jc.enabled,'readU8'), cooldown:f32(p,battle.cooldown), result:null};
                active.attempts.push(this.attempt);
            },
            onLeave(r) { if (this.attempt) this.attempt.result = !!(r.toInt32() & 255); }
        });
        // The player's position is advanced here (velocity * step), after the frame's action logic
        // where Enemy Step is decided. On frames without a jump the game never asks, so the answer
        // is taken here, before the player moves: what a jump pressed on this frame would have got.
        hook(module.base.add(jc.move), {
            onEnter() {
                // Any thread: units are updated by the engine's job threads as well.
                if (!active || active.moveEarly) return;
                const p = pointer(singleton('mediator'),build.scene.player);
                if (!id(p)) return;
                const isPlayer = this.context[jc.moveActor].equals(p);
                if (dx9 && !isPlayer) return;
                active.moveEarly = safe(() => early(p));
                if (!dx9 && active.moveEarly) active.moveEarly.where = [active.tid === this.threadId, isPlayer];
            }
        });
        if (dx9) return;
        hook(module.base.add(jc.inputUpdate), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.player = this.context.ecx; } },
            onLeave() {
                if (this.owner && this.owner === active)
                    active.inputs.push({player:id(this.player), ...buttons(this.player,battle.buttons)});
            }
        });
        hook(module.base.add(jc.predicate), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.actor = id(this.context.ecx); } },
            onLeave(r) {
                if (this.owner && this.owner === active) active.checks.push({actor:this.actor, result:!!(r.toInt32() & 255)});
            }
        });
        hook(module.base.add(jc.nativeMask), {
            onEnter() { if (active?.tid === this.threadId) { this.owner = active; this.actor = id(this.context.ecx); } },
            onLeave(r) { if (this.owner && this.owner === active) active.masks.set(this.actor, r.toUInt32()); }
        });
        // Only the successful branch reaches this return site after action dispatch.
        hook(module.base.add(jc.success), function () {
            if (active?.tid === this.threadId) active.events.push({player:id(this.context.edi), actor:id(this.context.esi), action:jc.action});
        });
    }
    function early(p) {
        const pos = vec(p,battle.position), limit = f32(module.base,jc.limit);
        const cooldown = f32(p,battle.cooldown);
        const learned = jc.skill === null || field(pointer(module.base,build.controls.save),
            jc.skill[u32(p,build.controls.character)],'readU8') === 1;
        const result = {allowed: learned && field(p,jc.enabled,'readU8') === 1 && (u32(p,jc.flags) & 2) !== 0 &&
            cooldown !== null && limit !== null && cooldown <= limit, enemies:{}};
        const seen = new Set();
        let e = pointer(singleton('unit'),battle.enemyLine);
        while (id(e) && !seen.has(id(e)) && seen.size < 256) {
            seen.add(id(e));
            if (type(e)?.names.includes('uEnemy')) {
                const spheres = geometry(e,pos);
                const can = canStep(e,spheres);
                result.enemies[id(e)] = {can, inside:(spheres || []).map(s => s.inside === true)};
                if (filteredMask && can) result.enemies[id(e)].mask = filteredMask(e);
            }
            e = pointer(e,build.scene.next);
        }
        return result;
    }
    function geometry(e, playerPos, mask) {
        let t = pointer(e,jc.table);
        if (!t || t.isNull() || !playerPos) return null;
        const remap = pointer(e,jc.remap), joints = pointer(e,jc.joints);
        const spheres = [];
        for (let i = 0; i <= 32; i++, t=t.add(32)) {
            const no = field(t,0,'readS32');
            if (no === -1) return spheres;
            if (no === null || i === 32) return null;
            const radius = f32(t,4), local = vec(t,16);
            const index = field(remap,no & 255,'readU8');
            let center = null, inside = null;
            if (index !== null && index !== 255 && id(joints) && local) {
                const m = vec(joints,index * jc.stride + jc.matrix,16);
                if (m) {
                    const v = [0,1,2,3].map(k => local[0]*m[k]+local[1]*m[4+k]+local[2]*m[8+k]+m[12+k]);
                    center = v[3] === 0 ? null : v.slice(0,3).map(k => k/v[3]);
                    if (center && radius !== null) {
                        const dy = center[1] - (playerPos[1]+85);
                        inside = (center[0]-playerPos[0])**2 + dy*dy + (center[2]-playerPos[2])**2 < radius*radius;
                    }
                }
            }
            spheres.push({joint:no, radius, local, center, inside, activeAtCheck:mask === undefined ? null : !!(mask & (1<<i))});
        }
    }
    function inSphere(spheres) {
        return spheres?.some(s=>s.inside===true) ? true :
                (spheres && spheres.every(s=>s.inside===false) ? false : null);
    }
    function canStep(e, spheres) {
        if (!id(pointer(e,jc.table)) || !field(e,jc.enemyEnabled,'readU8')) return false;
        return spheres?.every(s => s.center && s.radius !== null) ? safe(() => !!predicate(e)) : null;
    }
    function enemy(e, t, pos, phase) {
        const key = id(e), xyz = vec(e,battle.position);
        const spheres = geometry(e,pos,phase.masks.get(key));
        const checks = phase.checks.filter(item => item.actor === key);
        const damage = damageOffset(e,t);
        return {pointer:key, type:t.names[0], position:xyz, hp:damage !== null ? f32(e,damage+battle.damageHp) : null,
            action:field(e,battle.enemyAction,'readU8'), phase:field(e,battle.phase,'readU8'),
            motion:field(e,battle.motion,'readU16'), motionFrame:f32(e,battle.motionFrame),
            distance:pos && xyz ? Math.hypot(...xyz.map((v,i)=>v-pos[i])) : null,
            spheres, inSphere:inSphere(spheres),
            canStep:canStep(e,spheres), lastNaturalCheck:checks.length ? checks[checks.length-1].result : null,
            hitStopTimer:f32(e,battle.enemyStop), frozen:field(e,battle.enemyFrozen,'readU8'), unitDelta:f32(e,battle.unitDelta)};
    }
    function finish() {
        const phase = active;
        active = null;
        if (!phase) return null;
        const mediator = singleton('mediator'), unit = singleton('unit'), rates = root(0x1164b50);
        const p = pointer(mediator,build.scene.player), t = type(p);
        const pos = t?.names.includes('uPlayer') ? vec(p,battle.position) : null;
        const player = !t?.names.includes('uPlayer') ? null : {
            pointer:id(p), type:t.names[0], position:pos, quaternion:vec(p,0x50,4),
            velocity:vec(p,0x11f0), motionDelta:vec(p,0xf10),
            stepFlags:u32(p,jc.flags), stepEnabled:field(p,jc.enabled,'readU8'), stepCooldown:f32(p,battle.cooldown),
            action:u32(p,battle.action), motion:field(p,battle.motion,'readU16'), motionFrame:f32(p,battle.motionFrame),
            grounded:field(pointer(p,0x2470),0x34,'readU8'), lockTarget:id(pointer(p,0x1aa0)),
            hp:f32(p,battle.playerHp), devilGauge:f32(p,battle.playerDt), devilGaugeMax:f32(p,battle.playerDtMax),
            // Nero's Exceed (found 2026-11-21 by pressing the key on every frame after a slash):
            // +0xF2D0 counts up while the EX-Act window of a sword move is open and is 0 otherwise;
            // the key works on exactly the frames that end with it above 0, the first of them is
            // MAX-Act. The gauge is 0..3. Offsets per build: battle.exceedTimer / exceedGauge.
            exceed: t.names.includes('uPlayerNero') ?
                {timer:f32(p,battle.exceedTimer), gauge:u32(p,battle.exceedGauge)} : null,
            buttons:buttons(p,battle.buttons), peripheral:buttons(p,0x1888),
            direction:buttonsDirection(p), motionHitStop:field(p,0x3048,'readU8'),
            hitStopTimer:f32(p,battle.playerStop), frozen:field(p,battle.playerFrozen,'readU8'),
            hitSlowTimer:f32(p,0x19a0), hitSlowRate:f32(p,0x19a4), workDelta:f32(p,0x1010),
            unitSpeed:f32(p,0x1008), deltaUnits:f32(p,battle.unitDelta)
        };
        const enemies = [], seen = new Set();
        let e = pointer(unit,battle.enemyLine);
        let complete = e !== null;
        while (id(e)) {
            if (seen.has(id(e)) || seen.size >= 256) { complete=false; break; }
            seen.add(id(e));
            const et = type(e);
            if (!et) complete=false;
            if (et?.names.includes('uEnemy')) enemies.push(enemy(e,et,pos,phase));
            e=pointer(e,build.scene.next);
            if (!e) complete=false;
        }
        const engineDelta = f32(root(0x118d460),0x70);
        const timeMultiplier = engineDelta > 0 && player?.deltaUnits != null ? player.deltaUnits / engineDelta : null;
        return {matrices:cameraMatrices(), hpBefore:phase.hp,
            player, inputUpdates:phase.inputs, enemies:complete || enemies.length ? enemies : null,
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
    const cheats = {hp:false, dt:false, time:false, kill:false};
    let timeHeld = null, killSeen = 0, killCovered = 0;
    let timerScene = null;
    const skipped = new Set();
    function dxPractice() {
        killSeen = killCovered = 0;
        skipped.clear();
        if (!Object.values(cheats).some(Boolean)) return;
        const cfg = build.practice, slots = build.scene.slots;
        const mediator = singleton('mediator'), area = singleton('area');
        const p = pointer(mediator, build.scene.player), playerType = type(p);
        const mission = pointer(area, slots[2]), room = pointer(area, slots[3]);
        const missionType = type(mission)?.names[0], palace = missionType === 'aBloodyPalace';
        if (type(mediator)?.names[0] !== 'sMediator' || type(area)?.names[0] !== 'sArea' ||
            type(pointer(area, slots[1]))?.names[0] !== 'aGame' ||
            (!palace && !/^aMission\d\d$/.test(missionType)) ||
            !type(room)?.names.includes('aRoom') || u32(mediator, build.scene.loading) !== 0 ||
            !playerType?.names.includes('uPlayer') ||
            !['uPlayerDante', 'uPlayerNero', 'uPlayerNeroTutorial'].includes(playerType.names[0])) {
            timerScene = null;
            return;
        }
        const hp = f32(p, battle.playerHp), max = f32(p, battle.playerHpMax);
        if (cheats.hp && hp > 0 && hp < max) p.add(battle.playerHp).writeFloat(max);
        const dt = f32(p, battle.playerDt), dtMax = f32(p, battle.playerDtMax);
        if (cheats.dt && dt !== null && dt >= 0 && dt < dtMax) p.add(battle.playerDt).writeFloat(dtMax);
        if (cheats.time) {
            const key = [missionType, mission, room, p, u32(mediator, build.scene.mission)].join('/');
            const off = palace ? cfg.palaceRemaining : cfg.missionElapsed;
            const value = f32(mediator, off);
            if (key !== timerScene || value === null || value < 0) timeHeld = null;
            timerScene = key;
            const change = palace ? timeHeld - value : value - timeHeld;
            if (timeHeld !== null && change > 0 && change < 30) mediator.add(off).writeFloat(timeHeld);
            else timeHeld = value;
        }
        if (!cheats.hp && !cheats.kill) return;
        const unit = singleton('unit');
        if (type(unit)?.names[0] !== 'sUnit') return;
        const seen = new Set();
        const protectedMode = field(mediator, cfg.enemyNoDeath, 'readU8') !== 0;
        for (let e = pointer(unit, battle.enemyLine); id(e) && !seen.has(id(e)) && seen.size < 256;
             e = pointer(e, build.scene.next)) {
            seen.add(id(e));
            const t = type(e);
            if (!t?.names.includes('uEnemy') || t.names.includes('uPlayer') || e.equals(p)) continue;
            killSeen++;
            const offset = damageOffset(e,t,cfg.damageDead);
            if (!cfg.ordinaryEnemies.includes(t.names[0]) || offset === null || protectedMode) {
                skipped.add(t.names[0]);
                continue;
            }
            const damage = e.add(offset);
            const flags = u32(damage, cfg.damageFlags);
            if (field(damage, cfg.damageDead, 'readU8') !== 0 ||
                flags === null || (flags & cfg.noDeathMask) !== 0) {
                skipped.add(t.names[0]);
                continue;
            }
            const current = f32(damage, battle.damageHp), limit = f32(damage, battle.damageMax);
            if (!(current > 0 && current <= limit)) {
                skipped.add(t.names[0]);
                continue;
            }
            killCovered++;
            const target = cheats.kill ? Math.min(1, current) : limit;
            if (current !== target) damage.add(battle.damageHp).writeFloat(target);
        }
    }
    function cheat() {
        if (!cheats.time) timeHeld = null;
        if (!cheats.hp && !cheats.dt && !cheats.time && !cheats.kill) return;
        const mediator = singleton('mediator');
        if (!id(mediator)) return;
        if (cheats.hp || cheats.dt) {
            const p = pointer(mediator,build.scene.player), t = id(p) ? type(p) : null;
            if (t?.names.includes('uPlayer')) {
                const hp = f32(p,battle.playerHp), hpMax = f32(p,battle.playerHpMax), dtMax = f32(p,battle.playerDtMax);
                if (cheats.hp && hp > 0 && hpMax > 0 && hp < hpMax) safe(() => p.add(battle.playerHp).writeFloat(hpMax));
                if (cheats.dt && dtMax > 0) safe(() => p.add(battle.playerDt).writeFloat(dtMax));
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
            // One-hit kill wins over the life lock when both are on.
            const unit = singleton('unit'), seen = new Set();
            let e = id(unit) ? pointer(unit,battle.enemyLine) : null;
            killSeen = 0; killCovered = 0;
            while (id(e) && !seen.has(id(e)) && seen.size < 256) {
                seen.add(id(e));
                const t = type(e);
                if (t?.names.includes('uEnemy')) {
                    killSeen++;
                    const damage = damageOffset(e,t);
                    if (damage !== null) {
                        killCovered++;
                        const where = e.add(damage+battle.damageHp);
                        const hp = f32(e,damage+battle.damageHp), hpMax = f32(e,damage+battle.damageMax);
                        if (cheats.kill) {
                            if (hp > 1) safe(() => where.writeFloat(1));
                        } else if (hp > 0 && hpMax > 0 && hp < hpMax) {
                            safe(() => where.writeFloat(hpMax));
                        }
                    }
                }
                e = pointer(e,build.scene.next);
            }
        }
    }
    function setCheat(name, on) {
        if (!Object.prototype.hasOwnProperty.call(cheats, name)) throw new Error('Unknown switch ' + name);
        if (name === 'time' && !on) timeHeld = null;
        cheats[name] = !!on;
        return cheatStatus();
    }
    const cheatStatus = () => ({...cheats, enemies:killSeen, enemiesCovered:killCovered, skipped:[...skipped]});
    function dxEnemy(e,pos) {
        const spheres = geometry(e,pos);
        const can = canStep(e,spheres);
        const mask = can ? filteredMask(e) : null;
        for (const [i,sphere] of (spheres || []).entries())
            sphere.activeAtCheck = mask === null ? null : !!(mask & (1 << i));
        return {spheres, canStep:can, inSphere:inSphere(spheres)};
    }
    function dxFrame(p) {
        const phase = active;
        active = null;
        const stepCheck = phase.early || phase.moveEarly || (id(p) ? early(p) : null);
        if (stepCheck) stepCheck.sample = phase.early ? "decision" : phase.moveEarly ? "beforeMove" : "frameEnd";
        return {matrices:cameraMatrices(), hpBefore:phase.hp, stepCheck};
    }
    return {install, begin, inspect, controls, dxFrame, dxEnemy, damageOffset, finish:() => safe(finish), type,
            cheat:() => safe(dx9 ? dxPractice : cheat), setCheat, cheatStatus, cheatRestored() { timeHeld = null; }};
})();
