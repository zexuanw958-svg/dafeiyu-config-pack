# 还原备份：把 backup.ps1 打的 zip 里的 private\ 和 astrbot\data 放回工作目录。先照 Skill 把主路装到能聊天，再还原。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File restore.ps1 -Dir <工作目录> -Zip <备份 zip>
# 现在的 private 和 astrbot\data 不删：改名成「*.还原前-<时间>」留着，还原出问题能换回来。
# 还原后：登 QQ 的程序不在备份里，照第 4、5 步重新登小号（connect 脚本会把备份里的连接口令写回协议端）；
#   工作目录跟备份时不一样，而且他开着看视频的，再跑 import-pack.ps1 -Only video 把插件里的路径改到新位置。
param([Parameter(Mandatory)][string]$Dir, [Parameter(Mandatory)][string]$Zip)
. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Stop'
$Dir = [IO.Path]::GetFullPath($Dir).TrimEnd('\')
if (-not (Test-Path $Zip)) { throw "没找到备份：$Zip" }
if (-not (Test-Path "$Dir\astrbot")) { throw "$Dir 里没有 astrbot 文件夹：先照 Skill 把主路装好（能聊天了）再还原。启动器装的 AstrBot 不走这个脚本" }
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $Zip).Path)
try {
    $ours = @($z.Entries | Where-Object { ($_.FullName -like 'private/*' -or $_.FullName -like 'astrbot/data/*') -and $_.FullName -notmatch '(^|/)\.\.(/|$)' })
    if (-not $ours) { throw '这个 zip 不是大肥鱼的备份（里面没有 private 和 astrbot/data）' }
    $info = $null
    $ie = $z.GetEntry('备份信息.json')
    if ($ie) { $r = New-Object IO.StreamReader($ie.Open()); try { $info = $r.ReadToEnd() | ConvertFrom-Json } finally { $r.Dispose() } }

    # 1. 停掉工作目录里的 AstrBot（不停的话数据库被占着，换不了）
    $port = Get-BackendPort $Dir
    Stop-UnderDir "$Dir\uv"; Stop-UnderDir "$Dir\astrbot"; Start-Sleep 3
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "AstrBot（端口 $port）还开着，停不掉：请观众把 AstrBot 的黑窗口关掉，再重跑" }

    # 2. 现在的两样改名留着
    $stamp = Get-Date -Format 'MMdd-HHmm'
    $oldData = "$Dir\astrbot\data.还原前-$stamp"
    if (Test-Path "$Dir\private") { Rename-Item "$Dir\private" "private.还原前-$stamp" }
    if (Test-Path "$Dir\astrbot\data") { Rename-Item "$Dir\astrbot\data" (Split-Path $oldData -Leaf) }
    Say "原来的 private 和 astrbot\data 改名成「.还原前-$stamp」留着了"

    # 3. 解出备份
    $n = 0
    foreach ($e in $ours) {
        $dst = Join-Path $Dir ($e.FullName.Replace('/', '\'))
        if ($e.FullName.EndsWith('/')) { New-Item -ItemType Directory -Force $dst | Out-Null; continue }
        New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
        [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dst, $true); $n++
    }
    Say "从备份放回 $n 个文件"
} finally { $z.Dispose() }

# 4. 备份里没带表情包的，把这次新装好的表情包挪过来，省得重下
$memes = 'plugin_data\meme_manager'
if (-not (Test-Path "$Dir\astrbot\data\$memes") -and (Test-Path "$oldData\$memes")) {
    New-Item -ItemType Directory -Force "$Dir\astrbot\data\plugin_data" | Out-Null
    Move-Item "$oldData\$memes" "$Dir\astrbot\data\$memes"
    Say '备份里没带表情包，用的是这次装好的那几套'
}

# 5. 开 AstrBot（端口按备份里的设置重新读）
$port = Get-BackendPort $Dir
if (Test-Path "$Dir\start-astrbot.cmd") {
    Start-Detached 'astrbot' "$Dir\start-astrbot.cmd"
    if (Wait-Port $port 150) { Say "AstrBot 开起来了。后台密码现在是备份里那个（private\backend.txt）" }
    else { Say 'AstrBot 150 秒没起来，看 astrbot\run.log 最后几行；实在不行把「.还原前」那两个文件夹改回原名' }
}
if ($info -and $info.dir -and ($info.dir.TrimEnd('\') -ne $Dir)) {
    Say "备份是从 $($info.dir) 打的，现在的工作目录是 $Dir：开着看视频的话再跑 import-pack.ps1 -Only video，把插件里的路径改过来"
}
Say '下一步：照第 4、5 步重新登小号（登 QQ 的程序不在备份里）；登上后让他发「我家猫叫什么」这类以前聊过的事，看她还记不记得'
