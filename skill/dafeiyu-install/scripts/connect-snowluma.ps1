# 第 5 步（协议端选 SnowLuma）：等小号在桌面 QQ 里登好、SnowLuma 认出来，把这个号的连接设置也改成「只连 AstrBot」，重启 SnowLuma（不动 QQ），等它接上 AstrBot。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File connect-snowluma.ps1 -Dir C:\dafeiyu [-WaitSeconds 300]
param([Parameter(Mandatory)][string]$Dir, [int]$WaitSeconds = 300)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$sl = "$Dir\SnowLuma"; $cfg = "$sl\config"; $log = "$sl\snowluma-run.log"
if (-not (Test-Path "$sl\index.mjs")) { throw '没找到装好的 SnowLuma，先跑 install-snowluma.ps1' }
$tok = (Get-Content -Raw "$Dir\private\napcat-ws-token.txt").Trim()

function Read-SlLog { try { $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8); $t = $sr.ReadToEnd(); $sr.Close(); return $t } catch { return '' } }
function SlRunning { [bool](Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl, [StringComparison]::OrdinalIgnoreCase) }) }

# AstrBot 要开着、在听 6199（跟 NapCat 那条一样）
$port = Get-BackendPort $Dir
if (-not (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) -and (Test-Path "$Dir\start-astrbot.cmd")) {
    Say 'AstrBot 没在跑，先把它开起来'; Start-Detached 'astrbot' "$Dir\start-astrbot.cmd"; Wait-Port $port 150 | Out-Null
}
if (-not (Get-NetTCPConnection -State Listen -LocalPort 6199 -ErrorAction SilentlyContinue)) {
    $py = Find-AstrPython $Dir
    if ($py) { Say 'AstrBot 还没在听 6199，先建机器人平台「大肥鱼」'; & $py -X utf8 "$PSScriptRoot\astr_setup.py" --dir $Dir bot }
}
if (-not (SlRunning)) { Say 'SnowLuma 没在跑，先把它开起来'; Start-Detached 'snowluma' "$Dir\start-snowluma.cmd"; Wait-Port 5099 60 | Out-Null }

Say "等小号在桌面 QQ 里登录（最多 $WaitSeconds 秒）。QQ 窗口里扫码或点「快速登录」都行"
# 认小号：排除大号（private\owner-qq.txt）；以前认出过的（private\bot-qq.txt）优先
$owner = if (Test-Path "$Dir\private\owner-qq.txt") { (Get-Content -Raw "$Dir\private\owner-qq.txt").Trim() } else { '' }
$known = if (Test-Path "$Dir\private\bot-qq.txt") { (Get-Content -Raw "$Dir\private\bot-qq.txt").Trim() } else { '' }
$t0 = Get-Date; $f = $null
while (((Get-Date) - $t0).TotalSeconds -lt $WaitSeconds) {
    $c = @(Get-ChildItem $cfg -Filter 'onebot_*.json' -ErrorAction SilentlyContinue | Where-Object { $_.BaseName -match '^onebot_\d{5,}$' -and $_.BaseName -ne "onebot_$owner" })
    if ($known) { $f = $c | Where-Object BaseName -eq "onebot_$known" | Select-Object -First 1 }
    elseif ($c.Count -eq 1) { $f = $c[0] }
    elseif ($c.Count -gt 1) { Say '这台电脑的桌面 QQ 上登着不止一个号（除了大号还有别的），分不清哪个是小号：请观众把小号的 QQ 号粘进 private\bot-qq.txt 再重跑'; exit 6 }
    if ($f) { break }
    Start-Sleep 3
}
if (-not $f) {
    $t = Read-SlLog
    $last = ($t -split "`n" | Where-Object { $_ -match 'auto-load|inject|hook|login|QQ|error|ERR|WARN' } | Select-Object -Last 8) -join "`n"
    Say "等了 $WaitSeconds 秒，SnowLuma 还没认出登录的号。它日志里跟 QQ 有关的最后几行："
    Write-Output (($last -replace '\b\d{6,11}\b', '****') -replace 'password=\S+', 'password=***')
    Say '先查：QQ 窗口里是不是已经登上了；QQ 和 SnowLuma 是不是一个用管理员身份开、一个没有（要一样）；QQ 是不是刚自动升级过（升级后 SnowLuma 可能要等新版）'
    exit 3
}
$uin = $f.BaseName.Substring('onebot_'.Length)
[IO.File]::WriteAllText("$Dir\private\bot-qq.txt", $uin)
Say 'SnowLuma 认出了登录的小号（号码已存进 private\bot-qq.txt）'
Start-Sleep 5
Set-SnowLumaOneBot $f.FullName $tok
Stop-UnderDir $sl; Start-Sleep 3
Start-Detached 'snowluma' "$Dir\start-snowluma.cmd"
$ok = $false
for ($i = 0; $i -lt 45; $i++) {
    $c = Get-NetTCPConnection -RemotePort 6199 -State Established -ErrorAction SilentlyContinue | Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Path -like "$sl*" }
    if ($c) { $ok = $true; break }
    Start-Sleep 2
}
if ($ok) {
    [IO.File]::WriteAllText("$Dir\private\protocol.txt", 'snowluma')   # 掉线工具、工作台、收尾都看这个，知道现在用的是哪个协议端
    Say 'SnowLuma 已经连上 AstrBot（6199 有连接）。下一步：请观众用大号私聊小号「在吗」'
}
else { Say '90 秒没连上 AstrBot。查：AstrBot 开着没、平台「大肥鱼」建了没（astr_setup.py bot）、两边 token 一致没（都读 private\napcat-ws-token.txt）'; exit 3 }
