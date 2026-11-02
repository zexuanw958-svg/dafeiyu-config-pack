# 大肥鱼掉线自救：小号在线就退出；不在线先重启 NapCat 试快速登录，不行就打开扫码页面等扫码。
# 协议端是 SnowLuma 的（private\protocol.txt 写着 snowluma，或者只有 start-snowluma.cmd）：走下面 SnowLuma 那段，只开桌面 QQ 和 SnowLuma，让他在 QQ 窗口里登。
# finish.ps1 把它拷到 <工作目录>\tools\，桌面快捷方式「大肥鱼掉线-双击扫码」指向同目录的 relogin.cmd。
param([string]$Dir = '', [switch]$Autostart)
$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\common.ps1"
if (-not $Dir) { $Dir = Split-Path $PSScriptRoot }
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
function Online { Test-QQOnline $Dir }
function Boot-State([string]$State, [string]$Text) {
    if (-not $Autostart) { return }
    try {
        New-Item -ItemType Directory -Force "$Dir\logs" | Out-Null
        [IO.File]::WriteAllText((Join-Path $Dir 'logs/qq-autostart.json'), (@{state=$State;text=$Text;at=(Get-Date).ToString('o');pid=$PID} | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    } catch {}
}
function Start-Quiet([string]$File) {
    Start-Process "$env:SystemRoot\System32\conhost.exe" -ArgumentList '--headless', 'cmd.exe', '/c', "`"$File`"" -WindowStyle Hidden
}
function Start-Brain {
    $bs = Get-BackendState $Dir -Fresh
    if ($bs.state -eq 'up') { return }
    if (Test-Path "$Dir\start-astrbot.cmd") {
        Write-Host '她的大脑没开着，先打开，大约半分钟……'
        Start-Quiet "$Dir\start-astrbot.cmd"
        for ($i = 0; $i -lt 90; $i++) {
            if ((Get-BackendState $Dir -Fresh).state -eq 'up') { break }
            Start-Sleep 2
        }
    } else { Write-Host '启动器开着不等于实例开了：在 AstrBot 启动器里点这个实例的「启动」。这边会等着。' }
}
function Wait-Ready {
    # 开机不抢跑。实例可能要人手动点，安静一直等，不超时报错。
    while ($true) {
        if (Online) { return }
        $bs = Get-BackendState $Dir -Fresh
        if ($bs.state -ne 'up' -or -not (Get-NetTCPConnection -LocalPort $bs.ws_port -State Listen -ErrorAction SilentlyContinue)) {
            Boot-State 'waiting-brain' '在等大脑。启动器装的，请在启动器里点实例的「启动」。'
        } elseif (-not (Test-QQNetwork)) {
            Boot-State 'waiting-network' '在等网络，准备好就自己登。'
        } else { return }
        Start-Sleep 10
    }
}
# 开机、双击重登共用同一条路。同一目录同时只能有一个，避免两份抢号。
$sha = [Security.Cryptography.SHA256]::Create()
try { $tag = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Dir.ToLowerInvariant()))) -replace '-', '').Substring(0,24) } finally { $sha.Dispose() }
$mutex = New-Object Threading.Mutex($false, "Local\DafeiyuQQ-$tag")
try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
if (-not $locked) { Write-Host '已经在后台等大脑或重新登录了，打开工作台能看进度。'; $mutex.Dispose(); exit 0 }
# 脚本退出/异常都会释放命名锁；不留下锁文件。
try {
if ($Autostart) { Wait-Ready; if (Online) { Boot-State 'online' '小号已经连上大脑。'; exit 0 } }
$proto = if (Test-Path "$Dir\private\protocol.txt") { "$(Get-Content -Raw "$Dir\private\protocol.txt")".Trim() } elseif ((Test-Path "$Dir\start-snowluma.cmd") -and -not (Test-Path "$Dir\start-napcat.cmd")) { 'snowluma' } else { 'napcat' }

if ($Autostart -and $proto -eq 'snowluma') {
    # SnowLuma 的反向 WS 会自行重连，不重启桌面 QQ，也不把大号打开。
    $me = @(Get-UserStartupIdentity)[0]
    $running = Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -like "$Dir\SnowLuma\*" } | Where-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me }
    if (-not $running) { Start-Quiet "$Dir\start-snowluma.cmd" }
    Boot-State 'desktop-qq' 'SnowLuma 已打开。桌面 QQ 需在它自己的设置里开自动启动、自动登录小号。'
    exit 0
}

if ($proto -eq 'snowluma') {
    $sl = "$Dir\SnowLuma"; $me = @(Get-UserStartupIdentity)[0]
    function Mine($p) { (Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me }
    function SlRunning { [bool](Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl + '\', [StringComparison]::OrdinalIgnoreCase) -and (Mine $_) }) }
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
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl + '\', [StringComparison]::OrdinalIgnoreCase) -and (Mine $_) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
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
    # 路径和 Windows 用户都得对上，不动桌面 QQ 和别人的进程。
    $me = @(Get-UserStartupIdentity)[0]
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($shell.FullName + '\', [StringComparison]::OrdinalIgnoreCase) } |
        Where-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 3
    Start-Quiet "$Dir\start-napcat.cmd"
}

Write-Host ''
Write-Host '==== 大肥鱼重新登录 ====' -ForegroundColor Cyan
if (Online) { Write-Host '她现在是在线的，不用扫码。还是不回话的话，打开桌面的「大肥鱼工作台」点「复制求助信息」，把那句话发我。' -ForegroundColor Green; exit 0 }

Start-Brain
if (Online) { Write-Host '好了，她回来了，可以关掉这个窗口了。' -ForegroundColor Green; exit 0 }

# 开机和双击重登都先试快速登录。每次都看本轮新二维码，不拿上次的旧图吓人。
$readyForScan = $false
while (-not $readyForScan) {
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if ($Autostart) { Wait-Ready }
        if (Online) { Boot-State 'online' '小号已经连上大脑。'; Write-Host '已经自动登上了。'; exit 0 }
        $start = Get-Date
        Boot-State 'logging-in' "正在自动登录，第 $attempt 次。"
        Write-Host "正在试快速登录（$attempt/3）……"
        Restart-NapCat
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep 2
            if (Online) { Boot-State 'online' '小号已经连上大脑。'; Write-Host '不用扫码，已经自动登上了。'; exit 0 }
            if ((QrTime) -gt $start -and $i -gt 8) { break }
        }
        # 他自己双击来的，人就在电脑前：出了新码就给他扫；开机那条才连试三次
        if (($attempt -eq 3 -or -not $Autostart) -and (QrTime) -gt $start) { $readyForScan = $true; break }
        # 无网络/服务未就绪时不能因为旧二维码而弹浏览器；重试有间隔。
        Start-Sleep (10 * $attempt)
    }
    if (-not $readyForScan) {
        Boot-State 'retry-later' '自动登录还没成功，稍后继续试。没有新二维码，不用扫码。'
        if (-not $Autostart) { Write-Host '暂时没连上，也没有新二维码。打开工作台点「复制求助信息」。'; exit 1 }
        Start-Sleep 300
    }
}
# 连续三次没自动登上且本轮确实出了二维码，才打开一次扫码页。
if (Test-Path $html) { Start-Process $html } else { Start-Process $qr }
Boot-State 'scan' '自动登录没成功，请用小号手机 QQ 扫打开的二维码。'
Write-Host '请用登着小号的手机 QQ 扫码，再在手机上点「登录」。最多等 10 分钟。'
$deadline = (Get-Date).AddMinutes(10); $restarts = 0
while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    if (Online) { Boot-State 'online' '小号已经连上大脑。'; Write-Host '登录成功，可以关掉二维码页面了。'; exit 0 }
    if (((Get-Date) - (QrTime)).TotalSeconds -gt 180 -and $restarts -lt 2) {
        if ($Autostart) { Wait-Ready }
        $restarts++; Restart-NapCat; Start-Sleep 10
    }
}
Boot-State 'scan-timeout' '二维码等了 10 分钟。方便时双击桌面的「大肥鱼掉线-双击扫码」。'
Write-Host '10 分钟还没登上。方便时双击桌面「大肥鱼掉线-双击扫码」，或用工作台复制求助信息。'
exit 1
} finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
