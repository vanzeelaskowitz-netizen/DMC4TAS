# Captures the game window (even when covered) to logs\game.png at reduced size.
param([double]$Scale = 0.5, [string]$Name = 'game.png')
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace Shot -Name U -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
[Shot.U]::SetProcessDPIAware() | Out-Null
$game = Get-Process DevilMayCry4SpecialEdition -ErrorAction Stop | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$r = New-Object Shot.U+RECT
[Shot.U]::GetWindowRect($game.MainWindowHandle, [ref]$r) | Out-Null
$w = $r.R - $r.L; $h = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
$ok = [Shot.U]::PrintWindow($game.MainWindowHandle, $dc, 2)
$g.ReleaseHdc($dc)
$small = New-Object System.Drawing.Bitmap $bmp, ([int]($w * $Scale)), ([int]($h * $Scale))
$out = Join-Path (Split-Path -Parent $PSScriptRoot) "logs\$Name"
$small.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
"captured=$ok ${w}x${h} -> $out"
