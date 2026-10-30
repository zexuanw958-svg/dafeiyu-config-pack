# 复制求助信息（产品评审 S5）：收集版本、端口、开机自启、补装结果和最后 200 行日志，去掉密钥、口令、密码，QQ 号、群号只留头尾，
# 存成桌面上的「大肥鱼求助信息-<月日-时分>.txt」，再生成一句「卡在：…；原因：…；代码 …」复制到剪贴板。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File diag.ps1 -Dir <工作目录> [-NoClipboard] [-Json]
#   -NoClipboard：不动剪贴板（测试用）。-Json：最后多打一行 DIAG_JSON {...}（工作台读这一行）。
# 只读：不改任何设置，不开也不关任何程序。DeepSeek 余额用他自己的 key 在本机查，key 本身不写进任何地方。
param([Parameter(Mandatory)][string]$Dir, [switch]$NoClipboard, [switch]$Json)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
if (-not (Test-Path $Dir)) { throw "没有这个文件夹：$Dir" }

# ---------- 打码：private 里的所有秘密原样换成 ****，再按规则去掉 key、口令、长数字 ----------
$secretVals = New-Object Collections.ArrayList
$qqVals = New-Object Collections.ArrayList
foreach ($f in @(Get-ChildItem "$Dir\private" -File -ErrorAction SilentlyContinue)) {
    if ($f.Name -eq 'protocol.txt' -or $f.Length -gt 20000) { continue }
    if ($f.Name -eq 'group.txt' -or $f.Name -like '*-qq.txt') {   # QQ 号、群号只留头尾（分得清大号小号），5、6 位的老号也打
        foreach ($m in [regex]::Matches("$(Get-Content -Raw $f.FullName -ErrorAction SilentlyContinue)", '\d{5,}')) { [void]$qqVals.Add($m.Value) }
        continue
    }
    $raw = "$(Get-Content -Raw -Encoding UTF8 $f.FullName -ErrorAction SilentlyContinue)".Trim()
    if (-not $raw) { continue }
    $lines = @($raw -split "`r?`n" | Where-Object { $_.Trim() })
    $isKv = $lines.Count -gt 0 -and -not ($lines | Where-Object { ($_ -replace '：', ':') -notmatch ':' })
    if ($isKv) {
        foreach ($ln in $lines) {
            $k, $v = ($ln -replace '：', ':') -split ':\s*', 2
            $k = "$k".Trim().ToLower(); $v = "$v".Trim()
            if ($k -match 'url' -or $k -in 'username', 'user', '账号', '用户名') { continue }
            if ($v.Length -ge 4) { [void]$secretVals.Add($v) }
        }
    } elseif ($raw.Length -ge 4) { [void]$secretVals.Add($raw) }
}
$secretVals = @($secretVals | Sort-Object Length -Descending -Unique)
$qqVals = @($qqVals | Sort-Object Length -Descending -Unique)
function Hide([string]$s) {
    if (-not $s) { return $s }
    foreach ($v in $secretVals) { $s = $s.Replace($v, '****') }
    foreach ($v in $qqVals) { $s = $s -replace "(?<!\d)$v(?!\d)", ($v.Substring(0, 2) + '****' + $v.Substring($v.Length - 2)) }
    $s = $s -replace 'sk-[A-Za-z0-9_\-]{8,}', 'sk-****'
    $s = $s -replace 'brz_[A-Za-z0-9_\-]{8,}', 'brz_****'
    $s = $s -replace 'eyJ[\w\-]{10,}\.[\w\-]{10,}\.[\w\-]{5,}', '****'
    $s = $s -replace '(?i)(bearer\s+)[\w.\-~+/=]+', '$1****'
    $s = $s -replace '(?i)((?:access_?token|api_?key|apikey|secret|password|passwd|pwd|token|密码)["'']?\s*[:=：]\s*["'']?)[^\s"'',;&}]+', '$1****'
    $s = $s -replace 'u_[A-Za-z0-9_\-]{16,}', 'u_****'
    $s = $s -replace '(?i)([A-Z]:\\Users\\)[^\\/:*?"<>|\r\n]+', '$1<用户名>'
    # 7 位以上的数字（QQ 号、群号）只留头尾；20261028 这种日期不打
    $s = [regex]::Replace($s, '(?<![\d.])\d{7,}(?!\d)', { param($m) $d = $m.Value; if ($d -match '^20\d\d(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$') { $d } else { $d.Substring(0, 2) + '****' + $d.Substring($d.Length - 2) } })
    return $s
}
function Mask-QQ([string]$s) { $s = "$s".Trim(); if ($s -match '^\d{5,}$') { return $s.Substring(0, 2) + '****' + $s.Substring($s.Length - 2) } elseif ($s) { return '****' } else { return '' } }
function Tail([string]$Path, [int]$N = 200) {
    if (-not $Path -or -not (Test-Path $Path)) { return @() }
    $noise = '\[WebUI\] (GET|POST|PUT|DELETE) |receive health armed|system face catalog'
    $lines = @(Get-Content -Encoding UTF8 -Tail ($N * 4) $Path -ErrorAction SilentlyContinue | ForEach-Object { ($_ -replace "\x1b\[[0-9;]*[A-Za-z]", '').TrimEnd() } | Where-Object { $_ -and $_ -notmatch $noise })
    if ($lines.Count -gt $N) { $lines = $lines[($lines.Count - $N)..($lines.Count - 1)] }
    return $lines
}

# ---------- 底子：装了什么、端口、进程 ----------
$ans = @{}
try { (Get-Content -Raw -Encoding UTF8 "$Dir\answers.json" | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $ans[$_.Name] = "$($_.Value)" } } catch {}
$proto = "$(Get-Content -Raw "$Dir\private\protocol.txt" -ErrorAction SilentlyContinue)".Trim().ToLower()
if ($proto -notin 'napcat', 'snowluma') { $proto = if ((Test-Path "$Dir\start-snowluma.cmd") -and -not (Test-Path "$Dir\start-napcat.cmd")) { 'snowluma' } else { 'napcat' } }
$pname = if ($proto -eq 'snowluma') { 'SnowLuma' } else { 'NapCat' }
$proot = "$Dir\$pname"
$port = Get-BackendPort $Dir
$cli = Test-Path "$Dir\start-astrbot.cmd"
$tcp = @(Get-NetTCPConnection -ErrorAction SilentlyContinue)
function Listening([int]$p) { [bool]($tcp | Where-Object { $_.LocalPort -eq $p -and "$($_.State)" -eq 'Listen' }) }
$qqConn = @($tcp | Where-Object { "$($_.State)" -eq 'Established' -and $_.RemotePort -eq 6199 })
$procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$mine = @($procs | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith("$Dir\", [StringComparison]::OrdinalIgnoreCase) })
$protoProcs = @($mine | Where-Object { $_.ExecutablePath.StartsWith("$proot\", [StringComparison]::OrdinalIgnoreCase) })
$astrProcs = @($mine | Where-Object { $_.ExecutablePath.StartsWith("$Dir\uv\", [StringComparison]::OrdinalIgnoreCase) })
$now = Get-Date
function Since($t) { if (-not $t) { return '' }; $s = $now - $t; if ($s.TotalDays -ge 1) { '{0} 天 {1} 小时' -f [int][Math]::Floor($s.TotalDays), $s.Hours } elseif ($s.TotalHours -ge 1) { '{0} 小时 {1} 分' -f [int][Math]::Floor($s.TotalHours), $s.Minutes } else { '{0} 分钟' -f [int][Math]::Max(1, [Math]::Floor($s.TotalMinutes)) } }

$checks = New-Object Collections.ArrayList   # 每项：状态、名字、说明
$probs = New-Object Collections.ArrayList    # 每项：代码、卡在、原因（按严重程度先后放）
function Check([string]$State, [string]$Name, [string]$Text) { [void]$checks.Add([pscustomobject]@{ State = $State; Name = $Name; Text = $Text }) }
function Problem([string]$Code, [string]$Where, [string]$Why) { [void]$probs.Add([pscustomobject]@{ Code = $Code; Where = $Where; Why = $Why }) }

# 1 大脑
$brainUp = Listening $port
if ($brainUp) {
    $lp = ($tcp | Where-Object { $_.LocalPort -eq $port -and "$($_.State)" -eq 'Listen' } | Select-Object -First 1).OwningProcess
    $st = ($procs | Where-Object { $_.ProcessId -eq $lp } | Select-Object -First 1).CreationDate
    Check '正常' '大脑 AstrBot' ("后台端口 $port 在听" + $(if ($st) { "，连续跑了 $(Since $st)" }))
} elseif ($astrProcs) {
    Check '出问题' '大脑 AstrBot' "程序开着（$($astrProcs.Count) 个进程），后台端口 $port 没在听"
    Problem 'BRAIN-STUCK' '大脑（AstrBot）卡住了' "程序开着，后台端口 $port 一直没反应"
} else {
    Check '出问题' '大脑 AstrBot' ("没开（后台端口 $port 没在听）" + $(if (-not $cli) { '；是启动器装的，在 AstrBot 启动器里启动' }))
    Problem 'BRAIN-OFF' '大脑（AstrBot）没开' "后台端口 $port 没在听，工作目录里也没有它的进程"
}

# 2 登 QQ 的程序
$bot = Mask-QQ "$(Get-Content -Raw "$Dir\private\bot-qq.txt" -ErrorAction SilentlyContinue)"
$slLog = Get-ChildItem "$Dir\SnowLuma\logs\*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$suspect = $false
if ($proto -eq 'snowluma' -and $slLog) {
    $good = -1; $bad = -1; $i = 0
    foreach ($ln in @(Get-Content -Encoding UTF8 -Tail 3000 $slLog.FullName -ErrorAction SilentlyContinue)) {
        if ($ln -match 'pipe connected|connected ws://') { $good = $i }
        if ($ln -match '(?i)retry exhausted|(qq|version).{0,40}(unsupported|not supported)') { $bad = $i }
        $i++
    }
    $suspect = $bad -gt $good
}
if ($qqConn) { Check '正常' "登 QQ 的程序 $pname" ("小号 $bot 在线（6199 上有 $($qqConn.Count) 个连接）") }
elseif (-not $brainUp) { Check '出问题' "登 QQ 的程序 $pname" "大脑没开，小号也连不上（$pname 进程 $($protoProcs.Count) 个）" }
elseif (-not $protoProcs) {
    Check '出问题' "登 QQ 的程序 $pname" "$pname 没在运行"
    Problem 'QQ-NOPROC' '小号没登上 QQ' "$pname 没在运行"
} elseif ($suspect) {
    Check '出问题' "登 QQ 的程序 $pname" "$pname 开着但挂不上桌面 QQ（日志里有 retry exhausted），多半是 QQ 自动升级了"
    Problem 'QQ-UPGRADE' '小号没登上 QQ' "SnowLuma 挂不上桌面 QQ（auto-load retry exhausted），多半是 QQ 自动升级了，SnowLuma 还没跟上"
} else {
    Check '出问题' "登 QQ 的程序 $pname" "$pname 开着，但没连上大脑（6199 上没有连接），像是小号被踢下线了"
    Problem 'QQ-OFF' '小号没登上 QQ' "$pname 开着，但没连上大脑，像是小号被踢下线了"
}

# 3 DeepSeek（只取金额；key 用完马上丢掉）
$keyFile = "$Dir\private\deepseek-key.txt"
if (-not (Test-Path $keyFile)) {
    Check '出问题' 'DeepSeek' '没有 private\deepseek-key.txt'
    Problem 'DS-NOKEY' 'DeepSeek 没接好' '找不到密钥文件 private\deepseek-key.txt'
} else {
    $key = "$(Get-Content -Raw -Encoding UTF8 $keyFile)".Trim()
    if ($key -match '[:：]') { $kv = Read-KeyValueFile $keyFile; $key = ($kv.GetEnumerator() | Where-Object { $_.Key -match 'key' } | Select-Object -First 1).Value }
    $key = "$key".Split()[0]
    try {
        try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch {}
        $r = Invoke-RestMethod -Uri 'https://api.deepseek.com/user/balance' -Headers @{ Authorization = "Bearer $key"; Accept = 'application/json' } -TimeoutSec 10 -UseBasicParsing
        $info = @($r.balance_infos) | Where-Object { $_.currency -eq 'CNY' } | Select-Object -First 1
        if (-not $info) { $info = @($r.balance_infos)[0] }
        $amt = [double]"$($info.total_balance)"
        if (-not $r.is_available -or $amt -le 0) {
            Check '出问题' 'DeepSeek' "余额 ¥$amt，用完了"
            Problem 'DS-NOMONEY' 'DeepSeek 余额用完了' "余额 ¥$amt，她回不了话"
        } elseif ($amt -lt 2) {
            Check '要注意' 'DeepSeek' "余额只剩 ¥$amt"
            Problem 'DS-LOW' 'DeepSeek 余额快用完了' "只剩 ¥$amt"
        } else { Check '正常' 'DeepSeek' "余额 ¥$amt" }
    } catch {
        $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
        if ($code -eq 401 -or $code -eq 403) {
            Check '出问题' 'DeepSeek' "密钥不对或被删了（接口回 $code）"
            Problem 'DS-BADKEY' 'DeepSeek 用不了' "密钥不对或被删了（接口回 $code）"
        } elseif ($code) { Check '查不到' 'DeepSeek' "余额接口回 $code" }
        else { Check '查不到' 'DeepSeek' "连不上 api.deepseek.com（$(Hide $_.Exception.Message)）" }
    }
    $key = $null; $kv = $null
}

# 4 记忆：看实际接线（插件指向哪个记忆模型、在线的有没有 key、本机的模型在不在），不光看 answers.json。
# 以前选了 2 就写「正常」，10-28 群友那台插件配的是硅基流动、key 空着也是「正常」。插件在后台开没开这里看不出来（工作台和 astr_setup.py status 看得到）
$mem = $ans['memory']
$astrData = Find-AstrDataDir $Dir
$mw = Get-MemoryWiring $astrData
$mwName = if ($mw.local) { '记忆（本机 Ollama）' } elseif ($mw.providerId -match '硅基|siliconflow') { '记忆（硅基流动）' } else { '记忆' }
if ($mem -eq '0' -or (-not $mem -and $mw.problem -in 'no-plugin', 'no-provider')) { Check '没开' '记忆' $(if ($mem -eq '0') { '装的时候选了先不要' } else { '装的时候没选' }) }
else {
    switch ($mw.problem) {
        '' { Check '正常' $mwName $(if ($mw.local) { "Ollama 开着，$($mw.model) 在" } else { "在线服务，不用开（$($mw.providerId)，有 key）" }) }
        'no-plugin' { Check '出问题' '记忆' '记忆插件没装上'; Problem 'MEM-NOPROVIDER' '记不住事' '选了记忆，但记忆插件没装上' }
        'no-provider' { Check '出问题' '记忆' '记忆插件没指向记忆模型'; Problem 'MEM-NOPROVIDER' '记不住事' '记忆插件没指向记忆模型（上次没接好）' }
        'no-key' { Check '出问题' $mwName "记忆模型 $($mw.providerId) 是在线的，但 key 是空的"; Problem 'MEM-NOKEY' '记不住事' '记忆接的是在线服务（硅基流动），但没填 key' }
        'ollama-off' { Check '出问题' $mwName 'Ollama 没开（11434 没在听）'; Problem 'MEM-OFF' '记不住新的事' 'Ollama 没开（11434 没在听）' }
        'no-model' { Check '出问题' $mwName "Ollama 开着，没找到 $($mw.model)"; Problem 'MEM-NOMODEL' '记不住新的事' "Ollama 开着，但记忆模型 $($mw.model) 不在" }
    }
}

# 5 语音
$voice = $ans['voice']
switch ($voice) {
    '4' {
        if (Listening 8137) { Check '正常' '语音（本机 Breeze）' '8137 在听' }
        else {
            Check '出问题' '语音（本机 Breeze）' 'Breeze 没在运行（8137 没在听）'
            Problem 'VOICE-OFF' '发不了语音' 'Breeze 没在运行（8137 没在听）'
        }
    }
    '5' { Check '正常' '语音（Breeze 在线版）' '已选择在线服务；余额、密钥和连通性以实际合成为准' }
    '3' { Check '正常' '语音（豆包）' '在线服务，不用开' }
    '2' { Check '正常' '语音（小米 MiMo）' '在线服务，不用开' }
    '1' { Check '正常' '语音（Edge 免费女声）' '在线服务，不用开' }
    default { Check '没开' '语音' '装的时候没选' }
}

# 6 看图看视频
if ($ans['video'] -eq '1') {
    $ff = @($ans['ffmpeg_path'], "$(Get-Content -Raw "$Dir\ffmpeg-path.txt" -ErrorAction SilentlyContinue)".Trim(), "$Dir\ffmpeg\bin\ffmpeg.exe") | Where-Object { $_ } | Select-Object -First 1
    $asr = @($ans['asr_dir'], "$(Get-Content -Raw "$Dir\asr-path.txt" -ErrorAction SilentlyContinue)".Trim(), "$Dir\asr") | Where-Object { $_ } | Select-Object -First 1
    if (-not (Test-Path $ff)) {
        Check '出问题' '看图看视频' '找不到 ffmpeg'
        Problem 'VIDEO-NOFFMPEG' '看不了视频' '找不到 ffmpeg'
    } elseif ((Test-Path "$asr\sherpa-onnx-offline.exe") -and (Test-Path "$asr\tokens.txt") -and (Get-Item "$asr\model.int8.onnx" -ErrorAction SilentlyContinue).Length -ge 200MB) {
        Check '正常' '看图看视频' ('ffmpeg 在，本机识别模型在' + $(if (Test-Path "$asr\funasr-nano\llm.int8.onnx") { '（含增强版）' }))
    } else {
        # 以前只看识别程序在不在：老版本安装脚本断了会在 asr 里留半截模型，也算「在」
        $vk = ''; try { $vk = "$((Get-Content -Raw -Encoding UTF8 "$astrData\config\astrbot_plugin_dafeiyu_video_config.json" | ConvertFrom-Json).asr_key)".Trim() } catch {}
        $half = Test-Path "$asr\model.int8.onnx"
        if ($vk) { Check '正常' '看图看视频' 'ffmpeg 在；视频里的话用硅基流动在线识别' }
        else {
            Check '要注意' '看图看视频' ('ffmpeg 在；' + $(if ($half) { '本机识别模型只下了一半' } else { '本机识别模型没装' }) + '，听不到视频里说的话')
            Problem 'VIDEO-NOASR' '视频里的话听不到' $(if ($half) { '识别模型只下了一半（重跑 install-asr.ps1 会接着下）' } else { '识别模型没装上（install-asr.ps1，先走国内 ModelScope）' })
        }
    }
} else { Check '正常' '看图看视频' '能看图；看视频没选' }

# 7 开机自启
$startup = [Environment]::GetFolderPath('Startup')
$lnks = @(Get-ChildItem $startup -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
$miss = New-Object Collections.ArrayList
if ($cli -and '大肥鱼-AstrBot.lnk' -notin $lnks) { [void]$miss.Add('大脑') }
if ("大肥鱼-$pname.lnk" -notin $lnks) { [void]$miss.Add($pname) }
if ($mem -eq '1' -and 'Ollama.lnk' -notin $lnks) { [void]$miss.Add('Ollama') }
if ($voice -eq '4' -and -not ($lnks | Where-Object { $_ -match '(?i)breeze' })) { [void]$miss.Add('Breeze') }
# 启动器装的大脑：开机自启在启动器「设置 → 高级 → 通用 → 开机自启动」里开（我们不替他改）
if (-not $cli -and $astrData -match '\\core\\data$' -and -not (Get-LauncherAutostart)) { [void]$miss.Add('大脑（AstrBot 启动器没开「开机自启动」）') }
$both = '大肥鱼-SnowLuma.lnk' -in $lnks -and '大肥鱼-NapCat.lnk' -in $lnks
if ($both) {
    Check '要注意' '开机自启' 'SnowLuma 和 NapCat 都设了开机自启，会抢同一个小号'
    Problem 'AUTO-BOTH' '开机自启设重了' 'SnowLuma 和 NapCat 都会开机自启，会抢同一个小号'
} elseif ($miss.Count) {
    Check '要注意' '开机自启' "缺：$($miss -join '、')"
    Problem 'AUTO-MISS' '开机后不会自己上线' "开机自启缺：$($miss -join '、')"
} else { Check '正常' '开机自启' '都在' }

# 8 表情包
$dd = if (Test-Path "$Dir\astrbot\data") { "$Dir\astrbot\data" } else { '' }
$memeSets = @(); $memeImgs = 0
if ($dd) {
    try {
        $reg = Get-Content -Raw -Encoding UTF8 "$dd\plugin_data\meme_manager\registry.json" | ConvertFrom-Json
        foreach ($p in @($reg.installed_packs)) {
            if ($p.enabled -eq $false -or -not $p.id) { continue }
            $n = @(Get-ChildItem "$dd\plugin_data\meme_manager\packs\$($p.id)" -Recurse -File -Include *.png, *.jpg, *.jpeg, *.gif, *.webp -ErrorAction SilentlyContinue).Count
            $memeSets += "$($p.id)（$n 张）"; $memeImgs += $n
        }
    } catch {}
}
$memeRes = ''
if (Test-Path "$Dir\logs\import-memes.log") { $memeRes = (Get-Content -Encoding UTF8 -Tail 50 "$Dir\logs\import-memes.log" | Where-Object { $_ -match '^RESULT memes=' } | Select-Object -Last 1) }
if ($memeSets) { Check '正常' '表情包' "$($memeSets.Count) 套：$($memeSets -join '、')" }
elseif ($memeRes -match 'reason=network') {
    Check '出问题' '表情包' "没装上：$memeRes"
    Problem 'MEME-NET' '表情包下载' '连不上 GitHub（几个加速地址都没通）'
} else {
    Check '要注意' '表情包' ('一套都没装上' + $(if ($memeRes) { "（$memeRes）" }))
    Problem 'MEME-MISS' '表情包没装上' $(if ($memeRes) { $memeRes } else { '还没补装过' })
}

# 补装结果（后台日志的 RESULT 行）
$imports = New-Object Collections.ArrayList
foreach ($f in @(Get-ChildItem "$Dir\logs\import-*.log" -ErrorAction SilentlyContinue | Sort-Object Name)) {
    $blk = $f.BaseName -replace '^import-', ''
    $res = Get-Content -Encoding UTF8 -Tail 80 $f.FullName | Where-Object { $_ -match '^RESULT ' } | Select-Object -Last 1
    $running = (-not $res) -and (($now - $f.LastWriteTime).TotalSeconds -lt 180)
    [void]$imports.Add([pscustomobject]@{ Block = $blk; File = $f.FullName; Result = $res; Running = $running; Time = $f.LastWriteTime })
    if ($res -match '=(fail|partial)' -and $blk -ne 'memes') { Problem ("IMPORT-{0}-FAIL" -f $blk.ToUpper()) "补装$blk" (Hide $res) }
}

# 大脑日志里的错
$astLog = if (Test-Path "$Dir\astrbot\run.log") { "$Dir\astrbot\run.log" } else { '' }
$astLines = @(Tail $astLog 200)
$errLines = @($astLines | Where-Object { $_ -match '\[(ERRO|ERROR|CRIT)' })
if ($errLines.Count) {
    $first = ($errLines[-1] -replace '^\[[^\]]*\]\s*', '' -replace '^\[[^\]]*\]\s*\[[^\]]*\]\s*(\[[^\]]*\]:\s*)?', '').Trim()
    if ($first.Length -gt 70) { $first = $first.Substring(0, 70) + '…' }
    Problem 'LOG-ERR' '没查出大问题' ("大脑日志最近有 $($errLines.Count) 个错误，最后一个：" + (Hide $first))
}

# ---------- 一句话 ----------
$order = 'BRAIN-OFF', 'BRAIN-STUCK', 'QQ-NOPROC', 'QQ-UPGRADE', 'QQ-OFF', 'DS-NOKEY', 'DS-BADKEY', 'DS-NOMONEY', 'VOICE-OFF', 'MEM-NOKEY', 'MEM-NOPROVIDER', 'MEM-OFF', 'MEM-NOMODEL', 'VIDEO-NOFFMPEG', 'VIDEO-NOASR', 'MEME-NET', 'MEME-MISS', 'AUTO-BOTH', 'AUTO-MISS', 'DS-LOW', 'LOG-ERR'
$top = $probs | Sort-Object { $i = [array]::IndexOf($order, $_.Code); if ($i -lt 0) { if ($_.Code -like 'IMPORT-*') { 16.5 } else { 99 } } else { $i } } | Select-Object -First 1
if ($top) { $sentence = "卡在：$($top.Where)；原因：$($top.Why)；代码 $($top.Code)"; $code = $top.Code }
else { $sentence = '卡在：没查出毛病；原因：八项检查都正常，可能是 QQ 那边的问题；代码 OK'; $code = 'OK' }
$sentence = Hide $sentence

# ---------- 写 txt ----------
$sb = New-Object Text.StringBuilder
function W([string]$s = '') { [void]$sb.AppendLine((Hide $s)) }
function Sec([string]$t) { W ''; W "==== $t ====" }
W "大肥鱼求助信息（$($now.ToString('yyyy-MM-dd HH:mm')) 生成）"
W '已经打过码：QQ 号、群号只留头尾，密钥、口令、密码都去掉了，用户名换成了「<用户名>」。可以发给 Agent 或帮你看问题的人。'
W ''
W "一句话：$sentence"
if ($probs.Count -gt 1) { W '发现的问题（从重到轻）：'; foreach ($p in ($probs | Sort-Object { $i = [array]::IndexOf($order, $_.Code); if ($i -lt 0) { 50 } else { $i } })) { W "  $($p.Code)  $($p.Where)：$($p.Why)" } }

Sec '体检'
foreach ($c in $checks) { W ("[{0}] {1}：{2}" -f $c.State, $c.Name, $c.Text) }

Sec '电脑'
try {
    $os = Get-CimInstance Win32_OperatingSystem
    W "系统：$($os.Caption) $($os.Version)（$env:PROCESSOR_ARCHITECTURE）"
    W "这次开机：$($os.LastBootUpTime.ToString('MM-dd HH:mm'))（$(Since $os.LastBootUpTime)前）"
    W ('内存：可用 {0:N1} / 共 {1:N1} GB' -f ($os.FreePhysicalMemory / 1MB), ($os.TotalVisibleMemorySize / 1MB))
} catch {}
try { W "显卡：$((@(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name })) -join '；')" } catch {}
try { $drv = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Dir)); W ('工作目录所在盘剩 {0:N1} GB' -f ($drv.AvailableFreeSpace / 1GB)) } catch {}
W "PowerShell：$($PSVersionTable.PSVersion)"
W "工作目录：$Dir"

Sec '装了什么'
$memTxt = @{ '0' = '没开'; '1' = '本机 Ollama'; '2' = '硅基流动' }["$mem"]; if (-not $memTxt) { $memTxt = "（$mem）" }
$voiceTxt = @{ '0' = '没开'; '1' = 'Edge 免费女声'; '2' = '小米 MiMo'; '3' = '豆包'; '4' = '本机 Breeze'; '5' = 'Breeze 在线版' }["$voice"]; if (-not $voiceTxt) { $voiceTxt = "（$voice）" }
W "大脑：$(if ($cli) { '命令行装的 AstrBot' } else { 'AstrBot 启动器' })；登 QQ：$pname；记忆：$memTxt；语音：$voiceTxt；看视频：$(if ($ans['video'] -eq '1') { '开' } else { '没开' })"
W "群聊：$(if (Test-Path "$Dir\private\group.txt") { '进了群' } else { '只私聊' })"
W "private 里的文件：$((@(Get-ChildItem "$Dir\private" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })) -join '、')（只列名字，内容不写）"

Sec '版本'
$astV = Get-ChildItem "$Dir\uv\tools\astrbot\Lib\site-packages\astrbot-*.dist-info" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
W "AstrBot：$(if ($astV) { $astV.Name -replace '^astrbot-|\.dist-info$', '' } else { '（启动器装的，或者没找到）' })"
if ($proto -eq 'snowluma') {
    try { W "SnowLuma：$((Get-Content -Raw "$Dir\SnowLuma\release.json" | ConvertFrom-Json).version)" } catch { W 'SnowLuma：没找到 release.json' }
    $qqExe = Find-DesktopQQ
    if ($qqExe) {
        $qv = Get-ChildItem "$(Split-Path $qqExe)\versions" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d+\.\d+\.\d+-\d+$' } | Sort-Object { [version](($_.Name -replace '-', '.')) } | Select-Object -Last 1
        W "桌面 QQ：$(if ($qv) { $qv.Name } else { (Get-Item $qqExe).VersionInfo.ProductVersion })"
    } else { W '桌面 QQ：没找到' }
}
$shell = Get-ChildItem "$Dir\NapCat\NapCat.*.Shell" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($shell) { W "NapCat 自带 QQ：$((Get-ChildItem "$($shell.FullName)\versions" -Directory -ErrorAction SilentlyContinue | Select-Object -Last 1).Name)" }
$bz = Get-ChildItem "$Dir\breeze-tts\bin" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($bz) { W "Breeze：$($bz.Name)" }
if (Listening 11434) { try { W "Ollama：$((Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 3).version)" } catch {} }
try { $pk = Get-Content -Raw -Encoding UTF8 "$Dir\dafeiyu-config-pack\settings\pack.json" | ConvertFrom-Json; W "配置包：$(if ($pk.version) { $pk.version } else { '有（没写版本号）' })" } catch { W '配置包：工作目录里没有' }
$wbv = Select-String -Path "$PSScriptRoot\workbench\server.py" -Pattern '^VERSION = "([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($wbv) { W "工作台：$($wbv.Matches[0].Groups[1].Value)" }

Sec '端口'
$pp = @(@($port, "大脑后台"), @(6199, "大脑等 QQ 程序来连"), @(8137, "Breeze 语音"), @(11434, "Ollama 记忆"), @(5099, "SnowLuma 后台"), @(6099, "NapCat 后台"))
foreach ($x in $pp) { W ("{0,-6}{1}：{2}" -f $x[0], $x[1], $(if (Listening $x[0]) { '在听' } else { '没在听' })) }
W "6199 上的连接：$($qqConn.Count) 个"

Sec '工作目录里在跑的程序'
if ($mine) { foreach ($p in ($mine | Sort-Object CreationDate)) { W ("{0}  进程 {1}  {2} 起  {3}" -f $p.Name, $p.ProcessId, $(if ($p.CreationDate) { $p.CreationDate.ToString('MM-dd HH:mm') }), $p.ExecutablePath.Substring($Dir.Length + 1)) } }
else { W '（一个都没有）' }

Sec '开机自启（启动文件夹）'
$rel = @($lnks | Where-Object { $_ -match '大肥鱼|Ollama|(?i)breeze' })
W $(if ($rel) { $rel -join '、' } else { '（没有大肥鱼相关的）' })

Sec '补装结果'
if (-not $imports.Count) { W '（没有后台补装过）' }
foreach ($im in $imports) {
    W ("{0}：{1}（日志 {2} 更新）" -f $im.Block, $(if ($im.Result) { $im.Result } elseif ($im.Running) { '还在跑' } else { '没跑完就停了' }), $im.Time.ToString('MM-dd HH:mm'))
    foreach ($ln in @(Tail $im.File 15)) { W "    $ln" }
}

Sec "大脑 AstrBot 日志（最后 $($astLines.Count) 行）"
if ($astLog) { foreach ($ln in $astLines) { W $ln } } else { W '（没找到 astrbot\run.log；启动器装的在 AstrBot 后台「日志」里看）' }

$pLog = if ($proto -eq 'snowluma') { if ($slLog) { $slLog.FullName } } else {
    $nl = Get-ChildItem "$Dir\NapCat\NapCat.*.Shell\versions\*\resources\app\napcat\logs\*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($nl) { $nl.FullName } elseif (Test-Path "$($shell.FullName)\napcat-run.log") { "$($shell.FullName)\napcat-run.log" }
}
$pLines = @(Tail $pLog 200)
Sec "$pname 日志（最后 $($pLines.Count) 行）"
if ($pLog) { foreach ($ln in $pLines) { W $ln } } else { W '（没找到日志）' }

$desktop = [Environment]::GetFolderPath('Desktop')
$out = Join-Path $desktop ('大肥鱼求助信息-{0}.txt' -f $now.ToString('MMdd-HHmm'))
if (Test-Path $out) { $out = Join-Path $desktop ('大肥鱼求助信息-{0}.txt' -f $now.ToString('MMdd-HHmmss')) }
[IO.File]::WriteAllText($out, $sb.ToString(), (New-Object Text.UTF8Encoding($true)))

$copied = $false
if (-not $NoClipboard) { try { Set-Clipboard -Value $sentence -ErrorAction Stop; $copied = $true } catch {} }
Say "存到桌面了：$(Split-Path $out -Leaf)"
Say "一句话：$sentence$(if ($copied) { '（已复制到剪贴板）' })"
Say '把这句发到评论区或群里；桌面那个 txt 发给 Agent。号码只留头尾，密钥都去掉了。'
if ($Json) { 'DIAG_JSON ' + (@{ file = $out; sentence = $sentence; code = $code; copied = $copied } | ConvertTo-Json -Compress) }
