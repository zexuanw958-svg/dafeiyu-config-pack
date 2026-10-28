# 第 6、7 步：跑配置包导入工具的某几块。块：core（变成大肥鱼，几秒）、plugins、video、memory、voice、memes（表情包，最大）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File import-pack.ps1 -Dir <工作目录> -Only core[,plugins] [-Background]
#   -Background：放后台跑（表情包这种几百 MB 的），日志写 <工作目录>\logs\import-<块>.log，脚本马上返回。
#                你每半分钟读一次日志最后几行转述进度；日志里出现 RESULT 那一行就是这块跑完了。
# 回答文件 <工作目录>\answers.json 和配置包 <工作目录>\dafeiyu-config-pack 要先准备好（references/导入配置包.md 7.1、7.2）。
param([Parameter(Mandatory)][string]$Dir, [Parameter(Mandatory)][string]$Only, [switch]$Background)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
$py = Find-AstrPython $Dir
if (-not $py) { throw '找不到 AstrBot 自带的 Python（工作目录里和启动器实例里都没有），先完成第 2 步' }
$tool = "$Dir\dafeiyu-config-pack\tools\import_dafeiyu.py"
# 工作目录里没有配置包，或者是不认 --only 的旧包：用 Skill 自带的那份（发布时打进 <Skill>\pack\），不用联网
$bundled = Join-Path (Split-Path $PSScriptRoot) 'pack\dafeiyu-config-pack'
$old = (Test-Path $tool) -and -not (Select-String -Path $tool -Pattern '--only' -SimpleMatch -Quiet)
if (((-not (Test-Path $tool)) -or $old) -and (Test-Path "$bundled\tools\import_dafeiyu.py")) {
    if (Test-Path "$Dir\dafeiyu-config-pack") { Rename-Item "$Dir\dafeiyu-config-pack" ("dafeiyu-config-pack.旧-{0}" -f (Get-Date -Format 'MMdd-HHmm')); Say '工作目录里的配置包是旧版，改名留着，换成 Skill 自带的新版' }
    Copy-Item $bundled "$Dir\dafeiyu-config-pack" -Recurse -Force
    Say "配置包用的是 Skill 自带的那份（$bundled）"
}
if (-not (Test-Path $tool)) { throw "没找到配置包：$Dir\dafeiyu-config-pack（见 导入配置包.md 7.1）" }
if (-not (Select-String -Path $tool -Pattern '--only' -SimpleMatch -Quiet)) { throw "$Dir\dafeiyu-config-pack 是旧版配置包（不认 --only），换新包（见 导入配置包.md 7.1）" }
$ans = "$Dir\answers.json"
if (-not (Test-Path $ans)) { throw "没找到回答文件：$ans（见 导入配置包.md 7.2）" }
$Only = ($Only -replace '\s', '').ToLower()
if ($Only -notmatch '^(core|plugins|video|memory|voice|memes)(,(core|plugins|video|memory|voice|memes))*$') { throw "-Only 只认 core、plugins、video、memory、voice、memes，逗号隔开：$Only" }
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
