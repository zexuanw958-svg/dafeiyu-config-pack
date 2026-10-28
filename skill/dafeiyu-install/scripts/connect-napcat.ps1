# 第 5 步：等小号登录完成，把 NapCat 接到 AstrBot（反向 WS 127.0.0.1:6199），NapCat 后台只听本机，然后用快速登录重启 NapCat。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File connect-napcat.ps1 -Dir C:\dafeiyu [-WaitSeconds 300]
# 注意：扫码后要等 onebot11_<小号>.json 出现、再多等十秒才能重启 NapCat，早了会把刚完成的登录掐掉（10-27 实测踩过）。
param([Parameter(Mandatory)][string]$Dir, [int]$WaitSeconds = 300)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$shell = Get-ChildItem "$Dir\NapCat" -Directory -Filter 'NapCat.*.Shell' | Where-Object { Test-Path "$($_.FullName)\NapCatWinBootMain.exe" } | Select-Object -First 1
if (-not $shell) { throw '没找到装好的 NapCat，先跑 install-napcat.ps1' }
$cfgDir = Get-ChildItem "$($shell.FullName)\versions" -Directory | ForEach-Object { "$($_.FullName)\resources\app\napcat\config" } | Where-Object { Test-Path $_ } | Select-Object -First 1
$log = "$($shell.FullName)\napcat-run.log"

function Read-NapLog {
    try { $fs = [IO.File]::Open($log, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8); $t = $sr.ReadToEnd(); $sr.Close(); return $t } catch { return '' }
}

# AstrBot 那边要开着、并且有「大肥鱼」平台在听 6199，不然 NapCat 登上了也连不过去（10-27 测试里 DSH 漏跑过 bot）
$py = Find-AstrPython $Dir
$port = Get-BackendPort $Dir
if (-not (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
    if (Test-Path "$Dir\start-astrbot.cmd") {
        Say 'AstrBot 没在跑（电脑重启过？），先把它开起来'
        Start-Detached 'astrbot' "$Dir\start-astrbot.cmd"
        if (-not (Wait-Port $port 150)) { Say 'AstrBot 150 秒没起来，看 astrbot\run.log 最后几行' }
    } else { Say "AstrBot 后台（端口 $port）没在听：启动器装的，请观众在启动器里点「启动」" }
}
if (-not (Get-NetTCPConnection -State Listen -LocalPort 6199 -ErrorAction SilentlyContinue)) {
    if ($py) { Say 'AstrBot 还没在听 6199，先建机器人平台「大肥鱼」'; & $py -X utf8 "$PSScriptRoot\astr_setup.py" --dir $Dir bot }
    else { Say '注意：AstrBot 没在听 6199，NapCat 登上了也连不过去。先在 AstrBot 里建好平台「大肥鱼」（astr_setup.py bot）' }
}

Say "等小号登录完成（最多 $WaitSeconds 秒）。二维码两分钟过期一次，NapCat 会自己换新码，扫码页面会跟着刷新"
# 从最新一张二维码那里开始看：观众常常先扫完、手机上点了登录，才回「扫好了」，这之间的报错也要接住
$all = Read-NapLog
$seen = $all.LastIndexOf('请扫描下面的二维码')
if ($seen -lt 0) { $seen = $all.Length }
$t0 = Get-Date; $f = $null; $scanned = $false
while (((Get-Date) - $t0).TotalSeconds -lt $WaitSeconds) {
    $f = Get-ChildItem $cfgDir -Filter 'onebot11_*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { break }
    $all = Read-NapLog
    if ($all.Length -lt $seen) { $seen = 0 }   # NapCat 重启过，日志重写了
    $new = $all.Substring($seen); $seen = $all.Length
    if (-not $scanned -and $new -match '已被扫描|已扫码|scanned') { $scanned = $true; Say '手机已经扫了，等他在手机上点「登录」' }
    # QQ 服务器拒绝（风控）：日志里是 ErrInfo 带 serverErrorCode 和一句提示，没有「ErrCode:」，NapCat 之后也不再出新码
    $deny = [regex]::Match($new, '"serverErrorCode":\s*(\d+)[^}]*?"message":"([^"]*)"')
    if ($deny.Success) {
        Say "登录被 QQ 拒了（serverErrorCode $($deny.Groups[1].Value)），QQ 的原话：$($deny.Groups[2].Value)"
        Say '  这不是装错了。请他在登着小号的手机 QQ 上按提示恢复账号，恢复好了再重跑 install-napcat.ps1 出新码扫；恢复之前别让他反复扫。实在恢复不了，换一个号'
        exit 2
    }
    foreach ($m in [regex]::Matches($new, 'ErrCode:\s*(\d+)')) {
        $code = $m.Groups[1].Value
        if ($code -eq '3') { Say '二维码过期了，NapCat 已经换了新码（页面自动刷新），接着等'; $scanned = $false; continue }
        Say "登录失败（ErrCode $code）。先问观众：手机 QQ 上弹了什么提示，原话发过来，别让他反复扫。"
        switch ($code) {
            '5'  { Say '  ErrCode 5：二维码被取消或失效，重跑 install-napcat.ps1 出新码' }
            '10' { Say '  ErrCode 10：扫码、手机上确认后被拒，原因还没查清（10-27 测试机碰到过）。先看手机提示；小号新注册或刚被风控的，先在手机 QQ 里按提示恢复' }
        }
        exit 2
    }
    Start-Sleep 3
}
if (-not $f) { throw "等了 $WaitSeconds 秒还没登录成功。二维码过期就重跑 install-napcat.ps1" }
$uin = $f.BaseName.Substring('onebot11_'.Length)
# 扫成了大号（大多数人手机上只登着大号，一扫就把大号登成了机器人）：停下来，清掉这次登录，让他换小号再扫
$owner = if (Test-Path "$Dir\private\owner-qq.txt") { (Get-Content -Raw "$Dir\private\owner-qq.txt").Trim() } else { '' }
if ($owner -and $uin -eq $owner) {
    Stop-UnderDir $shell.FullName; Start-Sleep 3
    Get-ChildItem $cfgDir -File | Where-Object { $_.Name -in "onebot11_$uin.json", "napcat_$uin.json" } | Remove-Item -Force -ErrorAction SilentlyContinue
    Say '刚才是用大号扫的码（登上的是他自己的号，不是小号），已经退掉、清掉这次登录。'
    Say '  请他在手机 QQ 上切到小号：点左上角头像 → 设置 → 账号管理 → 切换或添加小号，切好了回「好了」，再重跑 install-napcat.ps1 出新码扫。'
    exit 7
}
[IO.File]::WriteAllText("$Dir\private\bot-qq.txt", $uin)
# 快速登录失效（日志「你的用户身份已失效」，10-28 测试机碰到）、补扫了一次码：这时跑着的已经是快速登录那套，配置早就改好了，再重启又会掉，直接等它连上 AstrBot
$quick = Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -like "$($shell.FullName)\*" -and $_.CommandLine -match "-q\s+$uin\b" }
if ($quick -and (Test-Path "$Dir\start-napcat.cmd") -and (Get-Content -Raw "$Dir\start-napcat.cmd") -match "NapCatWinBootMain\.exe $uin\b") {
    Say '配置上次已经改好了，NapCat 不再重启，等它连上 AstrBot（他还没扫就先扫，最多等 3 分钟）'
    if (Wait-Port 6199 180 'Established') {
        [IO.File]::WriteAllText("$Dir\private\protocol.txt", 'napcat')
        Say 'NapCat 已经连上 AstrBot（6199 有连接）。下一步：请观众用大号私聊小号「在吗」'
        exit 0
    }
    Say '3 分钟没连上。看 napcat-run.log 最后几行：又出了二维码就让他再扫，扫完重跑本脚本'
    exit 3
}
Say "小号登录成功（号码已存进 private\bot-qq.txt）。再等 10 秒让登录信息写完"
Start-Sleep 12

# 改 NapCat 配置：WS 客户端连 AstrBot；网页后台只听本机、换随机 token、自动登录这个号
if (-not $py) { throw '找不到能跑 napcat_cfg.py 的 Python（工作目录里和启动器实例里都没有）' }
& $py -X utf8 "$PSScriptRoot\napcat_cfg.py" --dir $Dir --config-dir $cfgDir --uin $uin

# 用快速登录重启
$cmd = "@echo off`r`nchcp 65001 >nul`r`ntitle Dafeiyu-NapCat (do not close)`r`ncd /d `"$($shell.FullName)`"`r`n.\NapCatWinBootMain.exe $uin > `"$log`" 2>&1`r`n"
[IO.File]::WriteAllText("$Dir\start-napcat.cmd", $cmd, (New-Object Text.UTF8Encoding($false)))
Stop-UnderDir $shell.FullName; Start-Sleep 3
Start-Detached 'napcat' "$Dir\start-napcat.cmd"
if (Wait-Port 6199 90 'Established') {
    [IO.File]::WriteAllText("$Dir\private\protocol.txt", 'napcat')   # 掉线工具、工作台、收尾都看这个，知道现在用的是哪个协议端
    Say 'NapCat 已经连上 AstrBot（6199 有连接）。下一步：请观众用大号私聊小号「在吗」'
} else {
    $t = Read-NapLog
    if ($t -match '二维码|qrcode|QRCode') { Say '快速登录没成功（日志里多半是「你的用户身份已失效」），NapCat 已经自己出了新码，扫码页面会跟着换：让他再扫一次，扫完重跑本脚本（这次不会再重启 NapCat）' }
    else { Say '90 秒没连上 AstrBot。查：AstrBot 开着没、平台「大肥鱼」建了没（astr_setup.py bot）、两边 token 一致没' }
    exit 3
}
