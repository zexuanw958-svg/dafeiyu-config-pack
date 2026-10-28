# 第 8 步：开机自启 + 桌面「大肥鱼掉线-双击扫码」。可以重复跑。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File finish.ps1 -Dir <工作目录>
# 家用电脑用「启动文件夹」就够了（他登录 Windows 时启动，窗口最小化）。
param([Parameter(Mandatory)][string]$Dir)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$startup = [Environment]::GetFolderPath('Startup')
$desktop = [Environment]::GetFolderPath('Desktop')   # 桌面不一定在 C:\Users\<名>\Desktop
$ws = New-Object -ComObject WScript.Shell
function New-Lnk([string]$Path, [string]$Target, [string]$WorkDir, [int]$Style, [string]$Desc) {
    $l = $ws.CreateShortcut($Path); $l.TargetPath = $Target; $l.WorkingDirectory = $WorkDir; $l.WindowStyle = $Style; $l.Description = $Desc; $l.Save()
}

# 1 开机自启（WindowStyle 7 = 最小化）
if (Test-Path "$Dir\start-astrbot.cmd") { New-Lnk "$startup\大肥鱼-AstrBot.lnk" "$Dir\start-astrbot.cmd" $Dir 7 '大肥鱼的大脑，别关'; Say '开机自启：AstrBot' }
else { Say '没找到 start-astrbot.cmd（启动器装的 AstrBot 在启动器设置里开「开机自启」）' }
# 协议端只自启现在用的那一个（两个都开会抢同一个小号，互相挤下线）
$proto = if (Test-Path "$Dir\private\protocol.txt") { "$(Get-Content -Raw "$Dir\private\protocol.txt")".Trim() } elseif ((Test-Path "$Dir\start-snowluma.cmd") -and -not (Test-Path "$Dir\start-napcat.cmd")) { 'snowluma' } else { 'napcat' }
if ($proto -eq 'napcat' -and (Test-Path "$Dir\start-napcat.cmd")) {
    Remove-Item "$startup\大肥鱼-SnowLuma.lnk" -ErrorAction SilentlyContinue
    if (-not (Select-String -Path "$Dir\start-napcat.cmd" -Pattern 'NapCatWinBootMain\.exe \d+' -Quiet)) { Say '注意：start-napcat.cmd 还没带小号（connect-napcat.ps1 没跑完？），开机会出二维码，不会自动登' }
    New-Lnk "$startup\大肥鱼-NapCat.lnk" "$Dir\start-napcat.cmd" $Dir 7 '帮大肥鱼登 QQ，别关'; Say '开机自启：NapCat（快速登录，一般不用扫码）'
}
if ($proto -eq 'snowluma' -and (Test-Path "$Dir\start-snowluma.cmd")) {
    Remove-Item "$startup\大肥鱼-NapCat.lnk" -ErrorAction SilentlyContinue
    New-Lnk "$startup\大肥鱼-SnowLuma.lnk" "$Dir\start-snowluma.cmd" $Dir 7 '帮大肥鱼登 QQ（挂在桌面 QQ 上），别关'
    Say '开机自启：SnowLuma。桌面 QQ 也要开机自动登小号：请他在 QQ 登录窗口勾「自动登录」，QQ 设置里打开「开机自动启动」（让他自己点，告诉他在哪）'
}
$ollamaApp = @("$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe", "$Dir\Ollama\ollama app.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ollamaApp) { New-Lnk "$startup\Ollama.lnk" $ollamaApp (Split-Path $ollamaApp) 7 '大肥鱼的记忆'; Say '开机自启：Ollama（记忆；它的安装包不会自己加）' }
if (Get-ChildItem $startup -Filter '*Breeze*' -ErrorAction SilentlyContinue) { Say '开机自启：Breeze 语音（装的时候已经加过）' }

# 2 掉线自救：脚本拷进工作目录（观众删了 Skill 也能用），桌面放快捷方式
New-Item -ItemType Directory -Force "$Dir\tools" | Out-Null
Copy-Item "$PSScriptRoot\relogin-qq.ps1" "$Dir\tools\relogin-qq.ps1" -Force
[IO.File]::WriteAllText("$Dir\tools\relogin.cmd", "@echo off`r`ntitle Dafeiyu relogin`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0relogin-qq.ps1`"`r`npause`r`n", (New-Object Text.UTF8Encoding($false)))
$reName = if ($proto -eq 'snowluma') { '大肥鱼掉线-双击重登' } else { '大肥鱼掉线-双击扫码' }
foreach ($old in '大肥鱼掉线-双击重登', '大肥鱼掉线-双击扫码') { if ($old -ne $reName) { Remove-Item "$desktop\$old.lnk" -ErrorAction SilentlyContinue } }
New-Lnk "$desktop\$reName.lnk" "$Dir\tools\relogin.cmd" "$Dir\tools" 1 '大肥鱼不回话了就双击我'
Say "桌面放了「$reName」"

# 3 工作台：双击打开一个小窗口（状态灯、一键修复、补装、复制求助信息、备份、直达后台、交给 Agent 的话）
#   本机小服务 tools\workbench\server.py 用 AstrBot 自带的 Python 跑，只听 127.0.0.1；它要调的几个脚本一起拷进 tools\（备份要 dot-source 同目录的 common.ps1）
#   找不到 Python 时 open.ps1 退回 workbench.ps1 的静态页（只能看，不能修）
New-Item -ItemType Directory -Force "$Dir\tools\workbench" | Out-Null
Copy-Item "$PSScriptRoot\workbench\*" "$Dir\tools\workbench\" -Recurse -Force
foreach ($f in 'workbench.ps1', 'common.ps1', 'backup.ps1', 'diag.ps1', 'import-pack.ps1') { Copy-Item "$PSScriptRoot\$f" "$Dir\tools\$f" -Force }
# 工作台「更新」页靠这两个文件：现在是哪一版 Skill、Skill 放在哪（只记技能文件夹里的；从下载文件夹跑的不记）
$skillHome = Split-Path $PSScriptRoot
if (Test-Path "$skillHome\version.json") { Copy-Item "$skillHome\version.json" "$Dir\tools\skill-version.json" -Force }
if ((Split-Path (Split-Path $skillHome) -Leaf) -eq 'skills') { [IO.File]::WriteAllText("$Dir\tools\skill-home.txt", $skillHome, (New-Object Text.UTF8Encoding($false))) }
[IO.File]::WriteAllText("$Dir\tools\workbench.cmd", "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"%~dp0workbench\open.ps1`"`r`n", (New-Object Text.UTF8Encoding($false)))
New-Lnk "$desktop\大肥鱼工作台.lnk" "$Dir\tools\workbench.cmd" "$Dir\tools" 7 '看大肥鱼在不在、一键修、补装、备份'
if (Test-Path "$Dir\tools\workbench\dafeiyu.ico") { $l = $ws.CreateShortcut("$desktop\大肥鱼工作台.lnk"); $l.IconLocation = "$Dir\tools\workbench\dafeiyu.ico,0"; $l.Save() }
& "$Dir\tools\workbench.ps1" -Dir $Dir -NoOpen   # 顺手生成兜底静态页
Say '桌面放了「大肥鱼工作台」（双击开小窗口，不用浏览器地址栏）；下一步照 收尾-自启和掉线.md 的 8.3 打开它给他看'
