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
    const name = p => type(p)?.names[0] ?? null;
    const flag = (p, off) => {
        const value = word(p, off);
        return value === null ? null : value !== 0;
    };

    function sample(diagnostic = false) {
        const mediator = root(0xd54b34), unit = root(0xd8d450), area = root(0xd8d448);
        const game = pointer(area, 0x3830);
        const candidate = pointer(mediator, 0x24);
        const hasPlayer = type(candidate)?.names.includes('uPlayer') ?? false;
        const seen = new Set();
        const menus = [];
        let playerPresent = false;
        let paused = false;
        for (let offset = 0x5c; offset <= 0x29c; offset += 0x18) {
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
                actor = pointer(actor, 0x14);
            }
        }
        menus.sort((a, b) => a.name.localeCompare(b.name) || a.state - b.state || a.phase - b.phase);
        return {
            loading: flag(mediator, 0x14c), mission: word(mediator, 0x150),
            cutscene: flag(mediator, 0x1a4), menu: flag(unit, 0x2b4), menu4: word(unit, 0x40),
            gameMode: word(root(0xd84c78), 4), playerPresent,
            areaClass: name(pointer(area, 0x382c)), gameClass: name(game),
            missionClass: name(pointer(area, 0x3834)), roomClass: name(pointer(area, 0x3838)),
            roomId: word(game, 0x88), menus, paused,
        };
    }

    return {sample};
})();
