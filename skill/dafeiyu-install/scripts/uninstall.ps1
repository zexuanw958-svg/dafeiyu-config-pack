# 卸载大肥鱼：先只列清单不删；他看过、回「确认」以后加 -Yes 才删。只删我们装的东西，工作目录里他自己放的文件原样留着。
# 用法：
#   列清单：powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 -Dir <工作目录>
#   真删：  powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1 -Dir <工作目录> -Yes [-Backup] [-RemoveOllamaModel]
#   -Backup：删之前先停 AstrBot、跑 backup.ps1，备份放桌面（他以后想回来，装好主路再 restore.ps1）。
#   -RemoveOllamaModel：Ollama 装在默认位置（不在工作目录里，可能别的程序也在用）时，顺手删掉我们下的 bge-m3 模型（约 1.1 GB）。
# 删掉的东西进回收站（单个太大的，Windows 会直接删）。桌面 QQ、DSH 这些正常软件不卸。
param([Parameter(Mandatory)][string]$Dir, [switch]$Yes, [switch]$Backup, [switch]$RemoveOllamaModel)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')

# 0. 防手滑：盘根、用户文件夹、桌面这类地方不许当工作目录整个卸；里面得有我们装的标志
$deny = @([IO.Path]::GetPathRoot($Dir), $env:USERPROFILE, [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('MyDocuments'), $env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ } | ForEach-Object { "$_".TrimEnd('\') }
if ($deny -contains $Dir) { throw "$Dir 是系统或用户自己的文件夹，不能当大肥鱼的工作目录卸。先确认工作目录（体检报告「工作目录」那行）" }
if (-not ((Test-Path "$Dir\private") -or (Test-Path "$Dir\start-astrbot.cmd") -or (Test-Path "$Dir\astrbot"))) { throw "$Dir 里没有大肥鱼装的东西（private、astrbot、start-astrbot.cmd 都没有），不卸" }

function Under([string]$p) { $p -and ([IO.Path]::GetFullPath($p.Trim('"'))).StartsWith("$Dir\", [StringComparison]::OrdinalIgnoreCase) }
function Size([string]$p) {
    if (Test-Path -LiteralPath $p -PathType Leaf) { return (Get-Item -LiteralPath $p -Force).Length }
    (Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
}

# 1. 工作目录里我们装的（名字是各安装脚本写死的）
$known = 'astrbot', 'uv', 'NapCat', 'SnowLuma', 'private', 'breeze-tts', 'ffmpeg', 'asr', 'Ollama', 'ollama-models', 'downloads', 'logs', 'tools', 'dafeiyu-config-pack', 'ffmpeg-unzip', 'asr-unzip', 'answers.json', 'asr-path.txt', 'ffmpeg-path.txt', '大肥鱼工作台.html', '扫码登录.html'
$inDir = New-Object Collections.ArrayList
$keep = New-Object Collections.ArrayList
foreach ($i in Get-ChildItem -LiteralPath $Dir -Force) {
    if ($known -contains $i.Name -or $i.Name -like 'start-*.cmd' -or $i.Name -like 'import-*.cmd' -or $i.Name -like 'workbench*') { [void]$inDir.Add($i.FullName) }
    else { [void]$keep.Add($i.Name) }
}

# 2. 工作目录外面的：启动文件夹、桌面上指向工作目录的快捷方式；指向工作目录的 dafeiyu-* 计划任务；环境变量；记工作目录的小文件
$sh = New-Object -ComObject WScript.Shell
$lnks = @(foreach ($folder in [Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('Desktop')) {
    foreach ($f in Get-ChildItem -LiteralPath $folder -Filter *.lnk -ErrorAction SilentlyContinue) {
        $l = $sh.CreateShortcut($f.FullName)
        if ((Under $l.TargetPath) -or (Under $l.WorkingDirectory) -or ("$($l.Arguments)" -match [regex]::Escape("$Dir\"))) { $f.FullName }
    }
})
$tasks = @(Get-ScheduledTask -TaskName 'dafeiyu-*' -ErrorAction SilentlyContinue | Where-Object { ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -match [regex]::Escape("$Dir\") } | ForEach-Object TaskName)
$envModels = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS', 'User')
$envHit = Under $envModels
$wdFile = "$env:LOCALAPPDATA\dafeiyu\workdir.txt"
$wdHit = (Test-Path $wdFile) -and ((Get-Content -Raw $wdFile).Trim().TrimEnd('\') -eq $Dir)
$ollamaOurs = Test-Path "$Dir\Ollama\ollama.exe"
$ollamaOther = @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1

# 3. 列清单
$total = ($inDir | ForEach-Object { Size $_ } | Measure-Object -Sum).Sum
Say ('要删的（工作目录 {0} 里，共 {1:N1} GB）：' -f $Dir, ($total / 1GB))
foreach ($p in $inDir) { Say ('  {0}（{1:N0} MB）' -f (Split-Path $p -Leaf), ((Size $p) / 1MB)) }
if ($lnks) { Say "快捷方式：$(($lnks | ForEach-Object { Split-Path $_ -Leaf }) -join '、')（启动文件夹和桌面上的）" }
if ($tasks) { Say "后台启动用的计划任务：$($tasks -join '、')" }
if ($envHit) { Say "环境变量 OLLAMA_MODELS（指向 $envModels）" }
if ($ollamaOurs) { Say 'Ollama（装在工作目录里的，用它自己的卸载程序卸）' }
elseif ($ollamaOther) { Say "Ollama 装在默认位置（$(Split-Path $ollamaOther)，可能是他自己装的、别的程序也在用），不卸$(if ($RemoveOllamaModel) { '，只删 bge-m3 模型' } else { '；我们下的 bge-m3 模型约 1.1 GB，不要了加 -RemoveOllamaModel' })" }
if ($keep.Count) { Say "工作目录里这些不是我们装的，留着不动：$($keep -join '、')" }
Say '不动的：桌面 QQ（正常软件，不想要了去「设置 → 应用」里卸）、AI 帮手本身'
if (-not $Yes) {
    Say '只列了清单，什么都没删。他看过、回「确认」后加 -Yes 再跑；想留个备份（人格、记忆、key）就再加 -Backup'
    exit 0
}

# 4. 停工作目录里的程序（AstrBot、协议端、Breeze、Ollama、工作台）。SnowLuma 挂在桌面 QQ 上的那部分要他退出 QQ 才放得开
Say '先停掉工作目录里在跑的程序'
Stop-UnderDir $Dir
# 启动器装的 AstrBot，工作台小服务用的 Python 不在工作目录里，按命令行再停一下
Get-CimInstance Win32_Process | Where-Object { "$($_.CommandLine)".IndexOf("$Dir\tools\workbench\server.py", [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
if ($Backup) {
    try { & "$PSScriptRoot\backup.ps1" -Dir $Dir } catch { Say "备份没成：$($_.Exception.Message)。先不删，处理好再跑"; exit 3 }
}
if ($ollamaOurs) {
    $un = Get-ChildItem "$Dir\Ollama" -Filter 'unins*.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($un) {
        Say '卸 Ollama'
        # 它的卸载程序会先把自己复制到临时文件夹、原进程马上退出，所以按 ollama.exe 消失来等
        Start-Process $un.FullName -ArgumentList '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES' | Out-Null
        $t0 = Get-Date; while ((Test-Path "$Dir\Ollama\ollama.exe") -and ((Get-Date) - $t0).TotalSeconds -lt 180) { Start-Sleep 3 }
        Start-Sleep 3
    }
} elseif ($ollamaOther -and $RemoveOllamaModel) {
    & $ollamaOther rm bge-m3 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Say '删了 bge-m3 模型' } else { Say 'bge-m3 没删掉（Ollama 没开着？），不影响卸载；要删就打开 Ollama 再跑 ollama rm bge-m3' }
}

# 5. 删：进回收站，不弹任何窗口；被占着的记下来
Add-Type -Namespace Dfy -Name Bin -MemberDefinition @'
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct OP { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags; [MarshalAs(UnmanagedType.Bool)] public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }
[DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern int SHFileOperation(ref OP op);
public static int Recycle(string path) {
    OP op = new OP(); op.wFunc = 3; op.pFrom = path + "\0";
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400;   // 进回收站、不确认、不显示进度、不弹错误框
    return SHFileOperation(ref op);
}
'@ -ErrorAction SilentlyContinue
if (-not ('Dfy.Bin' -as [type])) { Add-Type -AssemblyName Microsoft.VisualBasic }   # 编不出来就用 VB 自带的进回收站
$fail = New-Object Collections.ArrayList
foreach ($p in @($lnks) + @($inDir)) {
    if (-not (Test-Path -LiteralPath $p)) { continue }
    if ('Dfy.Bin' -as [type]) { $rc = [Dfy.Bin]::Recycle($p) } else {
        $rc = 0
        try {
            if (Test-Path -LiteralPath $p -PathType Container) { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }
            else { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($p, 'OnlyErrorDialogs', 'SendToRecycleBin') }
        } catch { $rc = 1 }
    }
    if ($rc -ne 0 -or (Test-Path -LiteralPath $p)) { [void]$fail.Add($p) }
}
foreach ($t in $tasks) { & schtasks.exe /delete /tn $t /f 2>&1 | Out-Null }
if ($envHit) { [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $null, 'User'); [Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', $null, 'User') }
if ($wdHit) { Remove-Item $wdFile -Force -ErrorAction SilentlyContinue; Remove-Item "$env:LOCALAPPDATA\dafeiyu" -ErrorAction SilentlyContinue }
if (-not (Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue)) { Remove-Item -LiteralPath $Dir -Force -ErrorAction SilentlyContinue }   # 空文件夹直接删

# 6. 报结果
if ($fail.Count) {
    Say "这些没删掉（多半是还有程序占着）：$(($fail | ForEach-Object { Split-Path $_ -Leaf }) -join '、')"
    if ($fail | Where-Object { $_ -like '*SnowLuma*' }) { Say '  SnowLuma 挂在桌面 QQ 上：请他先退出 QQ（右下角企鹅图标右键 → 退出），再重跑这条卸载命令' }
    else { Say '  重启电脑以后再跑一次这条卸载命令就行' }
    exit 2
}
Say '卸干净了，东西都在回收站里（想腾出空间就清空回收站）'
# 本机还有另一套大肥鱼（另装一套后卸掉其中一套）：它多半用着同一个 key、同一个小号，删 key、移除设备会把它也弄下线
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) '大肥鱼-AstrBot.lnk'
$otherAuto = if (Test-Path $lnk) { (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath } else { '' }
$ws = Get-NetTCPConnection -LocalPort 6199 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$otherWs = if ($ws) { (Get-Process -Id $ws.OwningProcess -ErrorAction SilentlyContinue).Path } else { '' }
$other = @($otherAuto, $otherWs) | Where-Object { $_ -and -not (Under $_) } | Select-Object -First 1
if ($other) { Say "这台电脑上还有另一套大肥鱼在用（$other）。它多半用着同一个 DeepSeek key 和同一个小号：key 别删，手机 QQ 的登录设备也别移除，不然那套也会下线" }
else { Say '还有两件要他自己做：① 去 DeepSeek 开放平台「API keys」把这个 key 删掉（豆包、硅基流动的也一样）；② 手机 QQ 登小号 → 设置 → 账号安全 → 登录设备管理，把这台电脑移除' }
