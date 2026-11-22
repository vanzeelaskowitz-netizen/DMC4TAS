<#
.SYNOPSIS
Decompiles the game functions listed in tools\ghidra\functions.txt into dump\decomp\ with Ghidra.

.DESCRIPTION
The repository holds no game code. This script rebuilds the decompiled text locally from your own
copy of the game, so the evidence links in docs\MAP.md work on your machine.

1. Image. The Steam executable is encrypted on disk, so the code is read from the running game into
   dump\dmc4se_mem.exe (tools\dump_memory.py, read-only access). Start the game normally first, not
   with start_game.bat: the TAS hooks rewrite code bytes, and the .text hash check below rejects such
   an image. Skipped when dump\dmc4se_mem.exe already exists.
2. Ghidra project. Imported and auto-analysed once (this can take tens of minutes); later runs reuse it.
3. Export. tools\ghidra\ExportCombat.java writes one <VA>.c file per function.

Needs Ghidra 12.2 or later and a JDK it accepts (JDK 25 for 12.2). The stock analyzeHeadless.bat
is not used: Java is started directly, with Ghidra's settings and cache kept under dump\ghidra-home.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\decompile.ps1 -GhidraDir C:\ghidra_12.2_PUBLIC -JavaHome C:\jdk-25

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File tools\decompile.ps1 -Only 0x9D1A70,0xC0CA60
#>
param(
    [string]$GhidraDir = $env:GHIDRA_INSTALL_DIR,
    [string]$JavaHome = $env:JAVA_HOME,
    [string]$ProjectDir,
    [string]$ProjectName = 'dmc4se',
    [string]$OutDir,
    [string[]]$Only,
    [string]$MaxHeap = '8G'
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$dump = Join-Path $root 'dump'
$image = Join-Path $dump 'dmc4se_mem.exe'
$list = Join-Path $PSScriptRoot 'ghidra\functions.txt'
if (-not $ProjectDir) { $ProjectDir = Join-Path $dump 'ghidra-project' }
if (-not $OutDir) { $OutDir = Join-Path $dump 'decomp' }

# .text of an untouched build 24329835 image, as dumped from memory.
$TextRva = 0x1000
$TextSize = 10882560
$TextSha = 'd4a76ad401f44315eee4437c7965f31191938fb0e42f9c0e8fe4d28cfa2265de'

function Get-ShortPath([string]$path) {
    # Ghidra and its scripts mishandle non-ASCII paths; Windows 8.3 folder names avoid that.
    # For a file only the folder is shortened: Ghidra names an imported program after its file.
    $fso = New-Object -ComObject Scripting.FileSystemObject
    if (Test-Path -LiteralPath $path -PathType Container) { return $fso.GetFolder($path).ShortPath }
    return Join-Path $fso.GetFolder((Split-Path -Parent $path)).ShortPath (Split-Path -Leaf $path)
}

function Test-Image([string]$path) {
    $bytes = [IO.File]::ReadAllBytes($path)
    if ($bytes.Length -lt $TextRva + $TextSize) { return $false }
    $sha = [Security.Cryptography.SHA256]::Create()
    $hash = -join ($sha.ComputeHash($bytes, $TextRva, $TextSize) | ForEach-Object { $_.ToString('x2') })
    return $hash -eq $TextSha
}

# Requested addresses
$entries = foreach ($line in Get-Content -LiteralPath $list -Encoding utf8) {
    $text = $line.Trim()
    if (-not $text -or $text.StartsWith('#')) { continue }
    ($text -split '\s+', 2)[0]
}
if ($Only) { $entries = $Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ } }
foreach ($va in $entries) {
    if ($va -notmatch '^0x[0-9A-Fa-f]+$') { throw "Not a 0x-prefixed address: $va" }
}

# Tools
if (-not $GhidraDir -or -not (Test-Path -LiteralPath (Join-Path $GhidraDir 'Ghidra\Framework\Utility\lib\Utility.jar'))) {
    throw 'Ghidra not found: pass -GhidraDir <Ghidra install folder> or set GHIDRA_INSTALL_DIR.'
}
$java = if ($JavaHome) { Join-Path $JavaHome 'bin\java.exe' } else { (Get-Command java.exe -ErrorAction SilentlyContinue).Source }
if (-not $java -or -not (Test-Path -LiteralPath $java)) {
    throw 'Java not found: pass -JavaHome <JDK folder> or set JAVA_HOME.'
}

# 1. Image
New-Item -ItemType Directory -Force -Path $dump | Out-Null
if (-not (Test-Path -LiteralPath $image)) {
    $game = @(Get-Process -Name DevilMayCry4SpecialEdition -ErrorAction SilentlyContinue)
    if ($game.Count -ne 1) {
        throw 'dump\dmc4se_mem.exe is missing. Start the game normally (not start_game.bat), reach the title screen, then run this again.'
    }
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup.bat first: .venv is missing.' }
    Write-Host "Reading the game image from PID $($game[0].Id)..."
    & $python -B (Join-Path $root 'tools\dump_memory.py') $game[0].Id
    if ($LASTEXITCODE -ne 0) { throw "dump_memory.py failed ($LASTEXITCODE)" }
}
if (-not (Test-Image $image)) {
    throw 'dump\dmc4se_mem.exe does not match build 24329835 untouched (.text hash differs). Delete it, start the game without the TAS tool and run this again.'
}

# 2 and 3. Ghidra
New-Item -ItemType Directory -Force -Path $ProjectDir, $OutDir | Out-Null
$ghidraHome = Join-Path $dump 'ghidra-home'
New-Item -ItemType Directory -Force -Path $ghidraHome | Out-Null
$shortHome = Get-ShortPath $ghidraHome
$props = foreach ($k in 'user.home', 'java.io.tmpdir', 'application.settingsdir', 'application.cachedir', 'application.tempdir') { "-D$k=$shortHome" }
$jvm = @("-Xmx$MaxHeap") + $props + @(
    '-Djava.system.class.loader=ghidra.GhidraClassLoader',
    '--enable-native-access=ALL-UNNAMED',
    '--sun-misc-unsafe-memory-access=allow',
    '-cp', (Join-Path $GhidraDir 'Ghidra\Framework\Utility\lib\Utility.jar'),
    'ghidra.Ghidra', 'ghidra.app.util.headless.AnalyzeHeadless')

$project = Get-ShortPath $ProjectDir
$scripts = Get-ShortPath (Join-Path $PSScriptRoot 'ghidra')
$out = Get-ShortPath $OutDir
$export = @('-scriptPath', $scripts, '-postScript', 'ExportCombat.java', $out) + $entries
if (Test-Path -LiteralPath (Join-Path $ProjectDir "$ProjectName.gpr")) {
    $run = @($project, $ProjectName, '-process', 'dmc4se_mem.exe', '-noanalysis', '-readOnly') + $export
    Write-Host "Exporting $($entries.Count) functions from the existing project..."
} else {
    $run = @($project, $ProjectName, '-import', (Get-ShortPath $image)) + $export
    Write-Host 'Importing and analysing the image once. This can take tens of minutes...'
}
& $java @jvm @run
if ($LASTEXITCODE -ne 0) { throw "Ghidra failed ($LASTEXITCODE)" }
$files = @(Get-ChildItem -LiteralPath $OutDir -Filter *.c)
Write-Host "Done: $($files.Count) files in $OutDir"
