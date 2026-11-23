'use strict';

const se = build.game === "se";
const game = Process.mainModule;
const base = game.base;
const address = rva => base.add(rva);
const main = () => address(build.roots.main).readPointer();
const keyboard = () => address(build.roots.keyboard).readPointer();
const renderer = () => address(build.roots.renderer).readPointer();
const area = () => address(build.roots.area).readPointer();
const native = (dll, name, result, args) => new NativeFunction(
    Process.getModuleByName(dll).getExportByName(name), result, args, 'stdcall');
const queryFrequency = native('kernel32.dll', 'QueryPerformanceFrequency', 'int', ['pointer']);
const sleep = native('kernel32.dll', 'Sleep', 'void', ['uint']);
const timerPeriod = name => native('winmm.dll', name, 'uint', ['uint']);
const beginPeriod = timerPeriod('timeBeginPeriod'), endPeriod = timerPeriod('timeEndPeriod');
const foreground = native('user32.dll', 'GetForegroundWindow', 'pointer', []);
const getWindowStyle = native('user32.dll', 'GetWindowLongW', 'int', ['pointer', 'int']);
const isIconic = native('user32.dll', 'IsIconic', 'int', ['pointer']);
const peek = native('user32.dll', 'PeekMessageW', 'int', ['pointer', 'pointer', 'uint', 'uint', 'uint']);
const translate = native('user32.dll', 'TranslateMessage', 'int', ['pointer']);
const dispatch = native('user32.dll', 'DispatchMessageW', 'long', ['pointer']);
const repostQuit = native('user32.dll', 'PostQuitMessage', 'void', ['int']);
const activateApp = build.strategy.focus === 'activateApp';
const sendMessage = native('user32.dll', 'SendMessageW', 'long', ['pointer', 'uint', 'uint', 'int']);
const setFocus = activateApp ? (_, active) => sendMessage(gameWindow(), 0x1c, active, 0)
    : new NativeFunction(address(build.sites.focus), 'void', ['pointer', 'int'], 'thiscall');
const message = Memory.alloc(32);
const counterBuffer = Memory.alloc(8);
if (!queryFrequency(counterBuffer)) throw new Error('QueryPerformanceFrequency failed');
const frequency = counterBuffer.readS64().toNumber();
const imports = [];
const hooks = [];
const clockCallers = {qpc: {}, millis: {}};
function noteClockCall(kind, caller) {
    if (!caller || caller.compare(base) < 0 || caller.compare(base.add(game.size)) >= 0) return;
    const rva = caller.sub(base).toString();
    clockCallers[kind][rva] = (clockCallers[kind][rva] || 0) + 1;
}
let mode = globalThis.TASStartParked ? 'paused' : 'observe';
let parked = false;
let gateHeld = false;
let pumping = false;
let runUntil = null;
let audible = false;            // a stretch is being played with the game's own sound (rpc audible)
let recording = null;
let sceneKey = null;
let sceneEpoch = 0;
let stopped = false;
let pinned = false;
let steps = 0;
let frame = 0;
let entered = 0;
let tid = 0;
let last = null;
let current = null;
let clockObject;
let clockStart = 0;
let millisStart = 0;
let ticks = 0;
let virtual = false;
let targetWall = 0;       // next frame release time in real QPC ticks; 0 = resynchronise
let recordEverywhere = false; // test only: virtual clock outside gameplay scenes too
let replay = null;            // state of a scene-synchronised replay, see replayInput()
let lease = Date.now() + 15000;
let failure = null;
let entryCount = 0;
let readCount = 0;
let testFallback = false;
let presentCount = 0;
let pumpCount = 0;
let pumpMessages = 0;
let keyMap = [];
let plan = [];
let planPosition = 0;
let planRange = 0;
const pending = [];
const blank = new Uint8Array(256);
const emptyInput = { di: blank, vk: blank };
const nowQpc = () => {
    realQpc(counterBuffer);
    return counterBuffer.readS64().toNumber();
};

function importSlot(rva) {
    const slot = address(rva);
    const range = Process.getRangeByAddress(slot);
    return { slot, before: slot.readPointer(), protection: range.protection };
}

function writeSlot(entry, value) {
    if (!Memory.protect(entry.slot, Process.pointerSize, 'rw-')) throw new Error('IAT is not writable');
    try {
        entry.slot.writePointer(value);
    } finally {
        Memory.protect(entry.slot, Process.pointerSize, entry.protection);
    }
}

function replaceImport(entry, result, args, implementation) {
    entry.callback = new NativeCallback(implementation, result, args, 'stdcall');
    writeSlot(entry, entry.callback);
    imports.push(entry);
}

function attach(ptr, callbacks) {
    const listener = Interceptor.attach(ptr, callbacks);
    hooks.push(listener);
    return listener;
}

function restoreImports() {
    for (const entry of imports) {
        if (entry.slot.readPointer().equals(entry.callback)) writeSlot(entry, entry.before);
    }
}

function pump() {
    pumpCount++;
    for (let n = 0; n < 32 && peek(message, NULL, 0, 0, 1); n++) {
        pumpMessages++;
        if (message.add(4).readU32() === 0x12) {
            repostQuit(message.add(8).readS32());
            mode = 'shutdown';
            return;
        }
        translate(message);
        dispatch(message);
    }
}

function setVirtualClock(clock) {
    clockStart = clock.add(build.clock.previous).readS64().toNumber();
    millisStart = realMillis();
    ticks = 0;
    virtual = true;
}

function anchorClock(clock) {
    // Re-anchor elapsed-time baselines before the first real-time frame.
    const counter = nowQpc();
    clock.add(build.clock.previous).writeS64(int64(counter));
    if (build.strategy.clock === 'fixedBranch')
        clock.add(build.clock.absolute).writeFloat(counter * clock.add(build.clock.reciprocal).readFloat());
    else clock.add(build.clock.absolute).writeDouble(counter * clock.add(build.clock.reciprocal).readDouble());
    clock.add(build.clock.sampleCounter).writeS64(int64(counter));
    const accumulated = clock.add(build.clock.accum).readS64();
    clock.add(build.clock.deadline).writeS64(accumulated);
}

function restoreClock() {
    if (!virtual || !clockObject) return;
    anchorClock(clockObject);
    virtual = false;
}

// A loaded room with a player character that is not loading or in a cutscene.
function liveRoom() {
    if (!se && mode === 'observe') return false;
    const scene = sceneState.sample();
    return scene.playerPresent && !scene.loading && !scene.cutscene && (se || !!scene.roomClass);
}

function pinFocus() {
    const state = main();
    // Keep game focus handling internally active. The OS foreground is untouched.
    if (state.add(build.fields.active).readU8() === 0) setFocus(state, 1);
    return gameWindow();
}

function shutdownAtGate() {
    if (virtual || activateApp) {
        const state = main();
        setFocus(state, foreground().equals(gameWindow()) ? 1 : 0);
    }
    restoreClock();
    restoreImports();
    if (!stopped) endPeriod(1);
    stopped = true;
    if (pinned) {
        pinned = false;
        Script.nextTick(() => Script.unpin());
    }
}

function expire() {
    if (Date.now() <= lease) return false;
    failure = 'Controller heartbeat expired';
    mode = 'shutdown';
    return true;
}

function scenePointers() {
    const object = area();
    return object.isNull() ? [] : build.scene.slots
        .map(off => object.add(off).readPointer().toString());
}

function captureActive() {
    if (!recording || !recording.active) return false;
    if (Date.now() <= recording.deadline && recording.rows.length < 200000) return true;
    recording.active = false;
    recording.reason = 'Recording client stopped polling or the frame buffer filled';
    mode = 'record-finish';
    steps = 0;
    return false;
}

const onTitle = scene => scene.gameClass === 'aTitle' && scene.loading === false
    && scene.menus.some(item => item.name === (se ? 'uGUI_Title' : 'uTitle') && item.state >= 2);

const preMission = scene => (scene.gameClass === 'aTitle' || scene.gameClass === 'aAdvertise')
    && scene.loading === false && !scene.playerPresent;

const gameWindow = () => build.roots.hwnd ? address(build.roots.hwnd).readPointer() : main().add(build.fields.hwnd).readPointer();

function windowed() {
    if ((getWindowStyle(gameWindow(), -16) & 0x00c00000) !== 0x00c00000) return false;
    if (!renderer().add(build.fields.renderDevice).readPointer().equals(swapChain)) return false;
    if (build.strategy.render === 'dxgi')
        return getFullscreen(swapChain, fullscreen, NULL) === 0 && fullscreen.readS32() === 0;
    // Query the implicit D3D9 swap chain each time. Reset can change its presentation mode.
    if (getChain(swapChain, 0, chainOut) !== 0) return false;
    const chain = chainOut.readPointer();
    try {
        return comMethod(chain, 9, 'int', ['pointer','pointer'])(chain, parameters) === 0
            && parameters.add(32).readU32() === 1;
    } finally { comMethod(chain, 2, 'uint', ['pointer'])(chain); }
}

function battle(scene) {
    if (!scene.playerPresent || scene.loading || scene.cutscene || !scene.roomClass) return false;
    const snapshot = se ? combat.inspect() : dxBattle();
    return Boolean(snapshot?.player && snapshot.enemiesComplete && snapshot.enemies.length);
}

// Scene-synchronised replay of a recording made by record_boot.py. The recording is cut into
// segments by scene key; loading frames are never part of a segment. Each frame: while loading,
// wait; if the scene matches the current segment, play its next frame; if it matches a later
// segment, jump there (the game got there sooner than when recorded); otherwise wait with no keys.
const sceneKeyOf = scene => [scene.gameClass, scene.missionClass, scene.roomClass, scene.cutscene,
    scene.playerPresent,
    // Only top-level menu objects: their sub-objects (uGUI_Title::cGUI_BG ...) come and go
    // differently from run to run.
    scene.menus.filter(item => !item.name.includes('::')).map(item => item.name + ':' + item.state).join('+'),
].map(String).join('|');

function replayInput(scene) {
    const r = replay;
    const stall = reason => {
        if (++r.waited > r.patience) {
            r.done = true;
            r.reason = reason;
            mode = 'observe';
        }
        return emptyInput;
    };
    r.key = sceneKeyOf(scene);
    r.loading = Boolean(scene.loading);
    if (r.loading) return stall('Loading did not finish');
    for (let guard = 0; guard < 64; guard++) {
        const segment = r.segments[r.segment];
        if (!segment || (r.segment === r.segments.length - 1 && r.position >= segment.length)) {
            // Every recorded input has been played: idle until a battle exists, then park.
            if (battle(scene)) {
                r.done = true;
                mode = 'paused';
                steps = 0;
                return emptyInput;
            }
            return stall('No battle scene appeared after the recording ended');
        }
        if (r.key === segment.key) {
            if (r.position >= segment.length) return stall(`Scene stayed at ${r.key} after segment ${r.segment}`);
            r.waited = 0;
            const at = ++r.position;
            while (segment.rows[r.range].end < at) r.range++;
            return segment.rows[r.range].input;
        }
        let jump = -1;
        for (let j = r.segment + 1; j < Math.min(r.segments.length, r.segment + 10); j++) {
            if (r.segments[j].key === r.key) { jump = j; break; }
        }
        if (jump < 0) return stall(`Scene ${r.key} matches neither segment ${r.segment} nor the ones after it`);
        r.jumps.push([r.segment, r.position, jump]);
        r.segment = jump;
        r.position = 0;
        r.range = 0;
    }
    return stall('Replay segment search did not settle');
}

// Snapshots only make sense inside one room. When a timeline (or a take) reaches a door or any
// loading, the tool lets go: the loader needs the sound threads (holding them deadlocked the game
// on a loading screen, 2026-11-19), and nothing after the load could be restored anyway.
let parkRefused = 0;           // times a frozen mode could not park and the game was handed back
let sceneBlocked = false;      // a stepped-back timeline reached a loading screen and was stopped there
let routeScene = null;
let scenesLeft = 0;
function mediatorLoading() {
    const mediator = address(build.roots.mediator).readPointer();
    return !mediator.isNull() && mediator.add(build.scene.loading).readU32() !== 0;
}
function leaveForScene(reason) {
    if (savesKept) dropSaves(reason);
    freeShaderPins();
    snapshotsAlive = false;
    soundSticky = false;
    audible = false;
    keepExcursionVoices();
    holdSound(false);
    if (recording && recording.active) {
        recording.active = false;
        recording.reason = reason;
        recording.sealed = {scene: sceneState.sample(), battle: false, title: false};
    }
    mode = 'observe';
    steps = 0;
    runUntil = null;
    routeScene = null;
    scenesLeft++;
}

function releaseFrame(invocation) {
    if (stopped) return;
    clockObject = invocation.context.ecx;
    // Save states end with the room. A timeline deals with loadings itself (below); this is for
    // the stretches in between, when the player has the game.
    if (savesKept && !snapshotsAlive && (mediatorLoading() || scenePointers().join(',') !== savesScene))
        dropSaves('scene change');
    if (snapshotsAlive && mode !== 'observe' && mode !== 'shutdown'
        && (mediatorLoading() || (routeScene !== null && scenePointers().join(',') !== routeScene))) {
        if (soundSticky) {
            // A timeline that has been stepped back cannot go through a loading screen: loading
            // needs the sound threads, and they may only wake in the timeline's first state (see
            // live()). Stop here; the player steps back, or hands the game back and walks through.
            if (!sceneBlocked) {
                sceneBlocked = true;
                // A take recorded on such a timeline ends here, parked. Left to finish by itself
                // it would let the game run on into the loading with the sound threads asleep.
                if (recording && recording.active) {
                    recording.active = false;
                    recording.reason = 'Scene change';
                    recording.sealed = {scene: sceneState.sample(), battle: false, title: false};
                }
                mode = 'paused';
                steps = 0;
                runUntil = null;
            }
        } else {
            leaveForScene('Scene change');
        }
    }
    if (mode === 'observe' && !expire()) {
        // The player is in control. Keep running while the game window has the focus; when it
        // loses it in a room with a character, freeze right here instead of letting the game go
        // on unattended (or open its pause screen), and carry on when the focus comes back.
        if (foreground().equals(gameWindow()) || !liveRoom()) {
            restoreClock();
            return;
        }
    }
    holdSound(soundAsleep());
    captureActive();
    const nextScene = scenePointers().join(',');
    if (nextScene !== sceneKey) {
        sceneEpoch++;
        sceneKey = nextScene;
    }
    if (mode === 'running' && runUntil !== null && frame >= runUntil) {
        mode = 'paused';
        runUntil = null;
    }
    if (mode === 'record-finish') {
        const scene = sceneState.sample();
        recording.sealed = {scene, battle: battle(scene), title: onTitle(scene)};
        mode = (recording.inRoom ? liveRoom() : recording.sealed.battle) ? 'paused' : 'observe';
    }
    const wantsGate = () => (mode === 'paused' && steps <= 0) ||
        ((mode === 'record' || mode === 'observe') && !foreground().equals(gameWindow()));
    // While recording, switching away from the game freezes it in any room with a character; the
    // frames spent frozen are not part of the recording.
    const canGate = () => mode === 'paused' || (mode === 'record' && liveRoom())
        || (mode === 'observe' && liveRoom());
    if (wantsGate() && canGate()) {
        const idle = mode === 'observe';
        if (windowed()) {
            parked = true;
            let checkAt = Date.now() + 100;
            while (wantsGate() && !expire()) {
                // The controller may take over while the game sits here: follow the mode.
                holdSound(soundAsleep());
                captureActive();
                if (Date.now() >= checkAt) {
                    // Re-check the reason for being here: the mode can change while parked (for
                    // instance paused -> live at the title, where nothing should be held).
                    if (!windowed() || !canGate()) break;
                    checkAt = Date.now() + 100;
                }
                if (!gateHeld) {
                    pumping = true;
                    try { pump(); } finally { pumping = false; }
                }
                sleep(2);
            }
            parked = false;
            // Back to the player after an idle freeze: the real clock ran on meanwhile, so
            // re-anchor the engine's time base or the next frame would cover the whole wait.
            if (!virtual && ((idle && mode === 'observe') ||
                (!se && (mode === 'observe' || mode === 'shutdown')))) anchorClock(clockObject);
        }
        // Still wanting the gate here means the park was refused or broken off (the window test
        // failed, even if only for a moment). Never go on in a frozen mode without being parked:
        // a paused game that is not parked runs flat out with no input (2026-11-20: 670 frames a
        // second, steps counting down below zero, then a crash). Hand the game back instead.
        if (wantsGate() && (mode === 'paused' || !windowed())) {
            if (recording) {
                recording.active = false;
                recording.reason = 'Windowed mode was lost';
                recording.sealed = {scene: sceneState.sample(), battle: false, title: false};
            }
            parkRefused++;
            mode = 'observe';
            steps = 0;
        }
        if (mode === 'record-finish') {
            releaseFrame(invocation);
            return;
        }
    }
    if (mode === 'shutdown' || expire()) {
        shutdownAtGate();
        return;
    }
    if (mode === 'observe') {
        restoreClock();
        return;
    }
    let recordScene = null;
    if (mode === 'record' || mode === 'replay') {
        // While recording, only gameplay runs on the fixed 1/60 s clock. Menus, movies, loading and
        // cutscenes keep real time: the engine's frame limiter there would never see time pass.
        recordScene = sceneState.sample();
        const gameplay = recordEverywhere
            || (recordScene.playerPresent && !recordScene.loading && !recordScene.cutscene);
        if (gameplay && !virtual) setVirtualClock(clockObject);
        else if (!gameplay && virtual) restoreClock();
    } else if (!virtual) {
        setVirtualClock(clockObject);
    }
    if (mode === 'running' || mode === 'record' || mode === 'replay') {
        // Pace on the real performance counter: Date.now() has 1 ms steps and Sleep(1) can overshoot,
        // which used to run the game at about 32 frames per second.
        const pacing = mode;
        const period = frequency/60;
        let now = nowQpc();
        if (targetWall === 0 || now - targetWall > 4*period) targetWall = now;
        while (now < targetWall && mode === pacing && !expire()) {
            pump();
            if (targetWall - now > frequency/400) sleep(1);
            now = nowQpc();
        }
        if (mode !== pacing || (pacing === 'record' && !foreground().equals(gameWindow())
            && liveRoom())) {
            releaseFrame(invocation);
            return;
        }
        targetWall += period;
    } else {
        if (steps <= 0) {           // paused with nothing to step: must never run a frame
            parkRefused++;
            mode = 'observe';
            steps = 0;
            restoreClock();
            return;
        }
        steps--;
    }
    const nativeInput = mode === 'record';
    const hwnd = nativeInput ? gameWindow() : pinFocus();
    const replaying = mode === 'replay';
    const next = nativeInput || replaying ? undefined : plan[planRange];
    // replayInput() may end the replay by switching mode; this frame still runs with its result.
    const row = replaying ? replayInput(recordScene) : next ? next.input : emptyInput;
    if (next && ++planPosition === next.end) planRange++;
    ticks++;
    entered++;
    current = { input: row, entry: entryCount, number: entered, index: next ? planPosition : null, tid: invocation.threadId,
                nativeInput, scene: recording?.active ? (recordScene || sceneState.sample()) : null,
                virtualClock: virtual,
                step: mode === 'paused', reads: 0, presents: 0,
                gameWindow: hwnd.toString(), foreground: foreground().toString(),
                iconic: Boolean(isIconic(hwnd)), nativeInputResult: null, presentResult: null };
    invocation.tasFrame = current;
}

function decode() {
    const instance = keyboard();
    const binding = new Uint8Array(instance.add(build.fields.keyMap).readByteArray(256));
    const bytes = new Uint8Array(instance.add(build.fields.keyBits).readByteArray(128));
    const names = new Map();
    for (const [name, di, vk] of keyMap) {
        if (binding[di]) names.set(binding[di], name);
        else if (!names.has(vk)) names.set(vk, name);
    }
    const fields = {};
    for (const [index, label] of ['held', 'previous', 'pressed', 'released'].entries()) {
        const mask = bytes.slice(index*32, (index+1)*32);
        const keys = [];
        for (let key = 0; key < 256; key++) {
            if (mask[key >> 3] & (1 << (key & 7))) keys.push(names.get(key) || 'Key0x' + key.toString(16));
        }
        fields[label] = keys.sort();
        fields[label + 'Bits'] = Array.from(mask, b => b.toString(16).padStart(2, '0')).join('');
    }
    return fields;
}

const qpc = importSlot(build.imports.qpc);
const millis = importSlot(build.imports.millis);
const asyncKeys = importSlot(build.imports.keys);
const realQpc = new NativeFunction(qpc.before, 'int', ['pointer'], 'stdcall');
const realMillis = new NativeFunction(millis.before, 'uint', [], 'stdcall');
const realAsync = build.strategy.keys === 'asyncKey' && new NativeFunction(asyncKeys.before, 'int16', ['int'], 'stdcall');
const device = keyboard().add(build.fields.keyDevice).readPointer();
const swapChain = renderer().add(build.fields.renderDevice).readPointer();
for (const [name, hex] of Object.entries(build.signatures)) {
    const actual = new Uint8Array(address(build.sites[name]).readByteArray(hex.length / 2));
    if (!actual.every((b,i) => b === parseInt(hex.slice(2*i,2*i+2),16)))
        throw new Error('Build signature mismatch at ' + name);
}
if (Process.arch !== 'ia32' || (device.isNull() && realAsync) || swapChain.isNull()) {
    throw new Error('Game build or initialized keyboard/renderer layout mismatch');
}

const fullscreen = Memory.alloc(4);
const comCache = new Map();
function comMethod(object, slot, result, args) {
    const target = object.readPointer().add(slot*4).readPointer();
    const key = target.toString() + ':' + slot;
    if (!comCache.has(key)) comCache.set(key, new NativeFunction(target, result, args, 'stdcall'));
    return comCache.get(key);
}
const chainOut = Memory.alloc(4), parameters = Memory.alloc(56);
const getChain = build.strategy.render === 'd3d9' && comMethod(swapChain, 14, 'int', ['pointer','uint','pointer']);
const getFullscreen = build.strategy.render === 'dxgi' && new NativeFunction(swapChain.readPointer().add(11*4).readPointer(),
    'int', ['pointer', 'pointer', 'pointer'], 'stdcall');
if (!windowed()) throw new Error('DMC4 must be in windowed mode before attaching');

beginPeriod(1);
try {
    // A lost host leaves the IAT callbacks alive until the game reaches cleanup.
    Script.pin();
    pinned = true;
    if (se) combat.install(attach);
    attach(address(build.sites.focus), { onEnter(args) {
        // Also while recording gameplay: otherwise switching to another window makes the game open
        // its own pause screen (uGUI_Pause), and every later replay or snapshot starts paused.
        // And while the player is in control too: with the tool attached the game never learns
        // that its window lost the focus, so it never opens that pause screen; releaseFrame()
        // freezes it at the frame gate instead.
        if (stopped || mode === 'shutdown') return;
        if (!activateApp) args[0] = ptr(1);
        else if (args[1].toUInt32() === 0x1c) args[2] = ptr(1);
    } });
    // Clock policy. `virtual` means every QueryPerformanceCounter / timeGetTime call made by the
    // game thread through the main module's import table returns frame-count time (1/60 s per
    // frame). Substituting only the engine's own samples is not enough: other callers on the game
    // thread feed the frame-unit delta too (tried 2026-11-18, the delta drifted). While recording,
    // releaseFrame() switches `virtual` on only in gameplay scenes; menus, movies, loading and
    // cutscenes keep real time because their frame limiter would never see virtual time pass.
    // Do not put a probe directly on an indirect `call [iat]` instruction: it left +0x280e8 stale.
    attach(address(build.sites.sample), { onEnter() {
        if (current && this.threadId === current.tid) {
            const seconds = this.context.esi.add(build.clock.seconds);
            if (virtual && build.strategy.clock === 'fixedBranch') seconds.writeFloat(1/60);
            current.deltaSeconds = seconds.readFloat();
        }
    } });
    if (build.strategy.clock === 'fixedBranch') {
        let savedFlag = null;
        attach(address(build.sites.limiter), {onEnter() {
            if (!virtual || this.threadId !== tid) return;
            // Enter the native fixed-step branch with its deadline already met.
            savedFlag = clockObject.add(build.clock.fixedFlag).readU8();
            clockObject.add(build.clock.fixedFlag).writeU8(1);
            clockObject.add(build.clock.accum).writeS64(clockObject.add(build.clock.deadline).readS64());
        }});
        attach(address(build.sites.afterClock), {onEnter() {
            if (savedFlag === null || this.threadId !== tid) return;
            clockObject.add(build.clock.fixedFlag).writeU8(savedFlag);
            savedFlag = null;
        }});
    }
    const stateCallbacks = {
        onEnter(args) {
            if (!this.returnAddress.equals(address(build.sites.diReturn)) || args[1].toUInt32() !== 256) return;
            this.buffer = args[2];
            this.owner = current;
            readCount++;
        },
        onLeave(result) {
            if (!this.owner || this.threadId !== this.owner.tid) return;
            this.owner.nativeInputResult = result.toInt32();
            this.owner.reads++;
            if (this.owner.nativeInput) return;
            this.buffer.writeByteArray(this.owner.input.di.buffer);
            result.replace(0);
        }
    };
    const pollCallbacks = {
        onEnter() { this.override = current && !current.nativeInput && this.threadId === tid
            && this.returnAddress.equals(address(build.sites.pollReturn)); },
        onLeave(result) { if (this.override) result.replace(0); }
    };
    const installedDeviceMethods = new Set();
    function observeDeviceMethod(table, slot, callbacks) {
        const target = table.add(slot*4).readPointer();
        if (installedDeviceMethods.has(target.toString())) return;
        attach(target, callbacks);
        installedDeviceMethods.add(target.toString());
    }
    function observeDevice(object) {
        if (object.isNull()) return;
        const table = object.readPointer();
        observeDeviceMethod(table, 9, stateCallbacks);
        // Poll may reject the background device before the game gets to GetDeviceState.
        if (build.strategy.keys === 'keyState') observeDeviceMethod(table, 25, pollCallbacks);
    }
    observeDevice(device);
    attach(swapChain.readPointer().add(build.fields.presentSlot*4).readPointer(), {
        onEnter(args) {
            if (!args[0].equals(swapChain)) return;
            presentCount++;
            this.owner = current;
        },
        onLeave(result) {
            if (!se && result.toInt32() === -2005530520) invalidateDevice("D3D9 device lost");
            if (!this.owner) return;
            this.owner.presents++;
            this.owner.presentResult = result.toInt32();
        }
    });
    replaceImport(qpc, 'int', ['pointer'], function (out) {
        // Scheduling waits use wall time on their own threads.
        if (!virtual || Process.getCurrentThreadId() !== tid) return realQpc(out);
        if (build.strategy.clock === 'fixedBranch') noteClockCall('qpc', this.returnAddress);
        out.writeS64(int64(clockStart + Math.round(ticks*frequency/60)));
        return 1;
    });
    replaceImport(millis, 'uint', [], function () {
        if (!virtual || Process.getCurrentThreadId() !== tid) return realMillis();
        if (build.strategy.clock === 'fixedBranch') noteClockCall('millis', this.returnAddress);
        return (millisStart + Math.floor(ticks*1000/60)) >>> 0;
    });
    if (build.strategy.keys === 'keyState') {
        attach(asyncKeys.before, {
            onEnter(args) {
                if (!this.returnAddress.equals(address(build.sites.fallbackReturn))) return;
                readCount++;
                if (!current || current.nativeInput || this.threadId !== tid) return;
                this.owner = current;
                this.out = args[0];
            },
            onLeave(result) {
                if (!this.owner) return;
                this.out.writeByteArray(this.owner.input.vk.buffer);
                this.owner.fallbackReads = (this.owner.fallbackReads || 0) + 1;
                this.owner.reads++;
                result.replace(1);
            }
        });
        // Acceptance only: the sampler's private receiver has a null device. ESI still points
        // to the real game's bitmap. Never replace the shared keyboard object's device pointer.
        const noDevice = Memory.alloc(build.fields.keyDevice + 4);
        let lastDevice = device;
        attach(address(build.sites.keySampler), {onEnter() {
            if (!current || this.threadId !== tid) return;
            if (testFallback) { this.context.eax = noDevice; return; }
            const sampledDevice = this.context.eax.add(build.fields.keyDevice).readPointer();
            if (!sampledDevice.equals(lastDevice)) {
                observeDevice(sampledDevice);
                Interceptor.flush();
                lastDevice = sampledDevice;
            }
        }});
    } else replaceImport(asyncKeys, 'int16', ['int'], function (key) {
        return virtual && mode !== 'record' && !current?.nativeInput
            ? (current && current.input.vk[key] ? -32768 : 0) : realAsync(key);
    });
    attach(address(build.sites.frame), {
        onEnter() {
            tid = this.threadId;
            entryCount++;
            try {
                releaseFrame(this);
                if (!se && this.tasFrame && snapshotsAlive) {
                    // Both settings are sampled before dispatch. The engine joins its last
                    // batch and render pass before returning to the frame gate.
                    const render = renderer().add(build.fields.renderThread);
                    this.savedRenderThread = render.readU8();
                    render.writeU8(0);
                }
                if (!stopped) combat.cheat();       // practice aids, once per frame before it runs
                if (se && this.tasFrame) combat.begin(this.threadId);
            } catch (err) {
                failure = String(err);
                mode = 'shutdown';
                parked = false;
                shutdownAtGate();
            }
        },
        onLeave() {
            if (!this.tasFrame) return;
            try {
                if (this.savedRenderThread !== undefined)
                    renderer().add(build.fields.renderThread).writeU8(this.savedRenderThread);
                const { input, scene, ...row } = this.tasFrame;
                row.combat = se ? combat.finish() : dxBattle(row);
                if (!se) row.scene = sceneState.sample();
                row.keys = decode();
                row.qpcTicks = clockObject.add(build.clock.previous).readS64().toNumber();
                row.deltaUnits = clockObject.add(build.clock.units).readFloat();
                row.gameActive = main().add(build.fields.active).readU8();
                last = row;
                frame++;
                if (scene && recording) {
                    recording.rows.push({
                        frame, scene, held: row.keys.held, heldBits: row.keys.heldBits,
                        nativeInput: row.nativeInput, inputResult: row.nativeInputResult,
                        reads: row.reads, qpcTicks: row.qpcTicks, deltaSeconds: row.deltaSeconds,
                        virtualClock: row.virtualClock, deltaUnits: row.deltaUnits,
                    });
                }
                if (row.step) pending.push(row);
            } catch (err) {
                failure = String(err);
                mode = 'shutdown';
            } finally {
                current = null;
            }
        }
    });
    Interceptor.flush();
} catch (err) {
    endPeriod(1);
    restoreImports();
    for (const hook of hooks) hook.detach();
    if (pinned) Script.unpin();
    throw err;
}

// Append from the game before leaving the exception unhandled. The project path can be Unicode.
const openCrashLog = native('kernel32.dll','CreateFileW','pointer',
    ['pointer','uint','uint','pointer','uint','uint','pointer']);
const writeCrashLog = native('kernel32.dll','WriteFile','int',['pointer','pointer','uint','pointer','pointer']);
const closeCrashLog = native('kernel32.dll','CloseHandle','int',['pointer']);
const crashPath = Memory.allocUtf16String(globalThis.TASCrashLog);
let exceptionCount = 0;
Process.setExceptionHandler(details => {
    try {
        const where = Process.findModuleByAddress(details.address);
        if (exceptionCount >= 12 || details.type !== 'access-violation' || (where && /frida/i.test(where.name))) return false;
        exceptionCount++;
        const label = pointer => {
            const module = Process.findModuleByAddress(pointer);
            return module ? `${module.name}+0x${pointer.sub(module.base).toString(16)}` : pointer.toString();
        };
        const c = details.context;
        const stack = [];
        for (let i = 0; i < 96; i++) {
            try {
                const value = c.sp.add(i*4).readPointer();
                const module = Process.findModuleByAddress(value);
                if (module) stack.push(`+${(i*4).toString(16)}:${label(value)}`);
            } catch (_) { break; }
        }
        let trace = [];
        try { trace = Thread.backtrace(c, Backtracer.FUZZY).slice(0, 24).map(label); } catch (_) {}
        const line = JSON.stringify({
            time: new Date().toISOString(), type: details.type, at: label(details.address),
            memory: details.memory ? {operation: details.memory.operation, address: details.memory.address.toString()} : null,
            thread: Process.getCurrentThreadId(), gameThread: tid, mode, frame, parked,
            registers: {eax: c.eax.toString(), ecx: c.ecx.toString(), edx: c.edx.toString(), ebx: c.ebx.toString(),
                        esi: c.esi.toString(), edi: c.edi.toString(), ebp: c.ebp.toString(), esp: c.sp.toString()},
            trace, stack,
        }) + '\n';
        const bytes = Memory.allocUtf8String(line);
        const length = native('kernel32.dll','lstrlenA','int',['pointer'])(bytes);
        const log = openCrashLog(crashPath, 4, 3, NULL, 4, 0x80, NULL); // append, OPEN_ALWAYS
        if (!log.equals(ptr(-1))) {
            try { writeCrashLog(log, bytes, length, Memory.alloc(4), NULL); }
            finally { closeCrashLog(log); }
        }
    } catch (_) {}
    return false;
});

// Sound. sSound::UpdateThread and sSound::LoadThread (Ogg Vorbis streaming) keep running while the
// game thread is parked. Their stream state sits in the allocator arenas but their buffers do not,
// so after a snapshot restore they crashed (memcpy in the Vorbis read path, 2026-11-18). While the
// TAS holds the game, both thread bodies are replaced by a short sleep: no sound, no stale reads.
// After the first restore the hold stays on for good, because the sound state is then out of step.
const soundReplaced = [];
// Declared here, ahead of everything that hooks the game: a hooked function can be called by a
// game thread while this script is still loading, and must not meet a variable that is not there yet.
let snapshotsAlive = false;
// Named save states (tas/savestate.py) outlive a timeline: the service keeps their snapshots after
// control is handed back, so the GPU resource guard below stays on for as long as one exists.
// They end with the room: the first loading or scene change seen while no timeline is running
// drops them, and bumps sceneEpoch so that no snapshot from before can be restored by mistake.
let savesKept = false;
let savesScene = null;
let savesLost = null;           // why they were dropped, for the service to report
let soundTicks = 0;             // times a sound thread body has run; equal ticks = no sound in between
function gpuKept() { return snapshotsAlive || savesKept; }
function dropSaves(reason) {
    savesKept = false;
    savesScene = null;
    savesLost = reason;
    sceneEpoch++;
    if (!snapshotsAlive) freeShaderPins();
}
let soundHold = false;
let soundSticky = false;
let soundIdle = {};
const soundThreads = {};        // ids of the two sound threads, once they have been seen
let soundDirty = false;         // the sound threads have run since the voices were last drained
if (se) try {
    const soundTable = address(0xd8d494).readPointer().add(0x24).readPointer().readPointer();
    for (const slot of [5, 6]) {
        const target = soundTable.add(slot*4).readPointer();
        // propagate: a fault inside the game's own sound code must reach the crash recorder above
        // with its address and stack, not surface here as a script error without either.
        const original = new NativeFunction(target, 'void', ['pointer'], {abi: 'thiscall', exceptions: 'propagate'});
        Interceptor.replace(target, new NativeCallback(function (self) {
            const id = Process.getCurrentThreadId();
            if (id === tid) return original(self);
            soundThreads[id] = true;
            // Slot 6 is the music streaming thread (Sleep(50), then exe+0x805F80: stream voices
            // and the Vorbis reader). Its decoder state is restored by snapshots but its buffers
            // and file positions are not, so it crashed the first time it woke for a play with
            // sound while music was on (2026-11-21). In such a play only slot 5 (voices, sound
            // effects) wakes: effects and voices are heard, the music stays silent.
            if (audible && slot === 6 && !stopped) {
                soundIdle[id] = (soundIdle[id] || 0) + 1;
                sleep(4);
                return;
            }
            if (!soundHold || stopped) {
                soundDirty = true;
                soundTicks++;
                return original(self);
            }
            soundIdle[id] = (soundIdle[id] || 0) + 1;
            sleep(4);
        }, 'void', ['pointer'], 'thiscall'));
        soundReplaced.push(target);
    }
    Interceptor.flush();
} catch (err) {
    failure = 'Sound hold could not be installed: ' + err;
}
// Sound that may wake again and again (2026-11-21, for playing a script with the game's own sound).
// XAudio2 lives outside every snapshot. What used to go wrong when the sound threads woke in a
// restored state, and what is done about each:
// - The game had destroyed source voices that the restored memory still points to. DestroyVoice is
//   not carried out while snapshots exist: the voice is stopped, emptied and kept (never freed).
// - Voices kept playing buffers whose memory the restore took away, and reported "buffer ended"
//   into it. Whenever the sound threads have run and then gone to sleep, every known voice is
//   stopped and emptied before the first snapshot is taken or restored (drainSound): memory still
//   agrees with XAudio2 at that moment, so the reports land where they belong. From then on XAudio2
//   holds nothing, and every state reached without sound has the same idea of the voices.
// - XAudio2's own thread released effect objects into the game's heap later on (exe+0x80CA60, the
//   crash of 2026-11-20). Skipped on threads that are not the game's while snapshots exist.
// - The streaming thread reads files through the handle's own position, which no restore rewinds.
//   The positions are noted when the sound threads go to sleep with no snapshot around (that is
//   the sound state every later frame of the timeline carries) and put back before they wake.
const xaudio = {ready: false, error: null, voices: new Map(), self: false, created: 0, destroyCalls: 0,
    kept: 0, drains: 0, drainLeft: 0, releaseSkipped: 0, submits: 0, reads: 0, wakes: 0,
    purging: false, purgeArmed: false, purged: 0,
    files: new Map(), origin: null, positionsRestored: 0};
// Off since 2026-11-22: with it on, handing the game back after a step back crashed the music
// streaming thread three times in a row (memcpy in the Vorbis reader). Off is how hand-back
// behaved before the play-with-sound work; play-sound refuses to start while it is off.
let guardVoices = false;        // rpc guard(on, 'sound') switches it
function keepExcursionVoices() {
    for (const entry of xaudio.voices.values()) entry.excursion = false;
    xaudio.purgeArmed = false;
}
let drainSound = () => {};
let purgeSound = () => {};
let noteSoundFiles = () => {};
let restoreSoundFiles = () => {};
function xaudioSetup() {
  if (xaudio.ready) return;
  try {
    const engine = address(0xd8d494).readPointer().add(0x24).readPointer().add(0x20).readPointer();
    if (engine.isNull()) throw new Error('the game has no XAudio2 engine yet');
    const createAt = engine.readPointer().add(5*4).readPointer();
    const create = new NativeFunction(createAt, 'int',
        ['pointer', 'pointer', 'pointer', 'uint', 'float', 'pointer', 'pointer', 'pointer'], 'stdcall');
    // A voice of our own, only to learn where the source voice methods are.
    const format = Memory.alloc(20);
    format.writeU16(1);
    format.add(2).writeU16(2);
    format.add(4).writeU32(48000);
    format.add(8).writeU32(192000);
    format.add(12).writeU16(4);
    format.add(14).writeU16(16);
    format.add(16).writeU16(0);
    const slot = Memory.alloc(8);
    const made = create(engine, slot, format, 0, 2.0, NULL, NULL, NULL);
    if (made !== 0) throw new Error('CreateSourceVoice for the probe failed: ' + made);
    const probe = slot.readPointer(), table = probe.readPointer();
    const methodAt = index => table.add(index*4).readPointer();
    const destroyAt = methodAt(18), startAt = methodAt(19), stopAt = methodAt(20), submitAt = methodAt(21),
        getStateAt = methodAt(25);
    const destroy = new NativeFunction(destroyAt, 'void', ['pointer'], 'stdcall');
    // Outside the replacement below a call lands in the replacement first; `purging` sends it on.
    const destroyAgain = new NativeFunction(destroyAt, 'void', ['pointer'], 'stdcall');
    const start = new NativeFunction(startAt, 'int', ['pointer', 'uint', 'uint'], 'stdcall');
    const stop = new NativeFunction(stopAt, 'int', ['pointer', 'uint', 'uint'], 'stdcall');
    const flush = new NativeFunction(methodAt(22), 'int', ['pointer'], 'stdcall');
    const getState = new NativeFunction(getStateAt, 'void', ['pointer', 'pointer', 'uint'], 'stdcall');
    destroy(probe);
    const sourceVoice = voice => !voice.isNull() && voice.readPointer().equals(table);
    const entryOf = voice => {
        const key = voice.toString();
        let entry = xaudio.voices.get(key);
        if (!entry) {
            entry = {voice, started: true, gone: false};      // met while in use: assume it runs
            xaudio.voices.set(key, entry);
        }
        return entry;
    };
    const queued = voice => {
        const state = Memory.alloc(24);
        getState(voice, state, 0x100);                         // XAUDIO2_VOICE_NOSAMPLESPLAYED
        return state.add(4).readU32();
    };
    const settle = (voices, limit) => {
        const end = Date.now() + limit;
        let left = voices.filter(voice => queued(voice) > 0);
        while (left.length && Date.now() < end) {
            sleep(2);
            left = left.filter(voice => queued(voice) > 0);
        }
        return left.length;
    };
    attach(createAt, {
        onEnter(args) { this.slot = args[1]; },
        onLeave(result) {
            if (result.toInt32() !== 0) return;
            const voice = this.slot.readPointer();
            if (!sourceVoice(voice)) return;
            xaudio.created++;
            // excursion: made during a stretch played with sound. No snapshot ever contains such a
            // voice, so it can really go when that stretch is over (purgeSound).
            xaudio.voices.set(voice.toString(), {voice, started: false, gone: false, excursion: audible});
        }
    });
    attach(startAt, { onEnter(args) {
        if (xaudio.self) return;
        const entry = entryOf(args[0]);
        entry.started = true;
        entry.gone = false;                                    // a kept voice is in use again
    } });
    attach(stopAt, { onEnter(args) { if (!xaudio.self) entryOf(args[0]).started = false; } });
    attach(submitAt, { onEnter(args) {
        xaudio.submits++;
        const entry = entryOf(args[0]);
        entry.submits = (entry.submits || 0) + 1;              // a stream is fed again and again
        entry.tick = soundTicks;                               // when its newest buffer was queued
    } });
    attach(getStateAt, { onEnter(args) { if (!xaudio.self) entryOf(args[0]); } });
    Interceptor.replace(destroyAt, new NativeCallback(function (voice) {
        xaudio.destroyCalls++;
        if (!sourceVoice(voice)) return destroy(voice);       // submix and mastering voices
        if (guardVoices && snapshotsAlive && !xaudio.purging) {
            xaudio.self = true;
            try {
                stop(voice, 0, 0);
                flush(voice);
                settle([voice], 100);
            } finally {
                xaudio.self = false;
            }
            const entry = entryOf(voice);
            entry.started = false;
            entry.gone = true;
            xaudio.kept++;
            return;
        }
        xaudio.voices.delete(voice.toString());
        destroy(voice);
    }, 'void', ['pointer'], 'stdcall'));
    soundReplaced.push(destroyAt);
    drainSound = () => {
        // Not the music stream (a voice that is fed again and again): its thread sleeps through
        // every stretch with sound, so it is left exactly as before this guard existed. Emptying
        // it too was followed by a crash of the streaming thread on hand-back (2026-11-22,
        // memcpy in the Vorbis reader), which never happened while it was left alone.
        const all = [...xaudio.voices.values()].filter(entry => (entry.submits || 0) <= 1);
        xaudio.self = true;
        try {
            for (const entry of all) {
                stop(entry.voice, 0, 0);
                flush(entry.voice);
            }
            xaudio.drainLeft = settle(all.map(entry => entry.voice), 300);
            for (const entry of all) if (entry.started && !entry.gone) start(entry.voice, 0, 0);
        } finally {
            xaudio.self = false;
        }
        xaudio.drains++;
    };
    purgeSound = () => {
        // After a stretch with sound, at the gate before the silent state is put back: the voices
        // made in that stretch are stopped and empty, memory still is the stretch's own, and no
        // snapshot knows them. Destroy them for real, or every replay would leave 200 behind.
        xaudio.purging = true;
        try {
            for (const [key, entry] of [...xaudio.voices]) {
                if (!entry.excursion) continue;
                destroyAgain(entry.voice);
                xaudio.voices.delete(key);
                xaudio.purged++;
            }
        } finally {
            xaudio.purging = false;
        }
    };
    // exe+0x80CA60: mov ecx,[esp+4] / push esi / or esi,-1 / lock xadd [ecx+0x1c],esi
    const dropAt = address(0x80ca60);
    if (dropAt.readU32() !== 0x04244c8b || dropAt.add(4).readU32() !== 0xffce8356)
        throw new Error('unexpected bytes at the effect release');
    const drop = new NativeFunction(dropAt, 'uint', ['pointer'], 'stdcall');
    Interceptor.replace(dropAt, new NativeCallback(function (object) {
        const id = Process.getCurrentThreadId();
        if (guardVoices && snapshotsAlive && id !== tid && !soundThreads[id]) {
            xaudio.releaseSkipped++;
            return 1;
        }
        return drop(object);
    }, 'uint', ['pointer'], 'stdcall'));
    soundReplaced.push(dropAt);
    // Files the sound threads read, and where each one stands.
    const base = Process.getModuleByName('KERNELBASE.dll');
    const setPointer = new NativeFunction(base.getExportByName('SetFilePointerEx'), 'int',
        ['pointer', 'int64', 'pointer', 'uint'], 'stdcall');
    const fileSize = new NativeFunction(base.getExportByName('GetFileSizeEx'), 'int', ['pointer', 'pointer'], 'stdcall');
    attach(base.getExportByName('ReadFile'), { onEnter(args) {
        if (!soundThreads[this.threadId]) return;
        xaudio.reads++;
        xaudio.files.set(args[0].toString(), args[0]);
    } });
    const where = handle => {
        const out = Memory.alloc(16);
        if (!setPointer(handle, 0, out, 1) || !fileSize(handle, out.add(8))) return null;
        return {handle, position: out.readS64(), size: out.add(8).readS64()};
    };
    const pathOf = new NativeFunction(base.getExportByName('GetFinalPathNameByHandleW'), 'uint',
        ['pointer', 'pointer', 'uint', 'uint'], 'stdcall');
    xaudio.fileNames = () => [...xaudio.files.values()].map(handle => {
        const text = Memory.alloc(1040);
        const length = pathOf(handle, text, 512, 0);
        const at = where(handle);
        return {handle: handle.toString(), name: length ? text.readUtf16String(length).split('\\').slice(-3).join('\\') : null,
                position: at ? at.position.toString() : null};
    });
    xaudio.voiceList = () => [...xaudio.voices.values()].map(entry => ({voice: entry.voice.toString(),
        started: entry.started, gone: entry.gone, excursion: entry.excursion === true, submits: entry.submits || 0,
        queued: queued(entry.voice)}));
    // Voices that hold a buffer queued after sound tick `since` (rpc soundbusy). A save state made
    // at that tick knows nothing of such a buffer: its "buffer ended" report would free a block in
    // a heap that never allocated it. With `empty` the ones that are not streams are stopped and
    // emptied here and now, while memory still is the state those buffers belong to.
    xaudio.busy = (since, empty) => {
        const late = [...xaudio.voices.values()].filter(entry => !entry.gone && (entry.tick || 0) > since
            && queued(entry.voice) > 0);
        const loose = late.filter(entry => (entry.submits || 0) <= 1);
        let emptied = 0;
        if (empty && loose.length) {
            xaudio.self = true;
            try {
                for (const entry of loose) {
                    stop(entry.voice, 0, 0);
                    flush(entry.voice);
                }
                settle(loose.map(entry => entry.voice), 300);
                for (const entry of loose) if (entry.started) start(entry.voice, 0, 0);
            } finally {
                xaudio.self = false;
            }
            emptied = loose.length;
        }
        return {busy: late.length, streams: late.length - loose.length, emptied};
    };
    noteSoundFiles = () => {
        xaudio.origin = [...xaudio.files.values()].map(where).filter(Boolean);
    };
    restoreSoundFiles = () => {
        for (const file of xaudio.origin || []) {
            const now = where(file.handle);
            // Same handle, same file (as far as the size tells): move it back.
            if (now && now.size.equals(file.size) && !now.position.equals(file.position)) {
                setPointer(file.handle, file.position, NULL, 0);
                xaudio.positionsRestored++;
            }
        }
    };
    Interceptor.flush();
    xaudio.ready = true;
    xaudio.error = null;
  } catch (err) {
    xaudio.error = String(err);
  }
}
if (se) xaudioSetup();
// GPU resources. nDraw::Resource::Release (VA 0xC420B0, fastcall, refcount at +8) destroys the
// object and its Direct3D objects when the count runs out. Those Direct3D objects live in the
// driver, outside every snapshot, so after a restore the game could hold textures whose GPU side
// was already destroyed and crash releasing them again (2026-11-19, nDraw::Texture after a take
// with Q). While snapshots exist the last release is skipped: the resource is kept (leaked) and
// stays valid whatever frame is restored. Normal releases resume when control is handed back.
let keptResources = 0;
let releaseCalls = 0, finalReleases = 0;   // diagnostics: every call / calls that would destroy
let releaseFaults = 0, lastReleaseFault = null;
// Two more ways a GPU object died behind a snapshot's back (found 2026-11-21, after a release
// fault and a crash at exe+0x8432F2: ID3D11ShaderResourceView::GetDesc on a view that was gone):
// - Textures shared by name. sRender ([0x118D42C], in arena 3, so inside every snapshot) keeps a
//   table of them (12-byte entries: two name strings and the resource, count at +0x46D8A4) and
//   holds a reference itself. Such a resource (bit 31 of +0x20) is destroyed when its count goes
//   from 2 to 1, not from 1 to 0, so the test below used to let it through. The filters own such
//   textures; sEffect's uMultiBlurFilter makes four ("OcclusionFactor") for the blur of a hit and
//   drops them about 32 frames later. After a step back to a moment in between, the table entry
//   and the textures were back in memory with their GPU side gone: the drop faulted in Release,
//   the next blur got a dead texture from the table and crashed in GetDesc.
// - The deferred delete list (sRender +0x4640A0, count at +0x46D8A0). A destroyed resource that
//   was drawn recently is only queued; 0x66DFD0 sweeps the list every frame. Entries queued just
//   before the first snapshot would be destroyed after it and, after a step back, a second time.
//   The sweep is skipped while snapshots exist; the list is swept once control is handed back.
let pooledKept = 0, sweepsSkipped = 0;
let filterCalls = 0;
const filterEvents = [];
let guardPooled = true;         // test switch (rpc guard): false reproduces the old behaviour
const keptNames = {};           // name of a kept shared texture -> times kept (diagnostics)
function destroysOnRelease(resource) {
    const count = resource.add(8).readS32();
    return resource.add(0x20).readS32() < 0 ? count === 2 : count <= 1;
}
function sharedName(resource) {
    try {
        const render = address(0xd8d42c).readPointer();
        const count = Math.min(render.add(0x46d8a4).readU32(), 512);
        for (let i = 0; i < count; i++) {
            const entry = render.add(0x46c0a0 + 12 * i);
            if (entry.add(8).readPointer().equals(resource))
                return (entry.readPointer().readCString() + ' / ' + entry.add(4).readPointer().readCString()).slice(0, 80);
        }
    } catch (_) { /* diagnostics only */ }
    return '?';
}
if (se) try {
    const releaseAt = address(0x8420b0);
    // sub esp,8 / push ebx / push ebp / push esi / push edi / mov edi,ecx
    if (releaseAt.readU32() !== 0x5308ec83 || releaseAt.add(4).readU32() !== 0x8b575655)
        throw new Error('unexpected bytes at nDraw::Resource::Release');
    const release = new NativeFunction(releaseAt, 'void', ['pointer'], 'fastcall');
    Interceptor.replace(releaseAt, new NativeCallback(function (resource) {
        releaseCalls++;
        const final = !resource.isNull() && destroysOnRelease(resource);
        const pooled = final && resource.add(8).readS32() === 2;
        if (final) finalReleases++;
        // A room being unloaded releases everything it had: do not keep all of that for saves
        // that end with the room anyway.
        if (final && savesKept && !snapshotsAlive && mediatorLoading()) dropSaves('loading');
        if (gpuKept() && final && (guardPooled || !pooled)) {
            keptResources++;
            if (!pooled) return;        // an ordinary resource is simply left as it is (leaked)
            // A shared texture must leave sRender's table as usual, or the table (512 entries)
            // fills up: every blur effect of a hit adds four 1x1 "OcclusionFactor" textures, and a
            // kept one still looks in use, so it is never handed out again. So let the game's own
            // release run, but stamp the texture as used this frame (+4 against [0x118D478]): the
            // release then queues it on the deferred delete list instead of destroying it, and
            // that list is not swept while snapshots exist.
            pooledKept++;
            const name = sharedName(resource);
            keptNames[name] = (keptNames[name] || 0) + 1;
            resource.add(4).writeS32(address(0xd8d478).readS32());
        }
        // The game's own release can fault on a resource whose insides are stale (2026-11-21,
        // access violation at 0x3800008 during a step after a step back). Without this guard that
        // is a game crash; uncaught here it is a script error, which the service treats as fatal:
        // it quit and left the game frozen at the frame gate. Count it and carry on.
        try {
            release(resource);
        } catch (err) {
            releaseFaults++;
            lastReleaseFault = String(err);
        }
    }, 'void', ['pointer'], 'fastcall'));
    soundReplaced.push(releaseAt);           // reverted together with the sound hooks on unhook
    const sweepAt = address(0x66dfd0);
    // push ebx / push esi / mov esi,ecx / cmp byte [esi+0x1c],0
    if (sweepAt.readU32() !== 0xf18b5653 || sweepAt.add(4).readU32() !== 0x001c7e80)
        throw new Error('unexpected bytes at the deferred delete sweep');
    const sweep = new NativeFunction(sweepAt, 'void', ['pointer'], 'thiscall');
    Interceptor.replace(sweepAt, new NativeCallback(function (render) {
        if (gpuKept() && guardPooled) {
            // The list has room for 8192 entries and nothing checks that (exe+0x66B1B0). A
            // timeline gets its count back with every restore; saves kept through live play only
            // add to it, so they are given up before it can run over.
            if (!snapshotsAlive && render.add(0x46d8a0).readU32() > 6000) {
                dropSaves('deferred delete list nearly full');
            } else {
                sweepsSkipped++;
                return;
            }
        }
        sweep(render);
    }, 'void', ['pointer'], 'thiscall'));
    soundReplaced.push(sweepAt);
    // Diagnostics: the ambient occlusion filter drops (0x7051D0) and makes (0x705070) its two
    // shared textures ("mpLinearRTexture", "OcclusionFactor"); that is where both failures of
    // 2026-11-21 happened. Remember when it does.
    for (const [rva, kind] of [[0x7051d0, 'drop'], [0x705070, 'make']]) {
        attach(address(rva), { onEnter() {
            filterCalls++;
            if (filterEvents.length < 60)
                filterEvents.push({kind, frame, mode, alive: snapshotsAlive, focus: foreground().equals(gameWindow())});
        } });
    }
    Interceptor.flush();
} catch (err) {
    failure = 'GPU resource guard could not be installed: ' + err;
}

// The software mixer has one secondary DirectSound buffer at sSoundN+3D518.
// Both workers are idle before Stop. The driver can then consume no further
// samples until we restore the cursor and previous loop/play state.
let heldMix = null;
let mixStops = 0, mixRestarts = 0;
const mixOut = Memory.alloc(8);
function mixCall(buffer, slot, types, args) {
    const hr = comMethod(buffer, slot, 'int', ['pointer', ...types])(buffer, ...args);
    if (hr < 0) throw new Error('DirectSound buffer method ' + slot + ' failed: ' + hr);
}
function parkMix() {
    const buffer = address(build.roots.sound).readPointer().add(build.fields.mixBuffer).readPointer();
    if (heldMix) {
        if (!heldMix.buffer.equals(buffer)) throw new Error('DirectSound mixer changed');
        return;
    }
    if (buffer.isNull()) return;
    mixCall(buffer, 9, ['pointer'], [mixOut]);
    const flags = mixOut.readU32();
    mixCall(buffer, 18, [], []);
    mixCall(buffer, 4, ['pointer', 'pointer'], [mixOut, mixOut.add(4)]);
    heldMix = {buffer, flags, cursor:mixOut.readU32()};
    mixStops++;
}
function resumeMix() {
    if (!heldMix) return;
    const {buffer, cursor, flags} = heldMix;
    mixCall(buffer, 13, ['uint'], [cursor]);
    if (flags & 1) mixCall(buffer, 12, ['uint','uint','uint'], [0,0,(flags & 4) ? 1 : 0]);
    heldMix = null;
    mixRestarts++;
}
// Shader records in the Resource arena are populated lazily by D3D9. Keep a
// separate COM reference so a replay can bind the same native object. A rewind
// also transfers the game's reference when it restores or clears a record.
const shaders = new Map();
let shaderBindings = null, shaderCreates = 0, shaderReuses = 0;
function shaderRef(shader, slot) {
    return comMethod(shader, slot, 'uint', ['pointer'])(shader);
}
function freeShaderPins() {
    if (se) return;
    for (const entry of shaders.values()) shaderRef(entry.shader, 2);
    shaders.clear();
    shaderBindings = null;
}
function shaderOwners() {
    return new Set([...shaders.values()].filter(e => e.record.readPointer().equals(e.shader)));
}
function reconcileShaders(restored) {
    if (restored && shaderBindings) {
        const now = shaderOwners();
        for (const entry of shaders.values()) {
            if (now.has(entry) !== shaderBindings.has(entry)) shaderRef(entry.shader, now.has(entry) ? 1 : 2);
        }
    }
    shaderBindings = null;
}
function guardShaders() {
    const args = ['pointer','pointer','pointer'];
    for (const slot of [91, 106]) {
        const target = swapChain.readPointer().add(slot*4).readPointer();
        const create = new NativeFunction(target, 'int', args, 'stdcall');
        Interceptor.replace(target, new NativeCallback(function (device, code, out) {
            const register = build.shaderCallers[this.returnAddress.sub(base).toString()];
            if (!gpuKept() || !register || !device.equals(swapChain)) return create(device, code, out);
            const record = this.context[register];
            const size = record.add(8).readU32();
            if (!record.add(4).readPointer().equals(code) || !size || size > 1048576)
                return create(device, code, out);
            const key = [record,slot,Checksum.compute('sha256', code.readByteArray(size))].join('/');
            const entry = shaders.get(key);
            if (entry) {
                shaderRef(entry.shader, 1);
                out.writePointer(entry.shader);
                shaderReuses++;
                return 0;
            }
            const hr = create(device, code, out);
            if (hr >= 0) {
                const shader = out.readPointer();
                shaderRef(shader, 1);
                shaders.set(key, {record,shader});
                shaderCreates++;
            }
            return hr;
        }, 'int', args, 'stdcall'));
        soundReplaced.push(target);
    }
}
// DX9's two outer loops already sleep. Hold only the body called by each loop,
// after any previous call has returned. A fresh acknowledgement is required from both.
if (!se) {
    guardShaders();
    // D3DLOCK_DISCARD may return a different CPU address on each Lock. Eight draw
    // contexts retain that address and two cursors after Unlock. Give each dynamic
    // buffer a stable CPU mapping and submit the bytes drawn to the real lock.
    // The engine still issues every D3D draw, Lock, Unlock and Present.
    const staging = new Map(), mapped = [];
    for (const spec of build.drawStreams) {
        attach(address(build.sites[spec.site]), function () {
            if (!current || !snapshotsAlive || this.context.eax.isNull()) return;
            const render = this.context.ebx;
            const index = render.add(build.fields.streamIndex).readU32();
            const resource = render.add(spec.resources + 4*index).readPointer();
            if (!(resource.add(12).readU32() & 2)) return;  // only DISCARD buffers
            const bytes = spec.element * render.add(spec.capacity).readU32();
            const key = resource.toString() + '/' + bytes;
            if (!staging.has(key)) staging.set(key, Memory.alloc(bytes));
            const stable = staging.get(key), actual = this.context.eax;
            const workers = clockObject.add(build.fields.jobThreads).readU32();
            mapped.push({spec, resource, actual, stable, bytes, workers});
            resource.add(0x14).writePointer(stable);
            this.context.eax = stable;
        });
    }
    attach(address(build.sites.beforeUnlock), function () {
        const render = this.context.ebx;
        for (const {spec, resource, actual, stable, bytes, workers} of mapped) {
            const stride = (Math.floor(bytes / spec.element / workers) & ~31) * spec.element;
            for (let i = 0; i < workers; i++) {
                const view = render.add(spec.view + i*build.fields.drawStride);
                const used = view.add(4).readPointer().sub(stable.add(i*stride)).toUInt32();
                if (used > stride) throw new Error('DX9 draw stream cursor outside mapped slice');
                if (used) Memory.copy(actual.add(i*stride), stable.add(i*stride), used);
            }
            resource.add(0x14).writePointer(actual);
        }
        mapped.length = 0;
    });
    // The scheduler reselects the configured worker count between batches.
    // Override it at the selection point and restore it before the caller resumes.
    let activeBatch = null;
    attach(address(build.sites.dispatchJobs), {
        onEnter() {
            if (current && snapshotsAlive && this.threadId === tid && this.context.esi.equals(clockObject))
                activeBatch = this;
        },
        onLeave() {
            if (activeBatch !== this) return;
            if (this.workers !== undefined) clockObject.add(build.fields.jobThreads).writeU32(this.workers);
            activeBatch = null;
        }
    });
    attach(address(build.sites.dispatchCount), function () {
        if (!activeBatch || this.threadId !== tid) return;
        const count = this.context.esi.add(build.fields.jobThreads);
        activeBatch.workers = count.readU32();
        count.writeU32(1);
    });
    for (const name of ['defaultAlloc', 'defaultFree']) {
        attach(address(build.sites[name]), {onEnter() {
            if (gpuKept() && this.context.ecx.equals(address(build.snapshot.defaultAllocator).readPointer()))
                invalidateDevice('Default allocator mapping changed');
        }});
    }
    for (const name of ['soundUpdate', 'soundLoad']) {
        const site = address(build.sites[name]);
        const caller = address(build.sites[name + 'Return']);
        const run = new NativeFunction(site, 'void', ['pointer'], 'stdcall');
        Interceptor.replace(site, new NativeCallback(function (obj) {
            if (soundHold && !stopped && this.returnAddress.equals(caller)) {
                soundIdle[name] = (soundIdle[name] || 0) + 1;
                return;
            }
            soundTicks++;
            run(obj);
        }, 'void', ['pointer'], 'stdcall'));
        soundReplaced.push(site);
    }
    // Under the renderer lock, keep a final release on the engine's sweep list.
    // The normal decrement still runs. The use bit postpones the destructor.
    attach(address(build.sites.releaseLocked), function () {
        releaseCalls++;
        const resource = this.context.edi, render = this.context.esi;
        if (!gpuKept() || resource.add(8).readS32() !== 1 || resource.add(4).readU32()) return;
        const count = render.add(build.fields.resourceCount), list = render.add(build.fields.resourceList);
        const n = count.readU32();
        let i = 0;
        while (i < n && !list.add(i * 4).readPointer().equals(resource)) i++;
        if (i === n) {
            if (n >= (build.fields.resourceCount - build.fields.resourceList) / 4) { invalidateDevice('GPU resource list full'); return; }
            list.add(n * 4).writePointer(resource);
            count.writeU32(n + 1);
        }
        resource.add(4).writeU16(1);
        keptResources++;
    });
    const sweepAt = address(build.sites.sweep);
    const sweep = new NativeFunction(sweepAt, 'void', ['pointer'], 'stdcall');
    Interceptor.replace(sweepAt, new NativeCallback(function (render) {
        if (gpuKept()) { sweepsSkipped++; return; }
        sweep(render);
    }, 'void', ['pointer'], 'stdcall'));
    soundReplaced.push(sweepAt);
    attach(address(build.sites.resetPending), {onEnter(args) {
        if (gpuKept() && args[0].add(build.fields.resetFlags).readU32())
            invalidateDevice('D3D9 Reset pending');
    }});
    attach(swapChain.readPointer().add(16*4).readPointer(), {onEnter(args) {
        if (args[0].equals(swapChain)) invalidateDevice('D3D9 Reset');
    }});
    attach(address(build.sites.updatePlayer), {
        onEnter() {
            if (current && this.context.ecx.equals(address(build.roots.mediator).readPointer().add(build.scene.player).readPointer())) {
                this.actor = this.context.ecx;
                this.row = current;
            }
        },
        onLeave() {
            if (this.row && this.row === current) {
                (current.playerUpdates ??= []).push([0, 4, 8].map(o => this.actor.add(build.battle.buttons + o).readU32()));
            }
        }
    });
    attach(address(build.sites.enemyStep), function () { if (current) current.enemyStep = true; });
    Interceptor.flush();
}
function invalidateDevice(reason) {
    if (gpuKept()) {
        dropSaves(reason);
        leaveForScene(reason);
    }
}
function dxBattle(row = {}) {
    const field = build.battle;
    const p = address(build.roots.mediator).readPointer().add(build.scene.player).readPointer();
    if (p.isNull()) return null;
    const actor = (obj, player) => ({
        pointer: obj.toString(), position: [0,4,8].map(o => obj.add(field.position + o).readFloat()),
        action: player ? obj.add(field.action).readS32() : obj.add(field.enemyAction).readU8(),
        phase: obj.add(field.phase).readU8(), motion: obj.add(field.motion).readU16(),
        motionFrame: obj.add(field.motionFrame).readFloat(),
        hitStopTimer: obj.add(player ? field.playerStop : field.enemyStop).readFloat(),
        frozen: obj.add(player ? field.playerFrozen : field.enemyFrozen).readU8()
    });
    const player = actor(p, true);
    player.hp = p.add(field.playerHp).readFloat();
    player.stepCooldown = p.add(field.cooldown).readFloat();
    player.input = [0, 4, 8].map(o => p.add(field.buttons + o).readU32());
    const enemies = [], seen = new Set();
    let e = address(build.roots.unit).readPointer().add(field.enemyLine).readPointer();
    while (!e.isNull() && !seen.has(e.toString()) && seen.size < 256) {
        seen.add(e.toString());
        const types = sceneState.classes(e);
        if (types.includes('uEnemy')) {
            const item = actor(e, false);
            item.type = types[0];
            item.distance = Math.hypot(...item.position.map((v, i) => v - player.position[i]));
            item.hp = types.includes('uEm000') ? e.add(field.scarecrowHp).readFloat() : null;
            enemies.push(item);
        }
        e = e.add(build.scene.next).readPointer();
    }
    return {player, enemies, enemiesComplete:e.isNull(), inputUpdates:row.playerUpdates || [],
            enemyStep:!!row.enemyStep, rng:Checksum.compute('sha256', address(layout.rng).readByteArray(layout.rngSize))};
}
const soundSettled = () => se ? Object.values(soundIdle).some(count => count >= 3)
    : soundIdle.soundUpdate > 0 && soundIdle.soundLoad > 0;
// The sound threads sleep while the tool holds the game: frozen, stepping, playing a script, and
// while a take is recorded in a room (the player asked for sound on playback only, 2026-11-21).
// They run in a stretch that is played with sound (rpc audible), and whenever the player is in
// control.
function soundAsleep() {        // a declaration, not a const: the frame hook can run while this script loads
    return mode === 'paused' || (mode === 'running' && !audible)
        || (mode === 'record' && recording?.inRoom === true);
}
function holdSound(wanted) {
    wanted = wanted || soundSticky;
    if (wanted && !soundHold) soundIdle = {};
    if (!se && !wanted) resumeMix();
    soundHold = wanted;
}

// Write watch. When the game was started by watch_launch.py its eight allocator arenas were created
// with MEM_WRITE_WATCH, and the system can list the pages written since the last reset. Snapshots
// then read and write only those pages instead of the whole 561 MB image.
const getWriteWatch = native('kernel32.dll', 'GetWriteWatch', 'uint',
    ['uint', 'pointer', 'uint', 'pointer', 'pointer', 'pointer']);
const WATCH_CAPACITY = 45000;                       // the largest arena has 40960 pages
const watchBuffer = Memory.alloc(WATCH_CAPACITY*4);
const watchCount = Memory.alloc(Process.pointerSize);
const watchGranularity = Memory.alloc(4);
const layout = build.snapshot;
const arenas = layout.slots.map(rva => {
    const descriptor = address(rva).readPointer();
    if (!descriptor.readPointer().equals(address(layout.vt))) throw new Error('Unexpected allocator at ' + descriptor);
    const low = descriptor.add(layout.low).readPointer();
    const size = layout.size ? descriptor.add(layout.size).readU32()
        : descriptor.add(layout.end).readPointer().sub(low).toUInt32();
    return {low, size};
});
// Runs of written pages as [address, pageCount], lowest first; null when the watch is not available.
function writeWatch(reset) {
    const runs = [];
    for (const arena of arenas) {
        watchCount.writeU32(WATCH_CAPACITY);
        if (getWriteWatch(reset ? 1 : 0, arena.low, arena.size, watchBuffer, watchCount, watchGranularity) !== 0)
            return null;
        const count = watchCount.readU32();
        if (count >= WATCH_CAPACITY || watchGranularity.readU32() !== 4096) return null;
        const pages = new Uint32Array(watchBuffer.readByteArray(count*4));
        let start = 0, length = 0;
        for (let i = 0; i < count; i++) {
            if (length && pages[i] === start + length*4096) {
                length++;
            } else {
                if (length) runs.push([start, length]);
                start = pages[i];
                length = 1;
            }
        }
        if (length) runs.push([start, length]);
    }
    return runs;
}
const watchAvailable = writeWatch(false) !== null;

function status() {
    lease = Date.now() + 15000;
    return { build: build.id, game: build.game, rollback: build.rollback, clockCallers: se ? undefined : clockCallers,
             cheats: combat.cheatStatus(), sceneBlocked, parkRefused, watchAvailable, snapshotsAlive, soundSticky, scenesLeft,
             savesKept, savesLost, soundTicks, sceneEpoch, keptResources, releaseCalls, finalReleases, releaseFaults, lastReleaseFault, pooledKept, sweepsSkipped, keptNames, guardPooled, filterCalls, filterEvents, audible, guardVoices,
             shaderCreates, shaderReuses, mixStops, mixRestarts, mixCursor:heldMix?.cursor, soundIdle, arenas: arenas.length, sound: {ready: xaudio.ready, error: xaudio.error, voices: xaudio.voices.size, created: xaudio.created,
                     destroyCalls: xaudio.destroyCalls, kept: xaudio.kept, drains: xaudio.drains,
                     drainLeft: xaudio.drainLeft, releaseSkipped: xaudio.releaseSkipped, submits: xaudio.submits,
                     reads: xaudio.reads, files: xaudio.files.size, positionsRestored: xaudio.positionsRestored,
                     wakes: xaudio.wakes, purged: xaudio.purged, dirty: soundDirty, held: soundHold},
             mode, parked, stopped, failure, frame, entered, steps, tid, frequency, entryCount, planPosition,
             readCount, presentCount, pumpCount, pumpMessages, virtual, clockObject: clockObject?.toString(), foreground: foreground().toString(),
             gameWindow: gameWindow().toString(), gameActive: main().add(build.fields.active).readU8(), last };
}

rpc.exports = {
    status,
    fallbacktest(on) {
        if (se || !parked) throw new Error('Fallback test requires a parked DX9 frame');
        testFallback = on === true;
    },
    sounddetail() {             // diagnostics: the voices known and the files the sound threads read
        return {voices: xaudio.voiceList ? xaudio.voiceList() : [], files: xaudio.fileNames ? xaudio.fileNames() : []};
    },
    guard(on, which = 'gpu') {  // tests only: switch the 2026-11-21 additions of the GPU or sound guard
        if (which === 'sound') guardVoices = on !== false;
        else guardPooled = on !== false;
        return status();
    },
    pause() {
        if (stopped || !windowed()) throw new Error('Pausing requires a live windowed game');
        mode = 'paused';
        steps = 0;
            return status();
    },
    load(bindings, ranges, position = 0) {
        if (!parked) throw new Error('Pause before loading a timeline');
        keyMap = bindings;
        let end = 0;
        plan = ranges.map(range => {
            const di = new Uint8Array(256);
            const vk = new Uint8Array(256);
            for (const [scan, key] of range.keys) {
                di[scan] = 0x80;
                vk[key] = 0x80;
                if (key >= 0xa0 && key <= 0xa5) vk[[0x10, 0x11, 0x12][(key-0xa0) >> 1]] = 0x80;
            }
            end += range.count;
            return { end, input: { di, vk } };
        });
        planPosition = Math.min(end, position);
        planRange = plan.findIndex(range => range.end > planPosition);
        if (planRange < 0) planRange = plan.length;
        return { frames: end };
    },
    step(n) {
        if (!parked || steps || gateHeld || !Number.isInteger(n) || n <= 0 || n > 10000) throw new Error('Step requires a paused game and 1–10000 frames');
        steps = n;
        return status();
    },
    drain() { return pending.splice(0); },
    resume(until = null) {
        if (stopped || gateHeld || (until !== null && (!parked || until <= frame))) throw new Error('Cannot resume from this gate');
        runUntil = until;
            targetWall = 0;
        mode = 'running';
        steps = 0;
        return status();
    },
    live(clean = false) {
        if (gateHeld || recording) throw new Error('Cannot release an active snapshot or recording');
        // `clean`: the service has just restored the very first snapshot of the timeline. That is
        // the state the sound threads were put to sleep in, so memory once more agrees with what
        // XAudio2 holds (its queued buffers carry pointers into the arenas) and the threads may
        // wake up. Waking them in any other restored state crashes: 2026-11-19 on detach,
        // 2026-11-20 in XAudio2's buffer-end callback freeing a block that the rewound heap no
        // longer knew (exe+0x5F04F9).
        if (clean) {
            // File positions are no longer moved here: the streaming thread never runs between the
            // snapshot and this wake, so they cannot have changed, and moving them was one suspect.
            xaudio.wakes++;
            soundSticky = false;
            holdSound(false);
        }
        audible = false;
        keepExcursionVoices();
        // Wake the sound threads here and now (unless a step back keeps them asleep). The frame
        // hook only looks at this again when the game window is not in front, so without this a
        // game that was handed back while it had the focus stayed silent until the player
        // switched windows (seen 2026-11-21: 12 s of silence, music stream not fed).
        holdSound(false);
        sceneBlocked = false;
        if (!savesKept) freeShaderPins();
        snapshotsAlive = false;    // the service drops its snapshots with this call
        routeScene = null;
        mode = 'observe';
        steps = 0;
        return status();
    },
    audible(on) {
        // on: the service has just restored the first state of the timeline, the one every later
        // frame shares its idea of the sound with (see drainSound above); from here a stretch is
        // played with the sound threads awake. off: back to silence; the next snapshot gate
        // empties the voices again.
        if (gateHeld || recording) throw new Error('Cannot change the sound during a snapshot or a recording');
        const was = audible;
        audible = on === true;
        if (audible) {
            // File positions are no longer moved here: the streaming thread never runs between the
            // snapshot and this wake, so they cannot have changed, and moving them was one suspect.
            xaudio.wakes++;
            soundSticky = false;
        } else if (was && mode === 'paused') {
            xaudio.purgeArmed = true;       // the service restores the silent state next
        } else {
            keepExcursionVoices();          // the game went on by itself: they are its own now
        }
        return status();
    },
    replaystart(segments, patience = 3600) {
        if (gateHeld || recording || stopped || !windowed()) throw new Error('Replay requires a windowed game and an idle service');
        const built = segments.map(segment => {
            let end = 0;
            const rows = segment.rows.map(row => {
                const di = new Uint8Array(256);
                const vk = new Uint8Array(256);
                for (const [scan, key] of row.keys) {
                    di[scan] = 0x80;
                    vk[key] = 0x80;
                    if (key >= 0xa0 && key <= 0xa5) vk[[0x10, 0x11, 0x12][(key-0xa0) >> 1]] = 0x80;
                }
                end += row.count;
                return {end, input: {di, vk}};
            });
            return {key: segment.key, rows, length: end};
        });
        if (!built.length || built.some(segment => !segment.length)) throw new Error('Empty replay segment');
        replay = {segments: built, segment: 0, position: 0, range: 0, waited: 0, patience,
                  done: false, reason: null, key: null, loading: false, jumps: []};
        targetWall = 0;
        steps = 0;
        mode = 'replay';
        return status();
    },
    replaystatus() {
        lease = Date.now() + 15000;
        if (!replay) throw new Error('No replay session');
        const {segment, position, waited, done, reason, key, loading, jumps} = replay;
        return {segment, segments: replay.segments.length, position,
                length: replay.segments[segment]?.length ?? 0, expected: replay.segments[segment]?.key ?? null,
                waited, done, reason, key, loading, jumps: jumps.length, mode, parked, frame, virtual};
    },
    recordscene(diagnostic = false) {
        const scene = sceneState.sample(diagnostic);
        return {scene, title: onTitle(scene), ready: preMission(scene), windowed: windowed()};
    },
    recordstart(bindings, test = false, advertise = false, everywhere = false, inRoom = false) {
        recordEverywhere = everywhere === true;
        if (gateHeld || recording || !windowed()) throw new Error('Recording requires a windowed game and idle service');
        const scene = sceneState.sample();
        // Two kinds of recording. The boot route starts at the title (or the attract movie it falls
        // into). A take starts at the frame gate in a room with a character and ends parked there.
        const allowed = inRoom ? (parked && liveRoom()) : preMission(scene);
        if (!allowed || (test && !parked))
            throw new Error(inRoom ? 'A take must start parked in a room with a character' : 'Recording must start at the title');
        recording = {rows: [], deadline: Date.now() + 120000, active: true, reason: null, sealed: null,
                     inRoom: inRoom === true};
        const mapping = Array.from(new Uint8Array(keyboard().add(build.fields.keyMap).readByteArray(256)));
        if (!test) {
            keyMap = bindings;
            targetWall = 0;
            mode = 'record';
        }
        return {frame, mapping};
    },
    recordpoll() {
        if (!recording) throw new Error('No recording session');
        captureActive();
        recording.deadline = Date.now() + 120000;
        return {rows: recording.rows.splice(0), reason: recording.reason, parked, frame};
    },
    recordfinish() {
        if (!recording) throw new Error('No recording session');
        if (!recording.sealed) mode = 'record-finish';
        return status();
    },
    recordstop() {
        if (!recording?.sealed || mode === 'record-finish' || (mode === 'paused' && !parked)) return null;
        const result = {...recording.sealed, rows: recording.rows, reason: recording.reason, frame, parked};
        recording = null;
        return result;
    },
    holdgate(collect = false) {
        if (!parked || steps || current || gateHeld) throw new Error('Snapshot requires an idle frame gate');
        if (pumping) return null;
        if (!se) {
            const scene = sceneState.sample();
            if (scene.loading || !(preMission(scene) || (scene.playerPresent && scene.roomClass)))
                throw new Error('Snapshot requires a title screen or a loaded battle room');
        }
        // Wait until the sound threads are provably idling in the stub before touching memory.
        if (soundReplaced.length && !(soundHold && soundSettled())) return null;
        // The sound threads have run since the last time and are asleep now, and nothing has been
        // restored in between: memory and XAudio2 agree. Empty the voices before that changes.
        if (se) xaudioSetup();
        else parkMix();
        if (se && soundDirty && guardVoices) {
            drainSound();
            if (xaudio.purgeArmed) purgeSound();
            if (!snapshotsAlive) noteSoundFiles();
            soundDirty = false;
        }
        xaudio.purgeArmed = false;
        gateHeld = true;
        if (!se && !snapshotsAlive) savesLost = null;
        snapshotsAlive = true;     // from this instant on, GPU resources are kept (see above)
        if (routeScene === null) routeScene = scenePointers().join(',');
        try {
            if (!virtual) setVirtualClock(clockObject);
            const state = se ? combat.inspect() : dxBattle();
            // A whole-heap snapshot does not care what is in the room; it only needs a loaded room
            // with a player character (menus, movies and loading screens are not supported).
            if (se && !state?.player)
                throw new Error('Snapshot requires a loaded room with a player character');
            const scene = scenePointers();
            if (scene.length !== 4 || (se ? scene : scene.slice(0, 2)).some(p => p === '0x0')) throw new Error('Room is not loaded');
            if (!renderer().add(build.fields.renderDevice).readPointer().equals(swapChain))
                throw new Error('Swap chain changed; reattach before taking a snapshot');
            // With the gate held nothing on the game thread writes memory (the message pump is
            // stopped too), so the pages listed here are exactly what changed since the last reset.
            if (!se) shaderBindings = shaderOwners();
            const dirty = collect && watchAvailable ? writeWatch(true) : null;
            return { ...status(), layout, dirty, ticks, clockStart, millisStart, base:base.toString(), scene, sceneEpoch,
                combat:state, clock:clockObject.toString(), main:main().toString(),
                keyboard:keyboard().toString(), renderer:renderer().toString(),
                swapChain:swapChain.toString(), area:area().toString() };
        } catch (err) {
            gateHeld = false;
            throw err;
        }
    },
    releasegate(saved = null, reset = false) {
        if (!gateHeld) throw new Error('Snapshot gate is already released');
        // After a restore the controller's own writes are not game changes: forget them.
        if (!se) reconcileShaders(!!saved);
        if (reset && watchAvailable) writeWatch(true);
        if (saved) {
            sceneBlocked = false;
            combat.cheatRestored();
            soundSticky = true;
            ({frame, ticks, clockStart, millisStart, last} = saved);
            entered = frame;
            pending.length = 0;
        }
        gateHeld = false;
        return status();
    },
    keepsaves(on) {
        // The service holds (on) or no longer holds (off) named save states of this room.
        if (on === true) {
            if (!savesKept) savesScene = scenePointers().join(',');
            savesKept = true;
            savesLost = null;
        } else {
            savesKept = false;
            savesScene = null;
            if (!snapshotsAlive) freeShaderPins();
        }
        return status();
    },
    soundbusy(since = 0, empty = false) {
        // Asked before a save state from before the last stretch of sound is restored: does
        // XAudio2 still hold buffers that were queued after that save was made?
        xaudioSetup();
        if (!xaudio.ready || !xaudio.busy) return {ready: false, error: xaudio.error, ticks: soundTicks};
        return {ready: true, ticks: soundTicks, asleep: soundHold && soundSettled(), ...xaudio.busy(since, empty === true)};
    },
    cheat(name, on) { return combat.setCheat(name, on); },
    shutdown() { mode = 'shutdown'; return status(); },
    unhook() {
        if (!stopped) throw new Error('Wait for the frame boundary before removing hooks');
        if (!se) { resumeMix(); freeShaderPins(); }
        for (const hook of hooks) hook.detach();
        for (const target of soundReplaced) Interceptor.revert(target);
        Interceptor.flush();
        return imports.every(entry => entry.slot.readPointer().equals(entry.before));
    }
};

// Keep every SE-only RPC inaccessible even to direct Controller users.
if (!se) {
    const allowed = new Set(['status','pause','load','step','drain','resume','live','recordscene','fallbacktest','holdgate','releasegate','shutdown','unhook','cheat',
        'recordstart','recordpoll','recordfinish','recordstop','keepsaves']);
    for (const name of Object.keys(rpc.exports)) {
        if (!allowed.has(name)) rpc.exports[name] = () => { throw new Error('Unavailable on DX9: ' + name); };
    }
}
