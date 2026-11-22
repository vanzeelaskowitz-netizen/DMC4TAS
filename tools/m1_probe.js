'use strict';

const game = Process.mainModule;
const base = game.base;
const user = Process.getModuleByName('user32.dll');
function win(name, result, args) {
    return new NativeFunction(user.getExportByName(name), result, args, 'stdcall');
}
const getForeground = win('GetForegroundWindow', 'pointer', []);
let counts = {};
let stacks = {};
let latest = {};
function label(address) {
    const module = Process.findModuleByAddress(address);
    return module ? `${module.name}+${address.sub(module.base)}` : address.toString();
}
function hit(name, ctx, extra) {
    counts[name] = (counts[name] || 0) + 1;
    if (counts[name] === 1) {
        stacks[name] = { tid: ctx.threadId, extra, accurate: Thread.backtrace(ctx.context, Backtracer.ACCURATE).map(label),
                        fuzzy: Thread.backtrace(ctx.context, Backtracer.FUZZY).map(label) };
    }
}
function hook(address, name) {
    Interceptor.attach(address, { onEnter() { hit(name, this); } });
}
const keyboard = base.add(0xd8d468).readPointer();
const device = keyboard.add(0xa08).readPointer();
const getDeviceState = device.readPointer().add(9*4).readPointer();
Interceptor.attach(getDeviceState, {
    onEnter() { this.keyboard = this.returnAddress.equals(base.add(0x6b8541)); if (this.keyboard) hit('keyboardCall', this); },
    onLeave(result) { if (this.keyboard) latest.keyboardResult = result.toString(); }
});
hook(base.add(0x6b8620), 'keyboardUpdate');
const renderer = base.add(0xd8d42c).readPointer();
const swapChain = renderer.add(0x4727d8).readPointer();
const present = swapChain.readPointer().add(8*4).readPointer();
Interceptor.attach(present, {
    onEnter(args) { this.isGame = args[0].equals(swapChain); if (this.isGame) hit('present', this, label(present)); },
    onLeave(result) { if (this.isGame) latest.presentResult = result.toString(); }
});
let delta = null;
Interceptor.attach(base.add(0x6b4dcb), { onEnter() {
    delta = this.context.esi.add(0x280e8).readFloat();
}
});
for (const entry of game.enumerateImports()) {
    if (!['QueryPerformanceCounter', 'timeGetTime', 'GetAsyncKeyState', 'GetForegroundWindow',
          'Sleep', 'GetTickCount', 'D3D10CreateDeviceAndSwapChain', 'D3D11CreateDeviceAndSwapChain'].includes(entry.name)) continue;
    if (!entry.slot) continue;
    Interceptor.attach(entry.slot.readPointer(), { onEnter() {
        if (this.returnAddress.compare(base) < 0 || this.returnAddress.compare(base.add(game.size)) >= 0) return;
        hit(`${entry.name}@${this.returnAddress.sub(base)}`, this, entry.slot.sub(base).toString());
    } });
}
rpc.exports = { sample() {
    const main = base.add(0xd8d45c).readPointer();
    const result = { foreground: getForeground().toString(), active: main.add(0x2555).readU8(),
                     keyboardEnabled: keyboard.add(0xa00).readU8(), delta, counts, stacks, latest };
    counts = {}; stacks = {}; latest = {}; return result;
},
    watch(rvas) { for (const rva of rvas) hook(base.add(rva), 'game@' + rva.toString(16)); } };
