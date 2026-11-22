$ErrorActionPreference = 'Stop'
if ([Environment]::Is64BitProcess) {
    & "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -File $PSCommandPath
    exit $LASTEXITCODE
}
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $game = @(Get-Process -Name 'DevilMayCry4SpecialEdition' -ErrorAction SilentlyContinue)
    if ($game.Count -ne 1) { throw "Expected one running game, found $($game.Count)" }
    $g = $game[0]
    $threads = foreach ($thread in ($g.Threads | Sort-Object StartTime)) {
        [ordered]@{tid=$thread.Id; started=$thread.StartTime.ToString('o'); cpu_ms=$thread.TotalProcessorTime.TotalMilliseconds; state=$thread.ThreadState.ToString()}
    }
    $modules = foreach ($module in $g.Modules) {
        [ordered]@{name=$module.ModuleName; path=$module.FileName; base=$module.BaseAddress.ToInt64(); size=$module.ModuleMemorySize}
    }
    $info = [ordered]@{pid=$g.Id; started=$g.StartTime.ToString('o'); time=(Get-Date).ToString('o'); threads=$threads; modules=$modules}
    $info | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'logs\process.json' -Encoding utf8
    py -3.13 -B 'tools\live_read.py'
    if ($LASTEXITCODE -ne 0) { throw "Read-only probe exited with $LASTEXITCODE" }
} finally {
    Pop-Location
}

