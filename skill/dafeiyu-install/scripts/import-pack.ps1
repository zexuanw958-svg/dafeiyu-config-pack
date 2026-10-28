# 第 6、7 步：跑配置包导入工具的某几块。块：core（变成大肥鱼，几秒）、plugins、video、memory、voice、memes（表情包，最大）、pet（桌宠也是她，setup-pet.ps1 调）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File import-pack.ps1 -Dir <工作目录> -Only core[,plugins] [-Background]
#   -Background：放后台跑（表情包这种几百 MB 的），日志写 <工作目录>\logs\import-<块>.log，脚本马上返回。
#                你每半分钟读一次日志最后几行转述进度；日志里出现 RESULT 那一行就是这块跑完了。
# 回答文件 <工作目录>\answers.json 和配置包 <工作目录>\dafeiyu-config-pack 要先准备好（references/导入配置包.md 7.1、7.2）。
# 每次导入前工具会自动存一份设置快照（<工作目录>\private\snapshots\，第一次那份另存 original.json）。另外两种用法：
#   -Snapshot                只存一份快照、不装（改他自己配的机器人之前先跑）
#   -Restore [-From <文件>]  退回到快照：不写 -From 就退回 original.json（第一次动之前的样子）
#   -Python <python.exe>     他自己装的 AstrBot 找不到自带的 Python 时，指定一个（导入工具只用标准库，Python 3.8 以上都行）
param([Parameter(Mandatory)][string]$Dir, [string]$Only = '', [switch]$Background, [switch]$Snapshot, [switch]$Restore, [string]$From = '', [string]$Python = '')
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$py = if ($Python) { $Python } else { Find-AstrPython $Dir }
if (-not $py) { throw '找不到 AstrBot 自带的 Python（工作目录里和启动器实例里都没有），先完成第 2 步；他自己装的 AstrBot 就用 -Python 指定一个' }
$tool = "$Dir\dafeiyu-config-pack\tools\import_dafeiyu.py"
# 工作目录里没有配置包，或者是不认 --only 的旧包：用 Skill 自带的那份（发布时打进 <Skill>\pack\），不用联网
$bundled = Join-Path (Split-Path $PSScriptRoot) 'pack\dafeiyu-config-pack'
$old = (Test-Path $tool) -and -not (Select-String -Path $tool -Pattern '--only' -SimpleMatch -Quiet)
# 要装桌宠、工作目录里那份配置包还不认 pet 这块（10-28 以前的包）：同样换成 Skill 自带的
if ((Test-Path $tool) -and ($Only -match 'pet') -and -not (Select-String -Path $tool -Pattern 'def block_pet' -SimpleMatch -Quiet)) { $old = $true }
if (((-not (Test-Path $tool)) -or $old) -and (Test-Path "$bundled\tools\import_dafeiyu.py")) {
    $dst = "$Dir\dafeiyu-config-pack"
    if (Test-Path $dst) {
        $bak = "dafeiyu-config-pack.旧-{0}" -f (Get-Date -Format 'MMdd-HHmm')
        try { Rename-Item $dst $bak -ErrorAction Stop; Say '工作目录里的配置包是旧版，改名留着，换成 Skill 自带的新版' }
        catch {
            # 改不了名（多半是资源管理器正开着里面的文件夹）：旧的整份拷一份留着，新版文件直接盖上去。
            # 不能照常 Copy-Item 整个文件夹：目标已经在，会被套进旧文件夹里一层，导入工具还是旧的（10-28 测试机踩到）
            Copy-Item $dst "$Dir\$bak" -Recurse -Force
            & robocopy.exe $bundled $dst /E /NFL /NDL /NJH /NJS /NP | Out-Null
            Say "工作目录里的配置包是旧版，改不了名（资源管理器开着它？），旧的拷了一份 $bak 留着，新版直接盖在原地"
        }
    }
    if (-not (Test-Path $dst)) { Copy-Item $bundled $dst -Recurse -Force }
    if (-not (Select-String -Path $tool -Pattern 'def block_pet' -SimpleMatch -Quiet)) { throw "配置包没换成新版（$dst）：关掉开着这个文件夹的资源管理器窗口，再跑一次" }
    Say "配置包用的是 Skill 自带的那份（$bundled）"
}
if (-not (Test-Path $tool)) { throw "没找到配置包：$Dir\dafeiyu-config-pack（见 导入配置包.md 7.1）" }
if (-not (Select-String -Path $tool -Pattern '--only' -SimpleMatch -Quiet)) { throw "$Dir\dafeiyu-config-pack 是旧版配置包（不认 --only），换新包（见 导入配置包.md 7.1）" }
$ans = "$Dir\answers.json"
if (-not (Test-Path $ans)) { throw "没找到回答文件：$ans（见 导入配置包.md 7.2）" }
if ($Snapshot -or $Restore) {
    # 旧版导入工具不认 --snapshot，会当成没写、去跑整套导入：先认一下
    if (-not (Select-String -Path $tool -Pattern '--snapshot' -SimpleMatch -Quiet)) { throw "$Dir\dafeiyu-config-pack 是旧版配置包，不会存快照、退回：换成 Skill 自带的新版（见 导入配置包.md 7.1）再来，这次什么都没改" }
    # 参数直接列出来：先存进变量再 @ 展开，单个参数会被 PowerShell 拆坏，导入工具收不到 --snapshot，会去跑整套导入（10-28 实测）
    if ($Snapshot) { & $py -X utf8 $tool --answers $ans --snapshot }
    elseif ($From) { & $py -X utf8 $tool --answers $ans --restore $From }
    else { & $py -X utf8 $tool --answers $ans --restore }
    exit $LASTEXITCODE
}
if (-not $Only) { throw '-Only 要写：core、plugins、video、memory、voice、memes、pet，逗号隔开（或者 -Snapshot、-Restore）' }
$Only = ($Only -replace '\s', '').ToLower()
if ($Only -notmatch '^(core|plugins|video|memory|voice|memes|pet)(,(core|plugins|video|memory|voice|memes|pet))*$') { throw "-Only 只认 core、plugins、video、memory、voice、memes、pet，逗号隔开：$Only" }
$tag = $Only -replace ',', '+'
if ($Background) {
    New-Item -ItemType Directory -Force "$Dir\logs" | Out-Null
    $log = "$Dir\logs\import-$tag.log"
    Remove-Item $log -Force -ErrorAction SilentlyContinue
    $cmd = "@echo off`r`nchcp 65001 >nul`r`n`"$py`" -X utf8 `"$tool`" --answers `"$ans`" --only $Only > `"$log`" 2>&1`r`n"
    [IO.File]::WriteAllText("$Dir\import-$tag.cmd", $cmd, (New-Object Text.UTF8Encoding($false)))
    Start-Detached "import-$tag" "$Dir\import-$tag.cmd"
    Say "已经放到后台跑了。看进度：Get-Content -Encoding UTF8 -Tail 6 '$log'"
} else {
    & $py -X utf8 $tool --answers $ans --only $Only
}
