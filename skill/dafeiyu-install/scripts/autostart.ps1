# 只管当前 Windows 用户的启动项。旧入口移走/禁用并留清单，不删除。
function Get-UserStartupDir { [Environment]::GetFolderPath('Startup') }
function Get-UserStartupIdentity {
    $me = [Security.Principal.WindowsIdentity]::GetCurrent()
    return @($me.Name, $me.User.Value, $env:USERNAME)
}

function Test-NapCatCommand([string]$Command) {
    if ($Command -match '(?i)napcat|napcat\.bat') { return $true }
    # 处理「快捷方式 → 自己写的 cmd/bat → napcat.bat」。不执行它。
    $m = [regex]::Match($Command, '(?i)"([^"]+\.(?:cmd|bat|ps1))"|([A-Z]:\\[^"\s]+\.(?:cmd|bat|ps1))')
    if ($m.Success) {
        $f = [Environment]::ExpandEnvironmentVariables($(if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }))
        if ((Test-Path -LiteralPath $f) -and (Get-Item -LiteralPath $f).Length -lt 65536) {
            return [bool]((Get-Content -Raw -LiteralPath $f -ErrorAction SilentlyContinue) -match '(?i)napcat')
        }
    }
    return $false
}

function Get-NapCatStartup([string]$Dir) {
    $startup = Get-UserStartupDir
    $ws = New-Object -ComObject WScript.Shell
    foreach ($f in Get-ChildItem -LiteralPath $startup -File -ErrorAction SilentlyContinue) {
        $cmd = $f.FullName
        if ($f.Extension -eq '.lnk') { $l = $ws.CreateShortcut($f.FullName); $cmd = "$($l.TargetPath) $($l.Arguments)" }
        # 本工作目录的正式入口由 Set-QQAutostart 原位升级，不当成重复项。
        if ($f.Name -eq '大肥鱼-NapCat.lnk' -and $cmd.IndexOf("$Dir\", [StringComparison]::OrdinalIgnoreCase) -ge 0) { continue }
        $ap = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder' -ErrorAction SilentlyContinue).($f.Name)
        if ($ap -is [byte[]] -and $ap.Length -and ($ap[0] -band 1)) { continue }
        if (Test-NapCatCommand $cmd) { [pscustomobject]@{ Kind='startup'; Name=$f.Name; Path=$f.FullName } }
    }
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $run = Get-ItemProperty $key -ErrorAction SilentlyContinue
    if ($run) { foreach ($p in $run.PSObject.Properties) {
        if ($p.Name -match '^PS(Path|ParentPath|ChildName|Drive|Provider)$') { continue }
        $ap = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run' -ErrorAction SilentlyContinue).($p.Name)
        if ($ap -is [byte[]] -and $ap.Length -and ($ap[0] -band 1)) { continue }
        if (Test-NapCatCommand "$($p.Value)") { [pscustomobject]@{ Kind='run'; Name=$p.Name; Path=$key } }
    } }
    $identities = @(Get-UserStartupIdentity)
    foreach ($t in Get-ScheduledTask -ErrorAction SilentlyContinue) {
        if ($t.State -eq 'Disabled' -or $t.Principal.UserId -notin $identities) { continue }
        # 仅启动/登录触发的任务；Start-Detached 建的临时 once 任务不算开机自启。
        if (-not @($t.Triggers | Where-Object { $_.CimClass.CimClassName -in 'MSFT_TaskBootTrigger','MSFT_TaskLogonTrigger' })) { continue }
        $cmd = ($t.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' '
        if (Test-NapCatCommand $cmd) { [pscustomobject]@{ Kind='task'; Name=$t.TaskName; Path=$t.TaskPath } }
    }
}

function Disable-NapCatDuplicates([string]$Dir) {
    $items = @(Get-NapCatStartup $Dir)
    if (-not $items.Count) { return }
    $archive = "$Dir\旧版\开机自启-$(Get-Date -Format yyyyMMdd-HHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,6))"
    New-Item -ItemType Directory -Path $archive -Force -ErrorAction Stop | Out-Null
    $manifest = New-Object Collections.ArrayList
    foreach ($i in $items) {
        # 每项先留恢复资料，再动入口。失败就停下，不把「未禁用」说成修好了。
        $entry = [ordered]@{ kind=$i.Kind; name=$i.Name; path=$i.Path; status='backed-up' }
        if ($i.Kind -eq 'startup') {
            Move-Item -LiteralPath $i.Path -Destination $archive -ErrorAction Stop
        } elseif ($i.Kind -eq 'run') {
            $rk = Get-Item -LiteralPath $i.Path
            $entry.value = $rk.GetValue($i.Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $entry.valueKind = "$($rk.GetValueKind($i.Name))"
            $entry | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath "$archive\run-$($manifest.Count).json" -Encoding UTF8
            Remove-ItemProperty -LiteralPath $i.Path -Name $i.Name -ErrorAction Stop
        } else {
            Export-ScheduledTask -TaskName $i.Name -TaskPath $i.Path -ErrorAction Stop | Set-Content -LiteralPath "$archive\task-$($manifest.Count).xml" -Encoding Unicode
            Disable-ScheduledTask -TaskName $i.Name -TaskPath $i.Path -ErrorAction Stop | Out-Null
        }
        $entry.status = 'disabled'
        [void]$manifest.Add($entry)
        $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$archive\恢复清单.json" -Encoding UTF8
    }
    Write-Output "旧 NapCat 启动项已停用，恢复资料：$archive"
}

function Set-QQAutostart([string]$Dir, [string]$Protocol) {
    $startup = Get-UserStartupDir
    $ws = New-Object -ComObject WScript.Shell
    Disable-NapCatDuplicates $Dir
    # 切协议端时旧入口也保留，不删除。
    $other = if ($Protocol -eq 'snowluma') { 'NapCat' } else { 'SnowLuma' }
    $old = "$startup\大肥鱼-$other.lnk"
    if (Test-Path -LiteralPath $old) {
        $archive = "$Dir\旧版\开机自启-$(Get-Date -Format yyyyMMdd-HHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,6))"
        New-Item -ItemType Directory -Path $archive -Force | Out-Null
        Move-Item -LiteralPath $old -Destination $archive -ErrorAction Stop
        Write-Output "旧 $other 入口已移到：$archive"
    }
    $name = if ($Protocol -eq 'snowluma') { 'SnowLuma' } else { 'NapCat' }
    if (-not (Test-Path "$Dir\start-$Protocol.cmd")) { throw "没找到 start-$Protocol.cmd，先装好 $name" }
    $entry = "$startup\大肥鱼-$name.lnk"
    if (Test-Path -LiteralPath $entry) {
        $archive = "$Dir\旧版\开机自启-$(Get-Date -Format yyyyMMdd-HHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,6))"
        New-Item -ItemType Directory -Path $archive -Force | Out-Null
        Copy-Item -LiteralPath $entry -Destination $archive -ErrorAction Stop
        Write-Output "旧入口已移到：$archive（保留副本）"
    }
    $l = $ws.CreateShortcut($entry)
    $l.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $l.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Dir\tools\relogin-qq.ps1`" -Dir `"$Dir`" -Autostart"
    $l.WorkingDirectory = $Dir; $l.WindowStyle = 7
    $l.Description = '等网络和大脑就绪后登 QQ，后台运行'
    $l.Save()
    if (-not (Test-QQAutostart $Dir $Protocol)) { throw '入口已写好，但还没启用或重登工具缺失。请在任务管理器「启动应用」里启用这条大肥鱼入口，再重跑 finish.ps1。' }
    Write-Output "ADDED 大肥鱼-$name.lnk"
}

function Test-QQAutostart([string]$Dir, [string]$Protocol) {
    try {
        $name = if ($Protocol -eq 'snowluma') { 'SnowLuma' } else { 'NapCat' }
        $f = (Get-UserStartupDir) + "\大肥鱼-$name.lnk"
        if (-not (Test-Path -LiteralPath $f)) { return $false }
        $l = (New-Object -ComObject WScript.Shell).CreateShortcut($f)
        $ap = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder' -ErrorAction SilentlyContinue).("大肥鱼-$name.lnk")
        return -not ($ap -is [byte[]] -and $ap.Length -and ($ap[0] -band 1)) -and
            $l.Arguments -match '(?i)-Autostart' -and $l.Arguments.IndexOf("$Dir\tools\relogin-qq.ps1", [StringComparison]::OrdinalIgnoreCase) -ge 0 -and (Test-Path "$Dir\tools\relogin-qq.ps1")
    } catch { return $false }
}

# 掉线提醒的 Python 用官方便携版（11 MB，解压即用）：一直在后台跑，用 AstrBot 自带的那份会占着它的文件，升级 AstrBot 时删不掉。
# 云端工作台也用这份，放 <工作目录>\python；本机的放 tools\python。返回 pythonw.exe 的路径，下不下来返回空
function Get-PortablePython([string]$Dir, [string]$To) {
    if (Test-Path "$To\pythonw.exe") { return "$To\pythonw.exe" }
    $zip = "$Dir\downloads\python-3.12.10-embed-amd64.zip"
    $urls = @('https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-embed-amd64.zip',
              'https://mirrors.aliyun.com/python-release/windows/python-3.12.10-embed-amd64.zip',
              'https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-embed-amd64.zip',
              'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip')
    try {
        Get-File -Urls $urls -OutFile $zip -MinBytes 11133606 -Sha256 '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3' -ExpectedBytes 11133606 | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $To -Force
        Remove-Item -LiteralPath $zip -ErrorAction SilentlyContinue
    } catch { Write-Host "便携版 Python 没下下来（$($_.Exception.Message)）" }   # 这个函数的输出是返回值，别用 Say
    if (Test-Path "$To\pythonw.exe") { return "$To\pythonw.exe" }
    return ''
}

# 掉线提醒：登录 Windows 时起来，一直在后台（qq_watch.py），小号掉了弹通知（qq-toast.ps1）。本机和云端各一条启动项，互不影响。
# 脚本要已经拷进 tools。可以重复跑：停掉旧的，换成这版再起。他在工作台关过（private\qq-watch-off.txt）也照样起，只是不提醒
function Set-QQWatch([string]$Dir, [string]$Pythonw, [switch]$Cloud) {
    $name = if ($Cloud) { '云端大肥鱼-掉线提醒' } else { '大肥鱼-掉线提醒' }
    if (-not $Pythonw) { Say '掉线提醒没放上：没有可用的 pythonw。网络好了再跑一次 finish.ps1'; return }
    $script = "$Dir\tools\qq_watch.py"
    Get-CimInstance Win32_Process | Where-Object { "$($_.CommandLine)".IndexOf($script, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $ws = New-Object -ComObject WScript.Shell
    $l = $ws.CreateShortcut((Get-UserStartupDir) + "\$name.lnk")
    $l.TargetPath = $Pythonw
    $l.Arguments = "-X utf8 `"$script`" --dir `"$Dir`""
    $l.WorkingDirectory = [IO.Path]::GetTempPath()
    $l.WindowStyle = 7
    $l.Description = '小号掉线了弹通知，点一下重新登录'
    $l.Save()
    [IO.File]::WriteAllText("$Dir\tools\qq-watch.cmd", "@echo off`r`nchcp 65001 >nul`r`n`"$Pythonw`" -X utf8 `"%~dp0qq_watch.py`" --dir `"$Dir`"`r`n", (New-Object Text.UTF8Encoding($false)))
    Start-Detached $(if ($Cloud) { 'qqwatch-cloud' } else { 'qqwatch' }) "$Dir\tools\qq-watch.cmd"   # 两套各用各的任务名，不然互相杀
    Say '掉线提醒：开机后在后台看着，小号掉线会弹通知（工作台「操作」页可以关、可以试一下）'
}
