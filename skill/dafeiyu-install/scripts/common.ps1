# 各步脚本共用的小工具，用 . "$PSScriptRoot\common.ps1" 引入。不打印任何秘密。
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ProgressPreference = 'SilentlyContinue'

# GitHub 加速前缀，按顺序试，最后直连
$script:GhProxies = @('https://gh.dpik.top/', 'https://edgeone.gh-proxy.com/', 'https://hk.gh-proxy.org/', 'https://gh-proxy.org/', '')

function Say([string]$msg) { Write-Output ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $msg) }

# 同一个文件的多个地址依次试；进度走 Host，管道里只返回成功的地址。
# ExpectedBytes 是确切大小；不知道时读响应头，MinBytes 仍会检查，防止把错误页当成文件。
function Get-File {
    param([Parameter(Mandatory)][string[]]$Urls, [Parameter(Mandatory)][string]$OutFile,
          [long]$MinBytes = 1, [string]$Sha256 = '', [long]$ExpectedBytes = 0)
    $OutFile = [IO.Path]::GetFullPath($OutFile)
    $parent = Split-Path -Parent $OutFile
    New-Item -ItemType Directory -Force $parent -ErrorAction Stop | Out-Null
    if ($Sha256 -and (Test-Path $OutFile) -and (Get-Item $OutFile).Length -ge $MinBytes -and
        (Get-FileHash $OutFile -Algorithm SHA256).Hash -eq $Sha256) { Say '文件已经下好，校验通过' | Write-Host; return $Urls[0] }
    $tag = [guid]::NewGuid().ToString('N')
    $headers = "$OutFile.$tag.headers"; $errors = "$OutFile.$tag.stderr"
    Set-KeepAwake
    function Read-DownloadTotal {
        if (-not (Test-Path $headers)) { return 0L }
        # curl 正在写响应头；允许共享读写，没写完就等下一次进度再读。
        try {
            $stream = [IO.File]::Open($headers, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $reader = New-Object IO.StreamReader($stream)
            try { $h = $reader.ReadToEnd() } finally { $reader.Dispose() }
        } catch { return 0L }
        if (-not $h.Trim()) { return 0L }
        # 只看最后一段响应，不能把重定向页面的大小当作文件大小。
        $blocks = @($h.Trim() -split '\r?\n\r?\n')
        $last = $blocks[-1]
        if ($last -match '(?im)^content-range:\s*bytes\s+(?:\d+-\d+|\*)/(\d+)') { return [long]$Matches[1] }
        if ($last -match '(?im)^HTTP/\S+\s+200\b' -and $last -match '(?im)^content-length:\s*(\d+)') { return [long]$Matches[1] }
        return 0L
    }
    function Check-DownloadSpace([long]$total) {
        if ($total -le 0) { return }
        $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($OutFile))
        if ($drive.AvailableFreeSpace -lt 2 * $total) {
            throw ('目标盘空间不够：下载和解压至少要留 {0:N1} GB，现在还剩 {1:N1} GB。腾出空间或换个盘再试。' -f (2 * $total / 1GB), ($drive.AvailableFreeSpace / 1GB))
        }
    }
    try {
        foreach ($u in $Urls) {
            $label = ([uri]$u).Host
            $total = $ExpectedBytes
            if (-not $total) {
                # HEAD 只取大小，不输出地址、响应头或 curl 的原始错误。
                & curl.exe -I -L -f -sS --connect-timeout 12 --speed-limit 30000 --speed-time 45 -D $headers -o NUL $u 2>$null
                $total = Read-DownloadTotal
            }
            Check-DownloadSpace $(if ($total -gt 0) { $total } else { $MinBytes })
            if ($Sha256 -and $total -gt 0 -and (Test-Path $OutFile) -and (Get-Item $OutFile).Length -ge $total) {
                # 开头已排除了校验正确的成品；这份已下满却校验不对，不能从文件尾一直续传。
                Say '已有文件校验不对，清掉后重新下载' | Write-Host
                Remove-Item -LiteralPath $OutFile -Force -ErrorAction Stop
            }
            Say "开始下载（走 $label，断了会接着下）" | Write-Host
            for ($attempt = 0; $attempt -lt 2; $attempt++) {
                [IO.File]::WriteAllText($headers, '')
                $args2 = @('-L', '-f', '-sS', '-C', '-', '--retry', '3', '--retry-delay', '2', '--connect-timeout', '12', '--speed-limit', '30000', '--speed-time', '45', '-D', ('"{0}"' -f $headers), '-o', ('"{0}"' -f $OutFile), ('"{0}"' -f $u))
                $p = Start-Process curl.exe -ArgumentList $args2 -PassThru -NoNewWindow -RedirectStandardError $errors
                $null = $p.Handle # PS 5.1：先保留句柄，否则 ExitCode 可能是空的
                try {
                    $lastSize = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                    $lastTime = Get-Date
                    while (-not $p.WaitForExit(15000)) {
                        $size = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                        if (-not $total) { $total = Read-DownloadTotal; Check-DownloadSpace $total }
                        $now = Get-Date; $speed = [math]::Max(0, ($size - $lastSize) / [math]::Max(1, ($now - $lastTime).TotalSeconds))
                        if ($total -gt 0) {
                            $eta = if ($speed -gt 0) { '大约还要 {0:N0} 分钟' -f [math]::Ceiling([math]::Max(0, $total - $size) / $speed / 60) } else { '这会儿没增加，正在等服务器' }
                            Say ('已下 {0:N1}/{1:N1} MB，{2}（走 {3}）' -f ($size / 1MB), ($total / 1MB), $eta, $label) | Write-Host
                        } else { Say ('已下 {0:N1} MB，当前 {1:N2} MB/s（走 {2}）' -f ($size / 1MB), ($speed / 1MB), $label) | Write-Host }
                        $lastSize = $size; $lastTime = $now
                    }
                    $p.WaitForExit(); $code = $p.ExitCode
                } finally { if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit() }; $p.Dispose() }
                if ($code -eq 33 -and $attempt -eq 0) {
                    Say '这个地址不支持续传，清掉半截文件，从头再下一次' | Write-Host
                    Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
                    continue
                }
                $size = if (Test-Path $OutFile) { (Get-Item $OutFile).Length } else { 0L }
                $ok = $code -eq 0 -and $size -ge $MinBytes -and ($ExpectedBytes -le 0 -or $size -eq $ExpectedBytes)
                if ($ok -and $Sha256) {
                    $ok = (Get-FileHash $OutFile -Algorithm SHA256).Hash -eq $Sha256
                    if (-not $ok) { Say '文件校验没对上，删掉这份，换下一个源' | Write-Host; Remove-Item -LiteralPath $OutFile -Force }
                }
                if ($ok) { Say ('下好了（走 {0}，{1:N1} MB）' -f $label, ($size / 1MB)) | Write-Host; return $u }
                # curl 成功但文件太小/大小不对，说明不是要的文件；网络中断则保留半截文件。
                if ($code -eq 0 -and (Test-Path $OutFile)) { Remove-Item -LiteralPath $OutFile -Force }
                break
            }
            Say "走 $label 没下成，换下一个（半截文件会留着续传）" | Write-Host
        }
        throw '几个下载地址都没下成。已下载的部分会保留，查好网络后重跑即可。'
    } finally {
        Set-KeepAwake -Off
        Remove-Item -LiteralPath $headers, $errors -Force -ErrorAction SilentlyContinue
    }
}

# 下载、安装大文件时不让电脑自己睡着（笔记本合盖、几分钟没动静都会睡，curl 就断了）。只管这个窗口开着的这段时间，跑完恢复。
function Set-KeepAwake([switch]$Off) {
    try {
        if (-not ('Dfy.Power' -as [type])) { Add-Type -Namespace Dfy -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);' }
        # 0x80000001 = 一直保持 + 别睡；0x80000000 = 恢复成系统自己管
        [void][Dfy.Power]::SetThreadExecutionState($(if ($Off) { [uint32]2147483648 } else { [uint32]2147483649 }))
    } catch {}
}

# 桌面 QQ（官方 NT 版）装在哪：先看卸载信息（装在别的盘也能找到），再看默认位置。找不到返回空。
function Find-DesktopQQ {
    $keys = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    $c = New-Object Collections.ArrayList
    foreach ($e in Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^(腾讯)?QQ$' }) {
        if ($e.InstallLocation) { [void]$c.Add((Join-Path "$($e.InstallLocation)".Trim('"') 'QQ.exe')) }
        if ($e.UninstallString) { [void]$c.Add((Join-Path (Split-Path ($e.UninstallString -replace '"', '')) 'QQ.exe')) }
    }
    [void]$c.Add("$env:ProgramFiles\Tencent\QQNT\QQ.exe"); [void]$c.Add("${env:ProgramFiles(x86)}\Tencent\QQNT\QQ.exe")
    $c | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

# 这台电脑上的第三方杀毒软件（人话名字，去重）。Windows 自带的 Defender 不算。NapCat 解压失败、文件被拿走时用来点名是谁。
function Get-ThirdPartyAV {
    $names = New-Object Collections.ArrayList
    foreach ($a in Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue) {
        if ("$($a.displayName)" -match 'Windows Defender|Microsoft Defender') { continue }
        # 安全中心里常有卸了没删干净的、或者装了但关着的（10-28 测试机：电脑管家程序早没了还登记着「开」，联想自带的火绒是「关」），
        # 所以要「状态是开」（productState 第 12–15 位是 1）并且程序文件还在，才算在管这台电脑
        $on = ((([int]$a.productState) -shr 12) -band 0xF) -eq 1
        $exe = [Environment]::ExpandEnvironmentVariables("$($a.pathToSignedProductExe)")
        if ($on -and $exe -and (Test-Path $exe)) { [void]$names.Add("$($a.displayName)") }
    }
    $proc = @{ '360Tray' = '360安全卫士'; '360sd' = '360杀毒'; 'HipsTray' = '火绒'; 'HipsDaemon' = '火绒'; 'QQPCTray' = '腾讯电脑管家'; 'kxetray' = '金山毒霸' }
    foreach ($k in $proc.Keys) { if (Get-Process -Name $k -ErrorAction SilentlyContinue) { [void]$names.Add($proc[$k]) } }
    # 安全中心里登记的名字和进程对出来的名字可能是同一家（「火绒安全软件」和「火绒」），按前两个字去重
    $out = New-Object Collections.ArrayList
    foreach ($n in $names) { if (-not ($out | Where-Object { $_.Substring(0, [math]::Min(2, $_.Length)) -eq $n.Substring(0, [math]::Min(2, $n.Length)) })) { [void]$out.Add($n) } }
    return ,@($out)
}

function Get-GitHubFile([string]$Url, [string]$OutFile, [long]$MinBytes = 1) {
    $urls = @($script:GhProxies | ForEach-Object { "$_$Url" })
    $used = Get-File -Urls $urls -OutFile $OutFile -MinBytes $MinBytes
    foreach ($prefix in $script:GhProxies) { if ($used -eq "$prefix$Url") { return $prefix } }
}

# 大文件开下前给每个地址试下 6 秒、量速度，按快慢排好返回（同一个加速站不同时候能差好几倍）。
# 能「接着下」的（回 206）排在前面，断了重跑不用从头来；一个都量不出来就照原顺序。
function Sort-BySpeed([string[]]$Urls) {
    $r = foreach ($u in $Urls) {
        $w = "$(& curl.exe -L -s -r 0-8388607 --connect-timeout 5 --max-time 6 -o NUL -w '%{http_code} %{speed_download}' $u 2>$null)"
        $code, $speed = $w -split ' '
        [pscustomobject]@{ Url = $u; Code = $code; Speed = if ($code -in '200', '206') { [double]$speed } else { 0 } }
    }
    $ok = @($r | Where-Object Speed -gt 0 | Sort-Object @{ e = { $_.Code -eq '206' }; Descending = $true }, @{ e = 'Speed'; Descending = $true })
    if (-not $ok) { return $Urls }
    # 用 Write-Host 不用 Say：Say 的输出会混进返回值，被当成下载地址
    Write-Host ('[{0}] 各地址测速：' -f (Get-Date -Format 'HH:mm:ss') + (($r | ForEach-Object { '{0} {1}' -f ($_.Url -replace '^https://([^/]+)/.*', '$1'), $(if ($_.Speed) { '{0:N1} MB/s' -f ($_.Speed / 1MB) } else { '不通' }) }) -join '，'))
    return @($ok.Url) + @($r | Where-Object Speed -eq 0 | ForEach-Object Url)
}

function New-RandomSecret([int]$Length = 16, [switch]$Mixed) {
    if ($Mixed) {
        # 后台密码规则：要有大写字母
        $c = @((65..90 | Get-Random -Count 4) + (97..122 | Get-Random -Count ($Length - 8)) + (50..57 | Get-Random -Count 4))
        return -join ($c | Sort-Object { Get-Random } | ForEach-Object { [char]$_ })
    }
    return -join ((48..57) + (97..122) | Get-Random -Count $Length | ForEach-Object { [char]$_ })
}

function Read-KeyValueFile([string]$Path) {
    $d = @{}
    foreach ($line in Get-Content -Encoding UTF8 $Path) {
        $k, $v = ($line -replace '：', ':') -split ':\s*', 2
        if ($v) { $d[$k.Trim().ToLower()] = $v.Trim() }
    }
    return $d
}

# 在当前登录的桌面会话里起一个独立进程（Agent 的命令结束也不会被带走）。
# 用计划任务在桌面会话里起后台程序（关掉这个窗口也不会跟着关）。
# 同名任务还在跑时 schtasks /run 会被悄悄忽略、照样返回成功（10-28 实测：换目录重装，新的没起来，脚本却说启动了），所以起之前先看、起之后核对
function Get-CmdRunning([string]$CmdFile) {
    @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue | Where-Object { "$($_.CommandLine)".IndexOf($CmdFile, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
}
function Start-Detached([string]$Name, [string]$CmdFile) {
    $tn = "dafeiyu-$Name"
    # 刚停掉的进程，外面那层 cmd 可能还没退：等它几秒
    for ($i = 0; $i -lt 5 -and (Get-CmdRunning $CmdFile); $i++) { Start-Sleep 1 }
    if (Get-CmdRunning $CmdFile) { Say "$Name 已经在跑了"; return }
    $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
    if ($t -and $t.State -eq 'Running') {
        # 跑着的是别的目录那份（比如换了盘重装）：先停掉，不然两份抢同一个端口，新的也起不来
        $old = if ("$($t.Actions[0].Arguments)" -match '([A-Za-z]:\\[^"]+?\.cmd)') { $Matches[1] } else { '' }
        Say "另一份 $Name 还开着$(if ($old) { "（$old）" })，先把它停掉"
        if ($old) { Get-CmdRunning $old | ForEach-Object { & taskkill.exe /pid $_.ProcessId /t /f 2>&1 | Out-Null } }
        & schtasks.exe /end /tn $tn 2>&1 | Out-Null
        for ($i = 0; $i -lt 10 -and (Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue).State -eq 'Running'; $i++) { Start-Sleep 1 }
    }
    $t0 = Get-Date
    # 套一层 conhost --headless：直接跑 cmd 会被系统默认终端（Windows Terminal）接管，弹一个大黑窗口盖在最上面，
    # 录屏挡画面，他顺手一关她就下线（10-28 桌面版实测）。这样不出窗口，看状态用工作台；开机自启那条照旧是最小化窗口
    & schtasks.exe /create /tn $tn /tr "conhost.exe --headless cmd.exe /c `"$CmdFile`"" /sc once /st 00:00 /it /f 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & schtasks.exe /run /tn $tn 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            # 0x800710E0（2147946720）＝任务拒绝启动；没拿到结果也按没起来算
            for ($i = 0; $i -lt 10; $i++) {
                $info = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction SilentlyContinue
                if ($info -and $info.LastRunTime -ge $t0.AddSeconds(-2) -and $info.LastTaskResult -ne 2147946720) { Say "已在后台启动 $Name"; return }
                Start-Sleep 1
            }
        }
    }
    Start-Process cmd.exe -ArgumentList '/c', "`"$CmdFile`"" -WindowStyle Minimized
    Say "已启动 $Name（最小化窗口，别关）"
}

function Wait-Port([int]$Port, [int]$Seconds = 120, [string]$State = 'Listen') {
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt $Seconds) {
        if ($State -eq 'Listen') { if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) { return $true } }
        else { if (Get-NetTCPConnection -RemotePort $Port -State Established -ErrorAction SilentlyContinue) { return $true } }
        Start-Sleep 2
    }
    return $false
}

function Stop-UnderDir([string]$Dir) {
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# 跑本 Skill 那几个 .py（只用标准库）要的 Python：命令行装的用工作目录里的；启动器装的（Vol.04 视频的装法）用实例自带的
function Find-AstrPython([string]$Dir) {
    $cli = "$Dir\uv\tools\astrbot\Scripts\python.exe"
    if (Test-Path $cli) { return $cli }
    $roots = @()
    try { $d = (Get-Content -Raw "$env:APPDATA\com.github.raven95676.astrbot-launcher\.data-dir.json" | ConvertFrom-Json).data_dir; if ($d) { $roots += $d } } catch {}
    $roots += "$env:USERPROFILE\.astrbot_launcher"
    $want = Get-BackendPort $Dir
    $cands = foreach ($r in $roots) { Get-ChildItem "$r\instances" -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path "$($_.FullName)\venv\Scripts\python.exe" } }
    foreach ($c in $cands) {
        try { if ((Get-Content -Raw -Encoding UTF8 "$($c.FullName)\core\data\cmd_config.json" | ConvertFrom-Json).dashboard.port -eq $want) { return "$($c.FullName)\venv\Scripts\python.exe" } } catch {}
    }
    if ($cands) { return "$(@($cands)[0].FullName)\venv\Scripts\python.exe" }
    return ''
}

# 后台端口：backend.txt 的 url 里写的（启动器常另给端口），没有就 6185
function Get-BackendPort([string]$Dir) {
    $f = "$Dir\private\backend.txt"
    if (Test-Path $f) { $u = (Read-KeyValueFile $f)['url']; if ($u -match ':(\d+)') { return [int]$Matches[1] } }
    return 6185
}

# SnowLuma 的 OneBot 配置。全局的 config\onebot.json 什么都不连（-Token 留空）：SnowLuma 会挂上这个 Windows 用户打开的每一个 QQ，
# 每个号第一次登录时照全局配置生成自己那份，全局不连，大号就算也在这台电脑的桌面 QQ 上登着，也接不到机器人。
# 只有认出来的小号那份 onebot_<小号>.json 加一个反向 WS 连 AstrBot。都不开对外的 HTTP/WS 服务。
function Set-SnowLumaOneBot([string]$Path, [string]$Token = '') {
    $o = if (Test-Path $Path) { try { Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json } catch { $null } } else { $null }
    if (-not $o) { $o = [pscustomobject]@{} }
    if (-not $o.networks) { $o | Add-Member -Force networks ([pscustomobject]@{}) }
    foreach ($k in 'httpServers', 'httpClients', 'wsServers') { $o.networks | Add-Member -Force $k (New-Object Collections.ArrayList) }
    # 用 ArrayList：PowerShell 5.1 会把只有一项的数组拆成单个对象写进 JSON，SnowLuma 就当没有连接（10-28 踩过）
    $ws = New-Object Collections.ArrayList
    if ($Token) { [void]$ws.Add([ordered]@{ name = 'astrbot'; enabled = $true; url = 'ws://127.0.0.1:6199/ws'; role = 'Universal'; accessToken = $Token; messageFormat = 'array'; reportSelfMessage = $false; reconnectIntervalMs = 5000 }) }
    $o.networks | Add-Member -Force wsClients $ws
    if ($null -eq $o.musicSignUrl) { $o | Add-Member -Force musicSignUrl '' }
    [IO.File]::WriteAllText($Path, ($o | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
}
