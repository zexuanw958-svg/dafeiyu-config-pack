# 大肥鱼掉线自救：小号在线就退出；不在线先重启 NapCat 试快速登录，不行就打开扫码页面等扫码。
# 协议端是 SnowLuma 的（private\protocol.txt 写着 snowluma，或者只有 start-snowluma.cmd）：走下面 SnowLuma 那段，只开桌面 QQ 和 SnowLuma，让他在 QQ 窗口里登。
# finish.ps1 把它拷到 <工作目录>\tools\，桌面快捷方式「大肥鱼掉线-双击扫码」指向同目录的 relogin.cmd。
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Dir = Split-Path $PSScriptRoot
function Online { [bool](Get-NetTCPConnection -RemotePort 6199 -State Established -ErrorAction SilentlyContinue) }
function Start-Brain {
    # AstrBot（她的大脑）没开的话先开，不然 QQ 登上了也连不过去
    if (-not (Get-NetTCPConnection -LocalPort 6185 -State Listen -ErrorAction SilentlyContinue) -and (Test-Path "$Dir\start-astrbot.cmd")) {
        Write-Host '她的大脑没开着，先打开，大约半分钟……'
        Start-Process "$Dir\start-astrbot.cmd" -WindowStyle Minimized
        for ($i = 0; $i -lt 90 -and -not (Get-NetTCPConnection -LocalPort 6185 -State Listen -ErrorAction SilentlyContinue); $i++) { Start-Sleep 2 }
        for ($i = 0; $i -lt 20 -and -not (Online); $i++) { Start-Sleep 1 }
    }
}
$proto = if (Test-Path "$Dir\private\protocol.txt") { "$(Get-Content -Raw "$Dir\private\protocol.txt")".Trim() } elseif ((Test-Path "$Dir\start-snowluma.cmd") -and -not (Test-Path "$Dir\start-napcat.cmd")) { 'snowluma' } else { 'napcat' }

if ($proto -eq 'snowluma') {
    $sl = "$Dir\SnowLuma"; $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    function Mine($p) { (Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me }
    function SlRunning { [bool](Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl, [StringComparison]::OrdinalIgnoreCase) }) }
    function MyQQ { @(Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -notmatch '\\NapCat' -and (Mine $_) }) }
    function Show-QQ {
        # 把 QQ 窗口调到最前面（10-29：退出账号后双击重登，登录窗口压在别的窗口底下，要从任务栏点出来）
        try {
            if (-not ('Dfy.Win' -as [type])) { Add-Type -Namespace Dfy -Name Win -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c); [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h); [DllImport("user32.dll")] public static extern void keybd_event(byte k, byte s, uint f, UIntPtr e);' }
            $w = Get-Process -Id @((MyQQ).ProcessId) -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
            if (-not $w) { return }
            if ([Dfy.Win]::IsIconic($w.MainWindowHandle)) { [Dfy.Win]::ShowWindow($w.MainWindowHandle, 9) | Out-Null }
            # Windows 不让后台程序抢前台，先按一下 Alt 再切（不按的话只会在任务栏闪一下）
            [Dfy.Win]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [Dfy.Win]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
            [Dfy.Win]::SetForegroundWindow($w.MainWindowHandle) | Out-Null
        } catch {}
    }
    function Restart-SnowLuma {
        # 只结束 SnowLuma 文件夹里的程序，QQ 不动
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl, [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep 2
        Start-Process "$Dir\start-snowluma.cmd" -WindowStyle Minimized
    }
    Write-Host ''
    Write-Host '==== 大肥鱼重新登录（SnowLuma）====' -ForegroundColor Cyan
    if (Online) { Write-Host '她现在是在线的，不用登。还是不回话的话，打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Green; exit 0 }
    Start-Brain
    if (Online) { Write-Host '好了，她回来了，可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }
    if (-not (SlRunning)) { Write-Host '帮她登 QQ 的 SnowLuma 没开着，先打开……'; Start-Process "$Dir\start-snowluma.cmd" -WindowStyle Minimized; Start-Sleep 8 }
    if (-not (MyQQ).Count) {
        $qqExe = $null
        if (Test-Path "$Dir\start-qq.cmd") { $qqExe = ([regex]::Match((Get-Content -Raw "$Dir\start-qq.cmd"), 'start "" "([^"]+)"')).Groups[1].Value }
        if (-not $qqExe -or -not (Test-Path $qqExe)) { $qqExe = @("$env:ProgramFiles\Tencent\QQNT\QQ.exe", "${env:ProgramFiles(x86)}\Tencent\QQNT\QQ.exe") + @(Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^(腾讯)?QQ$' -and $_.UninstallString } | ForEach-Object { Join-Path (Split-Path ($_.UninstallString -replace '"', '')) 'QQ.exe' }) | Where-Object { Test-Path $_ } | Select-Object -First 1 }
        if (-not $qqExe) { Write-Host '没找到桌面 QQ，打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Red; exit 1 }
        Write-Host '桌面 QQ 没开着，帮你打开了。'
        Start-Process $qqExe
    }
    for ($i = 0; $i -lt 30 -and -not (Online); $i++) { Start-Sleep 1 }
    if (Online) { Write-Host '已经自动登上了，可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }
    Write-Host '请在桌面 QQ 的窗口里登小号：有小号头像就点「登录」；是二维码就用登着小号的手机 QQ 扫一扫，再在手机上点「登录」。' -ForegroundColor Yellow
    Show-QQ
    Write-Host '（最多等 10 分钟）'
    $deadline = (Get-Date).AddMinutes(10); $kicked = $false; $t0 = Get-Date
    while ((Get-Date) -lt $deadline) {
        Start-Sleep 3
        if (Online) { Write-Host ''; Write-Host '登录成功，她回来了，可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }
        # QQ 已经登着、SnowLuma 却没连上：重启一次 SnowLuma（它会重新挂到开着的 QQ 上，不动 QQ）
        if (-not $kicked -and ((Get-Date) - $t0).TotalSeconds -gt 60) { $kicked = $true; Write-Host '重启一次 SnowLuma 再试……'; Restart-SnowLuma }
    }
    $log = Get-ChildItem "$sl\logs" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($log -and (Select-String -Path $log.FullName -Pattern 'retry exhausted|unsupported|not supported' -Quiet)) {
        Write-Host '10 分钟还没连上。看日志多半是 QQ 自动升级了、SnowLuma 还没跟上新版：打开「大肥鱼工作台」看提醒，或者在工作台点「复制求助信息」，把那句话发我。' -ForegroundColor Red
    } else { Write-Host '10 分钟还没连上。关掉这个窗口重新双击一次；还不行就打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Red }
    exit 1
}

$shell = Get-ChildItem "$Dir\NapCat" -Directory -Filter 'NapCat.*.Shell' | Where-Object { Test-Path "$($_.FullName)\NapCatWinBootMain.exe" } | Select-Object -First 1
if (-not $shell) { Write-Host '没找到 NapCat，打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Red; exit 1 }
$qr = Get-ChildItem "$($shell.FullName)\versions" -Directory | ForEach-Object { "$($_.FullName)\resources\app\napcat\cache\qrcode.png" } | Select-Object -First 1
$html = "$Dir\扫码登录.html"

function QrTime { if ($qr -and (Test-Path $qr)) { (Get-Item $qr).LastWriteTime } else { [datetime]::MinValue } }
function Restart-NapCat {
    # 只结束 NapCat 文件夹里的程序，你平时用的桌面 QQ 不动
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($shell.FullName, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 3
    Start-Process "$Dir\start-napcat.cmd" -WindowStyle Minimized
}

Write-Host ''
Write-Host '==== 大肥鱼重新登录 ====' -ForegroundColor Cyan
if (Online) { Write-Host '她现在是在线的，不用扫码。还是不回话的话，打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Green; exit 0 }

Start-Brain
if (Online) { Write-Host '好了，她回来了，可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }

$start = Get-Date
Write-Host '正在重启 QQ，先试不扫码直接登，大约 20 秒……'
Restart-NapCat
for ($i = 0; $i -lt 40 -and -not (Online); $i++) {
    Start-Sleep 1
    if ((QrTime) -gt $start -and $i -gt 8) { break }
}
if (Online) { Write-Host '不用扫码，已经自动登上了。可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }
if ((QrTime) -lt $start) { Write-Host '二维码没出来。关掉这个窗口再双击一次；还不行就打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Red; exit 1 }

if (Test-Path $html) { Start-Process $html } else { Start-Process $qr }
Write-Host '二维码已经打开了：用登着小号的手机 QQ 扫一扫，再在手机上点「登录」。' -ForegroundColor Yellow
Write-Host '（最多等 10 分钟，二维码过期会自动换新的）'

$deadline = (Get-Date).AddMinutes(10); $restarts = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    if (Online) { Write-Host ''; Write-Host '登录成功，她回来了，可以关掉这个窗口和二维码页面了。' -ForegroundColor Green; exit 0 }
    if (((Get-Date) - (QrTime)).TotalSeconds -gt 180 -and $restarts -lt 2) {
        Write-Host '二维码卡住了，自动重启一次 QQ……'
        $restarts++; Restart-NapCat; Start-Sleep 10
    }
}
Write-Host '10 分钟还没登上。关掉这个窗口重新双击一次；还不行就打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Red
exit 1
