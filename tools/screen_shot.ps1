# Copies the screen area of the game window (what is really on screen, unlike shot.ps1's PrintWindow)
# to logs\<Name> at reduced size, and prints the average brightness so a black picture shows as 0.
param([double]$Scale = 0.3, [string]$Name = 'screen.png')
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace Cap -Name U -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
[StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; }
[DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
[Cap.U]::SetProcessDPIAware() | Out-Null
$game = Get-Process DevilMayCry4SpecialEdition -ErrorAction Stop | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$r = New-Object Cap.U+RECT
[Cap.U]::GetClientRect($game.MainWindowHandle, [ref]$r) | Out-Null
$p = New-Object Cap.U+POINT
[Cap.U]::ClientToScreen($game.MainWindowHandle, [ref]$p) | Out-Null
$w = $r.R; $h = $r.B
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($p.X, $p.Y, 0, 0, (New-Object System.Drawing.Size $w, $h))
$small = New-Object System.Drawing.Bitmap $bmp, ([int]($w * $Scale)), ([int]($h * $Scale))
$sum = 0; $n = 0
for ($y = 4; $y -lt $small.Height; $y += 12) { for ($x = 4; $x -lt $small.Width; $x += 12) { $c = $small.GetPixel($x, $y); $sum += $c.R + $c.G + $c.B; $n++ } }
$out = Join-Path (Split-Path -Parent $PSScriptRoot) "logs\$Name"
$small.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
"brightness={0:N1} ${w}x${h} -> $out" -f ($sum / [Math]::Max(1, $n) / 3)
