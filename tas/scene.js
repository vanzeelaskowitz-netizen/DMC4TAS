// Preferred RVAs from the September 2026 layout in REPORT_01.
const sceneState = (() => {
    const base = Process.mainModule.base;
    const read = (p, off, method) => {
        if (!p || p.isNull()) return null;
        try { return p.add(off)[method](); } catch (_) { return null; }
    };
    const pointer = (p, off) => read(p, off, 'readPointer');
    const word = (p, off) => read(p, off, 'readU32');
    const root = rva => pointer(base, rva);
    const type = p => p && !p.isNull() ? combat.type(p) : null;
    const name = p => {
        if (build.game === "se") return type(p)?.names[0] ?? null;
        const getter = pointer(pointer(p, 0), 16);
        if (read(getter, 0, 'readU8') !== 0xb8 || read(getter, 5, 'readU8') !== 0xc3) return null;
        return read(pointer(pointer(getter, 1), 4), 0, 'readCString');
    };
    const flag = (p, off) => {
        const value = word(p, off);
        return value === null ? null : value !== 0;
    };

    function sample(diagnostic = false) {
        const layout = build.scene;
        const mediator = root(build.roots.mediator), unit = root(build.roots.unit), area = root(build.roots.area);
        const [areaSlot, gameSlot, missionSlot, roomSlot] = layout.slots;
        const game = pointer(area, gameSlot);
        const candidate = pointer(mediator, layout.player);
        if (build.game === "dx9") {
            const actor = pointer(unit, layout.firstLine);
            const menus = name(actor) === 'uTitle' ? [{name:'uTitle', address:actor.toString(),
                state:read(actor, layout.titleState, 'readU8'), phase:read(actor, layout.titlePhase, 'readU8'),
                cursor:word(actor, layout.cursor)}] : [];
            const room = pointer(area, roomSlot);
            const roomTypes = classes(room);
            const transition = pointer(mediator, layout.transition);
            return {areaClass:name(pointer(area, areaSlot)), gameClass:name(game),
                missionClass:name(pointer(area, missionSlot)), roomClass:name(room),
                paused:roomTypes.includes('aRoom') && read(room, layout.roomPaused, 'readU8') === 1,
                cutscene:!!transition && !transition.isNull() && !read(transition, layout.transitionDone, 'readU8'),
                mission:word(mediator, layout.mission), loading:flag(mediator,layout.loading),
                roomId:name(game) === 'aGame' ? word(game,layout.roomId) : null,
                playerPresent:!!candidate && !candidate.isNull(), menus};
        }
        const hasPlayer = type(candidate)?.names.includes('uPlayer') ?? false;
        const seen = new Set();
        const menus = [];
        let playerPresent = false;
        let paused = false;
        for (let offset = layout.firstLine; offset <= layout.lastLine; offset += layout.lineStride) {
            let actor = pointer(unit, offset);
            for (let count = 0; actor && !actor.isNull() && count < 256; count++) {
                const address = actor.toString();
                if (seen.has(address)) break;
                seen.add(address);
                if (hasPlayer && actor.equals(candidate)) playerPresent = true;
                const info = name(actor);
                // The game's own pause screen. It opens by itself whenever the window loses focus.
                if (info && info.startsWith('uGUI_Pause')) paused = true;
                if (info && /^(uTitle|uGUI_Title|uMissionMenu|uOptionMenu|uGUI_MissionSelect|uPauseMenu)/.test(info)) {
                    const item = {name: info, state: read(actor, 4, 'readU8'), phase: read(actor, 5, 'readU8')};
                    if (diagnostic) {
                        item.address = address;
                        item.line = offset;
                        item.words = Array.from({length: 40}, (_, i) => word(actor, i*4));
                    }
                    menus.push(item);
                }
                actor = pointer(actor, layout.next);
            }
        }
        menus.sort((a, b) => a.name.localeCompare(b.name) || a.state - b.state || a.phase - b.phase);
        return {
            loading: flag(mediator, layout.loading), mission: word(mediator, layout.mission),
            cutscene: flag(mediator, layout.cutscene), menu: flag(unit, layout.menu), menu4: word(unit, layout.menu4),
            gameMode: word(root(build.roots.gameMode), 4), playerPresent,
            areaClass: name(pointer(area, areaSlot)), gameClass: name(game),
            missionClass: name(pointer(area, missionSlot)), roomClass: name(pointer(area, roomSlot)),
            roomId: word(game, layout.roomId), menus, paused,
        };
    }

    function classes(obj) {
        const getter = pointer(pointer(obj, 0), 16);
        if (read(getter, 0, 'readU8') !== 0xb8 || read(getter, 5, 'readU8') !== 0xc3) return [];
        const names = [];
        for (let dti = pointer(getter, 1); dti && !dti.isNull() && names.length < 32; dti = pointer(dti, 16))
            names.push(read(pointer(dti, 4), 0, 'readCString'));
        return names;
    }
    return {sample, classes};
})();
