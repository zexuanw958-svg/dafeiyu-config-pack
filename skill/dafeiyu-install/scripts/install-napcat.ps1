# 第 4 步（协议端选 NapCat）：命令行下载、安装 NapCat 一键包，启动，出二维码页面。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-napcat.ps1 -Dir C:\dafeiyu
# 用 curl 下载、命令行运行的安装器不弹 SmartScreen（10-27 实测）。可以重复跑：装好的跳过，残留会自己清。
param([Parameter(Mandatory)][string]$Dir, [string]$Version = 'v4.18.41')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$t0 = Get-Date
$nc = "$Dir\NapCat"; $dl = "$Dir\downloads"
New-Item -ItemType Directory -Force $nc, $dl | Out-Null

function Find-Shell { Get-ChildItem $nc -Directory -Filter 'NapCat.*.Shell' -ErrorAction SilentlyContinue | Where-Object { Test-Path "$($_.FullName)\NapCatWinBootMain.exe" } | Select-Object -First 1 }
function Find-NapDir([string]$sub) {
    $s = Find-Shell; if (-not $s) { return $null }
    Get-ChildItem "$($s.FullName)\versions" -Directory -ErrorAction SilentlyContinue | ForEach-Object { "$($_.FullName)\resources\app\napcat\$sub" } | Where-Object { Test-Path $_ } | Select-Object -First 1
}

$shell = Find-Shell
if (-not $shell -or -not (Find-NapDir 'config')) {
    # 上次没装成留下的 Shell 文件夹会让安装器每次卡在「移动 versions 目录」，先清掉（下好的 QQ.exe、zip 留着）
    Get-ChildItem $nc -Directory -Filter 'NapCat.*.Shell' -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-UnderDir $_.FullName; Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue; Say "清掉上次没装成的 $($_.Name)"
    }
    if (-not (Test-Path "$nc\NapCatInstaller.exe")) {
        Say "下载 NapCat 一键包 $Version（约 1 MB，装的时候它自己再下 QQ 和 NapCat 本体）"
        $zip = "$dl\NapCat.Shell.Windows.OneKey.zip"
        Get-GitHubFile "https://github.com/NapNeko/NapCatQQ/releases/download/$Version/NapCat.Shell.Windows.OneKey.zip" $zip 500KB | Out-Null
        Expand-Archive -Force $zip $nc
    }
    Say '运行 NapCat 安装器：下 QQ（约 200 MB）和 NapCat，三分钟左右，别关窗口'
    $enter = "$dl\enter.txt"; "`r`n`r`n`r`n`r`n" | Set-Content -Encoding ascii $enter
    $p = Start-Process -FilePath "$nc\NapCatInstaller.exe" -WorkingDirectory $nc -RedirectStandardInput $enter `
        -RedirectStandardOutput "$nc\installer.out" -RedirectStandardError "$nc\installer.err" -PassThru -WindowStyle Hidden
    # 安装器窗口是隐藏的：每 15 秒报一次 NapCat 文件夹长到多大、安装器最后说了什么，别让他对着不动的屏幕干等
    $size0 = 0L; $waited = 0
    Set-KeepAwake
    while (-not $p.WaitForExit(15000)) {
        $waited += 15
        $size = (Get-ChildItem $nc -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        $last = (Get-Content "$nc\installer.out" -Tail 3 -Encoding UTF8 -ErrorAction SilentlyContinue | Where-Object { $_.Trim() } | Select-Object -Last 1)
        Say ('NapCat 安装中：已经 {0:N0} MB{1}{2}' -f ($size / 1MB), $(if ($size -le $size0) { '（这会儿没变大）' } else { '' }), $(if ($last) { "，安装器：$("$last".Trim() -replace '\s+', ' ' | ForEach-Object { $_.Substring(0, [math]::Min(60, $_.Length)) })" } else { '' }))
        $size0 = $size
        if ($waited -ge 1200) { Stop-Process -Id $p.Id -Force; Say '安装器 20 分钟没装完，停了'; break }
    }
    Set-KeepAwake -Off
    $shell = Find-Shell
    if (-not $shell -or -not (Find-NapDir 'config')) {
        $tail = @(Get-Content "$nc\installer.out" -Tail 15 -ErrorAction SilentlyContinue | Where-Object { $_.Trim() })
        $tail
        $av = Get-ThirdPartyAV
        if (($tail -join ' ') -match '拒绝访问|解压失败|EPERM|EBUSY|Access is denied|找不到文件|cannot find') {
            $who = if ($av.Count) { $av -join '、' } else { 'Windows 自带的杀毒（Defender）' }
            Say "NapCat 没装成，多半是 $who 把登 QQ 的程序当成可疑文件拿走或锁住了。不用关杀毒：先把 $Dir 加进它的信任区（信任区 / 白名单 / 排除项），步骤见 references/杀毒软件.md；只有 Defender 的跑 trust-folder.ps1，点一下「是」就加好。加好后重跑本脚本，被拿走的文件会重新下，不用去隔离区恢复"
            exit 8
        }
        throw "NapCat 没装成：看上面几行（下载失败就过一会儿重跑；$(if ($av.Count) { "这台装了 $($av -join '、')，" })上面要是有「拒绝访问」「解压失败」，照 references/杀毒软件.md 加信任区）"
    }
    Say ("NapCat 装好了：{0}（用时 {1:N0} 秒）" -f $shell.Name, ((Get-Date) - $t0).TotalSeconds)
} else {
    Say "NapCat 已经装好：$($shell.Name)"
}

# 启动脚本：有小号就快速登录（不用扫码），没有就出二维码
$uinFile = "$Dir\private\bot-qq.txt"
$uin = if (Test-Path $uinFile) { (Get-Content -Raw $uinFile).Trim() } else { '' }
$cmd = "@echo off`r`nchcp 65001 >nul`r`ntitle Dafeiyu-NapCat (do not close)`r`ncd /d `"$($shell.FullName)`"`r`n.\NapCatWinBootMain.exe $uin > `"$($shell.FullName)\napcat-run.log`" 2>&1`r`n"
[IO.File]::WriteAllText("$Dir\start-napcat.cmd", $cmd, (New-Object Text.UTF8Encoding($false)))

$cache = Find-NapDir 'cache'
if (-not $cache) { $cache = (Get-ChildItem "$($shell.FullName)\versions" -Directory | Select-Object -First 1).FullName + '\resources\app\napcat\cache'; New-Item -ItemType Directory -Force $cache | Out-Null }
$qr = "$cache\qrcode.png"
Stop-UnderDir $shell.FullName; Start-Sleep 2
Remove-Item $qr -Force -ErrorAction SilentlyContinue
Start-Detached 'napcat' "$Dir\start-napcat.cmd"
for ($i = 0; $i -lt 45 -and -not (Test-Path $qr); $i++) { Start-Sleep 2 }
if (-not (Test-Path $qr) -and -not (Test-Path "$($shell.FullName)\NapCatWinBootMain.exe")) {
    Say "NapCat 的启动程序刚才还在，现在不见了：多半是杀毒软件$(if (($av = Get-ThirdPartyAV).Count) { "（$($av -join '、')）" })把它拿走了。先把 $Dir 加进信任区（references/杀毒软件.md），再重跑本脚本"
    exit 8
}
if (-not (Test-Path $qr)) {
    $cfgDir = Find-NapDir 'config'
    if ($uin -and (Test-Path "$cfgDir\onebot11_$uin.json")) { Say '小号快速登录中，没出二维码（正常）。下一步跑 connect-napcat.ps1'; exit 0 }
    Get-Content "$($shell.FullName)\napcat-run.log" -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { $_ -replace 'token=\S+', 'token=***' } | Where-Object { $_ -notmatch "[\u2580-\u259F]" }
    throw '90 秒没出二维码，看上面的日志'
}

# 二维码页面：每 3 秒自动刷新，过期了 NapCat 会自己换新码
$html = @"
<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>大肥鱼 · 小号扫码登录</title>
<style>body{margin:0;background:#f4f1ea;font:18px "Microsoft YaHei",sans-serif;color:#222;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh}
img{width:320px;height:320px;image-rendering:pixelated;background:#fff;padding:16px;border-radius:12px}p{margin:12px 0 0}</style></head>
<body><img id="q" src="file:///$($qr -replace '\\','/')"><p>用登着<b>小号</b>的手机 QQ 扫，扫完在手机上点「登录」</p><p id="t" style="color:#888;font-size:14px"></p>
<script>setInterval(function(){document.getElementById('q').src='file:///$($qr -replace '\\','/')?'+Date.now();document.getElementById('t').textContent='每 3 秒自动刷新 · '+new Date().toLocaleTimeString()},3000)</script></body></html>
"@
[IO.File]::WriteAllText("$Dir\扫码登录.html", $html, (New-Object Text.UTF8Encoding($false)))
try {
    $fs = [IO.File]::Open("$($shell.FullName)\napcat-run.log", 'Open', 'Read', 'ReadWrite'); $rl = (New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)).ReadToEnd(); $fs.Close()
    $nv = [regex]::Match($rl, 'NapCat\.Core Version:\s*v?(\d+\.\d+\.\d+)').Groups[1].Value
    if ($nv) { Say "装上的 NapCat 是 $nv$(if ($nv -ne $Version.TrimStart('v')) { "（Skill 测过的是 $($Version.TrimStart('v'))，新版本一般也能用，碰到怪问题先记下这个版本号）" })" }
} catch {}
Start-Process "$Dir\扫码登录.html"
Say "二维码出来了，已经用浏览器打开：$Dir\扫码登录.html（二维码图片：$qr）"
Say '请观众用小号的手机 QQ 扫码并在手机上点登录；扫完跑 connect-napcat.ps1，它会等登录完成再接 AstrBot'
