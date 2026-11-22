# Copies the screen area of a top-level window found by (part of) its title to logs\<Name>.
# For checking the tool's own windows: window_shot.ps1 -Title '输入脚本' -Name script.png
param([Parameter(Mandatory = $true)][string]$Title, [string]$Name = 'window.png', [double]$Scale = 1.0)
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace Win -Name U -MemberDefinition @'
public delegate bool EnumProc(IntPtr h, IntPtr l);
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
'@
[Win.U]::SetProcessDPIAware() | Out-Null
$found = [IntPtr]::Zero
$callback = [Win.U+EnumProc] {
    param($h, $l)
    $text = New-Object System.Text.StringBuilder 256
    [Win.U]::GetWindowTextW($h, $text, 256) | Out-Null
    if ([Win.U]::IsWindowVisible($h) -and $text.ToString().Contains($Title)) { $script:found = $h; return $false }
    return $true
}
[Win.U]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
if ($found -eq [IntPtr]::Zero) { "no visible window with '$Title' in its title"; exit 1 }
$r = New-Object Win.U+RECT
[Win.U]::GetWindowRect($found, [ref]$r) | Out-Null
$w = $r.R - $r.L; $h = $r.B - $r.T
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, (New-Object System.Drawing.Size $w, $h))
if ($Scale -ne 1.0) { $bmp = New-Object System.Drawing.Bitmap $bmp, ([int]($w * $Scale)), ([int]($h * $Scale)) }
$out = Join-Path (Split-Path -Parent $PSScriptRoot) "logs\$Name"
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
"${w}x${h} -> $out"
