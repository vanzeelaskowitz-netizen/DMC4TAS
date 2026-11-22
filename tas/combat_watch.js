'use strict';
const frameEntry = Process.mainModule.base.add(0x5d1a70);
let batch = [], frameNo = 0, recording = true;
const hooks = [];
function watchHook(address, callbacks) {
    hooks.push(Interceptor.attach(address, callbacks));
}
function flush() {
    if (batch.length) send({rows:batch.splice(0)});
}
if (Process.arch !== 'ia32' || frameEntry.readU32() !== 0x83ec8b55)
    throw new Error('Unsupported game frame entry');
combat.install(watchHook);
watchHook(frameEntry,{
    onEnter() {
        this.track = recording;
        if (this.track) combat.begin(this.threadId);
    },
    onLeave() {
        if (!this.track || !recording) return;
        batch.push({frame:++frameNo, combat:combat.finish()});
        if (batch.length>=16) flush();
    }
});
rpc.exports = {
    stop() {
        recording = false;
        for (const hook of hooks) hook.detach();
        Interceptor.flush();
        flush();
        send({end:frameNo});
        return frameNo;
    }
};
