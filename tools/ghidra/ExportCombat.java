// Export only explicitly requested functions. Arguments: output-directory, preferred VAs.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;

public class ExportCombat extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) throw new IllegalArgumentException("output directory and VAs required");
        Path out = Files.createDirectories(Path.of(args[0]));
        DecompInterface decompiler = new DecompInterface();
        var done = new HashSet<String>();
        try {
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
            for (int i = 1; i < args.length; i++) {
                var at = toAddr(Long.decode(args[i]));
                Function f = getFunctionContaining(at);
                if (f == null) {
                    // Auto-analysis missed some small functions; define one at the requested entry.
                    // With -readOnly the change stays in memory and the project is not modified.
                    f = createFunction(at, null);
                    if (f != null) println("Defined a function at " + args[i]);
                }
                if (f == null) { printerr("No function at " + args[i]); continue; }
                String va = f.getEntryPoint().toString();
                if (!done.add(va)) continue;
                DecompileResults result = decompiler.decompileFunction(f, 60, monitor);
                if (!result.decompileCompleted()) throw new IllegalStateException(va + " " + result.getErrorMessage());
                Files.writeString(out.resolve(va + ".c"), "// " + currentProgram.getName() + " VA " + va + "\n" + result.getDecompiledFunction().getC());
                println("Exported " + va);
            }
        } finally { decompiler.dispose(); }
    }
}
