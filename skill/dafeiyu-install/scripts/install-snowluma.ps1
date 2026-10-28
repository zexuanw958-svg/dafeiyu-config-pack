# 第 4 步（协议端选 SnowLuma）：下载官方完整包原样解压，配好「连 AstrBot」，在桌面里跟 QQ 同一个用户、同一个权限级别启动，再打开桌面 QQ 等扫码。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File install-snowluma.ps1 -Dir C:\dafeiyu [-AcceptAgreements] [-StopNapCat]
#   -AcceptAgreements：观众读过 SnowLuma 的用户协议和隐私政策、亲口说了「同意」才加（见 references/协议端-SnowLuma.md）。不加也能用，只是它的网页后台要他自己点同意才解锁。
#   -StopNapCat：这台电脑上本 Skill 装的 NapCat 正登着同一个小号时，先把它停掉（同一个号不能同时接两个）。
# 可以重复跑：装好的跳过，只更新配置、重启 SnowLuma；不会重启 QQ。
param([Parameter(Mandatory)][string]$Dir, [string]$Version = 'v2.2.1', [switch]$AcceptAgreements, [switch]$StopNapCat)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$sl = "$Dir\SnowLuma"; $dl = "$Dir\downloads"; $priv = "$Dir\private"
New-Item -ItemType Directory -Force $dl, $priv | Out-Null

# 1. 权限级别：SnowLuma 要往 QQ 里注入，两边必须同一个用户、同一个级别。沙盒（低完整性）里起它就是 issue #563「一直注入失败」的原因。
$il = (whoami /groups) -join ' '
if ($il -match 'S-1-16-4096') { throw '现在是在 AI 帮手的沙盒里（低权限），起不了 SnowLuma：请观众把对话框下方的权限切成「完全访问」再跑' }

# 2. 桌面 QQ（官方 NT 版）装在哪（Find-DesktopQQ 在 common.ps1）
$qqExe = Find-DesktopQQ
if (-not $qqExe) { Say '这台电脑没装桌面 QQ：先跑 install-qq.ps1（从 QQ 官网下官方安装包装上），再重跑本脚本'; exit 4 }
$qqDir = Split-Path $qqExe
$qqVer = (Get-ChildItem "$qqDir\versions" -Directory -ErrorAction SilentlyContinue | Where-Object Name -match '^\d+\.\d+\.\d+-\d+$' | Sort-Object { [version]($_.Name -replace '-', '.') } -Descending | Select-Object -First 1).Name
Say "桌面 QQ：$qqExe（版本 $(if ($qqVer) { $qqVer } else { '没读到' })）"

# 3. 别的程序占着这个号或这台电脑的 QQ：本 Skill 装的 NapCat
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$napQQ = @(Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Where-Object { $_.ExecutablePath -match '\\NapCat' -and (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me })
if ($napQQ) {
    if ($StopNapCat) { Stop-UnderDir "$Dir\NapCat"; Start-Sleep 3; Say '已停掉 NapCat（它登着同一个小号，同时开会互相挤下线）' }
    else { Say 'NapCat 还开着（多半登着同一个小号）。确认要换成 SnowLuma 的话，加 -StopNapCat 重跑'; exit 5 }
}

# 4. 下官方完整包，校验，原样解压（不改任何文件）
$cur = if (Test-Path "$sl\release.json") { (Get-Content -Raw "$sl\release.json" | ConvertFrom-Json).version } else { '' }
if ($cur -ne $Version.TrimStart('v')) {
    $name = "SnowLuma-$Version-win-x64.zip"; $zip = "$dl\$name"
    $known = @{ 'v2.2.1' = '09a3fbbf42ea805c4d61b5e67bec2a46ef341fa803f211d78c40796cb7f535dc' }
    $want = $known[$Version]
    if (-not $want) {
        try { $want = ((Invoke-RestMethod "https://api.github.com/repos/SnowLuma/SnowLuma/releases/tags/$Version" -TimeoutSec 20).assets | Where-Object name -eq $name).digest -replace '^sha256:', '' } catch {}
    }
    if (-not (Test-Path $zip) -or ($want -and (Get-FileHash $zip -Algorithm SHA256).Hash -ne $want)) {
        Say "下载 SnowLuma $Version 官方完整包（约 43 MB，自带运行环境）"
        Get-GitHubFile "https://github.com/SnowLuma/SnowLuma/releases/download/$Version/$name" $zip 30MB | Out-Null
    }
    if ($want -and (Get-FileHash $zip -Algorithm SHA256).Hash -ne $want) { Remove-Item $zip -Force; throw '下载的包和官方发布的校验值对不上（可能下坏了），已删掉，重跑本脚本重新下' }
    if (Test-Path $sl) {
        # 升级：只换程序，config\（登录信息、连接设置、后台密码）原样留着
        Stop-UnderDir $sl; Start-Sleep 2
        Get-ChildItem $sl -Force | Where-Object { $_.Name -notin 'config', 'logs', 'data' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    $tmp = "$dl\SnowLuma-unpack"; Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    $avMsg = { param($what) $av = Get-ThirdPartyAV; Say "$what：多半是杀毒软件$(if ($av.Count) { "（$($av -join '、')）" })把 SnowLuma 的文件当成可疑的拿走或锁住了。不用关杀毒：把 $Dir 加进它的信任区（references/杀毒软件.md；只有 Windows 自带杀毒的跑 trust-folder.ps1），再重跑本脚本"; exit 8 }
    try { Expand-Archive -Force $zip $tmp -ErrorAction Stop } catch { & $avMsg "解压失败（$($_.Exception.Message)）" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zf = [IO.Compression.ZipFile]::OpenRead($zip); $need = @($zf.Entries | Where-Object { $_.Name }).Count; $zf.Dispose()
    $got = @(Get-ChildItem $tmp -Recurse -File -Force -ErrorAction SilentlyContinue).Count
    if ($got -lt $need) { & $avMsg "解压出来少了 $($need - $got) 个文件" }
    $root = if (Test-Path "$tmp\index.mjs") { $tmp } else { (Get-ChildItem $tmp -Directory | Where-Object { Test-Path "$($_.FullName)\index.mjs" } | Select-Object -First 1).FullName }
    if (-not $root) { throw '压缩包里没找到 index.mjs，包不对' }
    New-Item -ItemType Directory -Force $sl | Out-Null
    Get-ChildItem $root -Force | Move-Item -Destination $sl -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Say "SnowLuma $Version 已解压到 $sl"
} else { Say "SnowLuma $Version 已经装好：$sl" }

# 5. 配置：全局什么都不连，只有小号连 AstrBot（反向 WS 127.0.0.1:6199，token 跟 AstrBot 一致，见 common.ps1 Set-SnowLumaOneBot）；网页后台只听本机；自动注入 QQ
$tokFile = "$priv\napcat-ws-token.txt"
if (-not (Test-Path $tokFile)) { [IO.File]::WriteAllText($tokFile, (New-RandomSecret 24)); Say '生成了连 AstrBot 用的 token（private\napcat-ws-token.txt）；AstrBot 那边跑 astr_setup.py bot 时会用同一个' }
$tok = (Get-Content -Raw $tokFile).Trim()
$cfg = "$sl\config"; New-Item -ItemType Directory -Force $cfg | Out-Null
Set-SnowLumaOneBot "$cfg\onebot.json"
$bot = if (Test-Path "$priv\bot-qq.txt") { (Get-Content -Raw "$priv\bot-qq.txt").Trim() } else { '' }
if ($bot -and (Test-Path "$cfg\onebot_$bot.json")) { Set-SnowLumaOneBot "$cfg\onebot_$bot.json" $tok }
$rt = if (Test-Path "$cfg\runtime.json") { Get-Content -Raw "$cfg\runtime.json" | ConvertFrom-Json } else { [pscustomobject]@{} }
$rt | Add-Member -Force webuiPort 5099; $rt | Add-Member -Force webuiHost '127.0.0.1'; $rt | Add-Member -Force hookAutoLoad $true
[IO.File]::WriteAllText("$cfg\runtime.json", ($rt | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))

# 6. 后台密码：我们先定好（它的规则：10 位以上、大小写、带符号），存 private\snowluma-webui.txt，第一次启动时交给它；不用再去黑窗口里抄临时密码
$pwFile = "$priv\snowluma-webui.txt"
if (-not (Test-Path $pwFile)) {
    $pw = (New-RandomSecret 14 -Mixed) + '#' + [char](Get-Random -Minimum 97 -Maximum 123)
    [IO.File]::WriteAllText($pwFile, $pw)
}
if ($AcceptAgreements) { [IO.File]::WriteAllText("$priv\snowluma-agreed.txt", "观众在对话里看过 SnowLuma 用户协议和隐私政策后回复同意：$(Get-Date -Format 'yyyy-MM-dd HH:mm')") }

# 7. 启动脚本：在桌面会话里跑（计划任务，普通权限，跟 QQ 一样），日志写 snowluma-run.log。密码和同意都从文件读，脚本里不写明文
$agreeLines = if (Test-Path "$priv\snowluma-agreed.txt") { "set SNOWLUMA_ACCEPT_EULA=1`r`nset SNOWLUMA_ACCEPT_PRIVACY=1`r`n" } else { '' }
$cmd = "@echo off`r`nchcp 65001 >nul`r`ntitle Dafeiyu-SnowLuma (do not close)`r`ncd /d `"$sl`"`r`nset SNOWLUMA_HOOK_AUTOLOAD=1`r`nset /p SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD=<`"$pwFile`"`r`n$agreeLines.\node.exe .\index.mjs > `"$sl\snowluma-run.log`" 2>&1`r`n"
[IO.File]::WriteAllText("$Dir\start-snowluma.cmd", $cmd, (New-Object Text.UTF8Encoding($false)))
Stop-UnderDir $sl; Start-Sleep 2
Start-Detached 'snowluma' "$Dir\start-snowluma.cmd"
if (-not (Wait-Port 5099 60)) {
    if (-not (Test-Path "$sl\node.exe") -or -not (Test-Path "$sl\index.mjs")) { $av = Get-ThirdPartyAV; Say "SnowLuma 的程序文件不见了：多半是杀毒软件$(if ($av.Count) { "（$($av -join '、')）" })拿走的。把 $Dir 加进信任区（references/杀毒软件.md）再重跑本脚本"; exit 8 }
    Get-Content "$sl\snowluma-run.log" -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { $_ -replace 'password=\S+', 'password=***' }
    throw 'SnowLuma 60 秒没起来，看上面的日志'
}
Say 'SnowLuma 起来了（网页后台 http://127.0.0.1:5099 ，账号 admin，密码在 private\snowluma-webui.txt）'

# 8. 桌面 QQ：这个 Windows 用户没开就替他打开（同样走计划任务，普通权限）
$myQQ = @(Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Where-Object { $_.ExecutablePath -eq $qqExe -and (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Domain)\$($_.User)" }) -eq $me })
if (-not $myQQ) {
    [IO.File]::WriteAllText("$Dir\start-qq.cmd", "@echo off`r`nstart `"`" `"$qqExe`"`r`n", (New-Object Text.UTF8Encoding($false)))
    Start-Detached 'qq' "$Dir\start-qq.cmd"
    Say '已打开桌面 QQ'
} else { Say '桌面 QQ 已经开着' }
Say '请观众在桌面 QQ 的窗口里用小号的手机 QQ 扫码登录（窗口里有「快速登录」就点它）。登好以后跑 connect-snowluma.ps1，它会等 SnowLuma 接上 AstrBot'
