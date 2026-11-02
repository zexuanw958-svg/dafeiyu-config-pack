# 搬上云时管这台电脑这边（move_to_cloud.py 调它）。数据一个字不动，只挪入口、停程序，都能退回来。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File move-local.ps1 -Action park|unpark|cloud-off -Dir <工作目录>
#   park      把启动文件夹和桌面上指向这个工作目录的入口挪进 <工作目录>\旧版\搬云前-入口（留清单），停掉在跑的程序。
#             启动器装的大脑它停不了，退出码 5，让人去启动器里点停止，再跑一次
#   unpark    入口放回原位，把她开起来（启动器装的要人去点）
#   cloud-off 退回本机时用，-Dir 是云端工作目录：停掉提醒和工作台，桌面入口收进它的「旧版」
param([Parameter(Mandatory)][ValidateSet('park', 'unpark', 'cloud-off')][string]$Action, [Parameter(Mandatory)][string]$Dir)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
$parkDir = "$Dir\旧版\搬云前-入口"
$list = "$parkDir\清单.json"
$utf8 = New-Object Text.UTF8Encoding($false)

function Read-ParkList {
    # 先赋给变量再 foreach：PowerShell 5.1 的 ConvertFrom-Json 把整个数组当成一个对象输出，直接包 @() 拿到的是「一个数组」不是一条条
    if (-not (Test-Path $list)) { return }
    $raw = Get-Content -Raw -Encoding UTF8 $list | ConvertFrom-Json
    foreach ($x in $raw) { if ($x -and $x.name) { $x } }
}
function Under([string]$p) { $p -and ([IO.Path]::GetFullPath($p.Trim('"'))).StartsWith("$Dir\", [StringComparison]::OrdinalIgnoreCase) }
function Stop-ByScript {
    # 跑在别处 Python 或 PowerShell 里的（工作台、提醒、开机登录）按命令行认，只认这个工作目录的
    $marks = "$Dir\tools\workbench\server.py", "$Dir\tools\qq_watch.py", "$Dir\tools\relogin-qq.ps1", "$Dir\tools\qq-watch.cmd", "$Dir\tools\dafeiyu_pet_bridge.py"
    Get-CimInstance Win32_Process | Where-Object {
        $l = "$($_.CommandLine)"; @($marks | Where-Object { $l.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
    } | ForEach-Object { if ($_.ProcessId -ne $PID) { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
}
function Find-Lnks {
    # 指向这个工作目录的快捷方式；Ollama 的启动项只认我们建的（说明是「大肥鱼的记忆」），他自己装的 Ollama 不碰
    $sh = New-Object -ComObject WScript.Shell
    foreach ($pair in @(@('startup', [Environment]::GetFolderPath('Startup')), @('desktop', [Environment]::GetFolderPath('Desktop')))) {
        foreach ($f in Get-ChildItem -LiteralPath $pair[1] -Filter *.lnk -ErrorAction SilentlyContinue) {
            $l = $sh.CreateShortcut($f.FullName)
            if ((Under $l.TargetPath) -or (Under $l.WorkingDirectory) -or ("$($l.Arguments)" -match [regex]::Escape("$Dir\")) -or $l.Description -eq '大肥鱼的记忆') {
                [pscustomobject]@{ where = $pair[0]; name = $f.Name; from = $f.FullName }
            }
        }
    }
}

if ($Action -eq 'cloud-off') {
    Stop-ByScript
    foreach ($i in @(Find-Lnks)) {
        New-Item -ItemType Directory -Force "$Dir\旧版\退回本机时收的入口" | Out-Null
        Move-Item -LiteralPath $i.from "$Dir\旧版\退回本机时收的入口\$($i.name)" -Force
    }
    Say '云端的掉线提醒和工作台入口收起来了'
    exit 0
}

if ($Action -eq 'park') {
    New-Item -ItemType Directory -Force "$parkDir\startup", "$parkDir\desktop" | Out-Null
    $moved = @(Read-ParkList)
    $stuck = @()
    foreach ($i in @(Find-Lnks)) {
        try {
            Move-Item -LiteralPath $i.from -Destination "$parkDir\$($i.where)\$($i.name)" -Force -ErrorAction Stop
            $moved += [pscustomobject]@{ where = $i.where; name = $i.name; from = $i.from }
        } catch { $stuck += $i.name }
    }
    [IO.File]::WriteAllText($list, (ConvertTo-Json @($moved) -Depth 3), $utf8)
    # 挪不动就先别停她：开机自启还留着的话，一重启她又跟服务器抢小号
    if ($stuck.Count) { Say ("这些入口挪不动（被占用或没权限）：{0}。她还开着，没动。处理好重跑 run" -f ($stuck -join '、')); exit 6 }
    if ($moved.Count) { Say ("开机自启和桌面入口收进了「旧版\搬云前-入口」：{0}" -f (($moved | ForEach-Object name) -join '、')) }
    Stop-ByScript
    if (Test-Path "$PSScriptRoot\stop-instance.ps1") { & "$PSScriptRoot\stop-instance.ps1" -Dir $Dir | Out-Host }
    # 语音和记忆也只为她开着（工作目录里的 Breeze、Ollama），留着白占显卡和内存
    Stop-UnderDir "$Dir\breeze-tts"
    Stop-UnderDir "$Dir\Ollama"
    # 真停了才往下：启动中、卡住的进程也可能还在写库，这时候传过去的数据不一致
    for ($i = 0; $i -lt 6; $i++) { Start-Sleep 2; $st = Get-BackendState $Dir -Fresh; if ($st.state -eq 'off') { break } }
    if ($st.state -ne 'off') {
        if (Test-Path "$Dir\start-astrbot.cmd") { Say ("大脑没停掉（现在是 {0}）。让他看看任务栏上有没有「大肥鱼」的黑窗口，手动关掉，再重跑 run" -f $st.state); exit 6 }
        Say ("大脑还没停（现在是 {0}）：它是 AstrBot 启动器装的，我停不了" -f $st.state)
        exit 5
    }
    $proto = "$(Get-Content -Raw "$Dir\private\protocol.txt" -ErrorAction SilentlyContinue)".Trim().ToLower()
    if ($proto -eq 'snowluma') { Say '桌面 QQ 没动。里面还登着小号的话请退出小号：同一个号在服务器上再登，两边会互相踢' }
    Say '这台电脑上的她停了，数据原样留着'
    exit 0
}

# unpark
$services = @()
if (-not (Test-Path $list)) { Say '没有收起来的入口，不用放回'; }
else {
    $left = @()
    foreach ($i in @(Read-ParkList)) {
        $src = "$parkDir\$($i.where)\$($i.name)"
        if (Test-Path -LiteralPath $src) {
            try { Move-Item -LiteralPath $src -Destination $i.from -Force -ErrorAction Stop } catch { $left += $i; continue }
        }
        if ($i.where -eq 'startup' -and $i.name -match 'Ollama|Breeze') { $services += $i.from }
    }
    if ($left.Count) {
        # 没放回去的留在清单里，下次接着放
        [IO.File]::WriteAllText($list, (ConvertTo-Json @($left) -Depth 3), $utf8)
        Say ("这些入口放不回去（被占用或没权限）：{0}" -f (($left | ForEach-Object name) -join '、'))
        exit 6
    }
    Remove-Item -LiteralPath $list -Force -ErrorAction SilentlyContinue
    Say '开机自启和桌面入口放回去了'
}
# 搬的时候停掉的语音、记忆也开回来：走它们原来的启动项，跟开机时一样
foreach ($s in $services) { try { Invoke-Item -LiteralPath $s -ErrorAction Stop } catch { Say "$s 没开起来，重启一次电脑就有了" } }
if (Test-Path "$Dir\tools\qq-watch.cmd") { Start-Detached 'qqwatch' "$Dir\tools\qq-watch.cmd" }
if (-not (Test-Path "$Dir\start-astrbot.cmd")) {
    Say '大脑是启动器装的：打开 AstrBot 启动器，点实例的「启动」，再把「开机自启动」开回去'
    exit 5
}
& "$PSScriptRoot\stop-instance.ps1" -Dir $Dir -StartDir $Dir | Out-Host
exit $LASTEXITCODE
