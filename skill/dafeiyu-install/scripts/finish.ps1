# 第 8 步：开机自启 + 桌面「大肥鱼掉线-双击扫码」+ 工作台。可以重复跑。装在云服务器上的（private\cloud.txt）只放「云端大肥鱼工作台」。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File finish.ps1 -Dir <工作目录>
# 家用电脑用「启动文件夹」就够了（他登录 Windows 时启动，窗口最小化）。
param([Parameter(Mandatory)][string]$Dir)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$startup = [Environment]::GetFolderPath('Startup')
$desktop = [Environment]::GetFolderPath('Desktop')   # 桌面不一定在 C:\Users\<名>\Desktop
$ws = New-Object -ComObject WScript.Shell
function New-Lnk([string]$Path, [string]$Target, [string]$WorkDir, [int]$Style, [string]$Desc, [string]$Arguments = '') {
    $l = $ws.CreateShortcut($Path); $l.TargetPath = $Target; $l.Arguments = $Arguments; $l.WorkingDirectory = $WorkDir; $l.WindowStyle = $Style; $l.Description = $Desc; $l.Save()
}
function Copy-Workbench {
    # 工作台：tools\workbench\server.py 是本机小服务，只听 127.0.0.1；「更新」页靠 skill-version.json（现在是哪一版）和 skill-home.txt（Skill 放在哪，只记技能文件夹里的）
    New-Item -ItemType Directory -Force "$Dir\tools\workbench" | Out-Null
    Copy-Item "$PSScriptRoot\workbench\*" "$Dir\tools\workbench\" -Recurse -Force
    $skillHome = Split-Path $PSScriptRoot
    if (Test-Path "$skillHome\version.json") { Copy-Item "$skillHome\version.json" "$Dir\tools\skill-version.json" -Force }
    if ((Split-Path (Split-Path $skillHome) -Leaf) -eq 'skills') { [IO.File]::WriteAllText("$Dir\tools\skill-home.txt", $skillHome, (New-Object Text.UTF8Encoding($false))) }
    [IO.File]::WriteAllText("$Dir\tools\workbench.cmd", "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"%~dp0workbench\open.ps1`"`r`n", (New-Object Text.UTF8Encoding($false)))
}

# 云端（她装在云服务器上，references/云端.md）：这台电脑上没有 AstrBot，只有 private 里连服务器的钥匙。
# 开机自启、掉线重登都在服务器上（C10），这里只放工作台；小服务要的 Python 用官方便携版（11 MB，解压即用，不装进系统）
$cloudTxt = "$Dir\private\cloud.txt"
$isCloud = (Test-Path $cloudTxt) -and (Select-String -Path $cloudTxt -Pattern '^\s*(host|ip)\s*[:：]\s*\S' -Quiet) -and
           -not (Test-Path "$Dir\start-astrbot.cmd") -and -not (Test-Path "$Dir\astrbot\data")
if ($isCloud) {
    if (-not (Test-Path "$Dir\python\pythonw.exe")) {
        $zip = "$Dir\downloads\python-3.12.10-embed-amd64.zip"
        $urls = @('https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-embed-amd64.zip',
                  'https://mirrors.aliyun.com/python-release/windows/python-3.12.10-embed-amd64.zip',
                  'https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-embed-amd64.zip',
                  'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip')
        try {
            Get-File -Urls $urls -OutFile $zip -MinBytes 11133606 -Sha256 '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3' -ExpectedBytes 11133606 | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath "$Dir\python" -Force
            Remove-Item -LiteralPath $zip -ErrorAction SilentlyContinue
            Say '工作台用的 Python（便携版）放在工作目录的 python 文件夹'
        } catch { Say "工作台用的 Python 没下下来（$($_.Exception.Message)）：网络好了再跑一次 finish.ps1，不然桌面的工作台打不开" }
    }
    Copy-Workbench
    $lnk = "$desktop\云端大肥鱼工作台.lnk"
    New-Lnk $lnk "$Dir\tools\workbench.cmd" "$Dir\tools" 7 '看云上的大肥鱼在不在、扫码登录、一键修、备份'
    if (Test-Path "$Dir\tools\workbench\dafeiyu.ico") { $l = $ws.CreateShortcut($lnk); $l.IconLocation = "$Dir\tools\workbench\dafeiyu.ico,0"; $l.Save() }
    # 以前收尾放在桌面的两个 .cmd 和说明，工作台都包了：挪进工作目录的「旧版」，不删
    $moved = @()
    foreach ($n in '云端大肥鱼-打开后台.cmd', '云端大肥鱼掉线-扫码.cmd', '云端大肥鱼-说明.txt') {
        if (Test-Path -LiteralPath "$desktop\$n") {
            New-Item -ItemType Directory -Force "$Dir\旧版\桌面旧入口" | Out-Null
            Move-Item -LiteralPath "$desktop\$n" "$Dir\旧版\桌面旧入口\$n" -Force; $moved += $n
        }
    }
    if ($moved) { Say ("桌面上以前的 {0} 挪进了工作目录的「旧版\桌面旧入口」（工作台都包了）" -f ($moved -join '、')) }
    if (-not (Test-Path "$Dir\python\pythonw.exe")) { exit 1 }
    Say '桌面放了「云端大肥鱼工作台」（双击开小窗口）；下一步照 云端.md 的 C10 打开它给他看'
    exit 0
}

# 1 开机自启（WindowStyle 7 = 最小化）
if (Test-Path "$Dir\start-astrbot.cmd") { New-Lnk "$startup\大肥鱼-AstrBot.lnk" "$Dir\start-astrbot.cmd" $Dir 7 '大肥鱼的大脑，别关'; Say '开机自启：AstrBot' }
elseif ((Find-AstrDataDir $Dir) -match '\\core\\data$') {
    # 启动器装的：开机自启是启动器自己的设置（存在它自己的数据库里），我们不替他改，也不另加一份自启（两份会抢着开同一个实例）
    if (Get-LauncherAutostart) { Say '开机自启：AstrBot 启动器已经开了「开机自启动」。再确认启动器里「退出时保留实例运行状态」也开着，实例才会跟着起来' }
    else {
        Say '开机自启：AstrBot 是启动器装的，要他自己在启动器里点（告诉他在哪，这一步没做完就不算装好）：'
        Say '  打开 AstrBot 启动器，点最左边的「高级」，在「通用」那一栏打开三个开关：开机自启动、自启动后最小化到托盘、退出时保留实例运行状态'
        Say '  同一栏的「关闭窗口时」保持「最小化到系统托盘」：启动器一退出，大脑也跟着停（托盘图标别点退出）'
        Say '  实例保持运行，重启一次电脑，看她自己上线就对了（工作台「开机自启」那一项会显示这次开机她几点起来的）'
    }
} else { Say '没找到 start-astrbot.cmd，也没认出启动器的实例：AstrBot 的开机自启按他原来的装法自己设' }
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
# Ollama 开机只起服务、不开托盘程序（托盘程序每次启动都弹聊天窗口）；同名的 Ollama.lnk（安装包自己加的、旧版指向托盘程序的）一并换掉
$ollamaCli = @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "$Dir\Ollama\ollama.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($ollamaCli) { New-Lnk "$startup\Ollama.lnk" "$env:SystemRoot\System32\conhost.exe" (Split-Path $ollamaCli) 7 '大肥鱼的记忆' "--headless `"$ollamaCli`" serve"; Say '开机自启：Ollama（记忆，后台跑、不出窗口）' }
if (Get-ChildItem $startup -Filter '*Breeze*' -ErrorAction SilentlyContinue) { Say '开机自启：Breeze 语音（装的时候已经加过）' }
# 桌宠桥（配过桌宠才有，setup-pet.ps1 建的）：开机后台起来、不出窗口。tools\ 里的桥顺手换成这版 Skill 带的（下次开机或工作台重开后生效）
if ((Test-Path "$Dir\private\pet-key.txt") -and (Test-Path "$Dir\start-petbridge.cmd")) {
    New-Item -ItemType Directory -Force "$Dir\tools" | Out-Null
    foreach ($f in 'dafeiyu_pet_bridge.py', 'pet_config.py', 'pet-persona.txt') { if (Test-Path "$PSScriptRoot\$f") { Copy-Item "$PSScriptRoot\$f" "$Dir\tools\$f" -Force } }
    New-Lnk "$startup\大肥鱼-桌宠桥.lnk" "$env:SystemRoot\System32\conhost.exe" $Dir 7 '桌宠跟大肥鱼说话走这里，别删' "--headless cmd.exe /c `"$Dir\start-petbridge.cmd`""
    Say '开机自启：大肥鱼桥（桌宠跟她说话走这里，后台跑、不出窗口）'
}

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
Copy-Workbench
foreach ($f in 'workbench.ps1', 'common.ps1', 'backup.ps1', 'diag.ps1', 'import-pack.ps1', 'setup-pet.ps1') { Copy-Item "$PSScriptRoot\$f" "$Dir\tools\$f" -Force }
New-Lnk "$desktop\大肥鱼工作台.lnk" "$Dir\tools\workbench.cmd" "$Dir\tools" 7 '看大肥鱼在不在、一键修、补装、备份'
if (Test-Path "$Dir\tools\workbench\dafeiyu.ico") { $l = $ws.CreateShortcut("$desktop\大肥鱼工作台.lnk"); $l.IconLocation = "$Dir\tools\workbench\dafeiyu.ico,0"; $l.Save() }
& "$Dir\tools\workbench.ps1" -Dir $Dir -NoOpen   # 顺手生成兜底静态页
Say '桌面放了「大肥鱼工作台」（双击开小窗口，不用浏览器地址栏）；下一步照 收尾-自启和掉线.md 的 8.3 打开它给他看'
