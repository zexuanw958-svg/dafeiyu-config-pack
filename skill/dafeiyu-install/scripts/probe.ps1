# 大肥鱼安装前体检（只读）。不装、不改、不删任何东西，也不打印任何 key、token、密码；QQ 号只打码显示。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File probe.ps1 [-NoNetwork] [-Exclude 'D:\','E:\某目录']
# -Exclude 只给我们自己测试用：多账户共用的盘上有别人的东西时，把它当成不存在。
# 输出三段：===JSON=== 给 Agent 读；===摘要=== 是人话，给 Agent 转述；===END===。
param([switch]$NoNetwork, [string[]]$Exclude = @())
# -File 方式传参时引号不会被去掉（'D:\' 会原样进来），这里去掉
$Exclude = @($Exclude | ForEach-Object { "$_".Trim("'", '"', ' ') } | Where-Object { $_ })
# 录屏用：测试账户设了用户环境变量 DAFEIYU_PROBE_EXCLUDE（分号隔开）时照它排除，输出里不列出来（画面上看不出）
$excludeQuiet = $false
if (-not $Exclude.Count -and $env:DAFEIYU_PROBE_EXCLUDE) { $Exclude = @($env:DAFEIYU_PROBE_EXCLUDE -split ';' | Where-Object { $_ }); $excludeQuiet = $true }

$ErrorActionPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

function Mask([string]$s) {
    if (-not $s) { return '' }
    return [regex]::Replace($s, '\d{5,}', { param($m) $v = $m.Value; $v.Substring(0, 2) + '***' + $v.Substring($v.Length - 2) })
}
function NonAscii([string]$s) { return [bool]($s -match '[^\x21-\x7E]') }
function ReadJson([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try { return (Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json) } catch { return $null }
}
function HasValue($v) {
    if ($null -eq $v) { return $false }
    if ($v -is [array]) { return [bool](($v | Where-Object { "$_".Trim() }) | Select-Object -First 1) }
    return [bool]("$v".Trim())
}
function GB($bytes) { return [math]::Round([double]$bytes / 1GB, 1) }
function Excluded([string]$path) {
    if (-not $path) { return $false }
    foreach ($e in $Exclude) { if ($e -and $path.StartsWith($e, [StringComparison]::OrdinalIgnoreCase)) { return $true } }
    return $false
}
# 进程归属：同一台电脑上别的 Windows 账户开的东西不算这个人的（端口照样算占用）
$me = $env:USERNAME
$ownerCache = @{}
function OwnerOf($procId) {
    if (-not $ownerCache.ContainsKey($procId)) {
        $cp = Get-CimInstance Win32_Process -Filter "ProcessId=$procId"
        $u = ''; if ($cp) { $u = (Invoke-CimMethod -InputObject $cp -MethodName GetOwner).User }
        $ownerCache[$procId] = "$u"
    }
    return $ownerCache[$procId]
}
function IsMine($procId) { $o = OwnerOf $procId; return (-not $o) -or ($o -eq $me) }
# 文件夹归属和最近一次用：别的 Windows 账户的、最近几天还在动的，第 1 步别叫他清（10-28 桌面版 DSH 把另一个账户的 NapCat 说成「没用的」）
function DirOwner([string]$d) { try { "$((Get-Acl -LiteralPath $d).Owner)" } catch { '' } }
function OtherUserOwns([string]$d) {
    $o = DirOwner $d
    return [bool]($o -and $o -notmatch '^(BUILTIN|NT AUTHORITY|NT SERVICE)\\' -and ($o -split '\\')[-1] -ne $me)
}
function NewestWrite([string[]]$paths) {
    $t = $paths | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | ForEach-Object { Get-Item -LiteralPath $_ -Force; Get-ChildItem -LiteralPath $_ -File -Force } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($t) { $t.LastWriteTime } else { $null }
}
function DaysSinceUsed([string[]]$paths) { $w = NewestWrite $paths; if ($w) { [int][math]::Floor(((Get-Date) - $w).TotalDays) } else { $null } }
function MinutesSinceWrite([string[]]$paths) { $w = NewestWrite $paths; if ($w) { [int]((Get-Date) - $w).TotalMinutes } else { $null } }

$warnings = New-Object Collections.Generic.List[string]

# Agent 的沙盒里（比如 DSH 默认的「工作区可写」）WMI 会被拒，HTTPS 也用不了（curl 报 SEC_E_NO_CREDENTIALS）。
# 这时系统、磁盘、显卡、进程、端口改用不靠 WMI 的办法读，网络只测 TCP 能不能连上，JSON 里 machine.sandboxed 记一笔。
$wmiOk = [bool](Get-CimInstance Win32_OperatingSystem)
if ($wmiOk) { $allProcs = @(Get-CimInstance Win32_Process | Select-Object ProcessId, Name, ExecutablePath) }
else { $allProcs = @(Get-Process | ForEach-Object { [pscustomobject]@{ ProcessId = $_.Id; Name = "$($_.ProcessName).exe"; ExecutablePath = $_.Path } }) }
$tcp = @(Get-NetTCPConnection | Select-Object LocalAddress, LocalPort, RemotePort, State, OwningProcess)
if (-not $tcp.Count) {
    foreach ($l in (netstat -ano)) {
        if ($l -match '^\s*TCP\s+(\S+):(\d+)\s+\S+:(\d+)\s+(\S+)\s+(\d+)\s*$') {
            $st = switch ($Matches[4]) { 'LISTENING' { 'Listen' } 'ESTABLISHED' { 'Established' } default { $Matches[4] } }
            $tcp += [pscustomobject]@{ LocalAddress = $Matches[1].Trim('[', ']'); LocalPort = [int]$Matches[2]; RemotePort = [int]$Matches[3]; State = $st; OwningProcess = [int]$Matches[5] }
        }
    }
}

# ---------- 1. 机器底子 ----------
$os = Get-CimInstance Win32_OperatingSystem
if (-not $os) {
    $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $b = 0; [int]::TryParse("$($cv.CurrentBuild)", [ref]$b) | Out-Null
    # 注册表里 Win11 的 ProductName 还写着 Windows 10，按版本号改过来
    $os = [pscustomobject]@{ Caption = $(if ($b -ge 22000) { "$($cv.ProductName)" -replace 'Windows 10', 'Windows 11' } else { "$($cv.ProductName)" }); BuildNumber = $b }
}
$build = 0; [int]::TryParse("$($os.BuildNumber)", [ref]$build) | Out-Null
# 沙盒里 WMI 读不到内存，换 .NET 自带的读法（10-28 桌面版 DSH 体检报「内存 0 GB」）
$ramBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
if (-not $ramBytes) { try { Add-Type -AssemblyName Microsoft.VisualBasic; $ramBytes = (New-Object Microsoft.VisualBasic.Devices.ComputerInfo).TotalPhysicalMemory } catch {} }
$desktop = [Environment]::GetFolderPath('Desktop')
# 本 Skill 装过的话，install-astrbot.ps1 把观众挑的工作目录记在这里（可能在任何盘、任何文件夹名）
$workDir = ''
$wdFile = "$env:LOCALAPPDATA\dafeiyu\workdir.txt"
if (Test-Path -LiteralPath $wdFile) { $workDir = "$(Get-Content -Raw -LiteralPath $wdFile)".Trim(); if (-not (Test-Path -LiteralPath $workDir) -or (Excluded $workDir)) { $workDir = '' } }
$machine = [ordered]@{
    os           = "$($os.Caption)"
    build        = $build
    win11        = ($build -ge 22000)
    user         = $env:USERNAME
    profile      = $env:USERPROFILE
    profileNonAscii = (NonAscii $env:USERPROFILE)
    desktop      = $desktop
    psVersion    = "$($PSVersionTable.PSVersion)"
    isAdmin      = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    sandboxed    = (-not $wmiOk)
    arch         = "$env:PROCESSOR_ARCHITECTURE"
    ramGB        = $(if ($ramBytes) { GB $ramBytes } else { 0 })
    domainJoined = $(try { [bool](Get-CimInstance Win32_ComputerSystem).PartOfDomain } catch { $false })
    policyLocked = $(try { [bool](Get-ExecutionPolicy -List | Where-Object { $_.Scope -in 'MachinePolicy', 'UserPolicy' -and "$($_.ExecutionPolicy)" -notin 'Undefined', 'Bypass', 'Unrestricted' }) } catch { $false })
}

$drives = @()
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
    $drives += [ordered]@{ drive = $_.DeviceID; freeGB = (GB $_.FreeSpace); sizeGB = (GB $_.Size) }
}
if (-not $drives.Count) {
    [IO.DriveInfo]::GetDrives() | Where-Object { "$($_.DriveType)" -eq 'Fixed' -and $_.IsReady } | ForEach-Object {
        $drives += [ordered]@{ drive = $_.Name.TrimEnd('\'); freeGB = (GB $_.AvailableFreeSpace); sizeGB = (GB $_.TotalSize) }
    }
}

# 显卡：先看 nvidia-smi（N 卡显存最准），再看驱动注册表里记的显存（A 卡、Intel 也有）
$gpus = @()
$regMem = @{}
$regNames = @()
Get-ChildItem 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath
    $name = $p.DriverDesc
    $mem = $p.'HardwareInformation.qwMemorySize'
    if (-not $mem) { $mem = $p.'HardwareInformation.MemorySize'; if ($mem -is [byte[]]) { $mem = [BitConverter]::ToUInt32($mem, 0) } }
    if ($name -and $mem) { $regMem[$name] = [double]$mem }
    if ($name) { $regNames += "$name" }
}
$nvidia = @{}
$smi = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
foreach ($line in @($smi)) { if ($line -match '^(.+?),\s*(\d+)') { $nvidia[$Matches[1].Trim()] = [double]$Matches[2] * 1MB } }
$vc = @(Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM)
if (-not $vc.Count) { $vc = @($regNames | Select-Object -Unique | ForEach-Object { [pscustomobject]@{ Name = $_; AdapterRAM = 0 } }) }
$vc | ForEach-Object {
    $n = "$($_.Name)"
    $vram = 0
    foreach ($k in $nvidia.Keys) { if ($n -like "*$k*" -or $k -like "*$n*") { $vram = $nvidia[$k] } }
    if (-not $vram -and $regMem.ContainsKey($n)) { $vram = $regMem[$n] }
    if (-not $vram) { $vram = [double]$_.AdapterRAM }
    $vendor = 'other'
    if ($n -match 'NVIDIA|GeForce|RTX|GTX|Quadro') { $vendor = 'nvidia' } elseif ($n -match 'AMD|Radeon') { $vendor = 'amd' } elseif ($n -match 'Intel') { $vendor = 'intel' }
    $integrated = ($vendor -eq 'intel' -and $n -notmatch 'Arc A\d') -or ($n -match 'Radeon\(TM\) Graphics|Vega \d+ Graphics|780M|680M|660M')
    if ($n -notmatch 'Virtual|Remote|Basic Display|Meta|Parsec|Mirage|IddCx') {
        $gpus += [ordered]@{ name = $n; vendor = $vendor; vramGB = (GB $vram); integrated = [bool]$integrated }
    }
}
$bestGpu = $gpus | Where-Object { -not $_.integrated } | Sort-Object { $_.vramGB } -Descending | Select-Object -First 1
# Breeze 合不合适：显存 ≥6 GB 大模型、3–6 GB 小模型（飞书 8.6 的口径），没有独显就是 slow（一句十几秒）。
# 只有 N 卡（4060 8 GB）实测过，A 卡、Intel 独显标 untested。
$breeze = 'slow'
if ($bestGpu -and $bestGpu.vramGB -ge 3) {
    $size = $(if ($bestGpu.vramGB -ge 6) { 'large' } else { 'small' })
    $breeze = $(if ($bestGpu.vendor -eq 'nvidia') { "nvidia-$size" } else { "untested-$size" })
}

$proxy = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
$av = @()
# 只算「状态是开、程序文件还在」的（安全中心里常有卸了没删干净、或者装了但关着的）
Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | ForEach-Object {
    $exe = [Environment]::ExpandEnvironmentVariables("$($_.pathToSignedProductExe)")
    if ("$($_.displayName)" -match 'Windows Defender|Microsoft Defender' -or (((([int]$_.productState) -shr 12) -band 0xF) -eq 1 -and $exe -and (Test-Path $exe))) { $av += "$($_.displayName)" }
}
foreach ($pn in '360Tray', '360sd', 'HipsTray', 'HipsDaemon', 'QQPCTray', 'kxetray') { if (Get-Process -Name $pn) { $av += "进程:$pn" } }

$cmds = [ordered]@{}
foreach ($c in 'curl.exe', 'python', 'node', 'git', 'docker', 'ollama', 'nvidia-smi', 'ffmpeg') {
    $g = Get-Command $c -All | Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    $cmds[$c] = [bool]$g
}
# ffmpeg 不一定在 PATH 里：install-ffmpeg.ps1 装在工作目录，winget 装的在 WinGet\Links
if (-not $cmds['ffmpeg']) { $cmds['ffmpeg'] = [bool](@($(if ($workDir) { "$workDir\ffmpeg\bin\ffmpeg.exe" }), 'C:\dafeiyu\ffmpeg\bin\ffmpeg.exe', 'D:\dafeiyu\ffmpeg\bin\ffmpeg.exe', "$env:LOCALAPPDATA\Microsoft\WinGet\Links\ffmpeg.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1) }
$cmds['pythonIsStoreStub'] = [bool]((Get-Command python -All | Select-Object -First 1).Source -match '\\WindowsApps\\')

$net = [ordered]@{}
$netVia = 'https'
if (-not $NoNetwork) {
    foreach ($u in 'https://github.com', 'https://gh-proxy.com', 'https://hf-mirror.com', 'https://api.deepseek.com') {
        $t0 = Get-Date
        $ok = $false
        $err = ''
        # 用 curl 测，跟安装脚本下载走同一条路；HEAD 返回 4xx 也算通
        if ($cmds['curl.exe']) { $err = "$(& curl.exe -s -S -I -o NUL --max-time 6 $u 2>&1)"; $ok = ($LASTEXITCODE -eq 0) }
        else { try { $r = Invoke-WebRequest -Uri $u -Method Head -UseBasicParsing -TimeoutSec 6; $ok = $true } catch { if ($_.Exception.Response) { $ok = $true }; $err = "$($_.Exception.Message)" } }
        if (-not $ok -and ((-not $wmiOk) -or $err -match 'SEC_E_NO_CREDENTIALS|AcquireCredentialsHandle')) {
            # 沙盒里 HTTPS 用不了：退一步只测 443 端口连不连得上
            $netVia = 'tcp'
            $c = New-Object Net.Sockets.TcpClient
            try { $ok = $c.ConnectAsync(([Uri]$u).Host, 443).Wait(4000) -and $c.Connected } catch { $ok = $false } finally { $c.Close() }
        }
        $net[$u] = [ordered]@{ ok = [bool]$ok; ms = [int]((Get-Date) - $t0).TotalMilliseconds }
    }
}

# 网速：三处各取前 2 MB、每处最多 5 秒，一共不超过 15 秒。GitHub（经加速站，取两个里快的）、hf-mirror（语音、识别模型）、国内镜像。
# 只用来定「拿不准就选哪个」和预计时间，不改任何设置。沙盒里 HTTPS 用不了，就不测。
$speed = [ordered]@{ github = $null; hf = $null; cn = $null; tier = 'unknown' }
if (-not $NoNetwork -and $netVia -eq 'https' -and $cmds['curl.exe']) {
    function MBps([string]$u) {
        $w = "$(& curl.exe -L -s -r 0-2097151 --connect-timeout 4 --max-time 5 -o NUL -w '%{http_code} %{speed_download} %{size_download}' $u 2>$null)"
        $code, $sp, $sz = $w -split ' '
        if ($code -in '200', '206' -and [double]$sz -gt 100KB) { return [math]::Round([double]$sp / 1MB, 2) } else { return 0 }
    }
    $gh = 'https://github.com/SnowLuma/SnowLuma/releases/download/v2.2.1/SnowLuma-v2.2.1-win-x64.zip'
    $speed.github = [math]::Max((MBps "https://gh.dpik.top/$gh"), (MBps "https://edgeone.gh-proxy.com/$gh"))
    $speed.hf = MBps 'https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/resolve/main/model.int8.onnx'
    $speed.cn = MBps 'https://mirrors.aliyun.com/pypi/packages/2c/c6/fa760e12a2483469e2bf5058c5faff664acf66cadb4df2ad6205b016a73d/imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl'
    # 大件（语音模型、Ollama、表情包）都在境外，按境外两处里快的那个分档
    $abroad = [math]::Max([double]$speed.github, [double]$speed.hf)
    $speed.tier = if ($abroad -ge 3) { 'fast' } elseif ($abroad -ge 1) { 'mid' } elseif ($abroad -gt 0) { 'slow' } else { 'none' }
}

# ---------- 2. 端口和进程 ----------
$watchPorts = 6185, 6186, 6199, 6099, 11434, 8137, 5099, 3000, 3001, 3100
$ports = [ordered]@{}
$tcp | Where-Object { "$($_.State)" -eq 'Listen' } | Where-Object { $_.LocalPort -in $watchPorts -or ($_.LocalPort -ge 18600 -and $_.LocalPort -le 18620) } | ForEach-Object {
    $pr = Get-Process -Id $_.OwningProcess
    $ports["$($_.LocalPort)"] = [ordered]@{ process = "$($pr.ProcessName)"; path = "$($pr.Path)"; addr = "$($_.LocalAddress)"; mine = (IsMine $_.OwningProcess) }
}
$established6199 = @($tcp | Where-Object { "$($_.State)" -eq 'Established' } | Where-Object { $_.LocalPort -eq 6199 -or $_.RemotePort -eq 6199 }).Count -gt 0

$uninst = @()
foreach ($k in 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*') {
    Get-ItemProperty $k | Where-Object { $_.DisplayName } | ForEach-Object { $uninst += $_ }
}
function Installed([string]$pattern) {
    $x = $uninst | Where-Object { $_.DisplayName -match $pattern } | Select-Object -First 1
    if ($x) { return [ordered]@{ name = "$($x.DisplayName)"; version = "$($x.DisplayVersion)"; location = "$($x.InstallLocation)".Trim('"') } }
    return $null
}

# 桌面 QQ（不在 NapCat、SnowLuma 文件夹里的 QQ.exe）
$qqProcs = @($allProcs | Where-Object { $_.Name -eq 'QQ.exe' } | Where-Object { $_.ExecutablePath -and (IsMine $_.ProcessId) })
$desktopQQRunning = [bool]($qqProcs | Where-Object { $_.ExecutablePath -notmatch 'napcat|snowluma' } | Select-Object -First 1)
$napcatQQRunning = [bool]($qqProcs | Where-Object { $_.ExecutablePath -match 'napcat' } | Select-Object -First 1)
$qqCand = @(foreach ($e in $uninst | Where-Object { $_.DisplayName -match '^(腾讯)?QQ$' }) {
    if ($e.InstallLocation) { Join-Path "$($e.InstallLocation)".Trim('"') 'QQ.exe' }
    if ($e.UninstallString) { Join-Path (Split-Path ("$($e.UninstallString)" -replace '"', '')) 'QQ.exe' }
}) + @("$env:ProgramFiles\Tencent\QQNT\QQ.exe", "${env:ProgramFiles(x86)}\Tencent\QQNT\QQ.exe")
$desktopQQExe = $qqCand | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
$desktopQQVer = ''
if ($desktopQQExe) { $desktopQQVer = (Get-ChildItem -LiteralPath (Join-Path (Split-Path $desktopQQExe) 'versions') -Directory | Where-Object Name -match '^\d+\.\d+\.\d+-\d+$' | Sort-Object { [version]($_.Name -replace '-', '.') } -Descending | Select-Object -First 1).Name }

# ---------- 3. 找候选文件夹（用户目录 + 各盘根目录下名字像的文件夹，往下最多 3 层）----------
$kw = 'napcat|astrbot|qq|bot|大肥鱼|dafeiyu|snowluma|bridge|maibot|lagrange|llonebot|lucky|companion|鱼|dsh'
$roots = New-Object Collections.Generic.List[string]
foreach ($d in $desktop, "$env:USERPROFILE\Downloads", [Environment]::GetFolderPath('MyDocuments')) { if ($d -and (Test-Path -LiteralPath $d)) { $roots.Add($d) } }
foreach ($dr in $drives) {
    Get-ChildItem -LiteralPath "$($dr.drive)\" -Directory -Force | Where-Object { $_.Name -match $kw -and $_.Name -notmatch '^\$' } | ForEach-Object { $roots.Add($_.FullName) }
}
Get-ChildItem -LiteralPath $env:USERPROFILE -Directory -Force | Where-Object { $_.Name -match $kw } | ForEach-Object { $roots.Add($_.FullName) }
$roots = New-Object Collections.Generic.List[string] -ArgumentList (,[string[]]@($roots | Where-Object { -not (Excluded $_) }))
$wanted = 'NapCatInstaller.exe', 'napcat.bat', 'NapCatWinBootMain.exe', 'cmd_config.json', 'launcher.bat', 'package.json', 'start.bat', 'Lagrange.OneBot.exe', 'LLOneBot.exe', 'LuckyLilliaBot.exe', 'bot.py'
$hits = @()
foreach ($r in ($roots | Select-Object -Unique)) {
    $hits += @(Get-ChildItem -LiteralPath $r -Recurse -Depth 3 -File -Force | Where-Object { $_.Name -in $wanted -and $_.FullName -notmatch '\\node_modules\\|\\site-packages\\|\\\.astrbot_launcher\\|\\backups?\\|\.bak' })
}

# ---------- 4. AstrBot（启动器） ----------
$launcher = Installed 'AstrBot Launcher'
$dataDirs = New-Object Collections.Generic.List[string]
$override = ReadJson "$env:APPDATA\com.github.raven95676.astrbot-launcher\.data-dir.json"
$dataDirOverridden = $false
if ($override -and $override.data_dir) { $dataDirs.Add("$($override.data_dir)"); $dataDirOverridden = $true }
$dataDirs.Add("$env:USERPROFILE\.astrbot_launcher")

$packPlugins = 'astrbot_plugin_group_chat_plus', 'astrbot_plugin_livingmemory', 'meme_manager', 'astrbot_plugin_qzone', 'astrbot_plugin_qqprofile', 'astrbot_plugin_proactive_chat'
$astrTokens = New-Object Collections.Generic.List[string]   # 只在内存里比对，不输出

function Inspect-AstrBot([string]$dataRoot, [string]$kind, [string]$instDir) {
    # $dataRoot 是含 cmd_config.json 的 data 目录
    $cfg = ReadJson (Join-Path $dataRoot 'cmd_config.json')
    $o = [ordered]@{ kind = $kind; dir = $instDir; hasConfig = [bool]$cfg }
    $o.otherUserOwns = $(if ($instDir) { OtherUserOwns $instDir } else { $false })
    $usedPaths = @($(if ($instDir) { $instDir; Join-Path $instDir 'astrbot' }), $dataRoot, (Join-Path $dataRoot 'logs'))
    $o.daysSinceUsed = DaysSinceUsed $usedPaths
    $o.minutesSinceWrite = MinutesSinceWrite $usedPaths
    if ($instDir) {
        $o.hasVenv = Test-Path -LiteralPath (Join-Path $instDir 'venv\Scripts\python.exe')
        $pp = Join-Path $instDir 'core\pyproject.toml'
        $v = Select-String -LiteralPath $pp -Pattern '^version\s*=\s*"(.+)"' | Select-Object -First 1
        $o.version = $(if ($v) { $v.Matches[0].Groups[1].Value } else { '' })
        # 命令行装的（本 Skill 的路线）：<工作目录>\uv\tools\astrbot 里的 dist-info 带版本号
        $uvTool = Join-Path (Split-Path $instDir) 'uv\tools\astrbot'
        if (Test-Path -LiteralPath $uvTool) {
            $o.route = 'cli'
            $di = Get-ChildItem -LiteralPath (Join-Path $uvTool 'Lib\site-packages') -Directory -Filter 'astrbot-*.dist-info' | Select-Object -First 1
            if ($di -and -not $o.version) { $o.version = ($di.Name -replace '^astrbot-', '' -replace '\.dist-info$', '') }
        }
    }
    $listen = @()
    foreach ($p in $ports.Keys) {
        $pp = $ports[$p].path
        if ($ports[$p].mine -and $ports[$p].process -match '^python|astrbot' -and $pp -and $instDir -and $pp.StartsWith($instDir, [StringComparison]::OrdinalIgnoreCase)) { $listen += [int]$p }
        # 命令行装的：进程在 <工作目录>\uv 里
        elseif ($o.route -eq 'cli' -and $ports[$p].mine -and $pp -and $pp.StartsWith((Join-Path (Split-Path $instDir) 'uv\'), [StringComparison]::OrdinalIgnoreCase)) { $listen += [int]$p }
    }
    # 手动装的常用系统 Python 跑，进程路径不在实例目录里：看配置里写的后台端口在不在听。
    # 但端口要是被别的装法（uv 装的、启动器的）占着，就不算它在跑
    if (-not $listen.Count -and $kind -eq 'manual' -and $o.route -ne 'cli' -and $cfg -and $cfg.dashboard.port -and $ports.Contains("$($cfg.dashboard.port)") -and $ports["$($cfg.dashboard.port)"].mine) {
        $pp = "$($ports["$($cfg.dashboard.port)"].path)"
        $other = $pp -match '\\uv\\(tools|python)\\'
        foreach ($dd in $dataDirs) { if ($dd -and $pp.StartsWith($dd, [StringComparison]::OrdinalIgnoreCase)) { $other = $true } }
        if (-not $other) { $listen += [int]$cfg.dashboard.port }
    }
    # 沙盒里认不出端口归哪个程序（path 为空）：配置里写的后台端口在听，就算它在跑
    if (-not $listen.Count -and $cfg -and $cfg.dashboard.port -and $ports.Contains("$($cfg.dashboard.port)") -and -not $ports["$($cfg.dashboard.port)"].path) { $listen += [int]$cfg.dashboard.port }
    $o.listeningPorts = $listen
    $o.running = ($listen.Count -gt 0)
    if (-not $cfg) { return $o }
    $o.dashboardHost = "$($cfg.dashboard.host)"
    $o.dashboardPortInConfig = $cfg.dashboard.port
    $plats = @()
    foreach ($pl in @($cfg.platform)) {
        if (-not $pl) { continue }
        if (HasValue $pl.ws_reverse_token) { $astrTokens.Add("$($pl.ws_reverse_token)") }
        $plats += [ordered]@{ id = (Mask "$($pl.id)"); type = "$($pl.type)"; enable = [bool]$pl.enable; wsPort = $pl.ws_reverse_port; wsHost = "$($pl.ws_reverse_host)"; hasToken = (HasValue $pl.ws_reverse_token) }
    }
    $o.platforms = $plats
    $srcs = @{}
    foreach ($s in @($cfg.provider_sources)) { if ($s) { $srcs["$($s.id)"] = $s } }
    $chat = @(); $tts = @(); $emb = @()
    foreach ($p in @($cfg.provider)) {
        if (-not $p) { continue }
        $src = $srcs["$($p.provider_source_id)"]
        $base = "$($p.api_base)"; if (-not $base -and $src) { $base = "$($src.api_base)" }
        $hasKey = (HasValue $p.key) -or ($src -and (HasValue $src.key))
        $host_ = ''; if ($base -match '^https?://([^/:]+)(:\d+)?') { $host_ = $Matches[1] + $Matches[2] }
        $item = [ordered]@{ id = "$($p.id)"; type = "$($p.type)"; model = "$($p.model)"; host = $host_; enable = [bool]$p.enable; hasKey = [bool]$hasKey }
        $ptype = "$($p.provider_type)"; if (-not $ptype -and $src) { $ptype = "$($src.provider_type)" }
        if ($ptype -eq 'text_to_speech') { $tts += $item } elseif ($ptype -eq 'embedding') { $emb += $item } elseif ($ptype -eq 'chat_completion' -or -not $ptype) { $chat += $item }
    }
    $o.chatProviders = $chat
    $o.defaultChat = "$($cfg.agent_runner.config.model.provider_id)"
    $o.deepseekReady = [bool]($chat | Where-Object { $_.host -match 'deepseek' -and $_.enable -and $_.hasKey } | Select-Object -First 1)
    $o.otherModel = [bool]($chat | Where-Object { $_.host -and $_.host -notmatch 'deepseek' -and $_.enable } | Select-Object -First 1)
    # 看图：默认对话模型的「模型能力」里有没有图片（没填就是全开）
    $dc = @($cfg.provider) | Where-Object { $_ -and "$($_.id)" -eq $o.defaultChat } | Select-Object -First 1
    if ($dc) { $mods = @($dc.modalities | Where-Object { $_ }); $o.chatSeesImages = (-not $mods.Count) -or ('image' -in $mods) }
    $o.ttsProviders = $tts
    $o.ttsEnabled = [bool]$cfg.provider_tts_settings.enable
    $o.embeddingProviders = $emb
    $o.whitelistOn = [bool]$cfg.platform_settings.enable_id_white_list
    $o.whitelistCount = @($cfg.platform_settings.id_whitelist | Where-Object { "$_".Trim() }).Count
    $o.adminCount = @($cfg.admins_id | Where-Object { "$_".Trim() }).Count
    $o.computerUse = "$($cfg.provider_settings.computer_use_runtime)"
    $plugDir = Join-Path $dataRoot 'plugins'
    $plugs = @(Get-ChildItem -LiteralPath $plugDir -Directory | ForEach-Object { $_.Name })
    $o.plugins = $plugs
    $o.packPluginsPresent = @($packPlugins | Where-Object { $_ -in $plugs }).Count
    $o.dafeiyuPlugins = @($plugs | Where-Object { $_ -match 'dafeiyu|seed_audio' }).Count
    $memeCfg = Join-Path $dataRoot 'config\meme_manager_config.json'
    if (Test-Path -LiteralPath $memeCfg) {
        $mt = Get-Content -Raw -Encoding UTF8 -LiteralPath $memeCfg
        $o.memeGroupFixed = [bool]($mt -match 'chat_and_plugin_llm')
    }
    $gcp = ReadJson (Join-Path $dataRoot 'config\astrbot_plugin_group_chat_plus_config.json')
    if ($gcp) { $o.groupImageOn = [bool]$gcp.enable_image_processing }
    $o.videoPlugin = 'astrbot_plugin_dafeiyu_video' -in $plugs
    $lm = ReadJson (Join-Path $dataRoot 'config\astrbot_plugin_livingmemory_config.json')
    if ($lm) { $o.memoryEmbedding = "$($lm.provider_settings.embedding_provider_id)" }
    if ($o.running) {
        $o.dashboardReachable = $false
        foreach ($p in $listen) {
            try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 4; if ($r.Content -match 'AstrBot') { $o.dashboardReachable = $true; $o.dashboardPort = $p } } catch {}
        }
    }
    return $o
}

$astr = @()
$components = [ordered]@{}
foreach ($dd in ($dataDirs | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $dd) -or (Excluded $dd)) { continue }
    foreach ($c in 'python', 'uv', 'nodejs') { $components[$c] = [bool](Get-ChildItem -LiteralPath (Join-Path $dd "components\$c") -Force | Select-Object -First 1) }
    Get-ChildItem -LiteralPath (Join-Path $dd 'instances') -Directory | ForEach-Object {
        $astr += (Inspect-AstrBot (Join-Path $_.FullName 'core\data') 'launcher' $_.FullName)
    }
}
# 不是启动器装的 AstrBot（源码、手动拷的 core、别的整合包）
foreach ($h in ($hits | Where-Object { $_.Name -eq 'cmd_config.json' })) {
    $dataRoot = $h.DirectoryName
    $isLauncher = $false
    foreach ($dd in $dataDirs) { if ($dataRoot.StartsWith($dd, [StringComparison]::OrdinalIgnoreCase)) { $isLauncher = $true } }
    if ($isLauncher) { continue }
    if ((Split-Path $dataRoot -Leaf) -ne 'data') { continue }
    $p1 = Split-Path $dataRoot
    $instDir = $p1                                   # ...\data 的上一级
    if ((Split-Path $p1 -Leaf) -eq 'core') { $instDir = Split-Path $p1 }   # ...\core\data → 实例根
    $astr += (Inspect-AstrBot $dataRoot 'manual' $instDir)
}

# 沙盒里认不出端口归哪个程序，配了同一个后台端口的几个实例会都算「在跑」：只留日志最近在写的那个
# （10-28 桌面版 DSH 把没开的 D:\astrbot-test 报成在跑）
if (-not $wmiOk) {
    foreach ($g in @($astr | Where-Object { $_.running -and $_.listeningPorts } | Group-Object { "$($_.listeningPorts -join ',')" } | Where-Object { $_.Count -gt 1 })) {
        $keep = $g.Group | Sort-Object { if ($null -ne $_.minutesSinceWrite) { $_.minutesSinceWrite } else { [int]::MaxValue } } | Select-Object -First 1
        foreach ($a in $g.Group) { if (-not [object]::ReferenceEquals($a, $keep)) { $a.running = $false } }
    }
}

# ---------- 5. NapCat ----------
$napBases = @($hits | Where-Object { $_.Name -in 'NapCatInstaller.exe', 'napcat.bat' } | ForEach-Object {
        $d = $_.DirectoryName
        if ((Split-Path $d -Leaf) -like 'NapCat.*.Shell') { Split-Path $d } else { $d }
    } | Select-Object -Unique)
foreach ($pr in ($allProcs | Where-Object { $_.ExecutablePath -match 'napcat' -and -not (Excluded $_.ExecutablePath) -and (IsMine $_.ProcessId) })) {
    $d = Split-Path $pr.ExecutablePath
    while ($d -and (Split-Path $d -Leaf) -notmatch '^NapCat') { $d = Split-Path $d }
    if ($d) { if ((Split-Path $d -Leaf) -like 'NapCat.*.Shell') { $d = Split-Path $d }; if ($d -notin $napBases) { $napBases += $d } }
}
# 去掉嵌套的（一键包里的 bootmain 之类子文件夹）
$napBases = @($napBases | Where-Object { $b0 = $_; -not ($napBases | Where-Object { $_ -ne $b0 -and $b0.StartsWith($_ + '\', [StringComparison]::OrdinalIgnoreCase) }) })
$napcat = @()
foreach ($b in $napBases) {
    $o = [ordered]@{ dir = $b }
    $o.hasInstaller = Test-Path -LiteralPath (Join-Path $b 'NapCatInstaller.exe')
    $zip = Get-Item -LiteralPath (Join-Path $b 'NapCat.Shell.zip')
    $o.shellZipMB = $(if ($zip) { [math]::Round($zip.Length / 1MB, 1) } else { 0 })
    $qqi = Get-Item -LiteralPath (Join-Path $b 'QQ.exe')
    $o.qqInstallerMB = $(if ($qqi) { [math]::Round($qqi.Length / 1MB, 0) } else { 0 })
    $shell = Get-ChildItem -LiteralPath $b -Directory | Where-Object { $_.Name -like 'NapCat.*.Shell' } | Select-Object -First 1
    if (-not $shell -and (Test-Path -LiteralPath (Join-Path $b 'napcat.bat'))) { $shell = Get-Item -LiteralPath $b }
    $o.hasShell = [bool]$shell
    $o.otherUserOwns = OtherUserOwns $b
    $o.daysSinceUsed = DaysSinceUsed @($b, $(if ($shell) { $shell.FullName }), $(if ($shell) { Join-Path $shell.FullName 'logs' }))
    $o.shellDirsCount = @(Get-ChildItem -LiteralPath $b -Directory | Where-Object { $_.Name -like 'NapCat.*.Shell' }).Count
    $o.hasNapcatBat = $false; $o.installComplete = $false; $o.accounts = @(); $o.wsClients = @()
    if ($shell) {
        $o.hasNapcatBat = Test-Path -LiteralPath (Join-Path $shell.FullName 'napcat.bat')
        $cfgDir = Get-ChildItem -LiteralPath (Join-Path $shell.FullName 'versions') -Directory | ForEach-Object { Join-Path $_.FullName 'resources\app\napcat\config' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        $o.qqVersion = (Get-ChildItem -LiteralPath (Join-Path $shell.FullName 'versions') -Directory | Select-Object -First 1).Name
        $o.installComplete = [bool]($o.hasNapcatBat -and $cfgDir)
        if ($cfgDir) {
            $web = ReadJson (Join-Path $cfgDir 'webui.json')
            if ($web) { $o.webuiPort = $web.port }
            foreach ($f in (Get-ChildItem -LiteralPath $cfgDir -Filter 'onebot11_*.json')) {
                $o.accounts += (Mask ($f.BaseName -replace '^onebot11_', ''))
                $j = ReadJson $f.FullName
                foreach ($w in @($j.network.websocketClients)) {
                    if (-not $w) { continue }
                    $u = "$($w.url)"
                    $o.wsClients += [ordered]@{
                        url = $u; enable = [bool]$w.enable
                        fullWidthColon = [bool]($u -match '：')
                        endsWithWs = [bool]($u -match '/ws/?$')
                        hasToken = (HasValue $w.token)
                        tokenMatchesAstrBot = [bool]((HasValue $w.token) -and ($astrTokens -contains "$($w.token)"))
                    }
                }
            }
        }
    }
    $o.running = [bool]($allProcs | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($b, [StringComparison]::OrdinalIgnoreCase) -and (IsMine $_.ProcessId) } | Select-Object -First 1)
    $o.minutesSinceWrite = MinutesSinceWrite @($b, $(if ($shell) { $shell.FullName; Join-Path $shell.FullName 'logs' }))
    # 沙盒里进程路径读不到：网页后台端口在听、又认不出归谁，先算它在跑，下面同端口的几份再按日志挑
    if (-not $wmiOk -and -not $o.running -and $o.webuiPort -and $ports.Contains("$($o.webuiPort)") -and -not $ports["$($o.webuiPort)"].path) { $o.running = $true }
    # 残留判断（对应飞书常见报错表）：有 Shell 文件夹但没装完；Shell.zip 太小
    $o.brokenResidue = [bool]($o.hasShell -and -not $o.installComplete)
    $o.zipLooksPartial = [bool]($zip -and $zip.Length -lt 20MB)
    $napcat += $o
}

if (-not $wmiOk) {
    foreach ($g in @($napcat | Where-Object { $_.running -and $_.webuiPort } | Group-Object { "$($_.webuiPort)" } | Where-Object { $_.Count -gt 1 })) {
        $keep = $g.Group | Sort-Object { if ($null -ne $_.minutesSinceWrite) { $_.minutesSinceWrite } else { [int]::MaxValue } } | Select-Object -First 1
        foreach ($n in $g.Group) { if (-not [object]::ReferenceEquals($n, $keep)) { $n.running = $false } }
    }
}

# ---------- 6. 别的装法 ----------
$others = @()
foreach ($h in ($hits | Where-Object { $_.Name -eq 'launcher.bat' })) {
    if (Get-ChildItem -LiteralPath $h.DirectoryName -Recurse -Depth 2 -Filter 'snowluma*' -Force | Select-Object -First 1) { $others += [ordered]@{ kind = 'SnowLuma'; dir = $h.DirectoryName } }
}
foreach ($h in ($hits | Where-Object { $_.Name -eq 'package.json' })) {
    $pj = ReadJson $h.FullName
    $n = "$($pj.name)"
    if ($n -match 'qq-bridge|ai-companion-kit|dsh-qq|qq-agent|snowluma') { $others += [ordered]@{ kind = $n; dir = $h.DirectoryName } }
}
foreach ($h in ($hits | Where-Object { $_.Name -in 'Lagrange.OneBot.exe', 'LLOneBot.exe', 'LuckyLilliaBot.exe' })) { $others += [ordered]@{ kind = $h.BaseName; dir = $h.DirectoryName } }
foreach ($h in ($hits | Where-Object { $_.Name -eq 'bot.py' -and $_.DirectoryName -match 'mai' })) { $others += [ordered]@{ kind = 'MaiBot'; dir = $h.DirectoryName } }
foreach ($pn in 'snowluma', 'Lagrange.OneBot', 'LLOneBot', 'LuckyLilliaBot', 'QQ-agent', 'MaiBot') {
    if (Get-Process -Name "*$pn*" | Where-Object { IsMine $_.Id }) { $others += [ordered]@{ kind = "$pn(在跑)"; dir = '' } }
}
foreach ($p in '5099', '3000', '3001', '3100') {
    if ($ports.Contains($p)) { $others += [ordered]@{ kind = "端口$p 被占"; dir = "$($ports[$p].process)$(if (-not $ports[$p].mine) { '（别的账户）' })"; path = "$($ports[$p].path)" } }
}
# 本 Skill 自己装在工作目录里的 SnowLuma 不算「别的装法」（它单独报在 snowluma 那一项）
if ($workDir) { $mySl = "$workDir\SnowLuma"; $others = @($others | Where-Object { $o = $_; -not (@("$($o.dir)", "$($o.path)") | Where-Object { $_.StartsWith($mySl, [StringComparison]::OrdinalIgnoreCase) }) }) }
$docker = @()
if ($cmds['docker']) {
    $job = Start-Job { docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>$null }
    if (Wait-Job $job -Timeout 10) { $docker = @(Receive-Job $job | Where-Object { $_ -match 'astrbot|napcat|snowluma|maibot|lagrange|llonebot' }) }
    Remove-Job $job -Force
}

# ---------- 7. AI 帮手 ----------
$agents = [ordered]@{
    dsh       = Installed 'DeepSeek Harness'
    workbuddy = Installed 'WorkBuddy'
    agentsSkills    = @(Get-ChildItem -LiteralPath "$env:USERPROFILE\.agents\skills" -Directory | ForEach-Object { $_.Name })
    workbuddySkills = @(Get-ChildItem -LiteralPath "$env:USERPROFILE\.workbuddy\skills" -Directory | ForEach-Object { $_.Name })
}

# ---------- 8. 记忆、语音 ----------
$ollama = [ordered]@{ installed = [bool]((Installed 'Ollama') -or (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") -or ($workDir -and (Test-Path "$workDir\Ollama\ollama.exe"))); running = [bool]($ports.Contains('11434') -and $ports['11434'].mine); portBusyByOtherUser = [bool]($ports.Contains('11434') -and -not $ports['11434'].mine) }
$models = $env:OLLAMA_MODELS; if (-not $models) { $models = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User') }; if (-not $models) { $models = "$env:USERPROFILE\.ollama\models" }
$ollama.bgeM3 = Test-Path -LiteralPath (Join-Path $models 'manifests\registry.ollama.ai\library\bge-m3')
$breezeDirs = @($(if ($workDir) { Join-Path $workDir 'breeze-tts' }), "$env:SystemDrive\breeze-tts", 'D:\breeze-tts', 'E:\breeze-tts') | Where-Object { (Test-Path -LiteralPath (Join-Path $_ 'start-breeze.cmd')) -and -not (Excluded $_) }
if ($ports.Contains('8137') -and $ports['8137'].mine -and $ports['8137'].path) { $bd = Split-Path $ports['8137'].path; while ($bd -and -not (Test-Path -LiteralPath (Join-Path $bd 'start-breeze.cmd'))) { $bd = Split-Path $bd }; if ($bd -and $bd -notin @($breezeDirs)) { $breezeDirs = @($breezeDirs) + $bd } }
$breezeInfo = [ordered]@{ installed = [bool](@($breezeDirs).Count -or ($ports.Contains('8137') -and $ports['8137'].mine -and $ports['8137'].process -match 'breeze')); serverPath = $(if ($ports.Contains('8137')) { "$($ports['8137'].path)" } else { '' }); dirs = @($breezeDirs); running = [bool]($ports.Contains('8137') -and $ports['8137'].mine); autostart = Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Startup')) '启动Breeze语音.lnk') }

# ---------- 8b. SnowLuma（本 Skill 装在工作目录里的）----------
$snowluma = $null
if ($workDir -and (Test-Path -LiteralPath "$workDir\SnowLuma\index.mjs")) {
    $sl = "$workDir\SnowLuma"; $rel = ReadJson "$sl\release.json"
    $acc = @(Get-ChildItem -LiteralPath "$sl\config" -Filter 'onebot_*.json' | Where-Object { $_.BaseName -match '^onebot_\d+$' })
    $wired = @($acc | Where-Object { $j = ReadJson $_.FullName; $j -and @($j.networks.wsClients | Where-Object { $_.enabled -and "$($_.url)" -match ':6199/ws$' }).Count })
    $snowluma = [ordered]@{
        dir = $sl; version = "$($rel.version)"; running = [bool]($allProcs | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($sl, [StringComparison]::OrdinalIgnoreCase) -and (IsMine $_.ProcessId) } | Select-Object -First 1)
        accountsSeen = $acc.Count; wiredToAstrBot = $wired.Count; agreed = Test-Path -LiteralPath "$workDir\private\snowluma-agreed.txt"
        autostart = Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Startup')) '大肥鱼-SnowLuma.lnk')
    }
    # 沙盒里读不到进程路径（10-28 桌面版 DSH 体检报「SnowLuma 没在跑」，其实在跑、小号也连着）：网页后台端口在听就算在跑
    $slPort = "$((ReadJson "$sl\config\runtime.json").webuiPort)"; if (-not $slPort) { $slPort = '5099' }
    if (-not $wmiOk -and -not $snowluma.running -and $ports.Contains($slPort) -and -not $ports[$slPort].path) { $snowluma.running = $true }
}

# ---------- 9. Vol.04 留下的东西 ----------
$leftovers = @()
foreach ($d in @('C:\大肥鱼', 'D:\大肥鱼', 'E:\大肥鱼', (Join-Path $desktop '大肥鱼'), 'C:\dafeiyu', 'D:\dafeiyu', $workDir) | Where-Object { $_ } | Select-Object -Unique) {
    # 有 private 子文件夹的是本 Skill 建的工作目录，别的是照 Vol.04 视频建的放 key 的文件夹
    if ((Test-Path -LiteralPath $d) -and -not (Excluded $d)) { $fl = @(Get-ChildItem -LiteralPath $d -File | ForEach-Object { $_.Name }); $leftovers += [ordered]@{ dir = $d; byThisSkill = (Test-Path -LiteralPath (Join-Path $d 'private')); fileCount = $fl.Count; files = @($fl | Select-Object -First 15) } }
}
$packs = @()
foreach ($r in ($roots | Select-Object -Unique)) {
    Get-ChildItem -LiteralPath $r -Recurse -Depth 2 -Directory -Force -Filter 'dafeiyu-config-pack*' | ForEach-Object {
        $imp = Join-Path $_.FullName 'tools\import_dafeiyu.py'
        if (Test-Path -LiteralPath $imp) {
            $t = Get-Content -Raw -Encoding UTF8 -LiteralPath $imp
            $packs += [ordered]@{ dir = $_.FullName; hasBreeze = (Test-Path -LiteralPath (Join-Path $_.FullName 'tools\breeze')); memeGroupFix = [bool]($t -match 'chat_and_plugin_llm'); dataDirAware = [bool]($t -match 'data-dir') }
        }
    }
}

# ---------- 10. 归类 ----------
$launcherAstr = @($astr | Where-Object { $_.kind -eq 'launcher' })
$readyInst = @($astr | Where-Object { $_.hasConfig })
$stage = 'S0-从零'
if ($launcher -or $launcherAstr.Count) { $stage = 'S1-启动器装了，组件或实例没好' }
if ($launcherAstr | Where-Object { $_.hasVenv -and $_.hasConfig }) { $stage = 'S2-实例建好了，还没装 NapCat' }
if ($napcat.Count) { $stage = 'S3-NapCat 下了，没装成' }
if ($napcat | Where-Object { $_.installComplete }) { $stage = 'S4-NapCat 装好了，还没登小号' }
if ($napcat | Where-Object { $_.accounts.Count }) { $stage = 'S5-小号登过，还没连上 AstrBot' }
if ($napcat | Where-Object { $_.wsClients | Where-Object { $_.enable -and $_.tokenMatchesAstrBot -and $_.endsWithWs -and -not $_.fullWidthColon } }) { $stage = 'S6-连接配好了，还没接 DeepSeek' }
if (($stage -like 'S6*') -and ($readyInst | Where-Object { $_.deepseekReady })) { $stage = 'S7-能聊天了，还没导配置包' }
if (($stage -like 'S7*') -and ($readyInst | Where-Object { $_.packPluginsPresent -ge 4 })) { $stage = 'S8-配置包导过了' }
if ($stage -eq 'S0-从零' -and $readyInst.Count) { $stage = 'S2-实例建好了，还没装 NapCat' }
# SnowLuma 路线（跟 NapCat 二选一；两个都有时看哪个走得更远）
if ($snowluma) {
    $sst = 'S4-SnowLuma 装好了，还没登小号'
    if ($snowluma.accountsSeen) { $sst = 'S5-小号登过，还没连上 AstrBot' }
    if ($snowluma.wiredToAstrBot) { $sst = 'S6-连接配好了，还没接 DeepSeek' }
    if (($sst -like 'S6*') -and ($readyInst | Where-Object { $_.deepseekReady })) { $sst = 'S7-能聊天了，还没导配置包' }
    if (($sst -like 'S7*') -and ($readyInst | Where-Object { $_.packPluginsPresent -ge 4 })) { $sst = 'S8-配置包导过了' }
    if ([int]($sst.Substring(1, 1)) -ge [int]($stage.Substring(1, 1))) { $stage = "$sst（SnowLuma）" }
}

if ($machine.profileNonAscii) { $warnings.Add('用户目录带中文或空格，启动器数据目录和工作目录要放全英文路径') }
foreach ($dr in $drives) { if ($dr.drive -eq "$env:SystemDrive" -and $dr.freeGB -lt 15) { $warnings.Add("系统盘只剩 $($dr.freeGB) GB") } }
$av3 = @($av | Select-Object -Unique | Where-Object { $_ -notmatch 'Windows Defender|Microsoft Defender' })
if ($av3.Count) { $warnings.Add('装了第三方杀毒软件：' + ($av3 -join '、') + '，NapCat 解压报拒绝访问时先关实时保护') }
if ($desktopQQRunning -and -not $snowluma) { $warnings.Add('桌面 QQ 正开着（多半登着大号）：选 SnowLuma 的话小号要在桌面 QQ 里再登一个、两个号同时开着；NapCat 不受影响') }
if ($snowluma -and -not $snowluma.running) { $warnings.Add('SnowLuma 装了但没在跑') }
if ($machine.domainJoined) { $warnings.Add('这台电脑加入了公司或学校的域：装软件可能被管理策略拦，也可能违反单位规定，先问他是不是自己的电脑') }
if ($machine.policyLocked) { $warnings.Add('这台电脑的 PowerShell 脚本策略被管理员锁了，安装脚本可能跑不起来') }
if ($machine.build -and $machine.build -lt 17134) { $warnings.Add('Windows 10 版本太老（1803 之前），没有自带 curl 和 tar，要先更新系统') }
if ($machine.arch -eq 'ARM64') { $warnings.Add('这是 ARM 处理器的电脑（骁龙笔记本这类）：QQ、NapCat、本机语音、本机记忆在上面都没测过，本机项先不建议') }
if ($machine.ramGB -and $machine.ramGB -lt 8) { $warnings.Add("内存只有 $($machine.ramGB) GB：本机语音和本机记忆别一起开") }
if ($speed.tier -in 'slow', 'none') { $warnings.Add('连境外的网速慢（GitHub、hf-mirror 都不到 1 MB/s）：本机语音、本机记忆这种几个 GB 的先不建议，表情包要等很久') }
if ($cmds['pythonIsStoreStub'] -and -not $cmds['python']) { $warnings.Add('python 命令是应用商店的空壳') }
if ($launcherAstr.Count -gt 1) { $warnings.Add("有 $($launcherAstr.Count) 个 AstrBot 实例，动之前问清用哪个") }
if (@($napBases).Count -gt 1) { $warnings.Add("找到 $(@($napBases).Count) 份 NapCat") }
foreach ($a in $readyInst) {
    if ($a.computerUse -and $a.computerUse -ne 'none') { $warnings.Add("实例 $($a.dir) 开着「使用电脑能力」($($a.computerUse))") }
    if ($a.hasConfig -and -not $a.whitelistOn) { $warnings.Add("实例 $($a.dir) 没开白名单，谁都能使唤她") }
    if ($a.packPluginsPresent -ge 4 -and $a.Contains('memeGroupFixed') -and -not $a.memeGroupFixed) { $warnings.Add("实例 $($a.dir) 的表情包还是旧设置，群里不发图") }
}
foreach ($p in $ports.Keys) { if (-not $ports[$p].mine) { $warnings.Add("端口 $p 被这台电脑上别的 Windows 账户的 $($ports[$p].process) 占着，装同类东西会抢端口") } }
if ($stage -match '^S[5-8]' -and -not ($astr | Where-Object { $_.running }) -and -not ($napcat | Where-Object { $_.running }) -and -not ($snowluma -and $snowluma.running)) { $warnings.Add('机器人装过，但现在 AstrBot 和协议端都没在跑') }
foreach ($n in $napcat) {
    if ($n.otherUserOwns) { continue }   # 这台电脑另一个 Windows 账户的，不归他管，不报它的毛病
    if ($n.brokenResidue) { $warnings.Add("NapCat 有没装完的残留：$($n.dir)") }
    if ($n.zipLooksPartial) { $warnings.Add("NapCat.Shell.zip 只有 $($n.shellZipMB) MB，多半没下完") }
    foreach ($w in $n.wsClients) { if ($w.enable -and $w.fullWidthColon) { $warnings.Add('NapCat 连接地址里有中文冒号') }; if ($w.enable -and -not $w.endsWithWs) { $warnings.Add('NapCat 连接地址结尾少了 /ws') }; if ($w.enable -and $w.hasToken -and -not $w.tokenMatchesAstrBot) { $warnings.Add("NapCat（$($n.dir)）和 AstrBot 两边的 token 对不上") } }
}

$result = [ordered]@{
    probedAt = (Get-Date).ToString('s'); stage = $stage; warnings = @($warnings | Select-Object -Unique); excluded = $(if ($excludeQuiet) { @() } else { $Exclude })
    machine = $machine; drives = $drives; gpus = $gpus; breezeFit = $breeze
    proxy = [ordered]@{ systemProxyOn = [bool]$proxy.ProxyEnable; server = "$($proxy.ProxyServer)" }
    antivirus = @($av | Select-Object -Unique); commands = $cmds; network = $net; networkVia = $netVia; speed = $speed
    desktopQQ = [ordered]@{ installed = [bool]((Installed '^(腾讯)?QQ$') -or $desktopQQExe); exe = "$desktopQQExe"; version = "$desktopQQVer"; running = $desktopQQRunning }
    workDir = $workDir; snowluma = $snowluma
    napcatQQRunning = $napcatQQRunning
    launcher = $launcher; launcherDataDirs = $dataDirs; dataDirOverridden = $dataDirOverridden; components = $components
    astrbot = $astr; napcat = $napcat; ws6199Established = $established6199
    others = $others; docker = $docker; agents = $agents; ollama = $ollama; breeze = $breezeInfo
    vol04Leftovers = $leftovers; configPacks = $packs; ports = $ports
}

'===JSON==='
$result | ConvertTo-Json -Depth 8
'===摘要==='
if ($machine.sandboxed) { "提醒：体检是在 AI 帮手的沙盒里跑的，系统信息换了备用读法；AstrBot、NapCat、SnowLuma 在不在跑是按端口和日志新旧推断的，跟他说「在不在线」之前，拿 ws6199Established（小号连没连着）再核一遍。安装要用完全访问权限跑。" }
"系统：$($machine.os)（$($machine.build)），用户目录 $($machine.profile)$(if ($machine.profileNonAscii) { '（带中文或空格）' })"
"磁盘：" + (($drives | ForEach-Object { "$($_.drive) 剩 $($_.freeGB) GB" }) -join '，')
"显卡：" + $(if ($gpus.Count) { ($gpus | ForEach-Object { "$($_.name) $($_.vramGB) GB" }) -join '；' } else { '没认出' }) + "；本机语音：$breeze"
"桌面 QQ：$(if ($result.desktopQQ.installed) { "装了$(if ($desktopQQVer) { "（$desktopQQVer）" })" } else { '没装' })，$(if ($desktopQQRunning) { '正开着' } else { '没开' })"
"工作目录：$(if ($workDir) { $workDir } else { '还没有（没用本 Skill 装过）' })"
"SnowLuma：$(if ($snowluma) { "$($snowluma.version)，$(if ($snowluma.running) { '在跑' } else { '没在跑' })，认出 $($snowluma.accountsSeen) 个号，连 AstrBot 的 $($snowluma.wiredToAstrBot) 个" } else { '没装' })"
"AstrBot：启动器$(if ($launcher) { " $($launcher.version) 装了" } else { '没装' })；实例 $($launcherAstr.Count) 个，别的装法 $(@($astr | Where-Object { $_.kind -eq 'manual' }).Count) 个；在跑 $(@($astr | Where-Object { $_.running }).Count) 个"
foreach ($a in $astr) { "  - $($a.kind) $($a.dir)：版本 $($a.version)，$(if ($a.running) { '在跑' } else { '没在跑' })，DeepSeek $(if ($a.deepseekReady) { '接好了' } else { '没接' })，白名单 $($a.whitelistCount) 条，配置包插件 $($a.packPluginsPresent)/6" }
"NapCat：找到 $(@($napcat).Count) 份" + $(if (@($napcat).Count) { '：' + (($napcat | ForEach-Object { "$($_.dir)（装完 $($_.installComplete)，登过 $($_.accounts.Count) 个号，在跑 $($_.running)）" }) -join '；') })
"小号和 AstrBot 连没连着（6199）：$(if ($established6199) { '连着' } else { '没连着' })"
"别的装法：" + $(if ($others.Count -or $docker.Count) { (@($others | ForEach-Object { "$($_.kind) $($_.dir)" }) + @($docker)) -join '；' } else { '没发现' })
"AI 帮手：DSH $(if ($agents.dsh) { $agents.dsh.version } else { '没装' })，WorkBuddy $(if ($agents.workbuddy) { $agents.workbuddy.version } else { '没装' })"
"记忆：Ollama $(if ($ollama.installed) { '装了' } else { '没装' })，bge-m3 $(if ($ollama.bgeM3) { '有' } else { '没有' })；Breeze $(if ($breezeInfo.installed) { '装了' } else { '没装' })$(if ($breezeInfo.running) { '、在跑' })"
"以前留下的文件夹：" + $(if ($leftovers.Count -or $packs.Count) { (@($leftovers | ForEach-Object { "$($_.dir)$(if ($_.byThisSkill) { '（本 Skill 的工作目录）' } else { '（照 Vol.04 视频建的）' })" }) + @($packs | ForEach-Object { "配置包 $($_.dir)" })) -join '；' } else { '没有' })
"网络：" + $(if ($net.Count) { ($net.Keys | ForEach-Object { "$($_ -replace 'https://','') $(if ($net[$_].ok) { "$($net[$_].ms)ms" } else { '不通' })" }) -join '，' } else { '没测' }) + $(if ($netVia -eq 'tcp') { '（在沙盒里只测了能不能连上）' }) + "；系统代理 $(if ($proxy.ProxyEnable) { '开着' } else { '关着' })"
"网速：" + $(if ($speed.tier -eq 'unknown') { '没测' } else { "GitHub（经加速）$($speed.github) MB/s，hf-mirror $($speed.hf) MB/s，国内镜像 $($speed.cn) MB/s，" + @{ fast = '快'; mid = '一般'; slow = '慢'; none = '境外都下不动' }[$speed.tier] })
"内存：$($machine.ramGB) GB；处理器：$($machine.arch)"
"判断：$stage"
foreach ($w in ($warnings | Select-Object -Unique)) { "注意：$w" }
'===END==='
