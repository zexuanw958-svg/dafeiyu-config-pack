# 关掉一套大肥鱼（只关不删）：停这个工作目录里的 AstrBot、NapCat、SnowLuma 和它们的启动窗口。
# 不碰 Breeze 语音、Ollama 记忆、识别模型这些：另装的一套常常直接连它们，两套共用。
# 用法：
#   关旧的（「另装一套」到登小号那步、两套抢 6199 时）：powershell -NoProfile -ExecutionPolicy Bypass -File stop-instance.ps1 -Dir <旧工作目录>
#   换回旧的：先关新的，再把旧的开起来：stop-instance.ps1 -Dir <新工作目录> -StartDir <旧工作目录>
# 两套用的是同一组端口（6199、5099），不能同时开。开机自启只认最后跑过 finish.ps1 的那套，换回旧的以后要在旧目录再跑一次 finish.ps1。
param([Parameter(Mandatory)][string]$Dir, [string]$StartDir = '')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
if (-not ((Test-Path "$Dir\private") -or (Test-Path "$Dir\start-astrbot.cmd"))) { throw "$Dir 里没有大肥鱼（private、start-astrbot.cmd 都没有），不动它" }

# 启动窗口（cmd 跑的 start-*.cmd）先停，不然它会把刚停的程序再拉起来
$cmds = foreach ($n in 'start-astrbot.cmd', 'start-napcat.cmd', 'start-snowluma.cmd', 'start-qq.cmd') { "$Dir\$n" }
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $line = "$($_.CommandLine)"; @($cmds | Where-Object { $line.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($sub in 'uv', 'astrbot', 'NapCat', 'SnowLuma') { if (Test-Path "$Dir\$sub") { Stop-UnderDir "$Dir\$sub" } }
Start-Sleep 3

$busy = foreach ($port in 6199, 5099, 6099) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $p = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).Path; if ("$p".StartsWith("$Dir\", [StringComparison]::OrdinalIgnoreCase)) { $port } }
}
if ($busy) { Say "还有没停掉的：端口 $($busy -join '、') 还被 $Dir 里的程序占着。让他看看任务栏上有没有「大肥鱼」的黑窗口，手动关掉"; exit 2 }
Say "$Dir 那套已经关了（文件都在，没删）。语音、记忆这些共用的没动"

if (-not $StartDir) { Say "想开回来：stop-instance.ps1 -Dir <现在开着的那套> -StartDir $Dir"; exit 0 }
$StartDir = [IO.Path]::GetFullPath($StartDir).TrimEnd('\')
if (-not (Test-Path "$StartDir\start-astrbot.cmd")) { throw "$StartDir 里没有 start-astrbot.cmd，开不了（启动器装的在启动器里开）" }
Start-Detached 'astrbot' "$StartDir\start-astrbot.cmd"
$proto = "$(Get-Content -Raw "$StartDir\private\protocol.txt" -ErrorAction SilentlyContinue)".Trim().ToLower()
if (-not $proto) { $proto = if (Test-Path "$StartDir\start-snowluma.cmd") { 'snowluma' } else { 'napcat' } }
if (Test-Path "$StartDir\start-$proto.cmd") { Start-Detached $proto "$StartDir\start-$proto.cmd" }
if (Wait-Port 6199 150 'Established') { Say "$StartDir 那套开起来了，小号已经连上（6199 有连接）。开机自启要换过来就在这个目录再跑一次 finish.ps1" }
else { Say "$StartDir 的 AstrBot 和 $proto 都开了，150 秒内小号没连上。桌面 QQ 或 NapCat 窗口里看看是不是要重新登录（relogin-qq.ps1 -Dir $StartDir）"; exit 3 }
