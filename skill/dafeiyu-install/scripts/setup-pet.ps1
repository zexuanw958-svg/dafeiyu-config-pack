# 让电脑桌宠也变成她（references/桌宠.md）：DeepSeek Harness 的桌宠插件 dsh-pet 跟他说话时，说话的就是 QQ 里这个大肥鱼
# （同一份人设、同一个 DeepSeek、同一套工具，记忆跟 QQ 私聊共用一份）。可以重复跑。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File setup-pet.ps1 -Dir <工作目录>
#       -Check          只看现在接没接上，不改东西
#       -Remove         拆掉：DSH 那边的设置改回去、停桥、删开机自启、作废桌宠的 key。记忆范围和互通插件留着（不影响 QQ 里的她）
#       -DshHome <目录>  DSH 的数据目录不在 %USERPROFILE%\.dsh 时指定
# 前提：第 1-7 步做完（她在 QQ 里能回话）；他自己在 DSH 的「插件管理」里装好了 dsh-pet。
# 做的事：
#   1 后台建一把只给桌宠用的 API key（astr_setup.py pet-key → private\pet-key.txt，另生成调桥的口令 private\pet-bridge-token.txt）
#   2 装「桌宠互通」插件，记忆改成 QQ 私聊和桌宠共用一份（import-pack.ps1 -Only pet）
#   3 「大肥鱼桥」拷进 tools\，后台起来（只听 127.0.0.1:26199），开机自启（启动文件夹「大肥鱼-桌宠桥」，不出窗口）
#   4 DSH 里登记自定义模型「大肥鱼（QQ 同款）」，dsh-pet 的对话模型选它，名字、人设换成她（pet_config.py apply）
# 最后一行 RESULT pet=ok|partial|fail reason=… next=… 给 Agent 读。输出不含 key、口令、QQ 号。
param([Parameter(Mandatory)][string]$Dir, [switch]$Remove, [switch]$Check, [string]$DshHome = '')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = $Dir.TrimEnd('\')
$port = 26199
$tools = "$Dir\tools"
$cmd = "$Dir\start-petbridge.cmd"
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) '大肥鱼-桌宠桥.lnk'
if (-not $DshHome) { $DshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { "$env:USERPROFILE\.dsh" } }
$py = Find-AstrPython $Dir
if (-not $py) { Say 'RESULT pet=fail reason=no-python next=找不到 AstrBot 自带的 Python：先确认第 2 步装好了'; exit 1 }
# 脚本从 Skill 跑，也可能从 tools\ 跑（工作台、卸载）：缺的文件两边找
function Find-Script([string]$Name) { foreach ($d in $PSScriptRoot, $tools) { if (Test-Path "$d\$Name") { return "$d\$Name" } }; return '' }

# DSH 网页服务开在哪几个端口（桌面版每次随机）：找得到 pet_config.py 就顺手让桌宠重载、核对模型清单；找不到也不影响写设置
function Get-DshPorts {
    $ids = @(Get-Process -Name 'DeepSeek Harness', 'dsh' -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    if (-not $ids) { return '' }
    $ps = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { ($ids -contains $_.OwningProcess) -and ($_.LocalAddress -in '127.0.0.1', '::1') } | ForEach-Object { $_.LocalPort } | Sort-Object -Unique)
    return ($ps -join ',')
}
function Get-Bridge([switch]$Deep) {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$port/health$(if ($Deep) { '?deep=1' })" -TimeoutSec 8 -UseBasicParsing } catch { return $null }
}
function Stop-Bridge {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $c = "$($_.CommandLine)"; $c.IndexOf('dafeiyu_pet_bridge.py', [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $c.IndexOf($Dir, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-CmdRunning $cmd | ForEach-Object { & taskkill.exe /pid $_.ProcessId /t /f 2>&1 | Out-Null }
}
function Invoke-PetConfig([string]$Verb, [string[]]$More = @()) {
    $pc = Find-Script 'pet_config.py'
    if (-not $pc) { Say '没找到 pet_config.py'; return @() }
    # 空字符串参数 PowerShell 5.1 传给 python 时会被吞掉、后面的参数错位：端口找不到就不传
    $a = @($pc, $Verb, '--dsh-home', $DshHome) + $More
    $ports = Get-DshPorts
    if ($ports) { $a += @('--dsh-ports', $ports) }
    $out = @(& $py -X utf8 @a 2>&1 | ForEach-Object { "$_" })
    $out | Where-Object { $_ -notmatch '^\{' } | ForEach-Object { Say "  $_" }
    return $out
}

# ---------------- 只看 ----------------
if ($Check) {
    $h = Get-Bridge -Deep
    $hasKey = Test-Path "$Dir\private\pet-key.txt"
    Say "桌宠的 key：$(if ($hasKey) { '有' } else { '没有（还没配过桌宠）' })"
    Say "大肥鱼桥：$(if (-not $h) { '没在跑' } elseif ($h.astrbot -eq 'ok') { '在跑，连得上她的大脑' } else { "在跑，但连不上她的大脑（$($h.reason)）" })"
    Say "开机自启：$(if (Test-Path $lnk) { '有' } else { '没有' })"
    $st = Invoke-PetConfig 'status' | Where-Object { $_ -match '^\{' } | Select-Object -First 1
    $s = if ($st) { try { $st | ConvertFrom-Json } catch { $null } }
    Say "DSH 和 dsh-pet 的设置：$(if ($s -and $s.ok) { '都指向她' } elseif ($s) { "还没弄全（自定义模型 $(@($s.profiles.PSObject.Properties | Where-Object { $_.Value }).Count) 个 profile、口令 $(if ($s.credential) { '有' } else { '没有' })、对话模型 $(if ($s.chat_model) { '是她' } else { '不是她' })）" } else { '读不出来' })"
    $ok = $hasKey -and $h -and $h.astrbot -eq 'ok' -and $s -and $s.ok
    Say "RESULT pet=$(if ($ok) { 'ok' } else { 'partial' })$(if (-not $ok) { ' next=跑一次 setup-pet.ps1（不带 -Check）补上' })"
    exit 0
}

# ---------------- 拆掉 ----------------
if ($Remove) {
    Say '把桌宠改回原来的样子…'
    $r = Invoke-PetConfig 'revert'
    Stop-Bridge
    & schtasks.exe /delete /tn 'dafeiyu-petbridge' /f 2>&1 | Out-Null
    Remove-Item $lnk, $cmd -Force -ErrorAction SilentlyContinue
    Say '大肥鱼桥停了，开机自启删了'
    $as = Find-Script 'astr_setup.py'
    if ($as) { & $py -X utf8 $as --dir $Dir pet-key remove 2>&1 | ForEach-Object { Say "  $_" } }
    else { Remove-Item "$Dir\private\pet-key.txt", "$Dir\private\pet-bridge-token.txt" -Force -ErrorAction SilentlyContinue; Say '  没找到 astr_setup.py：key 文件删了，后台「API 密钥」里那把「大肥鱼桌宠」请他自己删' }
    foreach ($f in 'dafeiyu_pet_bridge.py', 'pet_config.py', 'pet-persona.txt') { if ($PSScriptRoot -ne $tools) { Remove-Item "$tools\$f" -Force -ErrorAction SilentlyContinue } }
    Say '桌宠互通插件和记忆范围没动（QQ 里的她照旧，桌宠那边说过的话还在她的记忆里）'
    Say "RESULT pet=removed$(if (-not ($r -match 'RESULT dsh=reverted')) { ' reason=dsh-not-reverted next=DSH 那边没改回去：照 references/桌宠.md「拆掉」那节手动改' })"
    exit 0
}

# ---------------- 装 ----------------
# 0 他装没装 dsh-pet（这期不教装：没装就停，不主动推）
$det = (Invoke-PetConfig 'detect' | Where-Object { $_ -match '^\{' } | Select-Object -First 1)
$d = if ($det) { try { $det | ConvertFrom-Json } catch { $null } }
if (-not $d -or -not $d.dsh_home_exists) { Say "RESULT pet=fail reason=no-dsh next=没找到 DeepSeek Harness 的数据目录（$DshHome）：他没装 DSH 桌面版就不做这一步"; exit 1 }
if (-not @($d.pet_profiles).Count -and -not $d.pet_dir) { Say 'RESULT pet=fail reason=no-dsh-pet next=DSH 里没装桌宠插件 dsh-pet：这期不教装，他想要就自己在 DSH 的插件管理里装好、退出重开 DSH 后再来'; exit 1 }
Say "找到了桌宠插件 dsh-pet$(if ($d.pet_version) { " $($d.pet_version)" })（profile：$(@($d.pet_profiles) -join '、')）"

# 1 桌宠用的 key + 调桥的口令
$as = Find-Script 'astr_setup.py'
& $py -X utf8 $as --dir $Dir pet-key 2>&1 | ForEach-Object { Say "  $_" }
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$Dir\private\pet-key.txt")) { Say 'RESULT pet=fail reason=pet-key next=后台建不了 key：看 AstrBot 开没开（工作台「大脑」那一项），再跑一次'; exit 1 }

# 2 互通插件 + 记忆范围
$ip = Find-Script 'import-pack.ps1'
$imp = @(& powershell -NoProfile -ExecutionPolicy Bypass -File $ip -Dir $Dir -Only pet 2>&1 | ForEach-Object { "$_" })
$imp | Where-Object { $_ -match '^\s*\[|插件|记忆|RESULT|MIGRATE|没|失败' } | Select-Object -Last 12 | ForEach-Object { Say "  $_" }
$petRes = ($imp | Where-Object { $_ -match 'RESULT pet=' } | Select-Object -Last 1)
$migrate = [bool]($imp | Where-Object { $_ -match 'MIGRATE private-memories' })

# 3 桥：拷进 tools\（他删了 Skill 也能跑），写启动脚本，后台起来，开机自启
New-Item -ItemType Directory -Force $tools, "$Dir\logs" | Out-Null
foreach ($f in 'dafeiyu_pet_bridge.py', 'pet_config.py', 'pet-persona.txt') {
    $s = Find-Script $f
    if ($s -and $s -ne "$tools\$f") { Copy-Item $s "$tools\$f" -Force }
}
$body = "@echo off`r`nchcp 65001 >nul`r`ntitle Dafeiyu-PetBridge`r`nset PYTHONUTF8=1`r`n`"$py`" -X utf8 `"$tools\dafeiyu_pet_bridge.py`" --dir `"$Dir`" >> `"$Dir\logs\pet-bridge-run.log`" 2>&1`r`n"
[IO.File]::WriteAllText($cmd, $body, (New-Object Text.UTF8Encoding($false)))
$h = Get-Bridge
if ($h -and $h.version) {
    # 已经在跑（重跑这个脚本、或者换了新版桥）：重开一次，让它读到新的 key 和新代码
    Stop-Bridge; Start-Sleep 1
}
Start-Detached 'petbridge' $cmd
$h = $null
for ($i = 0; $i -lt 15 -and -not $h; $i++) { Start-Sleep 1; $h = Get-Bridge }
$ws = New-Object -ComObject WScript.Shell
$l = $ws.CreateShortcut($lnk); $l.TargetPath = "$env:SystemRoot\System32\conhost.exe"; $l.Arguments = "--headless cmd.exe /c `"$cmd`""
$l.WorkingDirectory = $Dir; $l.WindowStyle = 7; $l.Description = '桌宠跟大肥鱼说话走这里，别删'; $l.Save()
Say '开机自启：大肥鱼桥（后台跑、不出窗口）'
if (-not $h) { Say "RESULT pet=fail reason=bridge next=大肥鱼桥没起来：看 $Dir\logs\pet-bridge-run.log 和 pet-bridge.log 最后几行"; exit 1 }
& $py -X utf8 "$tools\dafeiyu_pet_bridge.py" --dir $Dir --check 2>&1 | ForEach-Object { Say "  $_" }
$bridgeOk = ($LASTEXITCODE -eq 0)

# 4 DSH 和 dsh-pet 的设置
$pc = Invoke-PetConfig 'apply' @('--token-file', "$Dir\private\pet-bridge-token.txt", '--port', "$port")
$dsh = ($pc | Where-Object { $_ -match '^RESULT dsh=' } | Select-Object -Last 1)

# 汇总
$reasons = @(); $next = @()
if (-not $bridgeOk) { $reasons += 'bridge-astrbot'; $next += '桥连不上她的大脑：看工作台「大脑」那一项' }
if (-not $petRes -or $petRes -notmatch 'pet=ok') { $reasons += 'petlink'; $next += '互通插件或记忆范围没弄完：再跑一次 import-pack.ps1 -Only pet' }
if ($dsh -match 'dsh=manual') { $reasons += 'dsh-manual'; $next += '照 references/桌宠.md「手动填」带他在 DSH 设置页里点' }
elseif ($dsh -match 'reason=restart-dsh') { $reasons += 'restart-dsh'; $next += '让他从托盘退出 DSH 再打开（会断掉这次对话，先把要说的说完）' }
elseif ($dsh -notmatch 'dsh=ok') { $reasons += 'dsh'; $next += 'DSH 那边没改成：看上面「没改成」那几行' }
if ($migrate) { Say 'MIGRATE private-memories' }
$state = if (-not $reasons) { 'ok' } elseif ($bridgeOk -and $dsh -match 'dsh=(ok|manual)') { 'partial' } else { 'fail' }
Say ("RESULT pet=$state" + $(if ($reasons) { " reason=$($reasons -join ',')" }) + $(if ($next) { " next=$($next -join '；')" }))
